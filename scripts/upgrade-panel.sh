#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC_DIR="${REPO_ROOT}/panel"
TARGET_DIR="${XNAT_PANEL_DIR:-/opt/xnat/panel}"
PROJECT_RELEASE="$(tr -d '[:space:]' < "${REPO_ROOT}/VERSION")"
COMPONENT_VERSION="$(tr -d '[:space:]' < "${SRC_DIR}/VERSION")"

info(){ echo; echo "==== $* ===="; }
warn(){ echo "[WARN] $*" >&2; }
die(){ echo "[ERROR] $*" >&2; exit 1; }

env_file_value(){
  local file="$1" key="$2" value=""
  [[ -f "$file" ]] || return 0
  value="$(sed -n "s/^${key}=//p" "$file" | tail -n1 | tr -d '\r')"
  case "$value" in
    \"*\") value="${value#\"}"; value="${value%\"}" ;;
    \'*\') value="${value#\'}"; value="${value%\'}" ;;
  esac
  printf '%s' "$value"
}

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

case "$CURRENT_VERSION" in
  1.0.2) UPGRADE_PATH="verified-v1.0.2" ;;
  1.0.*) UPGRADE_PATH="compatible-v1.0.x"; warn "当前为 v${CURRENT_VERSION}；v1.1.0 的正式升级验收基线是 v1.0.2。建议先升级到 v1.0.2。" ;;
  "$COMPONENT_VERSION") UPGRADE_PATH="reapply" ;;
  *) die "不支持从 v${CURRENT_VERSION} 直接使用此脚本升级到 v${COMPONENT_VERSION}" ;;
esac

OLD_PANEL_BIND_HOST="$(env_file_value "${TARGET_DIR}/.env" PANEL_BIND_HOST)"
OLD_PANEL_PORT="$(env_file_value "${TARGET_DIR}/.env" PANEL_PORT)"
OLD_PANEL_BIND_HOST="${OLD_PANEL_BIND_HOST:-127.0.0.1}"
OLD_PANEL_PORT="${OLD_PANEL_PORT:-8000}"
PANEL_BIND_HOST="${PANEL_BIND_HOST:-${OLD_PANEL_BIND_HOST}}"
PANEL_PORT="${PANEL_PORT:-${OLD_PANEL_PORT}}"
DATABASE_URL_VALUE="$(env_file_value "${TARGET_DIR}/.env" DATABASE_URL)"
DATABASE_URL_VALUE="${DATABASE_URL_VALUE:-sqlite:///./data/panel.db}"

[[ "$PANEL_PORT" =~ ^[0-9]+$ ]] || die "PANEL_PORT 必须是数字"
(( PANEL_PORT >= 1 && PANEL_PORT <= 65535 )) || die "PANEL_PORT 必须在 1-65535 之间"

info "XNAT Panel 更新"
echo "当前版本：v${CURRENT_VERSION}"
echo "目标版本：v${COMPONENT_VERSION}"
echo "Release：v${PROJECT_RELEASE}"
echo "升级路径：${UPGRADE_PATH}"
echo "保留监听：${PANEL_BIND_HOST}:${PANEL_PORT}"

info "1/8 升级前检查"
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y \
  ca-certificates curl openssl python3 python3-venv python3-pip sqlite3 nginx certbot dnsutils nftables iproute2

QUICK_CHECK="$(sqlite3 "${TARGET_DIR}/data/panel.db" 'PRAGMA quick_check;' | head -n1 || true)"
[[ "$QUICK_CHECK" == "ok" ]] || die "升级前 SQLite quick_check 失败：${QUICK_CHECK:-unknown}"

TS="$(date +'%Y%m%d-%H%M%S')"
BACKUP_DIR="/root/xnat-backups/panel-${CURRENT_VERSION}-to-${COMPONENT_VERSION}-${TS}"
install -d -m 0700 "${BACKUP_DIR}/code" "${BACKUP_DIR}/systemd" "${BACKUP_DIR}/bin"

