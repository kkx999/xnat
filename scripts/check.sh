#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

cleanup(){
  find panel agent -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true
  find panel agent -type f -name '*.pyc' -delete 2>/dev/null || true
}
trap cleanup EXIT

PYTHON_BIN="${XNAT_CHECK_PYTHON:-python3}"
if [[ -z "${XNAT_CHECK_PYTHON:-}" && -x /opt/xnat/panel/.venv/bin/python ]] && /opt/xnat/panel/.venv/bin/python -c 'import jinja2' >/dev/null 2>&1; then
  PYTHON_BIN=/opt/xnat/panel/.venv/bin/python
fi

echo "[1/7] Python syntax ($PYTHON_BIN)"
"$PYTHON_BIN" -m compileall -q panel/app agent/natvps_agent

echo "[2/7] Jinja templates"
"$PYTHON_BIN" - <<'PY'
from jinja2 import Environment, FileSystemLoader
from pathlib import Path
root=Path('panel/app/templates')
env=Environment(loader=FileSystemLoader(str(root)))
for name in env.list_templates():
    env.get_template(name)
print(f"templates: {len(env.list_templates())}")
PY

echo "[3/7] Shell syntax"
while IFS= read -r -d '' script; do
  bash -n "$script"
done < <(find scripts -type f \( -name '*.sh' -o -name 'xnat' -o -name 'xnat-firewall' \) -print0)

echo "[4/7] Release / component versions"
python3 - <<'PY'
import json, pathlib, re
root=pathlib.Path('.')
meta=json.loads((root/'release.json').read_text())
release=(root/'VERSION').read_text().strip()
panel=(root/'panel/VERSION').read_text().strip()
agent=(root/'agent/VERSION').read_text().strip()
api=(root/'agent/API_VERSION').read_text().strip()
assert meta['release_version']==release
assert meta['panel_version']==panel
assert meta['agent_version']==agent
assert str(meta['agent_api_version'])==api
assert api in [str(x) for x in meta['supported_agent_api_versions']]
assert str(meta.get('mobile_api_version')) == '1'
assert f'__version__ = "{panel}"' in (root/'panel/app/__init__.py').read_text()
agent_init=(root/'agent/natvps_agent/__init__.py').read_text()
assert f'__version__ = "{agent}"' in agent_init
assert f'__api_version__ = "{api}"' in agent_init
agent_main=(root/'agent/natvps_agent/main.py').read_text()
assert f'AGENT_VERSION = "{agent}"' in agent_main
assert f'AGENT_API_VERSION = "{api}"' in agent_main
panel_main=(root/'panel/app/main.py').read_text()
assert f'"version": "{panel}"' in panel_main
base=(root/'panel/app/templates/base.html').read_text()
assert f'XNAT v{panel} Multi-Node' in base
print(f'Release {release} / Panel {panel} / Agent {agent} / API v{api}')
PY

grep -q 'install -m 0755.*scripts/xnat.*usr/local/sbin/xnat' scripts/install-panel.sh
grep -q 'install -m 0755.*scripts/xnat.*usr/local/sbin/xnat' scripts/install-host.sh
grep -q 'upgrade-panel.sh' scripts/xnat
grep -q 'upgrade-host-agent.sh' scripts/xnat
grep -q 'prompt_choice()' scripts/xnat
grep -q 'pause_return()' scripts/xnat
grep -q 'print_menu_header()' scripts/xnat
grep -q '按 Ctrl+C 退出实时日志并返回菜单' scripts/xnat
grep -q '组件版本相同，但当前 Release' scripts/xnat
grep -q 'xnat doctor' README.md || true

