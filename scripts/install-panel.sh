#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC_DIR="${REPO_ROOT}/panel"
PROJECT_RELEASE="$(tr -d '[:space:]' < "${REPO_ROOT}/VERSION")"
COMPONENT_VERSION="$(tr -d '[:space:]' < "${SRC_DIR}/VERSION")"
DEST_DIR="${XNAT_PANEL_DIR:-/opt/xnat/panel}"
PANEL_BIND_HOST="${PANEL_BIND_HOST:-127.0.0.1}"
PANEL_PORT="${PANEL_PORT:-8000}"
PANEL_DOMAIN="${PANEL_DOMAIN:-}"
LETSENCRYPT_EMAIL="${LETSENCRYPT_EMAIL:-}"
CONFIGURE_DOMAIN="${CONFIGURE_DOMAIN:-ask}"
CRED_FILE="/root/xnat-panel-credentials.txt"

info(){ echo; echo "==== $* ===="; }
warn(){ echo "[WARN] $*" >&2; }
die(){ echo "[ERROR] $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "请使用 root 运行"
[[ -f "${SRC_DIR}/requirements.txt" ]] || die "找不到 panel 源码，请在 xnat 仓库中运行本脚本"
[[ -f "${REPO_ROOT}/scripts/xnat" ]] || die "找不到 XNAT 管理脚本"

. /etc/os-release
[[ "${ID:-}" == "debian" && "${VERSION_CODENAME:-}" == "bookworm" ]] || die "要求 Debian 12 bookworm"

if [[ -f "${DEST_DIR}/.env" ]]; then
  die "${DEST_DIR} 已存在 .env。本脚本仅用于全新安装，请使用 scripts/upgrade-panel.sh 升级。"
fi

info "1/7 安装系统依赖"
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y \
  ca-certificates curl openssl python3 python3-venv python3-pip sqlite3 \
  nginx certbot dnsutils nftables iproute2

timedatectl set-timezone Asia/Shanghai || true

info "2/7 安装 XNAT Panel"
install -d -m 0755 /opt/xnat
rm -rf "${DEST_DIR}"
mkdir -p "${DEST_DIR}"
cp -a "${SRC_DIR}/." "${DEST_DIR}/"
mkdir -p "${DEST_DIR}/data/backups"

APP_SECRET="$(openssl rand -hex 32)"
ADMIN_PASSWORD="$(openssl rand -base64 30 | tr -d '\n' | tr '/+' 'AZ' | cut -c1-28)"

cat > "${DEST_DIR}/.env" <<EOF_ENV
APP_NAME=XNAT
APP_SECRET=${APP_SECRET}
DATABASE_URL=sqlite:///./data/panel.db
SESSION_HTTPS_ONLY=false
APP_TIMEZONE=Asia/Shanghai
PUBLIC_BASE_URL=
PANEL_BIND_HOST=${PANEL_BIND_HOST}
PANEL_PORT=${PANEL_PORT}
XNAT_DEPLOYMENT_STATE=/etc/xnat/deployment.json
ADMIN_USERNAME=admin
ADMIN_PASSWORD=${ADMIN_PASSWORD}
ADMIN_EMAIL=admin@example.com
VPS_PROVIDER=remote
SMTP_HOST=
SMTP_PORT=587
SMTP_USERNAME=
SMTP_PASSWORD=
SMTP_FROM=
SMTP_STARTTLS=true
TELEGRAM_BOT_TOKEN=
TRONGRID_API_KEY=
POLYGON_RPC_URL=https://polygon.drpc.org
BACKUP_DIR=./data/backups
EOF_ENV
chmod 600 "${DEST_DIR}/.env"

info "3/7 安装 Python 环境"
cd "${DEST_DIR}"
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

info "4/7 配置敏感端口防火墙与 systemd"
install -m 0755 "${REPO_ROOT}/scripts/xnat-firewall" /usr/local/sbin/xnat-firewall
xnat-firewall install-panel "${PANEL_PORT}"

cat > /etc/systemd/system/xnat-panel.service <<EOF_SERVICE
[Unit]
Description=XNAT Panel
After=network-online.target xnat-firewall.service
Wants=network-online.target
Requires=xnat-firewall.service

[Service]
Type=simple
WorkingDirectory=${DEST_DIR}
ExecStart=${DEST_DIR}/.venv/bin/uvicorn app.main:app --env-file ${DEST_DIR}/.env --host ${PANEL_BIND_HOST} --port ${PANEL_PORT} --proxy-headers --forwarded-allow-ips 127.0.0.1,::1
Restart=always
RestartSec=3
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF_SERVICE

cat > /etc/systemd/system/xnat-maintenance.service <<EOF_MAINT
[Unit]
Description=XNAT Maintenance
After=network-online.target

[Service]
Type=oneshot
WorkingDirectory=${DEST_DIR}
EnvironmentFile=${DEST_DIR}/.env
ExecStart=${DEST_DIR}/.venv/bin/python -m app.maintenance expire
EOF_MAINT

cat > /etc/systemd/system/xnat-maintenance.timer <<'EOF_TIMER'
[Unit]
Description=Run XNAT maintenance

[Timer]
OnBootSec=2min
OnUnitActiveSec=1min
AccuracySec=15s
Persistent=true

[Install]
WantedBy=timers.target
EOF_TIMER

systemctl daemon-reload
systemctl enable --now xnat-panel.service xnat-maintenance.timer

info "5/7 配置 Nginx 与 XNAT 管理命令"
install -m 0755 "${REPO_ROOT}/scripts/xnat" /usr/local/sbin/xnat

XNAT_PANEL_DIR="${DEST_DIR}" XNAT_PANEL_BIND="${PANEL_BIND_HOST}" XNAT_PANEL_PORT="${PANEL_PORT}" xnat init

info "6/7 Panel 健康检查"
rm -f /tmp/xnat-panel-health.json
for _ in $(seq 1 45); do
  if curl -fsS "http://${PANEL_BIND_HOST}:${PANEL_PORT}/health" >/tmp/xnat-panel-health.json 2>/dev/null; then
    break
  fi
  sleep 1
done

[[ -s /tmp/xnat-panel-health.json ]] || {
  journalctl -u xnat-panel -n 100 --no-pager || true
  die "XNAT Panel 启动失败"
}
cat /tmp/xnat-panel-health.json
echo
install -d -m 0755 /etc/xnat
printf 'panel\n' > /etc/xnat/component
printf '%s\n' "${COMPONENT_VERSION}" > /etc/xnat/version
printf '%s\n' "${PROJECT_RELEASE}" > /etc/xnat/release
chmod 0644 /etc/xnat/component /etc/xnat/version /etc/xnat/release

PUBLIC_IP="$(curl -4fsS --max-time 10 https://api.ipify.org || true)"
FINAL_URL="http://${PUBLIC_IP:-YOUR_PANEL_IP}"
DOMAIN_OK=false

info "7/7 域名与 HTTPS"
want_domain=false
case "${CONFIGURE_DOMAIN,,}" in
  1|true|yes|y) want_domain=true ;;
  0|false|no|n) want_domain=false ;;
  ask|"")
    if [[ -n "$PANEL_DOMAIN" ]]; then
      want_domain=true
    elif [[ -t 0 ]]; then
      read -r -p "是否现在配置 Panel 域名与 HTTPS？[Y/n]: " answer
      [[ ! "$answer" =~ ^[Nn]$ ]] && want_domain=true
    fi
    ;;
  *) die "CONFIGURE_DOMAIN 只能是 ask/true/false" ;;
