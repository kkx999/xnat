import os
import re
import asyncio
import contextlib
import json
import secrets
import hashlib
import socket
from urllib.parse import quote_plus
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from decimal import Decimal, InvalidOperation
from pathlib import Path

import psutil
from urllib.parse import urlparse

from fastapi import FastAPI, File, Form, HTTPException, Request, Query, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import delete, func, or_, select, text
from starlette.middleware.sessions import SessionMiddleware

from .auth import (
    admin_required, current_user, ensure_csrf, hash_password, login_required,
    validate_csrf, validate_username, verify_password,
)
from .crypto import decrypt_secret, encrypt_secret
from .audit import write_audit
from .backups import BACKUP_DIR, create_backup, create_scheduled_backup_if_due, list_backups, restore_backup, store_uploaded_backup, validate_backup_file
from .jobs import enqueue_job, process_jobs
from .notifications import process_pending_notifications, queue_notification, queue_admin_notification, send_email_address, send_telegram_chat
from .runtime_config import notification_runtime_config, runtime_plain, runtime_secret
from .payments import (POLYGON_USDT0_CONTRACT, TRON_USDT_CONTRACT, create_recharge_order, manual_credit_order, normalize_tx_hash, payment_config, poll_pending_payments, rate_text, repair_credit_order, force_credit_order_without_tx, test_polygon_configuration, test_tron_configuration, usdt_units_to_text, valid_evm_address, valid_tron_address, verify_recharge_transaction)
from .reconcile import reconcile_all, reconcile_server as reconcile_one_server
from .security import (client_ip, consume_password_reset_token, create_login_session, create_password_reset_token, get_login_session, login_block_remaining_seconds, new_totp_secret, record_login_event, revoke_current_session, revoke_other_sessions, totp_uri, verify_totp)
from .db import Base, SessionLocal, engine
from .schema import ensure_schema_extensions
from .lifecycle import cancel_pending_expiry_delete, lifecycle_config, lifecycle_state, resume_after_renewal, run_expiry_lifecycle
from .deployment import deployment_status
from .models import (
    Announcement, AnnouncementRead, AuditLog, BalanceLedger, ChainTransaction, Coupon, CouponRedemption, HostNode, PlanHost, Job, LoginEvent, LoginSession,
    Notification, Order, PasswordResetToken, Plan, PortMapping, RechargeOrder, Server, SiteSetting,
    SystemImage, Ticket, TicketMessage, User,
)
from .traffic import (
    THROTTLE_MBPS, apply_sample, collect_all as collect_traffic_all, configured_bandwidth_mbps,
    effective_bandwidth_mbps, enforce_traffic_policy, ensure_cycle, reset_cycle, traffic_bonus_gb,
    traffic_cycle_mode_label, traffic_level, traffic_percent, traffic_quota_bytes, traffic_quota_gb, traffic_raw_percent,
    traffic_remaining_bytes, traffic_status_label, traffic_used_bytes,
)
from .providers.incus import IncusProvider
from .providers.remote import RemoteHostProvider
from .providers.mock import MockProvider
from .nodes import (
    HostAPIError, allocate_host_port, host_request, host_summary, host_port_pool_stats, host_schedule_state,
    refresh_all_hosts, refresh_host, select_host_for_plan,
)

APP_NAME = os.getenv("APP_NAME", "XNAT")
APP_SECRET = os.getenv("APP_SECRET", "dev-only-change-me")
SESSION_HTTPS_ONLY = os.getenv("SESSION_HTTPS_ONLY", "false").lower() == "true"
PROVIDER_NAME = os.getenv("VPS_PROVIDER", "mock").strip().lower()
APP_TIMEZONE = os.getenv("APP_TIMEZONE", "Asia/Shanghai")

try:
    APP_TZ = ZoneInfo(APP_TIMEZONE)
except Exception:
    APP_TZ = timezone.utc

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

def get_provider():
    if PROVIDER_NAME == "remote":
        return RemoteHostProvider()
    return IncusProvider() if PROVIDER_NAME == "incus" else MockProvider()

provider = get_provider()

def money(cents: int) -> str:
    return f"¥{cents / 100:.2f}"

def local_dt(value):
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(APP_TZ)

def human_bytes(value: int) -> str:
    value = max(0, int(value or 0))
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(value)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{value} B"


def traffic_cycle_label(server: Server) -> str:
    if server.traffic_cycle_end is None:
        return "-"
    return local_dt(server.traffic_cycle_end).strftime("%Y-%m-%d %H:%M")


def server_status_label(value: str | None) -> str:
    return {
        "running": "运行中",
        "stopped": "已关机",
        "provisioning": "开通中",
        "reinstalling": "重装中",
        "deleting": "删除中",
        "provision_failed": "开通失败",
        "deleted": "已删除",
    }.get((value or "").lower(), value or "未知")


