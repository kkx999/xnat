#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC_DIR="${REPO_ROOT}/agent"
DEST_DIR="${XNAT_AGENT_DIR:-/opt/xnat/agent}"
PROJECT_RELEASE="$(tr -d '[:space:]' < "${REPO_ROOT}/VERSION")"
COMPONENT_VERSION="$(tr -d '[:space:]' < "${SRC_DIR}/VERSION")"
AGENT_PORT="${AGENT_PORT:-29443}"

info(){ echo; echo "==== $* ===="; }
die(){ echo "[ERROR] $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "请使用 root 运行"
[[ -f "${DEST_DIR}/.env" ]] || die "未检测到 XNAT Host Agent：${DEST_DIR}/.env"
[[ -f "${SRC_DIR}/requirements.txt" ]] || die "目标 Release 缺少 Agent 源码"
[[ -f "${REPO_ROOT}/scripts/xnat" ]] || die "目标 Release 缺少 xnat 管理脚本"
[[ -f "${REPO_ROOT}/scripts/xnat-firewall" ]] || die "目标 Release 缺少 xnat-firewall"

. /etc/os-release
[[ "${ID:-}" == "debian" && "${VERSION_CODENAME:-}" == "bookworm" ]] || die "当前正式版要求 Debian 12 bookworm"

CURRENT_VERSION="$(grep -E '^__version__[[:space:]]*=' "${DEST_DIR}/natvps_agent/__init__.py" 2>/dev/null | head -n1 | cut -d'"' -f2 || true)"
CURRENT_VERSION="${CURRENT_VERSION:-unknown}"
PANEL_CIDR="$(grep -E '^PANEL_CIDR=' "${DEST_DIR}/.env" | tail -n1 | cut -d= -f2- || true)"
AGENT_PORT_ENV="$(grep -E '^AGENT_PORT=' "${DEST_DIR}/.env" | tail -n1 | cut -d= -f2- || true)"
AGENT_PORT="${AGENT_PORT_ENV:-${AGENT_PORT}}"
[[ -n "${PANEL_CIDR}" ]] || die "Agent .env 缺少 PANEL_CIDR，先执行 xnat firewall set-panel <PanelIP>"

info "XNAT Host Agent 更新"
echo "当前版本：v${CURRENT_VERSION}"
echo "目标版本：v${COMPONENT_VERSION}"
echo "Release：v${PROJECT_RELEASE}"

TS="$(date +'%Y%m%d-%H%M%S')"
BACKUP_DIR="/root/xnat-backups/agent-${CURRENT_VERSION}-to-${COMPONENT_VERSION}-${TS}"
install -d -m 0700 "${BACKUP_DIR}/code"

info "1/5 备份 Agent 配置、TLS 和代码"
cp -a "${DEST_DIR}/.env" "${BACKUP_DIR}/.env"
cp -a "${DEST_DIR}/tls" "${BACKUP_DIR}/tls"
find "${DEST_DIR}" -mindepth 1 -maxdepth 1 \
  ! -name '.env' ! -name 'tls' ! -name '.venv' \
  -exec cp -a -t "${BACKUP_DIR}/code" {} +

rollback(){
  echo "[WARN] Agent 更新失败，正在恢复更新前版本。" >&2
  systemctl stop xnat-host-agent.service >/dev/null 2>&1 || true
  find "${DEST_DIR}" -mindepth 1 -maxdepth 1 \
    ! -name '.env' ! -name 'tls' ! -name '.venv' \
    -exec rm -rf {} + || true
  cp -a "${BACKUP_DIR}/code/." "${DEST_DIR}/" || true
  cp -a "${BACKUP_DIR}/.env" "${DEST_DIR}/.env" || true
  rm -rf "${DEST_DIR}/tls" && cp -a "${BACKUP_DIR}/tls" "${DEST_DIR}/tls" || true
  systemctl restart xnat-host-agent.service >/dev/null 2>&1 || true
}
trap rollback ERR

info "2/5 更新 Agent 文件"
systemctl stop xnat-host-agent.service
find "${DEST_DIR}" -mindepth 1 -maxdepth 1 \
  ! -name '.env' ! -name 'tls' ! -name '.venv' \
  -exec rm -rf {} +
cp -a "${SRC_DIR}/." "${DEST_DIR}/"

info "3/5 更新 Python 环境"
cd "${DEST_DIR}"
[[ -x .venv/bin/python ]] || python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

info "4/5 更新管理命令、防火墙和服务"
DEBIAN_FRONTEND=noninteractive apt-get install -y nftables
install -m 0755 "${REPO_ROOT}/scripts/xnat" /usr/local/sbin/xnat
install -m 0755 "${REPO_ROOT}/scripts/xnat-firewall" /usr/local/sbin/xnat-firewall
xnat-firewall install-host "${PANEL_CIDR}" "${AGENT_PORT}"

cat > /etc/systemd/system/xnat-host-agent.service <<EOF_SERVICE
[Unit]
Description=XNAT Host Agent v${COMPONENT_VERSION}
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
EOF_SERVICE
systemctl daemon-reload
systemctl enable xnat-host-agent.service >/dev/null
systemctl restart xnat-host-agent.service

info "5/5 健康检查"
rm -f /tmp/xnat-agent-health.json
for _ in $(seq 1 45); do
  if curl -kfsS "https://127.0.0.1:${AGENT_PORT}/health" >/tmp/xnat-agent-health.json 2>/dev/null; then
    break
  fi
  sleep 1
done
[[ -s /tmp/xnat-agent-health.json ]] || {
  journalctl -u xnat-host-agent -n 100 --no-pager || true
  die "Host Agent 更新后健康检查失败"
}
cat /tmp/xnat-agent-health.json; echo

install -d -m 0755 /etc/xnat
printf 'host\n' > /etc/xnat/component
printf '%s\n' "${COMPONENT_VERSION}" > /etc/xnat/version
printf '%s\n' "${PROJECT_RELEASE}" > /etc/xnat/release
chmod 0644 /etc/xnat/component /etc/xnat/version /etc/xnat/release

trap - ERR
echo
echo "XNAT Host Agent v${COMPONENT_VERSION} 更新完成"
echo "Agent Token / TLS / Incus / natpool / VPS 均已保留"
echo "备份：${BACKUP_DIR}"
