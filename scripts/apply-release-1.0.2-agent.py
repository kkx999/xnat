#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "agent/natvps_agent/main.py"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        if new in text:
            print(f"[skip] {label}")
            return text
        raise RuntimeError(f"missing marker: {label}")
    return text.replace(old, new, 1)


def regex_once(text: str, pattern: str, replacement: str, label: str, flags: int = 0) -> str:
    result, count = re.subn(pattern, replacement, text, count=1, flags=flags)
    if count != 1:
        raise RuntimeError(f"regex marker count={count}: {label}")
    return result


agent = TARGET.read_text(encoding="utf-8")
agent = agent.replace('AGENT_VERSION = "1.0.0"', 'AGENT_VERSION = "1.0.1"')
agent = agent.replace('AGENT_API_VERSION = "1"', 'AGENT_API_VERSION = "2"')
agent = replace_once(
    agent,
    "_HOST_CLEANUP_LOCK = threading.Lock()\n",
    "_HOST_CLEANUP_LOCK = threading.Lock()\n_NONCE_LOCK = threading.Lock()\n_SEEN_NONCES: dict[str, int] = {}\n",
    "nonce state",
)

agent = regex_once(
    agent,
    r'''def expected_signature\(timestamp: str, method: str, path: str, body: bytes\) -> str:\n.*?\n\ndef instance_exists''',
    '''def expected_signature(timestamp: str, method: str, path: str, body: bytes, nonce: str = "") -> str:
    digest = hashlib.sha256(body).hexdigest()
    if nonce:
        message = f"{timestamp}\\n{nonce}\\n{method.upper()}\\n{path}\\n{digest}".encode()
    else:
        message = f"{timestamp}\\n{method.upper()}\\n{path}\\n{digest}".encode()
    return hmac.new(AGENT_TOKEN.encode(), message, hashlib.sha256).hexdigest()


def _consume_nonce(nonce: str, ts_int: int) -> bool:
    now = int(time.time())
    with _NONCE_LOCK:
        expired = [key for key, seen_at in _SEEN_NONCES.items() if now - seen_at > 120]
        for key in expired:
            _SEEN_NONCES.pop(key, None)
        if nonce in _SEEN_NONCES:
            return False
        _SEEN_NONCES[nonce] = ts_int
        return True


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    if request.url.path in {"/health"}:
        return await call_next(request)
    ts = request.headers.get("X-NAT-Timestamp", "")
    sig = request.headers.get("X-NAT-Signature", "")
    nonce = request.headers.get("X-NAT-Nonce", "").strip()
    try:
        ts_int = int(ts)
    except Exception:
        return JSONResponse({"detail": "缺少签名时间"}, status_code=401)
    if abs(int(time.time()) - ts_int) > 60:
        return JSONResponse({"detail": "请求签名已过期"}, status_code=401)
    body = await request.body()

    if nonce:
        expected = expected_signature(ts, request.method, request.url.path, canonical_body(body), nonce)
        if not hmac.compare_digest(sig, expected):
            return JSONResponse({"detail": "签名无效"}, status_code=401)
        if not _consume_nonce(nonce, ts_int):
            return JSONResponse({"detail": "请求 nonce 已使用，拒绝重放"}, status_code=409)
    else:
        # Compatibility bootstrap only: old cached capability may read status once.
        if not (request.method.upper() == "GET" and request.url.path == "/v1/status"):
            return JSONResponse({"detail": "Agent API 2 要求 X-NAT-Nonce，请先升级 Panel"}, status_code=426)
        expected = expected_signature(ts, request.method, request.url.path, canonical_body(body))
        if not hmac.compare_digest(sig, expected):
            return JSONResponse({"detail": "签名无效"}, status_code=401)
    return await call_next(request)


def instance_exists''',
    "API 2 HMAC nonce middleware",
    flags=re.S,
)

