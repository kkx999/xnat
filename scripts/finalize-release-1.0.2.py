#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def write(rel: str, value: str) -> None:
    (ROOT / rel).write_text(value, encoding="utf-8")
    print(f"[write] {rel}")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        if new in text:
            print(f"[skip] {label}")
            return text
        raise RuntimeError(f"missing marker: {label}")
    return text.replace(old, new, 1)


# 1) Persist TOFU TLS fingerprint even when RemoteHostProvider uses detached Host rows.
nodes = read("panel/app/nodes.py")
nodes = replace_once(
    nodes,
    "from sqlalchemy import delete, func, select\n",
    "from sqlalchemy import delete, func, or_, select, update\n",
    "nodes sqlalchemy imports",
)
nodes = replace_once(
    nodes,
    "from . import __version__ as PANEL_VERSION\nfrom .crypto import decrypt_secret\n",
    "from . import __version__ as PANEL_VERSION\nfrom .crypto import decrypt_secret\nfrom .db import SessionLocal\n",
    "nodes SessionLocal import",
)
old_verify = '''def _verify_or_pin_certificate(host: HostNode, base_url: str) -> None:
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
'''
new_verify = '''def _normalized_fingerprint(value: str | None) -> str:
    return str(value or "").strip().lower().replace(":", "")


def _persist_or_validate_tofu_fingerprint(host: HostNode, observed: str) -> str:
    observed = _normalized_fingerprint(observed)
    pinned = _normalized_fingerprint(host.tls_fingerprint)
    if pinned:
        if not hmac.compare_digest(pinned, observed):
            raise HostAPIError(
                f"Host Agent TLS 证书指纹不匹配：期望 {pinned[:16]}…，实际 {observed[:16]}…"
            )
        return pinned

    host_id = int(getattr(host, "id", 0) or 0)
    if not host_id:
        host.tls_fingerprint = observed
        return observed

    with SessionLocal() as db:
        row = db.get(HostNode, host_id)
        if not row:
            raise HostAPIError("宿主机记录不存在，无法保存 TLS 证书指纹")
        current = _normalized_fingerprint(row.tls_fingerprint)
        if not current:
            db.execute(
                update(HostNode)
                .where(
                    HostNode.id == host_id,
                    or_(HostNode.tls_fingerprint.is_(None), HostNode.tls_fingerprint == ""),
                )
                .values(tls_fingerprint=observed)
            )
            db.commit()
            row = db.get(HostNode, host_id)
            current = _normalized_fingerprint(row.tls_fingerprint if row else None)
        if not current:
            raise HostAPIError("Host Agent TLS 证书指纹保存失败")
        if not hmac.compare_digest(current, observed):
            raise HostAPIError(
                f"Host Agent TLS 证书指纹不匹配：期望 {current[:16]}…，实际 {observed[:16]}…"
            )

    host.tls_fingerprint = current
    return current


def _verify_or_pin_certificate(host: HostNode, base_url: str) -> None:
    if not base_url.lower().startswith("https://"):
        return
    observed = _peer_certificate_fingerprint(base_url)
    _persist_or_validate_tofu_fingerprint(host, observed)
'''
nodes = replace_once(nodes, old_verify, new_verify, "persist TOFU certificate fingerprint")
write("panel/app/nodes.py", nodes)


