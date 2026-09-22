from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import delete, select

from .models import HostPortLease, Job, Server


def finalize_panel_server_removal(
    db,
    server: Server,
    *,
    current_job_id: int | None = None,
    panel_only: bool = False,
    reason: str = "",
) -> dict:
    """Finalize Panel-side removal after a Host delete or an explicit admin force-remove.

    This helper never contacts the Host. Callers are responsible for deciding
    whether a Host delete must succeed first.
    """
    now = datetime.utcnow()

    stmt = select(Job).where(
        Job.server_id == server.id,
        Job.status == "pending",
        Job.job_type.in_(["provision_server", "reinstall_server", "delete_server"]),
    )
    if current_job_id:
        stmt = stmt.where(Job.id != int(current_job_id))
    pending_jobs = db.scalars(stmt).all()
    for pending in pending_jobs:
        pending.status = "cancelled"
        pending.finished_at = now
        pending.error_text = "服务器已删除，取消后续实例操作"

    # Clear Panel NAT mappings. For a normal delete the Host instance is already
    # gone; for force-remove the administrator explicitly accepts possible Host drift.
    mapping_ports = [(str(m.protocol or "tcp"), int(m.public_port)) for m in list(server.ports)]
    for mapping in list(server.ports):
        db.delete(mapping)

    # Short-lived allocation leases are no longer needed after normal deletion.
    # For force-remove, keep a safety quarantine so older Agents that do not yet
    # report live proxy ports cannot immediately recycle a possibly-live port.
    if server.host_id:
        lease_keys = set(mapping_ports)
        if server.ssh_port:
            lease_keys.add(("tcp", int(server.ssh_port)))
        if panel_only:
            quarantine_until = now + timedelta(days=30)
            for protocol, port in lease_keys:
                row = db.scalar(
                    select(HostPortLease).where(
                        HostPortLease.host_id == server.host_id,
                        HostPortLease.protocol == protocol,
                        HostPortLease.public_port == port,
                    )
                )
                if row:
                    row.expires_at = quarantine_until
                else:
                    db.add(HostPortLease(
                        host_id=server.host_id,
                        protocol=protocol,
                        public_port=port,
                        expires_at=quarantine_until,
                    ))
        else:
            for protocol, port in lease_keys:
                db.execute(
                    delete(HostPortLease).where(
                        HostPortLease.host_id == server.host_id,
                        HostPortLease.protocol == protocol,
                        HostPortLease.public_port == port,
                    )
                )

    server.status = "deleted"
    server.deleted_at = now
    server.root_password_enc = None
    server.reconcile_status = "deleted"
    server.reconcile_message = reason or (
        "Panel-only force removal; Host Agent was intentionally not contacted"
        if panel_only
        else "Host instance deleted successfully before Panel cleanup"
    )

    return {
        "cancelled_jobs": len(pending_jobs),
        "panel_only": bool(panel_only),
        "host_contacted": not bool(panel_only),
    }
