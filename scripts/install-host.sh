#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC_DIR="${REPO_ROOT}/agent"
PROJECT_RELEASE="$(tr -d '[:space:]' < "${REPO_ROOT}/VERSION")"
COMPONENT_VERSION="$(tr -d '[:space:]' < "${SRC_DIR}/VERSION")"
DEST_DIR="${XNAT_AGENT_DIR:-/opt/xnat/agent}"
AGENT_PORT="${AGENT_PORT:-29443}"

POOL_NAME="${POOL_NAME:-natpool}"
BRIDGE_NAME="${BRIDGE_NAME:-incusbr0}"
BRIDGE_ADDR="${BRIDGE_ADDR:-10.12.139.1/24}"
NATPOOL_GB="${NATPOOL_GB:-}"
PANEL_CIDR="${PANEL_CIDR:-${PANEL_IP:-}}"
VIRTUALIZATION_MODE="${VIRTUALIZATION_MODE:-}"

ZABBLY_FPR="4EFC590696CB15B87C73A3AD82CC8797C838DCFD"
TEST_NAME="xnat-install-test"
TEST_VM_NAME="xnat-install-test-vm"
CRED_FILE="/root/xnat-host-agent-credentials.txt"

info(){ echo; echo "==== $* ===="; }
die(){ echo "[ERROR] $*" >&2; exit 1; }

cleanup_test(){
  if command -v incus >/dev/null 2>&1; then
    incus delete "${TEST_NAME}" --force >/dev/null 2>&1 || true
    incus delete "${TEST_VM_NAME}" --force >/dev/null 2>&1 || true
  fi
}

detect_kvm(){
  [[ -c /dev/kvm && -r /dev/kvm && -w /dev/kvm ]]
}

detect_host_resources(){
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
    lxc) prefix="LXC"; total_min=4608; reserve=1024; min_pool=1; warn_total=8192 ;;
    kvm) prefix="KVM"; total_min=6656; reserve=1536; min_pool=4; warn_total=12288; requires_kvm="true" ;;
    hybrid) prefix="HYBRID"; total_min=6656; reserve=1536; min_pool=4; warn_total=12288; requires_kvm="true" ;;
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
    echo "     最低安装：1C / 1GB / 4.5GiB 总硬盘"
    echo "     建议配置：1C / 1GB / 8GiB+ 总硬盘"
    echo "     当前预计可用于 natpool：${LXC_POOL_GB} GiB（已预留约 1GiB 给系统/XNAT）"
    echo "     状态：$(mode_status "${LXC_OK}" "${LXC_REASON}")"
    echo "  2) KVM"
    echo "     最低安装：1C / 1GB / 6.5GiB 总硬盘 + /dev/kvm"
    echo "     建议配置：2C / 2GB / 12GiB+ 总硬盘"
    echo "     当前预计可用于 natpool：${KVM_POOL_GB} GiB（已预留约 1.5GiB 给系统/XNAT）"
    echo "     状态：$(mode_status "${KVM_OK}" "${KVM_REASON}")"
    echo "  3) LXC + KVM"
    echo "     最低安装：1C / 1GB / 6.5GiB 总硬盘 + /dev/kvm"
    echo "     建议配置：2C / 2GB / 12GiB+ 总硬盘"
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

trap cleanup_test EXIT

[[ $EUID -eq 0 ]] || die "请使用 root 运行"
[[ -f "${SRC_DIR}/requirements.txt" ]] || die "找不到 agent 源码，请在 xnat 仓库中运行本脚本"
[[ -f "${REPO_ROOT}/scripts/xnat-firewall" ]] || die "找不到 XNAT 防火墙脚本"
[[ -f "${REPO_ROOT}/scripts/xnat" ]] || die "找不到 XNAT 管理脚本"

. /etc/os-release
case "${ID:-}:${VERSION_CODENAME:-}" in
  debian:bookworm) OS_LABEL="Debian 12 Bookworm" ;;
  debian:trixie) OS_LABEL="Debian 13 Trixie" ;;
  ubuntu:jammy) OS_LABEL="Ubuntu 22.04 LTS Jammy" ;;
  ubuntu:noble) OS_LABEL="Ubuntu 24.04 LTS Noble" ;;
  ubuntu:resolute) OS_LABEL="Ubuntu 26.04 LTS Resolute" ;;
  *) die "当前系统不受支持。支持：Debian 12/13、Ubuntu 22.04/24.04/26.04 LTS" ;;
esac
INCUS_SUITE="${VERSION_CODENAME}"

# 全新 Debian 可能没有 python3。先安装基础校验依赖，再验证 PANEL_IP。
info "0/7 安装基础校验依赖"
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates python3

