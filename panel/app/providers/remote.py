from __future__ import annotations

from sqlalchemy import select

from ..db import SessionLocal
from ..models import HostNode, Server
from ..nodes import HostAPIError, host_request
from .base import NetworkStats, Provider, ProviderState, ProvisionResult


class RemoteHostProvider(Provider):
    """Provider implementation that delegates Incus work to a Host Agent."""

    public_host = "multi-node"

    def _host_for_server_id(self, server_id: int) -> HostNode:
        with SessionLocal() as db:
            server = db.get(Server, server_id)
            if not server or not server.host_id:
                raise HostAPIError("服务器尚未绑定宿主机")
            host = db.get(HostNode, server.host_id)
            if not host or not host.enabled:
                raise HostAPIError("服务器所属宿主机不存在或已停用")
            db.expunge(host)
            return host

    def _host_for_instance(self, instance_id: str) -> HostNode:
        with SessionLocal() as db:
            server = db.scalar(select(Server).where(Server.provider_instance_id == instance_id, Server.deleted_at.is_(None)))
            if not server or not server.host_id:
                raise HostAPIError(f"找不到实例 {instance_id} 对应的宿主机")
            host = db.get(HostNode, server.host_id)
            if not host:
                raise HostAPIError("宿主机记录不存在")
            db.expunge(host)
            return host

    def provision(self, server_id, instance_name, image_alias, memory_mb, disk_gb, cpu, bandwidth_mbps, ssh_port):
        host = self._host_for_server_id(server_id)
        data = host_request(host, "POST", "/v1/provision", payload={
            "server_id": server_id,
            "instance_name": instance_name,
            "image_alias": image_alias,
            "memory_mb": memory_mb,
            "disk_gb": disk_gb,
            "cpu": cpu,
            "bandwidth_mbps": bandwidth_mbps,
            "ssh_port": ssh_port,
        }, timeout=210)
        return ProvisionResult(
            str(data["instance_id"]), str(data.get("private_ip") or ""), int(data["ssh_port"]),
            str(data.get("status") or "running"), data.get("root_password")
        )

    def power_action(self, instance_id: str, action: str) -> str:
        host = self._host_for_instance(instance_id)
        data = host_request(host, "POST", f"/v1/instances/{instance_id}/power", payload={"action": action}, timeout=70)
        return str(data.get("status") or "unknown")

    def reset_password(self, instance_id: str) -> str:
        host = self._host_for_instance(instance_id)
        data = host_request(host, "POST", f"/v1/instances/{instance_id}/reset-password", payload={}, timeout=45)
        return str(data["root_password"])

    def reinstall(self, instance_id: str, image_alias: str, memory_mb: int, disk_gb: int, cpu: int, bandwidth_mbps: int, ssh_port: int) -> ProvisionResult:
        host = self._host_for_instance(instance_id)
        data = host_request(host, "POST", f"/v1/instances/{instance_id}/reinstall", payload={
            "image_alias": image_alias,
            "memory_mb": memory_mb,
            "disk_gb": disk_gb,
            "cpu": cpu,
            "bandwidth_mbps": bandwidth_mbps,
            "ssh_port": ssh_port,
        }, timeout=210)
        return ProvisionResult(str(data["instance_id"]), str(data.get("private_ip") or ""), int(data["ssh_port"]), str(data.get("status") or "running"), data.get("root_password"))

    def delete(self, instance_id: str) -> None:
        host = self._host_for_instance(instance_id)
        host_request(host, "DELETE", f"/v1/instances/{instance_id}", timeout=75)

    def add_port(self, instance_id: str, public_port: int, private_port: int, protocol: str) -> str:
        host = self._host_for_instance(instance_id)
        data = host_request(host, "POST", f"/v1/instances/{instance_id}/ports", payload={
            "public_port": public_port, "private_port": private_port, "protocol": protocol,
        }, timeout=40)
        return str(data["device_name"])

    def remove_port(self, instance_id: str, device_name: str) -> None:
        host = self._host_for_instance(instance_id)
        host_request(host, "DELETE", f"/v1/instances/{instance_id}/ports/{device_name}", timeout=40)

    def network_stats(self, instance_id: str) -> NetworkStats:
        host = self._host_for_instance(instance_id)
        try:
            data = host_request(host, "GET", f"/v1/instances/{instance_id}/stats", timeout=18)
            return NetworkStats(int(data.get("rx_bytes") or 0), int(data.get("tx_bytes") or 0), bool(data.get("available", True)))
        except Exception:
            return NetworkStats()

    def resize_resources(self, instance_id: str, cpu: int, memory_mb: int, disk_gb: int) -> dict:
        host = self._host_for_instance(instance_id)
        return host_request(
            host,
            "POST",
            f"/v1/instances/{instance_id}/resources",
            payload={"cpu": cpu, "memory_mb": memory_mb, "disk_gb": disk_gb},
            timeout=90,
        )

    def set_bandwidth(self, instance_id: str, bandwidth_mbps: int) -> None:
        host = self._host_for_instance(instance_id)
        host_request(host, "POST", f"/v1/instances/{instance_id}/bandwidth", payload={"bandwidth_mbps": bandwidth_mbps}, timeout=40)

    def inspect(self, instance_id: str) -> ProviderState:
        host = self._host_for_instance(instance_id)
        data = host_request(host, "GET", f"/v1/instances/{instance_id}/inspect", timeout=20)
        return ProviderState(bool(data.get("exists")), str(data.get("status") or "unknown"), data.get("bandwidth_mbps"))

    def port_device_exists(self, instance_id: str, device_name: str) -> bool:
        host = self._host_for_instance(instance_id)
        data = host_request(host, "GET", f"/v1/instances/{instance_id}/devices/{device_name}", timeout=18)
        return bool(data.get("exists"))
