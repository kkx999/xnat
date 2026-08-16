import json
import os
import re
import secrets
import string
import subprocess
import shlex
import time
from .base import NetworkStats, Provider, ProviderState, ProvisionResult

class ProviderError(RuntimeError):
    pass

class IncusProvider(Provider):
    def __init__(self):
        self.storage_pool = os.getenv("INCUS_STORAGE_POOL", "natpool")
        self.public_host = os.getenv("INCUS_PUBLIC_HOST", "127.0.0.1")
        self.timeout = int(os.getenv("INCUS_PROVISION_TIMEOUT", "180"))

    @staticmethod
    def _clean_command_output(text: str) -> str:
        lines = []
        for line in (text or "").splitlines():
            stripped = line.strip()
            if stripped.startswith("debconf: delaying package configuration, since apt-utils is not installed"):
                continue
            lines.append(line)
        return "\n".join(lines).strip()

    def _command_error(self, args, proc) -> str:
        command = shlex.join(str(x) for x in args)
        stderr = self._clean_command_output(proc.stderr)
        stdout = self._clean_command_output(proc.stdout)
        parts = [f"命令失败 (exit={proc.returncode}): {command}"]
        if stderr:
            parts.append(f"stderr: {stderr[-900:]}")
        if stdout:
            parts.append(f"stdout: {stdout[-900:]}")
        if len(parts) == 1:
            parts.append("无命令输出")
        return "\n".join(parts)

    def _run(self, args, *, input_text=None, timeout=None, check=True):
        effective_timeout = timeout or self.timeout
        try:
            proc = subprocess.run(
                args, input=input_text, text=True, capture_output=True,
                timeout=effective_timeout
            )
        except subprocess.TimeoutExpired as exc:
            command = shlex.join(str(x) for x in args)
            stderr = self._clean_command_output(exc.stderr or "")
            stdout = self._clean_command_output(exc.stdout or "")
            detail = stderr or stdout
            suffix = f"；最后输出: {detail[-600:]}" if detail else ""
            raise ProviderError(f"命令超时 ({effective_timeout}s): {command}{suffix}") from exc
        if check and proc.returncode != 0:
            raise ProviderError(self._command_error(args, proc)[:2200])
        return proc

    def _instance_exists(self, name: str) -> bool:
        return self._run(["incus", "info", name], check=False, timeout=20).returncode == 0

    def _delete_instance(self, name: str):
        if self._instance_exists(name):
            self._run(["incus", "delete", name, "--force"], check=False, timeout=60)

    def _instance_virtualization_type(self, name: str) -> str:
        proc = self._run(["incus", "query", f"/1.0/instances/{name}"], check=False, timeout=20)
        if proc.returncode != 0:
            return "lxc"
        try:
            payload = json.loads(proc.stdout or "{}")
            meta = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else payload
            return "kvm" if str(meta.get("type") or "").lower() == "virtual-machine" else "lxc"
        except Exception:
            return "lxc"

    def _random_password(self, length: int = 20) -> str:
        alphabet = string.ascii_letters + string.digits + "!@#_-"
        return "".join(secrets.choice(alphabet) for _ in range(length))

    def _wait_guest_agent(self, name: str, virtualization_type: str = "lxc") -> None:
        mode = (virtualization_type or "lxc").strip().lower()
        if mode != "kvm":
            return
        wait_seconds = 180
        deadline = time.time() + wait_seconds
        last_error = ""
        while time.time() < deadline:
            try:
                proc = self._run(["incus", "exec", name, "--", "true"], check=False, timeout=12)
                if proc.returncode == 0:
                    return
                last_error = self._clean_command_output(proc.stderr) or self._clean_command_output(proc.stdout)
            except ProviderError as exc:
                last_error = str(exc)
            time.sleep(3)
        detail = f"；最后错误: {last_error[-500:]}" if last_error else ""
        raise ProviderError(f"KVM Guest Agent 未能在 {wait_seconds} 秒内就绪{detail}")

    def _wait_ipv4(self, name: str, virtualization_type: str = "lxc") -> str:
        mode = (virtualization_type or "lxc").strip().lower()
        self._wait_guest_agent(name, mode)
        wait_seconds = 120 if mode == "kvm" else 90
        deadline = time.time() + wait_seconds
        last_error = ""
        while time.time() < deadline:
            try:
                proc = self._run(
                    ["incus", "exec", name, "--", "sh", "-lc",
                     "ip -4 -o addr show scope global | awk '$2 != \"lo\" {print $4; exit}' | cut -d/ -f1"],
                    check=False, timeout=15
                )
                ip = (proc.stdout or "").strip()
                if ip:
                    return ip
                last_error = self._clean_command_output(proc.stderr) or self._clean_command_output(proc.stdout)
            except ProviderError as exc:
                last_error = str(exc)
            time.sleep(2 if mode == "kvm" else 1)
        detail = f"；最后错误: {last_error[-500:]}" if last_error else ""
        raise ProviderError(f"{mode.upper()} 实例未能在 {wait_seconds} 秒内获取 IPv4{detail}")


    def _prepare_ssh(self, name: str, password: str):
        # Keep local-provider behavior identical to Host Agent provisioning.
        try:
            self._run(
                ["incus", "exec", name, "--", "chpasswd"],
                input_text=f"root:{password}\n", timeout=30
            )
        except Exception as exc:
            raise ProviderError(f"KVM/LXC SSH 初始化失败 [设置 root 密码]: {exc}") from exc
        script = r"""
set -Eeuo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends openssh-server ca-certificates
mkdir -p /run/sshd /etc/ssh/sshd_config.d
cat > /etc/ssh/sshd_config.d/00-00-xnat.conf <<'EOF_XNAT_SSH'
PermitRootLogin yes
PasswordAuthentication yes
EOF_XNAT_SSH
rm -f /etc/ssh/sshd_config.d/00-natvps.conf
TMP_CFG="$(mktemp)"
grep -vFx 'Include /etc/ssh/sshd_config.d/00-00-xnat.conf' /etc/ssh/sshd_config > "$TMP_CFG" || true
{
  printf '%s\n' 'Include /etc/ssh/sshd_config.d/00-00-xnat.conf'
  cat "$TMP_CFG"
} > /etc/ssh/sshd_config
rm -f "$TMP_CFG"
sshd -t
sshd -T | grep -x 'permitrootlogin yes' >/dev/null
sshd -T | grep -x 'passwordauthentication yes' >/dev/null
passwd -S root | awk '$2 == "P" {ok=1} END {exit ok ? 0 : 1}'
systemctl enable --now ssh
systemctl restart ssh
ss -lnt '( sport = :22 )' | grep 'LISTEN' >/dev/null
"""
        self._run(["incus", "exec", name, "--", "bash", "-lc", script], timeout=300)

    def _launch(
        self, name: str, image_alias: str,
        memory_mb: int, disk_gb: int, cpu: int, bandwidth_mbps: int,
        virtualization_type: str = "lxc",
    ):
        mode = (virtualization_type or "lxc").strip().lower()
        if mode not in {"lxc", "kvm"}:
            raise ProviderError(f"不支持的虚拟化类型: {mode}")
        args = [
            "incus", "launch", image_alias, name,
            "--storage", self.storage_pool,
            "--config", f"limits.cpu={cpu}",
            "--config", f"limits.memory={memory_mb}MiB",
            "--device", f"root,size={disk_gb}GiB",
        ]
        if mode == "kvm":
            if not (os.path.exists("/dev/kvm") and os.access("/dev/kvm", os.R_OK | os.W_OK)):
                raise ProviderError("/dev/kvm 不可用；请检查 Nested Virtualization")
            if memory_mb < 512:
                raise ProviderError("KVM 实例内存至少需要 512 MiB")
            if disk_gb < 4:
                raise ProviderError("KVM 实例系统盘至少需要 4 GiB")
            args.append("--vm")
        self._run(args, timeout=240 if mode == "kvm" else 180)
        self.set_bandwidth(name, bandwidth_mbps)

    def _add_proxy_device(self, name: str, device: str, protocol: str, public_port: int, private_port: int):
        args = [
            "incus", "config", "device", "add", name, device, "proxy",
            f"listen={protocol}:0.0.0.0:{public_port}",
        ]
        if self._instance_virtualization_type(name) == "kvm":
            args.extend([f"connect={protocol}:0.0.0.0:{private_port}", "nat=true"])
        else:
            args.append(f"connect={protocol}:127.0.0.1:{private_port}")
        self._run(args, timeout=30)

    def _add_ssh_proxy(self, name: str, ssh_port: int):
        self._add_proxy_device(name, f"ssh-{ssh_port}", "tcp", ssh_port, 22)

    def provision(
        self, server_id: int, instance_name: str, image_alias: str,
        memory_mb: int, disk_gb: int, cpu: int,
        bandwidth_mbps: int, ssh_port: int, virtualization_type: str = "lxc"
    ) -> ProvisionResult:
        password = self._random_password()
        if self._instance_exists(instance_name):
            raise ProviderError(f"实例 {instance_name} 已存在")
        try:
            self._launch(instance_name, image_alias, memory_mb, disk_gb, cpu, bandwidth_mbps, virtualization_type)
            private_ip = self._wait_ipv4(instance_name, virtualization_type)
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
        mode = self._instance_virtualization_type(instance_id)
        self._wait_guest_agent(instance_id, mode)
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
        bandwidth_mbps: int, ssh_port: int, virtualization_type: str = "lxc"
    ) -> ProvisionResult:
        self._delete_instance(instance_id)
        password = self._random_password()
        try:
            self._launch(instance_id, image_alias, memory_mb, disk_gb, cpu, bandwidth_mbps, virtualization_type)
            private_ip = self._wait_ipv4(instance_id, virtualization_type)
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
        self._add_proxy_device(instance_id, device, protocol, public_port, private_port)
        return device

    def remove_port(self, instance_id: str, device_name: str) -> None:
        self._run(["incus", "config", "device", "remove", instance_id, device_name], timeout=30)

    def network_stats(self, instance_id: str) -> NetworkStats:
        proc = self._run([
            "incus", "exec", instance_id, "--", "sh", "-lc",
            "rx=0; tx=0; "
            "for d in /sys/class/net/*; do "
            "[ \"$(basename \"$d\")\" = lo ] && continue; "
            "r=$(cat \"$d/statistics/rx_bytes\" 2>/dev/null || echo 0); "
            "t=$(cat \"$d/statistics/tx_bytes\" 2>/dev/null || echo 0); "
            "rx=$((rx+r)); tx=$((tx+t)); "
            "done; printf '%s %s\\n' \"$rx\" \"$tx\""
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
        mode = self._instance_virtualization_type(instance_id)
        if mode == "kvm" and (memory_mb < 512 or disk_gb < 4):
            raise ProviderError("KVM 实例至少需要 512 MiB 内存和 4 GiB 系统盘")
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
            config = meta.get("expanded_config") or meta.get("config") or {}
            raw_cpu = str(config.get("limits.cpu") or "").strip()
            memory_bytes = self._size_to_bytes(config.get("limits.memory"))
            disk_bytes = self._size_to_bytes((devices.get("root") or {}).get("size"))
            virtualization_type = "kvm" if str(meta.get("type") or "").lower() == "virtual-machine" else "lxc"
            return ProviderState(
                True, status, bw,
                int(raw_cpu) if raw_cpu.isdigit() else None,
                int(round(memory_bytes / (1024 ** 2))) if memory_bytes else None,
                int(round(disk_bytes / (1024 ** 3))) if disk_bytes else None,
                virtualization_type,
            )
        except Exception:
            return ProviderState(True, "unknown", None)

    def port_device_exists(self, instance_id: str, device_name: str) -> bool:
        proc = self._run(["incus", "config", "device", "get", instance_id, device_name, "type"], check=False, timeout=15)
        return proc.returncode == 0 and (proc.stdout or "").strip() == "proxy"
