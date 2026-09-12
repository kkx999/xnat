from pathlib import Path
import json
import re


def replace_once(path, old, new):
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected exactly one match, got {count}: {old[:120]!r}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


def replace_regex(path, pattern, repl):
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    new, count = re.subn(pattern, repl, text, count=1, flags=re.S)
    if count != 1:
        raise SystemExit(f"{path}: regex match count={count}: {pattern[:120]!r}")
    p.write_text(new, encoding="utf-8")


# Release/component metadata. Panel and all APIs remain unchanged.
Path("VERSION").write_text("1.6.4\n", encoding="utf-8")
Path("agent/VERSION").write_text("1.2.1\n", encoding="utf-8")
Path("agent/natvps_agent/__init__.py").write_text('__version__ = "1.2.1"\n__api_version__ = "1"\n', encoding="utf-8")
meta = json.loads(Path("release.json").read_text(encoding="utf-8"))
meta["release_version"] = "1.6.4"
meta["panel_version"] = "1.6.3"
meta["agent_version"] = "1.2.1"
meta["agent_api_version"] = "1"
meta["supported_agent_api_versions"] = ["1"]
meta["mobile_api_version"] = "1"
Path("release.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

# Host Agent: root filesystem guard + narrow safe cleanup.
replace_once("agent/natvps_agent/main.py", "import secrets\nimport socket\n", "import secrets\nimport shutil\nimport socket\n")
replace_once("agent/natvps_agent/main.py", 'AGENT_VERSION = "1.2.0"', 'AGENT_VERSION = "1.2.1"')
replace_once(
    "agent/natvps_agent/main.py",
    'TIMEOUT = int(os.getenv("INCUS_PROVISION_TIMEOUT", "180"))\n',
    'TIMEOUT = int(os.getenv("INCUS_PROVISION_TIMEOUT", "180"))\n'
    'ROOT_MIN_FREE_MB = max(128, int(os.getenv("XNAT_ROOT_MIN_FREE_MB", "512")))\n'
    'ROOT_RESUME_FREE_MB = max(ROOT_MIN_FREE_MB, int(os.getenv("XNAT_ROOT_RESUME_FREE_MB", "768")))\n',
)
cleanup_funcs = r'''

def root_free_mb() -> int:
    """Return free MiB on the Host root filesystem."""
    return int(shutil.disk_usage("/").free // (1024 * 1024))


def safe_cleanup_host_space() -> tuple[int, int]:
    """Perform only narrowly allow-listed Host cleanup actions.

    Never touches Incus storage pools, images, instance disks, VPS data, or
    XNAT configuration. Failures are best-effort because this is a recovery
    path for a nearly full filesystem.
    """
    before = root_free_mb()
    commands = [
        (["apt-get", "clean"], 90),
        (["journalctl", "--vacuum-size=50M"], 45),
    ]
    for command, command_timeout in commands:
        try:
            subprocess.run(
                command,
                text=True,
                capture_output=True,
                timeout=command_timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            pass
    return before, root_free_mb()


def ensure_host_root_space(action: str) -> int:
    """Auto-clean once when root space is critically low, then fail closed."""
    free_mb = root_free_mb()
    if free_mb >= ROOT_MIN_FREE_MB:
        return free_mb

    before_mb, free_mb = safe_cleanup_host_space()
    if free_mb < ROOT_RESUME_FREE_MB:
        raise HTTPException(
            status_code=507,
            detail=(
                f"Host 系统盘空间不足：{action}前仅剩 {before_mb} MiB；"
                f"自动安全清理后剩余 {free_mb} MiB，至少需要 {ROOT_RESUME_FREE_MB} MiB。"
                "请释放系统盘空间或扩容 Host。"
            ),
        )
    return free_mb
'''
replace_once(
    "agent/natvps_agent/main.py",
    "    return proc\n\n\ndef require_instance(name: str):\n",
    "    return proc\n" + cleanup_funcs + "\n\ndef require_instance(name: str):\n",
)
replace_once(
    "agent/natvps_agent/main.py",
    'def launch(name: str, image_alias: str, memory_mb: int, disk_gb: float, cpu: int, bandwidth_mbps: int, virtualization_type: str = "lxc"):\n    mode = require_virtualization_allowed(virtualization_type)\n',
    'def launch(name: str, image_alias: str, memory_mb: int, disk_gb: float, cpu: int, bandwidth_mbps: int, virtualization_type: str = "lxc"):\n    ensure_host_root_space("创建 VPS")\n    mode = require_virtualization_allowed(virtualization_type)\n',
)
replace_once(
    "agent/natvps_agent/main.py",
    'def add_proxy_device(name: str, device: str, protocol: str, public_port: int, private_port: int):\n    args = [\n',
    'def add_proxy_device(name: str, device: str, protocol: str, public_port: int, private_port: int):\n    ensure_host_root_space("创建端口映射")\n    args = [\n',
)
replace_once(
    "agent/natvps_agent/main.py",
    '    mode = require_virtualization_allowed(body.virtualization_type)\n    if mode == "kvm" and (body.memory_mb < 512 or body.disk_gb < 4):\n',
    '    mode = require_virtualization_allowed(body.virtualization_type)\n    ensure_host_root_space("重装 VPS")\n    if mode == "kvm" and (body.memory_mb < 512 or body.disk_gb < 4):\n',
)
replace_once(
    "agent/natvps_agent/main.py",
    '        "memory_available_mb": int(vm.available / 1024 / 1024),\n        "storage_total_gb": round(total_gb, 2),\n',
    '        "memory_available_mb": int(vm.available / 1024 / 1024),\n        "root_free_mb": root_free_mb(),\n        "root_min_free_mb": ROOT_MIN_FREE_MB,\n        "root_resume_free_mb": ROOT_RESUME_FREE_MB,\n        "storage_total_gb": round(total_gb, 2),\n',
)

# Host installer UX: distinguish technical minimums from recommended disk sizes.
install = Path("scripts/install-host.sh")
text = install.read_text(encoding="utf-8")
repls = {
    'local mode="$1" prefix total_min reserve min_pool warn_total requires_kvm="false"': 'local mode="$1" prefix total_min reserve min_pool warn_total recommended_total requires_kvm="false"',
    'lxc) prefix="LXC"; total_min=4608; reserve=1024; min_pool=1; warn_total=5120 ;;': 'lxc) prefix="LXC"; total_min=4608; reserve=1024; min_pool=1; warn_total=5120; recommended_total=8192 ;;',
    'kvm) prefix="KVM"; total_min=6656; reserve=1536; min_pool=4; warn_total=8192; requires_kvm="true" ;;': 'kvm) prefix="KVM"; total_min=6656; reserve=1536; min_pool=4; warn_total=8192; recommended_total=12288; requires_kvm="true" ;;',
    'hybrid) prefix="HYBRID"; total_min=6656; reserve=1536; min_pool=4; warn_total=8192; requires_kvm="true" ;;': 'hybrid) prefix="HYBRID"; total_min=6656; reserve=1536; min_pool=4; warn_total=8192; recommended_total=12288; requires_kvm="true" ;;',
}
for old, new in repls.items():
    if text.count(old) != 1:
        raise SystemExit(f"install-host.sh mismatch: {old}")
    text = text.replace(old, new, 1)
needle = '  printf -v "${prefix}_WARN_TOTAL_MIB" \'%s\' "$warn_total"\n'
if text.count(needle) != 1:
    raise SystemExit("install-host.sh WARN_TOTAL_MIB assignment mismatch")
text = text.replace(needle, needle + '  printf -v "${prefix}_RECOMMENDED_TOTAL_MIB" \'%s\' "$recommended_total"\n', 1)
if text.count('echo "     母鸡基线：1C / 1GB / 4.5GiB 总硬盘"') != 1:
    raise SystemExit("install-host.sh LXC label mismatch")
text = text.replace(
    'echo "     母鸡基线：1C / 1GB / 4.5GiB 总硬盘"',
    'echo "     最低安装：1C / 1GB / 4.5GiB 总硬盘"\n    echo "     建议配置：1C / 1GB / 8GiB+ 总硬盘"',
    1,
)
if text.count('echo "     母鸡基线：1C / 1GB / 6.5GiB 总硬盘 + /dev/kvm"') != 2:
    raise SystemExit("install-host.sh KVM labels mismatch")
text = text.replace(
    'echo "     母鸡基线：1C / 1GB / 6.5GiB 总硬盘 + /dev/kvm"',
    'echo "     最低安装：1C / 1GB / 6.5GiB 总硬盘 + /dev/kvm"\n    echo "     建议配置：2C / 2GB / 12GiB+ 总硬盘 + /dev/kvm"',
)
for old, new in [
    ('VIRTUALIZATION_MODES_JSON=\'["lxc"]\'; VIRTUALIZATION_LABEL="LXC"; MAX_SAFE_GB="${LXC_POOL_GB}"; SYSTEM_RESERVE_MIB="${LXC_RESERVE_MIB}"; MIN_POOL_GB="${LXC_MIN_POOL_GB}"; WARN_TOTAL_MIB="${LXC_WARN_TOTAL_MIB}"',
     'VIRTUALIZATION_MODES_JSON=\'["lxc"]\'; VIRTUALIZATION_LABEL="LXC"; MAX_SAFE_GB="${LXC_POOL_GB}"; SYSTEM_RESERVE_MIB="${LXC_RESERVE_MIB}"; MIN_POOL_GB="${LXC_MIN_POOL_GB}"; WARN_TOTAL_MIB="${LXC_WARN_TOTAL_MIB}"; RECOMMENDED_TOTAL_MIB="${LXC_RECOMMENDED_TOTAL_MIB}"'),
    ('VIRTUALIZATION_MODES_JSON=\'["kvm"]\'; VIRTUALIZATION_LABEL="KVM"; MAX_SAFE_GB="${KVM_POOL_GB}"; SYSTEM_RESERVE_MIB="${KVM_RESERVE_MIB}"; MIN_POOL_GB="${KVM_MIN_POOL_GB}"; WARN_TOTAL_MIB="${KVM_WARN_TOTAL_MIB}"',
     'VIRTUALIZATION_MODES_JSON=\'["kvm"]\'; VIRTUALIZATION_LABEL="KVM"; MAX_SAFE_GB="${KVM_POOL_GB}"; SYSTEM_RESERVE_MIB="${KVM_RESERVE_MIB}"; MIN_POOL_GB="${KVM_MIN_POOL_GB}"; WARN_TOTAL_MIB="${KVM_WARN_TOTAL_MIB}"; RECOMMENDED_TOTAL_MIB="${KVM_RECOMMENDED_TOTAL_MIB}"'),
    ('VIRTUALIZATION_MODES_JSON=\'["lxc","kvm"]\'; VIRTUALIZATION_LABEL="LXC + KVM"; MAX_SAFE_GB="${HYBRID_POOL_GB}"; SYSTEM_RESERVE_MIB="${HYBRID_RESERVE_MIB}"; MIN_POOL_GB="${HYBRID_MIN_POOL_GB}"; WARN_TOTAL_MIB="${HYBRID_WARN_TOTAL_MIB}"',
     'VIRTUALIZATION_MODES_JSON=\'["lxc","kvm"]\'; VIRTUALIZATION_LABEL="LXC + KVM"; MAX_SAFE_GB="${HYBRID_POOL_GB}"; SYSTEM_RESERVE_MIB="${HYBRID_RESERVE_MIB}"; MIN_POOL_GB="${HYBRID_MIN_POOL_GB}"; WARN_TOTAL_MIB="${HYBRID_WARN_TOTAL_MIB}"; RECOMMENDED_TOTAL_MIB="${HYBRID_RECOMMENDED_TOTAL_MIB}"'),
]:
    if text.count(old) != 1:
        raise SystemExit(f"install-host.sh mode assignment mismatch: {old[:50]}")
    text = text.replace(old, new, 1)
old_warn = '  if (( ROOT_TOTAL_MIB < WARN_TOTAL_MIB || MAX_SAFE_GB == MIN_POOL_GB )); then echo "  [WARN] 当前属于低容量区间，建议只创建少量轻量实例。"; fi\n'
new_warn = '  if (( ROOT_TOTAL_MIB < RECOMMENDED_TOTAL_MIB )); then\n    echo "  [WARN] 当前低于长期运行建议容量（建议至少 $((RECOMMENDED_TOTAL_MIB/1024))GiB 总硬盘）。"\n    echo "         可安装，但建议仅用于测试或少量轻量实例，并持续关注 Host 系统盘剩余空间。"\n  elif (( ROOT_TOTAL_MIB < WARN_TOTAL_MIB || MAX_SAFE_GB == MIN_POOL_GB )); then\n    echo "  [WARN] 当前属于低容量区间，建议只创建少量轻量实例。"\n  fi\n'
if text.count(old_warn) != 1:
    raise SystemExit("install-host.sh low-capacity warning mismatch")
text = text.replace(old_warn, new_warn, 1)
install.write_text(text, encoding="utf-8")

# Host CLI: hide repo-wide Release as Host primary version, add safe cleanup menu.
replace_once(
    "scripts/xnat",
    '  [[ "$release" != unknown ]] && printf \'Release    v%s\\n\' "$release"\n  if [[ "$role" == host ]]; then\n',
    '  if [[ "$role" == panel && "$release" != unknown ]]; then\n    printf \'Release    v%s\\n\' "$release"\n  fi\n  if [[ "$role" == host ]]; then\n',
)
new_show_update = r'''show_update_check(){
  local role current current_rel release target api
  role="$(detect_role)"
  current="$(current_version "$role")"
  current_rel="$(current_release)"
  release="$(resolve_latest_release)"
  target="$(target_component_version "$release" "$role")"
  [[ -n "$target" ]] || die "Release v${release} 缺少组件版本信息"

  ui_section "检查结果"
  if [[ "$role" == host ]]; then
    printf '组件             Host Agent\n'
    printf '当前版本         v%s\n' "$current"
    printf '最新版本         v%s\n' "$target"
    api="$(release_meta_value "$release" agent_api_version)"
    printf 'Agent API        v%s\n' "${api:-?}"
    echo
    if [[ "$current" == "$target" ]]; then
      ui_success "Host Agent v${current} 已是最新版本"
      if [[ "$current_rel" != "$release" ]]; then
        if [[ "$current_rel" == unknown ]]; then
          ui_notice "有新的 Host 管理脚本可同步（来源 XNAT v${release}），不会改变 Agent API"
        else
          ui_notice "Host 管理脚本可同步：XNAT v${current_rel} → v${release}；Host Agent 核心版本不变"
        fi
      fi
    elif [[ "$current" != unknown ]] && dpkg --compare-versions "$target" lt "$current"; then
      ui_notice "当前 Host Agent 版本高于最新正式版本"
    else
      ui_notice "发现 Host Agent 更新：v${current} → v${target}"
    fi
    return 0
  fi

  printf '组件             %s\n' "$(component_label "$role")"
  printf '当前组件版本     v%s\n' "$current"
  [[ "$current_rel" != unknown ]] && printf '当前 XNAT Release v%s\n' "$current_rel"
  printf '最新 XNAT Release v%s\n' "$release"
  printf 'Release 组件版本 v%s\n' "$target"
  echo

  if [[ "$current" == "$target" && "$current_rel" == "$release" ]]; then
    ui_success "当前组件已是最新版本"
  elif [[ "$current" == "$target" ]]; then
    if [[ "$current_rel" == unknown ]]; then
      ui_notice "组件版本相同，但当前 Release 记录缺失；可同步到 Release v${release} 的管理脚本与发布文件"
    else
      ui_notice "组件版本相同，但当前 Release 为 v${current_rel}；可同步到 Release v${release} 的管理脚本与发布文件"
    fi
  elif [[ "$current" != unknown ]] && dpkg --compare-versions "$target" lt "$current"; then
    ui_notice "当前组件版本高于该 Release"
  else
    ui_notice "发现组件更新：v${current} → v${target}"
  fi
}
'''
replace_regex("scripts/xnat", r"show_update_check\(\)\{.*?\n\}\n\n\nis_prerelease_of_target\(\)\{", new_show_update + "\n\nis_prerelease_of_target(){")
replace_once(
    "scripts/xnat",
    '  echo "XNAT 系统诊断"\n  echo "组件：$(component_label "$role") v$(current_version "$role") / Release v$(current_release)"\n  [[ "$role" == host ]] && echo "Agent API：v$(current_agent_api_version)"\n',
    '  echo "XNAT 系统诊断"\n  if [[ "$role" == host ]]; then\n    echo "组件：Host Agent v$(current_version host)"\n    echo "Agent API：v$(current_agent_api_version)"\n  else\n    echo "组件：Panel v$(current_version panel) / Release v$(current_release)"\n  fi\n',
)
replace_once(
    "scripts/xnat",
    '    printf \'当前组件版本：v%s\\n\' "$current"\n    [[ "$(current_release)" != unknown ]] && printf \'当前 XNAT Release：v%s\\n\' "$(current_release)"\n    echo\n    echo "  1  检查最新正式 Release"\n    echo "  2  更新当前组件"\n    echo "  3  指定 XNAT Release 更新"\n',
    '    if [[ "$role" == host ]]; then\n      printf \'当前 Host Agent：v%s\\n\' "$current"\n    else\n      printf \'当前 Panel：v%s\\n\' "$current"\n      [[ "$(current_release)" != unknown ]] && printf \'当前 XNAT Release：v%s\\n\' "$(current_release)"\n    fi\n    echo\n    echo "  1  检查最新 $(component_label "$role")"\n    echo "  2  更新 $(component_label "$role")"\n    echo "  3  指定 XNAT 发布版本更新"\n',
)
cleanup_fn = r'''
cmd_host_cleanup(){
  need_root
  [[ "$(detect_role)" == host ]] || die "系统空间清理仅适用于 Host Agent"
  local before after released answer
  before="$(fs_available_mb /)"; before="${before:-0}"
  echo "XNAT Host 安全清理"
  echo "当前系统盘可用：${before} MiB"
  echo
  echo "仅清理："
  echo "  - APT 软件包下载缓存"
  echo "  - systemd journal 历史日志（保留到约 50 MiB）"
  echo "  - XNAT 健康检查临时文件"
  echo
  echo "明确不会删除：VPS、natpool、Incus 镜像/实例磁盘、Agent 配置或用户数据"
  if [[ "${XNAT_CLEANUP_ASSUME_YES:-0}" != 1 ]]; then
    [[ -t 0 ]] || die "非交互清理请设置 XNAT_CLEANUP_ASSUME_YES=1"
    read -r -p "确认执行安全清理？[y/N]: " answer
    [[ "$answer" =~ ^[Yy]$ ]] || { echo "已取消。"; return 0; }
  fi
  have apt-get && DEBIAN_FRONTEND=noninteractive apt-get clean >/dev/null 2>&1 || true
  have journalctl && journalctl --vacuum-size=50M >/dev/null 2>&1 || true
  rm -f /tmp/xnat-agent-health.* /tmp/xnat-health.* 2>/dev/null || true
  sync || true
  after="$(fs_available_mb /)"; after="${after:-0}"
  released=0
  if [[ "$before" =~ ^[0-9]+$ && "$after" =~ ^[0-9]+$ ]] && (( after > before )); then
    released=$((after - before))
  fi
  ui_success "安全清理完成：${before} MiB → ${after} MiB 可用（释放约 ${released} MiB）"
}
'''
replace_once("scripts/xnat", "\ncmd_uninstall(){\n", cleanup_fn + "\ncmd_uninstall(){\n")
replace_once(
    "scripts/xnat",
    '    echo "  8  系统诊断"\n    echo "  9  卸载 Host Agent"\n    echo "  0  退出"\n    echo\n    prompt_choice "0-9" n || exit 0\n',
    '    echo "  8  系统诊断"\n    echo "  9  清理 Host 系统空间"\n    echo " 10  卸载 Host Agent"\n    echo "  0  退出"\n    echo\n    prompt_choice "0-10" n || exit 0\n',
)
replace_once(
    "scripts/xnat",
    '      8) interactive_diagnostics ;;\n      9) ui_section "卸载 Host Agent"; cmd_uninstall; exit 0 ;;\n      0) exit 0 ;;\n',
    '      8) interactive_diagnostics ;;\n      9) ui_section "清理 Host 系统空间"; cmd_host_cleanup; pause_return ;;\n      10) ui_section "卸载 Host Agent"; cmd_uninstall; exit 0 ;;\n      0) exit 0 ;;\n',
)
replace_once("scripts/xnat", '  uninstall) shift; cmd_uninstall "$@" ;;\n', '  uninstall) shift; cmd_uninstall "$@" ;;\n  cleanup) shift; cmd_host_cleanup "$@" ;;\n')
replace_once(
    "scripts/xnat",
    '  *) die "用法：xnat [version|status|restart|logs|doctor|backup|restore|update|token|panel-ip|firewall|domain|cloudflare|uninstall]；诊断报告：xnat doctor report" ;;\n',
    '  *) die "用法：xnat [version|status|restart|logs|doctor|backup|restore|update|cleanup|token|panel-ip|firewall|domain|cloudflare|uninstall]；诊断报告：xnat doctor report" ;;\n',
)

# README release summary / Host sizing / upgrade instructions.
replace_once("README.md", "**当前正式版本：XNAT v1.6.3**", "**当前正式版本：XNAT v1.6.4**")
replace_once("README.md", "| XNAT Release | v1.6.3 |\n| Panel | v1.6.3 |\n| Host Agent | v1.2.0 |", "| XNAT Release | v1.6.4 |\n| Panel | v1.6.3 |\n| Host Agent | v1.2.1 |")
replace_regex(
    "README.md",
    r"> v1\.6\.3 为 Panel 修复版本：.*?真实物理占用仍按 Host Agent 原始上报值执行存储水位保护。",
    "> v1.6.4 为 Host 运维修复版本：Host Agent v1.2.1 增加系统盘低空间保护与白名单安全清理，Host 安装器明确区分最低安装配置与长期运行建议配置；Panel 保持 v1.6.3，Agent API / Mobile API 均保持 v1。",
)
replace_once(
    "README.md",
    "| 模式 | Host 基线 | 说明 |\n| --- | --- | --- |\n| LXC | 1C / 1GB / 4.5GiB 总硬盘 | 从当前可用空间预留约 1GiB 给系统/XNAT，再计算 natpool |\n| KVM | 1C / 1GB / 6.5GiB 总硬盘 | 需要可用 `/dev/kvm`，预留约 1.5GiB，natpool 至少 4GiB |\n| LXC + KVM | 同 KVM | 同时开放两种实例类型 |",
    "| 模式 | 最低安装配置 | 建议配置 | 说明 |\n| --- | --- | --- | --- |\n| LXC | 1C / 1GB / 4.5GiB 总硬盘 | 1C / 1GB / 8GiB+ | 最低值可安装但仅建议测试或少量轻量实例；系统盘需持续保留运行余量 |\n| KVM | 1C / 1GB / 6.5GiB 总硬盘 + `/dev/kvm` | 2C / 2GB / 12GiB+ | natpool 至少 4GiB |\n| LXC + KVM | 同 KVM | 2C / 2GB / 12GiB+ | 同时开放两种实例类型 |",
)
replace_regex(
    "README.md",
    r"## 升级到 v1\.6\.3\n.*?更早版本的升级历史与兼容说明请查看 \[CHANGELOG\.md\]\(CHANGELOG\.md\) 和 \[docs/README\.md\]\(docs/README\.md\)。",
    '''## 升级到 v1.6.4

现有 **Host Agent v1.2.0**：

```bash
xnat update 1.6.4
```

升级器会保留 Agent Token、TLS、`/etc/xnat/node.json`、Incus、natpool、现有 VPS 与端口映射，并继续使用 Agent API v1。

Panel 业务组件保持 **v1.6.3**，无需为本次 Host 修复单独升级 Panel 业务代码；Android / Mobile API 也无需改动。

Host 菜单新增“清理 Host 系统空间”，仅清理 APT 下载缓存、受限 journal 历史与 XNAT 临时健康检查文件；不会触碰 `/var/lib/incus/disks`、natpool、VPS 或用户数据。Agent 在创建/重装/端口映射前检测到系统盘严重不足时，会先自动执行同一白名单安全清理，仍不足则 fail closed 并返回明确错误。

更早版本的升级历史与兼容说明请查看 [CHANGELOG.md](CHANGELOG.md) 和 [docs/README.md](docs/README.md)。''',
)

# Changelog.
changelog = Path("CHANGELOG.md")
ct = changelog.read_text(encoding="utf-8")
entry = '''## v1.6.4

- Host Agent 升级至 v1.2.1；Panel 保持 v1.6.3，Agent API v1 与 Mobile API v1 均不变。
- Host Agent 在创建 VPS、重装和新增端口映射前检查 Host 根分区可用空间；低于 512MiB 时自动执行一次白名单安全清理，清理后低于 768MiB 则以明确的 507 错误停止操作。
- 自动清理严格限制为 APT 下载缓存与 systemd journal 历史，不触碰 Incus storage、镜像、实例磁盘、natpool、VPS、Agent Token/TLS 或用户数据。
- `xnat` Host 菜单新增“清理 Host 系统空间”，显示清理前后可用空间；同时提供 `xnat cleanup` 命令。
- Host 安装器将 4.5GiB / 6.5GiB 明确标为最低安装配置，并新增 LXC 8GiB+、KVM/混合 12GiB+ 的长期运行建议提示；不改变既有技术安装门槛与 natpool 容量算法。
- Host 版本检查以 Host Agent 组件版本为主，不再把 repo-wide XNAT Release 当作 Host 主版本展示；仅在 Agent 版本相同但管理脚本有更新时提示“Host 管理脚本可同步”。
- 保持 v1.2.0 → v1.2.1 原地升级：继续保留 `.env`、TLS、`/etc/xnat/node.json`、Incus、natpool、现有 VPS 与端口映射。

'''
if not ct.startswith("# Changelog\n\n"):
    raise SystemExit("CHANGELOG.md header mismatch")
changelog.write_text("# Changelog\n\n" + entry + ct[len("# Changelog\n\n"):], encoding="utf-8")

# Release notes for a Host-only patch.
replace_regex(
    "scripts/build-release.sh",
    r'cat > "\$DIST/RELEASE_NOTES\.md" <<EOF_NOTES\n.*?\nEOF_NOTES',
    '''cat > "$DIST/RELEASE_NOTES.md" <<EOF_NOTES
# XNAT v${RELEASE_VERSION}

本次版本为 Host 运维修复版本。

- Panel：v${PANEL_VERSION}（业务组件保持不变）
- Host Agent：v${AGENT_VERSION}
- Agent API：v${AGENT_API_VERSION}
- Mobile API：v1
- Host Agent 新增根分区低空间保护：低于安全阈值时先执行白名单安全清理，仍不足则明确阻止创建/重装/端口映射
- Host 菜单新增“清理 Host 系统空间”，仅清理 APT 下载缓存、受限 journal 历史与 XNAT 临时健康检查文件
- 不删除 Incus storage、镜像、实例磁盘、natpool、VPS、Agent Token/TLS 或用户数据
- Host 安装提示区分最低配置与建议配置：LXC 建议 8GiB+，KVM / 混合建议 12GiB+
- Host 版本检查以 Host Agent 版本为主，repo-wide Release 仅作为管理脚本发布来源
- v1.2.0 → v${AGENT_VERSION} 支持原地升级，Agent API 保持 v${AGENT_API_VERSION}

**由 𝐍𝐀𝐌𝐄𝐋𝐄𝐒𝐒 和 GPT 倾力打造**

Host 推荐升级命令：

    xnat update ${RELEASE_VERSION}
EOF_NOTES''',
)

# Regression suite: permit host-only Release/Panel version divergence and add v1.6.4 guards.
replace_once("scripts/check.sh", "grep -q '组件版本相同，但当前 Release' scripts/xnat\n", "grep -q 'Host 管理脚本可同步' scripts/xnat\n")
replace_once("scripts/check.sh", "assert '当前正式版本：XNAT v1.6.3' in readme\n", "release=(Path('VERSION').read_text().strip())\nassert f'当前正式版本：XNAT v{release}' in readme\n")
replace_once("scripts/check.sh", "assert release == panel, f'release/panel version mismatch: {release} / {panel}'\n", "assert meta['release_version'] == release, f'release metadata mismatch: {release} / {meta.get(\"release_version\")}'\n")
v164_guard = r'''
# v1.6.4 Host disk-pressure guard / safe cleanup / Host-centric version UX.
grep -q 'ROOT_MIN_FREE_MB' agent/natvps_agent/main.py
grep -q 'ROOT_RESUME_FREE_MB' agent/natvps_agent/main.py
grep -q 'def safe_cleanup_host_space' agent/natvps_agent/main.py
grep -q 'def ensure_host_root_space' agent/natvps_agent/main.py
grep -q 'status_code=507' agent/natvps_agent/main.py
grep -q 'ensure_host_root_space("创建 VPS")' agent/natvps_agent/main.py
grep -q 'ensure_host_root_space("重装 VPS")' agent/natvps_agent/main.py
grep -q 'ensure_host_root_space("创建端口映射")' agent/natvps_agent/main.py
grep -q 'root_free_mb' agent/natvps_agent/main.py
grep -q 'cmd_host_cleanup()' scripts/xnat
grep -q '清理 Host 系统空间' scripts/xnat
grep -q 'xnat cleanup' CHANGELOG.md
grep -q '最低安装：1C / 1GB / 4.5GiB' scripts/install-host.sh
grep -q '建议配置：1C / 1GB / 8GiB+' scripts/install-host.sh
grep -q '建议配置：2C / 2GB / 12GiB+' scripts/install-host.sh
grep -q 'Host Agent v${current} 已是最新版本' scripts/xnat
grep -q 'Host 管理脚本可同步' scripts/xnat
python3 - <<'PYV164'
from pathlib import Path
import json
meta=json.loads(Path('release.json').read_text())
assert Path('VERSION').read_text().strip() == '1.6.4'
assert Path('panel/VERSION').read_text().strip() == '1.6.3'
assert Path('agent/VERSION').read_text().strip() == '1.2.1'
assert meta['release_version'] == '1.6.4'
assert meta['panel_version'] == '1.6.3'
assert meta['agent_version'] == '1.2.1'
assert str(meta['agent_api_version']) == '1'
agent=Path('agent/natvps_agent/main.py').read_text()
cleanup=agent.split('def safe_cleanup_host_space',1)[1].split('def ensure_host_root_space',1)[0]
for forbidden in ['/var/lib/incus', 'incus delete', 'storage delete', 'image delete']:
    assert forbidden not in cleanup, f'unsafe auto-clean token present: {forbidden}'
assert '["apt-get", "clean"]' in cleanup
assert '["journalctl", "--vacuum-size=50M"]' in cleanup
cli=Path('scripts/xnat').read_text()
manual=cli.split('cmd_host_cleanup(){',1)[1].split('cmd_uninstall(){',1)[0]
for forbidden in ['/var/lib/incus', 'incus delete', 'storage delete', 'image delete']:
    assert forbidden not in manual, f'unsafe manual-clean token present: {forbidden}'
assert 'apt-get clean' in manual and 'journalctl --vacuum-size=50M' in manual
print('v1.6.4 Host safety + version UX contract: ok')
PYV164

'''
marker = "# v1.3.2 Mobile API v1 contract for XNAT Android v1.0.0.\n"
check = Path("scripts/check.sh")
st = check.read_text(encoding="utf-8")
if st.count(marker) != 1:
    raise SystemExit("scripts/check.sh v1.3.2 marker mismatch")
check.write_text(st.replace(marker, v164_guard + marker, 1), encoding="utf-8")
