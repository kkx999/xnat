from pathlib import Path
import json


def replace_once(path: str, old: str, new: str):
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected exactly one match, got {count}: {old[:100]!r}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


# XNAT Release / Panel stay on v1.0.3. Only Host Agent gets a component hotfix.
Path("agent/VERSION").write_text("1.0.3\n", encoding="utf-8")
Path("agent/natvps_agent/__init__.py").write_text(
    '__version__ = "1.0.3"\n__api_version__ = "2"\n', encoding="utf-8"
)

agent_path = Path("agent/natvps_agent/main.py")
agent = agent_path.read_text(encoding="utf-8")
if agent.count('AGENT_VERSION = "1.0.2"') != 1:
    raise SystemExit("unexpected Host Agent version marker")
agent = agent.replace('AGENT_VERSION = "1.0.2"', 'AGENT_VERSION = "1.0.3"', 1)

route_start = agent.index('@app.post("/v1/instances/{instance_id}/reinstall")')
route_end = agent.index('\n\n@app.delete("/v1/instances/{instance_id}")', route_start)
new_reinstall = r'''def _strict_delete_instance(name: str):
    """Delete one instance and verify the destructive operation actually completed."""
    if not instance_exists(name):
        return
    error = None
    try:
        run(["incus", "delete", name, "--force"], timeout=120)
    except Exception as exc:
        error = exc
    if instance_exists(name):
        detail = str(error)[:900] if error else "Incus 返回成功但实例仍然存在"
        raise RuntimeError(f"删除实例 {name} 失败: {detail}")


def _restart_original_if_needed(instance_id: str, old_status: str):
    if old_status == "running" and instance_exists(instance_id):
        run(["incus", "start", instance_id], check=False, timeout=65)


def _prepare_reinstall_backup(instance_id: str, backup_name: str, old_status: str) -> str:
    """Preserve the original instance without trusting a single `incus move` call.

    Preferred path is a local rename (cheap and atomic on normal Incus storage).
    Some storage/backend combinations can reject that rename, so a stopped local
    copy is used as a fallback. The original is never deleted until the fallback
    copy has been verified to exist.
    """
    if old_status == "running":
        run(["incus", "stop", instance_id, "--timeout", "20", "--force"], timeout=45)

    move_error = ""
    try:
        run(["incus", "move", instance_id, backup_name], timeout=120)
    except Exception as exc:
        move_error = str(exc)

    source_exists = instance_exists(instance_id)
    backup_exists = instance_exists(backup_name)
    if backup_exists and not source_exists:
        return "move"

    if not source_exists and not backup_exists:
        raise RuntimeError(
            f"Incus move 后原实例和临时备份都不可见；move 错误: {move_error[:900] or '未知'}"
        )

    if source_exists and backup_exists:
        _restart_original_if_needed(instance_id, old_status)
        raise RuntimeError(
            f"Incus move 后原实例与临时备份同时存在，为避免误删已停止继续；move 错误: {move_error[:900] or '未知'}"
        )

    # Source is still intact and no backup exists: fall back to a stopped copy.
    try:
        run(["incus", "copy", instance_id, backup_name, "--instance-only"], timeout=300)
    except Exception as copy_exc:
        _restart_original_if_needed(instance_id, old_status)
        raise RuntimeError(
            "Incus 无法为安全重装创建临时备份；"
            f"move: {move_error[:700] or '失败但无输出'}；copy: {str(copy_exc)[:900]}"
        ) from copy_exc

    if not instance_exists(backup_name):
        _restart_original_if_needed(instance_id, old_status)
        raise RuntimeError("Incus copy 返回成功，但临时备份实例不存在；原实例已保留")

    try:
        _strict_delete_instance(instance_id)
    except Exception as delete_exc:
        cleanup_error = ""
        try:
            _strict_delete_instance(backup_name)
        except Exception as cleanup_exc:
            cleanup_error = f"；临时备份清理失败: {str(cleanup_exc)[:500]}"
        _restart_original_if_needed(instance_id, old_status)
        raise RuntimeError(
            f"临时备份已建立，但原实例无法安全移出名称: {str(delete_exc)[:900]}{cleanup_error}"
        ) from delete_exc

    if instance_exists(instance_id) or not instance_exists(backup_name):
        raise RuntimeError("安全重装备份状态校验失败；已拒绝继续部署新实例")
    return "copy"


def _restore_reinstall_backup(backup_name: str, instance_id: str, old_status: str) -> tuple[bool, str]:
    """Restore the preserved instance, with copy fallback if rename is unavailable."""
    if not instance_exists(backup_name):
        return False, "临时备份不存在"
    if instance_exists(instance_id):
        try:
            _strict_delete_instance(instance_id)
        except Exception as exc:
            return False, f"新实例清理失败，无法恢复原名称: {str(exc)[:800]}"

    warnings = []
    move_error = ""
    try:
        run(["incus", "move", backup_name, instance_id], timeout=120)
    except Exception as exc:
        move_error = str(exc)

    restored_exists = instance_exists(instance_id)
    backup_exists = instance_exists(backup_name)
    if not restored_exists and backup_exists:
        try:
            run(["incus", "copy", backup_name, instance_id, "--instance-only"], timeout=300)
        except Exception as copy_exc:
            return False, (
                f"恢复 rename 失败: {move_error[:650] or '无输出'}；"
                f"恢复 copy 也失败: {str(copy_exc)[:800]}"
            )
        restored_exists = instance_exists(instance_id)
        if not restored_exists:
            return False, "恢复 copy 返回成功，但原实例名称仍不存在"
        try:
            _strict_delete_instance(backup_name)
        except Exception as cleanup_exc:
            warnings.append(f"恢复后临时备份未能清理: {str(cleanup_exc)[:500]}")
    elif restored_exists and backup_exists:
        warnings.append("恢复后临时备份仍存在，已保留副本供人工核查")
    elif not restored_exists and not backup_exists:
        return False, f"恢复 move 后原名称与临时备份都不可见: {move_error[:800] or '未知错误'}"

    if old_status == "running":
        try:
            run(["incus", "start", instance_id], timeout=65)
        except Exception as start_exc:
            return False, f"原实例已恢复但重新开机失败: {str(start_exc)[:800]}"
    return True, "；".join(warnings)


@app.post("/v1/instances/{instance_id}/reinstall")
def reinstall(instance_id: str, body: ReinstallBody):
    require_instance(instance_id)
    require_nat_port_allowed(body.ssh_port)
    ensure_host_root_space("重装 VPS")
    mode = require_virtualization_allowed(body.virtualization_type)
    preflight_image(body.image_alias, body.disk_gb, mode)
    if mode == "kvm" and body.memory_mb < 512:
        raise HTTPException(422, "KVM 实例至少需要 512 MiB 内存")
    if not instance_exists(instance_id):
        raise HTTPException(404, "原实例不存在，无法执行安全重装")

    old_status = instance_status(instance_id)
    stored_server_id = instance_xnat_server_id(instance_id)
    requested_server_id = int(body.server_id) if body.server_id is not None else None
    if stored_server_id is not None and requested_server_id is not None and stored_server_id != requested_server_id:
        raise HTTPException(409, "实例 XNAT server_id 与 Panel 请求不一致，拒绝重装")
    effective_server_id = stored_server_id if stored_server_id is not None else requested_server_id
    backup_name = (instance_id[:46] + "-xnat-old-" + secrets.token_hex(4))[:63]

    try:
        backup_method = _prepare_reinstall_backup(instance_id, backup_name, old_status)
    except Exception as exc:
        _restart_original_if_needed(instance_id, old_status)
        raise HTTPException(
            500,
            f"安全重装预备阶段失败，原实例已保留或已尝试恢复: {str(exc)[:1400]}",
        )

    password = random_password()
    try:
        launch(
            instance_id, body.image_alias, body.memory_mb, body.disk_gb,
            body.cpu, body.bandwidth_mbps, mode, server_id=effective_server_id,
        )
        private_ip = wait_ipv4(instance_id, mode)
        prepare_ssh(instance_id, password)
        add_ssh_proxy(instance_id, body.ssh_port)

        backup_cleanup_pending = False
        try:
            _strict_delete_instance(backup_name)
        except Exception as cleanup_exc:
            backup_cleanup_pending = True
            print(
                f"[XNAT] 重装成功，但临时备份 {backup_name} 清理失败: {cleanup_exc}",
                flush=True,
            )
        return {
            "instance_id": instance_id,
            "private_ip": private_ip,
            "ssh_port": body.ssh_port,
            "status": "running",
            "root_password": password,
            "virtualization_type": mode,
            "rollback_safe": True,
            "backup_method": backup_method,
            "backup_cleanup_pending": backup_cleanup_pending,
        }
    except Exception as exc:
        delete_error = ""
        try:
            _strict_delete_instance(instance_id)
        except Exception as delete_exc:
            delete_error = f"新实例清理失败: {str(delete_exc)[:700]}"

        restored = False
        restore_detail = ""
        if not delete_error:
            restored, restore_detail = _restore_reinstall_backup(backup_name, instance_id, old_status)

        if isinstance(exc, HTTPException):
            detail = str(exc.detail)
            status_code = int(exc.status_code)
        else:
            detail = str(exc)
            status_code = 500

        if delete_error:
            rollback_text = f"；{delete_error}；原实例临时备份仍保留为 {backup_name}"
        elif restored:
            rollback_text = "；原实例已自动恢复"
            if restore_detail:
                rollback_text += f"（{restore_detail}）"
        else:
            rollback_text = f"；原实例自动恢复失败: {restore_detail[:900]}；临时备份名: {backup_name}"
        raise HTTPException(
            status_code,
            f"新系统部署失败: {detail[:1000]}{rollback_text}",
        )
'''
agent = agent[:route_start] + new_reinstall + agent[route_end:]
agent_path.write_text(agent, encoding="utf-8")

