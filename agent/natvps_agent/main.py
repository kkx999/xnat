from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import shutil
import socket
import string
import subprocess
import shlex
import threading
import time
from pathlib import Path

import psutil
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field
from .metrics import collect as collect_instance_metrics
from fastapi.responses import JSONResponse

AGENT_VERSION = "1.0.4"
AGENT_API_VERSION = "2"
AGENT_TOKEN = os.getenv("AGENT_TOKEN", "")
STORAGE_POOL = os.getenv("INCUS_STORAGE_POOL", "natpool")
BRIDGE_NAME = os.getenv("INCUS_BRIDGE", "incusbr0")
PUBLIC_IP = os.getenv("HOST_PUBLIC_IP", "")
AGENT_PORT = int(os.getenv("AGENT_PORT", "29443"))
NODE_CONFIG_PATH = Path(os.getenv("XNAT_NODE_CONFIG", "/etc/xnat/node.json"))
TIMEOUT = int(os.getenv("INCUS_PROVISION_TIMEOUT", "180"))
HOST_ROOT_MIN_FREE_MB = max(128, int(os.getenv("HOST_ROOT_MIN_FREE_MB", "512")))
HOST_JOURNAL_MAX_MB = max(10, int(os.getenv("HOST_JOURNAL_MAX_MB", "50")))
_HOST_CLEANUP_LOCK = threading.Lock()
_NONCE_LOCK = threading.Lock()
_SEEN_NONCES: dict[str, int] = {}

if not AGENT_TOKEN:
    raise RuntimeError("AGENT_TOKEN 未配置")

app = FastAPI(title="NAT VPS Host Agent", version=AGENT_VERSION)

class _MetricsAccessFilter(logging.Filter):
    def filter(self, record):
        try:
            return "/metrics HTTP/" not in record.getMessage()
        except Exception:
            return True

logging.getLogger("uvicorn.access").addFilter(_MetricsAccessFilter())

INSTANCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
DEVICE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")