if [[ -z "${PANEL_CIDR}" ]]; then
  if [[ -t 0 ]]; then
    echo
    echo "========================================"
    echo "       XNAT Host 安装 · 1/3"
    echo "========================================"
    echo
    echo "请输入 XNAT Panel Server 的【真实公网 IPv4】。"
    echo
    echo "这个 IP 是运行 XNAT Panel 的 VPS/服务器公网地址，用于限制"
    echo "Host Agent ${AGENT_PORT}/TCP：只有这台 Panel 才能访问管理接口。"
    echo
    echo "请不要填写："
    echo "  - Panel 域名"
    echo "  - Cloudflare IP"
    echo "  - 当前 Host 自己的 IP"
    echo
    echo "示例：203.0.113.10"
    echo
    read -r -p "Panel 公网 IPv4: " PANEL_CIDR
  else
    die "非交互安装必须指定 PANEL_IP=Panel公网IPv4"
  fi
fi

PANEL_CIDR="$(python3 - "${PANEL_CIDR}" <<'PY'
import ipaddress, sys
try:
    n = ipaddress.ip_network(sys.argv[1], strict=False)
except Exception:
    raise SystemExit(1)
if n.version != 4:
    raise SystemExit(1)
print(n)
PY
)" || die "Panel IPv4/CIDR 无效：${PANEL_CIDR}"

select_virtualization_mode
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
print(f"  系统/XNAT安装预留：约 {reserve/1024:.2f} GiB（不等同于长期安全余量）")
PY_RES
  [[ "${VIRTUALIZATION_MODE}" == "lxc" ]] || echo "  /dev/kvm：      ✓ 可用"
  echo "  natpool 可分配：${MAX_SAFE_GB} GiB"
  if (( ROOT_TOTAL_MIB < WARN_TOTAL_MIB || MAX_SAFE_GB == MIN_POOL_GB )); then
    echo "  [WARN] 当前 Host 仅达到最低安装区间，建议只用于测试或少量轻量实例。"
    if [[ "${VIRTUALIZATION_MODE}" == "lxc" ]]; then
      echo "  [WARN] 长期运行建议使用 8GiB+ 系统盘，并持续保留 Host 系统盘可用空间。"
    else
      echo "  [WARN] 长期运行建议使用 12GiB+ 系统盘，并持续保留 Host 系统盘可用空间。"
    fi
  fi
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
print(f"  系统/XNAT安装预留：  约 {reserve/1024:.2f} GiB（长期运行仍需额外余量）")
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


if command -v incus >/dev/null 2>&1; then
  [[ -z "$(incus storage list --format csv -c n 2>/dev/null || true)" ]] ||
    die "检测到已有 Incus Storage。本脚本仅用于全新 Host。"
  [[ -z "$(incus list --format csv -c n 2>/dev/null || true)" ]] ||
    die "检测到已有 Incus VPS。本脚本拒绝覆盖。"
fi

info "1/7 安装系统 / Incus 依赖"
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y \
  ca-certificates curl gnupg openssl python3 python3-venv python3-pip \
  lvm2 thin-provisioning-tools iproute2 nftables

timedatectl set-timezone Asia/Shanghai || true

mkdir -p /etc/apt/keyrings
curl -fsSL https://pkgs.zabbly.com/key.asc -o /tmp/xnat-zabbly.asc

FPR="$(gpg --show-keys --with-colons /tmp/xnat-zabbly.asc | awk -F: '$1=="fpr"{print $10;exit}')"
[[ "${FPR}" == "${ZABBLY_FPR}" ]] || die "Zabbly Key 指纹不匹配: ${FPR}"

install -m 0644 /tmp/xnat-zabbly.asc /etc/apt/keyrings/zabbly-incus.asc

cat > /etc/apt/sources.list.d/zabbly-incus-lts-7.0.sources <<EOF
Enabled: yes
Types: deb
URIs: https://pkgs.zabbly.com/incus/lts-7.0
Suites: ${INCUS_SUITE}
Components: main
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/zabbly-incus.asc
EOF

cat > /etc/apt/preferences.d/xnat-zabbly-incus <<'EOF_PIN'
Package: incus incus-base incus-client
Pin: origin pkgs.zabbly.com
Pin-Priority: 1001
EOF_PIN

apt-get update
apt-get install -y incus
systemctl enable --now incus
sleep 2

info "2/7 创建 LVM Thin 与 NAT Bridge"
cat <<EOF | incus admin init --preseed
config:
  images.auto_update_interval: "6"

networks:
- name: ${BRIDGE_NAME}
  type: bridge
  config:
    ipv4.address: ${BRIDGE_ADDR}
    ipv4.nat: "true"
    ipv6.address: "none"

storage_pools:
- name: ${POOL_NAME}
  driver: lvm
  config:
    size: ${NATPOOL_GB}GiB
    lvm.use_thinpool: "true"

