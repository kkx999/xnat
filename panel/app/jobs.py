from __future__ import annotations

import json
from datetime import datetime, timedelta

from sqlalchemy import select

from .audit import write_audit
from .crypto import encrypt_secret
from .db import SessionLocal
from .models import BalanceLedger, Job, Order, PortMapping, Server, SystemImage, User
from .notifications import queue_notification
from .traffic import apply_sample, ensure_cycle


def enqueue_job(db, job_type: str, *, user_id: int | None = None, server_id: int | None = None, payload: dict | None = None, max_attempts: int = 3) -> Job:
    row = Job(
        job_type=job_type,
        user_id=user_id,
        server_id=server_id,
        status="pending",
        payload_json=json.dumps(payload or {}, ensure_ascii=False, separators=(",", ":")),
        max_attempts=max(1, max_attempts),
    )
    db.add(row)
    db.flush()
    return row


def _payload(job: Job) -> dict:
    try:
        return json.loads(job.payload_json or "{}")
    except Exception:
        return {}


def _refund_failed_provision(db, server: Server):
    order = db.get(Order, server.order_id)
    if not order or order.status == "refunded":
        return
    order.status = "failed"
    if order.amount_cents <= 0:
        return
    user = db.get(User, server.user_id)
    if not user:
        return
    user.balance_cents += order.amount_cents
    db.flush()
    db.add(BalanceLedger(
        user_id=user.id,
        delta_cents=order.amount_cents,
        balance_after_cents=user.balance_cents,
        kind="provision_refund",
        reference_type="order",
        reference_id=order.id,
        note=f"开通任务失败退款: {server.name}",
    ))
    order.status = "refunded"


def _run_provision(db, provider, server: Server, job: Job):
    if server.deleted_at is not None:
        return {"noop": "deleted"}
    if server.provider_instance_id and server.status in {"running", "stopped"}:
        return {"noop": "already_provisioned"}
    if not server.ssh_port:
        raise RuntimeError("服务器没有预分配 SSH 端口")

    result = provider.provision(
        server.id,
        server.name,
        server.os_alias,
        server.memory_mb,
        server.disk_gb,
        server.cpu,
        server.bandwidth_mbps or 0,
        server.ssh_port,
    )
    server.provider_instance_id = result.instance_id
    server.private_ip = result.private_ip
    server.ssh_port = result.ssh_port
    server.status = result.status
    server.reconcile_status = "ok"
    server.reconcile_message = None
    server.reconciled_at = datetime.utcnow()
    if result.root_password:
        server.root_password_enc = encrypt_secret(result.root_password)

    order = db.get(Order, server.order_id)
    if order:
        order.status = "completed"

    now = datetime.utcnow()
    ensure_cycle(server, now)
    try:
        stats = provider.network_stats(server.provider_instance_id)
        apply_sample(server, stats, now, seed_first_sample=True)
    except Exception:
        pass

    user = db.get(User, server.user_id)
    if user:
        queue_notification(
            db,
            user,
            title="VPS 开通成功",
            body=f"{server.name} 已开通。SSH：{server.public_ip}:{server.ssh_port}，系统：{server.os_name}。root 密码可在服务器详情页查看。",
            kind="server",
            severity="success",
            event_key=f"provisioned:{server.id}:{server.order_id}",
        )
    write_audit(db, actor_username="system", action="server.provision.completed", target_type="server", target_id=server.id, target_name=server.name)
    return {"status": server.status, "private_ip": server.private_ip, "ssh_port": server.ssh_port}


def _run_reinstall(db, provider, server: Server, job: Job):
    payload = _payload(job)
    image = db.get(SystemImage, int(payload.get("os_image_id") or 0))
    if not image or not image.is_active:
        raise RuntimeError("目标系统镜像不可用")
    if not server.provider_instance_id:
        raise RuntimeError("服务器没有 provider 实例 ID")

    result = provider.reinstall(
        server.provider_instance_id,
        image.alias,
        server.memory_mb,
        server.disk_gb,
        server.cpu,
        server.bandwidth_mbps or 0,
        server.ssh_port,
    )
    # Reinstall removes custom NAT proxy devices; reflect that in DB.
    for mapping in list(server.ports):
        db.delete(mapping)
    server.private_ip = result.private_ip
    server.status = result.status
    server.os_image_id = image.id
    server.os_name = image.name
    server.os_alias = image.alias
    server.root_password_enc = encrypt_secret(result.root_password) if result.root_password else None
    server.traffic_last_rx_bytes = None
    server.traffic_last_tx_bytes = None
    server.traffic_last_sampled_at = datetime.utcnow()
    server.reconcile_status = "ok"
    server.reconcile_message = None
    server.reconciled_at = datetime.utcnow()

    user = db.get(User, server.user_id)
    if user:
        queue_notification(
            db,
            user,
            title="系统重装完成",
            body=f"{server.name} 已重装为 {image.name}。原系统数据已清空，新的 root 密码可在服务器详情页查看。",
            kind="server",
            severity="success",
            event_key=f"reinstall:{server.id}:{job.id}",
        )
    write_audit(db, actor_username="system", action="server.reinstall.completed", target_type="server", target_id=server.id, target_name=server.name, detail={"image": image.name})
    return {"image": image.name, "status": server.status}