def load_node_config() -> dict:
    try:
        payload = json.loads(NODE_CONFIG_PATH.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception:
        return {}


def save_node_config(payload: dict):
    NODE_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = NODE_CONFIG_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(NODE_CONFIG_PATH)


def nat_port_pool() -> tuple[int | None, int | None]:
    cfg = load_node_config()
    try:
        start = int(cfg.get("nat_port_start"))
        end = int(cfg.get("nat_port_end"))
    except (TypeError, ValueError):
        return None, None
    if not (1024 <= start <= end <= 65535):
        return None, None
    return start, end


def kvm_available() -> bool:
    path = Path("/dev/kvm")
    return path.exists() and os.access(path, os.R_OK | os.W_OK)


def configured_virtualization_modes() -> list[str]:
    cfg = load_node_config()
    raw = cfg.get("virtualization_modes", ["lxc"])
    if isinstance(raw, str):
        raw = [part.strip() for part in raw.split(",")]
    if not isinstance(raw, list):
        raw = ["lxc"]
    modes = []
    for item in raw:
        mode = str(item or "").strip().lower()
        if mode in {"lxc", "kvm"} and mode not in modes:
            modes.append(mode)
    return modes or ["lxc"]


def require_virtualization_allowed(mode: str) -> str:
    mode = (mode or "lxc").strip().lower()
    if mode not in {"lxc", "kvm"}:
        raise HTTPException(400, f"不支持的虚拟化类型: {mode}")
    configured = configured_virtualization_modes()
    if mode not in configured:
        raise HTTPException(409, f"当前 Host 未启用 {mode.upper()} 虚拟化")
    if mode == "kvm" and not kvm_available():
        raise HTTPException(409, "当前 Host 已配置 KVM，但 /dev/kvm 不可用；请检查上层宿主机是否开放 Nested Virtualization")
    return mode


def require_nat_port_allowed(port: int):
    start, end = nat_port_pool()
    if start is None or end is None:
        raise HTTPException(409, "节点尚未配置 NAT 端口池，请先在 Panel 后台配置")
    if port == AGENT_PORT:
        raise HTTPException(400, f"公网端口 {port} 与 Host Agent 管理端口冲突")
    if not (start <= port <= end):
        raise HTTPException(400, f"公网端口 {port} 不在节点允许的 NAT 端口池 {start}-{end} 内")


def current_proxy_public_ports_by_protocol() -> dict[str, set[int]]:
    """Collect live Incus proxy ports grouped by protocol."""
    result: dict[str, set[int]] = {"tcp": set(), "udp": set()}
    rows = run(["incus", "list", "--format", "json"], check=False, timeout=30)
    if rows.returncode != 0:
        return result
    try:
        instances = json.loads(rows.stdout or "[]")
    except Exception:
        return result

    listen_re = re.compile(r"^(tcp|udp):(?:0\\.0\\.0\\.0|\\[::\\]|[^:]+):(\\d+)$", re.I)
    for row in instances:
        name = str(row.get("name") or "")
        if not name:
            continue
        proc = run(["incus", "query", f"/1.0/instances/{name}?recursion=1"], check=False, timeout=25)
        if proc.returncode != 0:
            continue
        try:
            payload = json.loads(proc.stdout or "{}")
        except Exception:
            continue
        meta = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else payload
        devices = meta.get("expanded_devices") or meta.get("devices") or {}
        if not isinstance(devices, dict):
            continue
        for dev in devices.values():
            if not isinstance(dev, dict) or str(dev.get("type") or "") != "proxy":
                continue
            listen = str(dev.get("listen") or "")
            match = listen_re.match(listen)
            if match:
                try:
                    protocol = match.group(1).lower()
                    result.setdefault(protocol, set()).add(int(match.group(2)))
                except (TypeError, ValueError):
                    pass
    return result


def current_proxy_public_ports() -> set[int]:
    """Return all live proxy ports regardless of protocol."""
    grouped = current_proxy_public_ports_by_protocol()
    return set(grouped.get("tcp", set())) | set(grouped.get("udp", set()))


def _clean_command_output(text: str) -> str:
    lines = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        # Debian may emit this harmless warning on minimal images. Do not let
        # it hide the actual command failure returned elsewhere.
        if stripped.startswith("debconf: delaying package configuration, since apt-utils is not installed"):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def _command_error(args, proc) -> str:
    command = shlex.join(str(x) for x in args)
    stderr = _clean_command_output(proc.stderr)
    stdout = _clean_command_output(proc.stdout)
    parts = [f"命令失败 (exit={proc.returncode}): {command}"]
    # Keep the tail: apt/systemd failures are normally at the end.
    if stderr:
        parts.append(f"stderr: {stderr[-900:]}")
    if stdout:
        parts.append(f"stdout: {stdout[-900:]}")
    if len(parts) == 1:
        parts.append("无命令输出")
    return "\n".join(parts)


def run(args, *, input_text=None, timeout=None, check=True):
    effective_timeout = timeout or TIMEOUT
    try:
        proc = subprocess.run(
            args, input=input_text, text=True, capture_output=True,
            timeout=effective_timeout
        )
    except subprocess.TimeoutExpired as exc:
        command = shlex.join(str(x) for x in args)
        stderr = _clean_command_output(exc.stderr or "")
        stdout = _clean_command_output(exc.stdout or "")
        detail = stderr or stdout
        suffix = f"；最后输出: {detail[-600:]}" if detail else ""
        raise RuntimeError(f"命令超时 ({effective_timeout}s): {command}{suffix}") from exc
    if check and proc.returncode != 0:
        raise RuntimeError(_command_error(args, proc)[:2200])
    return proc


def host_root_space_mb() -> dict:
    usage = shutil.disk_usage("/")
    mib = 1024 * 1024
    return {
        "total_mb": int(usage.total / mib),
        "used_mb": int(usage.used / mib),
        "free_mb": int(usage.free / mib),
    }


def safe_host_cleanup() -> dict:
    """Free only disposable Host OS caches. Never touch Incus storage/VPS data."""
    with _HOST_CLEANUP_LOCK:
        before = host_root_space_mb()["free_mb"]
        actions = []

        if shutil.which("apt-get"):
            proc = run(["apt-get", "clean"], check=False, timeout=120)
            actions.append({"name": "apt-cache", "ok": proc.returncode == 0})

        if shutil.which("journalctl"):
            proc = run(["journalctl", f"--vacuum-size={HOST_JOURNAL_MAX_MB}M"], check=False, timeout=120)
            actions.append({"name": "journal", "ok": proc.returncode == 0})

        after = host_root_space_mb()["free_mb"]
        print(
            f"[XNAT] Host 安全清理完成：系统盘可用 {before} MiB -> {after} MiB",
            flush=True,
        )
        return {
            "before_free_mb": before,
            "after_free_mb": after,
            "freed_mb": max(0, after - before),
            "actions": actions,
        }


def ensure_host_root_space(context: str = "操作") -> dict:
    space = host_root_space_mb()
    if space["free_mb"] >= HOST_ROOT_MIN_FREE_MB:
        return space

    print(
        f"[XNAT] {context}前检测到 Host 系统盘仅剩 {space['free_mb']} MiB，开始安全自动清理",
        flush=True,
    )
    cleanup = safe_host_cleanup()
    space = host_root_space_mb()
    if space["free_mb"] < HOST_ROOT_MIN_FREE_MB:
        raise HTTPException(
            status_code=507,
            detail=(
                f"Host 系统盘可用空间不足：自动安全清理后仅剩 {space['free_mb']} MiB，"
                f"至少需要 {HOST_ROOT_MIN_FREE_MB} MiB；请扩容 Host 系统盘或运行 xnat 的『清理 Host 系统空间』"
            ),
        )
    return {**space, "cleanup": cleanup}


class HostRootSpaceError(RuntimeError):
    """Host root filesystem cannot safely accept more Incus runtime writes."""



def _is_no_space_text(text: str) -> bool:
    lowered = str(text or "").lower()
    return "no space left on device" in lowered or "enospc" in lowered


def _is_no_space_error(exc: Exception) -> bool:
    return _is_no_space_text(str(exc))


def _proxy_log_tail(name: str, device: str) -> str:
    if not INSTANCE_RE.fullmatch(name or "") or not DEVICE_RE.fullmatch(device or ""):
        return ""
    try:
        path = Path("/var/log/incus") / name / f"proxy.{device}.log"
        return path.read_text(encoding="utf-8", errors="replace")[-4000:]
    except Exception:
        return ""


def require_instance(name: str):
    if not INSTANCE_RE.fullmatch(name or ""):
        raise HTTPException(400, "实例名称无效")


def require_device(name: str):
    if not DEVICE_RE.fullmatch(name or ""):
        raise HTTPException(400, "设备名称无效")


def canonical_body(raw: bytes) -> bytes:
    return raw or b""


def expected_signature(timestamp: str, method: str, path: str, body: bytes, nonce: str = "") -> str:
    digest = hashlib.sha256(body).hexdigest()
    if nonce:
        message = f"{timestamp}\n{nonce}\n{method.upper()}\n{path}\n{digest}".encode()
    else:
        message = f"{timestamp}\n{method.upper()}\n{path}\n{digest}".encode()
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


def instance_exists(name: str) -> bool:
    return run(["incus", "info", name], check=False, timeout=20).returncode == 0


def delete_instance(name: str):
    if instance_exists(name):
        run(["incus", "delete", name, "--force"], check=False, timeout=70)


def random_password(length=20):
    chars = string.ascii_letters + string.digits + "!@#_-"
    return "".join(secrets.choice(chars) for _ in range(length))


def disk_size_value(disk_gb: float) -> str:
    # Convert the Panel's GiB value to an exact Incus MiB size.
    mib = int(round(float(disk_gb) * 1024))
    if mib < 128:
        raise RuntimeError("LXC 实例系统盘至少需要 128 MiB")
    return f"{mib}MiB"


def image_min_disk_gb(image_alias: str, virtualization_type: str = "lxc") -> float:
    # Per-image business minimums are owned by Panel. The Agent only keeps
    # virtualization-level technical floors so it cannot contradict Panel config.
    if str(virtualization_type or "lxc").strip().lower() == "kvm":
        return 3.0
    return 0.125


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


def instance_virtualization_type(name: str) -> str:
    proc = run(["incus", "query", f"/1.0/instances/{name}"], check=False, timeout=20)
    if proc.returncode != 0:
        return "lxc"
    try:
        payload = json.loads(proc.stdout or "{}")
        meta = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else payload
        return "kvm" if str(meta.get("type") or "").lower() == "virtual-machine" else "lxc"
    except Exception:
        return "lxc"


def wait_guest_agent(name: str, virtualization_type: str = "lxc"):
    mode = (virtualization_type or "lxc").strip().lower()
    # Containers can normally exec immediately. KVM VMs enter RUNNING before
    # the in-guest incus-agent is ready, so explicitly wait for the agent.
    if mode != "kvm":
        return
    wait_seconds = 180
    deadline = time.time() + wait_seconds
    last_error = ""
    while time.time() < deadline:
        try:
            proc = run(["incus", "exec", name, "--", "true"], check=False, timeout=12)
            if proc.returncode == 0:
                return
            last_error = _clean_command_output(proc.stderr) or _clean_command_output(proc.stdout)
        except RuntimeError as exc:
            last_error = str(exc)
        time.sleep(3)
    detail = f"；最后错误: {last_error[-500:]}" if last_error else ""
    raise RuntimeError(f"KVM Guest Agent 未能在 {wait_seconds} 秒内就绪{detail}")


def wait_ipv4(name: str, virtualization_type: str = "lxc"):
    mode = (virtualization_type or "lxc").strip().lower()
    wait_guest_agent(name, mode)
    wait_seconds = 120 if mode == "kvm" else 90
    deadline = time.time() + wait_seconds
    last_error = ""
    while time.time() < deadline:
        try:
            proc = run([
                "incus", "exec", name, "--", "sh", "-lc",
                "ip -4 -o addr show scope global | awk '$2 != \"lo\" {print $4; exit}' | cut -d/ -f1",
            ], check=False, timeout=15)
            ip = (proc.stdout or "").strip()
            if ip:
                return ip
            last_error = _clean_command_output(proc.stderr) or _clean_command_output(proc.stdout)
        except RuntimeError as exc:
            last_error = str(exc)
        time.sleep(2 if mode == "kvm" else 1)
    detail = f"；最后错误: {last_error[-500:]}" if last_error else ""
    raise RuntimeError(f"{mode.upper()} 实例未能在 {wait_seconds} 秒内获取 IPv4{detail}")


def guest_os_family(name: str) -> str:
    proc = run([
        "incus", "exec", name, "--", "sh", "-lc",
        "command -v apk >/dev/null 2>&1 && echo alpine || (command -v apt-get >/dev/null 2>&1 && echo apt || true)",
    ], check=False, timeout=20)
    family = (proc.stdout or "").strip().lower()
    return family if family in {"alpine", "apt"} else "unknown"


def restart_ssh_service(name: str):
    family = guest_os_family(name)
    command = (
        "rc-service sshd restart || rc-service sshd start"
        if family == "alpine"
        else "systemctl restart ssh || systemctl restart sshd"
    )
    proc = run(["incus", "exec", name, "--", "sh", "-lc", command], check=False, timeout=35)
    if proc.returncode != 0:
        raise RuntimeError(_command_error(["restart ssh"], proc)[:1200])


def prepare_ssh(name: str, password: str):
    try:
        run(["incus", "exec", name, "--", "chpasswd"], input_text=f"root:{password}\n", timeout=30)
    except Exception as exc:
        raise RuntimeError(f"KVM/LXC SSH 初始化失败 [设置 root 密码]: {exc}") from exc

    family = guest_os_family(name)
    if family == "alpine":
        script = r"""
set -eu
apk add --no-cache openssh ca-certificates iproute2
mkdir -p /run/sshd
ssh-keygen -A
sed -i '/^[#[:space:]]*PermitRootLogin[[:space:]]/d;/^[#[:space:]]*PasswordAuthentication[[:space:]]/d' /etc/ssh/sshd_config
printf '\nPermitRootLogin yes\nPasswordAuthentication yes\n' >> /etc/ssh/sshd_config
sshd -t
sshd -T | grep -x 'permitrootlogin yes' >/dev/null
sshd -T | grep -x 'passwordauthentication yes' >/dev/null
rc-update add sshd default >/dev/null 2>&1 || true
rc-service sshd restart >/dev/null 2>&1 || rc-service sshd start >/dev/null 2>&1
(command -v ss >/dev/null 2>&1 && ss -lnt | grep ':22 ' >/dev/null) || (command -v netstat >/dev/null 2>&1 && netstat -lnt | grep ':22 ' >/dev/null)
"""
        command = ["incus", "exec", name, "--", "sh", "-lc", script]
    elif family == "apt":
        script = r"""
set -Eeuo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends openssh-server ca-certificates iproute2
mkdir -p /run/sshd /etc/ssh/sshd_config.d
cat > /etc/ssh/sshd_config.d/00-00-xnat.conf <<'EOF_XNAT_SSH'
PermitRootLogin yes
PasswordAuthentication yes
EOF_XNAT_SSH
rm -f /etc/ssh/sshd_config.d/00-natvps.conf
TMP_CFG="$(mktemp)"
grep -vFx 'Include /etc/ssh/sshd_config.d/00-00-xnat.conf' /etc/ssh/sshd_config > "$TMP_CFG" || true
{
  printf '%s\n' 'Include /etc/ssh/sshd_config.d/00-00-xnat.conf'
  cat "$TMP_CFG"
} > /etc/ssh/sshd_config
rm -f "$TMP_CFG"
sshd -t
sshd -T | grep -x 'permitrootlogin yes' >/dev/null
sshd -T | grep -x 'passwordauthentication yes' >/dev/null
passwd -S root | awk '$2 == "P" {ok=1} END {exit ok ? 0 : 1}'
systemctl enable --now ssh
systemctl restart ssh
(command -v ss >/dev/null 2>&1 && ss -lnt '( sport = :22 )' | grep 'LISTEN' >/dev/null) || (command -v netstat >/dev/null 2>&1 && netstat -lnt | grep ':22 ' >/dev/null)
"""
        command = ["incus", "exec", name, "--", "bash", "-lc", script]
    else:
        raise RuntimeError("当前系统镜像缺少受支持的包管理器；已支持 Debian / Ubuntu / Alpine")
    try:
        run(command, timeout=300)
    except Exception as exc:
        raise RuntimeError(f"KVM/LXC SSH 初始化失败 [安装/配置 sshd]: {exc}") from exc


def set_eth0_value(instance_id: str, key: str, value: str):
    proc = run(["incus", "config", "device", "set", instance_id, "eth0", f"{key}={value}"], check=False, timeout=30)
    if proc.returncode == 0:
        return
    proc = run(["incus", "config", "device", "override", instance_id, "eth0", f"{key}={value}"], check=False, timeout=30)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "调整 eth0 失败").strip())