def server_ui_status_map(db, servers) -> dict[int, str]:
    """Return user-facing transient server states without mutating allocation state.

    A reinstall/delete is queued and processed asynchronously. The persisted
    Server.status intentionally continues to describe the last confirmed
    provider state so capacity/accounting code is not affected. For client UI,
    however, an active lifecycle job should take precedence until it finishes.
    """
    rows = list(servers or [])
    result = {s.id: (s.status or "unknown") for s in rows}
    server_ids = [s.id for s in rows if getattr(s, "id", None)]
    if not server_ids:
        return result

    jobs = db.scalars(
        select(Job).where(
            Job.server_id.in_(server_ids),
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


def order_status_label(value: str | None) -> str:
    return {
        "pending": "待处理",
        "paid": "已支付",
        "completed": "已完成",
        "failed": "失败",
        "cancelled": "已取消",
        "refunded": "已退款",
        "cancelled": "已取消",
    }.get((value or "").lower(), value or "未知")


def order_kind_label(value: str | None) -> str:
    return {
        "purchase": "新购",
        "renew": "续费",
        "renewal": "续费",
        "admin": "管理员开通",
        "admin_provision": "管理员开通",
        "traffic_reset": "流量重置",
    }.get((value or "purchase").lower(), value or "新购")


def recharge_status_label(value: str | None) -> str:
    return {
        "pending": "等待支付",
        "detected": "已检测，等待确认",
        "paid": "已到账",
        "expired": "已过期",
        "manual": "人工处理",
    }.get((value or "pending").lower(), value or "未知")


def job_status_label(value: str | None) -> str:
    return {
        "pending": "等待执行",
        "running": "执行中",
        "completed": "已完成",
        "failed": "失败",
    }.get((value or "pending").lower(), value or "未知")


def ticket_status_label(value: str | None) -> str:
    return {
        "open": "待处理",
        "answered": "已回复",
        "customer_reply": "用户已回复",
        "closed": "已关闭",
    }.get((value or "open").lower(), value or "未知")


def change_balance(db, user: User, delta_cents: int, *, kind: str, reference_type: str | None = None, reference_id: int | None = None, note: str | None = None):
    user.balance_cents = int(user.balance_cents or 0) + int(delta_cents)
    db.flush()
    db.add(BalanceLedger(
        user_id=user.id,
        delta_cents=int(delta_cents),
        balance_after_cents=user.balance_cents,
        kind=kind,
        reference_type=reference_type,
        reference_id=reference_id,
        note=note,
    ))
    return user.balance_cents


def parse_port_spec(value: str) -> set[int]:
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
                for p in range(max(1, a), min(65535, b) + 1):
                    ports.add(p)
        elif part.isdigit():
            p = int(part)
            if 1 <= p <= 65535:
                ports.add(p)
    return ports


def validate_port_policy(db, protocol: str, private_port: int):
    protocol = protocol.lower()
    if protocol == "tcp" and not setting_enabled(db, "port_tcp_enabled", True):
        raise ValueError("当前暂停新增 TCP 端口")
    if protocol == "udp" and not setting_enabled(db, "port_udp_enabled", True):
        raise ValueError("当前暂停新增 UDP 端口")
    blocked = parse_port_spec(get_setting(db, "port_blocked_private", ""))
    if private_port in blocked:
        raise ValueError(f"内部端口 {private_port} 已被管理员禁止映射")


def get_setting(db, key: str, default: str = "") -> str:
    row = db.get(SiteSetting, key)
    return row.value if row else default

def setting_enabled(db, key: str, default: bool = False) -> bool:
    fallback = "true" if default else "false"
    return get_setting(db, key, fallback).strip().lower() in {"1", "true", "yes", "on"}

def set_setting(db, key: str, value: str):
    row = db.get(SiteSetting, key)
    if row:
        row.value = value
    else:
        db.add(SiteSetting(key=key, value=value))


HOME_SETTING_DEFAULTS = {
    "home_eyebrow": "NAT VPS · SIMPLE CLOUD INFRASTRUCTURE",
    "home_title": "简单、稳定、清晰的",
    "home_highlight": "NAT VPS",
    "home_description": "从购买、自动开通，到 NAT 端口、系统重装、流量管理和在线续费，把一台轻量 VPS 真正需要的能力放进一个清楚的控制面板。",
    "home_guest_primary_label": "查看套餐",
    "home_guest_secondary_label": "登录控制台",
    "home_user_primary_label": "进入控制台",
    "home_user_secondary_label": "查看套餐",
    "home_feature_tags": "自动开通 | 多系统镜像 | NAT 端口管理 | 流量统计 | 在线续费",
    "home_overview_title": "轻量，但不简陋。",
    "home_overview_description": "面向个人项目、开发测试和轻量业务，把实例管理、网络、账务和支持集中在同一个入口。",
    "home_capability_1_title": "自动开通",
    "home_capability_1_description": "购买完成后进入任务队列自动部署",
    "home_capability_2_title": "NAT 端口",
    "home_capability_2_description": "公网端口映射与端口策略集中管理",
    "home_capability_3_title": "周期记账",
    "home_capability_3_description": "RX / TX、套餐总量与超额限速",
    "home_capability_4_title": "在线重装",
    "home_capability_4_description": "支持多个系统镜像快速切换",
    "home_signal_1_title": "统一控制台",
    "home_signal_1_description": "服务器、订单、充值与支持",
    "home_signal_2_title": "套餐独立配额",
    "home_signal_2_description": "CPU、内存、磁盘、带宽与流量",
    "home_signal_3_title": "独立实例隔离",
    "home_signal_3_description": "每台 VPS 独立管理与权限校验",
    "home_signal_4_title": "余额与 USDT",
    "home_signal_4_description": "购买、续费与充值记录统一管理",
    "home_plans_title": "在售套餐",
    "home_plans_description": "从轻量入门到更充足的资源配置，按实际需求选择。",
    "home_bottom_title": "少一点复杂，多一点清楚。",
    "home_bottom_description": "购买、部署、管理、流量、续费和支持保持在同一个逻辑里。不需要在多个页面之间反复寻找关键操作。",
}


def homepage_content(db):
    data = {
        key: get_setting(db, key, default)
        for key, default in HOME_SETTING_DEFAULTS.items()
    }
    data["feature_tags"] = [
        item.strip()
        for item in str(data.get("home_feature_tags") or "").split("|")
        if item.strip()
    ][:8]
    return data


def parse_local_datetime(value: str):
    value = (value or "").strip()
    if not value:
        return None
    local = datetime.fromisoformat(value)
    if local.tzinfo is None:
        local = local.replace(tzinfo=APP_TZ)
    return local.astimezone(timezone.utc).replace(tzinfo=None)

def calculate_coupon_discount(db, user: User, coupon_code: str, price_cents: int):
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

    if price_cents < coupon.min_order_cents:
        raise ValueError(f"该优惠码最低消费为 {money(coupon.min_order_cents)}")

    used = db.scalar(
        select(CouponRedemption).where(
            CouponRedemption.coupon_id == coupon.id,
            CouponRedemption.user_id == user.id,
        )
    )
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

    discount = max(0, min(price_cents, int(discount)))
    return coupon, discount

def provision_service(db, request, user, plan, system_image, *, order_amount_cents: int, order_kind: str, coupon=None, discount_cents: int = 0):
    inventory = plan_stock(db, plan)
    if inventory["sold_out"]:
        raise ValueError("该套餐已经售罄")

    order = Order(
        user_id=user.id,
        plan_id=plan.id,
        amount_cents=order_amount_cents,
        status="paid",
        kind=order_kind,
        coupon_code=coupon.code if coupon else None,
        discount_cents=discount_cents,
    )
    db.add(order)
    db.flush()

    server = Server(
        user_id=user.id,
        plan_id=plan.id,
        order_id=order.id,
        name=f"nat-{user.id}-{order.id}",
        provider=PROVIDER_NAME,
        status="provisioning",
        public_ip=getattr(provider, "public_host", os.getenv("INCUS_PUBLIC_HOST", "203.0.113.10")),
        os_image_id=system_image.id,
        os_name=system_image.name,
        os_alias=system_image.alias,
        cpu=plan.cpu,
        memory_mb=plan.memory_mb,
        disk_gb=plan.disk_gb,
        port_limit=plan.port_count,
        bandwidth_mbps=plan.bandwidth_mbps,
        traffic_gb=plan.traffic_gb,
        traffic_cycle_mode=get_setting(db, "traffic_cycle_default_mode", "rolling30"),
        traffic_cycle_day=max(1, min(28, int(get_setting(db, "traffic_cycle_default_day", "1") or 1))),
        monthly_price_cents=plan.monthly_price_cents,
        virtualization_type=(plan.virtualization_type or "lxc"),
        expires_at=datetime.utcnow() + timedelta(days=30),
    )
    db.add(server)
    db.flush()
    order.server_id = server.id

    ssh_port = allocate_public_port(db, "tcp")
    result = provider.provision(
        server.id,
        server.name,
        system_image.alias,
        server.memory_mb,
        server.disk_gb,
        server.cpu,
        server.bandwidth_mbps or 0,
        ssh_port,
        server.virtualization_type or "lxc",
    )

    server.provider_instance_id = result.instance_id
    server.private_ip = result.private_ip
    server.ssh_port = result.ssh_port
    server.status = result.status
    order.status = "completed"

    traffic_now = datetime.utcnow()
    ensure_cycle(server, traffic_now)
    try:
        initial_stats = provider.network_stats(server.provider_instance_id)
        apply_sample(server, initial_stats, traffic_now, seed_first_sample=True)
    except Exception:
        pass

    if coupon:
        coupon.used_count += 1
        db.add(CouponRedemption(
            coupon_id=coupon.id,
            user_id=user.id,
            order_id=order.id,
        ))

    if result.root_password:
        server.root_password_enc = encrypt_secret(result.root_password)
        one_time_secret(
            request,
            f"{server.name} root 初始密码（仅本次显示）",
            result.root_password,
        )

    return order, server


def queue_service_provision(db, user, plan, system_image, *, order_amount_cents: int, order_kind: str, coupon=None, discount_cents: int = 0):
    inventory = plan_stock(db, plan)
    if inventory["sold_out"]:
        raise ValueError("该套餐已经售罄")

    host = None
    if PROVIDER_NAME == "remote":
        host = select_host_for_plan(db, plan)

    order = Order(
        user_id=user.id, plan_id=plan.id, amount_cents=order_amount_cents,
        status="paid", kind=order_kind, coupon_code=coupon.code if coupon else None,
        discount_cents=discount_cents,
    )
    db.add(order)
    db.flush()

    ssh_port = allocate_public_port(db, "tcp", host)
    public_ip = host.public_ip if host is not None else getattr(provider, "public_host", os.getenv("INCUS_PUBLIC_HOST", "203.0.113.10"))
    server = Server(
        user_id=user.id, plan_id=plan.id, order_id=order.id, host_id=host.id if host else None,
        name=f"nat-{user.id}-{order.id}", provider=PROVIDER_NAME, status="provisioning",
        public_ip=public_ip,
        ssh_port=ssh_port, os_image_id=system_image.id, os_name=system_image.name, os_alias=system_image.alias,
        cpu=plan.cpu, memory_mb=plan.memory_mb, disk_gb=plan.disk_gb, port_limit=plan.port_count,
        bandwidth_mbps=plan.bandwidth_mbps, traffic_gb=plan.traffic_gb,
        traffic_cycle_mode=get_setting(db, "traffic_cycle_default_mode", "rolling30"),
        traffic_cycle_day=max(1, min(28, int(get_setting(db, "traffic_cycle_default_day", "1") or 1))),
        monthly_price_cents=plan.monthly_price_cents,
        virtualization_type=(plan.virtualization_type or "lxc"),
        expires_at=datetime.utcnow() + timedelta(days=30), reconcile_status="pending",
    )
    db.add(server)
    db.flush()
    order.server_id = server.id
    ensure_cycle(server, datetime.utcnow())

    if coupon:
        coupon.used_count += 1
        db.add(CouponRedemption(coupon_id=coupon.id, user_id=user.id, order_id=order.id))

    payload = {"order_id": order.id}
    if host:
        payload["host_id"] = host.id
        payload["host_name"] = host.name
    job = enqueue_job(db, "provision_server", user_id=user.id, server_id=server.id, payload=payload)
    return order, server, job


templates.env.globals["money"] = money
templates.env.globals["local_dt"] = local_dt
templates.env.globals["human_bytes"] = human_bytes
templates.env.globals["traffic_used_bytes"] = traffic_used_bytes
templates.env.globals["traffic_quota_bytes"] = traffic_quota_bytes
templates.env.globals["traffic_remaining_bytes"] = traffic_remaining_bytes
templates.env.globals["traffic_percent"] = traffic_percent
templates.env.globals["traffic_raw_percent"] = traffic_raw_percent
templates.env.globals["traffic_quota_gb"] = traffic_quota_gb
templates.env.globals["traffic_bonus_gb"] = traffic_bonus_gb
templates.env.globals["traffic_level"] = traffic_level
templates.env.globals["traffic_status_label"] = traffic_status_label
templates.env.globals["effective_bandwidth_mbps"] = effective_bandwidth_mbps
templates.env.globals["traffic_cycle_label"] = traffic_cycle_label
templates.env.globals["traffic_cycle_mode_label"] = traffic_cycle_mode_label
templates.env.globals["server_status_label"] = server_status_label
templates.env.globals["order_status_label"] = order_status_label
templates.env.globals["order_kind_label"] = order_kind_label
templates.env.globals["recharge_status_label"] = recharge_status_label
templates.env.globals["job_status_label"] = job_status_label
templates.env.globals["ticket_status_label"] = ticket_status_label
templates.env.globals["usdt_units_to_text"] = usdt_units_to_text
templates.env.globals["rate_text"] = rate_text
templates.env.globals["urlencode"] = quote_plus
templates.env.globals["decrypt_secret"] = decrypt_secret
templates.env.globals["host_summary"] = host_summary


def migrate_legacy_announcement(db) -> int | None:
    """Import the old single site announcement into the announcement center once."""
    marker = get_setting(db, "announcement_center_migrated", "false").strip().lower()
    if marker in {"1", "true", "yes", "on"}:
        return None
    legacy_text = get_setting(db, "announcement_text", "").strip()
    legacy_enabled = setting_enabled(db, "announcement_enabled", False)
    migrated_id = None
    if legacy_text:
        existing = db.scalar(select(Announcement).where(Announcement.body == legacy_text).order_by(Announcement.id.desc()))
        if existing:
            migrated_id = existing.id
        else:
            row = Announcement(title="站点公告", body=legacy_text, is_published=True, is_pinned=True,
                               show_on_login=legacy_enabled, published_at=datetime.utcnow(), updated_at=datetime.utcnow())
            db.add(row)
            db.flush()
            migrated_id = row.id

        # v1.1.0 UX3 stored a per-user SHA256 fingerprint when the old single
        # announcement had already been shown. Convert those fingerprints into
        # AnnouncementRead rows so upgrading to the announcement center does not
        # make users re-read an announcement they already acknowledged.
        legacy_key = hashlib.sha256(legacy_text.encode("utf-8")).hexdigest()
        seen_user_ids = db.scalars(select(User.id).where(User.announcement_seen_key == legacy_key)).all()
        existing_read_user_ids = set(db.scalars(select(AnnouncementRead.user_id).where(
            AnnouncementRead.announcement_id == migrated_id
        )).all())
        for user_id in seen_user_ids:
            if user_id not in existing_read_user_ids:
                db.add(AnnouncementRead(announcement_id=migrated_id, user_id=user_id))

    set_setting(db, "announcement_center_migrated", "true")
    return migrated_id


def published_announcements(db, *, limit: int = 30) -> list[Announcement]:
    return db.scalars(
        select(Announcement).where(Announcement.is_published == True)
        .order_by(Announcement.is_pinned.desc(), Announcement.published_at.desc(), Announcement.id.desc())
        .limit(limit)
    ).all()


def mark_announcement_read(db, user_id: int, announcement_id: int) -> bool:
    announcement = db.get(Announcement, announcement_id)
    if not announcement or not announcement.is_published:
        return False
    exists = db.scalar(select(AnnouncementRead).where(
        AnnouncementRead.announcement_id == announcement_id, AnnouncementRead.user_id == user_id
    ))
    if not exists:
        db.add(AnnouncementRead(announcement_id=announcement_id, user_id=user_id))
    return True


def seed():
    Path("data").mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(bind=engine)
    migrated = ensure_schema_extensions()
    if migrated:
        print("[schema] added: " + ", ".join(migrated))

    with SessionLocal() as db:

        if not (db.scalar(select(func.count()).select_from(Plan)) or 0):
            db.add_all([
                Plan(name="NAT Mini", cpu=1, memory_mb=128, disk_gb=2, port_count=5,
                     bandwidth_mbps=100, traffic_gb=200, monthly_price_cents=300, traffic_reset_price_cents=300,
                     stock_limit=0, sort_order=10, homepage_visible=True, homepage_sort_order=10,
                     is_recommended=False, recommendation_label="推荐"),
                Plan(name="NAT Basic", cpu=1, memory_mb=256, disk_gb=4, port_count=10,
                     bandwidth_mbps=100, traffic_gb=500, monthly_price_cents=500, traffic_reset_price_cents=500,
                     stock_limit=0, sort_order=20, homepage_visible=True, homepage_sort_order=20,
                     is_recommended=False, recommendation_label="推荐"),
                Plan(name="NAT Plus", cpu=1, memory_mb=512, disk_gb=8, port_count=15,
                     bandwidth_mbps=100, traffic_gb=1000, monthly_price_cents=900, traffic_reset_price_cents=900,
                     stock_limit=0, sort_order=30, homepage_visible=True, homepage_sort_order=30,
                     is_recommended=False, recommendation_label="推荐"),
            ])
            db.flush()

        if not (db.scalar(select(func.count()).select_from(SystemImage)) or 0):
            db.add_all([
                SystemImage(name="Debian 12", alias="images:debian/12", family="apt", sort_order=10),
                SystemImage(name="Debian 13", alias="images:debian/13", family="apt", sort_order=20),
                SystemImage(name="Ubuntu 22.04 LTS", alias="images:ubuntu/22.04", family="apt", sort_order=30),
                SystemImage(name="Ubuntu 24.04 LTS", alias="images:ubuntu/24.04", family="apt", sort_order=40),
            ])
            db.flush()

        default_settings = {
            **HOME_SETTING_DEFAULTS,
            "registration_enabled": "true",
            "announcement_enabled": "false",
            "announcement_text": "",
            # Payments. Runtime credentials can be configured in the admin UI.
            # .env remains a backwards-compatible fallback and bootstrap source.
            "payment_enabled": "false",
            "usdt_cny_rate": "7.20",
            "recharge_min_cny": "10",
            "recharge_max_cny": "10000",
            "payment_expire_minutes": "30",
            "payment_late_grace_hours": "24",
            "payment_tron_enabled": "true",
            "payment_tron_mode": "auto",
            "payment_tron_wallet": "",
            "payment_tron_contract": TRON_USDT_CONTRACT,
            "payment_polygon_enabled": "true",
            "payment_polygon_mode": "auto",
            "payment_polygon_wallet": "",
            "payment_polygon_rpc": os.getenv("POLYGON_RPC_URL", "https://polygon.drpc.org"),
            "payment_polygon_contract": POLYGON_USDT0_CONTRACT,
            "payment_polygon_confirmations": "20",
            # Login hardening.
            "login_max_failures": "10",
            "login_window_minutes": "15",
            "login_block_minutes": "30",
            "admin_require_2fa": "false",
            # NAT port policy.
            "port_blocked_private": "",
            "port_blocked_public": "",
            "port_tcp_enabled": "true",
            "port_udp_enabled": "true",
            # v1.1 lifecycle and operations policy. Auto-delete is OFF by default.
            "expiry_notice_days": "7,3,1",
            "expiry_grace_days": "0",
            "expiry_auto_stop_enabled": "true",
            "expiry_delete_enabled": "false",
            "expiry_delete_after_days": "7",
            "expiry_delete_notice_hours": "24",
            "expiry_renew_auto_start": "true",
            "traffic_cycle_default_mode": "rolling30",
            "traffic_cycle_default_day": "1",
            "node_nat_port_alert_percent": "10",
            # Web-managed notification/runtime configuration. Secrets are encrypted with APP_SECRET.
            "public_base_url": os.getenv("PUBLIC_BASE_URL", "").strip(),
            "smtp_host": os.getenv("SMTP_HOST", "").strip(),
            "smtp_port": os.getenv("SMTP_PORT", "587").strip() or "587",
            "smtp_username": os.getenv("SMTP_USERNAME", "").strip(),
            "smtp_password_enc": "",
            "smtp_from": os.getenv("SMTP_FROM", "").strip(),
            "smtp_starttls": os.getenv("SMTP_STARTTLS", "true").strip().lower(),
            "telegram_bot_token_enc": "",
            "trongrid_api_key_enc": "",
            # Global notification channel rules. User preferences are applied on top.
            "notify_rule_server_email": "true",
            "notify_rule_server_telegram": "true",
            "notify_rule_traffic_email": "true",
            "notify_rule_traffic_telegram": "true",
            "notify_rule_expiry_email": "true",
            "notify_rule_expiry_telegram": "true",
            "notify_rule_payment_email": "true",
            "notify_rule_payment_telegram": "true",
            "notify_rule_ticket_email": "true",
            "notify_rule_ticket_telegram": "true",
            "notify_rule_security_email": "true",
            "notify_rule_security_telegram": "true",
            "notify_rule_system_email": "true",
            "notify_rule_system_telegram": "true",
        }
        for key, value in default_settings.items():
            if not db.get(SiteSetting, key):
                db.add(SiteSetting(key=key, value=value))

        db.flush()
        migrated_announcement_id = migrate_legacy_announcement(db)
        if migrated_announcement_id:
            print(f"[announcement] migrated legacy notice to #{migrated_announcement_id}")

        admin_username = os.getenv("ADMIN_USERNAME", "admin")
        admin_password = os.getenv("ADMIN_PASSWORD", "ChangeThisAdminPassword123!")
        admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
        admin = db.scalar(select(User).where(User.username == admin_username))
        if not admin:
            db.add(User(
                username=admin_username,
                email=admin_email,
                password_hash=hash_password(admin_password),
                is_admin=True,
                balance_cents=0,
            ))

        db.commit()

def refresh_hosts_once():
    with SessionLocal() as db:
        ok, failed = refresh_all_hosts(db)
        db.commit()
        return ok, failed


async def host_monitor_loop():
    await asyncio.sleep(1)
    while True:
        if PROVIDER_NAME == "remote":
            try:
                ok, failed = await asyncio.to_thread(refresh_hosts_once)
                if failed:
                    print(f"[nodes] online={ok} failed={failed}")
            except Exception as exc:
                print(f"[nodes] monitor: {exc}")
        await asyncio.sleep(20)


async def traffic_collector_loop():
    await asyncio.sleep(3)
    while True:
        try:
            await asyncio.to_thread(collect_traffic_all, provider, PROVIDER_NAME)
        except Exception as exc:
            print(f"[traffic] collector: {exc}")
        await asyncio.sleep(60)


async def job_worker_loop():
    await asyncio.sleep(2)
    while True:
        try:
            await asyncio.to_thread(process_jobs, provider, PROVIDER_NAME, 5)
        except Exception as exc:
            print(f"[jobs] worker: {exc}")
        await asyncio.sleep(2)


async def payment_monitor_loop():
    await asyncio.sleep(6)
    while True:
        try:
            await asyncio.to_thread(poll_pending_payments)
        except Exception as exc:
            print(f"[payment] monitor: {exc}")
        await asyncio.sleep(20)


async def notification_sender_loop():
    await asyncio.sleep(8)
    while True:
        try:
            await asyncio.to_thread(process_pending_notifications, 50)
        except Exception as exc:
            print(f"[notify] sender: {exc}")
        await asyncio.sleep(15)


def expiry_maintenance_once():
    return run_expiry_lifecycle(provider, PROVIDER_NAME)


def notify_backup_failure(message: str):
    with SessionLocal() as db:
        now = datetime.utcnow()
        queue_admin_notification(
            db,
            title="数据库自动备份失败",
            body=f"XNAT 定时数据库备份失败：{message[:300]}",
            kind="system",
            severity="error",
            event_key=f"backup-failed:{now:%Y%m%d%H}",
        )
        db.commit()


async def housekeeping_loop():
    await asyncio.sleep(12)
    counter = 0
    while True:
        try:
            await asyncio.to_thread(expiry_maintenance_once)
            if counter % 2 == 0:
                await asyncio.to_thread(reconcile_all, provider, PROVIDER_NAME, repair=True)
            if counter % 12 == 0:
                try:
                    await asyncio.to_thread(create_scheduled_backup_if_due)
                except Exception as exc:
                    print(f"[backup] scheduled: {exc}")
                    await asyncio.to_thread(notify_backup_failure, str(exc))
            counter += 1
        except Exception as exc:
            print(f"[housekeeping] {exc}")
        await asyncio.sleep(300)


@asynccontextmanager
async def lifespan(app: FastAPI):
    seed()
    tasks = [
        asyncio.create_task(host_monitor_loop()),
        asyncio.create_task(traffic_collector_loop()),
        asyncio.create_task(job_worker_loop()),
        asyncio.create_task(payment_monitor_loop()),
        asyncio.create_task(notification_sender_loop()),
        asyncio.create_task(housekeeping_loop()),
    ]
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task

app = FastAPI(title=APP_NAME, lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=APP_SECRET,
    https_only=SESSION_HTTPS_ONLY,
    same_site="lax",
    max_age=60 * 60 * 24 * 14,
)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

def db_session():
    return SessionLocal()

def flash(request: Request, message: str, kind: str = "info"):
    request.session["flash"] = {"message": message, "kind": kind}

def one_time_secret(request: Request, title: str, value: str):
    request.session["one_time_secret"] = {"title": title, "value": value}

def render(request: Request, template_name: str, db, **context):
    user = current_user(request, db)
    flash_message = request.session.pop("flash", None)
    secret_message = request.session.pop("one_time_secret", None)
    registration_enabled = setting_enabled(db, "registration_enabled", True)
    unread_notifications = 0
    if user:
        unread_notifications = db.scalar(
            select(func.count()).select_from(Notification).where(
                Notification.user_id == user.id, Notification.read_at.is_(None)
            )
        ) or 0

    announcement_rows = []
    announcement_read_ids: set[int] = set()
    announcement_unread_count = 0
    login_announcement = None
    if user:
        announcement_rows = published_announcements(db)
        if announcement_rows:
            announcement_ids = [row.id for row in announcement_rows]
            announcement_read_ids = set(db.scalars(select(AnnouncementRead.announcement_id).where(
                AnnouncementRead.user_id == user.id, AnnouncementRead.announcement_id.in_(announcement_ids)
            )).all())
            announcement_unread_count = sum(1 for row in announcement_rows if row.id not in announcement_read_ids)
        if request.session.pop("show_login_announcement", False):
            login_announcement = next((row for row in announcement_rows if row.show_on_login and row.id not in announcement_read_ids), None)

    return templates.TemplateResponse(
        request=request,
        name=template_name,
        context={
            "app_name": APP_NAME,
            "user": user,
            "csrf_token": ensure_csrf(request),
            "flash": flash_message,
            "one_time_secret": secret_message,
            "provider_name": PROVIDER_NAME,
            "login_announcement": login_announcement,
            "announcement_rows": announcement_rows,
            "announcement_read_ids": announcement_read_ids,
            "announcement_unread_count": announcement_unread_count,
            "registration_enabled": registration_enabled,
            "unread_notifications": unread_notifications,
            **context,
        },
    )

def active_service_count(db, plan_id: int) -> int:
    return db.scalar(
        select(func.count()).select_from(Server).where(
            Server.plan_id == plan_id,
            Server.deleted_at.is_(None),
            Server.status.in_(["provisioning", "running", "stopped"]),
        )
    ) or 0

def plan_stock(db, plan: Plan) -> dict:
    used = active_service_count(db, plan.id)
    if not plan.stock_limit or plan.stock_limit <= 0:
        return {"used": used, "limit": 0, "available": None, "sold_out": False}
    available = max(plan.stock_limit - used, 0)
    return {
        "used": used,
        "limit": plan.stock_limit,
        "available": available,
        "sold_out": available <= 0,
    }

def public_port_in_use(db, port: int, protocol: str, host: HostNode | None = None) -> bool:
    if host is not None:
        from .nodes import public_port_in_use_on_host
        return public_port_in_use_on_host(db, host.id, port, protocol)
    if db.scalar(select(PortMapping).where(PortMapping.public_port == port, PortMapping.protocol == protocol)):
        return True
    if db.scalar(select(Server).where(Server.ssh_port == port, Server.deleted_at.is_(None))):
        return True

    socktype = socket.SOCK_STREAM if protocol == "tcp" else socket.SOCK_DGRAM
    with socket.socket(socket.AF_INET, socktype) as s:
        try:
            s.bind(("0.0.0.0", port))
            return False
        except OSError:
            return True

def allocate_public_port(db, protocol: str, host: HostNode | None = None) -> int:
    blocked = parse_port_spec(get_setting(db, "port_blocked_public", ""))
    if host is not None:
        return allocate_host_port(db, host, protocol, blocked)
    start = int(get_setting(db, "port_public_start", os.getenv("INCUS_PORT_START", "20000")) or os.getenv("INCUS_PORT_START", "20000"))
    end = int(get_setting(db, "port_public_end", os.getenv("INCUS_PORT_END", "29999")) or os.getenv("INCUS_PORT_END", "29999"))
    if start > end:
        start, end = end, start
    start = max(1024, start)
    end = min(65535, end)
    for port in range(start, end + 1):
        if port in blocked:
            continue
        if not public_port_in_use(db, port, protocol):
            return port
    raise HTTPException(409, "公网端口池已经耗尽")

def active_server_for_user(db, user, server_id: int):
    server = db.get(Server, server_id)
    if not server or server.user_id != user.id or server.deleted_at is not None:
        raise HTTPException(404, "服务器不存在")
    return server


def lifecycle_operation_block(db, server: Server) -> str | None:
    """Block service-changing operations after the expiry grace period.

    Stop/delete/renew remain handled by their dedicated routes. This helper is
    for operations that would otherwise mutate or revive an expired service.
    """
    state = lifecycle_state(server, lifecycle_config(db), datetime.utcnow())
    if state["code"] in {"expired", "suspended", "delete_queued"}:
        return "服务器已超过到期宽限期，请续费后再执行此操作。"
    return None

def parse_plan_form(
    name: str,
    cpu: int,
    memory_mb: int,
    disk_gb: int,
    bandwidth_mbps: int,
    traffic_gb: int,
    port_count: int,
    monthly_price: str,
    stock_limit: int,
    sort_order: int,
):
    name = name.strip()
    if not name or len(name) > 80:
        raise ValueError("套餐名称长度无效")
    if cpu < 1 or cpu > 128:
        raise ValueError("CPU 必须在 1-128 之间")
    if memory_mb < 64 or memory_mb > 1048576:
        raise ValueError("内存必须在 64-1048576 MB 之间")
    if disk_gb < 1 or disk_gb > 65536:
        raise ValueError("磁盘必须在 1-65536 GB 之间")
    if bandwidth_mbps < 0 or bandwidth_mbps > 10000:
        raise ValueError("带宽必须在 0-10000 Mbps 之间")
    if traffic_gb < 0 or traffic_gb > 100000000:
        raise ValueError("流量参数无效")
    if port_count < 0 or port_count > 10000:
        raise ValueError("端口数必须在 0-10000 之间")
    if stock_limit < 0 or stock_limit > 1000000:
        raise ValueError("库存必须为 0-1000000，0 表示不限库存")
    if sort_order < 0 or sort_order > 1000000:
        raise ValueError("排序值无效")

    try:
        price_cents = int(Decimal(monthly_price) * 100)
    except (InvalidOperation, ValueError):
        raise ValueError("价格格式错误")
    if price_cents < 0 or price_cents > 100_000_000:
        raise ValueError("价格范围无效")

    return {
        "name": name,
        "cpu": cpu,
        "memory_mb": memory_mb,
        "disk_gb": disk_gb,
        "bandwidth_mbps": bandwidth_mbps,
        "traffic_gb": traffic_gb,
        "port_count": port_count,
        "monthly_price_cents": price_cents,
        "stock_limit": stock_limit,
        "sort_order": sort_order,
    }

@app.head("/", include_in_schema=False)
def home_head():
    return Response(status_code=200)

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    with db_session() as db:
        plans = db.scalars(
            select(Plan)
            .where(Plan.is_active == True, Plan.homepage_visible == True)
            .order_by(Plan.homepage_sort_order, Plan.sort_order, Plan.monthly_price_cents, Plan.id)
            .limit(3)
        ).all()
        inventories = {p.id: plan_stock(db, p) for p in plans}
        return render(
            request, "home.html", db,
            plans=plans,
            inventories=inventories,
            home_content=homepage_content(db),
        )

@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    with db_session() as db:
        if not setting_enabled(db, "registration_enabled", True):
            return render(request, "register_closed.html", db)
        return render(request, "register.html", db)

@app.post("/register")
def register(
    request: Request,
    username: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(...),
):
    validate_csrf(request, csrf_token)
    username = username.strip()
    email = email.strip().lower()

    if not validate_username(username):
        flash(request, "用户名只能包含字母、数字、下划线，长度 3-32。", "error")
        return RedirectResponse("/register", status_code=303)
    if len(password) < 10:
        flash(request, "密码至少 10 个字符。", "error")
        return RedirectResponse("/register", status_code=303)

    with db_session() as db:
        if not setting_enabled(db, "registration_enabled", True):
            flash(request, "当前暂停新用户注册。", "error")
            return RedirectResponse("/login", status_code=303)

        exists = db.scalar(select(User).where((User.username == username) | (User.email == email)))
        if exists:
            flash(request, "用户名或邮箱已存在。", "error")
            return RedirectResponse("/register", status_code=303)

        user = User(username=username, email=email, password_hash=hash_password(password))
        db.add(user)
        db.flush()
        request.session.clear()
        create_login_session(db, request, user)
        user.last_login_at = datetime.utcnow()
        record_login_event(db, request, username, user_id=user.id, success=True, reason="register")
        write_audit(db, actor=user, request=request, action="account.register", target_type="user", target_id=user.id, target_name=user.username)
        db.commit()

        request.session["show_login_announcement"] = True
        ensure_csrf(request)
        flash(request, "注册成功。", "success")
        return RedirectResponse("/dashboard", status_code=303)


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    with db_session() as db:
        return render(request, "login.html", db)


@app.post("/login")
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(...),
):
    validate_csrf(request, csrf_token)
    username = username.strip()

    with db_session() as db:
        max_failures = max(3, int(get_setting(db, "login_max_failures", "10") or 10))
        window_minutes = max(1, int(get_setting(db, "login_window_minutes", "15") or 15))
        block_minutes = max(1, int(get_setting(db, "login_block_minutes", "30") or 30))
        remaining = login_block_remaining_seconds(
            db, request,
            max_failures=max_failures,
            window_minutes=window_minutes,
            block_minutes=block_minutes,
        )
        if remaining > 0:
            flash(request, f"当前 IP 登录失败次数过多，请约 {max(1, (remaining + 59)//60)} 分钟后再试。", "error")
            return RedirectResponse("/login", status_code=303)

        user = db.scalar(select(User).where(User.username == username))
        if not user or not verify_password(password, user.password_hash):
            record_login_event(db, request, username, user_id=getattr(user, "id", None), success=False, reason="bad_credentials")
            db.commit()
            flash(request, "用户名或密码错误。", "error")
            return RedirectResponse("/login", status_code=303)

        if not user.is_active:
            record_login_event(db, request, username, user_id=user.id, success=False, reason="disabled")
            db.commit()
            request.session.clear()
            flash(request, "账号已被停用，请联系管理员。", "error")
            return RedirectResponse("/login", status_code=303)

        if user.totp_enabled:
            request.session.clear()
            request.session["pending_2fa_user_id"] = user.id
            request.session["pending_2fa_username"] = user.username
            ensure_csrf(request)
            return RedirectResponse("/login/2fa", status_code=303)

        request.session.clear()
        create_login_session(db, request, user)
        user.last_login_at = datetime.utcnow()
        record_login_event(db, request, username, user_id=user.id, success=True, reason="password")
        write_audit(db, actor=user, request=request, action="account.login", target_type="user", target_id=user.id, target_name=user.username)
        db.commit()

        request.session["show_login_announcement"] = True
        if user.is_admin and setting_enabled(db, "admin_require_2fa", False) and not user.totp_enabled:
            request.session["force_2fa_setup"] = True
            flash(request, "管理员账号尚未启用 2FA，请尽快在账户设置中启用。", "warning")
            return RedirectResponse("/account", status_code=303)
        ensure_csrf(request)
        flash(request, "登录成功。", "success")
        return RedirectResponse("/dashboard", status_code=303)


@app.get("/login/2fa", response_class=HTMLResponse)
def login_2fa_page(request: Request):
    if not request.session.get("pending_2fa_user_id"):
        return RedirectResponse("/login", status_code=303)
    with db_session() as db:
        return render(request, "login_2fa.html", db, pending_username=request.session.get("pending_2fa_username"))


@app.post("/login/2fa")
def login_2fa_submit(request: Request, code: str = Form(...), csrf_token: str = Form(...)):
    validate_csrf(request, csrf_token)
    user_id = request.session.get("pending_2fa_user_id")
    if not user_id:
        return RedirectResponse("/login", status_code=303)

    with db_session() as db:
        user = db.get(User, int(user_id))
        if not user or not user.is_active or not user.totp_enabled:
            request.session.clear()
            return RedirectResponse("/login", status_code=303)
        secret = decrypt_secret(user.totp_secret_enc) if user.totp_secret_enc else ""
        if not verify_totp(secret, code):
            record_login_event(db, request, user.username, user_id=user.id, success=False, reason="bad_totp")
            db.commit()
            flash(request, "两步验证码错误。", "error")
            return RedirectResponse("/login/2fa", status_code=303)

        request.session.clear()
        create_login_session(db, request, user)
        user.last_login_at = datetime.utcnow()
        record_login_event(db, request, user.username, user_id=user.id, success=True, reason="totp")
        write_audit(db, actor=user, request=request, action="account.login.2fa", target_type="user", target_id=user.id, target_name=user.username)
        db.commit()
        request.session["show_login_announcement"] = True
        ensure_csrf(request)
        flash(request, "登录成功。", "success")
        return RedirectResponse("/dashboard", status_code=303)


@app.get("/forgot-password", response_class=HTMLResponse)
def forgot_password_page(request: Request):
    with db_session() as db:
        return render(request, "forgot_password.html", db)


@app.post("/forgot-password")
def forgot_password_submit(request: Request, email: str = Form(...), csrf_token: str = Form(...)):
    validate_csrf(request, csrf_token)
    email = email.strip().lower()
    with db_session() as db:
        user = db.scalar(select(User).where(User.email == email))
        if user and user.is_active:
            token = create_password_reset_token(db, user.id, minutes=30)
            base_url = runtime_plain(db, "public_base_url", env_name="PUBLIC_BASE_URL", default="").strip().rstrip("/")
            if base_url:
                try:
                    send_email_address(
                        user.email,
                        subject="[NAT VPS] 重置登录密码",
                        body=f"请在 30 分钟内打开以下地址重置密码：\n{base_url}/reset-password?token={token}\n\n如果不是你本人操作，请忽略。",
                    )
                except Exception as exc:
                    print(f"[security] password reset email: {exc}")
            write_audit(db, actor=user, request=request, action="account.password_reset.request", target_type="user", target_id=user.id, target_name=user.username)
        db.commit()
    flash(request, "如果该邮箱存在且邮件服务已配置，重置邮件会很快发送。", "success")
    return RedirectResponse("/login", status_code=303)


@app.get("/reset-password", response_class=HTMLResponse)
def reset_password_page(request: Request, token: str = Query("")):
    with db_session() as db:
        return render(request, "reset_password.html", db, reset_token=token)


@app.post("/reset-password")
def reset_password_submit(request: Request, token: str = Form(...), password: str = Form(...), csrf_token: str = Form(...)):
    validate_csrf(request, csrf_token)
    if len(password) < 10:
        flash(request, "新密码至少 10 个字符。", "error")
        return RedirectResponse(f"/reset-password?token={quote_plus(token)}", status_code=303)
    with db_session() as db:
        reset = consume_password_reset_token(db, token)
        if not reset:
            flash(request, "重置链接无效或已过期。", "error")
            return RedirectResponse("/forgot-password", status_code=303)
        user = db.get(User, reset.user_id)
        user.password_hash = hash_password(password)
        for sess in db.scalars(select(LoginSession).where(LoginSession.user_id == user.id, LoginSession.revoked_at.is_(None))).all():
            sess.revoked_at = datetime.utcnow()
        write_audit(db, actor=user, request=request, action="account.password_reset.complete", target_type="user", target_id=user.id, target_name=user.username)
        db.commit()
    request.session.clear()
    flash(request, "密码已重置，请重新登录。", "success")
    return RedirectResponse("/login", status_code=303)


@app.post("/logout")
def logout(request: Request, csrf_token: str = Form(...)):
    validate_csrf(request, csrf_token)
    with db_session() as db:
        user = current_user(request, db)
        revoke_current_session(db, request)
        if user:
            write_audit(db, actor=user, request=request, action="account.logout", target_type="user", target_id=user.id, target_name=user.username)
        db.commit()
    request.session.clear()
    return RedirectResponse("/", status_code=303)



@app.get("/account", response_class=HTMLResponse)
def account_page(request: Request):
    with db_session() as db:
        user = login_required(request, db)
        sessions = db.scalars(
            select(LoginSession).where(LoginSession.user_id == user.id).order_by(LoginSession.id.desc()).limit(20)
        ).all()
        login_events = db.scalars(
            select(LoginEvent).where(LoginEvent.user_id == user.id).order_by(LoginEvent.id.desc()).limit(20)
        ).all()
        pending_secret = None
        pending_uri = None
        pending_enc = request.session.get("pending_totp_secret_enc")
        if pending_enc:
            try:
                pending_secret = decrypt_secret(pending_enc)
                pending_uri = totp_uri(pending_secret, user.username, APP_NAME)
            except Exception:
                request.session.pop("pending_totp_secret_enc", None)
        return render(
            request, "account.html", db,
            sessions=sessions,
            login_events=login_events,
            current_login_session_id=request.session.get("login_session_id"),
            pending_totp_secret=pending_secret,
            pending_totp_uri=pending_uri,
            force_2fa_setup=bool(request.session.pop("force_2fa_setup", False)),
        )


@app.post("/account/profile")
def account_profile_update(
    request: Request,
    email: str = Form(...),
    telegram_chat_id: str = Form(""),
    notify_email: str | None = Form(None),
    notify_telegram: str | None = Form(None),
    csrf_token: str = Form(...),
):
    validate_csrf(request, csrf_token)
    email = email.strip().lower()
    if "@" not in email or len(email) > 255:
        flash(request, "邮箱格式无效。", "error")
        return RedirectResponse("/account", status_code=303)
    with db_session() as db:
        user = login_required(request, db)
        duplicate = db.scalar(select(User).where(User.email == email, User.id != user.id))
        if duplicate:
            flash(request, "该邮箱已被其他账号使用。", "error")
            return RedirectResponse("/account", status_code=303)
        user.email = email
        user.telegram_chat_id = telegram_chat_id.strip()[:64] or None
        user.notify_email = notify_email is not None
        user.notify_telegram = notify_telegram is not None
        write_audit(db, actor=user, request=request, action="account.profile.update", target_type="user", target_id=user.id, target_name=user.username)
        db.commit()
    flash(request, "账户设置已保存。", "success")
    return RedirectResponse("/account", status_code=303)


@app.post("/account/password")
def account_password_update(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    csrf_token: str = Form(...),
):
    validate_csrf(request, csrf_token)
    with db_session() as db:
        user = login_required(request, db)
        if not verify_password(current_password, user.password_hash):
            flash(request, "当前密码错误。", "error")
            return RedirectResponse("/account", status_code=303)
        if len(new_password) < 10:
            flash(request, "新密码至少 10 个字符。", "error")
            return RedirectResponse("/account", status_code=303)
        user.password_hash = hash_password(new_password)
        revoked = revoke_other_sessions(db, request, user.id)
        write_audit(db, actor=user, request=request, action="account.password.change", target_type="user", target_id=user.id, target_name=user.username, detail={"revoked_other_sessions": revoked})
        db.commit()
    flash(request, "密码已修改，其他设备会话已退出。", "success")
    return RedirectResponse("/account", status_code=303)


@app.post("/account/2fa/start")
def account_2fa_start(request: Request, csrf_token: str = Form(...)):
    validate_csrf(request, csrf_token)
    with db_session() as db:
        user = login_required(request, db)
        if user.totp_enabled:
            flash(request, "两步验证已经启用。", "info")
            return RedirectResponse("/account", status_code=303)
    request.session["pending_totp_secret_enc"] = encrypt_secret(new_totp_secret())
    flash(request, "请将密钥添加到验证器并输入 6 位验证码确认。", "info")
    return RedirectResponse("/account", status_code=303)


@app.post("/account/2fa/confirm")
def account_2fa_confirm(request: Request, code: str = Form(...), csrf_token: str = Form(...)):
    validate_csrf(request, csrf_token)
    pending_enc = request.session.get("pending_totp_secret_enc")
    if not pending_enc:
        flash(request, "请先生成两步验证密钥。", "error")
        return RedirectResponse("/account", status_code=303)
    try:
        secret = decrypt_secret(pending_enc)
    except Exception:
        request.session.pop("pending_totp_secret_enc", None)
        flash(request, "两步验证密钥已失效，请重新生成。", "error")
        return RedirectResponse("/account", status_code=303)
    if not verify_totp(secret, code):
        flash(request, "验证码错误。", "error")
        return RedirectResponse("/account", status_code=303)
    with db_session() as db:
        user = login_required(request, db)
        user.totp_secret_enc = encrypt_secret(secret)
        user.totp_enabled = True
        write_audit(db, actor=user, request=request, action="account.2fa.enable", target_type="user", target_id=user.id, target_name=user.username)
        db.commit()
    request.session.pop("pending_totp_secret_enc", None)
    flash(request, "两步验证已启用。", "success")
    return RedirectResponse("/account", status_code=303)


@app.post("/account/2fa/disable")
def account_2fa_disable(request: Request, password: str = Form(...), code: str = Form(...), csrf_token: str = Form(...)):
    validate_csrf(request, csrf_token)
    with db_session() as db:
        user = login_required(request, db)
        secret = decrypt_secret(user.totp_secret_enc) if user.totp_secret_enc else ""
        if not verify_password(password, user.password_hash) or not verify_totp(secret, code):
            flash(request, "密码或验证码错误。", "error")
            return RedirectResponse("/account", status_code=303)
        if user.is_admin and setting_enabled(db, "admin_require_2fa", False):
            flash(request, "站点设置要求管理员必须启用 2FA，无法关闭。", "error")
            return RedirectResponse("/account", status_code=303)
        user.totp_enabled = False
        user.totp_secret_enc = None
        write_audit(db, actor=user, request=request, action="account.2fa.disable", target_type="user", target_id=user.id, target_name=user.username)
        db.commit()
    flash(request, "两步验证已关闭。", "success")
    return RedirectResponse("/account", status_code=303)


@app.post("/account/sessions/revoke-others")
def account_revoke_sessions(request: Request, csrf_token: str = Form(...)):
    validate_csrf(request, csrf_token)
    with db_session() as db:
        user = login_required(request, db)
        count = revoke_other_sessions(db, request, user.id)
        write_audit(db, actor=user, request=request, action="account.sessions.revoke_others", target_type="user", target_id=user.id, target_name=user.username, detail={"count": count})
        db.commit()
    flash(request, f"已退出其他 {count} 个会话。", "success")
    return RedirectResponse("/account", status_code=303)


@app.get("/notifications", response_class=HTMLResponse)
def notifications_page(request: Request, page: int = Query(1, ge=1)):
    with db_session() as db:
        user = login_required(request, db)
        per_page = 30
        total_rows = db.scalar(select(func.count()).select_from(Notification).where(Notification.user_id == user.id)) or 0
        total_pages = max(1, (total_rows + per_page - 1) // per_page)
        page = min(page, total_pages)
        notifications = db.scalars(
            select(Notification).where(Notification.user_id == user.id).order_by(Notification.id.desc()).offset((page - 1)*per_page).limit(per_page)
        ).all()
        return render(request, "notifications.html", db, notifications=notifications, page=page, total_pages=total_pages, total_rows=total_rows)


@app.post("/notifications/read-all")
def notifications_read_all(request: Request, csrf_token: str = Form(...)):
    validate_csrf(request, csrf_token)
    with db_session() as db:
        user = login_required(request, db)
        for row in db.scalars(select(Notification).where(Notification.user_id == user.id, Notification.read_at.is_(None))).all():
            row.read_at = datetime.utcnow()
        db.commit()
    return RedirectResponse("/notifications", status_code=303)


@app.get("/recharge", response_class=HTMLResponse)
def recharge_page(request: Request):
    with db_session() as db:
        user = login_required(request, db)
        cfg = payment_config(db)
        orders = db.scalars(select(RechargeOrder).where(RechargeOrder.user_id == user.id).order_by(RechargeOrder.id.desc()).limit(20)).all()
        return render(request, "recharge.html", db, payment_cfg=cfg, recharge_orders=orders)


@app.post("/recharge")
def recharge_create(request: Request, chain: str = Form(...), amount: str = Form(...), csrf_token: str = Form(...)):
    validate_csrf(request, csrf_token)
    try:
        cny_amount = Decimal(amount.strip())
    except Exception:
        flash(request, "充值金额格式错误。", "error")
        return RedirectResponse("/recharge", status_code=303)
    with db_session() as db:
        user = login_required(request, db)
        try:
            order = create_recharge_order(db, user, chain=chain.strip().lower(), cny_amount=cny_amount)
            write_audit(db, actor=user, request=request, action="payment.recharge.create", target_type="recharge_order", target_id=order.id, detail={"chain": order.chain, "cny_cents": order.requested_cny_cents})
            db.commit()
            return RedirectResponse(f"/recharge/{order.id}", status_code=303)
        except Exception as exc:
            db.rollback()
            flash(request, f"创建充值订单失败：{str(exc)[:180]}", "error")
            return RedirectResponse("/recharge", status_code=303)


@app.get("/recharge/{recharge_id}", response_class=HTMLResponse)
def recharge_detail(request: Request, recharge_id: int):
    with db_session() as db:
        user = login_required(request, db)
        order = db.get(RechargeOrder, recharge_id)
        if not order or order.user_id != user.id:
            raise HTTPException(404, "充值订单不存在")
        return render(request, "recharge_detail.html", db, recharge=order, payment_cfg=payment_config(db), now=datetime.utcnow())


@app.post("/recharge/{recharge_id}/txid")
def recharge_submit_txid(request: Request, recharge_id: int, tx_hash: str = Form(...), csrf_token: str = Form(...)):
    validate_csrf(request, csrf_token)
    tx_hash = (tx_hash or "").strip()
    normalized = tx_hash[2:] if tx_hash.lower().startswith("0x") else tx_hash
    if not re.fullmatch(r"[0-9a-fA-F]{64}", normalized):
        flash(request, "交易哈希格式不正确，应为 64 位十六进制 TxID。", "error")
        return RedirectResponse(f"/recharge/{recharge_id}", status_code=303)
    with db_session() as db:
        user = login_required(request, db)
        order = db.get(RechargeOrder, recharge_id)
        if not order or order.user_id != user.id:
            raise HTTPException(404, "充值订单不存在")
        if order.status != "manual":
            flash(request, "只有人工充值订单需要提交交易哈希。", "warning")
            return RedirectResponse(f"/recharge/{recharge_id}", status_code=303)
        order.tx_hash = tx_hash[:128]
        write_audit(db, actor=user, request=request, action="payment.recharge.txid", target_type="recharge_order", target_id=order.id, detail={"chain": order.chain})
        db.commit()
        flash(request, "交易哈希已提交，请等待管理员核对并确认入账。", "success")
    return RedirectResponse(f"/recharge/{recharge_id}", status_code=303)


@app.get("/tickets", response_class=HTMLResponse)
def tickets_page(request: Request):
    with db_session() as db:
        user = login_required(request, db)
        tickets = db.scalars(select(Ticket).where(Ticket.user_id == user.id).order_by(Ticket.updated_at.desc())).all()
        return render(request, "tickets.html", db, tickets=tickets)


@app.get("/tickets/new", response_class=HTMLResponse)
def ticket_new_page(request: Request):
    with db_session() as db:
        login_required(request, db)
        return render(request, "ticket_new.html", db)


@app.post("/tickets/new")
def ticket_new_submit(request: Request, subject: str = Form(...), body: str = Form(...), priority: str = Form("normal"), csrf_token: str = Form(...)):
    validate_csrf(request, csrf_token)
    subject = subject.strip()
    body = body.strip()
    if not subject or not body or len(subject) > 160 or len(body) > 10000:
        flash(request, "工单标题或内容无效。", "error")
        return RedirectResponse("/tickets/new", status_code=303)
    if priority not in {"low", "normal", "high"}:
        priority = "normal"
    with db_session() as db:
        user = login_required(request, db)
        ticket = Ticket(user_id=user.id, subject=subject, priority=priority, status="open")
        db.add(ticket)
        db.flush()
        db.add(TicketMessage(ticket_id=ticket.id, author_user_id=user.id, author_is_admin=False, body=body))
        for admin_user in db.scalars(select(User).where(User.is_admin == True, User.is_active == True)).all():
            queue_notification(db, admin_user, title=f"新工单 #{ticket.id}", body=f"{user.username}: {subject}\n\n{body[:800]}", kind="ticket", severity="info", event_key=f"ticket-new:{ticket.id}:admin:{admin_user.id}")
        write_audit(db, actor=user, request=request, action="ticket.create", target_type="ticket", target_id=ticket.id, target_name=subject)
        db.commit()
        flash(request, "工单已提交。", "success")
        return RedirectResponse(f"/tickets/{ticket.id}", status_code=303)


@app.get("/tickets/{ticket_id}", response_class=HTMLResponse)
def ticket_detail(request: Request, ticket_id: int):
    with db_session() as db:
        user = login_required(request, db)
        ticket = db.get(Ticket, ticket_id)
        if not ticket or ticket.user_id != user.id:
            raise HTTPException(404, "工单不存在")
        messages = db.scalars(select(TicketMessage).where(TicketMessage.ticket_id == ticket.id).order_by(TicketMessage.id)).all()
        return render(request, "ticket_detail.html", db, ticket=ticket, ticket_messages=messages)


@app.post("/tickets/{ticket_id}/reply")
def ticket_reply(request: Request, ticket_id: int, body: str = Form(...), csrf_token: str = Form(...)):
    validate_csrf(request, csrf_token)
    body = body.strip()
    if not body or len(body) > 10000:
        flash(request, "回复内容不能为空。", "error")
        return RedirectResponse(f"/tickets/{ticket_id}", status_code=303)
    with db_session() as db:
        user = login_required(request, db)
        ticket = db.get(Ticket, ticket_id)
        if not ticket or ticket.user_id != user.id:
            raise HTTPException(404, "工单不存在")
        if ticket.status == "closed":
            flash(request, "已关闭的工单不能继续回复。", "error")
            return RedirectResponse(f"/tickets/{ticket_id}", status_code=303)
        db.add(TicketMessage(ticket_id=ticket.id, author_user_id=user.id, author_is_admin=False, body=body))
        for admin_user in db.scalars(select(User).where(User.is_admin == True, User.is_active == True)).all():
            queue_notification(db, admin_user, title=f"工单 #{ticket.id} 用户回复", body=f"{user.username}: {ticket.subject}\n\n{body[:800]}", kind="ticket", severity="info", event_key=f"ticket-user-reply:{ticket.id}:{int(datetime.utcnow().timestamp())}:admin:{admin_user.id}")
        ticket.status = "customer_reply"
        ticket.updated_at = datetime.utcnow()
        write_audit(db, actor=user, request=request, action="ticket.reply", target_type="ticket", target_id=ticket.id, target_name=ticket.subject)
        db.commit()
    return RedirectResponse(f"/tickets/{ticket_id}", status_code=303)


@app.post("/tickets/{ticket_id}/close")
def ticket_close(request: Request, ticket_id: int, csrf_token: str = Form(...)):
    validate_csrf(request, csrf_token)
    with db_session() as db:
        user = login_required(request, db)
        ticket = db.get(Ticket, ticket_id)
        if not ticket or ticket.user_id != user.id:
            raise HTTPException(404, "工单不存在")
        ticket.status = "closed"
        ticket.closed_at = datetime.utcnow()
        ticket.updated_at = datetime.utcnow()
        write_audit(db, actor=user, request=request, action="ticket.close", target_type="ticket", target_id=ticket.id, target_name=ticket.subject)
        db.commit()
    flash(request, "工单已关闭。", "success")
    return RedirectResponse(f"/tickets/{ticket_id}", status_code=303)


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    with db_session() as db:
        user = login_required(request, db)
        servers = db.scalars(
            select(Server).where(
                Server.user_id == user.id,
                Server.deleted_at.is_(None)
            ).order_by(Server.id.desc())
        ).all()

        recent_orders = db.scalars(
            select(Order).where(Order.user_id == user.id).order_by(Order.id.desc()).limit(4)
        ).all()

        now = datetime.utcnow()
        expiring_limit = now + timedelta(days=7)

        traffic_attention_servers = [
            s for s in servers
            if traffic_level(s) in {"warning", "critical", "exhausted", "exempt"}
        ]
        dashboard_stats = {
            "server_count": len(servers),
            "running_count": sum(1 for s in servers if s.status == "running"),
            "stopped_count": sum(1 for s in servers if s.status == "stopped"),
            "expiring_7d": sum(
                1 for s in servers
                if s.expires_at and now < s.expires_at <= expiring_limit
            ),
            "order_count": db.scalar(
                select(func.count()).select_from(Order).where(Order.user_id == user.id)
            ) or 0,
            "traffic_attention": len(traffic_attention_servers),
        }

        server_ui_status = server_ui_status_map(db, servers)
        return render(
            request,
            "dashboard.html",
            db,
            servers=servers[:3],
            server_ui_status=server_ui_status,
            recent_orders=recent_orders,
            dashboard_stats=dashboard_stats,
            traffic_attention_servers=traffic_attention_servers,
            now=now,
        )


@app.get("/servers", response_class=HTMLResponse)
def customer_servers_page(request: Request):
    with db_session() as db:
        user = login_required(request, db)
        servers = db.scalars(
            select(Server).where(
                Server.user_id == user.id,
                Server.deleted_at.is_(None)
            ).order_by(Server.id.desc())
        ).all()

        traffic = {}
        for s in servers:
            if s.status == "running" and s.provider == PROVIDER_NAME and s.provider_instance_id:
                try:
                    traffic[s.id] = provider.network_stats(s.provider_instance_id)
                    seed_first = (
                        s.traffic_last_rx_bytes is None
                        and s.traffic_last_tx_bytes is None
                        and s.traffic_last_sampled_at is None
                    )
                    apply_sample(s, traffic[s.id], datetime.utcnow(), seed_first_sample=seed_first)
                except Exception:
                    traffic[s.id] = None
            else:
                traffic[s.id] = None

        db.commit()

        return render(
            request,
            "servers.html",
            db,
            servers=servers,
            server_ui_status=server_ui_status_map(db, servers),
            traffic=traffic,
            now=datetime.utcnow(),
            app_timezone=APP_TIMEZONE,
        )


@app.get("/servers/{server_id}", response_class=HTMLResponse)
def customer_server_detail(request: Request, server_id: int):
    with db_session() as db:
        user = login_required(request, db)
        server = active_server_for_user(db, user, server_id)

        traffic_stat = None
        if server.status == "running" and server.provider == PROVIDER_NAME and server.provider_instance_id:
            try:
                traffic_stat = provider.network_stats(server.provider_instance_id)
                seed_first = (
                    server.traffic_last_rx_bytes is None
                    and server.traffic_last_tx_bytes is None
                    and server.traffic_last_sampled_at is None
                )
                apply_sample(server, traffic_stat, datetime.utcnow(), seed_first_sample=seed_first)
                db.flush()
            except Exception:
                traffic_stat = None

        system_images = db.scalars(
            select(SystemImage).where(SystemImage.is_active == True).order_by(
                SystemImage.sort_order, SystemImage.id
            )
        ).all()

        service_orders = db.scalars(
            select(Order).where(
                Order.user_id == user.id,
                Order.server_id == server.id,
            ).order_by(Order.id.desc()).limit(8)
        ).all()
        server_jobs = db.scalars(
            select(Job).where(Job.server_id == server.id).order_by(Job.id.desc()).limit(8)
        ).all()
        server_ui_status = server_ui_status_map(db, [server]).get(server.id, server.status)

        return render(
            request,
            "server_detail.html",
            db,
            server=server,
            server_ui_status=server_ui_status,
            traffic_stat=traffic_stat,
            system_images=system_images,
            service_orders=service_orders,
            server_jobs=server_jobs,
            lifecycle=lifecycle_state(server, lifecycle_config(db), datetime.utcnow()),
            lifecycle_cfg=lifecycle_config(db),
            now=datetime.utcnow(),
            app_timezone=APP_TIMEZONE,
        )


@app.get("/orders", response_class=HTMLResponse)
def customer_orders_page(
    request: Request,
    page: int = Query(1, ge=1),
):
    with db_session() as db:
        user = login_required(request, db)
        per_page = 25

        total_rows = db.scalar(
            select(func.count()).select_from(Order).where(Order.user_id == user.id)
        ) or 0
        total_pages = max(1, (total_rows + per_page - 1) // per_page)
        page = min(page, total_pages)

        orders = db.scalars(
            select(Order)
            .where(Order.user_id == user.id)
            .order_by(Order.id.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
        ).all()

        total_spend = db.scalar(
            select(func.coalesce(func.sum(Order.amount_cents), 0)).where(
                Order.user_id == user.id,
                Order.status == "completed",
            )
        ) or 0

        return render(
            request,
            "orders.html",
            db,
            orders=orders,
            page=page,
            total_pages=total_pages,
            total_rows=total_rows,
            per_page=per_page,
            total_spend=total_spend,
        )


@app.get("/plans", response_class=HTMLResponse)
def plans_page(request: Request):
    with db_session() as db:
        plans = db.scalars(
            select(Plan).where(Plan.is_active == True).order_by(Plan.sort_order, Plan.monthly_price_cents, Plan.id)
        ).all()
        system_images = db.scalars(
            select(SystemImage).where(SystemImage.is_active == True).order_by(SystemImage.sort_order, SystemImage.id)
        ).all()
        inventories = {p.id: plan_stock(db, p) for p in plans}
        return render(
            request, "plans.html", db,
            plans=plans,
            system_images=system_images,
            inventories=inventories,
        )

@app.post("/buy/{plan_id}")
def buy_plan(
    request: Request,
    plan_id: int,
    os_image_id: int = Form(...),
    coupon_code: str = Form(""),
    csrf_token: str = Form(...),
):
    validate_csrf(request, csrf_token)

    with db_session() as db:
        user = login_required(request, db)
        plan = db.get(Plan, plan_id)
        if not plan or not plan.is_active:
            raise HTTPException(404, "套餐不存在或已下架")
        if plan_stock(db, plan)["sold_out"]:
            flash(request, "该套餐已经售罄。", "error")
            return RedirectResponse("/plans", status_code=303)

        system_image = db.get(SystemImage, os_image_id)
        if not system_image or not system_image.is_active or system_image.family != "apt":
            raise HTTPException(400, "系统镜像不存在、已停用或暂不支持")

        try:
            coupon, discount_cents = calculate_coupon_discount(db, user, coupon_code, plan.monthly_price_cents)
        except ValueError as exc:
            flash(request, str(exc), "error")
            return RedirectResponse("/plans", status_code=303)

        final_price = max(0, plan.monthly_price_cents - discount_cents)
        if user.balance_cents < final_price:
            flash(request, f"余额不足，本次需支付 {money(final_price)}。可先在线充值。", "error")
            return RedirectResponse("/recharge", status_code=303)

        try:
            order, server, job = queue_service_provision(
                db, user, plan, system_image,
                order_amount_cents=final_price,
                order_kind="purchase",
                coupon=coupon,
                discount_cents=discount_cents,
            )
            if final_price:
                change_balance(db, user, -final_price, kind="purchase", reference_type="order", reference_id=order.id, note=f"购买 {plan.name}")
            write_audit(db, actor=user, request=request, action="server.purchase", target_type="server", target_id=server.id, target_name=server.name, detail={"plan": plan.name, "image": system_image.name, "job_id": job.id, "amount_cents": final_price})
            db.commit()
        except Exception as exc:
            db.rollback()
            flash(request, f"创建开通任务失败：{str(exc)[:180]}", "error")
            return RedirectResponse("/plans", status_code=303)

        flash(request, f"{plan.name} 已进入开通队列，任务 #{job.id}。通常数秒到数分钟完成。", "success")
        return RedirectResponse(f"/servers/{server.id}", status_code=303)

@app.post("/servers/{server_id}/action")
def server_action(
    request: Request,
    server_id: int,
    action: str = Form(...),
    csrf_token: str = Form(...),
):
    validate_csrf(request, csrf_token)
    if action not in {"start", "stop", "reboot"}:
        raise HTTPException(400, "不支持的操作")

    with db_session() as db:
        user = login_required(request, db)
        server = active_server_for_user(db, user, server_id)
        state = lifecycle_state(server, lifecycle_config(db), datetime.utcnow())
        if state["code"] in {"expired", "suspended", "delete_queued"} and action != "stop":
            flash(request, "服务器已超过到期宽限期，请续费后操作。", "error")
            return RedirectResponse(f"/servers/{server.id}", status_code=303)
        try:
            server.status = provider.power_action(server.provider_instance_id, action)
            write_audit(db, actor=user, request=request, action=f"server.power.{action}", target_type="server", target_id=server.id, target_name=server.name)
            db.commit()
            flash(request, f"{ {'start':'开机','stop':'关机','reboot':'重启'}[action] }已执行。", "success")
        except Exception as exc:
            write_audit(db, actor=user, request=request, action=f"server.power.{action}", target_type="server", target_id=server.id, target_name=server.name, detail=str(exc), success=False)
            db.commit()
            flash(request, f"操作失败：{str(exc)[:180]}", "error")
        return RedirectResponse(f"/servers/{server.id}", status_code=303)


@app.post("/servers/{server_id}/reset-password")
def reset_password(
    request: Request,
    server_id: int,
    csrf_token: str = Form(...),
):
    validate_csrf(request, csrf_token)
    with db_session() as db:
        user = login_required(request, db)
        server = active_server_for_user(db, user, server_id)
        blocked = lifecycle_operation_block(db, server)
        if blocked:
            flash(request, blocked, "error")
            return RedirectResponse(f"/servers/{server.id}", status_code=303)
        try:
            password = provider.reset_password(server.provider_instance_id)
            server.root_password_enc = encrypt_secret(password)
            queue_notification(db, user, title="root 密码已重置", body=f"{server.name} 的 root 密码已重置。新密码可在服务器详情页的“当前 root 密码”中查看。", kind="security", severity="warning", event_key=f"root-reset:{server.id}:{int(datetime.utcnow().timestamp())}")
            write_audit(db, actor=user, request=request, action="server.root_password.reset", target_type="server", target_id=server.id, target_name=server.name)
            db.commit()
            one_time_secret(request, f"{server.name} 新 root 密码（仅本次显示）", password)
            flash(request, "root 密码已重置。", "success")
        except Exception as exc:
            write_audit(db, actor=user, request=request, action="server.root_password.reset", target_type="server", target_id=server.id, target_name=server.name, detail=str(exc), success=False)
            db.commit()
            flash(request, f"密码重置失败：{str(exc)[:180]}", "error")
        return RedirectResponse(f"/servers/{server.id}", status_code=303)


@app.post("/servers/{server_id}/traffic/reset")
def customer_reset_traffic_cycle(
    request: Request,
    server_id: int,
    csrf_token: str = Form(...),
):
    validate_csrf(request, csrf_token)
    with db_session() as db:
        user = login_required(request, db)
        server = active_server_for_user(db, user, server_id)
        blocked = lifecycle_operation_block(db, server)
        if blocked:
            flash(request, blocked, "error")
            return RedirectResponse(f"/servers/{server.id}", status_code=303)

        if traffic_quota_gb(server) <= 0:
            flash(request, "当前套餐为不限流量，无需重置。", "info")
            return RedirectResponse(f"/servers/{server.id}", status_code=303)
        if traffic_raw_percent(server) < 100.0:
            flash(request, "仅当本周期流量已经用尽后才能重置流量。", "error")
            return RedirectResponse(f"/servers/{server.id}", status_code=303)

        # Resetting a full quota is a paid add-on. v1.1.1 plans are migrated
        # with the monthly price as the initial reset price; admins can set a
        # dedicated price per plan from 套餐管理. Never silently fall back to
        # a free reset even if an old/malformed row contains 0.
        reset_price = int(getattr(server.plan, "traffic_reset_price_cents", 0) or 0)
        if reset_price <= 0:
            reset_price = int(server.monthly_price_cents or server.plan.monthly_price_cents or 0)
        if reset_price <= 0:
            flash(request, "该套餐尚未配置有效的流量重置价格，请联系管理员。", "error")
            return RedirectResponse(f"/servers/{server.id}", status_code=303)
        if int(user.balance_cents or 0) < reset_price:
            flash(request, f"余额不足，重置流量需要 {money(reset_price)}。", "error")
            return RedirectResponse("/recharge", status_code=303)

        before = {
            "used_bytes": traffic_used_bytes(server),
            "quota_gb": traffic_quota_gb(server),
            "cycle_start": server.traffic_cycle_start.isoformat() if server.traffic_cycle_start else None,
            "cycle_end": server.traffic_cycle_end.isoformat() if server.traffic_cycle_end else None,
            "traffic_throttled": bool(server.traffic_throttled),
            "balance_cents": int(user.balance_cents or 0),
        }

        order = Order(
            user_id=user.id, plan_id=server.plan_id, server_id=server.id,
            amount_cents=reset_price, status="completed", kind="traffic_reset",
        )
        db.add(order)
        db.flush()
        change_balance(
            db, user, -reset_price, kind="traffic_reset",
            reference_type="order", reference_id=order.id,
            note=f"流量重置 {server.name}",
        )

        now = datetime.utcnow()
        reset_cycle(server, now)
        server.traffic_throttle_exempt = False
        provider_error = None
        try:
            enforce_traffic_policy(server, provider, now)
        except Exception as exc:
            provider_error = str(exc)[:180]

        write_audit(
            db, actor=user, request=request, action="server.traffic.reset",
            target_type="server", target_id=server.id, target_name=server.name,
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
            db, user,
            title="流量重置成功",
            body=(
                f"{server.name} 已扣除 {money(reset_price)} 并开启新的流量周期，本周期用量已清零。"
                + (" 带宽恢复正在等待后台重试。" if provider_error else f" 当前带宽已恢复为 {configured_bandwidth_mbps(server)} Mbps。")
                + f" 当前余额 {money(user.balance_cents)}。"
            ),
            kind="billing",
            severity="warning" if provider_error else "success",
            event_key=f"traffic-self-reset:{order.id}",
        )
        db.commit()
        message = f"流量已重置，已扣除 {money(reset_price)}，新的流量周期已开始。"
        if provider_error:
            message += f" 流量已清零，但带宽恢复暂时失败：{provider_error}"
        flash(request, message, "warning" if provider_error else "success")
        return RedirectResponse(f"/servers/{server.id}", status_code=303)


@app.post("/servers/{server_id}/reinstall")
def reinstall_server(
    request: Request,
    server_id: int,
    os_image_id: int = Form(...),
    confirm_name: str = Form(...),
    csrf_token: str = Form(...),
):
    validate_csrf(request, csrf_token)
    with db_session() as db:
        user = login_required(request, db)
        server = active_server_for_user(db, user, server_id)
        blocked = lifecycle_operation_block(db, server)
        if blocked:
            flash(request, blocked, "error")
            return RedirectResponse(f"/servers/{server.id}", status_code=303)
        if confirm_name.strip() != server.name:
            flash(request, "重装确认名称不正确。", "error")
            return RedirectResponse(f"/servers/{server.id}", status_code=303)
        system_image = db.get(SystemImage, os_image_id)
        if not system_image or not system_image.is_active or system_image.family != "apt":
            flash(request, "所选系统镜像不可用。", "error")
            return RedirectResponse(f"/servers/{server.id}", status_code=303)
        active_job = db.scalar(select(Job).where(Job.server_id == server.id, Job.status.in_(["pending","running"]), Job.job_type.in_(["reinstall_server","delete_server"])))
        if active_job:
            flash(request, f"服务器已有任务 #{active_job.id} 正在处理。", "error")
            return RedirectResponse(f"/servers/{server.id}", status_code=303)
        job = enqueue_job(db, "reinstall_server", user_id=user.id, server_id=server.id, payload={"os_image_id": system_image.id})
        write_audit(db, actor=user, request=request, action="server.reinstall.enqueue", target_type="server", target_id=server.id, target_name=server.name, detail={"image": system_image.name, "job_id": job.id})
        db.commit()
        flash(request, f"重装任务 #{job.id} 已提交。页面可以关闭，后台会继续执行。", "success")
        return RedirectResponse(f"/servers/{server.id}", status_code=303)


@app.post("/servers/{server_id}/delete")
def delete_server(
    request: Request,
    server_id: int,
    confirm_name: str = Form(...),
    csrf_token: str = Form(...),
):
    validate_csrf(request, csrf_token)
    with db_session() as db:
        user = login_required(request, db)
        server = active_server_for_user(db, user, server_id)
        if confirm_name.strip() != server.name:
            flash(request, "删除确认名称不正确。", "error")
            return RedirectResponse(f"/servers/{server.id}", status_code=303)
        active_job = db.scalar(select(Job).where(Job.server_id == server.id, Job.status.in_(["pending","running"]), Job.job_type == "delete_server"))
        if active_job:
            flash(request, f"删除任务 #{active_job.id} 已经在执行。", "info")
            return RedirectResponse(f"/servers/{server.id}", status_code=303)
        job = enqueue_job(db, "delete_server", user_id=user.id, server_id=server.id, payload={})
        write_audit(db, actor=user, request=request, action="server.delete.enqueue", target_type="server", target_id=server.id, target_name=server.name, detail={"job_id": job.id})
        db.commit()
        flash(request, f"永久删除任务 #{job.id} 已提交。", "warning")
        return RedirectResponse(f"/servers/{server.id}", status_code=303)


@app.post("/servers/{server_id}/renew")
def renew_server(
    request: Request,
    server_id: int,
    csrf_token: str = Form(...),
):
    validate_csrf(request, csrf_token)
    with db_session() as db:
        user = login_required(request, db)
        server = active_server_for_user(db, user, server_id)

        price = server.monthly_price_cents if server.monthly_price_cents is not None else server.plan.monthly_price_cents
        if user.balance_cents < price:
            flash(request, "余额不足，无法续费。", "error")
            return RedirectResponse("/recharge", status_code=303)

        cancelled_deletes, blocking_delete = cancel_pending_expiry_delete(db, server)
        if blocking_delete:
            flash(request, "服务器存在手动删除任务或自动删除任务已经开始执行，当前无法安全续费。请先确认删除任务状态。", "error")
            return RedirectResponse(f"/servers/{server.id}", status_code=303)

        now = datetime.utcnow()
        base = server.expires_at if server.expires_at and server.expires_at > now else now
        server.expires_at = base + timedelta(days=30)
        order = Order(user_id=user.id, plan_id=server.plan_id, server_id=server.id, amount_cents=price, status="completed", kind="renewal")
        db.add(order)
        db.flush()
        change_balance(db, user, -price, kind="renewal", reference_type="order", reference_id=order.id, note=f"续费 {server.name}")

        restarted, restart_error = resume_after_renewal(db, provider, PROVIDER_NAME, server)
        queue_notification(
            db,
            user,
            title="续费成功",
            body=(
                f"{server.name} 已续费 30 天，新到期时间：{local_dt(server.expires_at):%Y-%m-%d %H:%M}。"
                + (" 到期停机实例已自动恢复开机。" if restarted else "")
            ),
            kind="billing",
            severity="success",
            event_key=f"renew:{order.id}",
        )
        write_audit(
            db,
            actor=user,
            request=request,
            action="server.renew",
            target_type="server",
            target_id=server.id,
            target_name=server.name,
            detail={
                "order_id": order.id,
                "amount_cents": price,
                "cancelled_expiry_delete_jobs": cancelled_deletes,
                "auto_restarted": restarted,
                "restart_error": restart_error,
            },
        )
        db.commit()
        message = "续费成功，到期时间已延长 30 天。"
        if restarted:
            message += " 到期停机的 VPS 已自动恢复开机。"
        elif restart_error:
            message += f" 到期时间已恢复，但自动开机失败：{restart_error}"
        flash(request, message, "warning" if restart_error else "success")
        return RedirectResponse(f"/servers/{server.id}", status_code=303)


@app.post("/servers/{server_id}/ports")
def add_port_mapping(
    request: Request,
    server_id: int,
    private_port: int = Form(...),
    protocol: str = Form(...),
    csrf_token: str = Form(...),
):
    validate_csrf(request, csrf_token)
    protocol = protocol.lower()
    if protocol not in {"tcp", "udp"} or not (1 <= private_port <= 65535):
        raise HTTPException(400, "端口参数无效")
    with db_session() as db:
        user = login_required(request, db)
        server = active_server_for_user(db, user, server_id)
        blocked = lifecycle_operation_block(db, server)
        if blocked:
            flash(request, blocked, "error")
            return RedirectResponse(f"/servers/{server.id}", status_code=303)
        try:
            validate_port_policy(db, protocol, private_port)
        except ValueError as exc:
            flash(request, str(exc), "error")
            return RedirectResponse(f"/servers/{server.id}", status_code=303)
        port_limit = server.port_limit if server.port_limit is not None else server.plan.port_count
        if len(server.ports) >= port_limit:
            flash(request, f"该实例最多允许 {port_limit} 个自定义 NAT 端口。", "error")
            return RedirectResponse(f"/servers/{server.id}", status_code=303)
        try:
            public_port = allocate_public_port(db, protocol, server.host if PROVIDER_NAME == "remote" else None)
            device_name = provider.add_port(server.provider_instance_id, public_port, private_port, protocol)
            mapping = PortMapping(server_id=server.id, public_port=public_port, private_port=private_port, protocol=protocol, device_name=device_name)
            db.add(mapping)
            write_audit(db, actor=user, request=request, action="server.port.add", target_type="server", target_id=server.id, target_name=server.name, detail={"protocol": protocol, "public_port": public_port, "private_port": private_port})
            db.commit()
            flash(request, f"已分配公网 {protocol.upper()} 端口 {public_port} → {private_port}。", "success")
        except Exception as exc:
            db.rollback()
            flash(request, f"添加端口失败：{str(exc)[:180]}", "error")
        return RedirectResponse(f"/servers/{server.id}", status_code=303)


@app.post("/servers/{server_id}/ports/{mapping_id}/delete")
def delete_port_mapping(
    request: Request,
    server_id: int,
    mapping_id: int,
    csrf_token: str = Form(...),
):
    validate_csrf(request, csrf_token)
    with db_session() as db:
        user = login_required(request, db)
        server = active_server_for_user(db, user, server_id)
        mapping = db.get(PortMapping, mapping_id)
        if not mapping or mapping.server_id != server.id:
            raise HTTPException(404, "端口映射不存在")
        try:
            provider.remove_port(server.provider_instance_id, mapping.device_name)
            detail = {"protocol": mapping.protocol, "public_port": mapping.public_port, "private_port": mapping.private_port}
            db.delete(mapping)
            write_audit(db, actor=user, request=request, action="server.port.delete", target_type="server", target_id=server.id, target_name=server.name, detail=detail)
            db.commit()
            flash(request, "端口映射已删除。", "success")
        except Exception as exc:
            db.rollback()
            flash(request, f"删除端口失败：{str(exc)[:180]}", "error")
        return RedirectResponse(f"/servers/{server.id}", status_code=303)

@app.post("/announcements/{announcement_id}/read")
def announcement_mark_read(request: Request, announcement_id: int, csrf_token: str = Form(...)):
    validate_csrf(request, csrf_token)
    with db_session() as db:
        user = login_required(request, db)
        if not mark_announcement_read(db, user.id, announcement_id):
            raise HTTPException(404, "公告不存在")
        db.commit()
        unread = db.scalar(select(func.count()).select_from(Announcement).where(
            Announcement.is_published == True,
            ~Announcement.id.in_(select(AnnouncementRead.announcement_id).where(AnnouncementRead.user_id == user.id)),
        )) or 0
        return JSONResponse({"ok": True, "unread": int(unread)})


# ---------------- Admin ----------------



@app.get("/admin", response_class=HTMLResponse)
def admin_page(
    request: Request,
    section: str = Query("dashboard"),
    q: str = Query(""),
    page: int = Query(1, ge=1),
):
    allowed_sections = {
        "dashboard", "plans", "images", "coupons", "users", "provision",
        "servers", "orders", "payments", "tickets", "jobs", "audit",
        "backups", "nodes", "notifications", "announcements", "settings",
    }
    if section not in allowed_sections:
        section = "dashboard"
    q = q.strip()
    per_page = 50

    with db_session() as db:
        admin = admin_required(request, db)
        now_utc = datetime.utcnow()
        now_local = datetime.now(APP_TZ)
        today_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        month_local = today_local.replace(day=1)
        today_utc = today_local.astimezone(timezone.utc).replace(tzinfo=None)
        month_utc = month_local.astimezone(timezone.utc).replace(tzinfo=None)
        expiring_7d = now_utc + timedelta(days=7)

        stats = {
            "users": db.scalar(select(func.count()).select_from(User)) or 0,
            "servers": db.scalar(select(func.count()).select_from(Server).where(Server.deleted_at.is_(None))) or 0,
            "orders": db.scalar(select(func.count()).select_from(Order)) or 0,
            "revenue_cents": db.scalar(select(func.coalesce(func.sum(Order.amount_cents), 0)).where(Order.status == "completed")) or 0,
        }

        disk = psutil.disk_usage("/")
        mem = psutil.virtual_memory()
        node = {
            "cpu_percent": psutil.cpu_percent(interval=0.05),
            "cpu_count": psutil.cpu_count(),
            "memory_used_mb": round(mem.used / 1024 / 1024),
            "memory_total_mb": round(mem.total / 1024 / 1024),
            "memory_percent": mem.percent,
            "disk_used_gb": round(disk.used / 1024 / 1024 / 1024, 1),
            "disk_total_gb": round(disk.total / 1024 / 1024 / 1024, 1),
            "disk_percent": disk.percent,
        }

        traffic_policy_servers = db.scalars(select(Server).where(Server.deleted_at.is_(None))).all()
        traffic_attention_count = sum(1 for row in traffic_policy_servers if traffic_level(row) in {"warning", "critical", "exhausted", "exempt"})
        traffic_throttled_count = sum(1 for row in traffic_policy_servers if bool(row.traffic_throttled))
        business = {
            "active_plans": db.scalar(select(func.count()).select_from(Plan).where(Plan.is_active == True)) or 0,
            "running_servers": db.scalar(select(func.count()).select_from(Server).where(Server.deleted_at.is_(None), Server.status == "running")) or 0,
            "stopped_servers": db.scalar(select(func.count()).select_from(Server).where(Server.deleted_at.is_(None), Server.status == "stopped")) or 0,
            "expiring_7d": db.scalar(select(func.count()).select_from(Server).where(Server.deleted_at.is_(None), Server.expires_at.is_not(None), Server.expires_at > now_utc, Server.expires_at <= expiring_7d)) or 0,
            "today_orders": db.scalar(select(func.count()).select_from(Order).where(Order.created_at >= today_utc, Order.status == "completed")) or 0,
            "today_sales_cents": db.scalar(select(func.coalesce(func.sum(Order.amount_cents), 0)).where(Order.created_at >= today_utc, Order.status == "completed")) or 0,
            "month_sales_cents": db.scalar(select(func.coalesce(func.sum(Order.amount_cents), 0)).where(Order.created_at >= month_utc, Order.status == "completed")) or 0,
            "new_users_today": db.scalar(select(func.count()).select_from(User).where(User.created_at >= today_utc)) or 0,
            "traffic_attention": traffic_attention_count,
            "traffic_throttled": traffic_throttled_count,
            "pending_jobs": db.scalar(select(func.count()).select_from(Job).where(Job.status.in_(["pending", "running"]))) or 0,
            "open_tickets": db.scalar(select(func.count()).select_from(Ticket).where(Ticket.status != "closed")) or 0,
            "pending_recharges": db.scalar(select(func.count()).select_from(RechargeOrder).where(RechargeOrder.status.in_(["pending", "detected", "manual"]))) or 0,
            "reconcile_attention": db.scalar(select(func.count()).select_from(Server).where(Server.deleted_at.is_(None), Server.reconcile_status.in_(["warning", "error", "missing"]))) or 0,
            "hosts_total": db.scalar(select(func.count()).select_from(HostNode)) or 0,
            "hosts_online": db.scalar(select(func.count()).select_from(HostNode).where(HostNode.enabled == True, HostNode.status == "online")) or 0,
        }

        plans=[]; system_images=[]; coupons=[]; users=[]; servers=[]; orders=[]
        recharge_orders=[]; tickets=[]; jobs=[]; audit_logs=[]; balance_ledger=[]; hosts=[]; notification_rows=[]; announcement_admin_rows=[]
        dashboard_recent_orders=[]; dashboard_recent_servers=[]; dashboard_stock=[]
        inventories={}; user_stats={}; site_settings={}; backup_rows=[]; backup_preview=None; plan_host_map={}; host_port_stats={}; host_schedule_states={}
        repair_order=None; repair_preview=None; repair_error=None; repair_tx=""
        total_rows=0; total_pages=1

        def finish_page(stmt, *, order_by):
            nonlocal total_rows, total_pages, page
            total_rows = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
            total_pages = max(1, (total_rows + per_page - 1)//per_page)
            page = min(page, total_pages)
            return db.scalars(stmt.order_by(order_by).offset((page-1)*per_page).limit(per_page)).all()

        if section in {"dashboard", "plans", "provision", "nodes"}:
            plans = db.scalars(select(Plan).order_by(Plan.sort_order, Plan.id)).all()
            inventories = {row.id: plan_stock(db, row) for row in plans}
        if section == "dashboard":
            dashboard_recent_orders = db.scalars(select(Order).order_by(Order.id.desc()).limit(8)).all()
            dashboard_recent_servers = db.scalars(select(Server).where(Server.deleted_at.is_(None)).order_by(Server.id.desc()).limit(8)).all()
            dashboard_stock = [{"plan": row, "stock": inventories[row.id]} for row in plans if row.is_active]
        if section in {"dashboard", "images", "provision"}:
            system_images = db.scalars(select(SystemImage).order_by(SystemImage.sort_order, SystemImage.id)).all()

        if section == "coupons":
            stmt=select(Coupon)
            if q: stmt=stmt.where(Coupon.code.ilike(f"%{q}%"))
            coupons=finish_page(stmt, order_by=Coupon.id.desc())
        elif section == "users":
            stmt=select(User)
            if q: stmt=stmt.where(or_(User.username.ilike(f"%{q}%"), User.email.ilike(f"%{q}%")))
            users=finish_page(stmt, order_by=User.id.desc())
            for row in users:
                user_stats[row.id]={
                    "servers": db.scalar(select(func.count()).select_from(Server).where(Server.user_id==row.id, Server.deleted_at.is_(None))) or 0,
                    "orders": db.scalar(select(func.count()).select_from(Order).where(Order.user_id==row.id)) or 0,
                    "sessions": db.scalar(select(func.count()).select_from(LoginSession).where(LoginSession.user_id==row.id, LoginSession.revoked_at.is_(None))) or 0,
                }
        elif section == "servers":
            stmt=select(Server).join(User, Server.user_id==User.id).where(Server.deleted_at.is_(None))
            if q:
                like=f"%{q}%"
                stmt=stmt.where(or_(Server.name.ilike(like), User.username.ilike(like), User.email.ilike(like), Server.public_ip.ilike(like), Server.private_ip.ilike(like), Server.os_name.ilike(like)))
            servers=finish_page(stmt, order_by=Server.id.desc())
        elif section == "orders":
            stmt=select(Order).join(User, Order.user_id==User.id).join(Plan, Order.plan_id==Plan.id)
            if q:
                like=f"%{q}%"
                cond=[User.username.ilike(like), User.email.ilike(like), Plan.name.ilike(like), Order.status.ilike(like), Order.kind.ilike(like), Order.coupon_code.ilike(like)]
                if q.lstrip("#").isdigit(): cond.append(Order.id==int(q.lstrip("#")))
                stmt=stmt.where(or_(*cond))
            orders=finish_page(stmt, order_by=Order.id.desc())
        elif section == "payments":
            stmt=select(RechargeOrder).join(User, RechargeOrder.user_id==User.id)
            if q:
                like=f"%{q}%"
                cond=[User.username.ilike(like), User.email.ilike(like), RechargeOrder.chain.ilike(like), RechargeOrder.status.ilike(like), RechargeOrder.tx_hash.ilike(like), RechargeOrder.deposit_address.ilike(like)]
                if q.lstrip("#").isdigit(): cond.append(RechargeOrder.id==int(q.lstrip("#")))
                stmt=stmt.where(or_(*cond))
            recharge_orders=finish_page(stmt, order_by=RechargeOrder.id.desc())

            repair_order_id=(request.query_params.get("repair_order_id") or "").strip()
            repair_tx=(request.query_params.get("repair_tx") or "").strip()
            if repair_order_id.isdigit():
                repair_order=db.get(RechargeOrder,int(repair_order_id))
                if repair_order and repair_order.status=="paid":
                    repair_error="该订单已经入账，无需补单。"
                elif repair_order and repair_tx:
                    try:
                        repair_preview=verify_recharge_transaction(db,repair_order,repair_tx)
                    except Exception as exc:
                        repair_error=str(exc)[:300]
        elif section == "notifications":
            stmt=select(Notification).join(User, Notification.user_id==User.id)
            if q:
                like=f"%{q}%"
                stmt=stmt.where(or_(Notification.title.ilike(like), Notification.kind.ilike(like), Notification.email_status.ilike(like), Notification.telegram_status.ilike(like), User.username.ilike(like), User.email.ilike(like)))
            notification_rows=finish_page(stmt, order_by=Notification.id.desc())
        elif section == "announcements":
            stmt=select(Announcement)
            if q:
                like=f"%{q}%"
                stmt=stmt.where(or_(Announcement.title.ilike(like), Announcement.body.ilike(like)))
            announcement_admin_rows=finish_page(stmt, order_by=Announcement.id.desc())
        elif section == "tickets":
            stmt=select(Ticket).join(User, Ticket.user_id==User.id)
            if q:
                like=f"%{q}%"
                cond=[Ticket.subject.ilike(like), Ticket.status.ilike(like), Ticket.priority.ilike(like), User.username.ilike(like), User.email.ilike(like)]
                if q.lstrip("#").isdigit(): cond.append(Ticket.id==int(q.lstrip("#")))
                stmt=stmt.where(or_(*cond))
            tickets=finish_page(stmt, order_by=Ticket.updated_at.desc())
        elif section == "jobs":
            stmt=select(Job)
            if q:
                like=f"%{q}%"
                cond=[Job.job_type.ilike(like), Job.status.ilike(like), Job.error_text.ilike(like)]
                if q.lstrip("#").isdigit(): cond.extend([Job.id==int(q.lstrip("#")), Job.server_id==int(q.lstrip("#"))])
                stmt=stmt.where(or_(*cond))
            jobs=finish_page(stmt, order_by=Job.id.desc())
        elif section == "audit":
            stmt=select(AuditLog)
            if q:
                like=f"%{q}%"
                cond=[AuditLog.actor_username.ilike(like), AuditLog.action.ilike(like), AuditLog.target_type.ilike(like), AuditLog.target_name.ilike(like), AuditLog.ip.ilike(like), AuditLog.detail.ilike(like)]
                if q.lstrip("#").isdigit(): cond.append(AuditLog.id==int(q.lstrip("#")))
                stmt=stmt.where(or_(*cond))
            audit_logs=finish_page(stmt, order_by=AuditLog.id.desc())
        elif section == "nodes":
            hosts = db.scalars(select(HostNode).order_by(HostNode.region, HostNode.name)).all()
            host_port_stats = {row.id: host_port_pool_stats(db, row) for row in hosts}
            host_schedule_states = {row.id: host_schedule_state(db, row) for row in hosts}
            links = db.scalars(select(PlanHost).where(PlanHost.enabled == True)).all()
            for link in links:
                plan_host_map.setdefault(link.host_id, set()).add(link.plan_id)
        elif section == "backups":
            backup_rows=list_backups()
            total_rows=len(backup_rows)
            restore_candidate=(request.query_params.get("restore_candidate") or "").strip()
            if restore_candidate:
                try:
                    candidate=(BACKUP_DIR/Path(restore_candidate).name).resolve()
                    if candidate.parent==BACKUP_DIR.resolve() and candidate.exists():
                        backup_preview=validate_backup_file(candidate)
                except Exception as exc:
                    flash(request,f"备份校验失败：{str(exc)[:180]}","error")
        setting_keys=[]
        bool_setting_keys=[]
        if section == "settings":
            setting_keys=[
                *HOME_SETTING_DEFAULTS.keys(),
                "registration_enabled","public_base_url",
                "login_max_failures","login_window_minutes","login_block_minutes","admin_require_2fa",
                "port_blocked_private","port_blocked_public","port_tcp_enabled","port_udp_enabled",
                "expiry_notice_days","expiry_grace_days","expiry_auto_stop_enabled","expiry_delete_enabled",
                "expiry_delete_after_days","expiry_delete_notice_hours","expiry_renew_auto_start",
                "traffic_cycle_default_mode","traffic_cycle_default_day","node_nat_port_alert_percent",
            ]
            bool_setting_keys=[
                "registration_enabled","admin_require_2fa","port_tcp_enabled","port_udp_enabled",
                "expiry_auto_stop_enabled","expiry_delete_enabled","expiry_renew_auto_start",
            ]
        elif section == "payments":
            setting_keys=[
                "payment_enabled","usdt_cny_rate","recharge_min_cny","recharge_max_cny","payment_expire_minutes","payment_late_grace_hours",
                "payment_tron_enabled","payment_tron_mode","payment_tron_wallet",
                "payment_polygon_enabled","payment_polygon_mode","payment_polygon_wallet","payment_polygon_rpc","payment_polygon_confirmations",
            ]
            bool_setting_keys=["payment_enabled","payment_tron_enabled","payment_polygon_enabled"]
        elif section == "notifications":
            setting_keys=[
                "smtp_host","smtp_port","smtp_username","smtp_from","smtp_starttls",
                "notify_rule_server_email","notify_rule_server_telegram",
                "notify_rule_traffic_email","notify_rule_traffic_telegram",
                "notify_rule_expiry_email","notify_rule_expiry_telegram",
                "notify_rule_payment_email","notify_rule_payment_telegram",
                "notify_rule_ticket_email","notify_rule_ticket_telegram",
                "notify_rule_security_email","notify_rule_security_telegram",
                "notify_rule_system_email","notify_rule_system_telegram",
            ]
            bool_setting_keys=[key for key in setting_keys if key == "smtp_starttls" or key.startswith("notify_rule_")]

        if setting_keys:
            site_settings={key: get_setting(db, key, HOME_SETTING_DEFAULTS.get(key, "")) for key in setting_keys}
            for key in bool_setting_keys:
                site_settings[key]=str(site_settings.get(key,"")).lower() in {"1","true","yes","on"}

        payment_cfg=payment_config(db)
        runtime_cfg=notification_runtime_config(db)
        deployment=deployment_status(runtime_cfg["public_base_url"])
        env_status={
            "smtp": bool(runtime_cfg["smtp_host"] and runtime_cfg["smtp_from"]),
            "smtp_password": bool(runtime_cfg["smtp_password"]),
            "telegram": bool(runtime_cfg["telegram_bot_token"]),
            "trongrid": bool(runtime_cfg["trongrid_api_key"]),
            "public_base_url": runtime_cfg["public_base_url"],
        }
        return render(request,"admin.html",db,
            section=section,q=q,page=page,per_page=per_page,total_rows=total_rows,total_pages=total_pages,
            stats=stats,node=node,business=business,plans=plans,system_images=system_images,coupons=coupons,users=users,servers=servers,orders=orders,
            recharge_orders=recharge_orders,tickets=tickets,jobs=jobs,audit_logs=audit_logs,balance_ledger=balance_ledger,backup_rows=backup_rows,backup_preview=backup_preview,hosts=hosts,notification_rows=notification_rows,announcement_admin_rows=announcement_admin_rows,
            inventories=inventories,user_stats=user_stats,site_settings=site_settings,payment_cfg=payment_cfg,env_status=env_status,deployment=deployment,plan_host_map=plan_host_map,host_port_stats=host_port_stats,host_schedule_states=host_schedule_states,
            lifecycle_cfg=lifecycle_config(db),
            repair_order=repair_order,repair_preview=repair_preview,repair_error=repair_error,repair_tx=repair_tx,
            dashboard_recent_orders=dashboard_recent_orders,dashboard_recent_servers=dashboard_recent_servers,dashboard_stock=dashboard_stock,
        )



@app.post("/admin/nodes")
def admin_create_node(
    request: Request,
    name: str = Form(...),
    region: str = Form("default"),
    api_url: str = Form(...),
    api_token: str = Form(...),
    public_ip: str = Form(""),
    max_vps: int = Form(0),
    schedule_cpu_max_percent: int = Form(90),
    schedule_memory_max_percent: int = Form(90),
    schedule_storage_max_percent: int = Form(90),
    verify_tls: str | None = Form(None),
    csrf_token: str = Form(...),
):
    validate_csrf(request, csrf_token)
    name = name.strip()
    region = region.strip() or "default"
    api_url = api_url.strip().rstrip("/")
    public_ip = public_ip.strip()
    api_token = api_token.strip()
    if not name or not api_url.startswith(("http://", "https://")) or len(api_token) < 20:
        flash(request, "节点名称、Agent URL 或 Token 无效。", "error")
        return RedirectResponse("/admin?section=nodes", status_code=303)

    with db_session() as db:
        admin = admin_required(request, db)
        if db.scalar(select(HostNode).where(HostNode.name == name)):
            flash(request, "节点名称已存在。", "error")
            return RedirectResponse("/admin?section=nodes", status_code=303)
        row = HostNode(
            name=name,
            region=region,
            api_url=api_url,
            api_token_enc=encrypt_secret(api_token),
            public_ip=public_ip,
            port_start=None,
            port_end=None,
            max_vps=max(0, max_vps),
            enabled=True,
            verify_tls=verify_tls == "true",
            maintenance_mode=False,
            schedule_cpu_max_percent=max(0, min(100, schedule_cpu_max_percent)),
            schedule_memory_max_percent=max(0, min(100, schedule_memory_max_percent)),
            schedule_storage_max_percent=max(0, min(100, schedule_storage_max_percent)),
            status="unknown",
        )
        db.add(row)
        db.flush()
        try:
            refresh_host(row)
            msg = f"节点 {row.name} 已添加并连接成功。下一步请配置 NAT 端口池。"
            kind = "success"
        except Exception as exc:
            row.status = "offline"
            row.last_error = str(exc)[:1000]
            msg = f"节点已保存，但当前连接失败：{str(exc)[:160]}"
            kind = "warning"
        write_audit(
            db,
            actor=admin,
            request=request,
            action="admin.node.create",
            target_type="host_node",
            target_id=row.id,
            target_name=row.name,
            detail={"region": row.region, "api_url": row.api_url, "public_ip": row.public_ip},
        )
        db.commit()
        flash(request, msg, kind)
    return RedirectResponse("/admin?section=nodes", status_code=303)


@app.post("/admin/nodes/{node_id}/update")
def admin_update_node(
    request: Request,
    node_id: int,
    name: str = Form(...),
    region: str = Form("default"),
    api_url: str = Form(...),
    public_ip: str = Form(""),
    max_vps: int = Form(0),
    schedule_cpu_max_percent: int = Form(90),
    schedule_memory_max_percent: int = Form(90),
    schedule_storage_max_percent: int = Form(90),
    api_token: str = Form(""),
    verify_tls: str | None = Form(None),
    csrf_token: str = Form(...),
):
    validate_csrf(request, csrf_token)
    with db_session() as db:
        admin = admin_required(request, db)
        row = db.get(HostNode, node_id)
        if not row:
            raise HTTPException(404, "宿主机不存在")
        name = name.strip()
        api_url = api_url.strip().rstrip("/")
        public_ip = public_ip.strip()
        region = region.strip() or "default"
        if not name or not api_url.startswith(("http://", "https://")):
            flash(request, "节点参数无效。", "error")
            return RedirectResponse("/admin?section=nodes", status_code=303)
        duplicate = db.scalar(select(HostNode).where(HostNode.name == name, HostNode.id != row.id))
        if duplicate:
            flash(request, "节点名称已存在。", "error")
            return RedirectResponse("/admin?section=nodes", status_code=303)

        row.name = name
        row.region = region
        row.api_url = api_url
        row.public_ip = public_ip
        row.max_vps = max(0, max_vps)
        row.verify_tls = verify_tls == "true"
        row.schedule_cpu_max_percent = max(0, min(100, schedule_cpu_max_percent))
        row.schedule_memory_max_percent = max(0, min(100, schedule_memory_max_percent))
        row.schedule_storage_max_percent = max(0, min(100, schedule_storage_max_percent))
        if api_token.strip():
            row.api_token_enc = encrypt_secret(api_token.strip())

        write_audit(
            db,
            actor=admin,
            request=request,
            action="admin.node.update",
            target_type="host_node",
            target_id=row.id,
            target_name=row.name,
        )
        db.commit()
        flash(request, f"节点 {row.name} 设置已保存。", "success")
    return RedirectResponse("/admin?section=nodes", status_code=303)


@app.post("/admin/nodes/{node_id}/port-pool")
def admin_update_node_port_pool(
    request: Request,
    node_id: int,
    port_start: int = Form(...),
    port_end: int = Form(...),
    csrf_token: str = Form(...),
):
    validate_csrf(request, csrf_token)
    with db_session() as db:
        admin = admin_required(request, db)
        row = db.get(HostNode, node_id)
        if not row:
            raise HTTPException(404, "宿主机不存在")

        start = int(port_start)
        end = int(port_end)
        if start > end:
            flash(request, "NAT 端口池起始端口不能大于结束端口。", "error")
            return RedirectResponse("/admin?section=nodes", status_code=303)
        if not (1024 <= start <= end <= 65535):
            flash(request, "NAT 端口池必须位于 1024-65535。", "error")
            return RedirectResponse("/admin?section=nodes", status_code=303)

        parsed = urlparse(row.api_url)
        agent_port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if start <= agent_port <= end:
            flash(request, f"NAT 端口池不能包含 Host Agent 管理端口 {agent_port}。", "error")
            return RedirectResponse("/admin?section=nodes", status_code=303)

        used_ports: set[int] = set()
        servers_on_host = db.scalars(
            select(Server).where(Server.host_id == row.id, Server.deleted_at.is_(None))
        ).all()
        server_ids = []
        for server in servers_on_host:
            server_ids.append(server.id)
            if server.ssh_port:
                used_ports.add(int(server.ssh_port))
        if server_ids:
            mappings = db.scalars(
                select(PortMapping).where(PortMapping.server_id.in_(server_ids))
            ).all()
            used_ports.update(int(mapping.public_port) for mapping in mappings)

        outside = sorted(port for port in used_ports if not (start <= port <= end))
        if outside:
            preview = ", ".join(str(port) for port in outside[:8])
            if len(outside) > 8:
                preview += " ..."
            flash(request, f"不能保存：新的端口池会排除正在使用的公网端口 {preview}。", "error")
            return RedirectResponse("/admin?section=nodes", status_code=303)

        try:
            result = host_request(
                row,
                "POST",
                "/v1/config/nat-port-pool",
                payload={"port_start": start, "port_end": end},
                timeout=30,
            )
        except Exception as exc:
            flash(request, f"Agent 拒绝端口池配置：{str(exc)[:180]}", "error")
            return RedirectResponse("/admin?section=nodes", status_code=303)

        row.port_start = int(result.get("port_start") or start)
        row.port_end = int(result.get("port_end") or end)
        write_audit(
            db,
            actor=admin,
            request=request,
            action="admin.node.port_pool",
            target_type="host_node",
            target_id=row.id,
            target_name=row.name,
            detail={"port_start": row.port_start, "port_end": row.port_end},
        )
        db.commit()
        flash(
            request,
            f"{row.name} NAT 端口池已同步：{row.port_start}-{row.port_end}，共 {row.port_end-row.port_start+1} 个端口。",
            "success",
        )
    return RedirectResponse("/admin?section=nodes", status_code=303)


@app.post("/admin/nodes/{node_id}/test")
def admin_test_node(request: Request, node_id: int, csrf_token: str = Form(...)):
    validate_csrf(request, csrf_token)
    with db_session() as db:
        admin=admin_required(request,db); row=db.get(HostNode,node_id)
        if not row: raise HTTPException(404,"宿主机不存在")
        try:
            refresh_host(row); write_audit(db,actor=admin,request=request,action="admin.node.test",target_type="host_node",target_id=row.id,target_name=row.name); db.commit()
            flash(request,f"{row.name} 连接正常，Agent {row.agent_version or '-'} / API v{row.agent_api_version or '-'}。","success")
        except Exception as exc:
            db.commit(); flash(request,f"{row.name} 连接失败：{str(exc)[:180]}","error")
    return RedirectResponse("/admin?section=nodes",status_code=303)


@app.post("/admin/nodes/{node_id}/maintenance")
def admin_node_maintenance(
    request: Request,
    node_id: int,
    mode: str = Form(...),
    reason: str = Form(""),
    csrf_token: str = Form(...),
):
    validate_csrf(request, csrf_token)
    mode = (mode or "").strip().lower()
    with db_session() as db:
        admin = admin_required(request, db)
        row = db.get(HostNode, node_id)
        if not row:
            raise HTTPException(404, "宿主机不存在")
        if mode == "enter":
            row.maintenance_mode = True
            row.maintenance_reason = re.sub(r"\s+", " ", (reason or "").strip())[:255] or "计划维护"
            message = f"{row.name} 已进入维护模式：停止新 VPS 调度，现有 VPS 不受影响。"
        elif mode == "exit":
            row.maintenance_mode = False
            row.maintenance_reason = None
            message = f"{row.name} 已退出维护模式，恢复参与调度。"
        else:
            raise HTTPException(400, "维护模式参数错误")
        write_audit(
            db, actor=admin, request=request, action="admin.node.maintenance",
            target_type="host_node", target_id=row.id, target_name=row.name,
            detail={"maintenance_mode": row.maintenance_mode, "reason": row.maintenance_reason},
        )
        db.commit()
        flash(request, message, "success")
    return RedirectResponse("/admin?section=nodes", status_code=303)


@app.post("/admin/nodes/{node_id}/toggle")
def admin_toggle_node(request: Request, node_id: int, csrf_token: str = Form(...)):
    validate_csrf(request,csrf_token)
    with db_session() as db:
        admin=admin_required(request,db); row=db.get(HostNode,node_id)
        if not row: raise HTTPException(404,"宿主机不存在")
        row.enabled=not row.enabled
        if not row.enabled: row.status="disabled"
        write_audit(db,actor=admin,request=request,action="admin.node.toggle",target_type="host_node",target_id=row.id,target_name=row.name,detail={"enabled":row.enabled}); db.commit()
        flash(request,f"{row.name} 已{'启用' if row.enabled else '停用调度'}。","success")
    return RedirectResponse("/admin?section=nodes",status_code=303)


@app.post("/admin/nodes/{node_id}/plans")
async def admin_node_plans(request: Request, node_id: int):
    form=await request.form(); validate_csrf(request,str(form.get("csrf_token") or ""))
    with db_session() as db:
        admin=admin_required(request,db); row=db.get(HostNode,node_id)
        if not row: raise HTTPException(404,"宿主机不存在")
        selected={int(v) for v in form.getlist("plan_ids") if str(v).isdigit()}
        for old in db.scalars(select(PlanHost).where(PlanHost.host_id==row.id)).all(): db.delete(old)
        for plan_id in selected:
            if db.get(Plan,plan_id): db.add(PlanHost(plan_id=plan_id,host_id=row.id,enabled=True))
        write_audit(db,actor=admin,request=request,action="admin.node.plans",target_type="host_node",target_id=row.id,target_name=row.name,detail={"plan_ids":sorted(selected)}); db.commit()
        flash(request,f"{row.name} 套餐绑定已更新。未给任何节点绑定的套餐会允许调度到所有可用节点。","success")
    return RedirectResponse("/admin?section=nodes",status_code=303)


@app.post("/admin/nodes/{node_id}/adopt-unbound")
def admin_node_adopt_unbound(request: Request, node_id: int, csrf_token: str = Form(...)):
    validate_csrf(request,csrf_token)
    with db_session() as db:
        admin=admin_required(request,db); row=db.get(HostNode,node_id)
        if not row: raise HTTPException(404,"宿主机不存在")
        servers=db.scalars(select(Server).where(Server.deleted_at.is_(None),Server.host_id.is_(None),Server.provider_instance_id.is_not(None))).all()
        adopted=0
        for server in servers:
            # Only adopt when the agent confirms the exact Incus instance exists.
            try:
                data=host_request(row,"GET",f"/v1/instances/{server.provider_instance_id}/inspect",timeout=15)
                if not data.get("exists"): continue
            except Exception:
                continue
            server.host_id=row.id; server.provider="remote"; server.public_ip=row.public_ip; adopted+=1
        write_audit(db,actor=admin,request=request,action="admin.node.adopt_unbound",target_type="host_node",target_id=row.id,target_name=row.name,detail={"adopted":adopted}); db.commit()
        flash(request,f"已接管 {adopted} 台未绑定且在该节点实际存在的 VPS。","success" if adopted else "warning")
    return RedirectResponse("/admin?section=nodes",status_code=303)


@app.post("/admin/nodes/{node_id}/delete")
def admin_delete_node(request: Request, node_id: int, confirm_name: str = Form(...), csrf_token: str = Form(...)):
    validate_csrf(request,csrf_token)
    with db_session() as db:
        admin=admin_required(request,db); row=db.get(HostNode,node_id)
        if not row: raise HTTPException(404,"宿主机不存在")
        if confirm_name.strip()!=row.name:
            flash(request,"节点名称确认不匹配。","error"); return RedirectResponse("/admin?section=nodes",status_code=303)
        active=db.scalar(select(func.count()).select_from(Server).where(Server.host_id==row.id,Server.deleted_at.is_(None))) or 0
        if active:
            flash(request,f"该节点仍绑定 {active} 台有效 VPS，不能删除。请先迁移或删除这些实例。","error"); return RedirectResponse("/admin?section=nodes",status_code=303)
        name=row.name; db.delete(row); write_audit(db,actor=admin,request=request,action="admin.node.delete",target_type="host_node",target_id=node_id,target_name=name); db.commit(); flash(request,f"节点 {name} 已删除。","success")
    return RedirectResponse("/admin?section=nodes",status_code=303)

@app.post("/admin/balance")
def admin_balance(request: Request, user_id: int = Form(...), amount: str = Form(...), csrf_token: str = Form(...)):
    validate_csrf(request, csrf_token)
    try:
        delta_cents=int(Decimal(amount.strip())*100)
        if delta_cents == 0: raise ValueError
    except Exception:
        flash(request,"余额调整金额格式错误。","error"); return RedirectResponse("/admin?section=users",status_code=303)
    with db_session() as db:
        admin=admin_required(request,db); target=db.get(User,user_id)
        if not target: raise HTTPException(404,"用户不存在")
        change_balance(db,target,delta_cents,kind="admin_adjustment",reference_type="user",reference_id=target.id,note=f"管理员 {admin.username} 手动调整")
        queue_notification(db,target,title="账户余额已调整",body=f"管理员调整余额 {money(delta_cents)}，当前余额 {money(target.balance_cents)}。",kind="billing",severity="info",event_key=f"admin-balance:{target.id}:{int(datetime.utcnow().timestamp())}")
        write_audit(db,actor=admin,request=request,action="admin.balance.adjust",target_type="user",target_id=target.id,target_name=target.username,detail={"delta_cents":delta_cents,"balance_after":target.balance_cents})
        db.commit(); flash(request,f"{target.username} 余额已调整 {money(delta_cents)}，当前 {money(target.balance_cents)}。","success")
    return RedirectResponse("/admin?section=users",status_code=303)


@app.post("/admin/users/{user_id}/toggle")
def admin_toggle_user(request: Request,user_id:int,csrf_token:str=Form(...)):
    validate_csrf(request,csrf_token)
    with db_session() as db:
        admin=admin_required(request,db); target=db.get(User,user_id)
        if not target: raise HTTPException(404,"用户不存在")
        if target.id==admin.id: flash(request,"不能停用当前管理员账号。","error"); return RedirectResponse("/admin?section=users",status_code=303)
        target.is_active=not target.is_active
        revoked=0
        if not target.is_active:
            rows=db.scalars(select(LoginSession).where(LoginSession.user_id==target.id,LoginSession.revoked_at.is_(None))).all()
            for row in rows: row.revoked_at=datetime.utcnow(); revoked+=1
        write_audit(db,actor=admin,request=request,action="admin.user.toggle",target_type="user",target_id=target.id,target_name=target.username,detail={"active":target.is_active,"revoked_sessions":revoked})
        db.commit(); flash(request,f"{target.username} 已{'启用' if target.is_active else '停用'}。","success")
    return RedirectResponse("/admin?section=users",status_code=303)


@app.post("/admin/plans")
def admin_create_plan(
    request: Request,
    name: str = Form(...),
    cpu: int = Form(...),
    memory_mb: int = Form(...),
    disk_gb: int = Form(...),
    bandwidth_mbps: int = Form(...),
    traffic_gb: int = Form(...),
    port_count: int = Form(...),
    virtualization_type: str = Form("lxc"),
    monthly_price: str = Form(...),
    traffic_reset_price: str = Form(...),
    stock_limit: int = Form(0),
    sort_order: int = Form(100),
    homepage_visible: str = Form("false"),
    homepage_sort_order: int = Form(100),
    is_recommended: str = Form("false"),
    recommendation_label: str = Form("推荐"),
    csrf_token: str = Form(...),
):
    validate_csrf(request, csrf_token)
    try:
        price = int(Decimal(monthly_price) * 100)
        reset_price = int(Decimal(traffic_reset_price) * 100)
    except Exception:
        price = -1
        reset_price = -1

    clean_name = name.strip()
    virtualization_type = (virtualization_type or "lxc").strip().lower()
    clean_label = (recommendation_label or "").strip()[:32] or "推荐"
    if virtualization_type == "kvm" and (memory_mb < 512 or disk_gb < 4):
        flash(request, "KVM 套餐最低需要 512 MB 内存和 4 GB 磁盘。", "error")
        return RedirectResponse("/admin?section=plans", status_code=303)
    if (
        not clean_name or len(clean_name) > 80 or cpu < 1 or memory_mb < 64
        or disk_gb < 1 or virtualization_type not in {"lxc", "kvm"}
        or price < 0 or reset_price <= 0 or stock_limit < 0
        or not (0 <= sort_order <= 1000000)
        or not (0 <= homepage_sort_order <= 1000000)
    ):
        flash(request, "套餐参数无效。", "error")
        return RedirectResponse("/admin?section=plans", status_code=303)

    with db_session() as db:
        admin = admin_required(request, db)
        if db.scalar(select(Plan).where(Plan.name == clean_name)):
            flash(request, "套餐名称已存在。", "error")
            return RedirectResponse("/admin?section=plans", status_code=303)

        row = Plan(
            name=clean_name, cpu=cpu, memory_mb=memory_mb, disk_gb=disk_gb,
            bandwidth_mbps=max(0, bandwidth_mbps),
            traffic_gb=max(0, traffic_gb),
            port_count=max(0, port_count),
            virtualization_type=virtualization_type,
            monthly_price_cents=price,
            traffic_reset_price_cents=reset_price,
            stock_limit=stock_limit,
            sort_order=sort_order,
            homepage_visible=str(homepage_visible).lower() == "true",
            homepage_sort_order=homepage_sort_order,
            is_recommended=str(is_recommended).lower() == "true",
            recommendation_label=clean_label,
            is_active=True,
        )
        db.add(row)
        db.flush()
        write_audit(
            db, actor=admin, request=request, action="admin.plan.create",
            target_type="plan", target_id=row.id, target_name=row.name,
            detail={
                "virtualization_type": row.virtualization_type,
                "homepage_visible": row.homepage_visible,
                "homepage_sort_order": row.homepage_sort_order,
                "catalog_sort_order": row.sort_order,
                "is_recommended": row.is_recommended,
                "recommendation_label": row.recommendation_label,
            },
        )
        db.commit()
        flash(request, f"套餐 {row.name} 已创建。", "success")
    return RedirectResponse("/admin?section=plans", status_code=303)


@app.post("/admin/plans/{plan_id}/update")
def admin_update_plan(
    request: Request,
    plan_id: int,
    name: str = Form(...),
    cpu: int = Form(...),
    memory_mb: int = Form(...),
    disk_gb: int = Form(...),
    bandwidth_mbps: int = Form(...),
    traffic_gb: int = Form(...),
    port_count: int = Form(...),
    virtualization_type: str = Form("lxc"),
    monthly_price: str = Form(...),
    traffic_reset_price: str = Form(...),
    stock_limit: int = Form(...),
    sort_order: int = Form(...),
    homepage_visible: str = Form("false"),
    homepage_sort_order: int = Form(100),
    is_recommended: str = Form("false"),
    recommendation_label: str = Form("推荐"),
    csrf_token: str = Form(...),
):
    validate_csrf(request, csrf_token)
    try:
        price = int(Decimal(monthly_price) * 100)
        reset_price = int(Decimal(traffic_reset_price) * 100)
    except Exception:
        price = -1
        reset_price = -1

    clean_name = name.strip()
    virtualization_type = (virtualization_type or "lxc").strip().lower()
    clean_label = (recommendation_label or "").strip()[:32] or "推荐"

    with db_session() as db:
        admin = admin_required(request, db)
        row = db.get(Plan, plan_id)
        if not row:
            raise HTTPException(404, "套餐不存在")

        if virtualization_type == "kvm" and (memory_mb < 512 or disk_gb < 4):
            flash(request, "KVM 套餐最低需要 512 MB 内存和 4 GB 磁盘。", "error")
            return RedirectResponse("/admin?section=plans", status_code=303)
        if (
            not clean_name or len(clean_name) > 80 or price < 0 or reset_price <= 0 or cpu < 1
            or memory_mb < 64 or disk_gb < 1 or virtualization_type not in {"lxc", "kvm"} or stock_limit < 0
            or not (0 <= sort_order <= 1000000)
            or not (0 <= homepage_sort_order <= 1000000)
        ):
            flash(request, "套餐参数无效。", "error")
            return RedirectResponse("/admin?section=plans", status_code=303)

        duplicate = db.scalar(select(Plan).where(Plan.name == clean_name, Plan.id != row.id))
        if duplicate:
            flash(request, "套餐名称已被使用。", "error")
            return RedirectResponse("/admin?section=plans", status_code=303)

        before = {
            "name": row.name, "cpu": row.cpu, "memory_mb": row.memory_mb,
            "disk_gb": row.disk_gb, "bandwidth": row.bandwidth_mbps,
            "traffic": row.traffic_gb, "ports": row.port_count, "virtualization_type": row.virtualization_type,
            "price": row.monthly_price_cents, "traffic_reset_price": row.traffic_reset_price_cents, "stock": row.stock_limit,
            "catalog_sort_order": row.sort_order,
            "homepage_visible": row.homepage_visible,
            "homepage_sort_order": row.homepage_sort_order,
            "is_recommended": row.is_recommended,
            "recommendation_label": row.recommendation_label,
        }

        row.name = clean_name
        row.cpu = cpu
        row.memory_mb = memory_mb
        row.disk_gb = disk_gb
        row.bandwidth_mbps = max(0, bandwidth_mbps)
        row.traffic_gb = max(0, traffic_gb)
        row.port_count = max(0, port_count)
        row.virtualization_type = virtualization_type
        row.monthly_price_cents = price
        row.traffic_reset_price_cents = reset_price
        row.stock_limit = stock_limit
        row.sort_order = sort_order
        row.homepage_visible = str(homepage_visible).lower() == "true"
        row.homepage_sort_order = homepage_sort_order
        row.is_recommended = str(is_recommended).lower() == "true"
        row.recommendation_label = clean_label

        after = {
            "name": row.name, "cpu": row.cpu, "memory_mb": row.memory_mb,
            "disk_gb": row.disk_gb, "bandwidth": row.bandwidth_mbps,
            "traffic": row.traffic_gb, "ports": row.port_count, "virtualization_type": row.virtualization_type,
            "price": row.monthly_price_cents, "traffic_reset_price": row.traffic_reset_price_cents, "stock": row.stock_limit,
            "catalog_sort_order": row.sort_order,
            "homepage_visible": row.homepage_visible,
            "homepage_sort_order": row.homepage_sort_order,
            "is_recommended": row.is_recommended,
            "recommendation_label": row.recommendation_label,
        }
        write_audit(
            db, actor=admin, request=request, action="admin.plan.update",
            target_type="plan", target_id=row.id, target_name=row.name,
            detail={"before": before, "after": after},
        )
        db.commit()
        flash(request, f"套餐 {row.name} 已保存。", "success")
    return RedirectResponse("/admin?section=plans", status_code=303)



@app.post("/admin/plans/{plan_id}/toggle")
def admin_toggle_plan(request:Request,plan_id:int,csrf_token:str=Form(...)):
    validate_csrf(request,csrf_token)
    with db_session() as db:
        admin=admin_required(request,db); row=db.get(Plan,plan_id)
        if not row: raise HTTPException(404,"套餐不存在")
        row.is_active=not row.is_active; write_audit(db,actor=admin,request=request,action="admin.plan.toggle",target_type="plan",target_id=row.id,target_name=row.name,detail={"active":row.is_active}); db.commit()
        flash(request,f"{row.name} 已{'上架' if row.is_active else '下架'}。","success")
    return RedirectResponse("/admin?section=plans",status_code=303)



@app.post("/admin/servers/{server_id}/resources")
def admin_resize_server_resources(
    request: Request,
    server_id: int,
    cpu: int = Form(...),
    memory_mb: int = Form(...),
    disk_gb: int = Form(...),
    csrf_token: str = Form(...),
):
    validate_csrf(request, csrf_token)

    if cpu < 1 or cpu > 128:
        raise HTTPException(400, "CPU 核心数无效")
    if memory_mb < 64 or memory_mb > 1_048_576:
        raise HTTPException(400, "内存参数无效")
    if disk_gb < 1 or disk_gb > 65_536:
        raise HTTPException(400, "磁盘参数无效")

    with db_session() as db:
        admin = admin_required(request, db)
        server = db.get(Server, server_id)

        if not server or server.deleted_at is not None:
            raise HTTPException(404, "服务器不存在")
        if not server.provider_instance_id:
            flash(request, "实例尚未完成开通，不能调整资源。", "error")
            return RedirectResponse("/admin?section=servers", status_code=303)
        if server.status not in {"running", "stopped"}:
            flash(request, f"当前实例状态 {server.status} 不允许调整资源。", "error")
            return RedirectResponse("/admin?section=servers", status_code=303)

        virtualization_type = str(server.virtualization_type or "lxc").strip().lower()
        if virtualization_type == "kvm" and (memory_mb < 512 or disk_gb < 4):
            flash(request, "KVM 实例最低需要 512 MB 内存和 4 GB 磁盘。", "error")
            return RedirectResponse("/admin?section=servers", status_code=303)

        current_disk = int(server.disk_gb or 0)
        if current_disk and disk_gb < current_disk:
            flash(request, f"磁盘只能扩容，当前为 {current_disk} GB，不能缩小。", "error")
            return RedirectResponse("/admin?section=servers", status_code=303)

        old = {
            "cpu": int(server.cpu or 0),
            "memory_mb": int(server.memory_mb or 0),
            "disk_gb": current_disk,
        }

        if old == {"cpu": cpu, "memory_mb": memory_mb, "disk_gb": disk_gb}:
            flash(request, f"{server.name} 资源配置没有变化。", "info")
            return RedirectResponse("/admin?section=servers", status_code=303)

        try:
            actual = provider.resize_resources(
                server.provider_instance_id,
                cpu,
                memory_mb,
                disk_gb,
            )

            server.cpu = int(actual.get("cpu") or cpu)
            server.memory_mb = int(actual.get("memory_mb") or memory_mb)
            server.disk_gb = int(actual.get("disk_gb") or disk_gb)
            server.reconcile_status = "ok"
            server.reconcile_message = None
            server.reconciled_at = datetime.utcnow()

            write_audit(
                db,
                actor=admin,
                request=request,
                action="admin.server.resources",
                target_type="server",
                target_id=server.id,
                target_name=server.name,
                detail={
                    "before": old,
                    "requested": {"cpu": cpu, "memory_mb": memory_mb, "disk_gb": disk_gb},
                    "applied": {
                        "cpu": server.cpu,
                        "memory_mb": server.memory_mb,
                        "disk_gb": server.disk_gb,
                    },
                },
            )
            db.commit()
            flash(
                request,
                f"{server.name} 资源已调整为 {server.cpu}C / {server.memory_mb}MB / {server.disk_gb}GB。",
                "success",
            )
        except Exception as exc:
            db.rollback()
            flash(request, f"资源调整失败：{str(exc)[:220]}", "error")

    return RedirectResponse("/admin?section=servers", status_code=303)

@app.post("/admin/servers/{server_id}/bandwidth")
def admin_set_bandwidth(request:Request,server_id:int,bandwidth_mbps:int=Form(...),csrf_token:str=Form(...)):
    validate_csrf(request,csrf_token)
    if bandwidth_mbps<0 or bandwidth_mbps>10000: raise HTTPException(400,"带宽参数无效")
    with db_session() as db:
        admin=admin_required(request,db); server=db.get(Server,server_id)
        if not server or server.deleted_at is not None: raise HTTPException(404,"服务器不存在")
        old=server.bandwidth_mbps or 0; server.bandwidth_mbps=bandwidth_mbps
        try:
            if server.provider==PROVIDER_NAME and server.provider_instance_id: provider.set_bandwidth(server.provider_instance_id,effective_bandwidth_mbps(server))
            write_audit(db,actor=admin,request=request,action="admin.server.bandwidth",target_type="server",target_id=server.id,target_name=server.name,detail={"old":old,"new":bandwidth_mbps}); db.commit(); flash(request,f"{server.name} 带宽已调整。","success")
        except Exception as exc:
            db.rollback(); flash(request,f"带宽调整失败：{str(exc)[:180]}","error")
    return RedirectResponse("/admin?section=servers",status_code=303)


@app.post("/admin/servers/{server_id}/traffic/add")
def admin_add_traffic_quota(request:Request,server_id:int,extra_gb:int=Form(...),csrf_token:str=Form(...)):
    validate_csrf(request,csrf_token)
    if extra_gb<1 or extra_gb>1_000_000: raise HTTPException(400,"流量额度无效")
    with db_session() as db:
        admin=admin_required(request,db); server=db.get(Server,server_id)
        if not server or server.deleted_at is not None: raise HTTPException(404,"服务器不存在")
        server.traffic_bonus_gb=int(server.traffic_bonus_gb or 0)+extra_gb; server.traffic_throttle_exempt=False
        err=None
        try: enforce_traffic_policy(server,provider)
        except Exception as exc: err=str(exc)[:160]
        write_audit(db,actor=admin,request=request,action="admin.server.traffic.add",target_type="server",target_id=server.id,target_name=server.name,detail={"extra_gb":extra_gb}); db.commit()
        flash(request,f"{server.name} 当前周期增加 {extra_gb} GB。"+(f" Provider 应用失败：{err}" if err else ""),"error" if err else "success")
    return RedirectResponse("/admin?section=servers",status_code=303)


@app.post("/admin/servers/{server_id}/traffic/quota")
def admin_set_traffic_quota(
    request: Request,
    server_id: int,
    traffic_gb: int = Form(...),
    reset_used: str | None = Form(None),
    confirm_overused: str | None = Form(None),
    csrf_token: str = Form(...),
):
    validate_csrf(request, csrf_token)
    if traffic_gb < 0 or traffic_gb > 1_000_000:
        raise HTTPException(400, "流量额度无效")
    with db_session() as db:
        admin = admin_required(request, db)
        server = db.get(Server, server_id)
        if not server or server.deleted_at is not None:
            raise HTTPException(404, "服务器不存在")
        before = {
            "traffic_gb": int(server.traffic_gb or 0),
            "traffic_bonus_gb": int(server.traffic_bonus_gb or 0),
            "used_bytes": traffic_used_bytes(server),
        }
        would_exceed = traffic_gb > 0 and before["used_bytes"] >= traffic_gb * (1024 ** 3)
        if reset_used != "true" and would_exceed and confirm_overused != "true":
            flash(request, "新的流量额度低于或等于当前已用流量。请勾选确认后再保存；保存后会立即进入超额限速策略。", "error")
            return RedirectResponse("/admin?section=servers", status_code=303)
        if reset_used == "true":
            reset_cycle(server, datetime.utcnow())
        server.traffic_gb = int(traffic_gb)
        # Direct quota editing defines the full quota. Clear legacy one-cycle bonus
        # so the number displayed to the administrator is exactly what is enforced.
        server.traffic_bonus_gb = 0
        server.traffic_throttle_exempt = False
        provider_error = None
        try:
            enforce_traffic_policy(server, provider)
        except Exception as exc:
            provider_error = str(exc)[:180]
        write_audit(
            db, actor=admin, request=request, action="admin.server.traffic.quota",
            target_type="server", target_id=server.id, target_name=server.name,
            detail={
                "before": before,
                "traffic_gb": int(traffic_gb),
                "reset_used": reset_used == "true",
                "used_bytes_after": traffic_used_bytes(server),
            },
        )
        db.commit()
        text = f"{server.name} 流量额度已调整为 {'不限' if traffic_gb == 0 else str(traffic_gb) + ' GB'}。"
        if provider_error:
            text += f" 限速策略同步失败：{provider_error}"
        flash(request, text, "warning" if provider_error else "success")
    return RedirectResponse("/admin?section=servers", status_code=303)


@app.post("/admin/servers/{server_id}/traffic/cycle")
def admin_set_traffic_cycle(
    request: Request,
    server_id: int,
    cycle_mode: str = Form(...),
    cycle_day: int = Form(1),
    confirm_reset: str = Form(""),
    csrf_token: str = Form(...),
):
    validate_csrf(request, csrf_token)
    cycle_mode = (cycle_mode or "").strip().lower()
    if cycle_mode not in {"rolling30", "monthly"}:
        flash(request, "流量周期模式无效。", "error")
        return RedirectResponse("/admin?section=servers", status_code=303)
    cycle_day = max(1, min(28, int(cycle_day or 1)))
    if confirm_reset != "true":
        flash(request, "修改流量周期会立即重置本周期已用流量，请勾选确认。", "error")
        return RedirectResponse("/admin?section=servers", status_code=303)

    with db_session() as db:
        admin = admin_required(request, db)
        server = db.get(Server, server_id)
        if not server or server.deleted_at is not None:
            raise HTTPException(404, "服务器不存在")
        before = {
            "mode": server.traffic_cycle_mode,
            "day": server.traffic_cycle_day,
            "cycle_start": server.traffic_cycle_start.isoformat() if server.traffic_cycle_start else None,
            "cycle_end": server.traffic_cycle_end.isoformat() if server.traffic_cycle_end else None,
            "used_bytes": traffic_used_bytes(server),
        }
        server.traffic_cycle_mode = cycle_mode
        server.traffic_cycle_day = cycle_day
        reset_cycle(server, datetime.utcnow())
        provider_error = None
        try:
            enforce_traffic_policy(server, provider)
        except Exception as exc:
            provider_error = str(exc)[:180]
        write_audit(
            db, actor=admin, request=request, action="admin.server.traffic.cycle",
            target_type="server", target_id=server.id, target_name=server.name,
            detail={
                "before": before,
                "after": {"mode": cycle_mode, "day": cycle_day, "cycle_end": server.traffic_cycle_end.isoformat()},
            },
        )
        db.commit()
        message = f"{server.name} 流量周期已改为 {traffic_cycle_mode_label(server)}，本周期用量已重置。"
        if provider_error:
            message += f" 带宽策略同步失败：{provider_error}"
        flash(request, message, "warning" if provider_error else "success")
    return RedirectResponse("/admin?section=servers", status_code=303)


@app.post("/admin/servers/{server_id}/traffic/reset")
def admin_reset_traffic_cycle(request:Request,server_id:int,csrf_token:str=Form(...)):
    validate_csrf(request,csrf_token)
    with db_session() as db:
        admin=admin_required(request,db); server=db.get(Server,server_id)
        if not server or server.deleted_at is not None: raise HTTPException(404,"服务器不存在")
        reset_cycle(server,datetime.utcnow()); server.traffic_throttle_exempt=False
        try:
            if server.provider==PROVIDER_NAME and server.provider_instance_id: provider.set_bandwidth(server.provider_instance_id,configured_bandwidth_mbps(server))
            server.traffic_throttled=False; server.traffic_throttled_at=None
        except Exception: pass
        write_audit(db,actor=admin,request=request,action="admin.server.traffic.reset",target_type="server",target_id=server.id,target_name=server.name); db.commit(); flash(request,f"{server.name} 流量周期已重置。","success")
    return RedirectResponse("/admin?section=servers",status_code=303)


@app.post("/admin/servers/{server_id}/traffic/unthrottle")
def admin_unthrottle_traffic(request:Request,server_id:int,csrf_token:str=Form(...)):
    validate_csrf(request,csrf_token)
    with db_session() as db:
        admin=admin_required(request,db); server=db.get(Server,server_id)
        if not server or server.deleted_at is not None: raise HTTPException(404,"服务器不存在")
        server.traffic_throttle_exempt=True
        try:
            provider.set_bandwidth(server.provider_instance_id,configured_bandwidth_mbps(server)); server.traffic_throttled=False; server.traffic_throttled_at=None
            write_audit(db,actor=admin,request=request,action="admin.server.traffic.unthrottle",target_type="server",target_id=server.id,target_name=server.name); db.commit(); flash(request,f"{server.name} 本周期已解除限速。","success")
        except Exception as exc: db.rollback(); flash(request,f"解除限速失败：{str(exc)[:180]}","error")
    return RedirectResponse("/admin?section=servers",status_code=303)


@app.post("/admin/servers/{server_id}/traffic/auto")
def admin_resume_traffic_policy(request:Request,server_id:int,csrf_token:str=Form(...)):
    validate_csrf(request,csrf_token)
    with db_session() as db:
        admin=admin_required(request,db); server=db.get(Server,server_id)
        if not server or server.deleted_at is not None: raise HTTPException(404,"服务器不存在")
        server.traffic_throttle_exempt=False; err=None
        try: enforce_traffic_policy(server,provider)
        except Exception as exc: err=str(exc)[:160]
        write_audit(db,actor=admin,request=request,action="admin.server.traffic.auto",target_type="server",target_id=server.id,target_name=server.name); db.commit(); flash(request,f"{server.name} 已恢复自动流量策略。"+(f" Provider 应用失败：{err}" if err else ""),"error" if err else "success")
    return RedirectResponse("/admin?section=servers",status_code=303)


@app.post("/admin/servers/{server_id}/expiry")
def admin_set_server_expiry(
    request: Request,
    server_id: int,
    mode: str = Form(...),
    days: int = Form(0),
    expires_at: str = Form(""),
    confirm_expired: str = Form(""),
    csrf_token: str = Form(...),
):
    validate_csrf(request, csrf_token)
    mode = (mode or "").strip().lower()
    with db_session() as db:
        admin = admin_required(request, db)
        server = db.get(Server, server_id)
        if not server or server.deleted_at is not None:
            raise HTTPException(404, "服务器不存在")
        old_expiry = server.expires_at
        now = datetime.utcnow()
        if mode == "add":
            if days < 1 or days > 3650:
                flash(request, "续期天数应在 1-3650 天之间。", "error")
                return RedirectResponse("/admin?section=servers", status_code=303)
            base = old_expiry if old_expiry and old_expiry > now else now
            new_expiry = base + timedelta(days=days)
        elif mode == "set":
            try:
                new_expiry = parse_local_datetime(expires_at)
            except Exception:
                new_expiry = None
            if new_expiry is None:
                flash(request, "请输入有效的到期日期和时间。", "error")
                return RedirectResponse("/admin?section=servers", status_code=303)
            if new_expiry <= now and confirm_expired.strip() != "EXPIRE NOW":
                flash(request, "新的到期时间早于当前时间。如确实需要立即到期，请输入 EXPIRE NOW 确认。", "error")
                return RedirectResponse("/admin?section=servers", status_code=303)
        else:
            raise HTTPException(400, "不支持的到期时间调整方式")

        server.expires_at = new_expiry
        stopped = False
        restarted = False
        provider_error = None
        cancelled_deletes = 0

        if new_expiry > now:
            cancelled_deletes, blocking_delete = cancel_pending_expiry_delete(db, server)
            if blocking_delete:
                flash(request, "服务器存在手动删除任务或自动删除任务已经开始执行，不能仅通过修改到期时间撤销。请先确认任务状态。", "error")
                return RedirectResponse("/admin?section=servers", status_code=303)
            restarted, provider_error = resume_after_renewal(db, provider, PROVIDER_NAME, server)
        else:
            cfg = lifecycle_config(db)
            grace_end = new_expiry + timedelta(days=cfg["grace_days"])
            if cfg["auto_stop"] and now >= grace_end and server.status == "running" and server.provider == PROVIDER_NAME and server.provider_instance_id:
                try:
                    server.status = provider.power_action(server.provider_instance_id, "stop")
                    server.expiry_suspended_at = server.expiry_suspended_at or now
                    stopped = True
                except Exception as exc:
                    provider_error = str(exc)[:180]

        write_audit(
            db, actor=admin, request=request, action="admin.server.expiry",
            target_type="server", target_id=server.id, target_name=server.name,
            detail={
                "mode": mode, "days": days if mode == "add" else None,
                "before": old_expiry.isoformat() if old_expiry else None,
                "after": new_expiry.isoformat(), "stopped": stopped,
                "restarted": restarted, "cancelled_expiry_delete_jobs": cancelled_deletes,
            },
        )
        db.commit()
        local_expiry = local_dt(new_expiry).strftime("%Y-%m-%d %H:%M")
        msg = f"{server.name} 到期时间已调整为 {local_expiry}。"
        if stopped:
            msg += " 已按当前生命周期策略停止服务。"
        if restarted:
            msg += " 到期停机实例已自动恢复。"
        if provider_error:
            msg += f" Provider 操作失败：{provider_error}"
        flash(request, msg, "warning" if provider_error else "success")
    return RedirectResponse("/admin?section=servers", status_code=303)


@app.post("/admin/servers/{server_id}/extend")
def admin_extend_server(request:Request,server_id:int,csrf_token:str=Form(...)):
    # Backwards-compatible legacy endpoint; the v1.0.0 UI uses /expiry.
    validate_csrf(request,csrf_token)
    with db_session() as db:
        admin=admin_required(request,db); server=db.get(Server,server_id)
        if not server or server.deleted_at is not None: raise HTTPException(404,"服务器不存在")
        base=server.expires_at if server.expires_at and server.expires_at>datetime.utcnow() else datetime.utcnow(); old=server.expires_at; server.expires_at=base+timedelta(days=30)
        write_audit(db,actor=admin,request=request,action="admin.server.extend.legacy",target_type="server",target_id=server.id,target_name=server.name,detail={"days":30,"before":old.isoformat() if old else None,"after":server.expires_at.isoformat()}); db.commit(); flash(request,f"{server.name} 已延长 30 天。","success")
    return RedirectResponse("/admin?section=servers",status_code=303)


@app.post("/admin/servers/{server_id}/delete")
def admin_delete_server(request:Request,server_id:int,confirm_name:str=Form(...),csrf_token:str=Form(...)):
    validate_csrf(request,csrf_token)
    with db_session() as db:
        admin=admin_required(request,db); server=db.get(Server,server_id)
        if not server or server.deleted_at is not None: raise HTTPException(404,"服务器不存在")
        if confirm_name.strip()!=server.name: flash(request,"删除确认名称不正确。","error"); return RedirectResponse("/admin?section=servers",status_code=303)
        existing=db.scalar(select(Job).where(Job.server_id==server.id,Job.job_type=="delete_server",Job.status.in_(["pending","running"])))
        if existing: flash(request,f"删除任务 #{existing.id} 已在执行。","info"); return RedirectResponse("/admin?section=servers",status_code=303)
        job=enqueue_job(db,"delete_server",user_id=server.user_id,server_id=server.id,payload={"requested_by":"admin"})
        write_audit(db,actor=admin,request=request,action="admin.server.delete.queue",target_type="server",target_id=server.id,target_name=server.name,detail={"job_id":job.id}); db.commit(); flash(request,f"{server.name} 删除任务 #{job.id} 已进入队列。","success")
    return RedirectResponse("/admin?section=servers",status_code=303)


@app.post("/admin/servers/{server_id}/reconcile")
def admin_reconcile_server(request:Request,server_id:int,csrf_token:str=Form(...)):
    validate_csrf(request,csrf_token)
    with db_session() as db:
        admin=admin_required(request,db); server=db.get(Server,server_id)
        if not server or server.deleted_at is not None: raise HTTPException(404,"服务器不存在")
        try:
            result=reconcile_one_server(db,provider,server,repair=True); write_audit(db,actor=admin,request=request,action="admin.server.reconcile",target_type="server",target_id=server.id,target_name=server.name,detail=result); db.commit(); flash(request,f"{server.name} 状态校验完成：{server.reconcile_status}。","success")
        except Exception as exc: db.rollback(); flash(request,f"校验失败：{str(exc)[:180]}","error")
    return RedirectResponse("/admin?section=servers",status_code=303)


@app.post("/admin/reconcile-all")
def admin_reconcile_all(request:Request,csrf_token:str=Form(...)):
    validate_csrf(request,csrf_token)
    with db_session() as db: admin=admin_required(request,db); write_audit(db,actor=admin,request=request,action="admin.reconcile_all.request",target_type="system"); db.commit()
    try: ok_count,attention_count=reconcile_all(provider,PROVIDER_NAME,repair=True); flash(request,f"全量校验完成：正常/已修复 {ok_count} 台，需要关注 {attention_count} 台。","success" if attention_count==0 else "warning")
    except Exception as exc: flash(request,f"全量校验失败：{str(exc)[:180]}","error")
    return RedirectResponse("/admin?section=servers",status_code=303)


@app.post("/admin/system-images")
def admin_add_system_image(request:Request,name:str=Form(...),alias:str=Form(...),csrf_token:str=Form(...)):
    validate_csrf(request,csrf_token); name=name.strip(); alias=alias.strip()
    if not name or not alias.startswith("images:"): flash(request,"名称不能为空，镜像别名必须以 images: 开头。","error"); return RedirectResponse("/admin?section=images",status_code=303)
    with db_session() as db:
        admin=admin_required(request,db)
        if db.scalar(select(SystemImage).where(or_(SystemImage.name==name,SystemImage.alias==alias))): flash(request,"系统名称或镜像别名已经存在。","error"); return RedirectResponse("/admin?section=images",status_code=303)
        row=SystemImage(name=name,alias=alias,family="apt",is_active=True,sort_order=100); db.add(row); db.flush(); write_audit(db,actor=admin,request=request,action="admin.image.create",target_type="system_image",target_id=row.id,target_name=row.name,detail={"alias":row.alias}); db.commit(); flash(request,f"系统镜像 {name} 已添加。","success")
    return RedirectResponse("/admin?section=images",status_code=303)


@app.post("/admin/system-images/{image_id}/toggle")
def admin_toggle_system_image(request:Request,image_id:int,csrf_token:str=Form(...)):
    validate_csrf(request,csrf_token)
    with db_session() as db:
        admin=admin_required(request,db); row=db.get(SystemImage,image_id)
        if not row: raise HTTPException(404,"系统镜像不存在")
        row.is_active=not row.is_active; write_audit(db,actor=admin,request=request,action="admin.image.toggle",target_type="system_image",target_id=row.id,target_name=row.name,detail={"active":row.is_active}); db.commit(); flash(request,f"{row.name} 已{'启用' if row.is_active else '停用'}。","success")
    return RedirectResponse("/admin?section=images",status_code=303)


@app.post("/admin/announcements")
def admin_create_announcement(request: Request, title: str = Form(...), body: str = Form(...), status: str = Form("draft"),
                              is_pinned: str | None = Form(None), show_on_login: str | None = Form(None), csrf_token: str = Form(...)):
    validate_csrf(request, csrf_token)
    title, body = title.strip()[:160], body.strip()[:12000]
    if not title or not body:
        flash(request, "公告标题和内容不能为空。", "error")
        return RedirectResponse("/admin?section=announcements", status_code=303)
    published = status == "published"
    with db_session() as db:
        admin = admin_required(request, db)
        login_flag = bool(show_on_login) and published
        if login_flag:
            db.execute(text("UPDATE announcements SET show_on_login = 0"))
        row = Announcement(title=title, body=body, is_published=published, is_pinned=bool(is_pinned), show_on_login=login_flag,
                           published_at=datetime.utcnow() if published else None, updated_at=datetime.utcnow())
        db.add(row); db.flush()
        write_audit(db, actor=admin, request=request, action="announcement.create", target_type="announcement", target_id=row.id, target_name=row.title, detail={"published": published, "login": login_flag})
        db.commit()
    flash(request, "公告已创建并发布。" if published else "公告草稿已创建。", "success")
    return RedirectResponse("/admin?section=announcements", status_code=303)


@app.post("/admin/announcements/{announcement_id}/update")
def admin_update_announcement(request: Request, announcement_id: int, title: str = Form(...), body: str = Form(...), status: str = Form("draft"),
                              is_pinned: str | None = Form(None), show_on_login: str | None = Form(None), csrf_token: str = Form(...)):
    validate_csrf(request, csrf_token)
    title, body = title.strip()[:160], body.strip()[:12000]
    if not title or not body:
        flash(request, "公告标题和内容不能为空。", "error")
        return RedirectResponse("/admin?section=announcements", status_code=303)
    with db_session() as db:
        admin = admin_required(request, db); row = db.get(Announcement, announcement_id)
        if not row: raise HTTPException(404, "公告不存在")
        content_changed = row.title != title or row.body != body
        published = status == "published"; login_flag = bool(show_on_login) and published
        if login_flag:
            db.execute(text("UPDATE announcements SET show_on_login = 0 WHERE id != :id"), {"id": row.id})
        row.title, row.body, row.is_published, row.is_pinned, row.show_on_login = title, body, published, bool(is_pinned), login_flag
        if published and row.published_at is None: row.published_at = datetime.utcnow()
        row.updated_at = datetime.utcnow()
        if content_changed: db.execute(delete(AnnouncementRead).where(AnnouncementRead.announcement_id == row.id))
        write_audit(db, actor=admin, request=request, action="announcement.update", target_type="announcement", target_id=row.id, target_name=row.title, detail={"published": published, "login": login_flag, "content_changed": content_changed})
        db.commit()
    flash(request, "公告已更新。内容有变化时，用户会重新看到未读状态。", "success")
    return RedirectResponse("/admin?section=announcements", status_code=303)


@app.post("/admin/announcements/{announcement_id}/delete")
def admin_delete_announcement(request: Request, announcement_id: int, csrf_token: str = Form(...)):
    validate_csrf(request, csrf_token)
    with db_session() as db:
        admin = admin_required(request, db)
        row = db.get(Announcement, announcement_id)
        if not row:
            raise HTTPException(404, "公告不存在")
        title = row.title
        db.execute(delete(AnnouncementRead).where(AnnouncementRead.announcement_id == row.id))
        write_audit(db, actor=admin, request=request, action="announcement.delete", target_type="announcement", target_id=row.id, target_name=title)
        db.delete(row)
        db.commit()
    flash(request, f"公告「{title}」已删除。", "success")
    return RedirectResponse("/admin?section=announcements", status_code=303)


@app.post("/admin/settings")
async def admin_update_settings(request: Request):
    form = await request.form()
    validate_csrf(request, str(form.get("csrf_token") or ""))
    scope = str(form.get("scope") or "").strip().lower()

    with db_session() as db:
        admin = admin_required(request, db)
        values: dict[str, str] = {}
        secret_keys_changed: list[str] = []
        warnings: list[str] = []

        redirect_section = "payments" if scope == "payments" else ("notifications" if scope == "notifications" else "settings")
        try:
            if scope == "homepage":
                def home_text(key: str, limit: int) -> str:
                    value = str(form.get(key) or "").strip()
                    if not value:
                        value = HOME_SETTING_DEFAULTS[key]
                    return value[:limit]

                values = {
                    "home_eyebrow": home_text("home_eyebrow", 100),
                    "home_title": home_text("home_title", 120),
                    "home_highlight": home_text("home_highlight", 80),
                    "home_description": home_text("home_description", 600),
                    "home_guest_primary_label": home_text("home_guest_primary_label", 24),
                    "home_guest_secondary_label": home_text("home_guest_secondary_label", 24),
                    "home_user_primary_label": home_text("home_user_primary_label", 24),
                    "home_user_secondary_label": home_text("home_user_secondary_label", 24),
                    "home_feature_tags": home_text("home_feature_tags", 300),
                    "home_overview_title": home_text("home_overview_title", 100),
                    "home_overview_description": home_text("home_overview_description", 400),
                    "home_capability_1_title": home_text("home_capability_1_title", 40),
                    "home_capability_1_description": home_text("home_capability_1_description", 160),
                    "home_capability_2_title": home_text("home_capability_2_title", 40),
                    "home_capability_2_description": home_text("home_capability_2_description", 160),
                    "home_capability_3_title": home_text("home_capability_3_title", 40),
                    "home_capability_3_description": home_text("home_capability_3_description", 160),
                    "home_capability_4_title": home_text("home_capability_4_title", 40),
                    "home_capability_4_description": home_text("home_capability_4_description", 160),
                    "home_signal_1_title": home_text("home_signal_1_title", 40),
                    "home_signal_1_description": home_text("home_signal_1_description", 140),
                    "home_signal_2_title": home_text("home_signal_2_title", 40),
                    "home_signal_2_description": home_text("home_signal_2_description", 140),
                    "home_signal_3_title": home_text("home_signal_3_title", 40),
                    "home_signal_3_description": home_text("home_signal_3_description", 140),
                    "home_signal_4_title": home_text("home_signal_4_title", 40),
                    "home_signal_4_description": home_text("home_signal_4_description", 140),
                    "home_plans_title": home_text("home_plans_title", 60),
                    "home_plans_description": home_text("home_plans_description", 240),
                    "home_bottom_title": home_text("home_bottom_title", 100),
                    "home_bottom_description": home_text("home_bottom_description", 400),
                }
                tags = [x.strip() for x in values["home_feature_tags"].split("|") if x.strip()][:8]
                if not tags:
                    tags = [x.strip() for x in HOME_SETTING_DEFAULTS["home_feature_tags"].split("|") if x.strip()]
                values["home_feature_tags"] = " | ".join(tags)
                success_text = "首页内容设置已保存。"

            elif scope == "site":
                values = {
                    "registration_enabled": "true" if str(form.get("registration_enabled")) == "true" else "false",
                    "public_base_url": str(form.get("public_base_url") or "").strip().rstrip("/"),
                }
                success_text = "站点设置已保存。"

            elif scope == "payments":
                rate = Decimal(str(form.get("usdt_cny_rate") or "7.20"))
                min_cny = Decimal(str(form.get("recharge_min_cny") or "10"))
                max_cny = Decimal(str(form.get("recharge_max_cny") or "10000"))
                expire_minutes = int(str(form.get("payment_expire_minutes") or "30"))
                grace_hours = int(str(form.get("payment_late_grace_hours") or "24"))
                polygon_confirmations = int(str(form.get("payment_polygon_confirmations") or "20"))
                if rate <= 0 or min_cny <= 0 or max_cny < min_cny:
                    raise ValueError("汇率或充值金额范围不正确")
                if not (5 <= expire_minutes <= 1440 and 0 <= grace_hours <= 168):
                    raise ValueError("支付超时参数不正确")
                if not (1 <= polygon_confirmations <= 1000):
                    raise ValueError("Polygon 确认数应在 1-1000 之间")

                tron_wallet = str(form.get("payment_tron_wallet") or "").strip()
                polygon_wallet = str(form.get("payment_polygon_wallet") or "").strip()
                polygon_rpc = str(form.get("payment_polygon_rpc") or "").strip()
                tron_enabled = str(form.get("payment_tron_enabled")) == "true"
                polygon_enabled = str(form.get("payment_polygon_enabled")) == "true"
                payment_enabled = str(form.get("payment_enabled")) == "true"
                tron_mode = str(form.get("payment_tron_mode") or "auto").strip().lower()
                polygon_mode = str(form.get("payment_polygon_mode") or "auto").strip().lower()
                if tron_mode not in {"auto", "manual"} or polygon_mode not in {"auto", "manual"}:
                    raise ValueError("充值模式参数不正确")

                # Contracts are security constants owned by XNAT and are never accepted from the browser.
                if tron_wallet and not valid_tron_address(tron_wallet):
                    warnings.append("TRON 收款地址格式看起来不正确")
                if polygon_wallet and not valid_evm_address(polygon_wallet):
                    warnings.append("Polygon 收款地址格式看起来不正确")

                values = {
                    "payment_enabled": "true" if payment_enabled else "false",
                    "usdt_cny_rate": f"{rate:.6f}",
                    "recharge_min_cny": f"{min_cny:.2f}",
                    "recharge_max_cny": f"{max_cny:.2f}",
                    "payment_expire_minutes": str(expire_minutes),
                    "payment_late_grace_hours": str(grace_hours),
                    "payment_tron_enabled": "true" if tron_enabled else "false",
                    "payment_tron_mode": tron_mode,
                    "payment_tron_wallet": tron_wallet,
                    "payment_tron_contract": TRON_USDT_CONTRACT,
                    "payment_polygon_enabled": "true" if polygon_enabled else "false",
                    "payment_polygon_mode": polygon_mode,
                    "payment_polygon_wallet": polygon_wallet,
                    "payment_polygon_rpc": polygon_rpc,
                    "payment_polygon_contract": POLYGON_USDT0_CONTRACT,
                    "payment_polygon_confirmations": str(polygon_confirmations),
                }

                new_trongrid_key = str(form.get("trongrid_api_key") or "").strip()
                if str(form.get("clear_trongrid_api_key")) == "true":
                    values["trongrid_api_key_enc"] = ""
                    secret_keys_changed.append("trongrid_api_key")
                elif new_trongrid_key:
                    values["trongrid_api_key_enc"] = encrypt_secret(new_trongrid_key)
                    secret_keys_changed.append("trongrid_api_key")

                current_trongrid_key = new_trongrid_key or runtime_secret(db, "trongrid_api_key_enc", env_name="TRONGRID_API_KEY", default="")
                if payment_enabled:
                    if tron_enabled and not tron_wallet:
                        warnings.append("TRON 收款地址尚未配置")
                    elif tron_enabled and tron_mode == "auto" and not current_trongrid_key:
                        warnings.append("TRON 自动模式尚未配置 TronGrid API Key")
                    if polygon_enabled and not polygon_wallet:
                        warnings.append("Polygon 收款地址尚未配置")
                    elif polygon_enabled and polygon_mode == "auto" and not polygon_rpc:
                        warnings.append("Polygon 自动模式尚未配置 RPC")
                    if not tron_enabled and not polygon_enabled:
                        warnings.append("支付总开关已开启，但当前没有启用任何网络")
                success_text = "USDT 支付设置已保存。"

            elif scope == "operations":
                notice_raw = str(form.get("expiry_notice_days") or "7,3,1").strip()
                notice_values = []
                for chunk in notice_raw.split(","):
                    chunk = chunk.strip()
                    if not chunk:
                        continue
                    value = int(chunk)
                    if not (1 <= value <= 365):
                        raise ValueError("到期提醒天数应在 1-365 之间")
                    if value not in notice_values:
                        notice_values.append(value)
                if not notice_values:
                    raise ValueError("至少配置一个到期提醒天数")
                notice_values.sort(reverse=True)

                grace_days = int(str(form.get("expiry_grace_days") or "0"))
                delete_after_days = int(str(form.get("expiry_delete_after_days") or "7"))
                delete_notice_hours = int(str(form.get("expiry_delete_notice_hours") or "24"))
                cycle_mode = str(form.get("traffic_cycle_default_mode") or "rolling30").strip().lower()
                cycle_day = int(str(form.get("traffic_cycle_default_day") or "1"))
                port_alert = int(str(form.get("node_nat_port_alert_percent") or "10"))

                if not (0 <= grace_days <= 90):
                    raise ValueError("到期宽限期应在 0-90 天之间")
                if not (1 <= delete_after_days <= 365):
                    raise ValueError("自动删除等待天数应在 1-365 天之间")
                if not (1 <= delete_notice_hours <= 720):
                    raise ValueError("删除前最终提醒应在 1-720 小时之间")
                if cycle_mode not in {"rolling30", "monthly"}:
                    raise ValueError("默认流量周期模式无效")
                if not (1 <= cycle_day <= 28):
                    raise ValueError("每月固定重置日应在 1-28 之间")
                if not (0 <= port_alert <= 100):
                    raise ValueError("NAT 端口池告警阈值应在 0-100% 之间")

                auto_delete = str(form.get("expiry_delete_enabled")) == "true"
                values = {
                    "expiry_notice_days": ",".join(str(x) for x in notice_values),
                    "expiry_grace_days": str(grace_days),
                    "expiry_auto_stop_enabled": "true" if str(form.get("expiry_auto_stop_enabled")) == "true" else "false",
                    "expiry_delete_enabled": "true" if auto_delete else "false",
                    "expiry_delete_after_days": str(delete_after_days),
                    "expiry_delete_notice_hours": str(delete_notice_hours),
                    "expiry_renew_auto_start": "true" if str(form.get("expiry_renew_auto_start")) == "true" else "false",
                    "traffic_cycle_default_mode": cycle_mode,
                    "traffic_cycle_default_day": str(cycle_day),
                    "node_nat_port_alert_percent": str(port_alert),
                }
                if auto_delete:
                    warnings.append("自动删除已经开启：超过到期宽限期和删除等待期后，系统会永久删除 VPS")
                success_text = "运营与生命周期策略已保存。"

            elif scope == "security":
                max_failures = int(str(form.get("login_max_failures") or "10"))
                window_minutes = int(str(form.get("login_window_minutes") or "15"))
                block_minutes = int(str(form.get("login_block_minutes") or "30"))
                if not (3 <= max_failures <= 100):
                    raise ValueError("失败次数应在 3-100 之间")
                if not (1 <= window_minutes <= 1440):
                    raise ValueError("统计窗口应在 1-1440 分钟之间")
                if not (1 <= block_minutes <= 10080):
                    raise ValueError("封禁时间应在 1-10080 分钟之间")
                values = {
                    "login_max_failures": str(max_failures),
                    "login_window_minutes": str(window_minutes),
                    "login_block_minutes": str(block_minutes),
                    "admin_require_2fa": "true" if str(form.get("admin_require_2fa")) == "true" else "false",
                }
                success_text = "登录与管理员安全设置已保存。"

            elif scope == "nat":
                blocked_private = str(form.get("port_blocked_private") or "").strip()
                blocked_public = str(form.get("port_blocked_public") or "").strip()
                parse_port_spec(blocked_private)
                parse_port_spec(blocked_public)
                values = {
                    "port_blocked_private": blocked_private,
                    "port_blocked_public": blocked_public,
                    "port_tcp_enabled": "true" if str(form.get("port_tcp_enabled")) == "true" else "false",
                    "port_udp_enabled": "true" if str(form.get("port_udp_enabled")) == "true" else "false",
                }
                success_text = "NAT 端口安全策略已保存。各节点端口范围请在宿主机节点页面配置。"

            elif scope == "notifications":
                smtp_port_raw = str(form.get("smtp_port") or "587").strip()
                smtp_port = int(smtp_port_raw or "587")
                if not (1 <= smtp_port <= 65535):
                    raise ValueError("SMTP 端口应在 1-65535 之间")

                values = {
                    "smtp_host": str(form.get("smtp_host") or "").strip(),
                    "smtp_port": str(smtp_port),
                    "smtp_username": str(form.get("smtp_username") or "").strip(),
                    "smtp_from": str(form.get("smtp_from") or "").strip(),
                    "smtp_starttls": "true" if str(form.get("smtp_starttls")) == "true" else "false",
                }

                smtp_password = str(form.get("smtp_password") or "")
                telegram_token = str(form.get("telegram_bot_token") or "").strip()
                if str(form.get("clear_smtp_password")) == "true":
                    values["smtp_password_enc"] = ""
                    secret_keys_changed.append("smtp_password")
                elif smtp_password:
                    values["smtp_password_enc"] = encrypt_secret(smtp_password)
                    secret_keys_changed.append("smtp_password")

                if str(form.get("clear_telegram_bot_token")) == "true":
                    values["telegram_bot_token_enc"] = ""
                    secret_keys_changed.append("telegram_bot_token")
                elif telegram_token:
                    values["telegram_bot_token_enc"] = encrypt_secret(telegram_token)
                    secret_keys_changed.append("telegram_bot_token")

                # Partial configuration is allowed. The status card clearly reports what is usable.
                if values["smtp_host"] and not values["smtp_from"]:
                    warnings.append("SMTP Host 已保存，但发件地址仍为空")
                if values["smtp_from"] and not values["smtp_host"]:
                    warnings.append("SMTP 发件地址已保存，但 Host 仍为空")
                for group in ("server", "traffic", "expiry", "payment", "ticket", "security", "system"):
                    values[f"notify_rule_{group}_email"] = "true" if str(form.get(f"notify_rule_{group}_email")) == "true" else "false"
                    values[f"notify_rule_{group}_telegram"] = "true" if str(form.get(f"notify_rule_{group}_telegram")) == "true" else "false"
                success_text = "通知服务设置已保存。"

            else:
                raise ValueError("未知设置区域")

        except (ValueError, InvalidOperation) as exc:
            flash(request, f"本区域设置未保存：{exc}", "error")
            return RedirectResponse(f"/admin?section={redirect_section}", status_code=303)
        except Exception:
            flash(request, "本区域参数格式不正确，请检查刚刚修改的内容。", "error")
            return RedirectResponse(f"/admin?section={redirect_section}", status_code=303)

        for key, value in values.items():
            set_setting(db, key, value)
        write_audit(
            db,
            actor=admin,
            request=request,
            action="admin.settings.update",
            target_type="system",
            detail={"scope": scope, "keys": [k for k in values if not k.endswith("_enc")], "secret_keys_changed": secret_keys_changed},
        )
        db.commit()

        if warnings:
            flash(request, success_text + " 提示：" + "；".join(warnings) + "。", "success")
        else:
            flash(request, success_text, "success")
    return RedirectResponse(f"/admin?section={redirect_section}", status_code=303)


@app.post("/admin/notifications/test-email")
def admin_test_email_notification(request: Request, to_address: str = Form(...), csrf_token: str = Form(...)):
    validate_csrf(request, csrf_token)
    with db_session() as db:
        admin = admin_required(request, db)
        address = (to_address or "").strip()
        if "@" not in address or len(address) > 254:
            flash(request, "测试收件邮箱格式不正确。", "error")
            return RedirectResponse("/admin?section=notifications", status_code=303)
        try:
            ok = send_email_address(address, subject="[XNAT] SMTP 测试", body="XNAT 通知服务 SMTP 测试成功。")
            if not ok:
                raise RuntimeError("SMTP 配置不完整")
            write_audit(db, actor=admin, request=request, action="admin.notification.test_email", target_type="system", detail={"to": address})
            db.commit(); flash(request, f"测试邮件已发送到 {address}。", "success")
        except Exception as exc:
            db.rollback(); flash(request, f"SMTP 测试失败：{str(exc)[:180]}", "error")
    return RedirectResponse("/admin?section=notifications", status_code=303)


@app.post("/admin/notifications/test-telegram")
def admin_test_telegram_notification(request: Request, chat_id: str = Form(...), csrf_token: str = Form(...)):
    validate_csrf(request, csrf_token)
    with db_session() as db:
        admin = admin_required(request, db)
        target = (chat_id or "").strip()[:80]
        if not target:
            flash(request, "请输入用于测试的 Telegram Chat ID。", "error")
            return RedirectResponse("/admin?section=notifications", status_code=303)
        try:
            send_telegram_chat(target, "XNAT 通知服务 Telegram 测试成功。")
            write_audit(db, actor=admin, request=request, action="admin.notification.test_telegram", target_type="system", detail={"chat_id": target})
            db.commit(); flash(request, f"Telegram 测试消息已发送到 Chat ID {target}。", "success")
        except Exception as exc:
            db.rollback(); flash(request, f"Telegram 测试失败：{str(exc)[:180]}", "error")
    return RedirectResponse("/admin?section=notifications", status_code=303)


@app.post("/admin/coupons")
def admin_create_coupon(request:Request,code:str=Form(...),discount_type:str=Form(...),discount_value:str=Form(...),min_order:str=Form("0"),max_uses:int=Form(0),expires_at:str=Form(""),csrf_token:str=Form(...)):
    validate_csrf(request,csrf_token); code=code.strip().upper(); discount_type=discount_type.strip().lower()
    try:
        if not code or len(code)>64 or not all(ch.isalnum() or ch in "-_" for ch in code): raise ValueError
        if discount_type=="percent": value=int(discount_value); assert 1<=value<=100
        elif discount_type=="fixed": value=int(Decimal(discount_value)*100); assert value>=1
        else: raise ValueError
        min_cents=int(Decimal(min_order or "0")*100); expiry=parse_local_datetime(expires_at)
        if min_cents<0 or max_uses<0: raise ValueError
    except Exception: flash(request,"优惠参数格式错误。","error"); return RedirectResponse("/admin?section=coupons",status_code=303)
    with db_session() as db:
        admin=admin_required(request,db)
        if db.scalar(select(Coupon).where(Coupon.code==code)): flash(request,"优惠码已经存在。","error"); return RedirectResponse("/admin?section=coupons",status_code=303)
        row=Coupon(code=code,discount_type=discount_type,discount_value=value,min_order_cents=min_cents,max_uses=max_uses,expires_at=expiry,is_active=True); db.add(row); db.flush(); write_audit(db,actor=admin,request=request,action="admin.coupon.create",target_type="coupon",target_id=row.id,target_name=row.code); db.commit(); flash(request,f"优惠码 {code} 已创建。","success")
    return RedirectResponse("/admin?section=coupons",status_code=303)


@app.post("/admin/coupons/{coupon_id}/toggle")
def admin_toggle_coupon(request:Request,coupon_id:int,csrf_token:str=Form(...)):
    validate_csrf(request,csrf_token)
    with db_session() as db:
        admin=admin_required(request,db); row=db.get(Coupon,coupon_id)
        if not row: raise HTTPException(404,"优惠码不存在")
        row.is_active=not row.is_active; write_audit(db,actor=admin,request=request,action="admin.coupon.toggle",target_type="coupon",target_id=row.id,target_name=row.code,detail={"active":row.is_active}); db.commit(); flash(request,f"优惠码 {row.code} 已{'启用' if row.is_active else '停用'}。","success")
    return RedirectResponse("/admin?section=coupons",status_code=303)


@app.post("/admin/servers/provision")
def admin_provision_server(request:Request,user_identifier:str=Form(...),plan_id:int=Form(...),os_image_id:int=Form(...),csrf_token:str=Form(...)):
    validate_csrf(request,csrf_token)
    with db_session() as db:
        admin=admin_required(request,db); identity=user_identifier.strip(); user=db.scalar(select(User).where(or_(User.username==identity,User.email==identity.lower()))); plan=db.get(Plan,plan_id); image=db.get(SystemImage,os_image_id)
        if not user or not user.is_active: flash(request,"目标用户不存在或已停用。","error"); return RedirectResponse("/admin?section=provision",status_code=303)
        if not plan or not image or not image.is_active: flash(request,"套餐或系统镜像不可用。","error"); return RedirectResponse("/admin?section=provision",status_code=303)
        if plan_stock(db,plan)["sold_out"]: flash(request,"该套餐库存已满。","error"); return RedirectResponse("/admin?section=provision",status_code=303)
        try:
            order,server,job=queue_service_provision(db,user,plan,image,order_amount_cents=0,order_kind="admin_provision")
            write_audit(db,actor=admin,request=request,action="admin.server.provision.queue",target_type="server",target_id=server.id,target_name=server.name,detail={"user":user.username,"job_id":job.id}); db.commit(); flash(request,f"已为 {user.username} 创建 {server.name}，开通任务 #{job.id} 已进入队列。","success")
        except Exception as exc: db.rollback(); flash(request,f"管理员开通失败：{str(exc)[:180]}","error")
    return RedirectResponse("/admin?section=provision",status_code=303)


@app.post("/admin/payments/test-tron")
def admin_payment_test_tron(request: Request, csrf_token: str = Form(...)):
    validate_csrf(request, csrf_token)
    with db_session() as db:
        admin = admin_required(request, db)
        try:
            result = test_tron_configuration(db)
            write_audit(db, actor=admin, request=request, action="admin.payment.test_tron", target_type="payment", detail={"mode": result.get("mode")})
            db.commit()
            flash(request, result["message"], "success")
        except Exception as exc:
            db.rollback()
            flash(request, f"TRON 配置测试失败：{str(exc)[:180]}", "error")
    return RedirectResponse("/admin?section=payments", status_code=303)


@app.post("/admin/payments/test-polygon")
def admin_payment_test_polygon(request: Request, csrf_token: str = Form(...)):
    validate_csrf(request, csrf_token)
    with db_session() as db:
        admin = admin_required(request, db)
        try:
            result = test_polygon_configuration(db)
            write_audit(db, actor=admin, request=request, action="admin.payment.test_polygon", target_type="payment", detail={"mode": result.get("mode")})
            db.commit()
            flash(request, result["message"], "success")
        except Exception as exc:
            db.rollback()
            flash(request, f"Polygon 配置测试失败：{str(exc)[:180]}", "error")
    return RedirectResponse("/admin?section=payments", status_code=303)


@app.post("/admin/payments/{recharge_id}/manual-confirm")
def admin_payment_manual_confirm(request: Request, recharge_id: int, confirm_text: str = Form(...), tx_hash: str = Form(""), csrf_token: str = Form(...)):
    validate_csrf(request, csrf_token)
    if (confirm_text or "").strip().upper() != "PAID":
        flash(request, "请输入 PAID 确认人工入账。", "error")
        return RedirectResponse("/admin?section=payments", status_code=303)
    tx_hash = (tx_hash or "").strip()
    if tx_hash:
        normalized = tx_hash[2:] if tx_hash.lower().startswith("0x") else tx_hash
        if not re.fullmatch(r"[0-9a-fA-F]{64}", normalized):
            flash(request, "交易哈希格式不正确。", "error")
            return RedirectResponse("/admin?section=payments", status_code=303)
    with db_session() as db:
        admin = admin_required(request, db)
        order = db.get(RechargeOrder, recharge_id)
        if not order:
            raise HTTPException(404, "充值订单不存在")
        try:
            credited = manual_credit_order(db, order, tx_hash=tx_hash or order.tx_hash)
            write_audit(db, actor=admin, request=request, action="admin.payment.manual_confirm", target_type="recharge_order", target_id=order.id, detail={"credited": credited, "chain": order.chain})
            db.commit()
            flash(request, f"充值订单 #{order.id} 已人工确认入账。" if credited else "该充值订单已经入账。", "success")
        except Exception as exc:
            db.rollback()
            flash(request, f"人工确认失败：{str(exc)[:180]}", "error")
    return RedirectResponse("/admin?section=payments", status_code=303)


@app.post("/admin/payments/{recharge_id}/repair-confirm")
def admin_payment_repair_confirm(
    request: Request,
    recharge_id: int,
    tx_hash: str = Form(...),
    confirm_text: str = Form(...),
    csrf_token: str = Form(...),
):
    validate_csrf(request, csrf_token)
    if (confirm_text or "").strip().upper() != "PAID":
        flash(request, "请输入 PAID 确认补单。", "error")
        return RedirectResponse(f"/admin?section=payments&repair_order_id={recharge_id}", status_code=303)

    with db_session() as db:
        admin = admin_required(request, db)
        order = db.get(RechargeOrder, recharge_id)
        if not order:
            raise HTTPException(404, "充值订单不存在")
        try:
            verified = verify_recharge_transaction(db, order, tx_hash)
            credited = repair_credit_order(db, order, tx_hash=tx_hash, verified=verified)
            write_audit(
                db,
                actor=admin,
                request=request,
                action="admin.payment.repair_verified",
                target_type="recharge_order",
                target_id=order.id,
                detail={
                    "credited": credited,
                    "chain": order.chain,
                    "tx_hash": normalize_tx_hash(order.chain, tx_hash),
                    "expected_usdt": usdt_units_to_text(order.expected_usdt_units),
                    "requested_cny_cents": order.requested_cny_cents,
                },
            )
            db.commit()
            flash(request, f"充值订单 #{order.id} 已完成链上校验并补单入账。" if credited else "该订单已经入账。", "success")
        except Exception as exc:
            db.rollback()
            flash(request, f"补单失败：{str(exc)[:220]}", "error")
    return RedirectResponse("/admin?section=payments", status_code=303)


@app.post("/admin/payments/{recharge_id}/repair-force")
def admin_payment_repair_force(
    request: Request,
    recharge_id: int,
    tx_hash: str = Form(...),
    confirm_text: str = Form(...),
    csrf_token: str = Form(...),
):
    validate_csrf(request, csrf_token)
    if (confirm_text or "").strip().upper() != "FORCE PAID":
        flash(request, "请输入 FORCE PAID 确认强制补单。", "error")
        return RedirectResponse(f"/admin?section=payments&repair_order_id={recharge_id}&repair_tx={tx_hash}", status_code=303)

    with db_session() as db:
        admin = admin_required(request, db)
        order = db.get(RechargeOrder, recharge_id)
        if not order:
            raise HTTPException(404, "充值订单不存在")
        try:
            credited = repair_credit_order(db, order, tx_hash=tx_hash, force=True)
            write_audit(
                db,
                actor=admin,
                request=request,
                action="admin.payment.repair_force",
                target_type="recharge_order",
                target_id=order.id,
                detail={
                    "credited": credited,
                    "chain": order.chain,
                    "tx_hash": normalize_tx_hash(order.chain, tx_hash),
                    "expected_usdt": usdt_units_to_text(order.expected_usdt_units),
                    "requested_cny_cents": order.requested_cny_cents,
                    "warning": "force repair without chain verification",
                },
            )
            db.commit()
            flash(request, f"充值订单 #{order.id} 已强制补单入账。" if credited else "该订单已经入账。", "success")
        except Exception as exc:
            db.rollback()
            flash(request, f"强制补单失败：{str(exc)[:220]}", "error")
    return RedirectResponse("/admin?section=payments", status_code=303)


@app.post("/admin/payments/{recharge_id}/repair-no-tx")
def admin_payment_repair_no_tx(
    request: Request,
    recharge_id: int,
    reason: str = Form(...),
    confirm_text: str = Form(...),
    csrf_token: str = Form(...),
):
    validate_csrf(request, csrf_token)
    if (confirm_text or "").strip().upper() != "FORCE CREDIT":
        flash(request, "请输入 FORCE CREDIT 确认无 TxHash 强制补单。", "error")
        return RedirectResponse(
            f"/admin?section=payments&repair_order_id={recharge_id}#payment-repair",
            status_code=303,
        )

    reason = re.sub(r"\s+", " ", (reason or "").strip())
    if len(reason) < 5:
        flash(request, "请填写至少 5 个字符的补单原因。", "error")
        return RedirectResponse(
            f"/admin?section=payments&repair_order_id={recharge_id}#payment-repair",
            status_code=303,
        )
    if len(reason) > 300:
        flash(request, "补单原因最多 300 个字符。", "error")
        return RedirectResponse(
            f"/admin?section=payments&repair_order_id={recharge_id}#payment-repair",
            status_code=303,
        )

    with db_session() as db:
        admin = admin_required(request, db)
        order = db.get(RechargeOrder, recharge_id)
        if not order:
            raise HTTPException(404, "充值订单不存在")
        try:
            credited = force_credit_order_without_tx(db, order, reason=reason)
            write_audit(
                db,
                actor=admin,
                request=request,
                action="admin.payment.repair_no_tx",
                target_type="recharge_order",
                target_id=order.id,
                detail={
                    "credited": credited,
                    "chain": order.chain,
                    "tx_hash": None,
                    "chain_verified": False,
                    "expected_usdt": usdt_units_to_text(order.expected_usdt_units),
                    "requested_cny_cents": order.requested_cny_cents,
                    "reason": reason,
                    "warning": "credited without transaction hash or chain verification",
                },
            )
            db.commit()
            flash(
                request,
                f"充值订单 #{order.id} 已执行无 TxHash 人工补单。"
                if credited else "该订单已经入账。",
                "success",
            )
        except Exception as exc:
            db.rollback()
            flash(request, f"无 TxHash 补单失败：{str(exc)[:220]}", "error")

    return RedirectResponse("/admin?section=payments", status_code=303)


@app.post("/admin/payments/scan")
def admin_payment_scan(request:Request,csrf_token:str=Form(...)):
    validate_csrf(request,csrf_token)
    with db_session() as db: admin=admin_required(request,db); write_audit(db,actor=admin,request=request,action="admin.payment.scan",target_type="payment"); db.commit()
    try: credited,failed=poll_pending_payments(); flash(request,f"链上扫描完成：入账 {credited} 笔，扫描异常 {failed} 个网络。","success" if failed==0 else "warning")
    except Exception as exc: flash(request,f"链上扫描失败：{str(exc)[:180]}","error")
    return RedirectResponse("/admin?section=payments",status_code=303)


@app.post("/admin/tickets/{ticket_id}/reply")
def admin_ticket_reply(request:Request,ticket_id:int,body:str=Form(...),csrf_token:str=Form(...)):
    validate_csrf(request,csrf_token); body=body.strip()
    if not body or len(body)>10000: flash(request,"回复内容无效。","error"); return RedirectResponse("/admin?section=tickets",status_code=303)
    with db_session() as db:
        admin=admin_required(request,db); ticket=db.get(Ticket,ticket_id)
        if not ticket: raise HTTPException(404,"工单不存在")
        if ticket.status=="closed": flash(request,"已关闭工单不能回复。","error"); return RedirectResponse("/admin?section=tickets",status_code=303)
        db.add(TicketMessage(ticket_id=ticket.id,author_user_id=admin.id,author_is_admin=True,body=body)); ticket.status="answered"; ticket.updated_at=datetime.utcnow(); user=db.get(User,ticket.user_id)
        if user: queue_notification(db,user,title=f"工单 #{ticket.id} 已回复",body=f"{ticket.subject}\n\n管理员回复：{body[:1200]}",kind="ticket",severity="info",event_key=f"ticket-reply:{ticket.id}:{int(ticket.updated_at.timestamp())}")
        write_audit(db,actor=admin,request=request,action="admin.ticket.reply",target_type="ticket",target_id=ticket.id,target_name=ticket.subject); db.commit(); flash(request,f"工单 #{ticket.id} 已回复。","success")
    return RedirectResponse("/admin?section=tickets",status_code=303)


@app.post("/admin/tickets/{ticket_id}/close")
def admin_ticket_close(request:Request,ticket_id:int,csrf_token:str=Form(...)):
    validate_csrf(request,csrf_token)
    with db_session() as db:
        admin=admin_required(request,db); ticket=db.get(Ticket,ticket_id)
        if not ticket: raise HTTPException(404,"工单不存在")
        ticket.status="closed"; ticket.closed_at=datetime.utcnow(); ticket.updated_at=datetime.utcnow(); user=db.get(User,ticket.user_id)
        if user: queue_notification(db,user,title=f"工单 #{ticket.id} 已关闭",body=ticket.subject,kind="ticket",severity="info",event_key=f"ticket-closed:{ticket.id}")
        write_audit(db,actor=admin,request=request,action="admin.ticket.close",target_type="ticket",target_id=ticket.id,target_name=ticket.subject); db.commit(); flash(request,f"工单 #{ticket.id} 已关闭。","success")
    return RedirectResponse("/admin?section=tickets",status_code=303)


@app.post("/admin/jobs/{job_id}/retry")
def admin_job_retry(request:Request,job_id:int,csrf_token:str=Form(...)):
    validate_csrf(request,csrf_token)
    with db_session() as db:
        admin=admin_required(request,db); job=db.get(Job,job_id)
        if not job: raise HTTPException(404,"任务不存在")
        if job.status!="failed": flash(request,"只有失败任务可以重新执行。","error"); return RedirectResponse("/admin?section=jobs",status_code=303)
        if job.job_type=="provision_server": flash(request,"开通失败任务已自动退款，为避免免费重复开通不能手动重试；请重新手动开通。","error"); return RedirectResponse("/admin?section=jobs",status_code=303)
        job.status="pending"; job.attempts=0; job.error_text=None; job.available_at=datetime.utcnow(); job.started_at=None; job.finished_at=None
        write_audit(db,actor=admin,request=request,action="admin.job.retry",target_type="job",target_id=job.id,target_name=job.job_type); db.commit(); flash(request,f"任务 #{job.id} 已重新加入队列。","success")
    return RedirectResponse("/admin?section=jobs",status_code=303)


@app.post("/admin/backups/create")
def admin_backup_create(request:Request,csrf_token:str=Form(...)):
    validate_csrf(request,csrf_token)
    with db_session() as db: admin=admin_required(request,db); write_audit(db,actor=admin,request=request,action="admin.backup.create",target_type="system"); db.commit()
    try: row=create_backup("manual"); flash(request,f"备份 {row['name']} 已创建。","success")
    except Exception as exc: flash(request,f"备份失败：{str(exc)[:180]}","error")
    return RedirectResponse("/admin?section=backups",status_code=303)



@app.post("/admin/backups/upload")
def admin_backup_upload(
    request: Request,
    backup_file: UploadFile = File(...),
    csrf_token: str = Form(...),
):
    validate_csrf(request, csrf_token)
    with db_session() as db:
        admin = admin_required(request, db)
        actor_username = admin.username
    try:
        info = store_uploaded_backup(backup_file.file, backup_file.filename or "")
        with db_session() as db:
            write_audit(
                db,
                actor_username=actor_username,
                request=request,
                action="admin.backup.upload",
                target_type="backup",
                target_name=info["name"],
                detail={
                    "original_name": info.get("original_name"),
                    "sha256": info["sha256"],
                    "size": info["size"],
                    "users": info["users"],
                    "servers": info["servers"],
                    "orders": info["orders"],
                },
            )
            db.commit()
        flash(request, f"上传并校验通过：{info['name']}。确认信息后再执行恢复。", "success")
        return RedirectResponse(
            f"/admin?section=backups&restore_candidate={quote_plus(info['name'])}",
            status_code=303,
        )
    except Exception as exc:
        with db_session() as db:
            write_audit(
                db,
                actor_username=actor_username,
                request=request,
                action="admin.backup.upload",
                target_type="backup",
                target_name=Path(backup_file.filename or "upload.db").name,
                detail={"error": str(exc)[:500]},
                success=False,
            )
            db.commit()
        flash(request, f"上传失败：{str(exc)[:180]}", "error")
        return RedirectResponse("/admin?section=backups", status_code=303)
    finally:
        try:
            backup_file.file.close()
        except Exception:
            pass


@app.post("/admin/backups/{backup_name}/restore")
def admin_backup_restore(
    request: Request,
    backup_name: str,
    confirm_text: str = Form(...),
    csrf_token: str = Form(...),
):
    validate_csrf(request, csrf_token)
    if confirm_text.strip() != "RESTORE":
        flash(request, "恢复确认失败：请输入 RESTORE。", "error")
        return RedirectResponse("/admin?section=backups", status_code=303)

    safe_name = Path(backup_name).name
    with db_session() as db:
        admin = admin_required(request, db)
        actor_username = admin.username

    try:
        result = restore_backup(safe_name)
        safety_name = result["safety"]["name"]
        # Write the audit entry into the restored database. actor_user_id is intentionally
        # left null because the restored snapshot may contain a different user id layout.
        with db_session() as db:
            write_audit(
                db,
                actor_username=actor_username,
                request=request,
                action="admin.backup.restore",
                target_type="backup",
                target_name=safe_name,
                detail={
                    "sha256": result["restored"]["sha256"],
                    "pre_restore_backup": safety_name,
                    "users": result["restored"]["users"],
                    "servers": result["restored"]["servers"],
                    "orders": result["restored"]["orders"],
                },
            )
            db.commit()
        flash(
            request,
            f"数据库已恢复：{safe_name}。恢复前快照：{safety_name}。如登录状态失效，请重新登录。",
            "success",
        )
    except Exception as exc:
        try:
            with db_session() as db:
                write_audit(
                    db,
                    actor_username=actor_username,
                    request=request,
                    action="admin.backup.restore",
                    target_type="backup",
                    target_name=safe_name,
                    detail={"error": str(exc)[:500]},
                    success=False,
                )
                db.commit()
        except Exception:
            pass
        flash(request, f"恢复失败：{str(exc)[:180]}", "error")
    return RedirectResponse("/admin?section=backups", status_code=303)


@app.get("/admin/backups/{backup_name}/download")
def admin_backup_download(request:Request,backup_name:str):
    with db_session() as db: admin=admin_required(request,db); write_audit(db,actor=admin,request=request,action="admin.backup.download",target_type="backup",target_name=Path(backup_name).name); db.commit()
    safe=Path(backup_name).name
    candidate=(BACKUP_DIR/safe).resolve()
    if candidate.parent!=BACKUP_DIR.resolve() or not candidate.exists() or not candidate.name.startswith("panel-") or candidate.suffix!=".db": raise HTTPException(404,"备份不存在")
    return FileResponse(candidate,media_type="application/octet-stream",filename=candidate.name)

@app.get("/health")
def health():
    return {
        "status": "ok",
        "version": "1.3.0",
        "provider": PROVIDER_NAME,
        "timezone": APP_TIMEZONE,
    }
