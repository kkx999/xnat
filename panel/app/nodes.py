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
from .notifications import queue_admin_notification

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
        "User-Agent": "XNAT-Panel/1.1.1",
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


def _setting_int(db, key: str, default: int, low: int = 0, high: int = 100) -> int:
    row = db.get(SiteSetting, key)
    try:
        value = int((row.value if row else str(default)) or default)
    except Exception:
        value = default
    return max(low, min(high, value))


def host_resource_percentages(host: HostNode) -> dict:
    memory_percent = (host.memory_used_mb * 100 / host.memory_total_mb) if host.memory_total_mb else 0.0
    storage_percent = (host.storage_used_gb * 100 / host.storage_total_gb) if host.storage_total_gb else 0.0
    return {
        "cpu": round(float(host.cpu_percent or 0), 1),
        "memory": round(memory_percent, 1),
        "storage": round(storage_percent, 1),
    }


def refresh_all_hosts(db) -> tuple[int, int]:
    ok = 0
    failed = 0
    hosts = db.scalars(select(HostNode).where(HostNode.enabled == True).order_by(HostNode.id)).all()
    now = datetime.utcnow()
    for host in hosts:
        previous_status = host.status
        try:
            refresh_host(host)
            ok += 1
            if previous_status == "offline" and host.status == "online":
                queue_admin_notification(
                    db,
                    title="宿主机节点已恢复",
                    body=f"{host.name} ({host.public_ip or host.api_url}) 已恢复连接，当前 Agent {host.agent_version or '-'} / API v{host.agent_api_version or '-' }。",
                    kind="system",
                    severity="success",
                    event_key=f"host-recovered:{host.id}:{now:%Y%m%d%H%M}",
                )

            percentages = host_resource_percentages(host)
            storage_limit = max(0, int(host.schedule_storage_max_percent or 0))
            if storage_limit and percentages["storage"] >= storage_limit:
                queue_admin_notification(
                    db,
                    title="宿主机 natpool 存储达到调度水位",
                    body=f"{host.name} 存储已使用 {percentages['storage']:.1f}%（调度阈值 {storage_limit}%），系统已停止向该节点调度新 VPS。",
                    kind="system",
                    severity="error",
                    event_key=f"host-storage-high:{host.id}:{now:%Y%m%d}",
                )

            pool = host_port_pool_stats(db, host)
            alert_percent = _setting_int(db, "node_nat_port_alert_percent", 10, 0, 100)
            if pool.get("configured") and pool.get("total", 0) and alert_percent:
                remaining_percent = pool["remaining"] * 100 / max(pool["total"], 1)
                if remaining_percent <= alert_percent:
                    queue_admin_notification(
                        db,
                        title="宿主机 NAT 端口池余量不足",
                        body=f"{host.name} NAT 端口池仅剩 {pool['remaining']} / {pool['total']} 个可用端口（{remaining_percent:.1f}%）。",
                        kind="system",
                        severity="warning",
                        event_key=f"host-port-low:{host.id}:{now:%Y%m%d}",
                    )
        except Exception as exc:
            failed += 1
            if previous_status != "offline":
                queue_admin_notification(
                    db,
                    title="宿主机节点离线",
                    body=f"{host.name} ({host.public_ip or host.api_url}) 无法连接 Host Agent：{str(exc)[:240]}",
                    kind="system",
                    severity="error",
                    event_key=f"host-offline:{host.id}:{now:%Y%m%d%H%M}",
                )
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