# v1.0.x Host UX contract: NAT user port range is configured only after the
# node connects to Panel, not during Host installation.
! grep -q 'HOST_PORT_START' scripts/install-host.sh
! grep -q 'HOST_PORT_END' scripts/install-host.sh
grep -q '/v1/config/nat-port-pool' agent/natvps_agent/main.py
grep -q '尚未配置 NAT 端口池' panel/app/nodes.py
grep -q '保存并同步到 Agent' panel/app/templates/admin.html
# Stable admin UX contracts retained from v1.0.x.
grep -q '/admin/servers/{server_id}/traffic/quota' panel/app/main.py
grep -q '/admin/servers/{server_id}/expiry' panel/app/main.py
grep -q '磁盘仅支持扩容' panel/app/templates/admin.html
grep -q 'USDT 充值' panel/app/templates/admin.html
grep -q 'section == "notifications"' panel/app/templates/admin.html
grep -q '发送 Telegram 测试' panel/app/templates/admin.html
! grep -q 'UniqueConstraint("public_port", "protocol"' panel/app/models.py

# v1.1.x reliability / lifecycle / UX contracts.
grep -q 'def host_schedule_state' panel/app/nodes.py
grep -q '/admin/nodes/{node_id}/maintenance' panel/app/main.py
grep -q 'maintenance_mode' panel/app/models.py
grep -q 'schedule_storage_max_percent' panel/app/models.py
grep -q 'def run_expiry_lifecycle' panel/app/lifecycle.py
grep -q '"expiry_delete_enabled": "false"' panel/app/main.py
grep -q '/admin/servers/{server_id}/traffic/cycle' panel/app/main.py
grep -q '/servers/{server_id}/traffic/reset' panel/app/main.py
grep -q 'traffic-self-reset-button' panel/app/templates/server_detail.html
grep -q 'traffic_cycle_mode' panel/app/models.py
grep -q 'queue_admin_notification' panel/app/nodes.py
grep -q 'admin.payment.repair_no_tx' panel/app/main.py
grep -q 'FORCE CREDIT' panel/app/templates/admin.html
grep -q "static_asset_version('client.js')" panel/app/templates/base.html
grep -q "static_asset_version('style.css')" panel/app/templates/base.html
grep -q 'class="plan-coupon-field"' panel/app/templates/plans.html
grep -q 'class="card admin-plan-card admin-plan-fold"' panel/app/templates/admin.html
grep -q 'admin-plan-summary-specs' panel/app/templates/admin.html
grep -q 'release polish: responsive cards, visible coupon field, folded plans' panel/app/static/style.css
grep -q 'body.client-body .client-plan-grid{' panel/app/static/style.css
grep -q 'grid-template-columns:repeat(3,minmax(0,1fr))!important' panel/app/static/style.css
! grep -q 'flex:1 1 calc(25% - 12px)!important' panel/app/static/style.css
grep -Fq 'grid-template-columns:repeat(3,minmax(0,1fr))!important' panel/app/static/style.css
! grep -Fq 'justify-content:center!important' panel/app/static/style.css
grep -q 'ensure_schema_extensions' panel/app/main.py
grep -q 'ensure_schema_extensions' panel/app/backups.py
grep -q 'announcement_seen_key' panel/app/models.py
grep -q 'announcement_seen_key' panel/app/schema.py
grep -q 'class Announcement(Base)' panel/app/models.py
grep -q 'class AnnouncementRead(Base)' panel/app/models.py
grep -q 'data-announcement-center-toggle' panel/app/templates/base.html
grep -q '/announcements/{announcement_id}/read' panel/app/main.py
grep -q '/admin/announcements/{announcement_id}/delete' panel/app/main.py
grep -q 'announcement.delete' panel/app/main.py
grep -q 'window.setTimeout(dismiss, 3000)' panel/app/static/client.js
grep -q 'data-client-theme-toggle' panel/app/templates/base.html
grep -q 'data-client-sidebar-toggle' panel/app/templates/base.html
grep -q 'data-client-nav-group-toggle' panel/app/templates/base.html
grep -q 'xnat-client-mobile-nav-groups' panel/app/static/client.js
grep -q 'mobile client navigation drawer + collapsible categories' panel/app/static/style.css
grep -Fq '.client-sidebar-backdrop[hidden]{display:none!important;pointer-events:none!important}' panel/app/static/style.css
grep -Fq '.client-sidebar-backdrop.is-open{opacity:1;pointer-events:auto}' panel/app/static/style.css
grep -Fq '.client-mobile-menu span:nth-child(2){width:16px;align-self:center;margin-left:0}' panel/app/static/style.css
grep -Fq 'height:var(--xnat-client-viewport-height,100svh)!important' panel/app/static/style.css
grep -Fq 'padding:14px 12px max(48px,calc(env(safe-area-inset-bottom,0px) + 12px))' panel/app/static/style.css
grep -Fq 'const syncClientViewportHeight = () =>' panel/app/static/client.js
grep -q 'xnat-client-theme' panel/app/static/client.js
grep -q 'xnat-admin-theme' panel/app/static/client.js
grep -q 'data-admin-theme-toggle' panel/app/templates/admin.html
grep -q 'traffic_reset_price_cents' panel/app/models.py
grep -q 'kind="traffic_reset"' panel/app/main.py
grep -q 'data-xnat-confirm' panel/app/templates/server_detail.html
grep -q 'xnat-confirm-backdrop' panel/app/static/client.js
! grep -RInE '(^|[^A-Za-z])confirm\s*\(' panel/app/static/client.js panel/app/templates >/tmp/xnat-native-confirm.txt
grep -q 'data-client-theme="light"' panel/app/static/style.css
grep -q 'body.admin-body .xnat-toast' panel/app/static/style.css
grep -q 'announcement-option-switch' panel/app/templates/admin.html
grep -q '删除公告' panel/app/templates/admin.html
! grep -q '下线公告' panel/app/templates/admin.html
! grep -q 'name="announcement_enabled"' panel/app/templates/admin.html
! grep -q 'name="announcement_text"' panel/app/templates/admin.html
grep -q '数据库迁移缺少表' scripts/upgrade-panel.sh
grep -q '1.4.0) UPGRADE_PATH="verified-v1.4.0"' scripts/upgrade-panel.sh
grep -q '1.3.3) UPGRADE_PATH="verified-v1.3.3"' scripts/upgrade-panel.sh
grep -q '1.3.2) UPGRADE_PATH="verified-v1.3.2"' scripts/upgrade-panel.sh
grep -q '1.3.1) UPGRADE_PATH="verified-v1.3.1"' scripts/upgrade-panel.sh
grep -q '1.3.0) UPGRADE_PATH="verified-v1.3.0"' scripts/upgrade-panel.sh
grep -q '1.2.0) UPGRADE_PATH="verified-v1.2.0"' scripts/upgrade-panel.sh
grep -q 'PRAGMA quick_check' scripts/upgrade-panel.sh
grep -q 'DATABASE_URL_VALUE' scripts/upgrade-panel.sh
grep -q 'virtualization_type' panel/app/models.py
grep -q 'virtualization_modes' panel/app/models.py
grep -q 'kvm_available' panel/app/models.py
grep -q 'args.append("--vm")' agent/natvps_agent/main.py
grep -q 'def wait_guest_agent' agent/natvps_agent/main.py
grep -q 'KVM Guest Agent 未能在' agent/natvps_agent/main.py
grep -q 'debconf: delaying package configuration' agent/natvps_agent/main.py
grep -q 'def _wait_guest_agent' panel/app/providers/incus.py
grep -q 'def add_proxy_device' agent/natvps_agent/main.py
grep -q 'connect={protocol}:0.0.0.0:{private_port}' agent/natvps_agent/main.py
grep -q '"nat=true"' agent/natvps_agent/main.py
grep -q 'def _add_proxy_device' panel/app/providers/incus.py
grep -q '00-00-xnat.conf' agent/natvps_agent/main.py
grep -q 'passwordauthentication yes' agent/natvps_agent/main.py
# Avoid grep -q in SSH validation pipelines under set -o pipefail: an early
# grep exit can SIGPIPE sshd/ss and make a successful check return 141.
grep -Fq "sshd -T | grep -x 'permitrootlogin yes' >/dev/null" agent/natvps_agent/main.py
grep -Fq "sshd -T | grep -x 'passwordauthentication yes' >/dev/null" agent/natvps_agent/main.py
grep -Fq "ss -lnt '( sport = :22 )' | grep 'LISTEN' >/dev/null" agent/natvps_agent/main.py
! grep -Fq "sshd -T | grep -qx" agent/natvps_agent/main.py panel/app/providers/incus.py
! grep -Fq "ss -lnt '( sport = :22 )' | grep -q LISTEN" agent/natvps_agent/main.py panel/app/providers/incus.py
grep -Fq '$2 != \"lo\"' panel/app/providers/incus.py
! grep -q 'addr show dev eth0 scope global' panel/app/providers/incus.py
grep -q 'for d in /sys/class/net/\*' panel/app/providers/incus.py
grep -q 'VIRTUALIZATION_MODE' scripts/install-host.sh
grep -q 'LXC + KVM' scripts/install-host.sh
grep -q 'kvm_unavailable' panel/app/nodes.py
grep -q 'name="virtualization_type"' panel/app/templates/admin.html
grep -q 'timeout=600 if str(virtualization_type).lower() == "kvm" else 260' panel/app/providers/remote.py
grep -Fq 'str(detail)[:1200]' panel/app/nodes.py


