from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta
from urllib.parse import urlparse

import httpx
from sqlalchemy import func, select

from .crypto import decrypt_secret
from .models import HostNode, Plan, PlanHost, PortMapping, Server, SiteSetting

SUPPORTED_AGENT_API_VERSIONS = {"1"}


class HostAPIError(RuntimeError):
    pass


def _body_bytes(payload) -> bytes:
    if payload is None:
        return b""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _signature(token: str, timestamp: str, method: str, path: str, body: bytes) -> str:
    digest = hashlib.sha256(body).hexdigest()
    message = f"{timestamp}\n{method.upper()}\n{path}\n{digest}".encode("utf-8")
    return hmac.new(token.encode("utf-8"), message, hashlib.sha256).hexdigest()


def host_request(host: HostNode, method: str, path: str, *, payload=None, timeout: float = 25.0):
    token = decrypt_secret(host.api_token_enc or "")
    if not token:
        raise HostAPIError("宿主机 API Token 无法解密或为空")
    base = (host.api_url or "").rstrip("/")
    if not base.startswith(("http://", "https://")):
        raise HostAPIError("宿主机 Agent URL 无效")
    if not path.startswith("/"):
        path = "/" + path
    body = _body_bytes(payload)
    ts = str(int(time.time()))
    headers = {
        "X-NAT-Timestamp": ts,
        "X-NAT-Signature": _signature(token, ts, method, path, body),
        "Content-Type": "application/json",
        "User-Agent": "NAT-VPS-Panel/1.0.2",
    }
    try:
        with httpx.Client(verify=bool(host.verify_tls), timeout=timeout) as client:
            response = client.request(method.upper(), base + path, content=body if payload is not None else None, headers=headers)
    except Exception as exc:
        raise HostAPIError(f"连接宿主机失败: {exc}") from exc
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail") or response.text
        except Exception:
            detail = response.text
        raise HostAPIError(f"Host Agent HTTP {response.status_code}: {str(detail)[:300]}")
    if not response.content:
        return {}
    try:
        return response.json()
    except Exception as exc:
        raise HostAPIError("宿主机返回了无效 JSON") from exc


def refresh_host(host: HostNode) -> dict:
    now = datetime.utcnow()
    try:
        data = host_request(host, "GET", "/v1/status", timeout=12)
        host.agent_version = str(data.get("agent_version") or "")[:32] or None
        host.agent_api_version = str(data.get("api_version") or "")[:16] or None
        api_compatible = host.agent_api_version in SUPPORTED_AGENT_API_VERSIONS
        host.status = "online" if bool(data.get("ready", True)) and api_compatible else ("incompatible" if not api_compatible else "warning")
        host.hostname = str(data.get("hostname") or "")[:120] or None
        reported_public_ip = str(data.get("public_ip") or "").strip()
        if reported_public_ip and not (host.public_ip or "").strip():
            host.public_ip = reported_public_ip[:64]
        # Only recover the DB value when Panel has no pool configured yet.
        # Normal changes must go through the dedicated sync endpoint.
        if host.port_start is None and host.port_end is None and data.get("nat_port_pool_configured"):
            try:
                reported_start = int(data.get("port_start"))
                reported_end = int(data.get("port_end"))
                if 1024 <= reported_start <= reported_end <= 65535:
                    host.port_start = reported_start
                    host.port_end = reported_end
            except (TypeError, ValueError):
                pass
        host.cpu_percent = float(data.get("cpu_percent") or 0)
        host.memory_total_mb = max(0, int(data.get("memory_total_mb") or 0))
        host.memory_used_mb = max(0, int(data.get("memory_used_mb") or 0))
        host.storage_total_gb = max(0.0, float(data.get("storage_total_gb") or 0))
        host.storage_used_gb = max(0.0, float(data.get("storage_used_gb") or 0))
        host.active_vps = max(0, int(data.get("active_vps") or 0))
        host.last_seen_at = now
        host.last_error = None
        host.updated_at = now
        return data
    except Exception as exc:
        host.status = "offline"
        host.last_error = str(exc)[:1000]
        host.updated_at = now
        raise


def refresh_all_hosts(db) -> tuple[int, int]:
    ok = 0
    failed = 0
    hosts = db.scalars(select(HostNode).where(HostNode.enabled == True).order_by(HostNode.id)).all()
    for host in hosts:
        try:
            refresh_host(host)
            ok += 1
        except Exception:
            failed += 1
    return ok, failed


def host_active_server_count(db, host_id: int) -> int:
    return db.scalar(
        select(func.count()).select_from(Server).where(
            Server.host_id == host_id,
            Server.deleted_at.is_(None),
            Server.status.in_(["provisioning", "running", "stopped"]),
        )
    ) or 0


def host_allocated_memory_mb(db, host_id: int) -> int:
    return db.scalar(
        select(func.coalesce(func.sum(Server.memory_mb), 0)).where(
            Server.host_id == host_id,
            Server.deleted_at.is_(None),
            Server.status.in_(["provisioning", "running", "stopped"]),
        )
    ) or 0


def host_allocated_disk_gb(db, host_id: int) -> int:
    return db.scalar(
        select(func.coalesce(func.sum(Server.disk_gb), 0)).where(
            Server.host_id == host_id,
            Server.deleted_at.is_(None),
            Server.status.in_(["provisioning", "running", "stopped"]),
        )
    ) or 0


def allowed_hosts_for_plan(db, plan: Plan):
    links = db.scalars(select(PlanHost).where(PlanHost.plan_id == plan.id, PlanHost.enabled == True)).all()
    if links:
        ids = [row.host_id for row in links]
        return db.scalars(select(HostNode).where(HostNode.id.in_(ids), HostNode.enabled == True)).all()
    return db.scalars(select(HostNode).where(HostNode.enabled == True)).all()