esac

if [[ "$want_domain" == true ]]; then
  if [[ -z "$PANEL_DOMAIN" ]]; then
    if [[ -t 0 ]]; then
      read -r -p "请输入 Panel 域名（例如 panel.example.com）: " PANEL_DOMAIN
    else
      warn "非交互环境未提供 PANEL_DOMAIN，跳过域名配置。"
    fi
  fi
  if [[ -n "$PANEL_DOMAIN" ]]; then
    if XNAT_PANEL_DIR="${DEST_DIR}" XNAT_PANEL_BIND="${PANEL_BIND_HOST}" XNAT_PANEL_PORT="${PANEL_PORT}" \
      LETSENCRYPT_EMAIL="${LETSENCRYPT_EMAIL}" xnat domain set "$PANEL_DOMAIN" "$LETSENCRYPT_EMAIL"; then
      FINAL_URL="https://${PANEL_DOMAIN,,}"
      DOMAIN_OK=true
    else
      warn "域名 / HTTPS 配置失败，但 XNAT Panel 已正常安装。可以稍后执行：xnat domain"
    fi
  fi
else
  echo "已跳过域名配置。以后执行 xnat domain 即可设置。"
fi

cat > "${CRED_FILE}" <<EOF_CRED
XNAT Panel v${COMPONENT_VERSION}
Panel: ${FINAL_URL}
Origin: ${PANEL_BIND_HOST}:${PANEL_PORT}
Admin: admin
Password: ${ADMIN_PASSWORD}
Install path: ${DEST_DIR}
Domain managed: ${DOMAIN_OK}
EOF_CRED
chmod 600 "${CRED_FILE}"

echo
echo "========================================"
echo "       XNAT Panel v${COMPONENT_VERSION} 安装完成"
echo "========================================"
echo "URL: ${FINAL_URL}"
echo "Origin: ${PANEL_BIND_HOST}:${PANEL_PORT}（仅本机）"
echo "Admin: admin"
echo "Password: ${ADMIN_PASSWORD}"
echo "Credentials: ${CRED_FILE}"
echo "Management: xnat"
echo "Firewall: xnat-firewall status"
echo
if [[ "$DOMAIN_OK" == true ]]; then
  echo "请确保公网开放 TCP 80/443。"
  echo "如使用 Cloudflare 橙色云，SSL/TLS 建议使用 Full (strict)。"
else
  echo "当前通过 Nginx HTTP :80 访问，公网无需开放 ${PANEL_PORT}。"
  echo "配置域名：xnat domain"
fi
echo