def _run_delete(db, provider, server: Server, job: Job):
    if server.deleted_at is not None:
        return {"noop": "already_deleted"}
    if server.provider_instance_id:
        provider.delete(server.provider_instance_id)
    server.status = "deleted"
    server.deleted_at = datetime.utcnow()
    server.root_password_enc = None
    server.reconcile_status = "deleted"
    user = db.get(User, server.user_id)
    if user:
        queue_notification(
            db,
            user,
            title="VPS 已删除",
            body=f"{server.name} 已永久删除。",
            kind="server",
            severity="warning",
            event_key=f"deleted:{server.id}:{job.id}",
        )
    write_audit(db, actor_username="system", action="server.delete.completed", target_type="server", target_id=server.id, target_name=server.name)
    return {"deleted": True}


def run_one_job(provider, provider_name: str) -> bool:
    now = datetime.utcnow()
    with SessionLocal() as db:
        job = db.scalar(
            select(Job)
            .where(Job.status == "pending", Job.available_at <= now)
            .order_by(Job.id)
            .limit(1)
        )
        if not job:
            return False

        job.status = "running"
        job.started_at = now
        job.attempts = int(job.attempts or 0) + 1
        db.commit()
        job_id = job.id

    with SessionLocal() as db:
        job = db.get(Job, job_id)
        try:
            server = db.get(Server, job.server_id) if job.server_id else None
            if job.job_type in {"provision_server", "reinstall_server", "delete_server"} and not server:
                raise RuntimeError("任务关联服务器不存在")
            if server and server.provider != provider_name:
                raise RuntimeError("Provider 不匹配")

            if job.job_type == "provision_server":
                result = _run_provision(db, provider, server, job)
            elif job.job_type == "reinstall_server":
                result = _run_reinstall(db, provider, server, job)
            elif job.job_type == "delete_server":
                result = _run_delete(db, provider, server, job)
            else:
                raise RuntimeError(f"未知任务类型: {job.job_type}")

            job.status = "completed"
            job.result_json = json.dumps(result or {}, ensure_ascii=False, separators=(",", ":"))
            job.error_text = None
            job.finished_at = datetime.utcnow()
            db.commit()
            return True
        except Exception as exc:
            db.rollback()
            job = db.get(Job, job_id)
            server = db.get(Server, job.server_id) if job and job.server_id else None
            message = str(exc)[:1000]
            if job:
                job.error_text = message
                if job.attempts < job.max_attempts:
                    job.status = "pending"
                    job.available_at = datetime.utcnow() + timedelta(seconds=min(60, 5 * (2 ** max(0, job.attempts - 1))))
                else:
                    job.status = "failed"
                    job.finished_at = datetime.utcnow()
                    if job.job_type == "provision_server" and server:
                        server.status = "provision_failed"
                        server.reconcile_status = "error"
                        server.reconcile_message = message
                        _refund_failed_provision(db, server)
                        user = db.get(User, server.user_id)
                        if user:
                            queue_notification(
                                db,
                                user,
                                title="VPS 开通失败",
                                body=f"{server.name} 开通失败，已自动退回本次购买余额。错误：{message[:200]}",
                                kind="server",
                                severity="error",
                                event_key=f"provision-failed:{server.id}:{job.id}",
                            )
                    elif server:
                        server.reconcile_status = "error"
                        server.reconcile_message = message
                    write_audit(db, actor_username="system", action=f"job.{job.job_type}.failed", target_type="job", target_id=job.id, detail=message, success=False)
                db.commit()
            return True


def process_jobs(provider, provider_name: str, max_jobs: int = 5) -> int:
    count = 0
    for _ in range(max_jobs):
        if not run_one_job(provider, provider_name):
            break
        count += 1
    return count