# v1.3.2 Mobile API v1 contract for XNAT Android v1.0.0.
test -f panel/app/mobile_api.py
grep -q 'from \.mobile_api import router as mobile_api_router' panel/app/main.py
grep -q 'app.include_router(mobile_api_router)' panel/app/main.py
grep -q 'APIRouter(prefix="/api/v1"' panel/app/mobile_api.py
grep -q '@router.get("/health")' panel/app/mobile_api.py
grep -q '@router.post("/auth/login")' panel/app/mobile_api.py
grep -q '@router.get("/servers")' panel/app/mobile_api.py
grep -q '@router.get("/billing")' panel/app/mobile_api.py
grep -q '@router.get("/tickets")' panel/app/mobile_api.py
grep -q '@router.get("/catalog")' panel/app/mobile_api.py
grep -q '@router.post("/purchase/quote")' panel/app/mobile_api.py
grep -q '@router.post("/purchase")' panel/app/mobile_api.py
grep -q '@router.post("/servers/{server_id}/ports")' panel/app/mobile_api.py
grep -q '@router.post("/servers/{server_id}/reinstall")' panel/app/mobile_api.py

# v1.4.0 operations / diagnostics contracts.
grep -q 'run_update_preflight()' scripts/xnat
grep -q 'verify_release_tree()' scripts/xnat
grep -q 'panel_agent_api_preflight()' scripts/xnat
grep -q 'cmd_diagnostic_report()' scripts/xnat
grep -q 'redact_diagnostic_report()' scripts/xnat
grep -q 'interactive_diagnostics()' scripts/xnat
grep -q '导出脱敏诊断报告' scripts/xnat
grep -q 'HTTPS 证书正常' scripts/xnat
grep -q 'NAT 端口池' scripts/xnat
grep -q 'xnat doctor report' README.md
# v1.4.0 admin support / backup management contracts.
grep -q '/admin/backups/{backup_name}/delete' panel/app/main.py
grep -q 'def delete_backup' panel/app/backups.py
grep -q '完整会话' panel/app/templates/admin.html
grep -q 'Ticket.messages.any(TicketMessage.body.ilike(like))' panel/app/main.py
grep -q 'data-confirm-input="yes"' panel/app/templates/admin.html
# v1.4.0 final regression guards: keep the verified customer motion,
# admin notification/account fixes, and explicit safe balance actions intact.
grep -q 'v1.4.0 customer interaction system' panel/app/static/style.css
! grep -q 'client sidebar refinement' panel/app/static/style.css
grep -q 'beginClientThemeTransition' panel/app/static/client.js
grep -q 'client-route-leaving' panel/app/static/client.js
grep -q 'xnat-page-enter' panel/app/static/client.js
grep -q 'id="session-history-more"' panel/app/templates/account.html
grep -q 'id="login-history-more"' panel/app/templates/account.html
grep -q 'client-notification-preferences' panel/app/templates/account.html
grep -q 'client-toggle-control' panel/app/templates/account.html
grep -q 'notification-admin-fold' panel/app/templates/admin.html
grep -q 'admin-record-fold' panel/app/templates/admin.html
grep -q 'settings-section settings-fold' panel/app/templates/admin.html
grep -q 'page_size=12' panel/app/main.py
grep -q "static_asset_version('style.css')" panel/app/templates/base.html
grep -q "static_asset_version('client.js')" panel/app/templates/base.html
python3 - <<'PYFINAL140'
from pathlib import Path
css=Path('panel/app/static/style.css').read_text()
assert css.count('{') == css.count('}'), 'CSS brace imbalance'
assert '@media(max-width:760px){\n  html[data-client-theme="light"] body.client-body .client-sidebar-close' in css, 'mobile light nav styles leaked to desktop again'
assert 'body.admin-body .settings-save-row{\n  border-top:0!important;' in css, 'hard save separator returned'
assert 'body.admin-body .notification-rule-block{\n  margin-top:0!important;\n  padding-top:0!important;\n  border-top:0!important;' in css, 'notification hard separator returned'
assert 'body.client-body .security-history-grid{align-items:start!important}' in css, 'security cards may stretch together again'
account=Path('panel/app/templates/account.html').read_text()
assert account.count('class="security-history-more"') == 2, 'security history fold count changed'
assert 'name="notify_email"' in account and 'name="notify_telegram"' in account, 'notification preference field names changed'
assert account.count('class="client-notification-toggle"') == 2, 'notification switches must stay equal paired controls'
admin=Path('panel/app/templates/admin.html').read_text()
assert admin.count('class="settings-section settings-fold') == 6, 'site settings fold count changed unexpectedly'
assert admin.count('class="notification-admin-fold') >= 3, 'notification page is no longer compactly folded'
assert '<details class="admin-record-fold" {% if q %}open{% endif %}>' in admin, 'notification record fold changed unexpectedly'
base=Path('panel/app/templates/base.html').read_text()
assert "static_asset_version('style.css')" in base, 'content fingerprint style cache-bust missing'
assert "static_asset_version('client.js')" in base, 'content fingerprint client cache-bust missing'
assert 'class="balance-action-button credit"' in admin and 'class="balance-action-button debit"' in admin, 'explicit credit/debit buttons missing'
main=Path('panel/app/main.py').read_text()
assert 'action: str | None = Form(None)' in main and 'form_version: str = Form("")' in main, 'fail-closed balance route missing'
assert 'entered_cents > balance_before' in main, 'debit overdraw guard missing'
assert 'admin.balance.debit' in main and 'admin.balance.credit' in main, 'balance audit actions missing'
assert 'grid-template-columns:repeat(2,minmax(0,1fr));' in css, 'equal balance action button grid missing'
assert 'body.admin-body .admin-search input{\n  height:42px!important;\n  min-height:42px!important;\n  margin:0!important;' in css, 'admin search field alignment guard missing'
assert 'grid-template-columns:repeat(2,80px);' in css, 'compact equal balance button columns missing'
assert 'width:80px;' in css and 'height:34px;' in css, 'compact balance button geometry missing'
client_js=Path('panel/app/static/client.js').read_text()
assert 'submitter.disabled = true' not in client_js, 'submitter serialization regression returned'
assert 'form.dataset.xnatSubmitting' in client_js and 'aria-disabled' in client_js, 'safe duplicate-submit guard missing'
assert 'name="form_version" value="2"' in admin, 'versioned balance form missing'
assert '余额操作类型缺失或无效' in main, 'balance fail-closed guard missing'
assert '前端展示旗帜' in admin and '机器编号前缀' in admin, 'host flag/machine-prefix fields missing'
assert 'server_region_value' in admin and 'network_line_value' in admin, 'plan display metadata fields missing'
assert 'xnat-country-options' not in admin and 'xnat-node-country-options' in admin, 'country selector scope regression'
plans=Path('panel/app/templates/plans.html').read_text()
assert '服务器地区' in plans and '网络线路' in plans and 'NAT 端口' in plans, '3x3 plan location parameters missing'
assert 'xnat-country-flag' not in plans and 'flags/' not in plans, 'purchase page must not show country flags'
servers_tpl=Path('panel/app/templates/servers.html').read_text()
detail_tpl=Path('panel/app/templates/server_detail.html').read_text()
assert 'server_display_id(s)' in servers_tpl and '/flags/' in servers_tpl, 'server card stable ID/flag missing'
assert 'server_display_id(server)' in detail_tpl and '/flags/' in detail_tpl, 'server detail stable ID/flag missing'
for label in ['公网主机','私网 IPv4','SSH','配置','虚拟化','当前带宽','NAT 端口','剩余流量']:
    assert label in detail_tpl, f'server detail overview card missing: {label}'