def set_bandwidth(instance_id: str, mbps: int):
    if mbps < 0:
        raise RuntimeError("带宽不能小于 0")
    if mbps == 0:
        proc = run(["incus", "config", "device", "unset", instance_id, "eth0", "limits.max"], check=False, timeout=30)
        if proc.returncode == 0:
            return
        proc = run(["incus", "config", "device", "override", instance_id, "eth0"], check=False, timeout=30)
        if proc.returncode != 0 and "already exists" not in ((proc.stderr or "") + (proc.stdout or "")).lower():
            raise RuntimeError((proc.stderr or proc.stdout or "取消限速失败").strip())
        return
    set_eth0_value(instance_id, "limits.max", f"{mbps}Mbit")


def launch(name: str, image_alias: str, memory_mb: int, disk_gb: float, cpu: int, bandwidth_mbps: int, virtualization_type: str = "lxc", server_id: int | None = None):
    mode = require_virtualization_allowed(virtualization_type)
    validate_image_resources(image_alias, disk_gb, mode)
    args = [
        "incus", "launch", image_alias, name, "--storage", STORAGE_POOL,
        "--config", f"limits.cpu={cpu}", "--config", f"limits.memory={memory_mb}MiB",
        "--device", f"root,size={disk_size_value(disk_gb)}",
    ]
    if server_id is not None:
        args.extend(["--config", f"user.xnat.server_id={int(server_id)}"])
    if mode == "kvm":
        if memory_mb < 512:
            raise RuntimeError("KVM 实例内存至少需要 512 MiB")
        if disk_gb < 3:
            raise RuntimeError("KVM 实例系统盘至少需要 3 GiB")
        args.append("--vm")
    run(args, timeout=250 if mode == "kvm" else 190)
    set_bandwidth(name, bandwidth_mbps)


