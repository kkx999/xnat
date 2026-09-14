#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def path(rel: str) -> Path:
    return ROOT / rel


def read(rel: str) -> str:
    return path(rel).read_text(encoding="utf-8")


def write(rel: str, text: str) -> None:
    path(rel).write_text(text, encoding="utf-8")
    print(f"[write] {rel}")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        if new in text:
            print(f"[skip] {label}")
            return text
        raise RuntimeError(f"missing marker: {label}")
    return text.replace(old, new, 1)


def regex_once(text: str, pattern: str, replacement: str, label: str, flags: int = 0) -> str:
    result, count = re.subn(pattern, replacement, text, count=1, flags=flags)
    if count != 1:
        raise RuntimeError(f"regex marker count={count}: {label}")
    return result


# Models --------------------------------------------------------------------
models = read("panel/app/models.py")
models = replace_once(
    models,
    '    family: Mapped[str] = mapped_column(String(32), default="apt")\n'
    '    is_active: Mapped[bool] = mapped_column(Boolean, default=True)\n',
    '    family: Mapped[str] = mapped_column(String(32), default="apt")\n'
    '    # Minimum root disk accepted by this image. Web/Mobile/worker share this policy.\n'
    '    min_disk_gb: Mapped[float] = mapped_column(Float, default=2.0)\n'
    '    is_active: Mapped[bool] = mapped_column(Boolean, default=True)\n',
    "SystemImage.min_disk_gb",
)
models = replace_once(
    models,
    '    verify_tls: Mapped[bool] = mapped_column(Boolean, default=False)\n',
    '    verify_tls: Mapped[bool] = mapped_column(Boolean, default=False)\n'
    '    # TOFU SHA-256 fingerprint for the Host Agent HTTPS certificate.\n'
    '    tls_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)\n',
    "HostNode.tls_fingerprint",
)
if "class HostPortLease(Base):" not in models:
    models = replace_once(
        models,
        "\n\nclass SiteSetting(Base):\n",
        '''\n\nclass HostPortLease(Base):
    """Short-lived reservation that closes the allocate-then-use race window."""

    __tablename__ = "host_port_leases"
    __table_args__ = (
        UniqueConstraint("host_id", "protocol", "public_port", name="uq_host_port_lease"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    host_id: Mapped[int] = mapped_column(ForeignKey("host_nodes.id", ondelete="CASCADE"), index=True)
    protocol: Mapped[str] = mapped_column(String(8))
    public_port: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class SiteSetting(Base):
''',
        "HostPortLease",
    )
write("panel/app/models.py", models)


# Additive schema -----------------------------------------------------------
schema = read("panel/app/schema.py")
schema = replace_once(
    schema,
    '    "users": {\n',
    '    "system_images": {\n'
    '        "min_disk_gb": "FLOAT NOT NULL DEFAULT 2.0",\n'
    '    },\n'
    '    "users": {\n',
    "schema SystemImage min disk",
)
schema = replace_once(
    schema,
    '        "kvm_available": "BOOLEAN NOT NULL DEFAULT 0",\n',
    '        "kvm_available": "BOOLEAN NOT NULL DEFAULT 0",\n'
    '        "tls_fingerprint": "VARCHAR(64)",\n',
    "schema Host fingerprint",
)
if 'if "system_images" in existing_tables:' not in schema:
    schema = replace_once(
        schema,
        '        # Paid traffic reset was introduced after v1.1.1. For existing plans,\n',
        '''        if "system_images" in existing_tables:
            image_columns = {row["name"] for row in inspect(conn).get_columns("system_images")}
            if "min_disk_gb" in image_columns:
                conn.execute(text(
                    'UPDATE "system_images" SET "min_disk_gb" = 1.0 '
                    'WHERE LOWER("alias") LIKE \'images:alpine/%\''
                ))
                conn.execute(text(
                    'UPDATE "system_images" SET "min_disk_gb" = 2.0 '
                    'WHERE LOWER("alias") LIKE \'images:ubuntu/%\' OR LOWER("alias") LIKE \'images:debian/%\''
                ))

        # Existing traffic-reset migration behavior.
''',
        "schema image backfill",
    )