meta_path = Path("release.json")
meta = json.loads(meta_path.read_text(encoding="utf-8"))
if meta.get("release_version") != "1.0.3" or meta.get("panel_version") != "1.0.3":
    raise SystemExit("release/panel version changed unexpectedly")
if meta.get("agent_version") != "1.0.2":
    raise SystemExit("unexpected existing Agent version")
meta["agent_version"] = "1.0.3"
meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

replace_once("README.md", "再逐台 Host 更新 Host Agent 到 v1.0.2", "再逐台 Host 更新 Host Agent 到 v1.0.3")

intro_path = Path("docs/INTRODUCTION.md")
intro = intro_path.read_text(encoding="utf-8")
current, sep, history = intro.partition("### v1.0.2")
current = current.replace("| XNAT Host Agent | v1.0.2 |", "| XNAT Host Agent | v1.0.3 |", 1)
current = current.replace("Host Agent 为 v1.0.2", "Host Agent 为 v1.0.3", 1)
old = "- Host Agent v1.0.2 移除 Debian / Ubuntu / Alpine 的旧镜像家族硬编码，LXC 不再覆盖 Panel 的逐镜像最低系统盘配置。\n"
new = (
    "- Host Agent v1.0.3 保留 v1.0.2 的镜像磁盘策略修复，并增强安全重装：Incus 本地 rename 失败时可使用停止态临时副本兜底，回滚同样支持 copy 恢复；预备失败会返回真实 Incus 错误且不直接删除原实例。\n"
)
if old not in current:
    raise SystemExit("INTRODUCTION Host Agent bullet missing")
