from datetime import datetime
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, Float
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .db import Base


def utcnow():
    return datetime.utcnow()


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    balance_cents: Mapped[int] = mapped_column(Integer, default=0)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Account / security / notification preferences.
    telegram_chat_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    notify_email: Mapped[bool] = mapped_column(Boolean, default=True)
    notify_telegram: Mapped[bool] = mapped_column(Boolean, default=False)
    totp_secret_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Fingerprint of the latest site announcement this user has already seen.
    # This makes each announcement a one-time post-login notice per user.
    announcement_seen_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    servers: Mapped[list["Server"]] = relationship(back_populates="user")
    orders: Mapped[list["Order"]] = relationship(back_populates="user")


class Plan(Base):
    __tablename__ = "plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    cpu: Mapped[int] = mapped_column(Integer)
    memory_mb: Mapped[int] = mapped_column(Integer)
    disk_gb: Mapped[int] = mapped_column(Integer)
    port_count: Mapped[int] = mapped_column(Integer, default=5)
    bandwidth_mbps: Mapped[int] = mapped_column(Integer, default=100)
    traffic_gb: Mapped[int] = mapped_column(Integer, default=500)
    monthly_price_cents: Mapped[int] = mapped_column(Integer)
    # Paid self-service traffic reset price. Existing plans are backfilled to the monthly price.
    traffic_reset_price_cents: Mapped[int] = mapped_column(Integer, default=0)
    stock_limit: Mapped[int] = mapped_column(Integer, default=0)  # 0 = unlimited
    # Instance virtualization requested by this plan. Existing plans default to LXC.
    virtualization_type: Mapped[str] = mapped_column(String(16), default="lxc")
    # Legacy compatibility columns plus catalog display metadata. New admin UI uses only server_region/network_line on Plan; Host owns flag/prefix.
    country_code: Mapped[str] = mapped_column(String(2), default="")
    server_region: Mapped[str] = mapped_column(String(120), default="")
    region_code: Mapped[str] = mapped_column(String(16), default="")
    network_line: Mapped[str] = mapped_column(String(160), default="")

    # Homepage merchandising controls.
    # sort_order remains package-center/catalog order for compatibility.
    sort_order: Mapped[int] = mapped_column(Integer, default=100)
    homepage_visible: Mapped[bool] = mapped_column(Boolean, default=False)
    homepage_sort_order: Mapped[int] = mapped_column(Integer, default=100)
    is_recommended: Mapped[bool] = mapped_column(Boolean, default=False)
    recommendation_label: Mapped[str] = mapped_column(String(32), default="推荐")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class SystemImage(Base):
    __tablename__ = "system_images"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    alias: Mapped[str] = mapped_column(String(255), unique=True)
    family: Mapped[str] = mapped_column(String(32), default="apt")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=100)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)



class HostNode(Base):
    __tablename__ = "host_nodes"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    region: Mapped[str] = mapped_column(String(80), default="default", index=True)
    # Host presentation identity. country_code selects the front-end flag; region_code is the machine-number prefix. Legacy server_region/network_line columns remain for schema compatibility only.
    country_code: Mapped[str] = mapped_column(String(2), default="")
    server_region: Mapped[str] = mapped_column(String(120), default="")
    region_code: Mapped[str] = mapped_column(String(16), default="")
    network_line: Mapped[str] = mapped_column(String(160), default="")
    api_url: Mapped[str] = mapped_column(String(255))
    api_token_enc: Mapped[str] = mapped_column(Text)
    public_ip: Mapped[str] = mapped_column(String(64))
    port_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    port_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_vps: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    verify_tls: Mapped[bool] = mapped_column(Boolean, default=False)
    # Host Agent reports configured modes (lxc / kvm / lxc,kvm) and live KVM capability.
    virtualization_modes: Mapped[str] = mapped_column(String(32), default="lxc")
    kvm_available: Mapped[bool] = mapped_column(Boolean, default=False)

    # v1.1 scheduling controls. Maintenance/drain only blocks new placement;
    # existing instances keep running and remain manageable.
    maintenance_mode: Mapped[bool] = mapped_column(Boolean, default=False)
    maintenance_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    schedule_cpu_max_percent: Mapped[int] = mapped_column(Integer, default=90)
    schedule_memory_max_percent: Mapped[int] = mapped_column(Integer, default=90)
    schedule_storage_max_percent: Mapped[int] = mapped_column(Integer, default=90)

    status: Mapped[str] = mapped_column(String(24), default="unknown", index=True)
    agent_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    agent_api_version: Mapped[str | None] = mapped_column(String(16), nullable=True)
    hostname: Mapped[str | None] = mapped_column(String(120), nullable=True)
    cpu_percent: Mapped[float] = mapped_column(Float, default=0.0)
    memory_total_mb: Mapped[int] = mapped_column(Integer, default=0)
    memory_used_mb: Mapped[int] = mapped_column(Integer, default=0)
    storage_total_gb: Mapped[float] = mapped_column(Float, default=0.0)
    storage_used_gb: Mapped[float] = mapped_column(Float, default=0.0)
    active_vps: Mapped[int] = mapped_column(Integer, default=0)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    servers: Mapped[list["Server"]] = relationship(back_populates="host")
    plan_links: Mapped[list["PlanHost"]] = relationship(back_populates="host", cascade="all, delete-orphan")