def add_proxy_device(name: str, device: str, protocol: str, public_port: int, private_port: int):
    args = [
        "incus", "config", "device", "add", name, device, "proxy",
        f"listen={protocol}:0.0.0.0:{public_port}",
    ]
    if instance_virtualization_type(name) == "kvm":
        # Incus proxy devices on VMs are supported in NAT mode only. The
        # wildcard target lets Incus follow the VM's DHCP address on incusbr0.
        args.extend([f"connect={protocol}:0.0.0.0:{private_port}", "nat=true"])
    else:
        # Preserve the existing container proxy behavior.
        args.append(f"connect={protocol}:127.0.0.1:{private_port}")
    try:
        run(args, timeout=35)
    except RuntimeError as exc:
        error_text = str(exc)
        no_space = _is_no_space_error(exc)
        if not no_space and ("Failed to start device" in error_text or "Please look in" in error_text):
            no_space = _is_no_space_text(_proxy_log_tail(name, device))
        if not no_space:
            raise

        cleanup = safe_host_cleanup()
        space = host_root_space_mb()
        if space["free_mb"] < HOST_ROOT_MIN_FREE_MB:
            raise HostRootSpaceError(
                f"Host 系统盘可用空间不足：自动安全清理后仅剩 {space['free_mb']} MiB，"
                f"至少需要 {HOST_ROOT_MIN_FREE_MB} MiB（本次释放 {cleanup['freed_mb']} MiB）"
            ) from exc

        # A failed proxy start may leave a device definition behind. Remove
        # only this just-added proxy device before the single retry.
        run(["incus", "config", "device", "remove", name, device], check=False, timeout=35)
        try:
            run(args, timeout=35)
        except RuntimeError as retry_exc:
            retry_text = str(retry_exc)
            retry_no_space = _is_no_space_error(retry_exc)
            if not retry_no_space and ("Failed to start device" in retry_text or "Please look in" in retry_text):
                retry_no_space = _is_no_space_text(_proxy_log_tail(name, device))
            if retry_no_space:
                retry_space = host_root_space_mb()
                raise HostRootSpaceError(
                    f"Host 系统盘空间不足导致 Incus 端口映射启动失败；安全清理后仍无法恢复，"
                    f"当前可用 {retry_space['free_mb']} MiB，请扩容 Host 系统盘"
                ) from retry_exc
            raise


