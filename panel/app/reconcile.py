from __future__ import annotations

from datetime import datetime
from sqlalchemy import select

from .audit import write_audit
from .db import SessionLocal
from .models import PortMapping, Server
from .traffic import effective_bandwidth_mbps


def reconcile_server(db, provider, server: Server, *, repair: bool = True) -> dict:
    now = datetime.utcnow()
    if server.deleted_at is not None or not server.provider_instance_id:
        server.reconcile_status = "ignored"
        server.reconcile_message = None
        server.reconciled_at = now
        return {"status": "ignored", "changes": []}

    state = provider.inspect(server.provider_instance_id)
    changes: list[str] = []
    errors: list[str] = []

    if not state.exists:
        server.reconcile_status = "error"
        server.reconcile_message = "Incus 实例不存在；为避免误覆盖数据，面板不会自动重建。"
        server.reconciled_at = now
        return {"status": "error", "changes": [], "errors": [server.reconcile_message]}

    if state.status in {"running", "stopped"} and server.status != state.status:
        server.status = state.status
        changes.append(f"同步状态为 {state.status}")

    expected_bw = effective_bandwidth_mbps(server)
    if repair and state.bandwidth_mbps is not None and int(state.bandwidth_mbps) != int(expected_bw):
        try:
            provider.set_bandwidth(server.provider_instance_id, expected_bw)
            changes.append(f"修复带宽 {state.bandwidth_mbps} → {expected_bw} Mbps")
        except Exception as exc:
            errors.append(f"带宽修复失败：{str(exc)[:160]}")

    if server.ssh_port:
        ssh_device = f"ssh-{server.ssh_port}"
        try:
            if not provider.port_device_exists(server.provider_instance_id, ssh_device):
                if repair:
                    # add_port cannot create the special SSH name, so only flag here.
                    errors.append(f"SSH proxy 设备 {ssh_device} 缺失，需要重装或手动修复")
                else:
                    errors.append(f"SSH proxy 设备 {ssh_device} 缺失")
        except Exception as exc:
            errors.append(f"SSH proxy 检查失败：{str(exc)[:160]}")

    for mapping in list(server.ports):
        try:
            exists = provider.port_device_exists(server.provider_instance_id, mapping.device_name)
            if not exists and repair:
                device = provider.add_port(
                    server.provider_instance_id,
                    mapping.public_port,
                    mapping.private_port,
                    mapping.protocol,
                )
                if device != mapping.device_name:
                    mapping.device_name = device
                changes.append(f"恢复 {mapping.protocol.upper()} {mapping.public_port}→{mapping.private_port}")
            elif not exists:
                errors.append(f"NAT 端口 {mapping.public_port} 设备缺失")
        except Exception as exc:
            errors.append(f"端口 {mapping.public_port} 检查失败：{str(exc)[:160]}")

    server.reconciled_at = now
    if errors:
        server.reconcile_status = "warning" if changes else "error"
        server.reconcile_message = "；".join(errors)[:1000]
    elif changes:
        server.reconcile_status = "repaired"
        server.reconcile_message = "；".join(changes)[:1000]
    else:
        server.reconcile_status = "ok"
        server.reconcile_message = None

    if changes or errors:
        write_audit(
            db,
            actor_username="system",
            action="server.reconcile",
            target_type="server",
            target_id=server.id,
            target_name=server.name,
            detail={"changes": changes, "errors": errors},
            success=not bool(errors),
        )
    return {"status": server.reconcile_status, "changes": changes, "errors": errors}


def reconcile_all(provider, provider_name: str, *, repair: bool = True) -> tuple[int, int]:
    ok = 0
    attention = 0
    with SessionLocal() as db:
        servers = db.scalars(select(Server).where(
            Server.deleted_at.is_(None),
            Server.provider == provider_name,
            Server.provider_instance_id.is_not(None),
        )).all()
        for server in servers:
            try:
                result = reconcile_server(db, provider, server, repair=repair)
                if result["status"] in {"ok", "repaired"}:
                    ok += 1
                else:
                    attention += 1
            except Exception as exc:
                server.reconcile_status = "error"
                server.reconcile_message = str(exc)[:1000]
                server.reconciled_at = datetime.utcnow()
                attention += 1
        db.commit()
    return ok, attention