write("panel/app/schema.py", schema)


# Jobs: final policy guard + atomic claim ----------------------------------
jobs = read("panel/app/jobs.py")
jobs = replace_once(jobs, "from sqlalchemy import select\n", "from sqlalchemy import select, update\n", "jobs update import")
jobs = replace_once(
    jobs,
    "from .traffic import apply_sample, ensure_cycle\n",
    "from .traffic import apply_sample, ensure_cycle\nfrom .services.image_policy import validate_image_resources\n",
    "jobs image policy import",
)
jobs = replace_once(
    jobs,
    "    row = Job(\n",
    '    payload_data = dict(payload or {})\n'
    '    if server_id and job_type in {"provision_server", "reinstall_server", "delete_server"}:\n'
    '        payload_data.setdefault("operation_id", f"{job_type}:{server_id}")\n'
    '    row = Job(\n',
    "job operation id",
)
jobs = replace_once(
    jobs,
    '        payload_json=json.dumps(payload or {}, ensure_ascii=False, separators=(",", ":")),\n',
    '        payload_json=json.dumps(payload_data, ensure_ascii=False, separators=(",", ":")),\n',
    "job payload_data",
)
jobs = replace_once(
    jobs,
    '    if not server.ssh_port:\n        raise RuntimeError("服务器没有预分配 SSH 端口")\n\n    result = provider.provision(\n',
    '    if not server.ssh_port:\n'
    '        raise RuntimeError("服务器没有预分配 SSH 端口")\n'
    '    image = db.get(SystemImage, int(server.os_image_id or 0)) if server.os_image_id else None\n'
    '    if image:\n'
    '        validate_image_resources(image, server.disk_gb, server.virtualization_type or "lxc")\n\n'
    '    result = provider.provision(\n',
    "provision final policy",
)
jobs = replace_once(
    jobs,
    '    if not server.provider_instance_id:\n        raise RuntimeError("服务器没有 provider 实例 ID")\n\n    result = provider.reinstall(\n',
    '    if not server.provider_instance_id:\n'
    '        raise RuntimeError("服务器没有 provider 实例 ID")\n'
    '    validate_image_resources(image, server.disk_gb, server.virtualization_type or "lxc")\n\n'
    '    result = provider.reinstall(\n',
    "reinstall final policy",
)
if "def _claim_next_job_id" not in jobs:
    jobs = replace_once(
        jobs,
        "\ndef run_one_job(provider, provider_name: str) -> bool:\n",
        '''
def _claim_next_job_id(db, now: datetime) -> int | None:
    """Atomically transition one due job from pending to running."""
    for _ in range(8):
        candidate_id = db.scalar(
            select(Job.id)
            .where(Job.status == "pending", Job.available_at <= now)
            .order_by(Job.id)
            .limit(1)
        )
        if not candidate_id:
            return None
        result = db.execute(
            update(Job)
            .where(Job.id == candidate_id, Job.status == "pending", Job.available_at <= now)
            .values(status="running", started_at=now, attempts=Job.attempts + 1)
        )
        if int(result.rowcount or 0) == 1:
            db.commit()
            return int(candidate_id)
        db.rollback()
    return None


def run_one_job(provider, provider_name: str) -> bool:
''',
        "atomic job claim helper",
    )
jobs = regex_once(
    jobs,
    r'''def run_one_job\(provider, provider_name: str\) -> bool:\n    now = datetime\.utcnow\(\)\n    with SessionLocal\(\) as db:\n        job = db\.scalar\(\n            select\(Job\)\n            \.where\(Job\.status == "pending", Job\.available_at <= now\)\n            \.order_by\(Job\.id\)\n            \.limit\(1\)\n        \)\n        if not job:\n            return False\n\n        job\.status = "running"\n        job\.started_at = now\n        job\.attempts = int\(job\.attempts or 0\) \+ 1\n        db\.commit\(\)\n        job_id = job\.id\n''',
    '''def run_one_job(provider, provider_name: str) -> bool:
    now = datetime.utcnow()
    with SessionLocal() as db:
        job_id = _claim_next_job_id(db, now)
        if not job_id:
            return False
''',
    "replace non-atomic claim",
    flags=re.M,
)
write("panel/app/jobs.py", jobs)