def add_ssh_proxy(name: str, ssh_port: int):
    add_proxy_device(name, f"ssh-{ssh_port}", "tcp", ssh_port, 22)


def _space_from_payload(payload):
    """Return (total_bytes, used_bytes) from Incus resource responses.

    `incus query` has produced both metadata-unwrapped and full API-envelope
    JSON across CLI/API combinations. Accept both, plus nested resource forms.
    """
    if not isinstance(payload, dict):
        return 0.0, 0.0

    candidates = []

    direct = payload.get("space")
    if isinstance(direct, dict):
        candidates.append(direct)

    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        meta_space = metadata.get("space")
        if isinstance(meta_space, dict):
            candidates.append(meta_space)

        meta_resources = metadata.get("resources")
        if isinstance(meta_resources, dict):
            resource_space = meta_resources.get("space")
            if isinstance(resource_space, dict):
                candidates.append(resource_space)

    resources = payload.get("resources")
    if isinstance(resources, dict):
        resource_space = resources.get("space")
        if isinstance(resource_space, dict):
            candidates.append(resource_space)

    # Last-resort shallow recursive search for a space object.
    for value in payload.values():
        if isinstance(value, dict):
            nested = value.get("space")
            if isinstance(nested, dict):
                candidates.append(nested)

    for space in candidates:
        try:
            total = float(space.get("total") or 0)
            used = float(space.get("used") or 0)
        except (TypeError, ValueError):
            continue

        if total > 0:
            return total, max(0.0, used)

    return 0.0, 0.0


def _incus_storage_resource_stats():
    """Read storage capacity from the Incus storage resources endpoint."""
    commands = [
        ["incus", "query", f"/1.0/storage-pools/{STORAGE_POOL}/resources"],
        ["incus", "query", "--raw", f"/1.0/storage-pools/{STORAGE_POOL}/resources"],
    ]

    for command in commands:
        proc = run(command, check=False, timeout=15)
        if proc.returncode != 0:
            continue

        try:
            payload = json.loads(proc.stdout or "{}")
        except Exception:
            continue

        total_bytes, used_bytes = _space_from_payload(payload)
        if total_bytes > 0:
            return total_bytes, used_bytes

    return 0.0, 0.0


def _lvm_thin_storage_stats():
    """Fallback for LVM Thin pools when the Incus resource response is absent.

    Reads the thin-pool LV size and Data% without depending on the Incus JSON
    response shape. This is only used when the Incus resources endpoint did
    not provide a usable total size.
    """
    proc = run(
        [
            "lvs",
            "--reportformat", "json",
            "--units", "b",
            "--nosuffix",
            "-o", "lv_name,vg_name,lv_size,data_percent,lv_attr",
        ],
        check=False,
        timeout=15,
    )
    if proc.returncode != 0:
        return 0.0, 0.0

    try:
        payload = json.loads(proc.stdout or "{}")
        reports = payload.get("report") or []
        rows = []
        for report in reports:
            rows.extend(report.get("lv") or [])
    except Exception:
        return 0.0, 0.0

    thin_rows = []
    for row in rows:
        attr = str(row.get("lv_attr") or "").strip()
        if attr.startswith("t"):
            thin_rows.append(row)

    if not thin_rows:
        return 0.0, 0.0

    # Incus loop-backed LVM pools usually have one thin pool. If there are
    # several, prefer names that contain our Incus storage-pool name or
    # "thinpool"; otherwise choose the largest thin pool.
    def row_score(row):
        name = str(row.get("lv_name") or "").lower()
        vg = str(row.get("vg_name") or "").lower()
        preferred = int(STORAGE_POOL.lower() in name or STORAGE_POOL.lower() in vg)
        thin_named = int("thinpool" in name or "thin" in name)
        try:
            size = float(str(row.get("lv_size") or "0").strip())
        except ValueError:
            size = 0.0
        return preferred, thin_named, size

    row = max(thin_rows, key=row_score)

    try:
        total_bytes = float(str(row.get("lv_size") or "0").strip())
        data_percent_raw = str(row.get("data_percent") or "0").strip()
        data_percent = float(data_percent_raw) if data_percent_raw not in {"", "-"} else 0.0
    except ValueError:
        return 0.0, 0.0

    if total_bytes <= 0:
        return 0.0, 0.0

    used_bytes = total_bytes * max(0.0, min(100.0, data_percent)) / 100.0
    return total_bytes, used_bytes