agent = regex_once(
    agent,
    r'''def disk_size_value\(disk_gb: float\) -> str:\n.*?\n\ndef instance_virtualization_type''',
    '''def disk_size_value(disk_gb: float) -> str:
    # Convert the Panel's GiB value to an exact Incus MiB size.
    mib = int(round(float(disk_gb) * 1024))
    if mib < 128:
        raise RuntimeError("LXC 实例系统盘至少需要 128 MiB")
    return f"{mib}MiB"


def image_min_disk_gb(image_alias: str, virtualization_type: str = "lxc") -> float:
    alias = str(image_alias or "").strip().lower()
    if alias.startswith("images:alpine/"):
        minimum = 1.0
    elif alias.startswith("images:ubuntu/") or alias.startswith("images:debian/"):
        minimum = 2.0
    else:
        minimum = 1.0
    if str(virtualization_type or "lxc").strip().lower() == "kvm":
        minimum = max(minimum, 4.0)
    return minimum


def validate_image_resources(image_alias: str, disk_gb: float, virtualization_type: str = "lxc"):
    minimum = image_min_disk_gb(image_alias, virtualization_type)
    current = float(disk_gb or 0)
    if current + 1e-9 < minimum:
        label = str(image_alias or "系统镜像")
        raise HTTPException(
            422,
            f"{label} 最低需要 {minimum:g} GiB 系统盘，当前配置为 {current:g} GiB",
        )


def preflight_image(image_alias: str, disk_gb: float, virtualization_type: str = "lxc"):
    validate_image_resources(image_alias, disk_gb, virtualization_type)
    proc = run(["incus", "image", "info", image_alias], check=False, timeout=120)
    if proc.returncode != 0:
        detail = _clean_command_output(proc.stderr) or _clean_command_output(proc.stdout)
        raise HTTPException(422, f"目标系统镜像不可用：{detail[-800:] or image_alias}")


def instance_xnat_server_id(name: str) -> int | None:
    proc = run(["incus", "config", "get", name, "user.xnat.server_id"], check=False, timeout=20)
    raw = (proc.stdout or "").strip()
    try:
        return int(raw) if raw else None
    except ValueError:
        return None


def instance_status(name: str) -> str:
    proc = run(["incus", "info", name, "--format", "json"], check=False, timeout=20)
    if proc.returncode != 0:
        return "unknown"
    try:
        status = str(json.loads(proc.stdout or "{}").get("status") or "").lower()
        if status == "running":
            return "running"
        if status in {"stopped", "frozen"}:
            return "stopped"
        return status or "unknown"
    except Exception:
        return "unknown"


def instance_virtualization_type''',
    "image resource preflight helpers",
    flags=re.S,
)

# Minimal-image SSH readiness: install the command we use, and keep a fallback.
agent = agent.replace(
    "apk add --no-cache openssh ca-certificates",
    "apk add --no-cache openssh ca-certificates iproute2",
)
agent = agent.replace(
    "apt-get install -y --no-install-recommends openssh-server ca-certificates",
    "apt-get install -y --no-install-recommends openssh-server ca-certificates iproute2",
)
agent = agent.replace(
    "ss -lnt | grep ':22 ' >/dev/null",
    "(command -v ss >/dev/null 2>&1 && ss -lnt | grep ':22 ' >/dev/null) || "
    "(command -v netstat >/dev/null 2>&1 && netstat -lnt | grep ':22 ' >/dev/null)",
)
agent = agent.replace(
    "ss -lnt '( sport = :22 )' | grep 'LISTEN' >/dev/null",
    "(command -v ss >/dev/null 2>&1 && ss -lnt '( sport = :22 )' | grep 'LISTEN' >/dev/null) || "
    "(command -v netstat >/dev/null 2>&1 && netstat -lnt | grep ':22 ' >/dev/null)",
)

agent = replace_once(
    agent,
    'def launch(name: str, image_alias: str, memory_mb: int, disk_gb: float, cpu: int, bandwidth_mbps: int, virtualization_type: str = "lxc"):\n'
    '    mode = require_virtualization_allowed(virtualization_type)\n'
    '    args = [\n',
    'def launch(name: str, image_alias: str, memory_mb: int, disk_gb: float, cpu: int, bandwidth_mbps: int, virtualization_type: str = "lxc", server_id: int | None = None):\n'
    '    mode = require_virtualization_allowed(virtualization_type)\n'
    '    validate_image_resources(image_alias, disk_gb, mode)\n'
    '    args = [\n',
    "launch image guard",
)
agent = replace_once(
    agent,
    '    if mode == "kvm":\n        if memory_mb < 512:\n',
    '    if server_id is not None:\n'
    '        args.extend(["--config", f"user.xnat.server_id={int(server_id)}"])\n'
    '    if mode == "kvm":\n'
    '        if memory_mb < 512:\n',
    "launch XNAT identity metadata",
)

