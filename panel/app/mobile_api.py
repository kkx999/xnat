from __future__ import annotations

import json
import os
import secrets
import socket
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Request

from . import __version__ as PANEL_VERSION
from sqlalchemy import func, select

from .audit import write_audit
from .auth import verify_password
from .db import SessionLocal
from .lifecycle import lifecycle_config, lifecycle_state
from .jobs import enqueue_job
from .models import BalanceLedger, Coupon, CouponRedemption, Job, LoginSession, Notification, Order, Plan, PortMapping, RechargeOrder, Server, SiteSetting, SystemImage, Ticket, TicketMessage, User
from .providers.incus import IncusProvider
from .providers.mock import MockProvider
from .providers.remote import RemoteHostProvider
from .nodes import allocate_host_port, public_port_in_use_on_host, select_host_for_plan
from .security import client_ip, login_block_remaining_seconds, record_login_event, token_hash, user_agent, verify_totp
from .crypto import decrypt_secret
from .traffic import ensure_cycle, traffic_percent, traffic_quota_gb, traffic_remaining_bytes, traffic_used_bytes
from .notifications import queue_notification

router = APIRouter(prefix="/api/v1", tags=["mobile-api"])
PROVIDER_NAME = os.getenv("VPS_PROVIDER", "mock").strip().lower()
MOBILE_TOKEN_TTL_DAYS = max(1, int(os.getenv("MOBILE_TOKEN_TTL_DAYS", "30") or 30))


def _provider():
    if PROVIDER_NAME == "remote":
        return RemoteHostProvider()
    return IncusProvider() if PROVIDER_NAME == "incus" else MockProvider()


def _get_setting(db, key: str, default: str = "") -> str:
    row = db.get(SiteSetting, key)
    return row.value if row else default


def _setting_enabled(db, key: str, default: bool = False) -> bool:
    fallback = "true" if default else "false"
    return _get_setting(db, key, fallback).strip().lower() in {"1", "true", "yes", "on"}


def _parse_port_spec(value: str) -> set[int]:
    ports: set[int] = set()
    for part in (value or "").replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            left, right = part.split("-", 1)
            if left.isdigit() and right.isdigit():
                a, b = int(left), int(right)
                if a > b:
                    a, b = b, a
                for port in range(max(1, a), min(65535, b) + 1):
                    ports.add(port)
        elif part.isdigit():
            port = int(part)
            if 1 <= port <= 65535:
                ports.add(port)
    return ports


def _validate_port_policy(db, protocol: str, private_port: int):
    if protocol == "tcp" and not _setting_enabled(db, "port_tcp_enabled", True):
        raise HTTPException(409, "当前暂停新增 TCP 端口")
    if protocol == "udp" and not _setting_enabled(db, "port_udp_enabled", True):
        raise HTTPException(409, "当前暂停新增 UDP 端口")
    blocked = _parse_port_spec(_get_setting(db, "port_blocked_private", ""))
    if private_port in blocked:
        raise HTTPException(409, f"内部端口 {private_port} 已被管理员禁止映射")


def _public_port_in_use(db, port: int, protocol: str) -> bool:
    if db.scalar(select(PortMapping).where(PortMapping.public_port == port, PortMapping.protocol == protocol)):
        return True
    if db.scalar(select(Server).where(Server.ssh_port == port, Server.deleted_at.is_(None))):
        return True
    socktype = socket.SOCK_STREAM if protocol == "tcp" else socket.SOCK_DGRAM
    with socket.socket(socket.AF_INET, socktype) as sock:
        try:
            sock.bind(("0.0.0.0", port))
            return False
        except OSError:
            return True


def _allocate_public_port(db, protocol: str, server: Server) -> int:
    blocked = _parse_port_spec(_get_setting(db, "port_blocked_public", ""))
    if PROVIDER_NAME == "remote":
        if not server.host:
            raise HTTPException(409, "服务器未绑定宿主机")
        try:
            return allocate_host_port(db, server.host, protocol, blocked)
        except Exception as exc:
            raise HTTPException(409, str(exc)[:180])
    start = int(_get_setting(db, "port_public_start", os.getenv("INCUS_PORT_START", "20000")) or os.getenv("INCUS_PORT_START", "20000"))
    end = int(_get_setting(db, "port_public_end", os.getenv("INCUS_PORT_END", "29999")) or os.getenv("INCUS_PORT_END", "29999"))
    if start > end:
        start, end = end, start
    start = max(1024, start)
    end = min(65535, end)
    for port in range(start, end + 1):
        if port in blocked:
            continue
        if not _public_port_in_use(db, port, protocol):
            return port
    raise HTTPException(409, "公网端口池已经耗尽")


def _server_for_user(db, user: User, server_id: int) -> Server:
    server = db.get(Server, server_id)
    if not server or server.user_id != user.id or server.deleted_at is not None:
        raise HTTPException(404, "服务器不存在")
    return server


def _lifecycle_operation_block(db, server: Server) -> str | None:
    state = lifecycle_state(server, lifecycle_config(db), datetime.utcnow())
    if state["code"] in {"expired", "suspended", "delete_queued"}:
        return "服务器已超过到期宽限期，请续费后再执行此操作。"
    return None


def _json_body(data, key: str, default=""):
    value = data.get(key, default)
    return value.strip() if isinstance(value, str) else value


def _bearer(request: Request) -> str:
    header = (request.headers.get("authorization") or "").strip()
    if not header.lower().startswith("bearer "):
        raise HTTPException(401, "缺少登录令牌")
    raw = header[7:].strip()
    if not raw:
        raise HTTPException(401, "缺少登录令牌")
    return raw