# 2) Send the Panel server_id during reinstall so legacy instances acquire stable metadata.
remote = read("panel/app/providers/remote.py")
anchor = '''    def provision(self, server_id, instance_name, image_alias, memory_mb, disk_gb, cpu, bandwidth_mbps, ssh_port, virtualization_type="lxc"):
'''
helper = '''    def _server_id_for_instance(self, instance_id: str) -> int:
        with SessionLocal() as db:
            server = db.scalar(select(Server).where(
                or_(Server.provider_instance_id == instance_id, Server.name == instance_id),
                Server.deleted_at.is_(None),
            ))
            if not server:
                raise HostAPIError(f"找不到实例 {instance_id} 对应的服务器记录")
            return int(server.id)

    def provision(self, server_id, instance_name, image_alias, memory_mb, disk_gb, cpu, bandwidth_mbps, ssh_port, virtualization_type="lxc"):
'''
remote = replace_once(remote, anchor, helper, "remote server id helper")
old_reinstall = '''    def reinstall(self, instance_id: str, image_alias: str, memory_mb: int, disk_gb: float, cpu: int, bandwidth_mbps: int, ssh_port: int, virtualization_type: str = "lxc") -> ProvisionResult:
        host = self._host_for_instance(instance_id)
        data = host_request(host, "POST", f"/v1/instances/{instance_id}/reinstall", payload={
            "image_alias": image_alias,
            "memory_mb": memory_mb,
            "disk_gb": disk_gb,
            "cpu": cpu,
            "bandwidth_mbps": bandwidth_mbps,
            "ssh_port": ssh_port,
            "virtualization_type": virtualization_type,
        }, timeout=600 if str(virtualization_type).lower() == "kvm" else 260)
'''
new_reinstall = '''    def reinstall(self, instance_id: str, image_alias: str, memory_mb: int, disk_gb: float, cpu: int, bandwidth_mbps: int, ssh_port: int, virtualization_type: str = "lxc") -> ProvisionResult:
        host = self._host_for_instance(instance_id)
        server_id = self._server_id_for_instance(instance_id)
        data = host_request(host, "POST", f"/v1/instances/{instance_id}/reinstall", payload={
            "server_id": server_id,
            "image_alias": image_alias,
            "memory_mb": memory_mb,
            "disk_gb": disk_gb,
            "cpu": cpu,
            "bandwidth_mbps": bandwidth_mbps,
            "ssh_port": ssh_port,
            "virtualization_type": virtualization_type,
        }, timeout=600 if str(virtualization_type).lower() == "kvm" else 260)
'''
remote = replace_once(remote, old_reinstall, new_reinstall, "remote reinstall server id")
write("panel/app/providers/remote.py", remote)

agent = read("agent/natvps_agent/main.py")
agent = replace_once(
    agent,
    '''class ReinstallBody(BaseModel):
    image_alias: str
''',
    '''class ReinstallBody(BaseModel):
    server_id: int | None = None
    image_alias: str
''',
    "Agent reinstall server id field",
)
agent = replace_once(
    agent,
    '''    old_status = instance_status(instance_id)
    stored_server_id = instance_xnat_server_id(instance_id)
    backup_name = (instance_id[:58] + "-xnat-old-" + secrets.token_hex(4))[:79]
''',
    '''    old_status = instance_status(instance_id)
    stored_server_id = instance_xnat_server_id(instance_id)
    requested_server_id = int(body.server_id) if body.server_id is not None else None
    if stored_server_id is not None and requested_server_id is not None and stored_server_id != requested_server_id:
        raise HTTPException(409, "实例 XNAT server_id 与 Panel 请求不一致，拒绝重装")
    effective_server_id = stored_server_id if stored_server_id is not None else requested_server_id
    backup_name = (instance_id[:58] + "-xnat-old-" + secrets.token_hex(4))[:79]
''',
    "Agent reinstall identity validation",
)
agent = replace_once(
    agent,
    '''            body.cpu, body.bandwidth_mbps, mode, server_id=stored_server_id,
''',
    '''            body.cpu, body.bandwidth_mbps, mode, server_id=effective_server_id,
''',
    "Agent reinstall metadata backfill",
)
write("agent/natvps_agent/main.py", agent)