profiles:
- name: default
  config: {}
  devices:
    eth0:
      name: eth0
      network: ${BRIDGE_NAME}
      type: nic
    root:
      path: /
      pool: ${POOL_NAME}
      type: disk
EOF

[[ "$(incus storage show "${POOL_NAME}" | awk '/^driver:/{print $2}')" == "lvm" ]] ||
  die "Storage Pool 没有使用 LVM"

info "3/7 验证 LXC / 存储 / NAT Bridge"
cleanup_test

incus launch images:alpine/3.24 "${TEST_NAME}" \
  --storage "${POOL_NAME}" \
  --config limits.cpu=1 \
  --config limits.memory=64MiB \
  --device root,size=512MiB

IP=""
for _ in $(seq 1 45); do
  IP="$(
    incus exec "${TEST_NAME}" -- sh -lc \
      "ip -4 -o addr show scope global | awk '\$2 != \"lo\" {print \$4; exit}' | cut -d/ -f1" \
      2>/dev/null || true
  )"
  [[ -n "${IP}" ]] && break
  sleep 1
done

[[ -n "${IP}" ]] || die "测试容器没有获取 IPv4"

BYTES="$(
  incus exec "${TEST_NAME}" -- sh -lc \
    "df -B1 / | awk 'NR==2{print \$2}'"
)"

incus exec "${TEST_NAME}" -- df -h /

(( BYTES >= 384*1024*1024 && BYTES <= 640*1024*1024 )) ||
  die "512MiB LXC 磁盘配额验证失败"

incus exec "${TEST_NAME}" -- sh -lc "apk update >/dev/null" ||
  die "Alpine LXC 测试容器无法联网"

cleanup_test

if [[ "${VIRTUALIZATION_MODE}" == "kvm" || "${VIRTUALIZATION_MODE}" == "hybrid" ]]; then
  info "4/7 验证 KVM 虚拟机能力"
  incus launch images:debian/12 "${TEST_VM_NAME}" --vm \
    --storage "${POOL_NAME}" \
    --config limits.cpu=1 \
    --config limits.memory=512MiB \
    --device root,size=4GiB

  VM_IP=""
  for _ in $(seq 1 100); do
    VM_IP="$(incus exec "${TEST_VM_NAME}" -- sh -lc "ip -4 -o addr show scope global | awk '\$2 != \"lo\" {print \$4; exit}' | cut -d/ -f1" 2>/dev/null || true)"
    [[ -n "${VM_IP}" ]] && break
    sleep 1
  done
  [[ -n "${VM_IP}" ]] || die "KVM 测试虚拟机未能获取 IPv4 / incus-agent 未就绪"
  incus exec "${TEST_VM_NAME}" -- getent hosts deb.debian.org >/dev/null || die "KVM 测试虚拟机无法联网"
  cleanup_test
else
  info "4/7 KVM 验证已跳过（当前模式：LXC）"
fi

info "5/7 安装 XNAT Host Agent"
install -d -m 0755 /opt/xnat
rm -rf "${DEST_DIR}"
mkdir -p "${DEST_DIR}"
cp -a "${SRC_DIR}/." "${DEST_DIR}/"

cd "${DEST_DIR}"
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

PUBLIC_IP="$(curl -4fsS --max-time 10 https://api.ipify.org || true)"
[[ -n "${PUBLIC_IP}" ]] || die "无法获取公网 IPv4"

TOKEN="$(openssl rand -hex 32)"

mkdir -p "${DEST_DIR}/tls"

cat > /tmp/xnat-agent-openssl.cnf <<EOF
[req]
distinguished_name=dn
x509_extensions=v3
prompt=no

[dn]
CN=${PUBLIC_IP}

[v3]
subjectAltName=IP:${PUBLIC_IP}
EOF

openssl req -x509 -nodes -newkey rsa:2048 -days 3650 \
  -keyout "${DEST_DIR}/tls/agent.key" \
  -out "${DEST_DIR}/tls/agent.crt" \
  -config /tmp/xnat-agent-openssl.cnf >/dev/null 2>&1

cat > "${DEST_DIR}/.env" <<EOF
AGENT_TOKEN=${TOKEN}
HOST_PUBLIC_IP=${PUBLIC_IP}
INCUS_STORAGE_POOL=${POOL_NAME}
INCUS_BRIDGE=${BRIDGE_NAME}
INCUS_PROVISION_TIMEOUT=180
AGENT_PORT=${AGENT_PORT}
PANEL_CIDR=${PANEL_CIDR}
XNAT_NODE_CONFIG=/etc/xnat/node.json
EOF

chmod 600 "${DEST_DIR}/.env" "${DEST_DIR}/tls/agent.key"

