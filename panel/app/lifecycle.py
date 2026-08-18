from __future__ import annotations

import json
from datetime import datetime, timedelta

from sqlalchemy import select

from .audit import write_audit
from .db import SessionLocal
from .jobs import enqueue_job
from .models import Job, Server, SiteSetting, User
from .geo import server_display_id
from .notifications import queue_notification, queue_admin_notification


def _setting(db, key: str, default: str) -> str:
    row = db.get(SiteSetting, key)
    return (row.value if row else default) or default


def _bool_setting(db, key: str, default: bool) -> bool:
    fallback = "true" if default else "false"
    return _setting(db, key, fallback).strip().lower() in {"1", "true", "yes", "on"}


def lifecycle_config(db) -> dict:
    raw_days = _setting(db, "expiry_notice_days", "7,3,1")
    notice_days: list[int] = []
    for chunk in raw_days.split(","):
        try:
            value = int(chunk.strip())
        except Exception:
            continue
        if 1 <= value <= 365 and value not in notice_days:
            notice_days.append(value)
    notice_days.sort(reverse=True)
    if not notice_days:
        notice_days = [7, 3, 1]

    def integer(key: str, default: int, low: int, high: int) -> int:
        try:
            value = int(_setting(db, key, str(default)))
        except Exception:
            value = default
        return max(low, min(high, value))

    return {
        "notice_days": notice_days,
        "grace_days": integer("expiry_grace_days", 0, 0, 90),
        "auto_stop": _bool_setting(db, "expiry_auto_stop_enabled", True),
        "auto_delete": _bool_setting(db, "expiry_delete_enabled", False),
        "delete_after_days": integer("expiry_delete_after_days", 7, 1, 365),
        "delete_notice_hours": integer("expiry_delete_notice_hours", 24, 1, 720),
        "renew_auto_start": _bool_setting(db, "expiry_renew_auto_start", True),
    }


def lifecycle_state(server: Server, cfg: dict, now: datetime | None = None) -> dict:
    now = now or datetime.utcnow()
    if not server.expires_at:
        return {"code": "active", "label": "长期有效", "grace_end": None, "delete_at": None}

    grace_end = server.expires_at + timedelta(days=cfg["grace_days"])
    delete_at = grace_end + timedelta(days=cfg["delete_after_days"])
    if server.deleted_at:
        code = "deleted"
        label = "已删除"
    elif server.expiry_delete_queued_at:
        code = "delete_queued"
        label = "等待自动删除"
    elif now < server.expires_at:
        code = "active"
        label = "正常服务"
    elif now < grace_end:
        code = "grace"
        label = "到期宽限期"
    elif server.expiry_suspended_at:
        code = "suspended"
        label = "到期已暂停"
    else:
        code = "expired"
        label = "已到期"
    return {"code": code, "label": label, "grace_end": grace_end, "delete_at": delete_at}


def _is_expiry_delete_job(job: Job) -> bool:
    if job.job_type != "delete_server":
        return False
    try:
        payload = json.loads(job.payload_json or "{}")
    except Exception:
        return False
    return payload.get("source") == "expiry_lifecycle"


def cancel_pending_expiry_delete(db, server: Server) -> tuple[int, bool]:
    """Cancel only pending lifecycle deletes and report unsafe delete work.

    Returns ``(cancelled_count, blocking_delete_exists)``. A lifecycle delete
    that has already started cannot be reversed. A *manual* delete, whether
    pending or running, is also a hard block: renewal or an expiry-date edit
    must never silently override an administrator/customer deletion request.
    """
    rows = db.scalars(
        select(Job).where(
            Job.server_id == server.id,
            Job.job_type == "delete_server",
            Job.status.in_(["pending", "running"]),
        )
    ).all()
    cancelled = 0
    blocking = False
    for job in rows:
        if not _is_expiry_delete_job(job):
            blocking = True
            continue
        if job.status == "running":
            blocking = True
            continue
        job.status = "cancelled"
        job.finished_at = datetime.utcnow()
        job.error_text = "续费或恢复到期时间后取消自动删除"
        cancelled += 1
    if cancelled:
        server.expiry_delete_queued_at = None
    return cancelled, blocking


def resume_after_renewal(db, provider, provider_name: str, server: Server) -> tuple[bool, str | None]:
    """Clear lifecycle markers and optionally restart a VPS stopped by expiry."""
    cfg = lifecycle_config(db)
    was_suspended = server.expiry_suspended_at is not None
    server.expiry_suspended_at = None
    server.expiry_delete_queued_at = None
    if not (was_suspended and cfg["renew_auto_start"] and server.status == "stopped"):
        return False, None
    if server.provider != provider_name or not server.provider_instance_id:
        return False, "当前 Provider 无法自动恢复开机"
    try:
        server.status = provider.power_action(server.provider_instance_id, "start")
        return True, None
    except Exception as exc:
        return False, str(exc)[:180]