class PlanHost(Base):
    __tablename__ = "plan_hosts"
    __table_args__ = (
        UniqueConstraint("plan_id", "host_id", name="uq_plan_host"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("plans.id", ondelete="CASCADE"), index=True)
    host_id: Mapped[int] = mapped_column(ForeignKey("host_nodes.id", ondelete="CASCADE"), index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    plan: Mapped["Plan"] = relationship()
    host: Mapped["HostNode"] = relationship(back_populates="plan_links")


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    plan_id: Mapped[int] = mapped_column(ForeignKey("plans.id"))
    amount_cents: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    kind: Mapped[str] = mapped_column(String(24), default="purchase")
    server_id: Mapped[int | None] = mapped_column(ForeignKey("servers.id"), nullable=True)
    coupon_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    discount_cents: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    user: Mapped["User"] = relationship(back_populates="orders")
    plan: Mapped["Plan"] = relationship(foreign_keys=[plan_id])


class Server(Base):
    __tablename__ = "servers"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    plan_id: Mapped[int] = mapped_column(ForeignKey("plans.id"))
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))
    host_id: Mapped[int | None] = mapped_column(ForeignKey("host_nodes.id"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(80))
    # Stable Panel-facing identifier (for example TYO-0002). Provider/internal
    # instance names such as nat-1-2 remain unchanged and are never repurposed.
    display_id: Mapped[str | None] = mapped_column(String(32), nullable=True, unique=True, index=True)
    provider: Mapped[str] = mapped_column(String(32), default="mock")
    provider_instance_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="provisioning")
    public_ip: Mapped[str] = mapped_column(String(255), default="203.0.113.10")
    private_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ssh_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    root_password_enc: Mapped[str | None] = mapped_column(Text, nullable=True)

    os_image_id: Mapped[int | None] = mapped_column(ForeignKey("system_images.id"), nullable=True)
    os_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    os_alias: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Per-service snapshot.
    cpu: Mapped[int | None] = mapped_column(Integer, nullable=True)
    memory_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    disk_gb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    port_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bandwidth_mbps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    traffic_gb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    monthly_price_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Catalog presentation snapshot: already-opened servers must not silently move
    # region/line when an administrator later edits the plan.
    server_region_snapshot: Mapped[str | None] = mapped_column(String(120), nullable=True)
    network_line_snapshot: Mapped[str | None] = mapped_column(String(160), nullable=True)
    # Snapshot so reinstall keeps the original virtualization type even if the plan changes later.
    virtualization_type: Mapped[str] = mapped_column(String(16), default="lxc")

    # Persistent traffic accounting for the current 30-day service cycle.
    traffic_used_rx_bytes: Mapped[int] = mapped_column(Integer, default=0)
    traffic_used_tx_bytes: Mapped[int] = mapped_column(Integer, default=0)
    traffic_last_rx_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    traffic_last_tx_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    traffic_cycle_start: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    traffic_cycle_end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    traffic_last_sampled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    traffic_bonus_gb: Mapped[int] = mapped_column(Integer, default=0)
    traffic_throttled: Mapped[bool] = mapped_column(Boolean, default=False)
    traffic_throttled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    traffic_throttle_exempt: Mapped[bool] = mapped_column(Boolean, default=False)
    # Traffic reset policy is independent from service expiry.
    # rolling30 = every 30 days; monthly = fixed day (1-28) of each month.
    traffic_cycle_mode: Mapped[str] = mapped_column(String(24), default="rolling30")
    traffic_cycle_day: Mapped[int] = mapped_column(Integer, default=1)

    # Reconciliation / drift state.
    reconcile_status: Mapped[str] = mapped_column(String(24), default="unknown")
    reconcile_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    reconciled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Lifecycle markers used for safe expiry suspension / auto-delete.
    expiry_suspended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expiry_delete_queued_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    user: Mapped["User"] = relationship(back_populates="servers")
    host: Mapped["HostNode | None"] = relationship(back_populates="servers")
    plan: Mapped["Plan"] = relationship()
    os_image: Mapped["SystemImage | None"] = relationship()
    ports: Mapped[list["PortMapping"]] = relationship(back_populates="server", cascade="all, delete-orphan")


class PortMapping(Base):
    __tablename__ = "port_mappings"
    # Public ports are unique per Host + protocol, not globally across the Panel.
    # Host scoping is enforced by the allocator because host_id lives on Server.

    id: Mapped[int] = mapped_column(primary_key=True)
    server_id: Mapped[int] = mapped_column(ForeignKey("servers.id", ondelete="CASCADE"))
    public_port: Mapped[int] = mapped_column(Integer)
    private_port: Mapped[int] = mapped_column(Integer)
    protocol: Mapped[str] = mapped_column(String(8), default="tcp")
    device_name: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    server: Mapped["Server"] = relationship(back_populates="ports")