for removed in ['<span>国家 / 地区</span>','<span>服务器地区</span>','<span>区域代码</span>','<span>网络线路</span>','<span>本周期已用</span>','<span>本周期总流量</span>']:
    assert removed not in detail_tpl.split('<section class="traffic-usage-panel">',1)[0], f'redundant detail overview field returned: {removed}'
assert "static_asset_version('flags/' ~" in servers_tpl and "static_asset_version('flags/' ~" in detail_tpl, 'per-flag asset fingerprint missing'
from panel.app.geo import COUNTRY_OPTIONS, normalize_country_code
flags_dir=Path('panel/app/static/flags')
for item in COUNTRY_OPTIONS:
    code=item['code'].lower()
    assert (flags_dir/f'{code}.svg').is_file(), f'flag asset missing: {code}'
    assert normalize_country_code(item['name'], allow_empty=False) == item['code'], f'country mapping mismatch: {item}'
assert normalize_country_code('香港', allow_empty=False) == 'HK', 'Hong Kong mapping regression'
models=Path('panel/app/models.py').read_text()
schema=Path('panel/app/schema.py').read_text()
for field in ['country_code','server_region','region_code','network_line','display_id']:
    assert field in models and field in schema, f'schema field missing: {field}'
mobile=Path('panel/app/mobile_api.py').read_text()
for field in ['"display_id"','"country"','"region"','"region_code"','"network_line"','"nat_port"']:
    assert field in mobile, f'mobile API metadata missing: {field}'
