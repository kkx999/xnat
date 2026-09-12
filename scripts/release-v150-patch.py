from __future__ import annotations

from pathlib import Path
import json
import re


def read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    Path(path).write_text(text, encoding="utf-8")


def replace_once(path: str, old: str, new: str) -> None:
    text = read(path)
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected 1 anchor, found {count}: {old[:100]!r}")
    write(path, text.replace(old, new, 1))


def replace_all(path: str, old: str, new: str, minimum: int = 1) -> None:
    text = read(path)
    count = text.count(old)
    if count < minimum:
        raise RuntimeError(f"{path}: expected >= {minimum} anchors, found {count}: {old[:100]!r}")
    write(path, text.replace(old, new))


def regex_once(path: str, pattern: str, replacement: str) -> None:
    text = read(path)
    new, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
    if count != 1:
        raise RuntimeError(f"{path}: regex anchor matched {count}: {pattern[:120]}")
    write(path, new)


# ---------------------------------------------------------------------------
# Release versions
# ---------------------------------------------------------------------------
write("VERSION", "1.5.0\n")
write("panel/VERSION", "1.5.0\n")
write("agent/VERSION", "1.2.0\n")
write("panel/app/__init__.py", '__version__ = "1.5.0"\n')
write("agent/natvps_agent/__init__.py", '__version__ = "1.2.0"\n__api_version__ = "1"\n')
meta = json.loads(read("release.json"))
meta["release_version"] = "1.5.0"
meta["panel_version"] = "1.5.0"
meta["agent_version"] = "1.2.0"
release_payload = json.dumps(meta, ensure_ascii=False, indent=2) + "\n"
write("release.json", release_payload)
write("dist/release.json", release_payload)

# ---------------------------------------------------------------------------
# Persist sub-GiB LXC disk sizes as GiB floats (0.125 GiB = 128 MiB).
# SQLite accepts REAL values in existing INTEGER-affinity columns, so this is
# backwards compatible for upgraded installations while new schemas use REAL.
# ---------------------------------------------------------------------------
replace_once(
    "panel/app/models.py",
    "disk_gb: Mapped[int] = mapped_column(Integer)",
    "disk_gb: Mapped[float] = mapped_column(Float)",
)
replace_once(
    "panel/app/models.py",
    "disk_gb: Mapped[int | None] = mapped_column(Integer, nullable=True)",
    "disk_gb: Mapped[float | None] = mapped_column(Float, nullable=True)",
)

p = "panel/app/providers/base.py"
text = read(p).replace("disk_gb: int | None = None", "disk_gb: float | None = None")
text = text.replace("disk_gb: int", "disk_gb: float")
write(p, text)

# ---------------------------------------------------------------------------
# Host Agent v1.2.0: Alpine/OpenRC support + 128 MiB LXC disk floor.
# KVM retains its technical 512 MiB / 4 GiB guest floor to avoid creating
# configurations which boot unreliably. This is separate from Host floor.
# ---------------------------------------------------------------------------
p = "agent/natvps_agent/main.py"
text = read(p)
text = text.replace('AGENT_VERSION = "1.1.1"', 'AGENT_VERSION = "1.2.0"')
text = text.replace("disk_gb: int", "disk_gb: float")
text = text.replace("disk_gb: float = Field(ge=1, le=65536)", "disk_gb: float = Field(ge=0.125, le=65536)")
write(p, text)

replace_once(
    p,
    '''def random_password(length=20):
    chars = string.ascii_letters + string.digits + "!@#_-"
    return "".join(secrets.choice(chars) for _ in range(length))
''',
    r'''def random_password(length=20):
    chars = string.ascii_letters + string.digits + "!@#_-"
    return "".join(secrets.choice(chars) for _ in range(length))


def disk_size_value(disk_gb: float) -> str:
    """Convert the panel's GiB value to an exact Incus MiB size."""
    mib = int(round(float(disk_gb) * 1024))
    if mib < 128:
        raise RuntimeError("LXC 实例系统盘至少需要 128 MiB")
    return f"{mib}MiB"
''',
)