def _mobile_session(db, request: Request) -> tuple[User, LoginSession]:
    raw = _bearer(request)
    row = db.scalar(select(LoginSession).where(LoginSession.token_hash == token_hash(raw)))
    if not row or row.revoked_at is not None:
        raise HTTPException(401, "登录已失效，请重新登录")
    if row.created_at < datetime.utcnow() - timedelta(days=MOBILE_TOKEN_TTL_DAYS):
        row.revoked_at = datetime.utcnow()
        db.commit()
        raise HTTPException(401, "登录已过期，请重新登录")
    user = db.get(User, row.user_id)
    if not user or not user.is_active:
        row.revoked_at = datetime.utcnow()
        db.commit()
        raise HTTPException(401, "账号不可用")
    if not row.last_seen_at or (datetime.utcnow() - row.last_seen_at).total_seconds() >= 300:
        row.last_seen_at = datetime.utcnow()
        db.flush()
    return user, row


def _server_ui_status_map(db, servers) -> dict[int, str]:
    rows = list(servers or [])
    result = {s.id: (s.status or "unknown") for s in rows}
    ids = [s.id for s in rows if s.id]
    if not ids:
        return result
    jobs = db.scalars(
        select(Job).where(
            Job.server_id.in_(ids),
            Job.status.in_(["pending", "running"]),
            Job.job_type.in_(["provision_server", "reinstall_server", "delete_server"]),
        ).order_by(Job.id.desc())
    ).all()
    transient = {
        "provision_server": "provisioning",
        "reinstall_server": "reinstalling",
        "delete_server": "deleting",
    }
    seen = set()
    for job in jobs:
        if not job.server_id or job.server_id in seen:
            continue
        result[job.server_id] = transient.get(job.job_type, result.get(job.server_id, "unknown"))
        seen.add(job.server_id)
    return result


def _server_payload(db, server: Server, ui_status: str | None = None) -> dict:
    cfg = lifecycle_config(db)
    life = lifecycle_state(server, cfg, datetime.utcnow())
    used = int(traffic_used_bytes(server) or 0)
    remaining = int(traffic_remaining_bytes(server) or 0)
    return {
        "id": server.id,
        "name": server.name,
        "status": ui_status or server.status or "unknown",
        "public_ip": server.public_ip,
        "private_ip": server.private_ip,
        "ssh_port": server.ssh_port,
        "os_name": server.os_name,
        "cpu": server.cpu,
        "memory_mb": server.memory_mb,
        "disk_gb": server.disk_gb,
        "bandwidth_mbps": server.bandwidth_mbps,
        "traffic_quota_gb": int(traffic_quota_gb(server) or 0),
        "traffic_used_bytes": used,
        "traffic_remaining_bytes": remaining,
        "traffic_percent": round(float(traffic_percent(server) or 0), 2),
        "traffic_throttled": bool(server.traffic_throttled),
        "virtualization_type": server.virtualization_type,
        "expires_at": server.expires_at.isoformat() + "Z" if server.expires_at else None,
        "lifecycle": life,
        "port_limit": int(server.port_limit if server.port_limit is not None else (server.plan.port_count if server.plan else 0) or 0),
        "port_count": len(server.ports),
        "ports": [
            {
                "id": p.id,
                "public_port": p.public_port,
                "private_port": p.private_port,
                "protocol": p.protocol,
            }
            for p in sorted(server.ports, key=lambda x: (x.public_port, x.protocol))
        ],
    }


def _money_text(cents: int) -> str:
    return f"¥{int(cents or 0) / 100:.2f}"


def _active_plan_service_count(db, plan_id: int) -> int:
    return int(db.scalar(
        select(func.count()).select_from(Server).where(
            Server.plan_id == plan_id,
            Server.deleted_at.is_(None),
            Server.status.in_(["provisioning", "running", "stopped"]),
        )
    ) or 0)


def _plan_stock(db, plan: Plan) -> dict:
    used = _active_plan_service_count(db, plan.id)
    limit = int(plan.stock_limit or 0)
    if limit <= 0:
        return {"used": used, "limit": 0, "available": None, "sold_out": False}
    available = max(limit - used, 0)
    return {"used": used, "limit": limit, "available": available, "sold_out": available <= 0}


def _plan_payload(db, plan: Plan) -> dict:
    stock = _plan_stock(db, plan)
    return {
        "id": plan.id,
        "name": plan.name,
        "cpu": int(plan.cpu or 0),
        "memory_mb": int(plan.memory_mb or 0),
        "disk_gb": int(plan.disk_gb or 0),
        "port_count": int(plan.port_count or 0),
        "bandwidth_mbps": int(plan.bandwidth_mbps or 0),
        "traffic_gb": int(plan.traffic_gb or 0),
        "monthly_price_cents": int(plan.monthly_price_cents or 0),
        "virtualization_type": plan.virtualization_type or "lxc",
        "is_recommended": bool(plan.is_recommended),
        "recommendation_label": plan.recommendation_label or "推荐",
        "stock": stock,
    }