# Host communication: API v2, cert pinning, port leases -------------------
nodes = read("panel/app/nodes.py")
nodes = replace_once(nodes, "import math\nimport time\n", "import math\nimport time\nimport secrets\nimport socket\nimport ssl\n", "nodes security imports")
nodes = replace_once(nodes, "from sqlalchemy import func, select\n", "from sqlalchemy import delete, func, select\nfrom sqlalchemy.exc import IntegrityError\n", "nodes sqlalchemy imports")
nodes = replace_once(nodes, "from .crypto import decrypt_secret\n", "from . import __version__ as PANEL_VERSION\nfrom .crypto import decrypt_secret\n", "nodes version import")
nodes = replace_once(nodes, "from .models import HostNode, Plan, PlanHost, PortMapping, Server, SiteSetting\n", "from .models import HostNode, HostPortLease, Plan, PlanHost, PortMapping, Server, SiteSetting\n", "nodes lease import")
nodes = nodes.replace('SUPPORTED_AGENT_API_VERSIONS = {"1"}', 'SUPPORTED_AGENT_API_VERSIONS = {"1", "2"}')
nodes = regex_once(
    nodes,
    r'''def _signature\(token: str, timestamp: str, method: str, path: str, body: bytes\) -> str:\n    digest = hashlib\.sha256\(body\)\.hexdigest\(\)\n    message = f"\{timestamp\}\\\\n\{method\.upper\(\)\}\\\\n\{path\}\\\\n\{digest\}"\.encode\("utf-8"\)\n    return hmac\.new\(token\.encode\("utf-8"\), message, hashlib\.sha256\)\.hexdigest\(\)\n''',
    '''def _signature(token: str, timestamp: str, method: str, path: str, body: bytes, nonce: str = "") -> str:
    digest = hashlib.sha256(body).hexdigest()
    if nonce:
        message = f"{timestamp}\\n{nonce}\\n{method.upper()}\\n{path}\\n{digest}".encode("utf-8")
    else:
        message = f"{timestamp}\\n{method.upper()}\\n{path}\\n{digest}".encode("utf-8")
    return hmac.new(token.encode("utf-8"), message, hashlib.sha256).hexdigest()


def _peer_certificate_fingerprint(base_url: str, timeout: float = 8.0) -> str:
    parsed = urlparse(base_url)
    if parsed.scheme.lower() != "https":
        return ""
    hostname = parsed.hostname
    if not hostname:
        raise HostAPIError("宿主机 HTTPS URL 缺少主机名")
    port = int(parsed.port or 443)
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    with socket.create_connection((hostname, port), timeout=timeout) as raw:
        with context.wrap_socket(raw, server_hostname=hostname) as tls:
            cert = tls.getpeercert(binary_form=True)
    if not cert:
        raise HostAPIError("无法读取 Host Agent TLS 证书")
    return hashlib.sha256(cert).hexdigest()


def _verify_or_pin_certificate(host: HostNode, base_url: str) -> None:
    if not base_url.lower().startswith("https://"):
        return
    observed = _peer_certificate_fingerprint(base_url)
    pinned = str(host.tls_fingerprint or "").strip().lower().replace(":", "")
    if pinned and not hmac.compare_digest(pinned, observed):
        raise HostAPIError(
            f"Host Agent TLS 证书指纹不匹配：期望 {pinned[:16]}…，实际 {observed[:16]}…"
        )
    if not pinned:
        host.tls_fingerprint = observed
''',
    "signature and certificate pinning",
    flags=re.M,
)
nodes = regex_once(
    nodes,
    r'''def host_request\(host: HostNode, method: str, path: str, \*, payload=None, timeout: float = 25\.0\):\n.*?\n\ndef refresh_host''',
    '''def host_request(host: HostNode, method: str, path: str, *, payload=None, timeout: float = 25.0):
    token = decrypt_secret(host.api_token_enc or "")
    if not token:
        raise HostAPIError("宿主机 API Token 无法解密或为空")
    base = (host.api_url or "").rstrip("/")
    if not base.startswith(("http://", "https://")):
        raise HostAPIError("宿主机 Agent URL 无效")
    if not path.startswith("/"):
        path = "/" + path

    _verify_or_pin_certificate(host, base)
    body = _body_bytes(payload)

    def do_request(api_version: str):
        ts = str(int(time.time()))
        nonce = secrets.token_hex(16) if str(api_version) == "2" else ""
        headers = {
            "X-NAT-Timestamp": ts,
            "X-NAT-Signature": _signature(token, ts, method, path, body, nonce),
            "Content-Type": "application/json",
            "User-Agent": f"XNAT-Panel/{PANEL_VERSION}",
        }
        if nonce:
            headers["X-NAT-Nonce"] = nonce
        with httpx.Client(verify=bool(host.verify_tls), timeout=timeout) as client:
            return client.request(
                method.upper(), base + path,
                content=body if payload is not None else None,
                headers=headers,
            )

    api_version = "2" if str(host.agent_api_version or "") == "2" else "1"
    try:
        response = do_request(api_version)
        if response.status_code in {401, 426} and api_version != "2":
            try:
                with httpx.Client(verify=bool(host.verify_tls), timeout=min(timeout, 10.0)) as client:
                    health = client.get(base + "/health")
                if health.status_code < 400 and str(health.json().get("api_version") or "") == "2":
                    host.agent_api_version = "2"
                    response = do_request("2")
            except Exception:
                pass
    except Exception as exc:
        raise HostAPIError(f"连接宿主机失败: {exc}") from exc

    if response.status_code >= 400:
        try:
            detail = response.json().get("detail") or response.text
        except Exception:
            detail = response.text
        raise HostAPIError(f"Host Agent HTTP {response.status_code}: {str(detail)[:1200]}")
    if not response.content:
        return {}
    try:
        return response.json()
    except Exception as exc:
        raise HostAPIError("宿主机返回了无效 JSON") from exc


def refresh_host''',
    "host_request v2",
    flags=re.S,
)
if "def _lease_port(" not in nodes:
    nodes = replace_once(
        nodes,
        "def public_port_in_use_on_host(db, host_id: int, port: int, protocol: str) -> bool:\n",
        '''def _lease_port(db, host: HostNode, protocol: str, port: int, ttl_seconds: int = 180) -> bool:
    now = datetime.utcnow()
    db.execute(delete(HostPortLease).where(HostPortLease.expires_at <= now))
    try:
        with db.begin_nested():
            db.add(HostPortLease(
                host_id=host.id,
                protocol=protocol,
                public_port=port,
                expires_at=now + timedelta(seconds=max(30, ttl_seconds)),
            ))
            db.flush()
        return True
    except IntegrityError:
        return False


def public_port_in_use_on_host(db, host_id: int, port: int, protocol: str) -> bool:
''',
        "port lease helper",
    )