regex_once(
    p,
    r"def prepare_ssh\(name: str, password: str\):.*?\n\ndef set_eth0_value",
    r'''def guest_os_family(name: str) -> str:
    proc = run([
        "incus", "exec", name, "--", "sh", "-lc",
        "command -v apk >/dev/null 2>&1 && echo alpine || (command -v apt-get >/dev/null 2>&1 && echo apt || true)",
    ], check=False, timeout=20)
    family = (proc.stdout or "").strip().lower()
    return family if family in {"alpine", "apt"} else "unknown"


def restart_ssh_service(name: str):
    family = guest_os_family(name)
    command = (
        "rc-service sshd restart || rc-service sshd start"
        if family == "alpine"
        else "systemctl restart ssh || systemctl restart sshd"
    )
    proc = run(["incus", "exec", name, "--", "sh", "-lc", command], check=False, timeout=35)
    if proc.returncode != 0:
        raise RuntimeError(_command_error(["restart ssh"], proc)[:1200])


def prepare_ssh(name: str, password: str):
    try:
        run(["incus", "exec", name, "--", "chpasswd"], input_text=f"root:{password}\n", timeout=30)
    except Exception as exc:
        raise RuntimeError(f"KVM/LXC SSH 初始化失败 [设置 root 密码]: {exc}") from exc

    family = guest_os_family(name)
    if family == "alpine":
        script = r"""
set -eu
apk add --no-cache openssh ca-certificates
mkdir -p /run/sshd
ssh-keygen -A
sed -i '/^[#[:space:]]*PermitRootLogin[[:space:]]/d;/^[#[:space:]]*PasswordAuthentication[[:space:]]/d' /etc/ssh/sshd_config
printf '\nPermitRootLogin yes\nPasswordAuthentication yes\n' >> /etc/ssh/sshd_config
sshd -t
sshd -T | grep -x 'permitrootlogin yes' >/dev/null
sshd -T | grep -x 'passwordauthentication yes' >/dev/null
rc-update add sshd default >/dev/null 2>&1 || true
rc-service sshd restart >/dev/null 2>&1 || rc-service sshd start >/dev/null 2>&1
ss -lnt | grep ':22 ' >/dev/null
"""
        command = ["incus", "exec", name, "--", "sh", "-lc", script]
    elif family == "apt":
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
        command = ["incus", "exec", name, "--", "bash", "-lc", script]
    else:
        raise RuntimeError("当前系统镜像缺少受支持的包管理器；已支持 Debian / Ubuntu / Alpine")
    try:
        run(command, timeout=300)
    except Exception as exc:
        raise RuntimeError(f"KVM/LXC SSH 初始化失败 [安装/配置 sshd]: {exc}") from exc


def set_eth0_value''',
)

replace_once(p, '"--device", f"root,size={disk_gb}GiB",', '"--device", f"root,size={disk_size_value(disk_gb)}",')
replace_once(
    p,
    '"disk_gb": int(round(disk_bytes / (1024 ** 3))) if disk_bytes else 0,',
    '"disk_gb": round(disk_bytes / (1024 ** 3), 3) if disk_bytes else 0.0,',
)
replace_once(
    p,
    'def _set_root_disk_size(instance_id: str, disk_gb: float):\n    value = f"{disk_gb}GiB"',
    'def _set_root_disk_size(instance_id: str, disk_gb: float):\n    value = disk_size_value(disk_gb)',
)
replace_once(
    p,
    'run(["incus", "exec", instance_id, "--", "systemctl", "restart", "ssh"], timeout=30)',
    'restart_ssh_service(instance_id)',
)
replace_once(
    p,
    '"memory_used_mb": int((vm.total - vm.available) / 1024 / 1024),',
    '"memory_used_mb": int((vm.total - vm.available) / 1024 / 1024),\n        "memory_available_mb": int(vm.available / 1024 / 1024),',
)
replace_once(
    p,
    '"storage_used_gb": round(used_gb, 2),',
    '"storage_used_gb": round(used_gb, 2),\n        "storage_free_gb": round(max(0.0, total_gb - used_gb), 2),',
)

