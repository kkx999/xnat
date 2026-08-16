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
PANEL_CIDR="${PANEL_CIDR:-${PANEL_IP:-}}"

CRED_FILE="/root/xnat-host-agent-credentials.txt"

VIRTUALIZATION_MODE="${VIRTUALIZATION_MODE:-}"
VIRTUALIZATION_MODES_JSON='["lxc"]'
VIRTUALIZATION_LABEL="LXC"

detect_kvm(){
  [[ -c /dev/kvm && -r /dev/kvm && -w /dev/kvm ]]
}

select_virtualization_mode(){
  local kvm_ok="false" choice mode
  detect_kvm && kvm_ok="true"
  mode="${VIRTUALIZATION_MODE,,}"
  case "$mode" in
    1|lxc) mode="lxc" ;;
    2|kvm) mode="kvm" ;;
    3|hybrid|both|lxc+kvm|kvm+lxc) mode="hybrid" ;;
    "") ;;
    *) die "VIRTUALIZATION_MODE 仅支持 lxc / kvm / hybrid" ;;
  esac
  if [[ -z "$mode" && -t 0 ]]; then
    echo
    echo "========================================"
    echo "       XNAT Host Agent · 虚拟化模式"
    echo "========================================"
    echo
    echo "虚拟化能力检测："
    echo "  LXC：✓ 可用"
    if [[ "$kvm_ok" == "true" ]]; then
      echo "  KVM：✓ 可用（/dev/kvm 已开放）"
      echo
      echo "  1. LXC"
      echo "  2. KVM"
      echo "  3. LXC + KVM（推荐）"
      read -r -p "请选择 [1-3] [3]: " choice
      choice="${choice:-3}"
      case "$choice" in 1) mode="lxc";; 2) mode="kvm";; 3) mode="hybrid";; *) die "无效选择：${choice}";; esac
    else
      echo "  KVM：✗ 不可用（未检测到可访问的 /dev/kvm）"
      echo "当前只能使用 LXC；如需 KVM，请先开启 Nested Virtualization。"
      read -r -p "按 Enter 继续使用 LXC..." _
      mode="lxc"
    fi
  fi
  mode="${mode:-lxc}"
  if [[ "$mode" != "lxc" && "$kvm_ok" != "true" ]]; then
    die "选择了 KVM，但 /dev/kvm 不可用。"
  fi
  VIRTUALIZATION_MODE="$mode"
  case "$mode" in
    lxc) VIRTUALIZATION_MODES_JSON='["lxc"]'; VIRTUALIZATION_LABEL="LXC" ;;
    kvm) VIRTUALIZATION_MODES_JSON='["kvm"]'; VIRTUALIZATION_LABEL="KVM" ;;
    hybrid) VIRTUALIZATION_MODES_JSON='["lxc","kvm"]'; VIRTUALIZATION_LABEL="LXC + KVM" ;;
  esac
}

write_virtualization_config(){
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
}