print('v1.4.0 final + next-round regression guards: ok')
PYFINAL140

# v1.4.1 payment state-machine / immutable display snapshot guards
python3 - <<'PYDEV3'
from pathlib import Path
models=Path('panel/app/models.py').read_text()
schema=Path('panel/app/schema.py').read_text()
main=Path('panel/app/main.py').read_text()
pay=Path('panel/app/payments.py').read_text()
recharge=Path('panel/app/templates/recharge.html').read_text()
detail=Path('panel/app/templates/recharge_detail.html').read_text()
geo=Path('panel/app/geo.py').read_text()
css=Path('panel/app/static/style.css').read_text()
upgrade=Path('scripts/upgrade-panel.sh').read_text()
for f in ['cancelled_at','cancelled_by']:
    assert f in models and f in schema, f'missing recharge cancellation field {f}'
assert 'def cancel_recharge_order' in pay and 'RechargeOrder.status.in_(["pending", "manual"])' in pay, 'atomic cancellation guard missing'
assert 'status="exception"' in pay and '系统未自动入账' in pay, 'cancelled late-payment quarantine missing'
assert '@app.post("/recharge/{recharge_id}/cancel")' in main, 'cancel route missing'
assert '取消订单' in recharge and 'data-xnat-confirm' in recharge, 'cancel UI/confirmation missing'
assert "recharge.status == 'cancelled'" in detail and "recharge.status == 'exception'" in detail, 'cancelled/exception detail states missing'
assert 'payment-cancel-order-form' in detail and detail.index('payment-cancel-order-form') < detail.index('payment-pay-grid'), 'detail cancel action placement regression'
assert '"cancelled": "已取消"' in main and '"exception": "异常支付"' in main, 'status labels missing'
for f in ['server_region_snapshot','network_line_snapshot']:
    assert f in models and f in schema and f in main, f'server display snapshot missing {f}'