def _calculate_coupon_discount(db, user: User, coupon_code: str, price_cents: int):
    code = (coupon_code or "").strip().upper()
    if not code:
        return None, 0
    coupon = db.scalar(select(Coupon).where(Coupon.code == code))
    if not coupon or not coupon.is_active:
        raise ValueError("优惠码无效或已停用")
    now = datetime.utcnow()
    if coupon.expires_at and coupon.expires_at <= now:
        raise ValueError("优惠码已过期")
    if coupon.max_uses > 0 and coupon.used_count >= coupon.max_uses:
        raise ValueError("优惠码使用次数已耗尽")
    if price_cents < int(coupon.min_order_cents or 0):
        raise ValueError(f"该优惠码最低消费为 {_money_text(coupon.min_order_cents)}")
    used = db.scalar(select(CouponRedemption).where(
        CouponRedemption.coupon_id == coupon.id,
        CouponRedemption.user_id == user.id,
    ))
    if used:
        raise ValueError("该账号已经使用过此优惠码")
    if coupon.discount_type == "percent":
        if coupon.discount_value <= 0 or coupon.discount_value > 100:
            raise ValueError("优惠码配置错误")
        discount = price_cents * coupon.discount_value // 100
    elif coupon.discount_type == "fixed":
        discount = coupon.discount_value
    else:
        raise ValueError("优惠码配置错误")
    return coupon, max(0, min(price_cents, int(discount or 0)))


def _change_balance(db, user: User, delta_cents: int, *, kind: str, reference_type: str | None = None, reference_id: int | None = None, note: str | None = None):
    user.balance_cents = int(user.balance_cents or 0) + int(delta_cents or 0)
    db.add(BalanceLedger(
        user_id=user.id,
        delta_cents=int(delta_cents or 0),
        balance_after_cents=int(user.balance_cents or 0),
        kind=kind,
        reference_type=reference_type,
        reference_id=reference_id,
        note=note,
    ))
    return int(user.balance_cents or 0)


def _allocate_purchase_port(db, protocol: str, host=None) -> int:
    blocked = _parse_port_spec(_get_setting(db, "port_blocked_public", ""))
    if host is not None:
        return allocate_host_port(db, host, protocol, blocked)
    start = int(_get_setting(db, "port_public_start", os.getenv("INCUS_PORT_START", "20000")) or os.getenv("INCUS_PORT_START", "20000"))
    end = int(_get_setting(db, "port_public_end", os.getenv("INCUS_PORT_END", "29999")) or os.getenv("INCUS_PORT_END", "29999"))
    if start > end:
        start, end = end, start
    start = max(1024, start)
    end = min(65535, end)
    for port in range(start, end + 1):
        if port in blocked:
            continue
        if not _public_port_in_use(db, port, protocol):
            return port
    raise ValueError("公网端口池已经耗尽")


def _queue_purchase_service(db, user: User, plan: Plan, system_image: SystemImage, *, final_price: int, coupon=None, discount_cents: int = 0, client_request_id: str = ""):
    if _plan_stock(db, plan)["sold_out"]:
        raise ValueError("该套餐已经售罄")
    host = select_host_for_plan(db, plan) if PROVIDER_NAME == "remote" else None
    order = Order(
        user_id=user.id,
        plan_id=plan.id,
        amount_cents=int(final_price or 0),
        status="paid",
        kind="purchase",
        coupon_code=coupon.code if coupon else None,
        discount_cents=int(discount_cents or 0),
    )
    db.add(order)
    db.flush()
    ssh_port = _allocate_purchase_port(db, "tcp", host)
    provider = _provider()
    public_ip = host.public_ip if host is not None else getattr(provider, "public_host", os.getenv("INCUS_PUBLIC_HOST", "203.0.113.10"))
    server = Server(
        user_id=user.id,
        plan_id=plan.id,
        order_id=order.id,
        host_id=host.id if host else None,
        name=f"nat-{user.id}-{order.id}",
        provider=PROVIDER_NAME,
        status="provisioning",
        public_ip=public_ip,
        ssh_port=ssh_port,
        os_image_id=system_image.id,
        os_name=system_image.name,
        os_alias=system_image.alias,
        cpu=plan.cpu,
        memory_mb=plan.memory_mb,
        disk_gb=plan.disk_gb,
        port_limit=plan.port_count,
        bandwidth_mbps=plan.bandwidth_mbps,
        traffic_gb=plan.traffic_gb,
        traffic_cycle_mode=_get_setting(db, "traffic_cycle_default_mode", "rolling30"),
        traffic_cycle_day=max(1, min(28, int(_get_setting(db, "traffic_cycle_default_day", "1") or 1))),
        monthly_price_cents=plan.monthly_price_cents,
        virtualization_type=(plan.virtualization_type or "lxc"),
        expires_at=datetime.utcnow() + timedelta(days=30),
        reconcile_status="pending",
    )
    db.add(server)
    db.flush()
    order.server_id = server.id
    ensure_cycle(server, datetime.utcnow())
    if coupon:
        coupon.used_count += 1
        db.add(CouponRedemption(coupon_id=coupon.id, user_id=user.id, order_id=order.id))
    payload = {"order_id": order.id}
    if client_request_id:
        payload["client_request_id"] = client_request_id
    if host:
        payload["host_id"] = host.id
        payload["host_name"] = host.name
    job = enqueue_job(db, "provision_server", user_id=user.id, server_id=server.id, payload=payload)
    return order, server, job


def _purchase_replay(db, user_id: int, request_id: str):
    if not request_id:
        return None
    rows = db.scalars(
        select(Job).where(Job.user_id == user_id, Job.job_type == "provision_server").order_by(Job.id.desc()).limit(50)
    ).all()
    for job in rows:
        try:
            payload = json.loads(job.payload_json or "{}")
        except Exception:
            continue
        if str(payload.get("client_request_id") or "") != request_id:
            continue
        order_id = int(payload.get("order_id") or 0)
        order = db.get(Order, order_id) if order_id else None
        server = db.get(Server, job.server_id) if job.server_id else None
        if order and server and order.user_id == user_id and server.user_id == user_id:
            return order, server, job
    return None


