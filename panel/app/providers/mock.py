import os
import secrets
from .base import NetworkStats, Provider, ProviderState, ProvisionResult

class MockProvider(Provider):
    @property
    def public_host(self) -> str:
        return os.getenv("INCUS_PUBLIC_HOST", "203.0.113.10")

    def provision(
        self, server_id: int, instance_name: str, image_alias: str,
        memory_mb: int, disk_gb: int, cpu: int,
        bandwidth_mbps: int, ssh_port: int, virtualization_type: str = "lxc"
    ) -> ProvisionResult:
        host_octet = 100 + (server_id % 140)
        return ProvisionResult(
            instance_id=f"mock-{server_id}",
            private_ip=f"10.20.0.{host_octet}",
            ssh_port=ssh_port,
            status="running",
            root_password=secrets.token_urlsafe(15),
        )

    def power_action(self, instance_id: str, action: str) -> str:
        return "stopped" if action == "stop" else "running"

    def reset_password(self, instance_id: str) -> str:
        return secrets.token_urlsafe(15)

    def reinstall(
        self, instance_id: str, image_alias: str,
        memory_mb: int, disk_gb: int, cpu: int,
        bandwidth_mbps: int, ssh_port: int, virtualization_type: str = "lxc"
    ) -> ProvisionResult:
        return ProvisionResult(
            instance_id=instance_id,
            private_ip="10.20.0.200",
            ssh_port=ssh_port,
            status="running",
            root_password=secrets.token_urlsafe(15),
        )

    def delete(self, instance_id: str) -> None:
        return None

    def add_port(self, instance_id: str, public_port: int, private_port: int, protocol: str) -> str:
        return f"port-{protocol}-{public_port}"

    def remove_port(self, instance_id: str, device_name: str) -> None:
        return None

    def network_stats(self, instance_id: str) -> NetworkStats:
        return NetworkStats(0, 0, True)

    def resize_resources(self, instance_id: str, cpu: int, memory_mb: int, disk_gb: int) -> dict:
        return {"cpu": cpu, "memory_mb": memory_mb, "disk_gb": disk_gb}

    def set_bandwidth(self, instance_id: str, bandwidth_mbps: int) -> None:
        return None

    def inspect(self, instance_id: str) -> ProviderState:
        return ProviderState(True, "running", None)

    def port_device_exists(self, instance_id: str, device_name: str) -> bool:
        return True
