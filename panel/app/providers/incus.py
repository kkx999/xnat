import json
import os
import re
import secrets
import string
import subprocess
import time
from .base import NetworkStats, Provider, ProviderState, ProvisionResult

class ProviderError(RuntimeError):
    pass

class IncusProvider(Provider):
    def __init__(self):
        self.storage_pool = os.getenv("INCUS_STORAGE_POOL", "natpool")
        self.public_host = os.getenv("INCUS_PUBLIC_HOST", "127.0.0.1")
        self.timeout = int(os.getenv("INCUS_PROVISION_TIMEOUT", "180"))

    def _run(self, args, *, input_text=None, timeout=None, check=True):
        proc = subprocess.run(
            args, input=input_text, text=True, capture_output=True,
            timeout=timeout or self.timeout
        )
        if check and proc.returncode != 0:
            msg = (proc.stderr or proc.stdout or "command failed").strip()
            raise ProviderError(f"{' '.join(args[:4])}: {msg}")
        return proc

    def _instance_exists(self, name: str) -> bool:
        return self._run(["incus", "info", name], check=False, timeout=20).returncode == 0

    def _delete_instance(self, name: str):
        if self._instance_exists(name):
            self._run(["incus", "delete", name, "--force"], check=False, timeout=60)

    def _random_password(self, length: int = 20) -> str:
        alphabet = string.ascii_letters + string.digits + "!@#_-"
        return "".join(secrets.choice(alphabet) for _ in range(length))

    def _wait_ipv4(self, name: str) -> str:
        deadline = time.time() + 45
        while time.time() < deadline:
            proc = self._run(
                ["incus", "exec", name, "--", "sh", "-lc",
                 "ip -4 -o addr show dev eth0 scope global | awk '{print $4}' | cut -d/ -f1 | head -n1"],
                check=False, timeout=15
            )
            ip = (proc.stdout or "").strip()
            if ip:
                return ip
            time.sleep(1)
        raise ProviderError("容器未能在 45 秒内获取 IPv4")

    def _prepare_ssh(self, name: str, password: str):
        # Image catalog is apt-family only (Debian/Ubuntu).
        script = (
            "export DEBIAN_FRONTEND=noninteractive; "
            "apt-get update && "
            "apt-get install -y --no-install-recommends openssh-server ca-certificates && "
            "mkdir -p /run/sshd /etc/ssh/sshd_config.d && "
            "printf '%s\\n' 'PermitRootLogin yes' 'PasswordAuthentication yes' "
            "> /etc/ssh/sshd_config.d/00-natvps.conf && "
            "sshd -t && systemctl enable ssh && systemctl restart ssh"
        )
        self._run(["incus", "exec", name, "--", "bash", "-lc", script], timeout=150)
        self._run(
            ["incus", "exec", name, "--", "chpasswd"],
            input_text=f"root:{password}\n", timeout=30
        )
        self._run(["incus", "exec", name, "--", "systemctl", "restart", "ssh"], timeout=30)

    def _launch(
        self, name: str, image_alias: str,
        memory_mb: int, disk_gb: int, cpu: int, bandwidth_mbps: int
    ):
        self._run([
            "incus", "launch", image_alias, name,
            "--storage", self.storage_pool,
            "--config", f"limits.cpu={cpu}",
            "--config", f"limits.memory={memory_mb}MiB",
            "--device", f"root,size={disk_gb}GiB",
        ], timeout=180)
        self.set_bandwidth(name, bandwidth_mbps)

    def _add_ssh_proxy(self, name: str, ssh_port: int):
        self._run([
            "incus", "config", "device", "add", name,
            f"ssh-{ssh_port}", "proxy",
            f"listen=tcp:0.0.0.0:{ssh_port}",
            "connect=tcp:127.0.0.1:22"
        ], timeout=30)

    def provision(
        self, server_id: int, instance_name: str, image_alias: str,
        memory_mb: int, disk_gb: int, cpu: int,
        bandwidth_mbps: int, ssh_port: int
    ) -> ProvisionResult:
        password = self._random_password()
        if self._instance_exists(instance_name):
            raise ProviderError(f"实例 {instance_name} 已存在")
        try:
            self._launch(instance_name, image_alias, memory_mb, disk_gb, cpu, bandwidth_mbps)
            private_ip = self._wait_ipv4(instance_name)
            self._prepare_ssh(instance_name, password)
            self._add_ssh_proxy(instance_name, ssh_port)
            return ProvisionResult(instance_name, private_ip, ssh_port, "running", password)
        except Exception:
            self._delete_instance(instance_name)
            raise

    def power_action(self, instance_id: str, action: str) -> str:
        if action == "start":
            self._run(["incus", "start", instance_id], timeout=60)
            return "running"
        if action == "stop":
            self._run(["incus", "stop", instance_id, "--timeout", "20", "--force"], timeout=40)
            return "stopped"
        if action == "reboot":
            self._run(["incus", "restart", instance_id, "--timeout", "20", "--force"], timeout=50)
            return "running"
        raise ProviderError("不支持的电源操作")

    def reset_password(self, instance_id: str) -> str:
        password = self._random_password()
        self._run(
            ["incus", "exec", instance_id, "--", "chpasswd"],
            input_text=f"root:{password}\n", timeout=30
        )
        self._run(["incus", "exec", instance_id, "--", "systemctl", "restart", "ssh"], timeout=30)
        return password

    def reinstall(
        self, instance_id: str, image_alias: str,
        memory_mb: int, disk_gb: int, cpu: int,
        bandwidth_mbps: int, ssh_port: int
    ) -> ProvisionResult:
        self._delete_instance(instance_id)
        password = self._random_password()
        try:
            self._launch(instance_id, image_alias, memory_mb, disk_gb, cpu, bandwidth_mbps)
            private_ip = self._wait_ipv4(instance_id)
            self._prepare_ssh(instance_id, password)
            self._add_ssh_proxy(instance_id, ssh_port)
            return ProvisionResult(instance_id, private_ip, ssh_port, "running", password)
        except Exception:
            self._delete_instance(instance_id)
            raise

    def delete(self, instance_id: str) -> None:
        self._delete_instance(instance_id)

    def add_port(self, instance_id: str, public_port: int, private_port: int, protocol: str) -> str:
        protocol = protocol.lower()
        if protocol not in {"tcp", "udp"}:
            raise ProviderError("仅支持 TCP 或 UDP")
        if not (1 <= private_port <= 65535 and 1 <= public_port <= 65535):
            raise ProviderError("端口范围无效")
        device = f"nat-{protocol}-{public_port}"
        self._run([
            "incus", "config", "device", "add", instance_id,
            device, "proxy",
            f"listen={protocol}:0.0.0.0:{public_port}",
            f"connect={protocol}:127.0.0.1:{private_port}"
        ], timeout=30)
        return device

    def remove_port(self, instance_id: str, device_name: str) -> None:
        self._run(["incus", "config", "device", "remove", instance_id, device_name], timeout=30)

    def network_stats(self, instance_id: str) -> NetworkStats:
        proc = self._run([
            "incus", "exec", instance_id, "--", "sh", "-lc",
            "printf '%s %s' "
            "\"$(cat /sys/class/net/eth0/statistics/rx_bytes 2>/dev/null || echo 0)\" "
            "\"$(cat /sys/class/net/eth0/statistics/tx_bytes 2>/dev/null || echo 0)\""
        ], check=False, timeout=12)
        if proc.returncode != 0:
            return NetworkStats()
        try:
            rx, tx = (proc.stdout or "").strip().split()
            return NetworkStats(int(rx), int(tx), True)
        except Exception:
            return NetworkStats()

    def _set_eth0_value(self, instance_id: str, key: str, value: str) -> None:
        # If eth0 is already a local instance device, "set" is correct.
        proc = self._run(
            ["incus", "config", "device", "set", instance_id, "eth0", f"{key}={value}"],
            check=False, timeout=30
        )
        if proc.returncode == 0:
            return

        # Otherwise eth0 is inherited from a profile. Copy it locally and set the value.
        proc = self._run(
            ["incus", "config", "device", "override", instance_id, "eth0", f"{key}={value}"],
            check=False, timeout=30
        )
        if proc.returncode != 0:
            msg = (proc.stderr or proc.stdout or "command failed").strip()
            raise ProviderError(f"调整 eth0 失败: {msg}")


    def _instance_resource_snapshot(self, instance_id: str) -> dict:
        proc = self._run(["incus", "query", f"/1.0/instances/{instance_id}?recursion=1"], check=False, timeout=20)
        if proc.returncode != 0:
            raise ProviderError("无法读取实例配置")
        try:
            payload = json.loads(proc.stdout or "{}")
            meta = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else payload
            config = meta.get("expanded_config") or meta.get("config") or {}
            devices = meta.get("expanded_devices") or meta.get("devices") or {}
            raw_cpu = str(config.get("limits.cpu") or "").strip()
            mem = str(config.get("limits.memory") or "").strip()
            disk = str((devices.get("root") or {}).get("size") or "").strip()

            def to_unit(text, suffixes):
                m = re.match(r"^([0-9.]+)\\s*([A-Za-z]+)?$", text)
                if not m:
                    return 0.0
                num = float(m.group(1)); unit = (m.group(2) or "").lower()
                return num * suffixes.get(unit, 0.0)

            mem_mb = to_unit(mem, {"mib":1, "mb":1000**2/1024**2, "gib":1024, "gb":1000**3/1024**2})
            disk_gb = to_unit(disk, {"gib":1, "gb":1000**3/1024**3, "tib":1024, "tb":1000**4/1024**3})
            return {"cpu": int(raw_cpu) if raw_cpu.isdigit() else 0, "memory_mb": int(round(mem_mb)), "disk_gb": int(round(disk_gb))}
        except Exception as exc:
            raise ProviderError(f"解析实例资源失败: {exc}")

    def resize_resources(self, instance_id: str, cpu: int, memory_mb: int, disk_gb: int) -> dict:
        before = self._instance_resource_snapshot(instance_id)
        if before["disk_gb"] > 0 and disk_gb < before["disk_gb"]:
            raise ProviderError(f"根磁盘禁止缩容：当前 {before['disk_gb']} GiB")

        self._run(["incus", "config", "set", instance_id, f"limits.cpu={cpu}"], timeout=35)
        self._run(["incus", "config", "set", instance_id, f"limits.memory={memory_mb}MiB"], timeout=35)

        if before["disk_gb"] == 0 or disk_gb > before["disk_gb"]:
            proc = self._run(["incus", "config", "device", "set", instance_id, "root", f"size={disk_gb}GiB"], check=False, timeout=60)
            if proc.returncode != 0:
                proc = self._run(["incus", "config", "device", "override", instance_id, "root", f"size={disk_gb}GiB"], check=False, timeout=60)
                if proc.returncode != 0:
                    raise ProviderError((proc.stderr or proc.stdout or "根磁盘扩容失败").strip())

        after = self._instance_resource_snapshot(instance_id)
        return {"cpu": after["cpu"] or cpu, "memory_mb": after["memory_mb"] or memory_mb, "disk_gb": after["disk_gb"] or disk_gb}

    def set_bandwidth(self, instance_id: str, bandwidth_mbps: int) -> None:
        if bandwidth_mbps < 0:
            raise ProviderError("带宽不能小于 0")

        if bandwidth_mbps == 0:
            # Unset succeeds when eth0 is already a local instance device.
            proc = self._run(
                ["incus", "config", "device", "unset", instance_id, "eth0", "limits.max"],
                check=False, timeout=30
            )
            if proc.returncode == 0:
                return

            # If eth0 is still inherited, there is no local rate limit to remove.
            # Create a local override without a limits.max value so later changes can use "set".
            proc = self._run(
                ["incus", "config", "device", "override", instance_id, "eth0"],
                check=False, timeout=30
            )
            if proc.returncode != 0 and "already exists" not in ((proc.stderr or "") + (proc.stdout or "")).lower():
                msg = (proc.stderr or proc.stdout or "command failed").strip()
                raise ProviderError(f"取消 eth0 限速失败: {msg}")
            return

        self._set_eth0_value(instance_id, "limits.max", f"{bandwidth_mbps}Mbit")

    def inspect(self, instance_id: str) -> ProviderState:
        proc = self._run(["incus", "query", f"/1.0/instances/{instance_id}?recursion=1"], check=False, timeout=20)
        if proc.returncode != 0:
            return ProviderState(False, "missing", None)
        try:
            payload = json.loads(proc.stdout or "{}")
            meta = payload.get("metadata") or {}
            raw_status = str(meta.get("status") or "").lower()
            status = "running" if raw_status == "running" else "stopped" if raw_status in {"stopped", "frozen"} else raw_status or "unknown"
            devices = meta.get("expanded_devices") or meta.get("devices") or {}
            eth0 = devices.get("eth0") or {}
            raw_bw = str(eth0.get("limits.max") or "").strip()
            bw = 0 if not raw_bw else None
            if raw_bw:
                m = re.match(r"^([0-9.]+)\s*(Mbit|Mbps|M)?$", raw_bw, re.I)
                if m:
                    bw = int(float(m.group(1)))
            return ProviderState(True, status, bw)
        except Exception:
            return ProviderState(True, "unknown", None)

    def port_device_exists(self, instance_id: str, device_name: str) -> bool:
        proc = self._run(["incus", "config", "device", "get", instance_id, device_name, "type"], check=False, timeout=15)
        return proc.returncode == 0 and (proc.stdout or "").strip() == "proxy"