PANEL_WAS_ACTIVE="$(systemctl is-active xnat-panel.service 2>/dev/null || true)"
MAINT_TIMER_WAS_ENABLED="$(systemctl is-enabled xnat-maintenance.timer 2>/dev/null || true)"

info "2/8 备份数据库、配置、代码与服务"
sqlite3 "${TARGET_DIR}/data/panel.db" ".backup '${BACKUP_DIR}/panel.db'"
cp -a "${TARGET_DIR}/.env" "${BACKUP_DIR}/.env"
find "${TARGET_DIR}" -mindepth 1 -maxdepth 1 \
  ! -name '.env' ! -name 'data' ! -name '.venv' \
  -exec cp -a -t "${BACKUP_DIR}/code" {} +
for unit in xnat-panel.service xnat-maintenance.service xnat-maintenance.timer; do
  [[ -f "/etc/systemd/system/${unit}" ]] && cp -a "/etc/systemd/system/${unit}" "${BACKUP_DIR}/systemd/${unit}"
done
[[ -f /usr/local/sbin/xnat ]] && cp -a /usr/local/sbin/xnat "${BACKUP_DIR}/bin/xnat"
[[ -f /usr/local/sbin/xnat-firewall ]] && cp -a /usr/local/sbin/xnat-firewall "${BACKUP_DIR}/bin/xnat-firewall"
[[ -d /etc/xnat ]] && cp -a /etc/xnat "${BACKUP_DIR}/etc-xnat"
chmod 600 "${BACKUP_DIR}/panel.db" "${BACKUP_DIR}/.env"

rollback(){
  local rc=$?
  trap - ERR
  echo "[WARN] 更新失败，正在恢复 v${CURRENT_VERSION} 代码和数据。" >&2
  systemctl stop xnat-panel.service >/dev/null 2>&1 || true

  find "${TARGET_DIR}" -mindepth 1 -maxdepth 1 \
    ! -name '.env' ! -name 'data' ! -name '.venv' \
    -exec rm -rf {} + || true
  cp -a "${BACKUP_DIR}/code/." "${TARGET_DIR}/" || true
  cp -a "${BACKUP_DIR}/.env" "${TARGET_DIR}/.env" || true
  cp -a "${BACKUP_DIR}/panel.db" "${TARGET_DIR}/data/panel.db" || true

  for unit in xnat-panel.service xnat-maintenance.service xnat-maintenance.timer; do
    if [[ -f "${BACKUP_DIR}/systemd/${unit}" ]]; then
      cp -a "${BACKUP_DIR}/systemd/${unit}" "/etc/systemd/system/${unit}" || true
    fi
  done
  [[ -f "${BACKUP_DIR}/bin/xnat" ]] && cp -a "${BACKUP_DIR}/bin/xnat" /usr/local/sbin/xnat || true
  [[ -f "${BACKUP_DIR}/bin/xnat-firewall" ]] && cp -a "${BACKUP_DIR}/bin/xnat-firewall" /usr/local/sbin/xnat-firewall || true
  if [[ -d "${BACKUP_DIR}/etc-xnat" ]]; then
    rm -rf /etc/xnat
    cp -a "${BACKUP_DIR}/etc-xnat" /etc/xnat || true
  fi

  systemctl daemon-reload >/dev/null 2>&1 || true
  if [[ -x /usr/local/sbin/xnat-firewall ]]; then
    /usr/local/sbin/xnat-firewall install-panel "${OLD_PANEL_PORT}" >/dev/null 2>&1 || true
  fi
  if [[ "$PANEL_WAS_ACTIVE" == "active" ]]; then
    systemctl restart xnat-panel.service >/dev/null 2>&1 || true
  fi
  if [[ "$MAINT_TIMER_WAS_ENABLED" == "enabled" ]]; then
    systemctl enable --now xnat-maintenance.timer >/dev/null 2>&1 || true
  fi
  echo "[WARN] 已尝试回滚。升级前备份：${BACKUP_DIR}" >&2
  exit "$rc"
}
trap rollback ERR