class SiteSetting(Base):
    __tablename__ = "site_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


class Announcement(Base):
    __tablename__ = "announcements"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(160))
    body: Mapped[str] = mapped_column(Text)
    is_published: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    show_on_login: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AnnouncementRead(Base):
    __tablename__ = "announcement_reads"
    __table_args__ = (
        UniqueConstraint("announcement_id", "user_id", name="uq_announcement_read_user"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    announcement_id: Mapped[int] = mapped_column(ForeignKey("announcements.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    read_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    announcement: Mapped["Announcement"] = relationship()
    user: Mapped["User"] = relationship()


class Coupon(Base):
    __tablename__ = "coupons"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    discount_type: Mapped[str] = mapped_column(String(16))
    discount_value: Mapped[int] = mapped_column(Integer)
    min_order_cents: Mapped[int] = mapped_column(Integer, default=0)
    max_uses: Mapped[int] = mapped_column(Integer, default=0)
    used_count: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class CouponRedemption(Base):
    __tablename__ = "coupon_redemptions"
    __table_args__ = (
        UniqueConstraint("coupon_id", "user_id", name="uq_coupon_user_once"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    coupon_id: Mapped[int] = mapped_column(ForeignKey("coupons.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class BalanceLedger(Base):
    __tablename__ = "balance_ledger"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    delta_cents: Mapped[int] = mapped_column(Integer)
    balance_after_cents: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(32))
    reference_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reference_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    user: Mapped["User"] = relationship()


class RechargeOrder(Base):
    __tablename__ = "recharge_orders"
    __table_args__ = (
        UniqueConstraint("chain", "expected_usdt_units", name="uq_recharge_chain_amount"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    chain: Mapped[str] = mapped_column(String(16), index=True)  # tron | polygon
    requested_cny_cents: Mapped[int] = mapped_column(Integer)
    rate_micros: Mapped[int] = mapped_column(Integer)  # CNY per USDT * 1e6
    expected_usdt_units: Mapped[int] = mapped_column(Integer)  # USDT 6 decimals
    deposit_address: Mapped[str] = mapped_column(String(128))
    token_contract: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    tx_hash: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    tx_event_index: Mapped[str | None] = mapped_column(String(32), nullable=True)
    from_address: Mapped[str | None] = mapped_column(String(128), nullable=True)
    confirmations: Mapped[int] = mapped_column(Integer, default=0)
    start_block: Mapped[int | None] = mapped_column(Integer, nullable=True)
    detected_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cancelled_by: Mapped[str | None] = mapped_column(String(24), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    user: Mapped["User"] = relationship()


class ChainTransaction(Base):
    __tablename__ = "chain_transactions"
    __table_args__ = (
        UniqueConstraint("chain", "tx_hash", "event_index", name="uq_chain_tx_event"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    chain: Mapped[str] = mapped_column(String(16), index=True)
    tx_hash: Mapped[str] = mapped_column(String(128), index=True)
    event_index: Mapped[str] = mapped_column(String(32), default="0")
    from_address: Mapped[str | None] = mapped_column(String(128), nullable=True)
    to_address: Mapped[str] = mapped_column(String(128))
    token_contract: Mapped[str] = mapped_column(String(128))
    amount_units: Mapped[int] = mapped_column(Integer)
    block_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    raw_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    event_key: Mapped[str | None] = mapped_column(String(160), unique=True, nullable=True)
    kind: Mapped[str] = mapped_column(String(40), default="system")
    severity: Mapped[str] = mapped_column(String(16), default="info")
    title: Mapped[str] = mapped_column(String(160))
    body: Mapped[str] = mapped_column(Text)
    email_status: Mapped[str] = mapped_column(String(24), default="pending")
    telegram_status: Mapped[str] = mapped_column(String(24), default="pending")
    read_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    user: Mapped["User"] = relationship()


class LoginEvent(Base):
    __tablename__ = "login_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    username: Mapped[str] = mapped_column(String(80), default="")
    ip: Mapped[str] = mapped_column(String(64), index=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    reason: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class LoginSession(Base):
    __tablename__ = "login_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    ip: Mapped[str] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped["User"] = relationship()


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    actor_username: Mapped[str] = mapped_column(String(80), default="system")
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    action: Mapped[str] = mapped_column(String(80), index=True)
    target_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    target_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_type: Mapped[str] = mapped_column(String(48), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    server_id: Mapped[int | None] = mapped_column(ForeignKey("servers.id"), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    available_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)

    user: Mapped["User | None"] = relationship(foreign_keys=[user_id])
    server: Mapped["Server | None"] = relationship(foreign_keys=[server_id])


class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    subject: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(24), default="open", index=True)
    priority: Mapped[str] = mapped_column(String(16), default="normal")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    user: Mapped["User"] = relationship()
    messages: Mapped[list["TicketMessage"]] = relationship(back_populates="ticket", cascade="all, delete-orphan")


class TicketMessage(Base):
    __tablename__ = "ticket_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), index=True)
    author_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    author_is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    ticket: Mapped["Ticket"] = relationship(back_populates="messages")
    author: Mapped["User | None"] = relationship()
