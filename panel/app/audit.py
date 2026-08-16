from __future__ import annotations

import json
from typing import Any

from .models import AuditLog


def request_ip(request) -> str | None:
    if request is None:
        return None
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    if forwarded:
        return forwarded[:64]
    client = getattr(request, "client", None)
    return (getattr(client, "host", None) or "")[:64] or None


def write_audit(
    db,
    *,
    actor=None,
    request=None,
    action: str,
    target_type: str | None = None,
    target_id: str | int | None = None,
    target_name: str | None = None,
    detail: Any = None,
    success: bool = True,
    actor_username: str | None = None,
):
    if isinstance(detail, (dict, list, tuple)):
        detail = json.dumps(detail, ensure_ascii=False, separators=(",", ":"))
    elif detail is not None:
        detail = str(detail)

    row = AuditLog(
        actor_user_id=getattr(actor, "id", None),
        actor_username=(actor_username or getattr(actor, "username", None) or "system")[:80],
        ip=request_ip(request),
        action=action[:80],
        target_type=(target_type or None),
        target_id=str(target_id)[:80] if target_id is not None else None,
        target_name=(target_name or None),
        detail=detail,
        success=bool(success),
    )
    db.add(row)
    return row
