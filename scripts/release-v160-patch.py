from __future__ import annotations

from pathlib import Path
import json
import re

ROOT = Path(".")

def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")

def write(path: str, text: str) -> None:
    (ROOT / path).write_text(text, encoding="utf-8")

def replace_once(path: str, old: str, new: str) -> None:
    text = read(path)
    if old not in text:
        raise SystemExit(f"{path}: anchor not found: {old[:100]!r}")
    write(path, text.replace(old, new, 1))

def sub_once(path: str, pattern: str, repl: str, flags: int = 0) -> None:
    text = read(path)
    new_text, count = re.subn(pattern, lambda m: repl, text, count=1, flags=flags)
    if count != 1:
        raise SystemExit(f"{path}: regex anchor count={count}: {pattern[:100]!r}")
    write(path, new_text)

SUPPORTED_OS = r'''. /etc/os-release
case "${ID:-}:${VERSION_CODENAME:-}" in
  debian:bookworm) OS_LABEL="Debian 12 Bookworm" ;;
  debian:trixie) OS_LABEL="Debian 13 Trixie" ;;
  ubuntu:jammy) OS_LABEL="Ubuntu 22.04 LTS Jammy" ;;
  ubuntu:noble) OS_LABEL="Ubuntu 24.04 LTS Noble" ;;
  ubuntu:resolute) OS_LABEL="Ubuntu 26.04 LTS Resolute" ;;
  *) die "当前系统不受支持。支持：Debian 12/13、Ubuntu 22.04/24.04/26.04 LTS" ;;
esac
'''

write("VERSION", "1.6.0\n")
write("panel/VERSION", "1.6.0\n")
write("panel/app/__init__.py", '__version__ = "1.6.0"\n')
meta = json.loads(read("release.json"))
meta["release_version"] = "1.6.0"
meta["panel_version"] = "1.6.0"
write("release.json", json.dumps(meta, ensure_ascii=False, indent=2) + "\n")
replace_once("panel/app/main.py", '"version": "1.5.0"', '"version": "1.6.0"')
replace_once("panel/app/templates/base.html", "XNAT v1.5.0 Multi-Node", "XNAT v1.6.0 Multi-Node")

replace_once(
    "scripts/install-panel.sh",
    '. /etc/os-release\n[[ "${ID:-}" == "debian" && "${VERSION_CODENAME:-}" == "bookworm" ]] || die "要求 Debian 12 bookworm"\n',
    SUPPORTED_OS + 'info "系统兼容性"\necho "检测到：${OS_LABEL}"\n'
)
replace_once(
    "scripts/upgrade-panel.sh",
    '. /etc/os-release\n[[ "${ID:-}" == "debian" && "${VERSION_CODENAME:-}" == "bookworm" ]] || die "当前正式版要求 Debian 12 bookworm"\n',
    SUPPORTED_OS
)
replace_once(
    "scripts/upgrade-panel.sh",
    'case "$CURRENT_VERSION" in\n  1.4.3) UPGRADE_PATH="verified-v1.4.3" ;;',
    'case "$CURRENT_VERSION" in\n  1.5.0) UPGRADE_PATH="verified-v1.5.0" ;;\n  1.4.3) UPGRADE_PATH="verified-v1.4.3" ;;'
)

replace_once(
    "scripts/install-host.sh",
    '. /etc/os-release\n[[ "${ID:-}" == "debian" && "${VERSION_CODENAME:-}" == "bookworm" ]] || \\\n  die "要求 Debian 12 bookworm"\n',
    SUPPORTED_OS + 'INCUS_SUITE="${VERSION_CODENAME}"\n'
)