# ---------------------------------------------------------------------------
# Local Incus provider parity (legacy/single-host mode).
# ---------------------------------------------------------------------------
p = "panel/app/providers/incus.py"
text = read(p).replace("disk_gb: int", "disk_gb: float")
write(p, text)
replace_once(
    p,
    '''    def _random_password(self, length: int = 20) -> str:
        alphabet = string.ascii_letters + string.digits + "!@#_-"
        return "".join(secrets.choice(alphabet) for _ in range(length))
''',
    r'''    def _random_password(self, length: int = 20) -> str:
        alphabet = string.ascii_letters + string.digits + "!@#_-"
        return "".join(secrets.choice(alphabet) for _ in range(length))

    @staticmethod
    def _disk_size_value(disk_gb: float) -> str:
        mib = int(round(float(disk_gb) * 1024))
        if mib < 128:
            raise ProviderError("LXC 实例系统盘至少需要 128 MiB")
        return f"{mib}MiB"
''',
)
regex_once(
    p,
    r"    def _prepare_ssh\(self, name: str, password: str\):.*?\n    def _launch\(",
    r'''    def _guest_os_family(self, name: str) -> str:
        proc = self._run([
            "incus", "exec", name, "--", "sh", "-lc",
            "command -v apk >/dev/null 2>&1 && echo alpine || (command -v apt-get >/dev/null 2>&1 && echo apt || true)",
        ], check=False, timeout=20)
        family = (proc.stdout or "").strip().lower()
        return family if family in {"alpine", "apt"} else "unknown"

    def _restart_ssh_service(self, name: str):
        family = self._guest_os_family(name)
        command = (
            "rc-service sshd restart || rc-service sshd start"
            if family == "alpine"
            else "systemctl restart ssh || systemctl restart sshd"
        )
        proc = self._run(["incus", "exec", name, "--", "sh", "-lc", command], check=False, timeout=35)
        if proc.returncode != 0:
            raise ProviderError(self._command_error(["restart ssh"], proc)[:1200])

    def _prepare_ssh(self, name: str, password: str):
        try:
            self._run(["incus", "exec", name, "--", "chpasswd"], input_text=f"root:{password}\n", timeout=30)
        except Exception as exc:
            raise ProviderError(f"KVM/LXC SSH 初始化失败 [设置 root 密码]: {exc}") from exc
        family = self._guest_os_family(name)
        if family == "alpine":
            script = r"""
set -eu
apk add --no-cache openssh ca-certificates
mkdir -p /run/sshd
ssh-keygen -A
sed -i '/^[#[:space:]]*PermitRootLogin[[:space:]]/d;/^[#[:space:]]*PasswordAuthentication[[:space:]]/d' /etc/ssh/sshd_config
printf '\nPermitRootLogin yes\nPasswordAuthentication yes\n' >> /etc/ssh/sshd_config
sshd -t
sshd -T | grep -x 'permitrootlogin yes' >/dev/null
sshd -T | grep -x 'passwordauthentication yes' >/dev/null
rc-update add sshd default >/dev/null 2>&1 || true
rc-service sshd restart >/dev/null 2>&1 || rc-service sshd start >/dev/null 2>&1
ss -lnt | grep ':22 ' >/dev/null
"""
            command = ["incus", "exec", name, "--", "sh", "-lc", script]
        elif family == "apt":
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
            command = ["incus", "exec", name, "--", "bash", "-lc", script]
        else:
            raise ProviderError("当前系统镜像缺少受支持的包管理器；已支持 Debian / Ubuntu / Alpine")
        self._run(command, timeout=300)

    def _launch(''',
)
replace_once(p, '"--device", f"root,size={disk_gb}GiB",', '"--device", f"root,size={self._disk_size_value(disk_gb)}",')
replace_once(
    p,
    'self._run(["incus", "exec", instance_id, "--", "systemctl", "restart", "ssh"], timeout=30)',
    'self._restart_ssh_service(instance_id)',
)
replace_once(p, '"disk_gb": int(round(disk_gb))', '"disk_gb": round(disk_gb, 3)')
replace_all(p, 'f"size={disk_gb}GiB"', 'f"size={self._disk_size_value(disk_gb)}"')
replace_once(
    p,
    'int(round(disk_bytes / (1024 ** 3))) if disk_bytes else None',
    'round(disk_bytes / (1024 ** 3), 3) if disk_bytes else None',
)