assert 'server_region_snapshot' in geo and 'network_line_snapshot' in geo, 'geo helpers must prefer snapshots'
assert '机器编号前缀' in geo and '机器编号前缀' in main, 'machine prefix terminology/validation missing'
assert 'func.upper(HostNode.region_code) == machine_prefix.upper()' in main, 'duplicate machine prefix guard missing'
assert r'\nbody.client-body .recharge-' not in css and r'\nhtml[data-client-theme=' not in css, 'literal \\n escaped into executable CSS'
for token in ['.recharge-cancel-button', '.payment-cancel-order-button', '@media(max-width:700px)']:
    assert token in css, f'recharge responsive style missing: {token}'
for spec in ['servers:server_region_snapshot','servers:network_line_snapshot','recharge_orders:cancelled_at','recharge_orders:cancelled_by']:
    assert spec in upgrade, f'upgrade post-migration guard missing: {spec}'
print('v1.4.1 recharge + snapshot guards: ok')
PYDEV3

# v1.4.1 Telegram onboarding / configuration-state guards
python3 - <<'PYDEV5'
from pathlib import Path
main=Path('panel/app/main.py').read_text()
notifications=Path('panel/app/notifications.py').read_text()
runtime=Path('panel/app/runtime_config.py').read_text()
account=Path('panel/app/templates/account.html').read_text()
admin=Path('panel/app/templates/admin.html').read_text()
css=Path('panel/app/static/style.css').read_text()
assert 'def telegram_bot_identity' in notifications and '_telegram_api_request(bot_token, "getMe")' in notifications, 'Telegram getMe validator missing'
assert 'telegram_bot_username' in runtime and 'TELEGRAM_BOT_USERNAME' in runtime, 'runtime bot username missing'
assert '@app.post("/account/telegram/test")' in main, 'user Telegram test route missing'
assert '站点暂未配置 Telegram Bot，当前无法开启 Telegram 通知。' in main, 'Telegram enable fail-closed guard missing'
assert "Preserve the user's existing preference" in main, 'unavailable Telegram preference preservation missing'
assert 'Telegram Chat ID 格式无效' in main, 'Telegram Chat ID validation missing'
assert 'values["telegram_bot_username"]' in main and 'telegram_bot_identity(telegram_token)' in main, 'admin bot username discovery missing'
assert '打开机器人' in account and '发送测试消息' in account and '点击 Start' in account, 'account Telegram onboarding copy/actions missing'
assert 'disabled' in account and 'telegram_available' in account, 'unconfigured Telegram switch guard missing'
assert '当前机器人：' in admin and 'Telegram getMe' in admin, 'admin bot identity status missing'
assert 'XNAT v1.4.1: Telegram account onboarding.' in css and '.telegram-test-button' in css, 'scoped Telegram account styles missing'
assert 'body.client-body button{' not in css[-7000:], 'v1.4.1 must not add a global client button override'
print('v1.4.1 Telegram onboarding guards: ok')
PYDEV5