nodes = replace_once(
    nodes,
    '    if protocol == "tcp" and db.scalar(\n'
    '        select(Server).where(\n'
    '            Server.host_id == host_id,\n'
    '            Server.ssh_port == port,\n'
    '            Server.deleted_at.is_(None),\n'
    '        )\n'
    '    ):\n'
    '        return True\n'
    '    return False\n',
    '    if protocol == "tcp" and db.scalar(\n'
    '        select(Server).where(\n'
    '            Server.host_id == host_id,\n'
    '            Server.ssh_port == port,\n'
    '            Server.deleted_at.is_(None),\n'
    '        )\n'
    '    ):\n'
    '        return True\n'
    '    now = datetime.utcnow()\n'
    '    if db.scalar(select(HostPortLease).where(\n'
    '        HostPortLease.host_id == host_id,\n'
    '        HostPortLease.protocol == protocol,\n'
    '        HostPortLease.public_port == port,\n'
    '        HostPortLease.expires_at > now,\n'
    '    )):\n'
    '        return True\n'
    '    return False\n',
    "port lease used check",
)
nodes = replace_once(
    nodes,
    '        if not public_port_in_use_on_host(db, host.id, port, protocol):\n            return port\n',
    '        if public_port_in_use_on_host(db, host.id, port, protocol):\n'
    '            continue\n'
    '        if _lease_port(db, host, protocol, port):\n'
    '            return port\n',
    "atomic port allocation",
)
nodes = nodes.replace("v1.6.3:", "current:")
write("panel/app/nodes.py", nodes)