current = current.replace(old, new, 1)
intro_path.write_text(current + (sep + history if sep else ""), encoding="utf-8")

changelog_path = Path("CHANGELOG.md")
changelog = changelog_path.read_text(encoding="utf-8")
current, sep, history = changelog.partition("## v1.0.2")
current = current.replace("Host Agent 1.0.2", "Host Agent 1.0.3", 1)
old = "- Host Agent v1.0.2 修复旧的 Debian / Ubuntu 2 GiB 与 KVM 4 GiB 硬编码，LXC 不再覆盖 Panel 后台逐镜像配置；KVM 技术底线统一为 3 GiB。\n"
new = (
    "- Host Agent v1.0.3 保留镜像磁盘策略修复，并修复安全重装预备阶段对单次 `incus move` 的硬依赖：rename 失败可回退到停止态临时副本，回滚路径同样支持 copy 恢复，并保留真实 Incus 错误用于定位。\n"
)
if old not in current:
    raise SystemExit("CHANGELOG Host Agent bullet missing")
current = current.replace(old, new, 1)
changelog_path.write_text(current + (sep + history if sep else ""), encoding="utf-8")

build_path = Path("scripts/build-release.sh")
build = build_path.read_text(encoding="utf-8")
build = build.replace(
    "- Host Agent v1.0.2 修复旧镜像磁盘硬编码；Agent API、Panel 整体 UI 与主要业务交互保持不变",
    "- Host Agent v1.0.3 保留镜像磁盘策略修复，并增强安全重装的 rename/copy 备份与回滚兜底；Agent API、Panel 整体 UI 与主要业务交互保持不变",
    1,
)
build_path.write_text(build, encoding="utf-8")

check_path = Path("scripts/check.sh")
check = check_path.read_text(encoding="utf-8")
reinstall_guard = r'''

# v1.0.3 Host Agent rollback-safe reinstall fallback guard
python3 - <<'PYREINSTALLSAFE'
from pathlib import Path
agent=Path('agent/natvps_agent/main.py').read_text()
assert 'def _prepare_reinstall_backup' in agent
assert 'def _restore_reinstall_backup' in agent
assert '["incus", "copy", instance_id, backup_name, "--instance-only"]' in agent
assert '["incus", "copy", backup_name, instance_id, "--instance-only"]' in agent
assert '原实例已保留或已尝试恢复' in agent
assert 'backup_cleanup_pending' in agent
assert '[:63]' in agent, 'temporary instance name must stay within DNS/Incus-safe length'
print('v1.0.3 rollback-safe reinstall fallback guard: ok')
PYREINSTALLSAFE
'''
if "v1.0.3 Host Agent rollback-safe reinstall fallback guard" not in check:
    check += reinstall_guard
check_path.write_text(check, encoding="utf-8")

# Final invariants for the preparation patch itself.
agent = agent_path.read_text(encoding="utf-8")
assert 'AGENT_VERSION = "1.0.3"' in agent
assert 'def _prepare_reinstall_backup' in agent
assert 'incus", "copy"' in agent
assert '安全重装预备阶段失败，原实例已保留或已尝试恢复' in agent
assert Path("VERSION").read_text().strip() == "1.0.3"
assert Path("panel/VERSION").read_text().strip() == "1.0.3"
print("Host Agent v1.0.3 reinstall hotfix prepared")