info "3/8 更新 Panel 文件"
systemctl stop xnat-panel.service
find "${TARGET_DIR}" -mindepth 1 -maxdepth 1 \
  ! -name '.env' ! -name 'data' ! -name '.venv' \
  -exec rm -rf {} +
cp -a "${SRC_DIR}/." "${TARGET_DIR}/"
chmod 600 "${TARGET_DIR}/.env"

info "4/8 更新 Python 环境"
cd "${TARGET_DIR}"
[[ -x .venv/bin/python ]] || python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

info "5/8 执行 v1.1.0 数据库兼容迁移"
DATABASE_URL="${DATABASE_URL_VALUE}" .venv/bin/python - <<'PY'
from app.schema import ensure_schema_extensions
changed = ensure_schema_extensions()
print("schema migration:", ", ".join(changed) if changed else "no missing columns")
PY

for spec in \
  'users:announcement_seen_key' \
  'host_nodes:maintenance_mode' \
  'host_nodes:maintenance_reason' \
  'host_nodes:schedule_cpu_max_percent' \
  'host_nodes:schedule_memory_max_percent' \
  'host_nodes:schedule_storage_max_percent' \
  'servers:traffic_cycle_mode' \
  'servers:traffic_cycle_day' \
  'servers:expiry_suspended_at' \
  'servers:expiry_delete_queued_at'; do
  table="${spec%%:*}"; column="${spec#*:}"
  sqlite3 "${TARGET_DIR}/data/panel.db" "PRAGMA table_info('${table}');" | cut -d'|' -f2 | grep -Fxq "$column" \
    || die "数据库迁移缺少字段：${table}.${column}"
done

info "6/8 更新管理命令与 systemd"
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

cat > /etc/systemd/system/xnat-maintenance.service <<EOF_MAINT
[Unit]
Description=XNAT Maintenance
After=network-online.target

[Service]
Type=oneshot
WorkingDirectory=${TARGET_DIR}
EnvironmentFile=${TARGET_DIR}/.env
ExecStart=${TARGET_DIR}/.venv/bin/python -m app.maintenance expire
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
systemctl enable xnat-panel.service xnat-maintenance.timer >/dev/null
systemctl restart xnat-panel.service
systemctl restart xnat-maintenance.timer

info "7/8 Panel 健康与版本检查"
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
HEALTH_VERSION="$(python3 - <<'PY'
import json
try:
    with open('/tmp/xnat-panel-health.json', 'r', encoding='utf-8') as f:
        print(json.load(f).get('version', ''))
except Exception:
    print('')
PY
)"
[[ "$HEALTH_VERSION" == "$COMPONENT_VERSION" ]] || die "Panel 健康检查版本不匹配：${HEALTH_VERSION:-unknown}"

info "8/8 升级后完整性检查"
QUICK_CHECK="$(sqlite3 "${TARGET_DIR}/data/panel.db" 'PRAGMA quick_check;' | head -n1 || true)"
[[ "$QUICK_CHECK" == "ok" ]] || die "升级后 SQLite quick_check 失败：${QUICK_CHECK:-unknown}"
for table in announcements announcement_reads; do
  sqlite3 "${TARGET_DIR}/data/panel.db" "SELECT name FROM sqlite_master WHERE type='table' AND name='${table}';" | grep -Fxq "$table" \
    || die "数据库迁移缺少表：${table}"
done

install -d -m 0755 /etc/xnat
printf 'panel\n' > /etc/xnat/component
printf '%s\n' "${COMPONENT_VERSION}" > /etc/xnat/version
printf '%s\n' "${PROJECT_RELEASE}" > /etc/xnat/release
chmod 0644 /etc/xnat/component /etc/xnat/version /etc/xnat/release

trap - ERR
echo
echo "========================================"
echo " XNAT Panel v${COMPONENT_VERSION} 更新完成"
echo "========================================"
echo "来源版本：v${CURRENT_VERSION}"
echo "数据库：已自动迁移并通过 quick_check"
echo "Panel：${PANEL_BIND_HOST}:${PANEL_PORT}"
echo "Host Agent：无需因本次 Panel 升级重装"
echo "升级前备份：${BACKUP_DIR}"
