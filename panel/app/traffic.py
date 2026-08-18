from __future__ import annotations

from datetime import datetime, timedelta
from sqlalchemy import select

from .db import SessionLocal
from .models import Server, User
from .geo import server_display_id
from .notifications import queue_notification
from .providers.base import NetworkStats

CYCLE_DAYS = 30
GIB = 1024 ** 3
THROTTLE_MBPS = 1


def traffic_cycle_mode(server: Server) -> str:
    mode = (getattr(server, "traffic_cycle_mode", None) or "rolling30").strip().lower()
    return mode if mode in {"rolling30", "monthly"} else "rolling30"


def traffic_cycle_day(server: Server) -> int:
    try:
        day = int(getattr(server, "traffic_cycle_day", 1) or 1)
    except Exception:
        day = 1
    return max(1, min(28, day))


def traffic_cycle_mode_label(server: Server) -> str:
    if traffic_cycle_mode(server) == "monthly":
        return f"每月 {traffic_cycle_day(server)} 日重置"
    return "每 30 天滚动重置"


def _next_month_boundary(now: datetime, day: int) -> datetime:
    day = max(1, min(28, int(day)))
    candidate = now.replace(day=day, hour=0, minute=0, second=0, microsecond=0)
    if candidate > now:
        return candidate
    year = now.year + (1 if now.month == 12 else 0)
    month = 1 if now.month == 12 else now.month + 1
    return candidate.replace(year=year, month=month, day=day)


def _next_cycle_end(server: Server, start: datetime) -> datetime:
    if traffic_cycle_mode(server) == "monthly":
        return _next_month_boundary(start, traffic_cycle_day(server))
    return start + timedelta(days=CYCLE_DAYS)


def reset_cycle(server: Server, now: datetime) -> None:
    """Reset used traffic without coupling the cycle to VPS expiry.

    rolling30: a fresh 30-day window starts now.
    monthly: the fresh window starts now and ends at the next configured day.
    """
    server.traffic_cycle_start = now
    server.traffic_cycle_end = _next_cycle_end(server, now)
    server.traffic_used_rx_bytes = 0
    server.traffic_used_tx_bytes = 0
    server.traffic_last_rx_bytes = None
    server.traffic_last_tx_bytes = None
    server.traffic_last_sampled_at = now
    server.traffic_bonus_gb = 0
    server.traffic_throttle_exempt = False


def ensure_cycle(server: Server, now: datetime) -> bool:
    """Ensure a current traffic window exists for the configured policy."""
    changed = False

    if server.traffic_cycle_start is None or server.traffic_cycle_end is None:
        reset_cycle(server, now)
        return True

    if server.traffic_cycle_end and now >= server.traffic_cycle_end:
        start = server.traffic_cycle_end
        end = _next_cycle_end(server, start)
        while end <= now:
            start = end
            end = _next_cycle_end(server, start)
        server.traffic_cycle_start = start
        server.traffic_cycle_end = end
        server.traffic_used_rx_bytes = 0
        server.traffic_used_tx_bytes = 0
        server.traffic_last_rx_bytes = None
        server.traffic_last_tx_bytes = None
        server.traffic_last_sampled_at = now
        server.traffic_bonus_gb = 0
        server.traffic_throttle_exempt = False
        # Keep traffic_throttled=True until policy enforcement successfully
        # restores the configured bandwidth on the provider.
        changed = True

    return changed


def apply_sample(server: Server, stats: NetworkStats, now: datetime, *, seed_first_sample: bool = False) -> bool:
    if not stats or not stats.available:
        return False

    cycle_reset = ensure_cycle(server, now)
    rx = max(0, int(stats.rx_bytes or 0))
    tx = max(0, int(stats.tx_bytes or 0))

    last_rx = server.traffic_last_rx_bytes
    last_tx = server.traffic_last_tx_bytes

    if last_rx is None or last_tx is None or cycle_reset:
        if seed_first_sample and not cycle_reset:
            server.traffic_used_rx_bytes = max(0, int(server.traffic_used_rx_bytes or 0)) + rx
            server.traffic_used_tx_bytes = max(0, int(server.traffic_used_tx_bytes or 0)) + tx
        server.traffic_last_rx_bytes = rx
        server.traffic_last_tx_bytes = tx
        server.traffic_last_sampled_at = now
        return True

    # Kernel counters reset after reboot/reinstall/interface recreation.
    delta_rx = rx - last_rx if rx >= last_rx else rx
    delta_tx = tx - last_tx if tx >= last_tx else tx

    server.traffic_used_rx_bytes = max(0, int(server.traffic_used_rx_bytes or 0)) + max(0, delta_rx)
    server.traffic_used_tx_bytes = max(0, int(server.traffic_used_tx_bytes or 0)) + max(0, delta_tx)
    server.traffic_last_rx_bytes = rx
    server.traffic_last_tx_bytes = tx
    server.traffic_last_sampled_at = now
    return True


def traffic_base_quota_gb(server: Server) -> int:
    return max(0, int(server.traffic_gb or 0))


def traffic_bonus_gb(server: Server) -> int:
    return max(0, int(server.traffic_bonus_gb or 0))


def traffic_quota_gb(server: Server) -> int:
    base = traffic_base_quota_gb(server)
    if base <= 0:
        return 0  # 0 means unlimited
    return base + traffic_bonus_gb(server)


def traffic_quota_bytes(server: Server) -> int:
    return traffic_quota_gb(server) * GIB


def traffic_used_bytes(server: Server) -> int:
    return max(0, int(server.traffic_used_rx_bytes or 0)) + max(0, int(server.traffic_used_tx_bytes or 0))


def traffic_remaining_bytes(server: Server) -> int:
    quota = traffic_quota_bytes(server)
    if quota <= 0:
        return 0
    return max(0, quota - traffic_used_bytes(server))