new_host_functions = r'''detect_host_resources(){
  CPU_CORES="$(nproc 2>/dev/null || echo 1)"
  MEM_TOTAL_MIB="$(awk '/^MemTotal:/{print int($2/1024)}' /proc/meminfo)"
  MEM_AVAILABLE_MIB="$(awk '/^MemAvailable:/{print int($2/1024)}' /proc/meminfo)"
  ROOT_TOTAL_MIB="$(df -B1 --output=size / | tail -n1 | awk '{print int($1/1024/1024)}')"
  ROOT_USED_MIB="$(df -B1 --output=used / | tail -n1 | awk '{print int($1/1024/1024)}')"
  ROOT_AVAIL_MIB="$(df -B1 --output=avail / | tail -n1 | awk '{print int($1/1024/1024)}')"
  [[ "${CPU_CORES}" =~ ^[0-9]+$ && "${MEM_TOTAL_MIB}" =~ ^[0-9]+$ && "${MEM_AVAILABLE_MIB}" =~ ^[0-9]+$ && "${ROOT_TOTAL_MIB}" =~ ^[0-9]+$ && "${ROOT_USED_MIB}" =~ ^[0-9]+$ && "${ROOT_AVAIL_MIB}" =~ ^[0-9]+$ ]] || die "无法读取 Host 资源"
}

compute_mode_capacity(){
  local mode="$1" prefix total_min reserve min_pool warn_total requires_kvm="false"
  local pool_mib pool_gb ok="true" reason=""
  case "$mode" in
    lxc) prefix="LXC"; total_min=4608; reserve=1024; min_pool=1; warn_total=5120 ;;
    kvm) prefix="KVM"; total_min=6656; reserve=1536; min_pool=4; warn_total=8192; requires_kvm="true" ;;
    hybrid) prefix="HYBRID"; total_min=6656; reserve=1536; min_pool=4; warn_total=8192; requires_kvm="true" ;;
    *) die "未知虚拟化模式：${mode}" ;;
  esac
  pool_mib=$(( ROOT_AVAIL_MIB > reserve ? ROOT_AVAIL_MIB - reserve : 0 ))
  pool_gb=$(( pool_mib / 1024 ))
  if (( CPU_CORES < 1 )); then ok="false"; reason="${reason:+${reason}；}CPU 少于 1 核"; fi
  if (( MEM_TOTAL_MIB < 900 )); then ok="false"; reason="${reason:+${reason}；}总内存不足 1GB"; fi
  if (( ROOT_TOTAL_MIB < total_min )); then ok="false"; reason="${reason:+${reason}；}总硬盘低于 $((total_min/1024)).$(((total_min%1024)*10/1024))GiB 基线"; fi
  if (( pool_gb < min_pool )); then ok="false"; reason="${reason:+${reason}；}当前可用空间只能安全提供 ${pool_gb}GiB natpool，至少需要 ${min_pool}GiB"; fi
  if [[ "$requires_kvm" == "true" ]] && ! detect_kvm; then ok="false"; reason="${reason:+${reason}；}/dev/kvm 不可用"; fi
  printf -v "${prefix}_OK" '%s' "$ok"
  printf -v "${prefix}_REASON" '%s' "${reason:-满足安装条件}"
  printf -v "${prefix}_POOL_GB" '%s' "$pool_gb"
  printf -v "${prefix}_RESERVE_MIB" '%s' "$reserve"
  printf -v "${prefix}_MIN_POOL_GB" '%s' "$min_pool"
  printf -v "${prefix}_WARN_TOTAL_MIB" '%s' "$warn_total"
}

mode_status(){
  local ok="$1" reason="$2"
  [[ "$ok" == "true" ]] && printf '✓ 可安装' || printf '✗ %s' "$reason"
}

select_virtualization_mode(){
  local choice mode="${VIRTUALIZATION_MODE,,}"
  case "$mode" in
    1|lxc) mode="lxc" ;;
    2|kvm) mode="kvm" ;;
    3|hybrid|both|lxc+kvm|kvm+lxc) mode="hybrid" ;;
    "") ;;
    *) die "VIRTUALIZATION_MODE 仅支持 lxc / kvm / hybrid" ;;
  esac
  detect_host_resources
  compute_mode_capacity lxc
  compute_mode_capacity kvm
  compute_mode_capacity hybrid
  if [[ -z "$mode" && -t 0 ]]; then
    echo
    echo "=================================================="
    echo "                 XNAT Host 安装"
    echo "=================================================="
    echo "当前服务器："
    echo "  系统：       ${OS_LABEL}"
    echo "  CPU：        ${CPU_CORES} Core"
    echo "  内存：       总计 ${MEM_TOTAL_MIB} MiB / 当前可用 ${MEM_AVAILABLE_MIB} MiB"
    python3 - "${ROOT_TOTAL_MIB}" "${ROOT_USED_MIB}" "${ROOT_AVAIL_MIB}" <<'PY_DISK'
import sys
total, used, avail = map(int, sys.argv[1:])
print(f"  硬盘：       总计 {total/1024:.2f} GiB / 已用 {used/1024:.2f} GiB / 当前可用 {avail/1024:.2f} GiB")
PY_DISK
    detect_kvm && echo "  /dev/kvm：   ✓ 可用" || echo "  /dev/kvm：   ✗ 不可用"
    echo
    echo "请选择 Host 运行模式："
    echo "  1) LXC"
    echo "     母鸡基线：1C / 1GB / 4.5GiB 总硬盘"
    echo "     当前预计可用于 natpool：${LXC_POOL_GB} GiB（已预留约 1GiB 给系统/XNAT）"
    echo "     状态：$(mode_status "${LXC_OK}" "${LXC_REASON}")"
    echo "  2) KVM"
    echo "     母鸡基线：1C / 1GB / 6.5GiB 总硬盘 + /dev/kvm"
    echo "     当前预计可用于 natpool：${KVM_POOL_GB} GiB（已预留约 1.5GiB 给系统/XNAT）"
    echo "     状态：$(mode_status "${KVM_OK}" "${KVM_REASON}")"
    echo "  3) LXC + KVM"
    echo "     母鸡基线：1C / 1GB / 6.5GiB 总硬盘 + /dev/kvm"
    echo "     当前预计可用于 natpool：${HYBRID_POOL_GB} GiB（已预留约 1.5GiB 给系统/XNAT）"
    echo "     状态：$(mode_status "${HYBRID_OK}" "${HYBRID_REASON}")"
    read -r -p "请选择 [1-3] [1]: " choice
    choice="${choice:-1}"
    case "$choice" in 1) mode="lxc";; 2) mode="kvm";; 3) mode="hybrid";; *) die "无效选择：${choice}";; esac
  fi
  mode="${mode:-lxc}"
  case "$mode" in
    lxc)
      [[ "${LXC_OK}" == "true" ]] || die "当前 Host 不满足 LXC 安装条件：${LXC_REASON}"
      VIRTUALIZATION_MODES_JSON='["lxc"]'; VIRTUALIZATION_LABEL="LXC"; MAX_SAFE_GB="${LXC_POOL_GB}"; SYSTEM_RESERVE_MIB="${LXC_RESERVE_MIB}"; MIN_POOL_GB="${LXC_MIN_POOL_GB}"; WARN_TOTAL_MIB="${LXC_WARN_TOTAL_MIB}"
      ;;
    kvm)
      [[ "${KVM_OK}" == "true" ]] || die "当前 Host 不满足 KVM 安装条件：${KVM_REASON}"
      VIRTUALIZATION_MODES_JSON='["kvm"]'; VIRTUALIZATION_LABEL="KVM"; MAX_SAFE_GB="${KVM_POOL_GB}"; SYSTEM_RESERVE_MIB="${KVM_RESERVE_MIB}"; MIN_POOL_GB="${KVM_MIN_POOL_GB}"; WARN_TOTAL_MIB="${KVM_WARN_TOTAL_MIB}"
      ;;
    hybrid)
      [[ "${HYBRID_OK}" == "true" ]] || die "当前 Host 不满足 LXC + KVM 安装条件：${HYBRID_REASON}"
      VIRTUALIZATION_MODES_JSON='["lxc","kvm"]'; VIRTUALIZATION_LABEL="LXC + KVM"; MAX_SAFE_GB="${HYBRID_POOL_GB}"; SYSTEM_RESERVE_MIB="${HYBRID_RESERVE_MIB}"; MIN_POOL_GB="${HYBRID_MIN_POOL_GB}"; WARN_TOTAL_MIB="${HYBRID_WARN_TOTAL_MIB}"
      ;;
  esac
  VIRTUALIZATION_MODE="$mode"
}
'''
sub_once("scripts/install-host.sh", r'select_virtualization_mode\(\)\{.*?\n\}\ntrap cleanup_test EXIT', new_host_functions + '\ntrap cleanup_test EXIT', flags=re.S)

