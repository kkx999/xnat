from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass
class ProvisionResult:
    instance_id: str
    private_ip: str
    ssh_port: int
    status: str
    root_password: str | None = None

@dataclass
class NetworkStats:
    rx_bytes: int = 0
    tx_bytes: int = 0
    available: bool = False

@dataclass
class ProviderState:
    exists: bool = False
    status: str = "missing"
    bandwidth_mbps: int | None = None

class Provider(ABC):
    @abstractmethod
    def provision(
        self, server_id: int, instance_name: str, image_alias: str,
        memory_mb: int, disk_gb: int, cpu: int,
        bandwidth_mbps: int, ssh_port: int
    ) -> ProvisionResult:
        raise NotImplementedError

    @abstractmethod
    def power_action(self, instance_id: str, action: str) -> str:
        raise NotImplementedError

    @abstractmethod
    def reset_password(self, instance_id: str) -> str:
        raise NotImplementedError

    @abstractmethod
    def reinstall(
        self, instance_id: str, image_alias: str,
        memory_mb: int, disk_gb: int, cpu: int,
        bandwidth_mbps: int, ssh_port: int
    ) -> ProvisionResult:
        raise NotImplementedError

    @abstractmethod
    def delete(self, instance_id: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def add_port(self, instance_id: str, public_port: int, private_port: int, protocol: str) -> str:
        raise NotImplementedError

    @abstractmethod
    def remove_port(self, instance_id: str, device_name: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def network_stats(self, instance_id: str) -> NetworkStats:
        raise NotImplementedError

    @abstractmethod
    def resize_resources(self, instance_id: str, cpu: int, memory_mb: int, disk_gb: int) -> dict:
        raise NotImplementedError

    @abstractmethod
    def set_bandwidth(self, instance_id: str, bandwidth_mbps: int) -> None:
        raise NotImplementedError

    @abstractmethod
    def inspect(self, instance_id: str) -> ProviderState:
        raise NotImplementedError

    @abstractmethod
    def port_device_exists(self, instance_id: str, device_name: str) -> bool:
        raise NotImplementedError