# v1.4.1 formal release / direct-upgrade guards
python3 - <<'PYREL141'
from pathlib import Path
import json
root=Path('.')
release=(root/'VERSION').read_text().strip()
panel=(root/'panel/VERSION').read_text().strip()
meta=json.loads((root/'release.json').read_text())
assert release == '1.4.1', f'unexpected formal release version: {release}'
assert panel == '1.4.1', f'unexpected Panel version: {panel}'
assert meta['release_version'] == '1.4.1' and meta['panel_version'] == '1.4.1', 'release.json 1.4.1 metadata mismatch'
upgrade=(root/'scripts/upgrade-panel.sh').read_text()
assert '1.4.0) UPGRADE_PATH="verified-v1.4.0"' in upgrade, 'formal v1.4.0 -> v1.4.1 direct upgrade path missing'
main=(root/'panel/app/main.py').read_text()
assert '"version": "1.4.1"' in main, 'health version mismatch'
base=(root/'panel/app/templates/base.html').read_text()
assert 'XNAT v1.4.1 Multi-Node' in base, 'footer version mismatch'
readme=(root/'README.md').read_text()
assert 'xnat update 1.4.1' in readme, 'formal upgrade command missing from README'
build=(root/'scripts/build-release.sh').read_text()
assert 'v1.4.0 → v${PANEL_VERSION}' in build, 'v1.4.0 direct-upgrade compatibility missing from release notes generator'
assert '本次为 XNAT v${RELEASE_VERSION} 的兼容性增量更新' in build, 'release notes version must follow release metadata'
assert r'Tag \`v${RELEASE_VERSION}\`' in build and r'\`releases/latest\`' in build, 'release-note markdown backticks must be shell-escaped'
print('v1.4.1 formal release + direct-upgrade guards: ok')
PYREL141