# Remote provider + reconciliation orphan recovery -------------------------
remote = read("panel/app/providers/remote.py")
remote = replace_once(remote, "from sqlalchemy import select\n", "from sqlalchemy import or_, select\n", "remote or_ import")
remote = replace_once(
    remote,
    '            server = db.scalar(select(Server).where(Server.provider_instance_id == instance_id, Server.deleted_at.is_(None)))\n',
    '            server = db.scalar(select(Server).where(\n'
    '                or_(Server.provider_instance_id == instance_id, Server.name == instance_id),\n'
    '                Server.deleted_at.is_(None),\n'
    '            ))\n',
    "remote lookup fallback",
)
if "def recover_instance(" not in remote:
    remote = replace_once(
        remote,
        "    def power_action(self, instance_id: str, action: str) -> str:\n",
        '''    def recover_instance(self, server_id: int, instance_name: str) -> dict:
        host = self._host_for_server_id(server_id)
        return host_request(
            host,
            "GET",
            f"/v1/servers/{int(server_id)}/instances/{instance_name}",
            timeout=25,
        )

    def power_action(self, instance_id: str, action: str) -> str:
''',
        "remote recovery method",
    )
write("panel/app/providers/remote.py", remote)

reconcile = read("panel/app/reconcile.py")
reconcile = replace_once(
    reconcile,
    '    if server.deleted_at is not None or not server.provider_instance_id:\n'
    '        server.reconcile_status = "ignored"\n'
    '        server.reconcile_message = None\n'
    '        server.reconciled_at = now\n'
    '        return {"status": "ignored", "changes": []}\n\n'
    '    state = provider.inspect(server.provider_instance_id)\n'
    '    changes: list[str] = []\n',
    '    if server.deleted_at is not None:\n'
    '        server.reconcile_status = "ignored"\n'
    '        server.reconcile_message = None\n'
    '        server.reconciled_at = now\n'
    '        return {"status": "ignored", "changes": []}\n\n'
    '    changes: list[str] = []\n'
    '    if not server.provider_instance_id:\n'
    '        recover = getattr(provider, "recover_instance", None)\n'
    '        if not callable(recover):\n'
    '            server.reconcile_status = "ignored"\n'
    '            server.reconcile_message = None\n'
    '            server.reconciled_at = now\n'
    '            return {"status": "ignored", "changes": []}\n'
    '        recovered = recover(server.id, server.name)\n'
    '        if not recovered or not recovered.get("exists") or not recovered.get("matches"):\n'
    '            server.reconcile_status = "error"\n'
    '            server.reconcile_message = "Panel 未记录 provider_instance_id，Host 上也未找到匹配的 XNAT 实例。"\n'
    '            server.reconciled_at = now\n'
    '            return {"status": "error", "changes": [], "errors": [server.reconcile_message]}\n'
    '        server.provider_instance_id = server.name\n'
    '        changes.append("恢复 provider 实例关联")\n\n'
    '    state = provider.inspect(server.provider_instance_id)\n',
    "reconcile orphan recovery",
)
reconcile = replace_once(reconcile, '            Server.provider_instance_id.is_not(None),\n', '', "reconcile all active servers")
write("panel/app/reconcile.py", reconcile)


