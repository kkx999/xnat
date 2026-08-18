from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from .audit import write_audit
from .geo import confirmation_matches, server_display_id
from .jobs import enqueue_job
from .lifecycle import lifecycle_config, lifecycle_state
from .models import BalanceLedger, Job, Order, Server, User
from .notifications import queue_notification
from .traffic import (
    configured_bandwidth_mbps,
    enforce_traffic_policy,
    reset_cycle,
    traffic_quota_gb,
    traffic_raw_percent,
    traffic_used_bytes,
)


class ServiceActionError(ValueError):
    """User-facing business-rule failure shared by Web and Mobile API."""


def _money_text(cents: int) -> str:
    return f"¥{int(cents or 0) / 100:.2f}"


def traffic_reset_price_cents(server: Server) -> int:
    """Return the effective paid traffic-reset price for an existing service."""
    plan_price = int(getattr(getattr(server, "plan", None), "traffic_reset_price_cents", 0) or 0)
    if plan_price > 0:
        return plan_price
    snapshot_price = int(getattr(server, "monthly_price_cents", 0) or 0)
    if snapshot_price > 0:
        return snapshot_price
    return int(getattr(getattr(server, "plan", None), "monthly_price_cents", 0) or 0)


def traffic_reset_state(db, user: User, server: Server) -> dict:
    """Describe whether a customer can reset traffic right now.

    This is intentionally side-effect free so clients can render controls without
    trying the paid operation first.
    """
    state = lifecycle_state(server, lifecycle_config(db), datetime.utcnow())
    quota_gb = int(traffic_quota_gb(server) or 0)
    raw_percent = float(traffic_raw_percent(server) or 0.0)
    price = traffic_reset_price_cents(server)

    available = True
    reason = ""
    if state["code"] in {"expired", "suspended", "delete_queued"}:
        available = False
        reason = "服务器已超过到期宽限期，请续费后再执行此操作。"
    elif quota_gb <= 0:
        available = False
        reason = "当前套餐为不限流量，无需重置。"
    elif raw_percent < 100.0:
        available = False
        reason = "仅当本周期流量已经用尽后才能重置流量。"
    elif price <= 0:
        available = False
        reason = "该套餐尚未配置有效的流量重置价格，请联系管理员。"
    elif int(user.balance_cents or 0) < price:
        available = False
        reason = f"余额不足，重置流量需要 {_money_text(price)}。"

    return {
        "available": available,
        "reason": reason,
        "price_cents": int(price or 0),
        "quota_gb": quota_gb,
        "used_bytes": int(traffic_used_bytes(server) or 0),
        "raw_percent": round(raw_percent, 2),
        "balance_cents": int(user.balance_cents or 0),
    }


def reset_server_traffic(db, user: User, server: Server, provider, *, request=None, audit_action: str = "server.traffic.reset") -> dict:
    """Perform the paid self-service traffic reset used by Web and Mobile API."""
    reset_state = traffic_reset_state(db, user, server)
    if not reset_state["available"]:
        raise ServiceActionError(reset_state["reason"])

    reset_price = int(reset_state["price_cents"])
    before = {
        "used_bytes": int(traffic_used_bytes(server) or 0),
        "quota_gb": int(traffic_quota_gb(server) or 0),
        "cycle_start": server.traffic_cycle_start.isoformat() if server.traffic_cycle_start else None,
        "cycle_end": server.traffic_cycle_end.isoformat() if server.traffic_cycle_end else None,
        "traffic_throttled": bool(server.traffic_throttled),
        "balance_cents": int(user.balance_cents or 0),
    }

    order = Order(
        user_id=user.id,
        plan_id=server.plan_id,
        server_id=server.id,
        amount_cents=reset_price,
        status="completed",
        kind="traffic_reset",
    )
    db.add(order)
    db.flush()

    user.balance_cents = int(user.balance_cents or 0) - reset_price
    db.add(BalanceLedger(
        user_id=user.id,
        delta_cents=-reset_price,
        balance_after_cents=int(user.balance_cents or 0),
        kind="traffic_reset",
        reference_type="order",
        reference_id=order.id,
        note=f"流量重置 {server_display_id(server)}",
    ))

    now = datetime.utcnow()
    reset_cycle(server, now)
    server.traffic_throttle_exempt = False
    provider_error = None
    try:
        enforce_traffic_policy(server, provider, now)
    except Exception as exc:  # Reset stays committed; policy worker can retry bandwidth restore.
        provider_error = str(exc)[:180]

    write_audit(
        db,
        actor=user,
        request=request,
        action=audit_action,
        target_type="server",
        target_id=server.id,
        target_name=server.name,
        detail={
            "order_id": order.id,
            "amount_cents": reset_price,
            "balance_after_cents": int(user.balance_cents or 0),
            "before": before,
            "cycle_start": server.traffic_cycle_start.isoformat() if server.traffic_cycle_start else None,
            "cycle_end": server.traffic_cycle_end.isoformat() if server.traffic_cycle_end else None,
            "provider_error": provider_error,
        },
    )
    queue_notification(
        db,
        user,
        title="流量重置成功",
        body=(
            f"{server_display_id(server)} 已扣除 {_money_text(reset_price)} 并开启新的流量周期，本周期用量已清零。"
            + (" 带宽恢复正在等待后台重试。" if provider_error else f" 当前带宽已恢复为 {configured_bandwidth_mbps(server)} Mbps。")
            + f" 当前余额 {_money_text(user.balance_cents)}。"
        ),
        kind="billing",
        severity="warning" if provider_error else "success",
        event_key=f"traffic-self-reset:{order.id}",
    )

    return {
        "order": order,
        "price_cents": reset_price,
        "balance_after_cents": int(user.balance_cents or 0),
        "cycle_start": server.traffic_cycle_start,
        "cycle_end": server.traffic_cycle_end,
        "provider_error": provider_error,
    }


def enqueue_server_delete(db, user: User, server: Server, confirm_name: str, *, request=None, audit_action: str = "server.delete.enqueue") -> tuple[Job, bool]:
    """Validate the stable display identifier and enqueue deletion once."""
    if not confirmation_matches(server, confirm_name):
        raise ServiceActionError("删除确认编号不正确。")

    active_job = db.scalar(
        select(Job).where(
            Job.server_id == server.id,
            Job.status.in_(["pending", "running"]),
            Job.job_type == "delete_server",
        ).order_by(Job.id.desc())
    )
    if active_job:
        return active_job, True

    job = enqueue_job(db, "delete_server", user_id=user.id, server_id=server.id, payload={})
    write_audit(
        db,
        actor=user,
        request=request,
        action=audit_action,
        target_type="server",
        target_id=server.id,
        target_name=server.name,
        detail={"job_id": job.id, "display_id": server_display_id(server)},
    )
    return job, False