# 3) Mobile purchase surfaces image/disk incompatibility directly instead of wrapping it.
mobile = read("panel/app/mobile_api.py")
mobile = replace_once(
    mobile,
    '''        system_image = db.get(SystemImage, os_image_id)
        if not system_image or not system_image.is_active or system_image.family not in {"apt", "alpine"}:
            raise HTTPException(409, "系统镜像不存在、已停用或暂不支持")
        try:
            coupon, discount = _calculate_coupon_discount(db, user, coupon_code, int(plan.monthly_price_cents or 0))
''',
    '''        system_image = db.get(SystemImage, os_image_id)
        if not system_image or not system_image.is_active or system_image.family not in {"apt", "alpine"}:
            raise HTTPException(409, "系统镜像不存在、已停用或暂不支持")
        try:
            validate_image_resources(system_image, plan.disk_gb, plan.virtualization_type or "lxc")
        except ImagePolicyError as exc:
            raise HTTPException(409, str(exc))
        try:
            coupon, discount = _calculate_coupon_discount(db, user, coupon_code, int(plan.monthly_price_cents or 0))
''',
    "Mobile purchase direct image policy error",
)
write("panel/app/mobile_api.py", mobile)


# 4) Never auto-refund a remote provision whose outcome cannot be proven absent.
jobs = read("panel/app/jobs.py")
run_anchor = '''def run_one_job(provider, provider_name: str) -> bool:
'''
uncertain_helper = '''def _defer_uncertain_provision(db, provider, server: Server, job: Job, message: str) -> bool:
    """Keep funds/state pending until Host confirms absence or idempotent recovery succeeds."""
    recover = getattr(provider, "recover_instance", None)
    if not callable(recover):
        return False
    try:
        probe = recover(server.id, server.name) or {}
    except Exception as exc:
        job.status = "pending"
        job.attempts = max(0, int(job.max_attempts or 1) - 1)
        job.available_at = datetime.utcnow() + timedelta(minutes=5)
        job.finished_at = None
        server.status = "provisioning"
        server.reconcile_status = "warning"
        server.reconcile_message = f"开通结果暂无法确认，等待 Host 恢复后重试：{str(exc)[:600]}"
        queue_admin_notification(
            db,
            title="VPS 开通结果待确认",
            body=f"{server.name} 在达到常规重试上限后仍无法确认 Host 状态。为避免实例已创建却自动退款，任务将在 5 分钟后继续确认。",
            kind="system",
            severity="warning",
            event_key=f"provision-uncertain:{server.id}:{job.id}",
        )
        return True

    if bool(probe.get("exists")) and bool(probe.get("matches")):
        job.status = "pending"
        job.attempts = max(0, int(job.max_attempts or 1) - 1)
        job.available_at = datetime.utcnow() + timedelta(seconds=5)
        job.finished_at = None
        server.status = "provisioning"
        server.reconcile_status = "warning"
        server.reconcile_message = "Host 已找到匹配实例，等待幂等开通重试回收连接信息。"
        return True

    if bool(probe.get("exists")) and not bool(probe.get("matches")):
        job.status = "failed"
        job.finished_at = datetime.utcnow()
        server.status = "provision_unknown"
        server.reconcile_status = "error"
        server.reconcile_message = "Host 存在同名实例，但 XNAT server_id 不匹配；已停止自动退款并要求人工检查。"
        queue_admin_notification(
            db,
            title="VPS 开通身份冲突",
            body=f"{server.name} 在 Host 上存在同名实例，但 server_id 不匹配。系统未自动退款，请人工核对后处理。",
            kind="system",
            severity="error",
            event_key=f"provision-identity-conflict:{server.id}:{job.id}",
        )
        return True

    # Host explicitly confirmed that no instance exists; normal final-failure refund is safe.
    return False


def run_one_job(provider, provider_name: str) -> bool:
'''
jobs = replace_once(jobs, run_anchor, uncertain_helper, "uncertain provision safeguard helper")
jobs = replace_once(
    jobs,
    '''                else:
                    job.status = "failed"
                    job.finished_at = datetime.utcnow()
                    if job.job_type == "provision_server" and server:
''',
    '''                else:
                    if job.job_type == "provision_server" and server and _defer_uncertain_provision(db, provider, server, job, message):
                        db.commit()
                        return True
                    job.status = "failed"
                    job.finished_at = datetime.utcnow()
                    if job.job_type == "provision_server" and server:
''',
    "provision refund only after confirmed absence",
)
write("panel/app/jobs.py", jobs)

print("XNAT 1.0.2 final hardening patch complete")