echo "[5/7] Clean baseline guard"
if find . -maxdepth 3 -type f | grep -Ei '(testing|preview-[0-9]|rc[0-9]|patch-panel|patch-management|upgrade-from-v1\.0\.3|UPGRADE-FROM)' >/tmp/xnat-clean-guard.txt; then
  echo "[ERROR] Found testing/RC/legacy artifacts:"
  cat /tmp/xnat-clean-guard.txt
  exit 1
fi
if grep -RInE 'v1\.0\.3|v1\.0\.4|testing-v|(^|[^A-Za-z])RC[0-9]+|(^|[^A-Za-z])rc[0-9]+|候选版本' \
  --exclude-dir=.git --exclude-dir=__pycache__ --exclude='check.sh' . >/tmp/xnat-old-version.txt; then
  echo "[ERROR] Found old/test version references:"
  cat /tmp/xnat-old-version.txt
  exit 1
fi

echo "[6/7] Secret/runtime file guard"
if find . \
  -path './.git' -prune -o \
  -type f \( -name '.env' -o -name '*.db' -o -name '*.sqlite' -o -name '*.key' -o -name '*.pem' \) \
  -print | grep -q .; then
  echo "[ERROR] Repository contains runtime secret/data files:"
  find . \
    -path './.git' -prune -o \
    -type f \( -name '.env' -o -name '*.db' -o -name '*.sqlite' -o -name '*.key' -o -name '*.pem' \) \
    -print
  exit 1
fi

echo "[7/7] Placeholder secret scan"
if grep -RInE \
  --exclude-dir=.git --exclude-dir=__pycache__ \
  --exclude='*.example' --exclude='check.sh' \
  '(BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY|AGENT_TOKEN=[0-9a-fA-F]{32,}|APP_SECRET=[0-9a-fA-F]{32,})' \
  .; then
  echo "[ERROR] Possible real secret detected."
  exit 1
fi

# v1.3.x KVM/admin compatibility guards
grep -q 'KVM 套餐最低需要 512 MB 内存和 4 GB 磁盘' panel/app/main.py
grep -q 'data-virtualization-form' panel/app/templates/admin.html
grep -q 'KVM 实例最低需要 512 MB 内存和 4 GB 磁盘' panel/app/main.py
grep -q 'wait_guest_agent(instance_id, mode)' agent/natvps_agent/main.py
grep -q 'virtualization_type: str | None = None' panel/app/providers/base.py
grep -q '虚拟化类型不一致：Panel=' panel/app/reconcile.py
grep -q '实例内全部数据' panel/app/templates/server_detail.html
grep -q 's.virtualization_type' panel/app/templates/servers.html
grep -q 's.virtualization_type' panel/app/templates/dashboard.html

echo "XNAT repository checks passed."