new_capacity = r'''select_virtualization_mode
RECOMMENDED_GB="${MAX_SAFE_GB}"
if [[ -t 0 ]]; then
  echo
  echo "=================================================="
  echo "       XNAT Host 安装 · 环境确认"
  echo "=================================================="
  echo "  系统：          ${OS_LABEL}"
  echo "  模式：          ${VIRTUALIZATION_LABEL}"
  printf '  CPU：           %s 核\n' "${CPU_CORES}"
  printf '  内存：          总计 %s MiB / 当前可用 %s MiB\n' "${MEM_TOTAL_MIB}" "${MEM_AVAILABLE_MIB}"
  python3 - "${ROOT_TOTAL_MIB}" "${ROOT_USED_MIB}" "${ROOT_AVAIL_MIB}" "${SYSTEM_RESERVE_MIB}" <<'PY_RES'
import sys
total, used, avail, reserve = map(int, sys.argv[1:])
print(f"  总硬盘：        {total/1024:.2f} GiB")
print(f"  当前已用：      {used/1024:.2f} GiB")
print(f"  当前可用：      {avail/1024:.2f} GiB")
print(f"  系统/XNAT预留：约 {reserve/1024:.2f} GiB")
PY_RES
  [[ "${VIRTUALIZATION_MODE}" == "lxc" ]] || echo "  /dev/kvm：      ✓ 可用"
  echo "  natpool 可分配：${MAX_SAFE_GB} GiB"
  if (( ROOT_TOTAL_MIB < WARN_TOTAL_MIB || MAX_SAFE_GB == MIN_POOL_GB )); then echo "  [WARN] 当前属于低容量区间，建议只创建少量轻量实例。"; fi
fi
if [[ -z "${NATPOOL_GB}" ]]; then
  if [[ -t 0 ]]; then
    echo
    echo "========================================"
    echo "       XNAT Host 安装 · 存储分配"
    echo "========================================"
    echo "已按当前真实可用空间自动计算 natpool，普通安装直接回车即可。"
    python3 - "${ROOT_AVAIL_MIB}" "${SYSTEM_RESERVE_MIB}" <<'PY_POOL'
import sys
avail, reserve = map(int, sys.argv[1:])
print(f"  当前可用硬盘：       {avail/1024:.2f} GiB")
print(f"  系统/XNAT安全预留：  约 {reserve/1024:.2f} GiB")
PY_POOL
    echo "  natpool 推荐值：      ${RECOMMENDED_GB} GiB"
    echo "  natpool 最低值：      ${MIN_POOL_GB} GiB"
    read -r -p "natpool 大小 [${RECOMMENDED_GB}]: " NATPOOL_GB
    NATPOOL_GB="${NATPOOL_GB:-${RECOMMENDED_GB}}"
  else NATPOOL_GB="${RECOMMENDED_GB}"; fi
fi
[[ "${NATPOOL_GB}" =~ ^[0-9]+$ ]] || die "NATPOOL_GB 必须是整数 GiB"
(( NATPOOL_GB >= MIN_POOL_GB )) || die "${VIRTUALIZATION_LABEL} 模式 natpool 至少需要 ${MIN_POOL_GB}GiB"
(( NATPOOL_GB <= MAX_SAFE_GB )) || die "natpool=${NATPOOL_GB}GiB 超过当前安全上限 ${MAX_SAFE_GB}GiB；请保留系统/XNAT运行空间"
'''
sub_once("scripts/install-host.sh", r'select_virtualization_mode\n\nCPU_CORES=.*?\(\( NATPOOL_GB <= MAX_SAFE_GB \)\) \|\| die "natpool=.*?\n', new_capacity + '\n', flags=re.S)
replace_once("scripts/install-host.sh", 'info "1/7 安装 Debian / Incus 依赖"', 'info "1/7 安装系统 / Incus 依赖"')
replace_once("scripts/install-host.sh", 'Suites: bookworm\n', 'Suites: ${INCUS_SUITE}\n')
replace_once("scripts/install-host.sh", 'Signed-By: /etc/apt/keyrings/zabbly-incus.asc\nEOF\n\napt-get update\napt-get install -y incus\n', '''Signed-By: /etc/apt/keyrings/zabbly-incus.asc
EOF

cat > /etc/apt/preferences.d/xnat-zabbly-incus <<'EOF_PIN'
Package: incus incus-base incus-client
Pin: origin pkgs.zabbly.com
Pin-Priority: 1001
EOF_PIN

apt-get update
apt-get install -y incus
''')

