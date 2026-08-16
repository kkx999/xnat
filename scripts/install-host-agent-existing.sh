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

install -d -m 0755 /etc/xnat
if [[ ! -f /etc/xnat/node.json ]]; then
  printf '%s\n' '{}' > /etc/xnat/node.json
  chmod 600 /etc/xnat/node.json
fi

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
Bridge: ${BRIDGE_NAME}
Panel allow: ${PANEL_CIDR}
Agent Firewall: TCP ${AGENT_PORT} only from ${PANEL_CIDR}
Install path: ${DEST_DIR}
EOF
chmod 600 "${CRED_FILE}"

echo "Agent URL: https://${PUBLIC_IP}:${AGENT_PORT}"
echo "Agent Token: ${TOKEN}"
echo "Credentials: ${CRED_FILE}"

echo "Firewall: TCP ${AGENT_PORT} 仅允许 ${PANEL_CIDR}"
echo "Firewall status: xnat-firewall status"

install -d -m 0755 /etc/xnat
printf 'host\n' > /etc/xnat/component
printf '%s\n' "${COMPONENT_VERSION}" > /etc/xnat/version
printf '%s\n' "${PROJECT_RELEASE}" > /etc/xnat/release
chmod 0644 /etc/xnat/component /etc/xnat/version /etc/xnat/release
echo "Management: xnat"