def select_host_for_plan(db, plan: Plan) -> HostNode:
    candidates = []
    now = datetime.utcnow()
    for host in allowed_hosts_for_plan(db, plan):
        # Give a newly-added/unknown node one synchronous refresh attempt.
        stale = not host.last_seen_at or host.last_seen_at < now - timedelta(seconds=90)
        if host.status != "online" or stale:
            try:
                refresh_host(host)
            except Exception:
                continue
        if host.status != "online":
            continue
        if host.port_start is None or host.port_end is None:
            continue
        count = host_active_server_count(db, host.id)
        if host.max_vps and count >= host.max_vps:
            continue
        allocated_memory = host_allocated_memory_mb(db, host.id)
        allocated_disk = host_allocated_disk_gb(db, host.id)
        if host.memory_total_mb and allocated_memory + int(plan.memory_mb or 0) > host.memory_total_mb:
            continue
        if host.storage_total_gb and allocated_disk + int(plan.disk_gb or 0) > int(host.storage_total_gb * 0.96):
            continue
        memory_ratio = allocated_memory / max(host.memory_total_mb, 1)
        disk_ratio = allocated_disk / max(host.storage_total_gb, 1.0)
        max_ratio = count / max(host.max_vps, 1) if host.max_vps else 0
        score = float(host.cpu_percent or 0) + memory_ratio * 55 + disk_ratio * 35 + max_ratio * 20
        candidates.append((score, host.id, host))
    if not candidates:
        raise HostAPIError("当前没有可用宿主机：请检查节点在线状态、套餐绑定和资源容量")
    candidates.sort(key=lambda item: (item[0], item[1]))
    return candidates[0][2]


def public_port_in_use_on_host(db, host_id: int, port: int, protocol: str) -> bool:
    if db.scalar(
        select(PortMapping).join(Server, PortMapping.server_id == Server.id).where(
            Server.host_id == host_id,
            Server.deleted_at.is_(None),
            PortMapping.public_port == port,
            PortMapping.protocol == protocol,
        )
    ):
        return True
    if protocol == "tcp" and db.scalar(
        select(Server).where(
            Server.host_id == host_id,
            Server.ssh_port == port,
            Server.deleted_at.is_(None),
        )
    ):
        return True
    return False


def allocate_host_port(db, host: HostNode, protocol: str, blocked: set[int] | None = None) -> int:
    blocked = blocked or set()
    if host.port_start is None or host.port_end is None:
        raise HostAPIError(f"宿主机 {host.name} 尚未配置 NAT 端口池")
    start = max(1024, int(host.port_start))
    end = min(65535, int(host.port_end))
    if start > end:
        raise HostAPIError(f"宿主机 {host.name} 的 NAT 端口池配置无效")
    for port in range(start, end + 1):
        if port in blocked:
            continue
        if not public_port_in_use_on_host(db, host.id, port, protocol):
            return port
    raise HostAPIError(f"宿主机 {host.name} 的 {protocol.upper()} 公网端口池已经耗尽")


def host_summary(host: HostNode) -> dict:
    memory_percent = (host.memory_used_mb * 100 / host.memory_total_mb) if host.memory_total_mb else 0
    storage_percent = (host.storage_used_gb * 100 / host.storage_total_gb) if host.storage_total_gb else 0
    return {
        "memory_percent": round(memory_percent, 1),
        "storage_percent": round(storage_percent, 1),
        "api_compatible": host.agent_api_version in SUPPORTED_AGENT_API_VERSIONS,
    }



def host_port_pool_stats(db, host: HostNode) -> dict:
    if host.port_start is None or host.port_end is None:
        return {"configured": False, "start": None, "end": None, "total": 0, "used": 0, "remaining": 0}
    start = int(host.port_start)
    end = int(host.port_end)
    if not (1024 <= start <= end <= 65535):
        return {"configured": False, "start": start, "end": end, "total": 0, "used": 0, "remaining": 0}

    used_ports: set[int] = set()
    ssh_rows = db.scalars(
        select(Server).where(
            Server.host_id == host.id,
            Server.deleted_at.is_(None),
            Server.ssh_port.is_not(None),
        )
    ).all()
    for server in ssh_rows:
        try:
            port = int(server.ssh_port)
        except (TypeError, ValueError):
            continue
        if start <= port <= end:
            used_ports.add(port)

    mapping_rows = db.execute(
        select(PortMapping.public_port)
        .join(Server, PortMapping.server_id == Server.id)
        .where(Server.host_id == host.id, Server.deleted_at.is_(None))
    ).all()
    for (raw_port,) in mapping_rows:
        try:
            port = int(raw_port)
        except (TypeError, ValueError):
            continue
        if start <= port <= end:
            used_ports.add(port)

    blocked_ports: set[int] = set()
    setting = db.get(SiteSetting, "port_blocked_public")
    raw_blocked = (setting.value if setting else "") or ""
    for chunk in raw_blocked.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            if "-" in chunk:
                left, right = chunk.split("-", 1)
                a, b = int(left.strip()), int(right.strip())
                if a > b:
                    a, b = b, a
                for port in range(max(start, a), min(end, b) + 1):
                    blocked_ports.add(port)
            else:
                port = int(chunk)
                if start <= port <= end:
                    blocked_ports.add(port)
        except (TypeError, ValueError):
            continue

    total = end - start + 1
    used = len(used_ports)
    blocked = len(blocked_ports - used_ports)
    return {
        "configured": True,
        "start": start,
        "end": end,
        "total": total,
        "used": used,
        "blocked": blocked,
        "remaining": max(0, total - used - blocked),
    }