@router.get("/health")
def api_health():
    return {
        "ok": True,
        "api_version": "v1",
        "client": "android",
        "panel_version": PANEL_VERSION,
    }


@router.post("/auth/login")
async def api_login(request: Request):
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(400, "请求格式错误")

    username = str(_json_body(data, "username", ""))[:80]
    password = str(data.get("password") or "")
    totp_code = str(_json_body(data, "totp_code", ""))
    if not username or not password:
        raise HTTPException(400, "请输入用户名和密码")

    with SessionLocal() as db:
        from .models import SiteSetting

        def setting_int(key: str, default: int) -> int:
            row = db.get(SiteSetting, key)
            try:
                return int((row.value if row else str(default)) or default)
            except Exception:
                return default

        max_failures = max(3, setting_int("login_max_failures", 10))
        window_minutes = max(1, setting_int("login_window_minutes", 15))
        block_minutes = max(1, setting_int("login_block_minutes", 30))
        remaining = login_block_remaining_seconds(
            db,
            request,
            max_failures=max_failures,
            window_minutes=window_minutes,
            block_minutes=block_minutes,
        )
        if remaining > 0:
            raise HTTPException(429, f"当前 IP 登录失败次数过多，请约 {max(1, (remaining + 59)//60)} 分钟后再试")

        user = db.scalar(select(User).where(User.username == username))
        if not user or not verify_password(password, user.password_hash):
            record_login_event(db, request, username, user_id=getattr(user, "id", None), success=False, reason="bad_credentials_api")
            db.commit()
            raise HTTPException(401, "用户名或密码错误")
        if not user.is_active:
            record_login_event(db, request, username, user_id=user.id, success=False, reason="disabled_api")
            db.commit()
            raise HTTPException(403, "账号已被停用")

        if user.totp_enabled:
            if not totp_code:
                return {"ok": False, "two_factor_required": True}
            secret = decrypt_secret(user.totp_secret_enc) if user.totp_secret_enc else ""
            if not verify_totp(secret, totp_code):
                record_login_event(db, request, username, user_id=user.id, success=False, reason="bad_totp_api")
                db.commit()
                raise HTTPException(401, "两步验证码错误")

        raw = secrets.token_urlsafe(32)
        row = LoginSession(
            user_id=user.id,
            token_hash=token_hash(raw),
            ip=client_ip(request),
            user_agent=user_agent(request),
        )
        db.add(row)
        user.last_login_at = datetime.utcnow()
        record_login_event(db, request, username, user_id=user.id, success=True, reason="mobile_api")
        write_audit(db, actor=user, request=request, action="account.login.mobile", target_type="user", target_id=user.id, target_name=user.username)
        db.commit()

        return {
            "ok": True,
            "token_type": "Bearer",
            "access_token": raw,
            "expires_in": MOBILE_TOKEN_TTL_DAYS * 86400,
            "user": {
                "id": user.id,
                "username": user.username,
                "email": user.email,
                "balance_cents": user.balance_cents,
                "is_admin": bool(user.is_admin),
                "totp_enabled": bool(user.totp_enabled),
            },
        }


@router.post("/auth/logout")
def api_logout(request: Request):
    with SessionLocal() as db:
        user, row = _mobile_session(db, request)
        row.revoked_at = datetime.utcnow()
        write_audit(db, actor=user, request=request, action="account.logout.mobile", target_type="user", target_id=user.id, target_name=user.username)
        db.commit()
    return {"ok": True}


@router.get("/me")
def api_me(request: Request):
    with SessionLocal() as db:
        user, _ = _mobile_session(db, request)
        unread = db.scalar(select(func.count()).select_from(Notification).where(Notification.user_id == user.id, Notification.read_at.is_(None))) or 0
        db.commit()
        return {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "balance_cents": user.balance_cents,
            "is_admin": bool(user.is_admin),
            "totp_enabled": bool(user.totp_enabled),
            "unread_notifications": int(unread),
        }


@router.get("/dashboard")
def api_dashboard(request: Request):
    with SessionLocal() as db:
        user, _ = _mobile_session(db, request)
        servers = db.scalars(select(Server).where(Server.user_id == user.id, Server.deleted_at.is_(None)).order_by(Server.id.desc())).all()
        statuses = _server_ui_status_map(db, servers)
        now = datetime.utcnow()
        expiring_limit = now + timedelta(days=7)
        order_count = db.scalar(select(func.count()).select_from(Order).where(Order.user_id == user.id)) or 0
        unread = db.scalar(select(func.count()).select_from(Notification).where(Notification.user_id == user.id, Notification.read_at.is_(None))) or 0
        db.commit()
        return {
            "user": {
                "username": user.username,
                "balance_cents": user.balance_cents,
                "is_admin": bool(user.is_admin),
            },
            "stats": {
                "server_count": len(servers),
                "running_count": sum(1 for s in servers if statuses.get(s.id) == "running"),
                "stopped_count": sum(1 for s in servers if statuses.get(s.id) == "stopped"),
                "expiring_7d": sum(1 for s in servers if s.expires_at and now < s.expires_at <= expiring_limit),
                "order_count": int(order_count),
                "unread_notifications": int(unread),
            },
            "servers": [_server_payload(db, s, statuses.get(s.id)) for s in servers[:6]],
        }


@router.get("/servers")
def api_servers(request: Request):
    with SessionLocal() as db:
        user, _ = _mobile_session(db, request)
        servers = db.scalars(select(Server).where(Server.user_id == user.id, Server.deleted_at.is_(None)).order_by(Server.id.desc())).all()
        statuses = _server_ui_status_map(db, servers)
        payload = [_server_payload(db, s, statuses.get(s.id)) for s in servers]
        db.commit()
        return {"items": payload}