# ---------------------------------------------------------------------------
# Host capacity display: available means the conservative minimum of logical
# allocation headroom and real resource headroom under scheduling watermark.
# ---------------------------------------------------------------------------
p = "panel/app/nodes.py"
replace_once(p, '"User-Agent": "XNAT-Panel/1.4.1",', '"User-Agent": "XNAT-Panel/1.5.0",')
replace_once(
    p,
    '''    memory_allocatable_mb = int((host.memory_total_mb or 0) * memory_limit_percent / 100)
    storage_allocatable_gb = float((host.storage_total_gb or 0) * storage_limit_percent / 100)
    capacity = {
''',
    '''    memory_allocatable_mb = int((host.memory_total_mb or 0) * memory_limit_percent / 100)
    storage_allocatable_gb = float((host.storage_total_gb or 0) * storage_limit_percent / 100)
    logical_memory_remaining = max(0, memory_allocatable_mb - allocated_memory)
    logical_storage_remaining = max(0.0, storage_allocatable_gb - allocated_disk)
    physical_memory_remaining = max(0, memory_allocatable_mb - int(host.memory_used_mb or 0))
    physical_storage_remaining = max(0.0, storage_allocatable_gb - float(host.storage_used_gb or 0))
    capacity = {
''',
)
replace_once(
    p,
    '''        "remaining_memory_mb": max(0, memory_allocatable_mb - allocated_memory),
        "memory_limit_percent": memory_limit_percent,
        "allocated_disk_gb": round(allocated_disk, 1),
        "remaining_disk_gb": round(max(0.0, storage_allocatable_gb - allocated_disk), 1),
''',
    '''        "remaining_memory_mb": min(logical_memory_remaining, physical_memory_remaining),
        "logical_remaining_memory_mb": logical_memory_remaining,
        "physical_remaining_memory_mb": physical_memory_remaining,
        "memory_limit_percent": memory_limit_percent,
        "allocated_disk_gb": round(allocated_disk, 3),
        "remaining_disk_gb": round(min(logical_storage_remaining, physical_storage_remaining), 3),
        "logical_remaining_disk_gb": round(logical_storage_remaining, 3),
        "physical_remaining_disk_gb": round(physical_storage_remaining, 3),
''',
)
replace_once(
    p,
    "projected_disk = (allocated_disk + int(plan.disk_gb or 0)) * 100 / host.storage_total_gb",
    "projected_disk = (allocated_disk + float(plan.disk_gb or 0)) * 100 / host.storage_total_gb",
)

# ---------------------------------------------------------------------------
# Panel web + mobile catalog.
# ---------------------------------------------------------------------------
p = "panel/app/main.py"
text = read(p)
text = text.replace('"version": "1.4.3"', '"version": "1.5.0"')
text = text.replace("disk_gb: int = Form(...)", "disk_gb: float = Form(...)")
text = text.replace("disk_gb < 1", "disk_gb < 0.125")
text = text.replace("current_disk = int(server.disk_gb or 0)", "current_disk = float(server.disk_gb or 0)")
text = text.replace('server.disk_gb = int(actual.get("disk_gb") or disk_gb)', 'server.disk_gb = float(actual.get("disk_gb") or disk_gb)')
text = text.replace('system_image.family != "apt"', 'system_image.family not in {"apt", "alpine"}')
text = text.replace(
    'row=SystemImage(name=name,alias=alias,family="apt",is_active=True,sort_order=100)',
    'row=SystemImage(name=name,alias=alias,family=("alpine" if alias.lower().startswith("images:alpine/") else "apt"),is_active=True,sort_order=100)',
)
write(p, text)
replace_once(
    p,
    '''                SystemImage(name="Ubuntu 24.04 LTS", alias="images:ubuntu/24.04", family="apt", sort_order=40),
            ])
            db.flush()
''',
    '''                SystemImage(name="Ubuntu 24.04 LTS", alias="images:ubuntu/24.04", family="apt", sort_order=40),
                SystemImage(name="Alpine 3.24", alias="images:alpine/3.24", family="alpine", sort_order=50),
            ])
            db.flush()

        # Preserve existing/custom images and add Alpine only when missing.
        if not db.scalar(select(SystemImage).where(SystemImage.alias == "images:alpine/3.24")):
            db.add(SystemImage(name="Alpine 3.24", alias="images:alpine/3.24", family="alpine", sort_order=50))
            db.flush()
''',
)

