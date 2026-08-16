from __future__ import annotations

import smtplib
from email.message import EmailMessage
from datetime import datetime

import httpx
from sqlalchemy import select

from .db import SessionLocal
from .models import Notification, User
from .runtime_config import notification_runtime_config


def queue_notification(
    db,
    user: User,
    *,
    title: str,
    body: str,
    kind: str = "system",
    severity: str = "info",
    event_key: str | None = None,
) -> Notification | None:
    if event_key:
        exists = db.scalar(select(Notification).where(Notification.event_key == event_key))
        if exists:
            return None
    row = Notification(
        user_id=user.id,
        event_key=event_key,
        kind=kind,
        severity=severity,
        title=title[:160],
        body=body,
        email_status="pending",
        telegram_status="pending",
    )
    db.add(row)
    return row



def _delivery_config() -> dict:
    with SessionLocal() as db:
        return notification_runtime_config(db)


def _rule_group(kind: str) -> str:
    value = (kind or "system").strip().lower()
    if value == "traffic":
        return "traffic"
    if value in {"expiry", "renewal"}:
        return "expiry"
    if value in {"payment", "billing", "usdt_recharge"}:
        return "payment"
    if value == "ticket":
        return "ticket"
    if value == "security":
        return "security"
    if value in {"server", "purchase", "admin_provision", "provision_refund", "admin_adjustment"}:
        return "server"
    return "system"


def _channel_allowed(notification: Notification, channel: str, cfg: dict) -> bool:
    group = _rule_group(notification.kind)
    return bool((cfg.get("rules") or {}).get(group, {}).get(channel, True))


def _send_email_with_config(to_address: str, *, subject: str, body: str, cfg: dict) -> bool:
    host = cfg["smtp_host"].strip()
    from_addr = cfg["smtp_from"].strip()
    if not host or not from_addr or not (to_address or "").strip():
        return False
    try:
        port = int(cfg["smtp_port"] or 587)
    except Exception:
        port = 587
    username = cfg["smtp_username"].strip()
    password = cfg["smtp_password"]
    use_tls = bool(cfg["smtp_starttls"])
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_address.strip()
    msg.set_content(body)
    with smtplib.SMTP(host, port, timeout=15) as smtp:
        if use_tls:
            smtp.starttls()
        if username:
            smtp.login(username, password)
        smtp.send_message(msg)
    return True


def send_email_address(to_address: str, *, subject: str, body: str) -> bool:
    """Send a security/transactional email regardless of user notification preference."""
    return _send_email_with_config(to_address, subject=subject, body=body, cfg=_delivery_config())


def _send_email(user: User, notification: Notification, cfg: dict) -> str:
    if not _channel_allowed(notification, "email", cfg):
        return "rule_off"
    if not bool(user.notify_email):
        return "disabled"
    if not cfg["smtp_host"].strip() or not cfg["smtp_from"].strip():
        return "unconfigured"
    sent = _send_email_with_config(
        user.email,
        subject=f"[NAT VPS] {notification.title}",
        body=notification.body,
        cfg=cfg,
    )
    return "sent" if sent else "unconfigured"


def _send_telegram(user: User, notification: Notification, cfg: dict) -> str:
    if not _channel_allowed(notification, "telegram", cfg):
        return "rule_off"
    if not bool(user.notify_telegram):
        return "disabled"
    chat_id = (user.telegram_chat_id or "").strip()
    bot_token = cfg["telegram_bot_token"].strip()
    if not chat_id or not bot_token:
        return "unconfigured"

    text = f"{notification.title}\n\n{notification.body}"
    response = httpx.post(
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
        timeout=15,
    )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("ok"):
        raise RuntimeError(payload.get("description") or "Telegram send failed")
    return "sent"


def send_telegram_chat(chat_id: str, text: str) -> bool:
    cfg = _delivery_config()
    bot_token = cfg["telegram_bot_token"].strip()
    target = (chat_id or "").strip()
    if not bot_token:
        raise RuntimeError("Telegram Bot Token 未配置")
    if not target:
        raise RuntimeError("Telegram Chat ID 为空")
    response = httpx.post(
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        json={"chat_id": target, "text": text, "disable_web_page_preview": True},
        timeout=15,
    )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("ok"):
        raise RuntimeError(payload.get("description") or "Telegram send failed")
    return True


def process_pending_notifications(limit: int = 30) -> tuple[int, int]:
    sent = 0
    failed = 0
    cfg = _delivery_config()
    with SessionLocal() as db:
        rows = db.scalars(
            select(Notification)
            .where(
                (Notification.email_status == "pending") |
                (Notification.telegram_status == "pending")
            )
            .order_by(Notification.id)
            .limit(limit)
        ).all()

        for row in rows:
            user = db.get(User, row.user_id)
            if not user:
                row.email_status = "skipped"
                row.telegram_status = "skipped"
                continue

            if row.email_status == "pending":
                try:
                    row.email_status = _send_email(user, row, cfg)
                    if row.email_status == "sent":
                        sent += 1
                except Exception as exc:
                    row.email_status = f"failed:{str(exc)[:100]}"
                    failed += 1

            if row.telegram_status == "pending":
                try:
                    row.telegram_status = _send_telegram(user, row, cfg)
                    if row.telegram_status == "sent":
                        sent += 1
                except Exception as exc:
                    row.telegram_status = f"failed:{str(exc)[:100]}"
                    failed += 1

        db.commit()
    return sent, failed