replace_once("scripts/upgrade-host-agent.sh", '. /etc/os-release\n[[ "${ID:-}" == "debian" && "${VERSION_CODENAME:-}" == "bookworm" ]] || die "当前正式版要求 Debian 12 bookworm"\n', SUPPORTED_OS)

xnat = read("scripts/xnat")
anchor = 'have(){ command -v "$1" >/dev/null 2>&1; }\n'
helper = r'''have(){ command -v "$1" >/dev/null 2>&1; }

supported_os_label(){
  local file="${1:-$OS_RELEASE_FILE}" id codename
  [[ -r "$file" ]] || return 1
  id="$(. "$file"; printf '%s' "${ID:-}")"
  codename="$(. "$file"; printf '%s' "${VERSION_CODENAME:-}")"
  case "${id}:${codename}" in
    debian:bookworm) printf '%s' "Debian 12 Bookworm" ;;
    debian:trixie) printf '%s' "Debian 13 Trixie" ;;
    ubuntu:jammy) printf '%s' "Ubuntu 22.04 LTS Jammy" ;;
    ubuntu:noble) printf '%s' "Ubuntu 24.04 LTS Noble" ;;
    ubuntu:resolute) printf '%s' "Ubuntu 26.04 LTS Resolute" ;;
    *) return 1 ;;
  esac
}
'''
if anchor not in xnat: raise SystemExit("scripts/xnat helper anchor missing")
xnat = xnat.replace(anchor, helper, 1)
old = '''  if [[ -r "$OS_RELEASE_FILE" ]] && grep -q '^ID=debian' "$OS_RELEASE_FILE" && grep -q '^VERSION_CODENAME=bookworm' "$OS_RELEASE_FILE"; then
    preflight_ok "Debian 12 Bookworm"
  else
    preflight_fail "当前正式版要求 Debian 12 Bookworm" || true; ((fails+=1))
  fi
'''
new = '''  local os_label=""
  if os_label="$(supported_os_label "$OS_RELEASE_FILE")"; then preflight_ok "$os_label"; else preflight_fail "系统不受支持：需要 Debian 12/13 或 Ubuntu 22.04/24.04/26.04 LTS" || true; ((fails+=1)); fi
'''
if old not in xnat: raise SystemExit("scripts/xnat preflight anchor missing")
xnat = xnat.replace(old, new, 1)
old = '''  if [[ -r "$OS_RELEASE_FILE" ]] && grep -q '^ID=debian' "$OS_RELEASE_FILE" && grep -q '^VERSION_CODENAME=bookworm' "$OS_RELEASE_FILE"; then
    doctor_ok "Debian 12 Bookworm"
  else
    doctor_warn "当前系统不是 Debian 12 Bookworm"
  fi
'''
new = '''  local os_label=""
  if os_label="$(supported_os_label "$OS_RELEASE_FILE")"; then doctor_ok "$os_label"; else doctor_warn "当前系统不在正式支持列表（Debian 12/13、Ubuntu 22.04/24.04/26.04 LTS）"; fi
'''
if old not in xnat: raise SystemExit("scripts/xnat doctor anchor missing")
xnat = xnat.replace(old, new, 1)
write("scripts/xnat", xnat)

