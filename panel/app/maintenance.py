import argparse
import os
from datetime import datetime, timedelta
from sqlalchemy import select

from .backups import create_backup, create_scheduled_backup_if_due, restore_backup
from .db import SessionLocal
from .jobs import process_jobs
from .models import Server, User
from .notifications import process_pending_notifications, queue_notification
from .payments import poll_pending_payments
from .reconcile import reconcile_all
from .traffic import collect_all as collect_traffic_all
from .providers.incus import IncusProvider
from .providers.remote import RemoteHostProvider
from .providers.mock import MockProvider

PROVIDER_NAME = os.getenv("VPS_PROVIDER", "mock").strip().lower()
provider = RemoteHostProvider() if PROVIDER_NAME == "remote" else IncusProvider() if PROVIDER_NAME == "incus" else MockProvider()


def expiry_notifications_and_stop() -> int:
    now = datetime.utcnow()
    stopped = 0
    with SessionLocal() as db:
        servers = db.scalars(select(Server).where(
            Server.deleted_at.is_(None),
            Server.expires_at.is_not(None),
        )).all()
        for server in servers:
            user = db.get(User, server.user_id)
            if not user:
                continue
            remaining = server.expires_at - now
            days = remaining.total_seconds() / 86400
            for threshold in (7, 3, 1):
                if 0 < days <= threshold:
                    cycle = server.expires_at.strftime("%Y%m%d%H%M")
                    queue_notification(
                        db,
                        user,
                        title=f"VPS 将在 {threshold} 天内到期",
                        body=f"{server.name} 到期时间：{server.expires_at:%Y-%m-%d %H:%M} UTC，请及时续费。",
                        kind="expiry",
                        severity="warning",
                        event_key=f"expiry-{threshold}:{server.id}:{cycle}",
                    )
            if server.expires_at <= now:
                cycle = server.expires_at.strftime("%Y%m%d%H%M")
                queue_notification(
                    db,
                    user,
                    title="VPS 已到期",
                    body=f"{server.name} 已到期并停止服务。续费后可重新开机。",
                    kind="expiry",
                    severity="error",
                    event_key=f"expired:{server.id}:{cycle}",
                )
                if server.status == "running" and server.provider == PROVIDER_NAME and server.provider_instance_id:
                    try:
                        provider.power_action(server.provider_instance_id, "stop")
                        server.status = "stopped"
                        stopped += 1
                    except Exception as exc:
                        print(f"[expire] {server.name}: {exc}")
        db.commit()
    return stopped


def tick() -> dict:
    sampled, traffic_failed = collect_traffic_all(provider, PROVIDER_NAME)
    jobs = process_jobs(provider, PROVIDER_NAME, max_jobs=10)
    paid, payment_failed = poll_pending_payments()
    stopped = expiry_notifications_and_stop()
    notify_sent, notify_failed = process_pending_notifications(limit=50)
    reconcile_ok, reconcile_attention = reconcile_all(provider, PROVIDER_NAME, repair=True)
    backup_created = False
    try:
        backup_created = create_scheduled_backup_if_due()
    except Exception as exc:
        print(f"[backup] {exc}")
    return {
        "traffic_sampled": sampled,
        "traffic_failed": traffic_failed,
        "jobs": jobs,
        "payments_paid": paid,
        "payment_failed": payment_failed,
        "expired_stopped": stopped,
        "notifications_sent": notify_sent,
        "notification_failed": notify_failed,
        "reconcile_ok": reconcile_ok,
        "reconcile_attention": reconcile_attention,
        "backup_created": backup_created,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["expire", "traffic", "jobs", "payments", "notifications", "reconcile", "backup", "restore-backup", "tick"])
    parser.add_argument("value", nargs="?")
    args = parser.parse_args()

    if args.action == "expire":
        print(f"expired_stopped={expiry_notifications_and_stop()}")
    elif args.action == "traffic":
        sampled, failed = collect_traffic_all(provider, PROVIDER_NAME)
        print(f"traffic_sampled={sampled} traffic_failed={failed}")
    elif args.action == "jobs":
        print(f"jobs_processed={process_jobs(provider, PROVIDER_NAME, max_jobs=50)}")
    elif args.action == "payments":
        paid, failed = poll_pending_payments()
        print(f"payments_paid={paid} payments_failed={failed}")
    elif args.action == "notifications":
        sent, failed = process_pending_notifications(limit=100)
        print(f"notifications_sent={sent} notifications_failed={failed}")
    elif args.action == "reconcile":
        ok, attention = reconcile_all(provider, PROVIDER_NAME, repair=True)
        print(f"reconcile_ok={ok} reconcile_attention={attention}")
    elif args.action == "backup":
        print(create_backup("manual"))
    elif args.action == "restore-backup":
        if not args.value:
            parser.error("restore-backup 需要备份文件名")
        safety = restore_backup(args.value)
        print(f"restored={args.value} safety_copy={safety}")
    elif args.action == "tick":
        print(tick())


if __name__ == "__main__":
    main()