def storage_stats():
    total_bytes, used_bytes = _incus_storage_resource_stats()
    source = "incus"

    if total_bytes <= 0:
        total_bytes, used_bytes = _lvm_thin_storage_stats()
        source = "lvm"

    if total_bytes <= 0:
        return 0.0, 0.0, "unavailable"

    gib = 1024 ** 3
    return total_bytes / gib, used_bytes / gib, source



_SIZE_UNITS = {
    "B": 1,
    "KB": 1000,
    "KIB": 1024,
    "MB": 1000 ** 2,
    "MIB": 1024 ** 2,
    "GB": 1000 ** 3,
    "GIB": 1024 ** 3,
    "TB": 1000 ** 4,
    "TIB": 1024 ** 4,
}


def _api_metadata(payload):
    """Accept both a full Incus API envelope and `incus query` unwrapped JSON."""
    if not isinstance(payload, dict):
        return {}
    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        return metadata
    return payload


def _size_to_bytes(value):
    text = str(value or "").strip()
    if not text:
        return 0
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*([A-Za-z]+)?", text)
    if not match:
        return 0
    number = float(match.group(1))
    unit = (match.group(2) or "B").upper()
    factor = _SIZE_UNITS.get(unit)
    if factor is None:
        return 0
    return int(number * factor)


def instance_resource_snapshot(instance_id: str):
    proc = run(["incus", "query", f"/1.0/instances/{instance_id}?recursion=1"], check=False, timeout=20)
    if proc.returncode != 0:
        raise RuntimeError("无法读取 Incus 实例配置")

    try:
        payload = json.loads(proc.stdout or "{}")
    except Exception as exc:
        raise RuntimeError(f"Incus 实例配置不是有效 JSON: {exc}")

    meta = _api_metadata(payload)
    config = meta.get("expanded_config") or meta.get("config") or {}
    devices = meta.get("expanded_devices") or meta.get("devices") or {}
    root = devices.get("root") or {}

    raw_cpu = str(config.get("limits.cpu") or "").strip()
    cpu = int(raw_cpu) if raw_cpu.isdigit() else 0

    memory_bytes = _size_to_bytes(config.get("limits.memory"))
    disk_bytes = _size_to_bytes(root.get("size"))

    return {
        "cpu": cpu,
        "memory_mb": int(round(memory_bytes / (1024 ** 2))) if memory_bytes else 0,
        "disk_gb": round(disk_bytes / (1024 ** 3), 3) if disk_bytes else 0.0,
    }


def _set_root_disk_size(instance_id: str, disk_gb: float):
    value = disk_size_value(disk_gb)

    proc = run(
        ["incus", "config", "device", "set", instance_id, "root", f"size={value}"],
        check=False,
        timeout=60,
    )
    if proc.returncode == 0:
        return

    proc = run(
        ["incus", "config", "device", "override", instance_id, "root", f"size={value}"],
        check=False,
        timeout=60,
    )
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout or "根磁盘扩容失败").strip()
        raise RuntimeError(msg[:1000])


def resize_instance_resources(instance_id: str, cpu: int, memory_mb: int, disk_gb: float):
    """Adjust an already provisioned container.

    CPU and memory may be increased or decreased.
    Root disk is grow-only. Disk is applied last so CPU/memory can be rolled
    back if disk expansion fails.
    """
    before = instance_resource_snapshot(instance_id)

    if before["disk_gb"] > 0 and disk_gb < before["disk_gb"]:
        raise HTTPException(
            400,
            f"根磁盘禁止缩容：当前 {before['disk_gb']} GiB，请设置为不小于当前容量的值",
        )

    old_cpu = before["cpu"]
    old_memory = before["memory_mb"]
    cpu_changed = old_cpu > 0 and cpu != old_cpu
    memory_changed = old_memory > 0 and memory_mb != old_memory

    try:
        run(["incus", "config", "set", instance_id, f"limits.cpu={cpu}"], timeout=35)
        run(["incus", "config", "set", instance_id, f"limits.memory={memory_mb}MiB"], timeout=35)

        if before["disk_gb"] == 0 or disk_gb > before["disk_gb"]:
            _set_root_disk_size(instance_id, disk_gb)
    except Exception:
        # Best-effort rollback of reversible limits. Disk is applied last and
        # is intentionally never shrunk during rollback.
        if cpu_changed:
            run(["incus", "config", "set", instance_id, f"limits.cpu={old_cpu}"], check=False, timeout=35)
        if memory_changed:
            run(["incus", "config", "set", instance_id, f"limits.memory={old_memory}MiB"], check=False, timeout=35)
        raise

    after = instance_resource_snapshot(instance_id)
    return {
        "before": before,
        "cpu": after["cpu"] or cpu,
        "memory_mb": after["memory_mb"] or memory_mb,
        "disk_gb": after["disk_gb"] or disk_gb,
    }

class ProvisionBody(BaseModel):
    server_id: int
    instance_name: str
    image_alias: str
    memory_mb: int = Field(ge=64, le=1048576)
    disk_gb: float = Field(ge=0.125, le=65536)
    cpu: int = Field(ge=1, le=128)
    bandwidth_mbps: int = Field(ge=0, le=10000)
    ssh_port: int = Field(ge=1024, le=65535)
    virtualization_type: str = "lxc"



class ResourceResizeBody(BaseModel):
    cpu: int = Field(ge=1, le=128)
    memory_mb: int = Field(ge=64, le=1048576)
    disk_gb: float = Field(ge=0.125, le=65536)

class PowerBody(BaseModel):
    action: str