@router.get("/servers/{server_id}")
def api_server_detail(request: Request, server_id: int):
    with SessionLocal() as db:
        user, _ = _mobile_session(db, request)
        server = db.get(Server, server_id)
        if not server or server.user_id != user.id or server.deleted_at is not None:
            raise HTTPException(404, "服务器不存在")
        status = _server_ui_status_map(db, [server]).get(server.id, server.status)
        payload = _server_payload(db, server, status)
        db.commit()
        return payload


@router.post("/servers/{server_id}/action")
async def api_server_action(request: Request, server_id: int):
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(400, "请求格式错误")
    action = str(_json_body(data, "action", "")).lower()
    if action not in {"start", "stop", "reboot"}:
        raise HTTPException(400, "不支持的操作")

    with SessionLocal() as db:
        user, _ = _mobile_session(db, request)
        server = db.get(Server, server_id)
        if not server or server.user_id != user.id or server.deleted_at is not None:
            raise HTTPException(404, "服务器不存在")
        state = lifecycle_state(server, lifecycle_config(db), datetime.utcnow())
        if state["code"] in {"expired", "suspended", "delete_queued"} and action != "stop":
            raise HTTPException(409, "服务器已超过到期宽限期，请续费后操作")
        if not server.provider_instance_id:
            raise HTTPException(409, "服务器尚未完成开通")
        try:
            server.status = _provider().power_action(server.provider_instance_id, action)
            write_audit(db, actor=user, request=request, action=f"server.power.{action}.mobile", target_type="server", target_id=server.id, target_name=server.name)
            db.commit()
            return {"ok": True, "status": server.status}
        except HTTPException:
            raise
        except Exception as exc:
            write_audit(db, actor=user, request=request, action=f"server.power.{action}.mobile", target_type="server", target_id=server.id, target_name=server.name, detail=str(exc), success=False)
            db.commit()
            raise HTTPException(502, f"操作失败：{str(exc)[:180]}")


@router.get("/system-images")
def api_system_images(request: Request):
    with SessionLocal() as db:
        _mobile_session(db, request)
        rows = db.scalars(
            select(SystemImage).where(SystemImage.is_active.is_(True), SystemImage.family == "apt").order_by(SystemImage.sort_order, SystemImage.id)
        ).all()
        db.commit()
        return {
            "items": [
                {"id": row.id, "name": row.name, "alias": row.alias, "family": row.family}
                for row in rows
            ]
        }


@router.post("/servers/{server_id}/ports")
async def api_add_port_mapping(request: Request, server_id: int):
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(400, "请求格式错误")
    try:
        private_port = int(data.get("private_port"))
    except Exception:
        raise HTTPException(400, "内部端口无效")
    protocol = str(_json_body(data, "protocol", "tcp")).lower()
    if protocol not in {"tcp", "udp"} or not (1 <= private_port <= 65535):
        raise HTTPException(400, "端口参数无效")

    with SessionLocal() as db:
        user, _ = _mobile_session(db, request)
        server = _server_for_user(db, user, server_id)
        blocked = _lifecycle_operation_block(db, server)
        if blocked:
            raise HTTPException(409, blocked)
        if not server.provider_instance_id:
            raise HTTPException(409, "服务器尚未完成开通")
        _validate_port_policy(db, protocol, private_port)
        port_limit = int(server.port_limit if server.port_limit is not None else (server.plan.port_count if server.plan else 0) or 0)
        if len(server.ports) >= port_limit:
            raise HTTPException(409, f"该实例最多允许 {port_limit} 个自定义 NAT 端口")
        public_port = _allocate_public_port(db, protocol, server)
        try:
            device_name = _provider().add_port(server.provider_instance_id, public_port, private_port, protocol)
            mapping = PortMapping(
                server_id=server.id, public_port=public_port, private_port=private_port, protocol=protocol, device_name=device_name
            )
            db.add(mapping)
            db.flush()
            write_audit(
                db, actor=user, request=request, action="server.port.add.mobile", target_type="server", target_id=server.id, target_name=server.name,
                detail={"protocol": protocol, "public_port": public_port, "private_port": private_port},
            )
            db.commit()
            return {
                "ok": True,
                "mapping": {"id": mapping.id, "public_port": public_port, "private_port": private_port, "protocol": protocol},
            }
        except HTTPException:
            db.rollback()
            raise
        except Exception as exc:
            db.rollback()
            raise HTTPException(502, f"添加端口失败：{str(exc)[:180]}")


@router.delete("/servers/{server_id}/ports/{mapping_id}")
def api_delete_port_mapping(request: Request, server_id: int, mapping_id: int):
    with SessionLocal() as db:
        user, _ = _mobile_session(db, request)
        server = _server_for_user(db, user, server_id)
        mapping = db.get(PortMapping, mapping_id)
        if not mapping or mapping.server_id != server.id:
            raise HTTPException(404, "端口映射不存在")
        if not server.provider_instance_id:
            raise HTTPException(409, "服务器尚未完成开通")
        detail = {"protocol": mapping.protocol, "public_port": mapping.public_port, "private_port": mapping.private_port}
        try:
            _provider().remove_port(server.provider_instance_id, mapping.device_name)
            db.delete(mapping)
            write_audit(
                db, actor=user, request=request, action="server.port.delete.mobile", target_type="server", target_id=server.id, target_name=server.name, detail=detail
            )
            db.commit()
            return {"ok": True}
        except Exception as exc:
            db.rollback()
            raise HTTPException(502, f"删除端口失败：{str(exc)[:180]}")