readme = read("README.md")
for a,b in [("当前版本：**v1.5.0**","当前版本：**v1.6.0**"),("最新正式版本：**v1.5.0**","最新正式版本：**v1.6.0**"),("当前正式源码关系：**XNAT Release v1.5.0 / Panel v1.5.0 / Mobile API v1 / Host Agent v1.2.0 / Agent API v1**。","当前正式源码关系：**XNAT Release v1.6.0 / Panel v1.6.0 / Mobile API v1 / Host Agent v1.2.0 / Agent API v1**。"),("```text\nDebian 12 Bookworm\n```","```text\nDebian 12 Bookworm / Debian 13 Trixie\nUbuntu 22.04 LTS / 24.04 LTS / 26.04 LTS\n```"),("Host 需要支持 Incus / LXC 所需的虚拟化能力。","Host 会自动识别系统版本、CPU、内存、总硬盘、当前可用硬盘与 `/dev/kvm`，再判断 LXC / KVM / 混合模式是否可安装。"),("3. **natpool 大小**：用于存放用户 VPS 磁盘；脚本会检测磁盘并给出推荐值。","3. **真实资源容量**：菜单直接显示总硬盘、当前可用硬盘和各模式预计可分配 natpool；LXC 按总盘 4.5GiB 基线判断，不再要求安装后仍剩 4.5GiB。")]:
    readme = readme.replace(a,b,1)