class ReinstallBody(BaseModel):
    server_id: int | None = None
    image_alias: str
    memory_mb: int
    disk_gb: float
    cpu: int
    bandwidth_mbps: int
    ssh_port: int
    virtualization_type: str = "lxc"


class PortBody(BaseModel):
    public_port: int = Field(ge=1024, le=65535)
    private_port: int = Field(ge=1, le=65535)
    protocol: str


class BandwidthBody(BaseModel):
    bandwidth_mbps: int = Field(ge=0, le=10000)


class NatPortPoolBody(BaseModel):
    port_start: int = Field(ge=1024, le=65535)
    port_end: int = Field(ge=1024, le=65535)


@app.get("/health")
def health():
    configured = configured_virtualization_modes()
    return {
        "status": "ok", "agent_version": AGENT_VERSION, "api_version": AGENT_API_VERSION,
        "virtualization_modes": configured, "kvm_available": kvm_available(),
    }


@app.get("/v1/status")
def status():
    vm = psutil.virtual_memory()
    root_space = host_root_space_mb()
    port_start, port_end = nat_port_pool()
    total_gb, used_gb, storage_source = storage_stats()
    instances = run(["incus", "list", "--format", "json"], check=False, timeout=20)
    active = 0
    if instances.returncode == 0:
        try:
            rows = json.loads(instances.stdout or "[]")
            active = sum(1 for row in rows if str(row.get("name") or "").startswith("nat-"))
        except Exception:
            pass
    return {
        "ready": run(["incus", "version"], check=False, timeout=10).returncode == 0,
        "agent_version": AGENT_VERSION,
        "api_version": AGENT_API_VERSION,
        "hostname": socket.gethostname(),
        "public_ip": PUBLIC_IP,
        "storage_pool": STORAGE_POOL,
        "bridge": BRIDGE_NAME,
        "port_start": port_start,
        "port_end": port_end,
        "nat_port_pool_configured": port_start is not None and port_end is not None,
        "agent_port": AGENT_PORT,
        "cpu_percent": round(psutil.cpu_percent(interval=0.15), 1),
        "memory_total_mb": int(vm.total / 1024 / 1024),
        "memory_used_mb": int((vm.total - vm.available) / 1024 / 1024),
        "memory_available_mb": int(vm.available / 1024 / 1024),
        "host_root_total_mb": root_space["total_mb"],
        "host_root_used_mb": root_space["used_mb"],
        "host_root_free_mb": root_space["free_mb"],
        "host_root_min_free_mb": HOST_ROOT_MIN_FREE_MB,
        "host_root_low": root_space["free_mb"] < HOST_ROOT_MIN_FREE_MB,
        "storage_total_gb": round(total_gb, 2),
        "storage_used_gb": round(used_gb, 2),
        "storage_free_gb": round(max(0.0, total_gb - used_gb), 2),
        "storage_stats_source": storage_source,
        "active_vps": active,
        "virtualization_modes": configured_virtualization_modes(),
        "kvm_available": kvm_available(),
    }


@app.get("/v1/ports/used")
def used_public_ports():
    grouped = current_proxy_public_ports_by_protocol()
    return {
        "tcp": sorted(grouped.get("tcp", set())),
        "udp": sorted(grouped.get("udp", set())),
    }


@app.post("/v1/config/nat-port-pool")
def configure_nat_port_pool(body: NatPortPoolBody):
    start = int(body.port_start)
    end = int(body.port_end)
    if start > end:
        raise HTTPException(400, "NAT 端口池起始端口不能大于结束端口")
    if start <= AGENT_PORT <= end:
        raise HTTPException(400, f"NAT 端口池不能包含 Host Agent 管理端口 {AGENT_PORT}")

    used = sorted(current_proxy_public_ports())
    outside = [port for port in used if not (start <= port <= end)]
    if outside:
        preview = ", ".join(str(port) for port in outside[:8])
        if len(outside) > 8:
            preview += " ..."
        raise HTTPException(409, f"新的 NAT 端口池会排除正在使用的公网端口：{preview}")

    cfg = load_node_config()
    cfg["nat_port_start"] = start
    cfg["nat_port_end"] = end
    save_node_config(cfg)
    return {
        "configured": True,
        "port_start": start,
        "port_end": end,
        "total_ports": end - start + 1,
    }


@app.post("/v1/provision")
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


@app.post("/v1/instances/{instance_id}/power")
def power(instance_id: str, body: PowerBody):
    require_instance(instance_id)
    if body.action == "start":
        run(["incus", "start", instance_id], timeout=65); status = "running"
    elif body.action == "stop":
        run(["incus", "stop", instance_id, "--timeout", "20", "--force"], timeout=45); status = "stopped"
    elif body.action == "reboot":
        run(["incus", "restart", instance_id, "--timeout", "20", "--force"], timeout=55); status = "running"
    else:
        raise HTTPException(400, "不支持的电源操作")
    return {"status": status}


@app.post("/v1/instances/{instance_id}/reset-password")
def reset_password(instance_id: str):
    require_instance(instance_id)
    mode = instance_virtualization_type(instance_id)
    wait_guest_agent(instance_id, mode)
    password = random_password()
    run(["incus", "exec", instance_id, "--", "chpasswd"], input_text=f"root:{password}\n", timeout=30)
    restart_ssh_service(instance_id)
    return {"root_password": password}


def _strict_delete_instance(name: str):
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


@app.delete("/v1/instances/{instance_id}")
def delete(instance_id: str):
    require_instance(instance_id)
    _strict_delete_instance(instance_id)
    return {"deleted": True, "verified_absent": not instance_exists(instance_id)}


