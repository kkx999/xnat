from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote
from datetime import datetime, timedelta

from sqlalchemy import func, select

from .models import LoginEvent, LoginSession, PasswordResetToken


def client_ip(request) -> str:
    client = getattr(request, "client", None)
    peer = (getattr(client, "host", None) or "unknown").strip()

    # XNAT binds Uvicorn to loopback and only trusts proxy headers
    # received from the local Nginx reverse proxy. This prevents a client from
    # spoofing X-Forwarded-For if somebody accidentally exposes Uvicorn.
    if peer in {"127.0.0.1", "::1"}:
        forwarded = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
        if forwarded:
            return forwarded[:64]
        real_ip = (request.headers.get("x-real-ip") or "").strip()
        if real_ip:
            return real_ip[:64]

    return (peer or "unknown")[:64]


def user_agent(request) -> str:
    return (request.headers.get("user-agent") or "")[:255]


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def record_login_event(db, request, username: str, *, user_id: int | None, success: bool, reason: str):
    db.add(LoginEvent(
        user_id=user_id,
        username=(username or "")[:80],
        ip=client_ip(request),
        user_agent=user_agent(request),
        success=success,
        reason=reason[:80],
    ))


def login_block_remaining_seconds(db, request, *, max_failures: int = 10, window_minutes: int = 15, block_minutes: int = 30) -> int:
    ip = client_ip(request)
    now = datetime.utcnow()
    window_start = now - timedelta(minutes=window_minutes)
    failures = db.scalars(
        select(LoginEvent)
        .where(
            LoginEvent.ip == ip,
            LoginEvent.success == False,
            LoginEvent.created_at >= window_start,
        )
        .order_by(LoginEvent.created_at.desc())
    ).all()
    if len(failures) < max_failures:
        return 0
    trigger = failures[max_failures - 1].created_at
    until = trigger + timedelta(minutes=block_minutes)
    return max(0, int((until - now).total_seconds()))


def create_login_session(db, request, user) -> str:
    raw = secrets.token_urlsafe(32)
    row = LoginSession(
        user_id=user.id,
        token_hash=token_hash(raw),
        ip=client_ip(request),
        user_agent=user_agent(request),
    )
    db.add(row)
    db.flush()
    request.session["user_id"] = user.id
    request.session["login_session_token"] = raw
    request.session["login_session_id"] = row.id
    return raw


def get_login_session(db, request) -> LoginSession | None:
    raw = request.session.get("login_session_token")
    session_id = request.session.get("login_session_id")
    user_id = request.session.get("user_id")
    if not raw or not session_id or not user_id:
        return None
    row = db.get(LoginSession, int(session_id))
    if not row or row.user_id != int(user_id) or row.revoked_at is not None:
        return None
    if not secrets.compare_digest(row.token_hash, token_hash(str(raw))):
        return None
    now = datetime.utcnow()
    if not row.last_seen_at or (now - row.last_seen_at).total_seconds() >= 300:
        row.last_seen_at = now
    return row


def revoke_current_session(db, request):
    row = get_login_session(db, request)
    if row and row.revoked_at is None:
        row.revoked_at = datetime.utcnow()


def revoke_other_sessions(db, request, user_id: int) -> int:
    current_id = request.session.get("login_session_id")
    rows = db.scalars(
        select(LoginSession).where(
            LoginSession.user_id == user_id,
            LoginSession.revoked_at.is_(None),
        )
    ).all()
    count = 0
    for row in rows:
        if current_id and row.id == int(current_id):
            continue
        row.revoked_at = datetime.utcnow()
        count += 1
    return count


def create_password_reset_token(db, user_id: int, minutes: int = 30) -> str:
    raw = secrets.token_urlsafe(32)
    db.add(PasswordResetToken(
        user_id=user_id,
        token_hash=token_hash(raw),
        expires_at=datetime.utcnow() + timedelta(minutes=minutes),
    ))
    return raw


def consume_password_reset_token(db, raw_token: str) -> PasswordResetToken | None:
    if not raw_token:
        return None
    row = db.scalar(select(PasswordResetToken).where(PasswordResetToken.token_hash == token_hash(raw_token)))
    if not row or row.used_at is not None or row.expires_at <= datetime.utcnow():
        return None
    row.used_at = datetime.utcnow()
    return row


def new_totp_secret() -> str:
    # RFC 6238 compatible Base32 secret, no third-party dependency required.
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _totp_code(secret: str, counter: int, digits: int = 6) -> str:
    raw = (secret or "").strip().upper().replace(" ", "")
    padding = "=" * ((8 - len(raw) % 8) % 8)
    key = base64.b32decode(raw + padding, casefold=True)
    msg = struct.pack(">Q", int(counter))
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % (10 ** digits)).zfill(digits)


def verify_totp(secret: str, code: str) -> bool:
    code = (code or "").strip().replace(" ", "")
    if not secret or not code or not code.isdigit() or len(code) != 6:
        return False
    counter = int(time.time()) // 30
    try:
        return any(hmac.compare_digest(_totp_code(secret, counter + drift), code) for drift in (-1, 0, 1))
    except Exception:
        return False


def totp_uri(secret: str, username: str, issuer: str = "NAT VPS") -> str:
    label = f"{issuer}:{username}"
    return f"otpauth://totp/{quote(label)}?secret={quote(secret)}&issuer={quote(issuer)}&algorithm=SHA1&digits=6&period=30"