def traffic_raw_percent(server: Server) -> float:
    quota = traffic_quota_bytes(server)
    if quota <= 0:
        return 0.0
    return max(0.0, traffic_used_bytes(server) * 100.0 / quota)


def traffic_percent(server: Server) -> float:
    return min(100.0, traffic_raw_percent(server))


def traffic_level(server: Server) -> str:
    quota = traffic_quota_bytes(server)
    if quota <= 0:
        return "unlimited"
    percent = traffic_raw_percent(server)
    if percent >= 100:
        return "exempt" if bool(server.traffic_throttle_exempt) else "exhausted"
    if percent >= 90:
        return "critical"
    if percent >= 80:
        return "warning"
    return "normal"


def traffic_status_label(server: Server) -> str:
    return {
        "unlimited": "不限流量",
        "normal": "流量正常",
        "warning": "已使用超过 80%",
        "critical": "已使用超过 90%",
        "exhausted": "流量已用尽 · 自动限速 1 Mbps",
        "exempt": "流量已用尽 · 管理员已解除限速",
    }[traffic_level(server)]


def configured_bandwidth_mbps(server: Server) -> int:
    return max(0, int(server.bandwidth_mbps or 0))


def effective_bandwidth_mbps(server: Server) -> int:
    if bool(server.traffic_throttled):
        return THROTTLE_MBPS
    return configured_bandwidth_mbps(server)


def enforce_traffic_policy(server: Server, provider, now: datetime | None = None) -> str:
    """Apply or remove the 1 Mbps over-quota throttle.

    server.bandwidth_mbps remains the customer's configured/plan bandwidth.
    The automatic 1 Mbps policy is tracked separately so a new cycle can
    restore the original bandwidth exactly.
    """
    now = now or datetime.utcnow()
    quota = traffic_quota_bytes(server)
    used = traffic_used_bytes(server)
    should_throttle = quota > 0 and used >= quota and not bool(server.traffic_throttle_exempt)

    if should_throttle:
        if not bool(server.traffic_throttled):
            provider.set_bandwidth(server.provider_instance_id, THROTTLE_MBPS)
            server.traffic_throttled = True
            server.traffic_throttled_at = now
            return "throttled"
        return "already_throttled"

    # Unlimited plans, quota additions, manual exemptions and a new cycle all
    # converge here. Restore the configured service bandwidth if necessary.
    if bool(server.traffic_throttled):
        provider.set_bandwidth(server.provider_instance_id, configured_bandwidth_mbps(server))
        server.traffic_throttled = False
        server.traffic_throttled_at = None
        return "restored"

    return "unchanged"


def collect_all(provider, provider_name: str) -> tuple[int, int]:
    sampled = 0
    failed = 0
    now = datetime.utcnow()

    with SessionLocal() as db:
        servers = db.scalars(
            select(Server).where(
                Server.deleted_at.is_(None),
                Server.provider == provider_name,
                Server.provider_instance_id.is_not(None),
                Server.status.in_(["running", "stopped"]),
            )
        ).all()

        for server in servers:
            previous_cycle_start = server.traffic_cycle_start
            previous_throttled = bool(server.traffic_throttled)
            cycle_changed = ensure_cycle(server, now)

            if server.status == "running":
                try:
                    stats = provider.network_stats(server.provider_instance_id)
                    seed_first = (
                        server.traffic_last_rx_bytes is None
                        and server.traffic_last_tx_bytes is None
                        and server.traffic_last_sampled_at is None
                    )
                    if apply_sample(server, stats, now, seed_first_sample=seed_first):
                        sampled += 1
                except Exception as exc:
                    failed += 1
                    print(f"[traffic] sample {server.name}: {exc}")

            policy_result = "unchanged"
            try:
                policy_result = enforce_traffic_policy(server, provider, now)
            except Exception as exc:
                failed += 1
                print(f"[traffic] policy {server.name}: {exc}")

            user = db.get(User, server.user_id)
            if user:
                cycle_key = (server.traffic_cycle_start or now).strftime("%Y%m%d%H%M%S")
                level = traffic_level(server)
                if level == "warning":
                    queue_notification(db, user, title="流量已使用 80%", body=f"{server_display_id(server)} 本周期流量已使用 {traffic_raw_percent(server):.1f}%。", kind="traffic", severity="warning", event_key=f"traffic80:{server.id}:{cycle_key}")
                elif level == "critical":
                    queue_notification(db, user, title="流量已使用 90%", body=f"{server_display_id(server)} 本周期流量已使用 {traffic_raw_percent(server):.1f}%，请关注剩余流量。", kind="traffic", severity="warning", event_key=f"traffic90:{server.id}:{cycle_key}")
                elif level == "exhausted":
                    queue_notification(db, user, title="流量已用尽，已自动限速", body=f"{server_display_id(server)} 已用尽本周期流量，当前带宽自动降为 {THROTTLE_MBPS} Mbps；新周期开始后会自动恢复。", kind="traffic", severity="error", event_key=f"traffic100:{server.id}:{cycle_key}")

                if cycle_changed and previous_cycle_start is not None:
                    queue_notification(db, user, title="流量周期已重置", body=f"{server_display_id(server)} 已进入新的流量周期，流量重新计数。", kind="traffic", severity="success", event_key=f"traffic-reset:{server.id}:{cycle_key}")
                if previous_throttled and policy_result == "restored":
                    queue_notification(db, user, title="带宽已恢复", body=f"{server_display_id(server)} 的流量限速已解除，带宽恢复为 {configured_bandwidth_mbps(server) or '不限'} Mbps。", kind="traffic", severity="success", event_key=f"traffic-restored:{server.id}:{cycle_key}")

        db.commit()

    return sampled, failed