# Existing Web routes: pre-queue checks only; UI/templates untouched --------
main = read("panel/app/main.py")
main = replace_once(
    main,
    "from .service_actions import ServiceActionError, enqueue_server_delete, reset_server_traffic\n",
    "from .service_actions import ServiceActionError, enqueue_server_delete, reset_server_traffic\n"
    "from .services.image_policy import ImagePolicyError, minimum_disk_for_alias, validate_image_resources\n",
    "main image policy import",
)
main = replace_once(
    main,
    'def queue_service_provision(db, user, plan, system_image, *, order_amount_cents: int, order_kind: str, coupon=None, discount_cents: int = 0):\n'
    '    inventory = plan_stock(db, plan)\n',
    'def queue_service_provision(db, user, plan, system_image, *, order_amount_cents: int, order_kind: str, coupon=None, discount_cents: int = 0):\n'
    '    validate_image_resources(system_image, plan.disk_gb, plan.virtualization_type or "lxc")\n'
    '    inventory = plan_stock(db, plan)\n',
    "main shared provisioning policy",
)
main = replace_once(
    main,
    '''        system_image = db.get(SystemImage, os_image_id)
        if not system_image or not system_image.is_active or system_image.family not in {"apt", "alpine"}:
            raise HTTPException(400, "系统镜像不存在、已停用或暂不支持")

        try:
            coupon, discount_cents = calculate_coupon_discount''',
    '''        system_image = db.get(SystemImage, os_image_id)
        if not system_image or not system_image.is_active or system_image.family not in {"apt", "alpine"}:
            raise HTTPException(400, "系统镜像不存在、已停用或暂不支持")
        try:
            validate_image_resources(system_image, plan.disk_gb, plan.virtualization_type or "lxc")
        except ImagePolicyError as exc:
            flash(request, str(exc), "error")
            return RedirectResponse("/plans", status_code=303)

        try:
            coupon, discount_cents = calculate_coupon_discount''',
    "web purchase preflight",
)
main = replace_once(
    main,
    '''        system_image = db.get(SystemImage, os_image_id)
        if not system_image or not system_image.is_active or system_image.family not in {"apt", "alpine"}:
            flash(request, "所选系统镜像不可用。", "error")
            return RedirectResponse(f"/servers/{server.id}", status_code=303)
        active_job = db.scalar''',
    '''        system_image = db.get(SystemImage, os_image_id)
        if not system_image or not system_image.is_active or system_image.family not in {"apt", "alpine"}:
            flash(request, "所选系统镜像不可用。", "error")
            return RedirectResponse(f"/servers/{server.id}", status_code=303)
        try:
            validate_image_resources(system_image, server.disk_gb, server.virtualization_type or "lxc")
        except ImagePolicyError as exc:
            flash(request, str(exc), "error")
            return RedirectResponse(f"/servers/{server.id}", status_code=303)
        active_job = db.scalar''',
    "web reinstall preflight",
)
main = main.replace('SystemImage(name="Debian 12", alias="images:debian/12", family="apt", sort_order=10)', 'SystemImage(name="Debian 12", alias="images:debian/12", family="apt", min_disk_gb=2.0, sort_order=10)')
main = main.replace('SystemImage(name="Debian 13", alias="images:debian/13", family="apt", sort_order=20)', 'SystemImage(name="Debian 13", alias="images:debian/13", family="apt", min_disk_gb=2.0, sort_order=20)')
main = main.replace('SystemImage(name="Ubuntu 22.04 LTS", alias="images:ubuntu/22.04", family="apt", sort_order=30)', 'SystemImage(name="Ubuntu 22.04 LTS", alias="images:ubuntu/22.04", family="apt", min_disk_gb=2.0, sort_order=30)')
main = main.replace('SystemImage(name="Ubuntu 24.04 LTS", alias="images:ubuntu/24.04", family="apt", sort_order=40)', 'SystemImage(name="Ubuntu 24.04 LTS", alias="images:ubuntu/24.04", family="apt", min_disk_gb=2.0, sort_order=40)')
main = main.replace('SystemImage(name="Alpine 3.24", alias="images:alpine/3.24", family="alpine", sort_order=50)', 'SystemImage(name="Alpine 3.24", alias="images:alpine/3.24", family="alpine", min_disk_gb=1.0, sort_order=50)')
main = main.replace(
    'SystemImage(name=name,alias=alias,family=("alpine" if alias.lower().startswith("images:alpine/") else "apt"),is_active=True,sort_order=100)',
    'SystemImage(name=name,alias=alias,family=("alpine" if alias.lower().startswith("images:alpine/") else "apt"),min_disk_gb=minimum_disk_for_alias(alias, ("alpine" if alias.lower().startswith("images:alpine/") else "apt")).min_disk_gb,is_active=True,sort_order=100)',
)
write("panel/app/main.py", main)