write("README.md", readme)
mobile = read("docs/MOBILE_API.md")
mobile = re.sub(r'XNAT Panel `v1\.5\.0` 继续保持 \*\*Mobile API v1\*\*。[^\n]*', 'XNAT Panel `v1.6.0` 继续保持 **Mobile API v1**。本次调整 Panel / Host 母机系统兼容与 Host 安装容量检测；Mobile API v1 的路由、认证和既有客户端语义保持兼容。', mobile, count=1)
write("docs/MOBILE_API.md", mobile)

changelog = read("CHANGELOG.md")
entry = '''## v1.6.0

- Panel / Host 支持 Debian 12/13 与 Ubuntu 22.04/24.04/26.04 LTS，不再写死 Debian 12。
- Host 安装模式菜单在选择前显示系统、CPU、总/可用内存、总/已用/可用硬盘、KVM 状态与各模式预计 natpool。
- LXC 的 4.5GiB 改为母机总盘基线；按当前 Avail 预留约 1GiB 后计算 natpool，4.9G 总盘 / 3.9G 可用的轻量 Host 可正常进入 LXC 安装。
- KVM / 混合保留 6.5GiB 总盘、/dev/kvm、约 1.5GiB 系统预留与至少 4GiB natpool 的技术基线。
- Host 默认模式改为 LXC；不满足条件的模式在选择前直接显示原因。
- Zabbly Incus 源根据系统 codename 自动配置，并增加包优先级保护。
- `xnat update`、`xnat doctor`、Panel/Host 安装与升级统一使用同一系统支持矩阵。
- Panel 升级至 v1.6.0；Host Agent 核心保持 v1.2.0，Agent API / Mobile API 保持 v1。

'''
if "## v1.6.0" not in changelog: changelog = changelog.replace("# Changelog\n\n", "# Changelog\n\n"+entry, 1)
write("CHANGELOG.md", changelog)

