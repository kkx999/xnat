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

detect_kvm(){ [[ -c /dev/kvm && -r /dev/kvm && -w /dev/kvm ]]; }
ensure_virtualization_config(){
  install -d -m 0755 /etc/xnat
  if python3 - /etc/xnat/node.json <<'PY_CHECK'
import json, sys
from pathlib import Path
p=Path(sys.argv[1])
try: data=json.loads(p.read_text())
except Exception: raise SystemExit(1)
raise SystemExit(0 if isinstance(data,dict) and data.get("virtualization_modes") else 1)
PY_CHECK
  then
    return
  fi
  local mode="${VIRTUALIZATION_MODE:-}" choice kvm_ok="false"
  detect_kvm && kvm_ok="true"
  mode="${mode,,}"
  if [[ -z "$mode" && -t 0 ]]; then
    echo
    echo "首次升级到 Agent v1.1.0：请选择 Host 虚拟化模式。"
    echo "  LXC：✓ 可用"
    if [[ "$kvm_ok" == "true" ]]; then
      echo "  KVM：✓ 可用"
      echo "  1. LXC  2. KVM  3. LXC + KVM"
      read -r -p "请选择 [1-3] [1]: " choice
      choice="${choice:-1}"
      case "$choice" in 1) mode=lxc;; 2) mode=kvm;; 3) mode=hybrid;; *) die "无效选择";; esac
    else
      echo "  KVM：✗ 不可用；保持 LXC。"
      mode=lxc
    fi
  fi
  mode="${mode:-lxc}"
  if [[ "$mode" != lxc && "$kvm_ok" != true ]]; then die "KVM 模式要求可访问的 /dev/kvm"; fi
  case "$mode" in lxc) modes='["lxc"]';; kvm) modes='["kvm"]';; hybrid|both|lxc+kvm) modes='["lxc","kvm"]';; *) die "VIRTUALIZATION_MODE 无效";; esac
  python3 - /etc/xnat/node.json "$modes" <<'PY_WRITE'
import json,sys
from pathlib import Path
p=Path(sys.argv[1])
try: data=json.loads(p.read_text()) if p.exists() else {}
except Exception: data={}
if not isinstance(data,dict): data={}
data['virtualization_modes']=json.loads(sys.argv[2])
tmp=p.with_suffix('.tmp'); tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n'); tmp.chmod(0o600); tmp.replace(p)
PY_WRITE
}


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

ensure_virtualization_config

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
echo "虚拟化配置：$(python3 -c 'import json; print(" + ".join(x.upper() for x in json.load(open("/etc/xnat/node.json")).get("virtualization_modes", ["lxc"])))')"
echo "备份：${BACKUP_DIR}"