def run_expiry_lifecycle(provider, provider_name: str) -> dict:
    """Process expiry notices, grace, suspension and optional auto-delete."""
    now = datetime.utcnow()
    stats = {"notices": 0, "grace": 0, "stopped": 0, "delete_warned": 0, "delete_queued": 0, "errors": 0}

    with SessionLocal() as db:
        cfg = lifecycle_config(db)
        servers = db.scalars(
            select(Server).where(Server.deleted_at.is_(None), Server.expires_at.is_not(None))
        ).all()

        for server in servers:
            user = db.get(User, server.user_id)
            if not user:
                continue
            expiry_key = server.expires_at.strftime("%Y%m%d%H%M")
            remaining = server.expires_at - now
            days_remaining = remaining.total_seconds() / 86400

            if remaining.total_seconds() > 0:
                for threshold in cfg["notice_days"]:
                    if 0 < days_remaining <= threshold:
                        if queue_notification(
                            db,
                            user,
                            title=f"VPS 将在 {threshold} 天内到期",
                            body=f"{server_display_id(server)} 将于 {server.expires_at:%Y-%m-%d %H:%M} UTC 到期，请及时续费。",
                            kind="expiry",
                            severity="warning",
                            event_key=f"expiry-{threshold}:{server.id}:{expiry_key}",
                        ):
                            stats["notices"] += 1
                continue

            grace_end = server.expires_at + timedelta(days=cfg["grace_days"])
            if now < grace_end:
                if queue_notification(
                    db,
                    user,
                    title="VPS 已到期，当前处于宽限期",
                    body=f"{server_display_id(server)} 已到期，将在宽限期结束后暂停服务。宽限结束：{grace_end:%Y-%m-%d %H:%M} UTC。",
                    kind="expiry",
                    severity="warning",
                    event_key=f"expiry-grace:{server.id}:{expiry_key}",
                ):
                    stats["grace"] += 1
                continue

            queue_notification(
                db,
                user,
                title="VPS 已到期",
                body=(
                    f"{server_display_id(server)} 已超过到期宽限期。"
                    + ("服务将保持停止，续费后可自动恢复。" if cfg["auto_stop"] else "当前站点未启用自动停机。")
                ),
                kind="expiry",
                severity="error",
                event_key=f"expired:{server.id}:{expiry_key}",
            )

            if cfg["auto_stop"] and server.status == "running" and server.provider == provider_name and server.provider_instance_id:
                try:
                    server.status = provider.power_action(server.provider_instance_id, "stop")
                    server.expiry_suspended_at = server.expiry_suspended_at or now
                    stats["stopped"] += 1
                    write_audit(
                        db,
                        actor_username="system",
                        action="lifecycle.expiry.stop",
                        target_type="server",
                        target_id=server.id,
                        target_name=server.name,
                        detail={"expires_at": server.expires_at.isoformat(), "grace_days": cfg["grace_days"]},
                    )
                except Exception as exc:
                    stats["errors"] += 1
                    queue_admin_notification(
                        db,
                        title="到期 VPS 自动停机失败",
                        body=f"{server_display_id(server)} (#{server.id}) 自动停机失败：{str(exc)[:240]}",
                        kind="system",
                        severity="error",
                        event_key=f"expiry-stop-failed:{server.id}:{now:%Y%m%d%H}",
                    )

            if not cfg["auto_delete"]:
                continue

            delete_at = grace_end + timedelta(days=cfg["delete_after_days"])
            warn_at = delete_at - timedelta(hours=cfg["delete_notice_hours"])
            if warn_at <= now < delete_at:
                if queue_notification(
                    db,
                    user,
                    title="VPS 即将自动删除",
                    body=f"{server_display_id(server)} 预计在 {delete_at:%Y-%m-%d %H:%M} UTC 永久删除。请在删除前完成续费。",
                    kind="expiry",
                    severity="error",
                    event_key=f"expiry-delete-warning:{server.id}:{expiry_key}",
                ):
                    stats["delete_warned"] += 1

            if now < delete_at or server.expiry_delete_queued_at:
                continue

            existing = db.scalar(
                select(Job).where(
                    Job.server_id == server.id,
                    Job.job_type == "delete_server",
                    Job.status.in_(["pending", "running"]),
                )
            )
            if existing:
                continue
            job = enqueue_job(
                db,
                "delete_server",
                user_id=server.user_id,
                server_id=server.id,
                payload={"source": "expiry_lifecycle", "delete_at": delete_at.isoformat()},
                max_attempts=5,
            )
            server.expiry_delete_queued_at = now
            stats["delete_queued"] += 1
            queue_notification(
                db,
                user,
                title="VPS 已进入自动删除队列",
                body=f"{server_display_id(server)} 已超过自动删除期限，删除任务 #{job.id} 已进入队列。",
                kind="expiry",
                severity="error",
                event_key=f"expiry-delete-queued:{server.id}:{expiry_key}",
            )
            write_audit(
                db,
                actor_username="system",
                action="lifecycle.expiry.delete.queue",
                target_type="server",
                target_id=server.id,
                target_name=server.name,
                detail={"job_id": job.id, "delete_at": delete_at.isoformat()},
            )

        db.commit()
    return stats