def host_schedule_state(db, host: HostNode, plan: Plan | None = None, *, refresh_if_stale: bool = False) -> dict:
    """Return whether a Host may receive a new VPS and the exact block reason."""
    now = datetime.utcnow()
    stale = not host.last_seen_at or host.last_seen_at < now - timedelta(seconds=90)
    if refresh_if_stale and (host.status != "online" or stale) and host.enabled:
        try:
            refresh_host(host)
            stale = False
        except Exception:
            pass

    if not host.enabled:
        return {"allowed": False, "code": "disabled", "label": "已停用调度", "reason": "节点已被管理员停用"}
    if bool(host.maintenance_mode):
        reason = (host.maintenance_reason or "管理员已将节点置于维护模式").strip()
        return {"allowed": False, "code": "maintenance", "label": "维护中", "reason": reason}
    if host.status != "online":
        return {"allowed": False, "code": "offline", "label": "不可调度", "reason": f"节点状态：{host.status or 'unknown'}"}
    if stale:
        return {"allowed": False, "code": "stale", "label": "心跳过期", "reason": "节点心跳超过 90 秒"}
    if host.port_start is None or host.port_end is None:
        return {"allowed": False, "code": "port_pool", "label": "待配置", "reason": "尚未配置 NAT 端口池"}

    count = host_active_server_count(db, host.id)
    if host.max_vps and count >= host.max_vps:
        return {"allowed": False, "code": "max_vps", "label": "容量已满", "reason": f"VPS 数量已达到 {host.max_vps}"}

    percentages = host_resource_percentages(host)
    thresholds = {
        "cpu": max(0, int(host.schedule_cpu_max_percent or 0)),
        "memory": max(0, int(host.schedule_memory_max_percent or 0)),
        "storage": max(0, int(host.schedule_storage_max_percent or 0)),
    }
    labels = {"cpu": "CPU", "memory": "内存", "storage": "natpool 存储"}
    for key in ("cpu", "memory", "storage"):
        limit = thresholds[key]
        if limit and percentages[key] >= limit:
            return {
                "allowed": False, "code": f"watermark_{key}", "label": "资源保护",
                "reason": f"{labels[key]} {percentages[key]:.1f}% 已达到调度阈值 {limit}%",
                "percentages": percentages,
            }

    allocated_memory = host_allocated_memory_mb(db, host.id)
    allocated_disk = host_allocated_disk_gb(db, host.id)
    if plan is not None:
        if host.memory_total_mb:
            projected_memory = (allocated_memory + int(plan.memory_mb or 0)) * 100 / host.memory_total_mb
            limit = thresholds["memory"] or 100
            if projected_memory > limit:
                return {"allowed": False, "code": "capacity_memory", "label": "内存不足", "reason": f"开通后分配内存将达到 {projected_memory:.1f}%（上限 {limit}%）"}
        if host.storage_total_gb:
            projected_disk = (allocated_disk + int(plan.disk_gb or 0)) * 100 / host.storage_total_gb
            limit = thresholds["storage"] or 100
            if projected_disk > limit:
                return {"allowed": False, "code": "capacity_storage", "label": "存储不足", "reason": f"开通后逻辑磁盘分配将达到 {projected_disk:.1f}%（上限 {limit}%）"}

    return {
        "allowed": True, "code": "ready", "label": "可调度", "reason": "节点在线且资源水位正常",
        "percentages": percentages, "active_vps": count,
    }


def select_host_for_plan(db, plan: Plan) -> HostNode:
    candidates = []
    rejected: list[str] = []
    for host in allowed_hosts_for_plan(db, plan):
        state = host_schedule_state(db, host, plan, refresh_if_stale=True)
        if not state["allowed"]:
            rejected.append(f"{host.name}: {state['reason']}")
            continue
        count = host_active_server_count(db, host.id)
        allocated_memory = host_allocated_memory_mb(db, host.id)
        allocated_disk = host_allocated_disk_gb(db, host.id)
        memory_ratio = allocated_memory / max(host.memory_total_mb, 1)
        disk_ratio = allocated_disk / max(host.storage_total_gb, 1.0)
        max_ratio = count / max(host.max_vps, 1) if host.max_vps else 0
        score = float(host.cpu_percent or 0) + memory_ratio * 55 + disk_ratio * 35 + max_ratio * 20
        candidates.append((score, host.id, host))
    if not candidates:
        detail = "；".join(rejected[:4])
        suffix = f"。{detail}" if detail else ""
        raise HostAPIError("当前没有可用宿主机：请检查维护模式、节点在线状态、NAT 端口池和资源水位" + suffix)
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
    percentages = host_resource_percentages(host)
    return {
        "memory_percent": percentages["memory"],
        "storage_percent": percentages["storage"],
        "cpu_percent": percentages["cpu"],
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