info(){ echo; echo "==== $* ===="; }
die(){ echo "[ERROR] $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "请使用 root 运行"
[[ -f "${SRC_DIR}/requirements.txt" ]] || die "找不到 agent 源码，请在 xnat 仓库中运行本脚本"
command -v incus >/dev/null 2>&1 || die "没有检测到 Incus"
[[ -f "${REPO_ROOT}/scripts/xnat-firewall" ]] || die "找不到 XNAT 防火墙脚本"
[[ -f "${REPO_ROOT}/scripts/xnat" ]] || die "找不到 XNAT 管理脚本"

# 某些精简 Debian 环境没有 python3，先补齐 IP 校验依赖。
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates python3

if [[ -z "${PANEL_CIDR}" ]]; then
  if [[ -t 0 ]]; then
    echo
    echo "请输入 XNAT Panel Server 的【真实公网 IPv4】。"
    echo "用于限制 Host Agent ${AGENT_PORT}/TCP，只允许 Panel 访问。"
    echo "不要填写 Panel 域名、Cloudflare IP 或当前 Host 自己的 IP。"
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

incus storage show "${POOL_NAME}" >/dev/null 2>&1 ||
  die "找不到 Incus storage pool: ${POOL_NAME}"

incus network show "${BRIDGE_NAME}" >/dev/null 2>&1 ||
  die "找不到 Incus network: ${BRIDGE_NAME}"

. /etc/os-release
[[ "${ID:-}" == "debian" && "${VERSION_CODENAME:-}" == "bookworm" ]] ||
  die "要求 Debian 12 bookworm"

info "1/4 安装 Agent 依赖"
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y \
  ca-certificates curl openssl python3 python3-venv python3-pip lvm2 nftables

info "2/4 安装 XNAT Host Agent"
install -d -m 0755 /opt/xnat
rm -rf "${DEST_DIR}"
mkdir -p "${DEST_DIR}"
cp -a "${SRC_DIR}/." "${DEST_DIR}/"

cd "${DEST_DIR}"
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

PUBLIC_IP="${HOST_PUBLIC_IP:-$(curl -4fsS --max-time 10 https://api.ipify.org || true)}"
[[ -n "${PUBLIC_IP}" ]] || die "无法获取公网 IPv4，请用 HOST_PUBLIC_IP=... 重试"

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

write_virtualization_config

info "3/4 配置防火墙与 systemd"
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
ExecStart=${DEST_DIR}/.venv/bin/uvicorn natvps_agent.main:app --host 0.0.0.0 --port ${AGENT_PORT} --ssl-keyfile ${DEST_DIR}/tls/agent.key --ssl-certfile ${DEST_DIR}/tls/agent.crt
Restart=always
RestartSec=3
Environment=PYTHONUNBUFFERED=1
[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now xnat-host-agent.service

info "4/4 健康检查"
for _ in $(seq 1 40); do
  curl -kfsS "https://127.0.0.1:${AGENT_PORT}/health" >/tmp/xnat-agent-health.json 2>/dev/null && break
  sleep 1
done

[[ -s /tmp/xnat-agent-health.json ]] || {
  journalctl -u xnat-host-agent -n 100 --no-pager || true
  die "Agent 启动失败"
}

cat /tmp/xnat-agent-health.json
echo

cat > "${CRED_FILE}" <<EOF
XNAT Host Agent v${COMPONENT_VERSION}
Agent URL: https://${PUBLIC_IP}:${AGENT_PORT}
Agent Token: ${TOKEN}
Public IP: ${PUBLIC_IP}
NAT Port Pool: 尚未配置，请在 Panel 后台设置
Storage: ${POOL_NAME}
Virtualization: ${VIRTUALIZATION_LABEL}
KVM device: $([[ -c /dev/kvm ]] && echo available || echo unavailable)
Bridge: ${BRIDGE_NAME}
Panel allow: ${PANEL_CIDR}
Agent Firewall: TCP ${AGENT_PORT} only from ${PANEL_CIDR}
Install path: ${DEST_DIR}
EOF
chmod 600 "${CRED_FILE}"

echo "Agent URL: https://${PUBLIC_IP}:${AGENT_PORT}"
echo "Agent Token: ${TOKEN}"
echo "Credentials: ${CRED_FILE}"
echo "Virtualization: ${VIRTUALIZATION_LABEL}"

echo "Firewall: TCP ${AGENT_PORT} 仅允许 ${PANEL_CIDR}"
echo "Firewall status: xnat-firewall status"

install -d -m 0755 /etc/xnat
printf 'host\n' > /etc/xnat/component
printf '%s\n' "${COMPONENT_VERSION}" > /etc/xnat/version
printf '%s\n' "${PROJECT_RELEASE}" > /etc/xnat/release
chmod 0644 /etc/xnat/component /etc/xnat/version /etc/xnat/release
echo "Management: xnat"