@router.post("/servers/{server_id}/reinstall")
async def api_reinstall_server(request: Request, server_id: int):
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(400, "请求格式错误")
    try:
        os_image_id = int(data.get("os_image_id"))
    except Exception:
        raise HTTPException(400, "请选择系统镜像")
    confirm_name = str(_json_body(data, "confirm_name", ""))

    with SessionLocal() as db:
        user, _ = _mobile_session(db, request)
        server = _server_for_user(db, user, server_id)
        blocked = _lifecycle_operation_block(db, server)
        if blocked:
            raise HTTPException(409, blocked)
        if confirm_name != server.name:
            raise HTTPException(400, "重装确认名称不正确")
        system_image = db.get(SystemImage, os_image_id)
        if not system_image or not system_image.is_active or system_image.family != "apt":
            raise HTTPException(409, "所选系统镜像不可用")
        active_job = db.scalar(
            select(Job).where(
                Job.server_id == server.id,
                Job.status.in_(["pending", "running"]),
                Job.job_type.in_(["reinstall_server", "delete_server"]),
            )
        )
        if active_job:
            raise HTTPException(409, f"服务器已有任务 #{active_job.id} 正在处理")
        job = enqueue_job(db, "reinstall_server", user_id=user.id, server_id=server.id, payload={"os_image_id": system_image.id})
        write_audit(
            db, actor=user, request=request, action="server.reinstall.enqueue.mobile", target_type="server", target_id=server.id, target_name=server.name,
            detail={"image": system_image.name, "job_id": job.id},
        )
        db.commit()
        return {"ok": True, "job_id": job.id, "status": "reinstalling", "image": system_image.name}


@router.get("/catalog")
def api_catalog(request: Request):
    with SessionLocal() as db:
        user, _ = _mobile_session(db, request)
        plans = db.scalars(
            select(Plan).where(Plan.is_active.is_(True)).order_by(Plan.sort_order, Plan.monthly_price_cents, Plan.id)
        ).all()
        images = db.scalars(
            select(SystemImage).where(SystemImage.is_active.is_(True), SystemImage.family == "apt").order_by(SystemImage.sort_order, SystemImage.id)
        ).all()
        payload = {
            "balance_cents": int(user.balance_cents or 0),
            "plans": [_plan_payload(db, plan) for plan in plans],
            "system_images": [
                {"id": image.id, "name": image.name, "alias": image.alias, "family": image.family}
                for image in images
            ],
        }
        db.commit()
        return payload


@router.post("/purchase/quote")
async def api_purchase_quote(request: Request):
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(400, "请求格式错误")
    try:
        plan_id = int(data.get("plan_id"))
    except Exception:
        raise HTTPException(400, "请选择套餐")
    coupon_code = str(_json_body(data, "coupon_code", ""))[:64]
    with SessionLocal() as db:
        user, _ = _mobile_session(db, request)
        plan = db.get(Plan, plan_id)
        if not plan or not plan.is_active:
            raise HTTPException(404, "套餐不存在或已下架")
        stock = _plan_stock(db, plan)
        if stock["sold_out"]:
            raise HTTPException(409, "该套餐已经售罄")
        try:
            coupon, discount = _calculate_coupon_discount(db, user, coupon_code, int(plan.monthly_price_cents or 0))
        except ValueError as exc:
            raise HTTPException(409, str(exc))
        final_price = max(0, int(plan.monthly_price_cents or 0) - int(discount or 0))
        db.commit()
        return {
            "plan": _plan_payload(db, plan),
            "list_price_cents": int(plan.monthly_price_cents or 0),
            "discount_cents": int(discount or 0),
            "final_price_cents": final_price,
            "balance_cents": int(user.balance_cents or 0),
            "sufficient_balance": int(user.balance_cents or 0) >= final_price,
            "coupon": None if not coupon else {"code": coupon.code, "discount_type": coupon.discount_type, "discount_value": int(coupon.discount_value or 0)},
        }