p = "panel/app/mobile_api.py"
text = read(p)
text = text.replace('SystemImage.family == "apt"', 'SystemImage.family.in_(["apt", "alpine"])')
text = text.replace('system_image.family != "apt"', 'system_image.family not in {"apt", "alpine"}')
text = text.replace('"disk_gb": int(plan.disk_gb or 0),', '"disk_gb": float(plan.disk_gb or 0),')
write(p, text)

# ---------------------------------------------------------------------------
# Admin UI: editable global floor for LXC, keep KVM's technical guest floor.
# ---------------------------------------------------------------------------
p = "panel/app/templates/admin.html"
text = read(p)
text = text.replace(
    'min="{{ 4 if (p.virtualization_type or \'lxc\') == \'kvm\' else 1 }}" value="{{ p.disk_gb }}" required data-virt-disk',
    'min="{{ 4 if (p.virtualization_type or \'lxc\') == \'kvm\' else 0.125 }}" step="0.125" value="{{ p.disk_gb }}" required data-virt-disk',
)
text = text.replace(
    'name="disk_gb" min="1" value="2" required data-virt-disk',
    'name="disk_gb" min="0.125" step="0.125" value="1" required data-virt-disk',
)
text = text.replace(
    'name="disk_gb" min="{{ s.disk_gb or 1 }}" max="65536" value="{{ s.disk_gb or 1 }}" required',
    'name="disk_gb" min="{{ s.disk_gb or 0.125 }}" step="0.125" max="65536" value="{{ s.disk_gb or 0.125 }}" required',
)
text = text.replace("<span>剩余内存</span>", "<span>可分配内存</span>")
text = text.replace(
    "<small>已分配 {{ cap.get('allocated_memory_mb', 0) }} MB</small>",
    "<small>逻辑已分配 {{ cap.get('allocated_memory_mb', 0) }} MB · 实际余量 {{ cap.get('physical_remaining_memory_mb', 0) }} MB</small>",
)
text = text.replace("<span>剩余存储</span>", "<span>可分配存储</span>")
text = text.replace(
    "<small>已分配 {{ '%.1f'|format(cap.get('allocated_disk_gb', 0)) }} GB</small>",
    "<small>逻辑已分配 {{ '%.3f'|format(cap.get('allocated_disk_gb', 0)) }} GB · natpool 实际余量 {{ '%.3f'|format(cap.get('physical_remaining_disk_gb', 0)) }} GB</small>",
)
write(p, text)