@app.post("/v1/instances/{instance_id}/ports")
def add_port(instance_id: str, body: PortBody):
    require_instance(instance_id)
    require_nat_port_allowed(body.public_port)
    ensure_host_root_space("添加 NAT 端口")
    protocol = body.protocol.lower()
    if protocol not in {"tcp", "udp"}:
        raise HTTPException(400, "仅支持 TCP / UDP")
    device = f"nat-{protocol}-{body.public_port}"
    try:
        add_proxy_device(instance_id, device, protocol, body.public_port, body.private_port)
    except HostRootSpaceError as exc:
        raise HTTPException(507, str(exc)[:1800])
    return {"device_name": device}


@app.delete("/v1/instances/{instance_id}/ports/{device_name}")
def remove_port(instance_id: str, device_name: str):
    require_instance(instance_id); require_device(device_name)
    run(["incus", "config", "device", "remove", instance_id, device_name], timeout=35)
    return {"removed": True}


@app.get("/v1/instances/{instance_id}/stats")
def stats(instance_id: str):
    require_instance(instance_id)
    proc = run([
        "incus", "exec", instance_id, "--", "sh", "-lc",
        "rx=0; tx=0; "
        "for d in /sys/class/net/*; do "
        "[ \"$(basename \"$d\")\" = lo ] && continue; "
        "r=$(cat \"$d/statistics/rx_bytes\" 2>/dev/null || echo 0); "
        "t=$(cat \"$d/statistics/tx_bytes\" 2>/dev/null || echo 0); "
        "rx=$((rx+r)); tx=$((tx+t)); "
        "done; printf '%s %s\\n' \"$rx\" \"$tx\"",
    ], check=False, timeout=15)
    if proc.returncode != 0:
        return {"rx_bytes": 0, "tx_bytes": 0, "available": False}
    try:
        rx, tx = (proc.stdout or "").strip().split()
        return {"rx_bytes": int(rx), "tx_bytes": int(tx), "available": True}
    except Exception:
        return {"rx_bytes": 0, "tx_bytes": 0, "available": False}



@app.get("/v1/instances/{instance_id}/metrics")
def metrics(instance_id: str):
    require_instance(instance_id)
    try:
        return collect_instance_metrics(
            instance_id, run=run, instance_exists=instance_exists,
            instance_status=instance_status, resource_snapshot=instance_resource_snapshot,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, f"实时资源暂不可用: {str(exc)[:500]}")


@app.post("/v1/instances/{instance_id}/resources")
def resize_resources(instance_id: str, body: ResourceResizeBody):
    require_instance(instance_id)
    if not instance_exists(instance_id):
        raise HTTPException(404, "实例不存在")
    mode = instance_virtualization_type(instance_id)
    if mode == "kvm" and (body.memory_mb < 512 or body.disk_gb < 3):
        raise HTTPException(400, "KVM 实例至少需要 512 MiB 内存和 3 GiB 系统盘")
    try:
        result = resize_instance_resources(instance_id, body.cpu, body.memory_mb, body.disk_gb)
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, str(exc)[:800])


@app.post("/v1/instances/{instance_id}/bandwidth")
def bandwidth(instance_id: str, body: BandwidthBody):
    require_instance(instance_id)
    set_bandwidth(instance_id, body.bandwidth_mbps)
    return {"bandwidth_mbps": body.bandwidth_mbps}


@app.get("/v1/instances/{instance_id}/inspect")
def inspect(instance_id: str):
    require_instance(instance_id)
    proc = run(["incus", "query", f"/1.0/instances/{instance_id}?recursion=1"], check=False, timeout=20)
    if proc.returncode != 0:
        return {"exists": False, "status": "missing", "bandwidth_mbps": None}
    try:
        payload = json.loads(proc.stdout or "{}")
        meta = _api_metadata(payload)
        raw_status = str(meta.get("status") or "").lower()
        status = "running" if raw_status == "running" else "stopped" if raw_status in {"stopped", "frozen"} else raw_status or "unknown"
        devices = meta.get("expanded_devices") or meta.get("devices") or {}
        config = meta.get("expanded_config") or meta.get("config") or {}
        raw_bw = str((devices.get("eth0") or {}).get("limits.max") or "").strip()
        bw = 0 if not raw_bw else None
        if raw_bw:
            match = re.match(r"^([0-9.]+)\\s*(Mbit|Mbps|M)?$", raw_bw, re.I)
            if match:
                bw = int(float(match.group(1)))
        raw_cpu = str(config.get("limits.cpu") or "").strip()
        memory_bytes = _size_to_bytes(config.get("limits.memory"))
        disk_bytes = _size_to_bytes((devices.get("root") or {}).get("size"))
        return {
            "exists": True,
            "status": status,
            "bandwidth_mbps": bw,
            "cpu": int(raw_cpu) if raw_cpu.isdigit() else None,
            "memory_mb": int(round(memory_bytes / (1024 ** 2))) if memory_bytes else None,
            "disk_gb": int(round(disk_bytes / (1024 ** 3))) if disk_bytes else None,
            "virtualization_type": "kvm" if str(meta.get("type") or "").lower() == "virtual-machine" else "lxc",
        }
    except Exception:
        return {"exists": True, "status": "unknown", "bandwidth_mbps": None}


@app.get("/v1/instances/{instance_id}/devices/{device_name}")
def device_exists(instance_id: str, device_name: str):
    require_instance(instance_id); require_device(device_name)
    proc = run(["incus", "config", "device", "get", instance_id, device_name, "type"], check=False, timeout=15)
    return {"exists": proc.returncode == 0 and (proc.stdout or "").strip() == "proxy"}