agent = regex_once(
    agent,
    r'''@app\.post\("/v1/provision"\)\ndef provision\(body: ProvisionBody\):\n.*?\n\n@app\.post\("/v1/instances/\{instance_id\}/power"\)''',
    '''@app.post("/v1/provision")
def provision(body: ProvisionBody):
    require_instance(body.instance_name)
    require_nat_port_allowed(body.ssh_port)
    ensure_host_root_space("创建 VPS")
    mode = require_virtualization_allowed(body.virtualization_type)
    preflight_image(body.image_alias, body.disk_gb, mode)

    if instance_exists(body.instance_name):
        stored_server_id = instance_xnat_server_id(body.instance_name)
        if stored_server_id != int(body.server_id):
            raise HTTPException(409, "实例名称已存在，但 XNAT server_id 不匹配，拒绝接管")
        if instance_virtualization_type(body.instance_name) != mode:
            raise HTTPException(409, "已存在实例的虚拟化类型与本次请求不一致")
        password = random_password()
        private_ip = wait_ipv4(body.instance_name, mode)
        prepare_ssh(body.instance_name, password)
        device = f"ssh-{body.ssh_port}"
        run(["incus", "config", "device", "remove", body.instance_name, device], check=False, timeout=35)
        add_ssh_proxy(body.instance_name, body.ssh_port)
        return {
            "instance_id": body.instance_name,
            "private_ip": private_ip,
            "ssh_port": body.ssh_port,
            "status": instance_status(body.instance_name),
            "root_password": password,
            "virtualization_type": mode,
            "idempotent_replay": True,
        }

    password = random_password()
    try:
        launch(
            body.instance_name, body.image_alias, body.memory_mb, body.disk_gb,
            body.cpu, body.bandwidth_mbps, mode, server_id=body.server_id,
        )
        private_ip = wait_ipv4(body.instance_name, mode)
        prepare_ssh(body.instance_name, password)
        add_ssh_proxy(body.instance_name, body.ssh_port)
        return {
            "instance_id": body.instance_name,
            "private_ip": private_ip,
            "ssh_port": body.ssh_port,
            "status": "running",
            "root_password": password,
            "virtualization_type": mode,
            "idempotent_replay": False,
        }
    except HostRootSpaceError as exc:
        delete_instance(body.instance_name)
        raise HTTPException(507, str(exc)[:1800])
    except HTTPException:
        raise
    except Exception as exc:
        delete_instance(body.instance_name)
        raise HTTPException(500, str(exc)[:1800])


@app.get("/v1/servers/{server_id}/instances/{instance_name}")
def recover_instance_identity(server_id: int, instance_name: str):
    require_instance(instance_name)
    if not instance_exists(instance_name):
        return {"exists": False, "matches": False}
    stored = instance_xnat_server_id(instance_name)
    return {
        "exists": True,
        "matches": stored == int(server_id),
        "server_id": stored,
        "instance_id": instance_name,
        "status": instance_status(instance_name),
        "virtualization_type": instance_virtualization_type(instance_name),
    }


@app.post("/v1/instances/{instance_id}/power")''',
    "idempotent provision",
    flags=re.S,
)

agent = regex_once(
    agent,
    r'''@app\.post\("/v1/instances/\{instance_id\}/reinstall"\)\ndef reinstall\(instance_id: str, body: ReinstallBody\):\n.*?\n\n@app\.delete\("/v1/instances/\{instance_id\}"\)''',
    '''@app.post("/v1/instances/{instance_id}/reinstall")
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
    backup_name = (instance_id[:58] + "-xnat-old-" + secrets.token_hex(4))[:79]

    # Keep the old instance until the replacement is fully ready. If anything
    # fails, restore the old name and previous power state.
    if old_status == "running":
        run(["incus", "stop", instance_id, "--timeout", "20", "--force"], timeout=45)
    try:
        run(["incus", "move", instance_id, backup_name], timeout=120)
    except Exception:
        if old_status == "running" and instance_exists(instance_id):
            run(["incus", "start", instance_id], check=False, timeout=65)
        raise HTTPException(500, "安全重装预备阶段失败，原实例未删除")

    password = random_password()
    try:
        launch(
            instance_id, body.image_alias, body.memory_mb, body.disk_gb,
            body.cpu, body.bandwidth_mbps, mode, server_id=stored_server_id,
        )
        private_ip = wait_ipv4(instance_id, mode)
        prepare_ssh(instance_id, password)
        add_ssh_proxy(instance_id, body.ssh_port)
        delete_instance(backup_name)
        return {
            "instance_id": instance_id,
            "private_ip": private_ip,
            "ssh_port": body.ssh_port,
            "status": "running",
            "root_password": password,
            "virtualization_type": mode,
            "rollback_safe": True,
        }
    except Exception as exc:
        delete_instance(instance_id)
        rollback_error = ""
        try:
            run(["incus", "move", backup_name, instance_id], timeout=120)
            if old_status == "running":
                run(["incus", "start", instance_id], timeout=65)
        except Exception as rollback_exc:
            rollback_error = f"；原实例自动恢复失败: {str(rollback_exc)[:500]}"
        if isinstance(exc, HTTPException):
            detail = str(exc.detail)
            status_code = int(exc.status_code)
        else:
            detail = str(exc)
            status_code = 500
        raise HTTPException(
            status_code,
            f"新系统部署失败，已尝试恢复原实例: {detail[:1000]}{rollback_error}",
        )


@app.delete("/v1/instances/{instance_id}")''',
    "rollback-safe reinstall",
    flags=re.S,
)

TARGET.write_text(agent, encoding="utf-8")
print("Agent patch complete")