# ---------------------------------------------------------------------------
# Host installer: mode-aware low-resource floor and clearer menu.
# ---------------------------------------------------------------------------
p = "scripts/install-host.sh"
replace_once(
    p,
    '''      echo "  1. LXC"
      echo "  2. KVM"
      echo "  3. LXC + KVM（推荐，可同时销售两类套餐）"
''',
    '''      echo "  1. LXC"
      echo "     最低母鸡：1C / 1GB / 4.5GB 可用硬盘"
      echo "     适合 Alpine / Debian / Ubuntu 轻量 NAT VPS"
      echo "  2. KVM"
      echo "     最低母鸡：1C / 1GB / 6.5GB 可用硬盘，且 /dev/kvm 可用"
      echo "  3. LXC + KVM"
      echo "     最低母鸡：1C / 1GB / 6.5GB 可用硬盘，且 /dev/kvm 可用"
''',
)
regex_once(
    p,
    r"select_virtualization_mode\n\nTOTAL_GB=.*?\n\n\nif command -v incus",
    r'''select_virtualization_mode

CPU_CORES="$(nproc 2>/dev/null || echo 1)"
MEM_TOTAL_MIB="$(awk '/^MemTotal:/{print int($2/1024)}' /proc/meminfo)"
TOTAL_MIB="$(df -B1 --output=size / | tail -n1 | awk '{print int($1/1024/1024)}')"
FREE_MIB="$(df -B1 --output=avail / | tail -n1 | awk '{print int($1/1024/1024)}')"
[[ "${CPU_CORES}" =~ ^[0-9]+$ && "${MEM_TOTAL_MIB}" =~ ^[0-9]+$ && "${FREE_MIB}" =~ ^[0-9]+$ ]] || die "无法读取 Host 资源"

# Linux on a 1 GiB VPS normally exposes slightly less than 1024 MiB as MemTotal.
MIN_RAM_MIB=900
SYSTEM_RESERVE_MIB=2560
case "${VIRTUALIZATION_MODE}" in
  lxc)
    MIN_FREE_MIB=4608
    WARN_FREE_MIB=5120
    MIN_POOL_GB=2
    HOST_MIN_DISK="4.5 GiB"
    ;;
  kvm|hybrid)
    MIN_FREE_MIB=6656
    WARN_FREE_MIB=8192
    MIN_POOL_GB=4
    HOST_MIN_DISK="6.5 GiB"
    ;;
esac

(( CPU_CORES >= 1 )) || die "CPU 不足：${VIRTUALIZATION_LABEL} Host 最低需要 1 核"
(( MEM_TOTAL_MIB >= MIN_RAM_MIB )) || die "内存不足：${VIRTUALIZATION_LABEL} Host 最低需要 1GB 内存（系统当前可见 ${MEM_TOTAL_MIB} MiB）"
(( FREE_MIB >= MIN_FREE_MIB )) || die "可用硬盘不足：${VIRTUALIZATION_LABEL} Host 最低需要 ${HOST_MIN_DISK} 可用硬盘"

MAX_SAFE_GB=$(( (FREE_MIB - SYSTEM_RESERVE_MIB) / 1024 ))
(( MAX_SAFE_GB >= MIN_POOL_GB )) || die "磁盘无法安全创建 natpool：至少需为系统/XNAT 保留约 2.5GiB"
RECOMMENDED_GB="${MAX_SAFE_GB}"

if [[ -t 0 ]]; then
  echo
  echo "========================================"
  echo "       XNAT Host 安装 · 环境确认"
  echo "========================================"
  printf '  模式：        %s\n' "${VIRTUALIZATION_LABEL}"
  printf '  CPU：         %s 核      ✓ 最低 1 核\n' "${CPU_CORES}"
  printf '  内存：        %s MiB    ✓ 最低 1 GB\n' "${MEM_TOTAL_MIB}"
  python3 - "${FREE_MIB}" "${MIN_FREE_MIB}" <<'PY_RES'
import sys
free=int(sys.argv[1]); minimum=int(sys.argv[2])
print(f"  可用硬盘：    {free/1024:.2f} GiB ✓ 最低 {minimum/1024:.1f} GiB")
PY_RES
  if [[ "${VIRTUALIZATION_MODE}" == "kvm" || "${VIRTUALIZATION_MODE}" == "hybrid" ]]; then
    echo "  /dev/kvm：    ✓ 可用"
  fi
  echo "  系统安全预留：约 2.5 GiB"
  echo "  推荐 natpool：${RECOMMENDED_GB} GiB"
  if (( FREE_MIB < WARN_FREE_MIB )); then
    echo
    echo "  [WARN] 当前属于最低容量区间，仅建议少量轻量实例。"
  fi
fi

if [[ -z "${NATPOOL_GB}" ]]; then
  if [[ -t 0 ]]; then
    echo
    echo "========================================"
    echo "       XNAT Host 安装 · 存储分配"
    echo "========================================"
    echo "默认已自动计算 natpool，普通安装直接回车即可。"
    echo "  系统/XNAT 安全预留：约 2.5 GiB"
    echo "  natpool 推荐值：     ${RECOMMENDED_GB} GiB"
    echo "  natpool 最低值：     ${MIN_POOL_GB} GiB"
    read -r -p "natpool 大小 [${RECOMMENDED_GB}]: " NATPOOL_GB
    NATPOOL_GB="${NATPOOL_GB:-${RECOMMENDED_GB}}"
  else
    NATPOOL_GB="${RECOMMENDED_GB}"
  fi
fi

[[ "${NATPOOL_GB}" =~ ^[0-9]+$ ]] || die "NATPOOL_GB 必须是整数 GiB"
(( NATPOOL_GB >= MIN_POOL_GB )) || die "${VIRTUALIZATION_LABEL} 模式 natpool 至少需要 ${MIN_POOL_GB}GiB"
(( NATPOOL_GB <= MAX_SAFE_GB )) || die "natpool=${NATPOOL_GB}GiB 过大；当前安全上限 ${MAX_SAFE_GB}GiB，需要为系统/XNAT 保留约 2.5GiB"

if command -v incus''',
)
replace_once(
    p,
    '''incus launch images:debian/12 "${TEST_NAME}" \
  --storage "${POOL_NAME}" \
  --config limits.cpu=1 \
  --config limits.memory=128MiB \
  --device root,size=2GiB''',
    '''incus launch images:alpine/3.24 "${TEST_NAME}" \
  --storage "${POOL_NAME}" \
  --config limits.cpu=1 \
  --config limits.memory=64MiB \
  --device root,size=512MiB''',
)
replace_once(
    p,
    '''(( BYTES >= 1500*1024*1024 && BYTES <= 2300*1024*1024 )) ||
  die "2GiB 磁盘配额验证失败"

incus exec "${TEST_NAME}" -- getent hosts deb.debian.org >/dev/null ||
  die "测试容器无法联网"''',
    '''(( BYTES >= 384*1024*1024 && BYTES <= 640*1024*1024 )) ||
  die "512MiB LXC 磁盘配额验证失败"

incus exec "${TEST_NAME}" -- sh -lc "apk update >/dev/null" ||
  die "Alpine LXC 测试容器无法联网"''',
)