@router.post("/purchase")
async def api_purchase(request: Request):
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(400, "请求格式错误")
    try:
        plan_id = int(data.get("plan_id"))
        os_image_id = int(data.get("os_image_id"))
    except Exception:
        raise HTTPException(400, "请选择套餐和系统镜像")
    coupon_code = str(_json_body(data, "coupon_code", ""))[:64]
    request_id = str(_json_body(data, "request_id", ""))[:80]
    with SessionLocal() as db:
        user, _ = _mobile_session(db, request)
        replay = _purchase_replay(db, user.id, request_id) if request_id else None
        if replay:
            order, server, job = replay
            db.commit()
            return {
                "ok": True, "replayed": True,
                "order": {"id": order.id, "amount_cents": int(order.amount_cents or 0), "discount_cents": int(order.discount_cents or 0), "status": order.status},
                "server": {"id": server.id, "name": server.name, "status": server.status, "os_name": server.os_name},
                "job": {"id": job.id, "status": job.status, "job_type": job.job_type},
                "balance_after_cents": int(user.balance_cents or 0),
            }
        plan = db.get(Plan, plan_id)
        if not plan or not plan.is_active:
            raise HTTPException(404, "套餐不存在或已下架")
        if _plan_stock(db, plan)["sold_out"]:
            raise HTTPException(409, "该套餐已经售罄")
        system_image = db.get(SystemImage, os_image_id)
        if not system_image or not system_image.is_active or system_image.family != "apt":
            raise HTTPException(409, "系统镜像不存在、已停用或暂不支持")
        try:
            coupon, discount = _calculate_coupon_discount(db, user, coupon_code, int(plan.monthly_price_cents or 0))
        except ValueError as exc:
            raise HTTPException(409, str(exc))
        final_price = max(0, int(plan.monthly_price_cents or 0) - int(discount or 0))
        if int(user.balance_cents or 0) < final_price:
            raise HTTPException(409, f"余额不足，本次需支付 {_money_text(final_price)}")
        try:
            order, server, job = _queue_purchase_service(
                db, user, plan, system_image, final_price=final_price, coupon=coupon, discount_cents=discount, client_request_id=request_id
            )
            if final_price:
                _change_balance(
                    db, user, -final_price, kind="purchase", reference_type="order", reference_id=order.id, note=f"购买 {plan.name}"
                )
            write_audit(
                db,
                actor=user,
                request=request,
                action="server.purchase.mobile",
                target_type="server",
                target_id=server.id,
                target_name=server.name,
                detail={"plan": plan.name, "image": system_image.name, "job_id": job.id, "amount_cents": final_price},
            )
            db.commit()
            return {
                "ok": True,
                "order": {"id": order.id, "amount_cents": final_price, "discount_cents": int(discount or 0), "status": order.status},
                "server": {"id": server.id, "name": server.name, "status": server.status, "os_name": server.os_name},
                "job": {"id": job.id, "status": job.status, "job_type": job.job_type},
                "balance_after_cents": int(user.balance_cents or 0),
            }
        except HTTPException:
            db.rollback()
            raise
        except Exception as exc:
            db.rollback()
            raise HTTPException(409, f"创建开通任务失败：{str(exc)[:180]}")


@router.get("/billing")
def api_billing(request: Request):
    with SessionLocal() as db:
        user, _ = _mobile_session(db, request)

        orders = db.scalars(
            select(Order).where(Order.user_id == user.id).order_by(Order.id.desc()).limit(50)
        ).all()
        ledger = db.scalars(
            select(BalanceLedger).where(BalanceLedger.user_id == user.id).order_by(BalanceLedger.id.desc()).limit(50)
        ).all()
        recharges = db.scalars(
            select(RechargeOrder).where(RechargeOrder.user_id == user.id).order_by(RechargeOrder.id.desc()).limit(30)
        ).all()
        total_spend = db.scalar(
            select(func.coalesce(func.sum(Order.amount_cents), 0)).where(
                Order.user_id == user.id,
                Order.status == "completed",
            )
        ) or 0
        order_count = db.scalar(select(func.count()).select_from(Order).where(Order.user_id == user.id)) or 0
        ledger_count = db.scalar(select(func.count()).select_from(BalanceLedger).where(BalanceLedger.user_id == user.id)) or 0
        recharge_count = db.scalar(select(func.count()).select_from(RechargeOrder).where(RechargeOrder.user_id == user.id)) or 0

        payload = {
            "summary": {
                "balance_cents": int(user.balance_cents or 0),
                "total_spend_cents": int(total_spend or 0),
                "order_count": int(order_count),
                "ledger_count": int(ledger_count),
                "recharge_count": int(recharge_count),
            },
            "orders": [
                {
                    "id": row.id,
                    "plan_name": getattr(getattr(row, "plan", None), "name", None),
                    "amount_cents": int(row.amount_cents or 0),
                    "discount_cents": int(row.discount_cents or 0),
                    "status": row.status,
                    "kind": row.kind,
                    "server_id": row.server_id,
                    "coupon_code": row.coupon_code,
                    "created_at": row.created_at.isoformat() + "Z" if row.created_at else None,
                }
                for row in orders
            ],
            "ledger": [
                {
                    "id": row.id,
                    "delta_cents": int(row.delta_cents or 0),
                    "balance_after_cents": int(row.balance_after_cents or 0),
                    "kind": row.kind,
                    "reference_type": row.reference_type,
                    "reference_id": row.reference_id,
                    "note": row.note,
                    "created_at": row.created_at.isoformat() + "Z" if row.created_at else None,
                }
                for row in ledger
            ],
            "recharges": [
                {
                    "id": row.id,
                    "chain": row.chain,
                    "requested_cny_cents": int(row.requested_cny_cents or 0),
                    "expected_usdt_units": int(row.expected_usdt_units or 0),
                    "status": row.status,
                    "tx_hash": row.tx_hash,
                    "confirmations": int(row.confirmations or 0),
                    "expires_at": row.expires_at.isoformat() + "Z" if row.expires_at else None,
                    "paid_at": row.paid_at.isoformat() + "Z" if row.paid_at else None,
                    "created_at": row.created_at.isoformat() + "Z" if row.created_at else None,
                }
                for row in recharges
            ],
        }
        db.commit()
        return payload



def _ticket_status_label(value: str | None) -> str:
    return {
        "open": "待处理",
        "customer_reply": "待处理",
        "answered": "已回复",
        "closed": "已关闭",
    }.get((value or "").strip().lower(), value or "未知")


def _ticket_priority_label(value: str | None) -> str:
    return {"low": "低", "normal": "普通", "high": "高"}.get((value or "").strip().lower(), value or "普通")


def _ticket_payload(ticket: Ticket) -> dict:
    return {
        "id": ticket.id,
        "subject": ticket.subject,
        "status": ticket.status,
        "status_label": _ticket_status_label(ticket.status),
        "priority": ticket.priority,
        "priority_label": _ticket_priority_label(ticket.priority),
        "created_at": ticket.created_at.isoformat() + "Z" if ticket.created_at else None,
        "updated_at": ticket.updated_at.isoformat() + "Z" if ticket.updated_at else None,
        "closed_at": ticket.closed_at.isoformat() + "Z" if ticket.closed_at else None,
    }


