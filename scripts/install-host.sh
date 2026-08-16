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

ZABBLY_FPR="4EFC590696CB15B87C73A3AD82CC8797C838DCFD"
TEST_NAME="xnat-install-test"
CRED_FILE="/root/xnat-host-agent-credentials.txt"

info(){ echo; echo "==== $* ===="; }
die(){ echo "[ERROR] $*" >&2; exit 1; }

cleanup_test(){
  command -v incus >/dev/null 2>&1 &&
    incus delete "${TEST_NAME}" --force >/dev/null 2>&1 || true
}
trap cleanup_test EXIT

[[ $EUID -eq 0 ]] || die "请使用 root 运行"
[[ -f "${SRC_DIR}/requirements.txt" ]] || die "找不到 agent 源码，请在 xnat 仓库中运行本脚本"
[[ -f "${REPO_ROOT}/scripts/xnat-firewall" ]] || die "找不到 XNAT 防火墙脚本"
[[ -f "${REPO_ROOT}/scripts/xnat" ]] || die "找不到 XNAT 管理脚本"

. /etc/os-release
[[ "${ID:-}" == "debian" && "${VERSION_CODENAME:-}" == "bookworm" ]] || \
  die "要求 Debian 12 bookworm"

# 全新 Debian 可能没有 python3。先安装基础校验依赖，再验证 PANEL_IP。
info "0/6 安装基础校验依赖"
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates python3

if [[ -z "${PANEL_CIDR}" ]]; then
  if [[ -t 0 ]]; then
    echo
    echo "========================================"
    echo "       XNAT Host 安装 · 1/2"
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

TOTAL_GB="$(df -BG --output=size / | tail -n1 | tr -dc '0-9')"
FREE_GB="$(df -BG --output=avail / | tail -n1 | tr -dc '0-9')"
[[ "${TOTAL_GB}" =~ ^[0-9]+$ && "${FREE_GB}" =~ ^[0-9]+$ ]] || die "无法读取磁盘空间"

# 推荐保留空间：至少 8GiB；较大磁盘则保留约总容量 15%。
RESERVE_GB=$(( (TOTAL_GB * 15 + 99) / 100 ))
(( RESERVE_GB < 8 )) && RESERVE_GB=8
RECOMMENDED_GB=$((FREE_GB - RESERVE_GB))
(( RECOMMENDED_GB > FREE_GB - 8 )) && RECOMMENDED_GB=$((FREE_GB - 8))
(( RECOMMENDED_GB >= 8 )) || die "可用磁盘太少：需要至少约 16GiB 可用空间"

if [[ -z "${NATPOOL_GB}" ]]; then
  if [[ -t 0 ]]; then
    echo
    echo "========================================"
    echo "       XNAT Host 安装 · 2/2"
    echo "========================================"
    echo
    echo "natpool 是 LVM Thin 存储池，专门用于存放用户 NAT VPS 的系统盘。"
    echo
    echo "当前根分区总容量：约 ${TOTAL_GB} GB"
    echo "当前可用空间：    约 ${FREE_GB} GB"
    echo "建议给系统保留：  至少 ${RESERVE_GB} GB"
    echo "推荐 natpool：     ${RECOMMENDED_GB} GiB"
    echo
    echo "例如输入 24，表示计划给所有用户 VPS 磁盘划出约 24 GiB 的 Thin Pool。"
    echo "后续 VPS 的 2GB / 4GB / 8GB 等磁盘套餐都从这个池中分配。"
    echo
    read -r -p "请输入 natpool 大小 [${RECOMMENDED_GB}]: " NATPOOL_GB
    NATPOOL_GB="${NATPOOL_GB:-${RECOMMENDED_GB}}"
  else
    NATPOOL_GB="${RECOMMENDED_GB}"
  fi
fi

[[ "${NATPOOL_GB}" =~ ^[0-9]+$ ]] || die "NATPOOL_GB 必须是整数 GiB"
MAX_SAFE=$((FREE_GB - 8))
(( NATPOOL_GB > MAX_SAFE )) && die "natpool=${NATPOOL_GB}GiB 过大；当前最多建议 ${MAX_SAFE}GiB，并至少为系统保留 8GiB"
(( NATPOOL_GB >= 8 )) || die "natpool 至少需要 8GiB"


if command -v incus >/dev/null 2>&1; then
  [[ -z "$(incus storage list --format csv -c n 2>/dev/null || true)" ]] ||
    die "检测到已有 Incus Storage。本脚本仅用于全新 Host。"
  [[ -z "$(incus list --format csv -c n 2>/dev/null || true)" ]] ||
    die "检测到已有 Incus VPS。本脚本拒绝覆盖。"
fi

info "1/6 安装 Debian / Incus 依赖"
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
Suites: bookworm
Components: main
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/zabbly-incus.asc
EOF

apt-get update
apt-get install -y incus
systemctl enable --now incus
sleep 2

info "2/6 创建 LVM Thin 与 NAT Bridge"
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

info "3/6 强制验证 2GiB 实例磁盘"
cleanup_test

incus launch images:debian/12 "${TEST_NAME}" \
  --storage "${POOL_NAME}" \
  --config limits.cpu=1 \
  --config limits.memory=128MiB \
  --device root,size=2GiB

IP=""
for _ in $(seq 1 45); do
  IP="$(
    incus exec "${TEST_NAME}" -- sh -lc \
      "ip -4 -o addr show dev eth0 scope global | awk '{print \$4}' | cut -d/ -f1 | head -n1" \
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

(( BYTES >= 1500*1024*1024 && BYTES <= 2300*1024*1024 )) ||
  die "2GiB 磁盘配额验证失败"

incus exec "${TEST_NAME}" -- getent hosts deb.debian.org >/dev/null ||
  die "测试容器无法联网"

cleanup_test

info "4/6 安装 XNAT Host Agent"
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
if [[ ! -f /etc/xnat/node.json ]]; then
  printf '%s\n' '{}' > /etc/xnat/node.json
  chmod 600 /etc/xnat/node.json
fi

info "5/6 配置防火墙与 systemd"
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

info "6/6 健康检查"
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
