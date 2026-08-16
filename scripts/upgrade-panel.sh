#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC_DIR="${REPO_ROOT}/panel"
TARGET_DIR="${XNAT_PANEL_DIR:-/opt/xnat/panel}"
PROJECT_RELEASE="$(tr -d '[:space:]' < "${REPO_ROOT}/VERSION")"
COMPONENT_VERSION="$(tr -d '[:space:]' < "${SRC_DIR}/VERSION")"
PANEL_BIND_HOST="${PANEL_BIND_HOST:-127.0.0.1}"
PANEL_PORT="${PANEL_PORT:-8000}"

info(){ echo; echo "==== $* ===="; }
die(){ echo "[ERROR] $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "请使用 root 运行"
[[ -f "${TARGET_DIR}/.env" ]] || die "未检测到 XNAT Panel：${TARGET_DIR}/.env"
[[ -f "${TARGET_DIR}/data/panel.db" ]] || die "未检测到 XNAT 数据库：${TARGET_DIR}/data/panel.db"
[[ -f "${SRC_DIR}/requirements.txt" ]] || die "目标 Release 缺少 Panel 源码"
[[ -f "${REPO_ROOT}/scripts/xnat" ]] || die "目标 Release 缺少 xnat 管理脚本"
[[ -f "${REPO_ROOT}/scripts/xnat-firewall" ]] || die "目标 Release 缺少 xnat-firewall"

. /etc/os-release
[[ "${ID:-}" == "debian" && "${VERSION_CODENAME:-}" == "bookworm" ]] || die "当前正式版要求 Debian 12 bookworm"

CURRENT_VERSION="$(grep -E '^__version__[[:space:]]*=' "${TARGET_DIR}/app/__init__.py" 2>/dev/null | head -n1 | cut -d'"' -f2 || true)"
CURRENT_VERSION="${CURRENT_VERSION:-unknown}"

info "XNAT Panel 更新"
echo "当前版本：v${CURRENT_VERSION}"
echo "目标版本：v${COMPONENT_VERSION}"
echo "Release：v${PROJECT_RELEASE}"

info "1/6 安装更新依赖"
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y \
  ca-certificates curl openssl python3 python3-venv python3-pip sqlite3 nginx certbot dnsutils nftables iproute2

TS="$(date +'%Y%m%d-%H%M%S')"
BACKUP_DIR="/root/xnat-backups/panel-${CURRENT_VERSION}-to-${COMPONENT_VERSION}-${TS}"
install -d -m 0700 "${BACKUP_DIR}/code"

info "2/6 备份数据库、配置和当前代码"
sqlite3 "${TARGET_DIR}/data/panel.db" ".backup '${BACKUP_DIR}/panel.db'"
cp -a "${TARGET_DIR}/.env" "${BACKUP_DIR}/.env"
find "${TARGET_DIR}" -mindepth 1 -maxdepth 1 \
  ! -name '.env' ! -name 'data' ! -name '.venv' \
  -exec cp -a -t "${BACKUP_DIR}/code" {} +
chmod 600 "${BACKUP_DIR}/panel.db" "${BACKUP_DIR}/.env"

rollback(){
  echo "[WARN] 更新失败，正在恢复更新前代码和数据。" >&2
  systemctl stop xnat-panel.service >/dev/null 2>&1 || true
  find "${TARGET_DIR}" -mindepth 1 -maxdepth 1 \
    ! -name '.env' ! -name 'data' ! -name '.venv' \
    -exec rm -rf {} + || true
  cp -a "${BACKUP_DIR}/code/." "${TARGET_DIR}/" || true
  cp -a "${BACKUP_DIR}/.env" "${TARGET_DIR}/.env" || true
  cp -a "${BACKUP_DIR}/panel.db" "${TARGET_DIR}/data/panel.db" || true
  systemctl restart xnat-panel.service >/dev/null 2>&1 || true
}
trap rollback ERR

info "3/6 更新 Panel 文件"
systemctl stop xnat-panel.service
find "${TARGET_DIR}" -mindepth 1 -maxdepth 1 \
  ! -name '.env' ! -name 'data' ! -name '.venv' \
  -exec rm -rf {} +
cp -a "${SRC_DIR}/." "${TARGET_DIR}/"
chmod 600 "${TARGET_DIR}/.env"

info "4/6 更新 Python 环境"
cd "${TARGET_DIR}"
[[ -x .venv/bin/python ]] || python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

info "5/6 更新管理命令与服务"
install -m 0755 "${REPO_ROOT}/scripts/xnat" /usr/local/sbin/xnat
install -m 0755 "${REPO_ROOT}/scripts/xnat-firewall" /usr/local/sbin/xnat-firewall
xnat-firewall install-panel "${PANEL_PORT}"

cat > /etc/systemd/system/xnat-panel.service <<EOF_SERVICE
[Unit]
Description=XNAT Panel v${COMPONENT_VERSION}
After=network-online.target xnat-firewall.service
Wants=network-online.target
Requires=xnat-firewall.service

[Service]
Type=simple
WorkingDirectory=${TARGET_DIR}
ExecStart=${TARGET_DIR}/.venv/bin/uvicorn app.main:app --env-file ${TARGET_DIR}/.env --host ${PANEL_BIND_HOST} --port ${PANEL_PORT} --proxy-headers --forwarded-allow-ips 127.0.0.1,::1
Restart=always
RestartSec=3
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF_SERVICE

systemctl daemon-reload
systemctl enable xnat-panel.service >/dev/null
systemctl restart xnat-panel.service

info "6/6 健康检查"
rm -f /tmp/xnat-panel-health.json
for _ in $(seq 1 45); do
  if curl -fsS "http://${PANEL_BIND_HOST}:${PANEL_PORT}/health" >/tmp/xnat-panel-health.json 2>/dev/null; then
    break
  fi
  sleep 1
done
[[ -s /tmp/xnat-panel-health.json ]] || {
  journalctl -u xnat-panel -n 100 --no-pager || true
  die "Panel 更新后健康检查失败"
}
cat /tmp/xnat-panel-health.json; echo

install -d -m 0755 /etc/xnat
printf 'panel\n' > /etc/xnat/component
printf '%s\n' "${COMPONENT_VERSION}" > /etc/xnat/version
printf '%s\n' "${PROJECT_RELEASE}" > /etc/xnat/release
chmod 0644 /etc/xnat/component /etc/xnat/version /etc/xnat/release

trap - ERR
echo
echo "XNAT Panel v${COMPONENT_VERSION} 更新完成"
echo "备份：${BACKUP_DIR}"