def _ticket_message_payload(message: TicketMessage) -> dict:
    return {
        "id": message.id,
        "body": message.body,
        "author_is_admin": bool(message.author_is_admin),
        "author_name": "XNAT 支持" if message.author_is_admin else (message.author.username if message.author else "用户"),
        "created_at": message.created_at.isoformat() + "Z" if message.created_at else None,
    }


@router.get("/tickets")
def api_tickets(request: Request):
    with SessionLocal() as db:
        user, _ = _mobile_session(db, request)
        rows = db.scalars(
            select(Ticket).where(Ticket.user_id == user.id).order_by(Ticket.updated_at.desc(), Ticket.id.desc())
        ).all()
        open_count = sum(1 for ticket in rows if ticket.status != "closed")
        return {
            "items": [_ticket_payload(ticket) for ticket in rows],
            "count": len(rows),
            "open_count": open_count,
        }


@router.post("/tickets")
async def api_ticket_create(request: Request):
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(400, "请求格式错误")
    subject = str(_json_body(data, "subject", ""))
    body = str(_json_body(data, "body", ""))
    priority = str(_json_body(data, "priority", "normal")).lower()
    if not subject or not body or len(subject) > 160 or len(body) > 10000:
        raise HTTPException(400, "工单标题或内容无效")
    if priority not in {"low", "normal", "high"}:
        priority = "normal"

    with SessionLocal() as db:
        user, _ = _mobile_session(db, request)
        ticket = Ticket(user_id=user.id, subject=subject, priority=priority, status="open")
        db.add(ticket)
        db.flush()
        db.add(TicketMessage(ticket_id=ticket.id, author_user_id=user.id, author_is_admin=False, body=body))
        for admin_user in db.scalars(select(User).where(User.is_admin == True, User.is_active == True)).all():
            queue_notification(
                db,
                admin_user,
                title=f"新工单 #{ticket.id}",
                body=f"{user.username}: {subject}\n\n{body[:800]}",
                kind="ticket",
                severity="info",
                event_key=f"ticket-new:{ticket.id}:admin:{admin_user.id}",
            )
        write_audit(
            db,
            actor=user,
            request=request,
            action="ticket.create.api",
            target_type="ticket",
            target_id=ticket.id,
            target_name=subject,
        )
        db.commit()
        db.refresh(ticket)
        return {"ok": True, "ticket": _ticket_payload(ticket)}


@router.get("/tickets/{ticket_id}")
def api_ticket_detail(request: Request, ticket_id: int):
    with SessionLocal() as db:
        user, _ = _mobile_session(db, request)
        ticket = db.get(Ticket, ticket_id)
        if not ticket or ticket.user_id != user.id:
            raise HTTPException(404, "工单不存在")
        messages = db.scalars(
            select(TicketMessage).where(TicketMessage.ticket_id == ticket.id).order_by(TicketMessage.id)
        ).all()
        out = _ticket_payload(ticket)
        out["messages"] = [_ticket_message_payload(message) for message in messages]
        return out


@router.post("/tickets/{ticket_id}/reply")
async def api_ticket_reply(request: Request, ticket_id: int):
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(400, "请求格式错误")
    body = str(_json_body(data, "body", ""))
    if not body or len(body) > 10000:
        raise HTTPException(400, "回复内容不能为空")

    with SessionLocal() as db:
        user, _ = _mobile_session(db, request)
        ticket = db.get(Ticket, ticket_id)
        if not ticket or ticket.user_id != user.id:
            raise HTTPException(404, "工单不存在")
        if ticket.status == "closed":
            raise HTTPException(409, "已关闭的工单不能继续回复")
        db.add(TicketMessage(ticket_id=ticket.id, author_user_id=user.id, author_is_admin=False, body=body))
        for admin_user in db.scalars(select(User).where(User.is_admin == True, User.is_active == True)).all():
            queue_notification(
                db,
                admin_user,
                title=f"工单 #{ticket.id} 用户回复",
                body=f"{user.username}: {ticket.subject}\n\n{body[:800]}",
                kind="ticket",
                severity="info",
                event_key=f"ticket-user-reply:{ticket.id}:{int(datetime.utcnow().timestamp())}:admin:{admin_user.id}",
            )
        ticket.status = "customer_reply"
        ticket.updated_at = datetime.utcnow()
        write_audit(
            db,
            actor=user,
            request=request,
            action="ticket.reply.api",
            target_type="ticket",
            target_id=ticket.id,
            target_name=ticket.subject,
        )
        db.commit()
        return {"ok": True, "ticket": _ticket_payload(ticket)}


@router.post("/tickets/{ticket_id}/close")
def api_ticket_close(request: Request, ticket_id: int):
    with SessionLocal() as db:
        user, _ = _mobile_session(db, request)
        ticket = db.get(Ticket, ticket_id)
        if not ticket or ticket.user_id != user.id:
            raise HTTPException(404, "工单不存在")
        if ticket.status != "closed":
            ticket.status = "closed"
            ticket.closed_at = datetime.utcnow()
            ticket.updated_at = datetime.utcnow()
            write_audit(
                db,
                actor=user,
                request=request,
                action="ticket.close.api",
                target_type="ticket",
                target_id=ticket.id,
                target_name=ticket.subject,
            )
            db.commit()
        return {"ok": True, "ticket": _ticket_payload(ticket)}