# Mobile API: same shared policy -------------------------------------------
mobile = read("panel/app/mobile_api.py")
mobile = replace_once(
    mobile,
    "from .service_actions import ServiceActionError, enqueue_server_delete, reset_server_traffic, traffic_reset_state\n",
    "from .service_actions import ServiceActionError, enqueue_server_delete, reset_server_traffic, traffic_reset_state\n"
    "from .services.image_policy import ImagePolicyError, validate_image_resources\n",
    "mobile image policy import",
)
mobile = replace_once(
    mobile,
    'def _queue_purchase_service(db, user: User, plan: Plan, system_image: SystemImage, *, final_price: int, coupon=None, discount_cents: int = 0, client_request_id: str = ""):\n'
    '    if _plan_stock(db, plan)["sold_out"]:\n',
    'def _queue_purchase_service(db, user: User, plan: Plan, system_image: SystemImage, *, final_price: int, coupon=None, discount_cents: int = 0, client_request_id: str = ""):\n'
    '    validate_image_resources(system_image, plan.disk_gb, plan.virtualization_type or "lxc")\n'
    '    if _plan_stock(db, plan)["sold_out"]:\n',
    "mobile purchase shared policy",
)
mobile = replace_once(
    mobile,
    '''        system_image = db.get(SystemImage, os_image_id)
        if not system_image or not system_image.is_active or system_image.family not in {"apt", "alpine"}:
            raise HTTPException(409, "所选系统镜像不可用")
        active_job = db.scalar(
''',
    '''        system_image = db.get(SystemImage, os_image_id)
        if not system_image or not system_image.is_active or system_image.family not in {"apt", "alpine"}:
            raise HTTPException(409, "所选系统镜像不可用")
        try:
            validate_image_resources(system_image, server.disk_gb, server.virtualization_type or "lxc")
        except ImagePolicyError as exc:
            raise HTTPException(409, str(exc))
        active_job = db.scalar(
''',
    "mobile reinstall preflight",
)
write("panel/app/mobile_api.py", mobile)


# Release/version hygiene ---------------------------------------------------
write("VERSION", "1.0.2\n")
write("panel/app/__init__.py", '__version__ = "1.0.2"\n')
write(
    "release.json",
    json.dumps(
        {
            "release_version": "1.0.2",
            "panel_version": "1.0.2",
            "agent_version": "1.0.1",
            "agent_api_version": "2",
            "supported_agent_api_versions": ["1", "2"],
            "mobile_api_version": "1",
        },
        ensure_ascii=False,
        indent=2,
    ) + "\n",
)
for rel in (
    "scripts/upgrade-panel-from-v1.0.2.sh",
    "scripts/upgrade-panel-from-v1.1.0.sh",
    "scripts/upgrade-panel-from-v1.1.1.sh",
):
    candidate = path(rel)
    if candidate.exists():
        candidate.unlink()
        print(f"[remove] {rel}")

changelog = read("CHANGELOG.md")
entry = '''## v1.0.2 - 2026-09-14

- Panel 1.0.2 / Host Agent 1.0.1 / Agent API 2; Mobile API remains v1.
- Added image/disk preflight on Web, Mobile, background jobs and Host Agent. Alpine keeps 1 GiB support; Debian/Ubuntu require 2 GiB; KVM requires at least 4 GiB disk.
- Fixed Alpine minimal-image SSH readiness by installing `iproute2` and using a fallback listener check.
- Provisioning is idempotent by XNAT server identity and reconciliation can recover matching orphaned instances.
- Job claiming now uses an atomic conditional update; NAT/SSH allocation uses short-lived unique Host port leases.
- Agent API 2 signs a nonce and rejects replayed mutations. Panel supports API 1 and 2 for staged upgrades.
- HTTPS Host connections pin the SHA-256 certificate fingerprint on first trusted contact and reject later certificate changes.
- Reinstall uses a rollback-safe blue/green flow: the old instance is retained until the replacement is fully ready.
- Cross-surface validation now lives in `panel/app/services`; templates, static assets, page layout and existing interaction flow are unchanged.
- Removed obsolete development-line Panel upgrade scripts and normalized release/version strings.

'''
if not changelog.startswith("## v1.0.2"):
    changelog = entry + changelog
write("CHANGELOG.md", changelog)

print("Panel/core patch complete")