build = read("scripts/build-release.sh")
notes = r'''cat > "$DIST/RELEASE_NOTES.md" <<EOF_NOTES
# XNAT v${RELEASE_VERSION}

本次版本修复母机系统兼容和 Host 安装容量判断，让低配 LXC Host 按真实资源计算，而不是把母机总盘门槛误当成安装后的剩余空间。

- Panel：v${PANEL_VERSION}
- Host Agent：v${AGENT_VERSION}
- Agent API：v${AGENT_API_VERSION}
- Mobile API：v1
- Panel / Host 支持 Debian 12/13、Ubuntu 22.04/24.04/26.04 LTS
- Host 菜单显示 CPU、总/可用内存、总/已用/可用硬盘、KVM 与各模式预计 natpool
- LXC：1C / 1GB / 4.5GiB 总盘基线，约 1GiB 系统/XNAT 预留后动态计算 natpool
- KVM / 混合：1C / 1GB / 6.5GiB 总盘、/dev/kvm、至少 4GiB natpool
- 默认 Host 模式改为 LXC；不满足模式会在选择前显示原因
- Zabbly Incus 源按发行版 codename 自动配置并优先使用 Zabbly 包
- v1.5.0 → v${PANEL_VERSION} 支持原地升级，业务数据保持不变
- Host Agent 核心保持 v${AGENT_VERSION} / Agent API v${AGENT_API_VERSION}

**由 𝐍𝐀𝐌𝐄𝐋𝐄𝐒𝐒 和 GPT 倾力打造**

Panel / Host 推荐升级命令：

    xnat update ${RELEASE_VERSION}
EOF_NOTES'''
build, n = re.subn(r'cat > "\$DIST/RELEASE_NOTES\.md" <<EOF_NOTES.*?EOF_NOTES', lambda m: notes, build, count=1, flags=re.S)
if n != 1: raise SystemExit("release notes anchor missing")
write("scripts/build-release.sh", build)

check = read("scripts/check.sh")
for a,b in [("assert release == '1.5.0'","assert release == '1.6.0'"),("assert panel == '1.5.0'","assert panel == '1.6.0'"),("'\\\"version\\\": \\\"1.5.0\\\"'","'\\\"version\\\": \\\"1.6.0\\\"'"),("XNAT v1.5.0 Multi-Node","XNAT v1.6.0 Multi-Node"),("'v1.5.0' in docs","'v1.6.0' in docs"),("最新正式版本：**v1.5.0**","最新正式版本：**v1.6.0**")]: check = check.replace(a,b,1)
old = '''# v1.5.0 low-resource Host / Alpine compatibility contracts.
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
new = '''# v1.6.0 multi-OS + real Host capacity contracts.
grep -q 'debian:trixie' scripts/install-host.sh
grep -q 'ubuntu:jammy' scripts/install-host.sh
grep -q 'ubuntu:noble' scripts/install-host.sh
grep -q 'ubuntu:resolute' scripts/install-host.sh
grep -q 'ROOT_TOTAL_MIB' scripts/install-host.sh
grep -q 'ROOT_AVAIL_MIB' scripts/install-host.sh
grep -q 'total_min=4608; reserve=1024; min_pool=1' scripts/install-host.sh
grep -q 'total_min=6656; reserve=1536; min_pool=4' scripts/install-host.sh
grep -q '请选择 \\[1-3\\] \\[1\\]' scripts/install-host.sh
grep -q 'Suites: ${INCUS_SUITE}' scripts/install-host.sh
grep -q 'Pin: origin pkgs.zabbly.com' scripts/install-host.sh
grep -q 'supported_os_label' scripts/xnat
grep -q '1.5.0) UPGRADE_PATH="verified-v1.5.0"' scripts/upgrade-panel.sh
grep -q 'images:alpine/3.24' scripts/install-host.sh
grep -q 'def guest_os_family' agent/natvps_agent/main.py
grep -q 'apk add --no-cache openssh' agent/natvps_agent/main.py
grep -q '0.125' panel/app/templates/admin.html
grep -q 'physical_remaining_disk_gb' panel/app/nodes.py
! grep -RIn '要求 Debian 12 bookworm\\|当前正式版要求 Debian 12 Bookworm' scripts >/tmp/xnat-v160-debian12-only.txt
'''
if old not in check: raise SystemExit("check contract anchor missing")
check = check.replace(old,new,1)
write("scripts/check.sh", check)
print("XNAT v1.6.0 patch applied")