# ---------------------------------------------------------------------------
# Upgrade compatibility and visible version.
# ---------------------------------------------------------------------------
replace_once(
    "scripts/upgrade-panel.sh",
    'case "$CURRENT_VERSION" in\n  1.4.2-dev1)',
    'case "$CURRENT_VERSION" in\n  1.4.3) UPGRADE_PATH="verified-v1.4.3" ;;\n  1.4.2-dev1)',
)
replace_once(
    "scripts/upgrade-host-agent.sh",
    'echo "首次启用 Agent v1.1.x 虚拟化能力：请选择 Host 虚拟化模式。"',
    'echo "首次配置 Host 虚拟化能力：请选择运行模式。"',
)
replace_once("panel/app/templates/base.html", "XNAT v1.4.3 Multi-Node", "XNAT v1.5.0 Multi-Node")

# ---------------------------------------------------------------------------
# Regression guards.
# ---------------------------------------------------------------------------
p = "scripts/check.sh"
text = read(p)
marker = "# v1.3.2 Mobile API v1 contract for XNAT Android v1.0.0.\n"
guards = '''# v1.5.0 low-resource Host / Alpine compatibility contracts.
grep -q 'MIN_FREE_MIB=4608' scripts/install-host.sh
grep -q 'MIN_FREE_MIB=6656' scripts/install-host.sh
grep -q 'images:alpine/3.24' scripts/install-host.sh
grep -q 'def guest_os_family' agent/natvps_agent/main.py
grep -q 'apk add --no-cache openssh' agent/natvps_agent/main.py
grep -q 'disk_size_value' agent/natvps_agent/main.py
grep -q 'family not in {"apt", "alpine"}' panel/app/main.py
grep -q '0.125' panel/app/templates/admin.html
grep -q 'physical_remaining_disk_gb' panel/app/nodes.py
grep -q '1.4.3) UPGRADE_PATH="verified-v1.4.3"' scripts/upgrade-panel.sh

'''
if guards not in text:
    if marker not in text:
        raise RuntimeError("scripts/check.sh insertion marker missing")
    text = text.replace(marker, guards + marker, 1)
write(p, text)

print("XNAT v1.5.0 patch applied")
