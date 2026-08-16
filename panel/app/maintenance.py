import argparse
import os
from datetime import datetime, timedelta
from sqlalchemy import select

from .backups import create_backup, create_scheduled_backup_if_due, restore_backup
from .db import SessionLocal
from .jobs import process_jobs
from .models import Server, User
from .notifications import process_pending_notifications, queue_admin_notification
from .lifecycle import run_expiry_lifecycle
from .payments import poll_pending_payments
from .reconcile import reconcile_all
from .traffic import collect_all as collect_traffic_all
from .providers.incus import IncusProvider
from .providers.remote import RemoteHostProvider
from .providers.mock import MockProvider

PROVIDER_NAME = os.getenv("VPS_PROVIDER", "mock").strip().lower()
provider = RemoteHostProvider() if PROVIDER_NAME == "remote" else IncusProvider() if PROVIDER_NAME == "incus" else MockProvider()


def expiry_notifications_and_stop() -> int:
    # Backwards-compatible CLI name; v1.1 runs the full lifecycle engine.
    result = run_expiry_lifecycle(provider, PROVIDER_NAME)
    return int(result.get("stopped", 0))


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
        with SessionLocal() as db:
            queue_admin_notification(
                db, title="数据库自动备份失败", body=f"XNAT 定时数据库备份失败：{str(exc)[:300]}",
                kind="system", severity="error", event_key=f"backup-failed:{datetime.utcnow():%Y%m%d%H}"
            )
            db.commit()
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