# NAT 用户端口池不在安装阶段决定。连接 Panel 后由后台配置并同步到这里。
install -d -m 0755 /etc/xnat
python3 - /etc/xnat/node.json "${VIRTUALIZATION_MODES_JSON}" <<'PY_NODE_CONFIG'
import json, sys
from pathlib import Path
p = Path(sys.argv[1])
try:
    data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
except Exception:
    data = {}
if not isinstance(data, dict):
    data = {}
data["virtualization_modes"] = json.loads(sys.argv[2])
tmp = p.with_suffix(".tmp")
tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
tmp.chmod(0o600)
tmp.replace(p)
PY_NODE_CONFIG
chmod 600 /etc/xnat/node.json

info "6/7 配置防火墙与 systemd"
install -m 0755 "${REPO_ROOT}/scripts/xnat" /usr/local/sbin/xnat
install -m 0755 "${REPO_ROOT}/scripts/xnat-firewall" /usr/local/sbin/xnat-firewall
xnat-firewall install-host "${PANEL_CIDR}" "${AGENT_PORT}"

cat > /etc/systemd/system/xnat-host-agent.service <<EOF
[Unit]
Description=XNAT Host Agent
After=network-online.target incus.service xnat-firewall.service
Wants=network-online.target
Requires=incus.service xnat-firewall.service

[Service]
Type=simple
WorkingDirectory=${DEST_DIR}
EnvironmentFile=${DEST_DIR}/.env
ExecStart=${DEST_DIR}/.venv/bin/uvicorn natvps_agent.main:app \
  --host 0.0.0.0 \
  --port ${AGENT_PORT} \
  --ssl-keyfile ${DEST_DIR}/tls/agent.key \
  --ssl-certfile ${DEST_DIR}/tls/agent.crt
Restart=always
RestartSec=3
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now xnat-host-agent.service

info "7/7 健康检查"
rm -f /tmp/xnat-agent-health.json
for _ in $(seq 1 40); do
  if curl -kfsS "https://127.0.0.1:${AGENT_PORT}/health" \
    >/tmp/xnat-agent-health.json 2>/dev/null; then
    break
  fi
  sleep 1
done

[[ -s /tmp/xnat-agent-health.json ]] || {
  journalctl -u xnat-host-agent -n 100 --no-pager || true
  die "XNAT Host Agent 启动失败"
}

cat /tmp/xnat-agent-health.json
echo
install -d -m 0755 /etc/xnat
printf 'host\n' > /etc/xnat/component
printf '%s\n' "${COMPONENT_VERSION}" > /etc/xnat/version
printf '%s\n' "${PROJECT_RELEASE}" > /etc/xnat/release
chmod 0644 /etc/xnat/component /etc/xnat/version /etc/xnat/release

cat > "${CRED_FILE}" <<EOF
XNAT Host Agent v${COMPONENT_VERSION}
Agent URL: https://${PUBLIC_IP}:${AGENT_PORT}
Agent Token: ${TOKEN}
Public IP: ${PUBLIC_IP}
NAT Port Pool: 尚未配置，请在 Panel 后台连接节点后设置
Storage: ${POOL_NAME} / LVM Thin / ${NATPOOL_GB}GiB
Virtualization: ${VIRTUALIZATION_LABEL}
KVM device: $([[ -c /dev/kvm ]] && echo available || echo unavailable)
Bridge: ${BRIDGE_NAME} / ${BRIDGE_ADDR}
Panel allow: ${PANEL_CIDR}
Agent Firewall: TCP ${AGENT_PORT} only from ${PANEL_CIDR}
Install path: ${DEST_DIR}
EOF
chmod 600 "${CRED_FILE}"

echo
echo "XNAT Host Node v${COMPONENT_VERSION} 安装完成"
echo "Agent URL: https://${PUBLIC_IP}:${AGENT_PORT}"
echo "Agent Token: ${TOKEN}"
echo "Credentials: ${CRED_FILE}"
echo "Virtualization: ${VIRTUALIZATION_LABEL}"
echo "Firewall: TCP ${AGENT_PORT} 仅允许 ${PANEL_CIDR}"
echo "Management: xnat"
echo "Firewall status: xnat-firewall status"
echo
echo
echo "下一步："
echo "  1. 登录 XNAT Panel 后台添加此 Host Agent"
echo "  2. 连接检测成功后，在节点卡片中配置 NAT 端口池"
echo "  3. NAT 端口池保存后会自动同步到 Agent"
echo
echo "公网端口说明："
echo "  TCP ${AGENT_PORT}: XNAT 已在系统层仅允许 Panel ${PANEL_CIDR}"
echo "  NAT TCP/UDP 端口池：由 Panel 后台配置后，再按相同范围设置云厂商安全组"
echo "  TCP 22: XNAT 不自动修改，避免把管理员锁在 SSH 外"

trap - EXIT
