from __future__ import annotations

import os

from .crypto import decrypt_secret
from .models import SiteSetting


def runtime_plain(db, key: str, *, env_name: str | None = None, default: str = "") -> str:
    """Read a runtime config value from the database first, then fall back to .env."""
    row = db.get(SiteSetting, key)
    if row and str(row.value or "").strip():
        return str(row.value).strip()
    if env_name:
        return os.getenv(env_name, default).strip()
    return default


def runtime_secret(db, key: str, *, env_name: str | None = None, default: str = "") -> str:
    """Read an encrypted secret from SiteSetting, with .env as backwards-compatible fallback."""
    row = db.get(SiteSetting, key)
    if row and str(row.value or "").strip():
        value = decrypt_secret(str(row.value))
        if value is not None:
            return value
    if env_name:
        return os.getenv(env_name, default).strip()
    return default


def runtime_bool(db, key: str, *, env_name: str | None = None, default: bool = False) -> bool:
    fallback = "true" if default else "false"
    value = runtime_plain(db, key, env_name=env_name, default=fallback)
    return value.lower() in {"1", "true", "yes", "on"}


def notification_runtime_config(db) -> dict:
    rule_groups = ("server", "traffic", "expiry", "payment", "ticket", "security", "system")
    rules = {
        group: {
            "email": runtime_bool(db, f"notify_rule_{group}_email", default=True),
            "telegram": runtime_bool(db, f"notify_rule_{group}_telegram", default=True),
        }
        for group in rule_groups
    }
    return {
        "public_base_url": runtime_plain(db, "public_base_url", env_name="PUBLIC_BASE_URL", default="").rstrip("/"),
        "smtp_host": runtime_plain(db, "smtp_host", env_name="SMTP_HOST", default=""),
        "smtp_port": runtime_plain(db, "smtp_port", env_name="SMTP_PORT", default="587") or "587",
        "smtp_username": runtime_plain(db, "smtp_username", env_name="SMTP_USERNAME", default=""),
        "smtp_password": runtime_secret(db, "smtp_password_enc", env_name="SMTP_PASSWORD", default=""),
        "smtp_from": runtime_plain(db, "smtp_from", env_name="SMTP_FROM", default=""),
        "smtp_starttls": runtime_bool(db, "smtp_starttls", env_name="SMTP_STARTTLS", default=True),
        "telegram_bot_token": runtime_secret(db, "telegram_bot_token_enc", env_name="TELEGRAM_BOT_TOKEN", default=""),
        "trongrid_api_key": runtime_secret(db, "trongrid_api_key_enc", env_name="TRONGRID_API_KEY", default=""),
        "rules": rules,
    }
