#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PYTHON_BIN="${XNAT_CHECK_PYTHON:-python3}"

cleanup(){
  find panel agent -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true
  find panel agent -type f -name '*.pyc' -delete 2>/dev/null || true
}
trap cleanup EXIT

echo "[1/5] Python syntax"
"$PYTHON_BIN" -m compileall -q panel/app agent/natvps_agent

echo "[2/5] Jinja templates"
"$PYTHON_BIN" - <<'PY'
from jinja2 import Environment, FileSystemLoader
from pathlib import Path
root=Path('panel/app/templates')
env=Environment(loader=FileSystemLoader(str(root)))
for name in env.list_templates():
    env.get_template(name)
print(f"templates: {len(env.list_templates())}")
PY

echo "[3/5] Shell syntax"
while IFS= read -r -d '' script; do
  bash -n "$script"
done < <(find scripts -type f \( -name '*.sh' -o -name 'xnat' -o -name 'xnat-firewall' \) -print0)

echo "[4/5] Release matrix + version + dual-theme UI contracts"
"$PYTHON_BIN" - <<'PY'
from pathlib import Path
import json
root=Path('.')
meta=json.loads((root/'release.json').read_text())
assert (root/'VERSION').read_text().strip() == '1.0.5'
assert (root/'panel/VERSION').read_text().strip() == '1.0.5'
assert (root/'agent/VERSION').read_text().strip() == '1.0.4'
assert meta['release_version'] == '1.0.5'
assert meta['panel_version'] == '1.0.5'
assert meta['agent_version'] == '1.0.4'
assert str(meta['agent_api_version']) == '2'
assert [str(x) for x in meta['supported_agent_api_versions']] == ['1','2']
assert str(meta['mobile_api_version']) == '1'
assert '__version__ = "1.0.5"' in (root/'panel/app/__init__.py').read_text()
assert '__version__ = "1.0.4"' in (root/'agent/natvps_agent/__init__.py').read_text()
assert '__api_version__ = "2"' in (root/'agent/natvps_agent/__init__.py').read_text()
assert '当前正式版本：v1.0.5' in (root/'README.md').read_text()
assert '"version": "1.0.5"' in (root/'panel/app/main.py').read_text()
assert 'XNAT v1.0.5 Multi-Node' in (root/'panel/app/templates/base.html').read_text()
assert '1.0.4) UPGRADE_PATH="verified-v1.0.4"' in (root/'scripts/upgrade-panel.sh').read_text()

tpl=(root/'panel/app/templates/server_detail.html').read_text()
assert 'id="server-detail-theme-polish-v105"' in tpl
for token in [
    'body.client-body .server-live-head>div:first-child span{color:#edf3fb}',
    'html[data-client-theme="light"] body.client-body .server-live-head>div:first-child span{color:#263b52!important}',
    'body.client-body .client-detail-section[open]>summary{border-bottom:0!important}',
    'html[data-client-theme="light"] body.client-body .client-detail-section[open]>summary{border-bottom:0!important}',
    'body.client-body .client-detail-body{border-top:0!important;background:#0f1720}',
    'html[data-client-theme="light"] body.client-body .client-detail-body{border-top:0!important;background:#fafbfc}',
    'border-radius:999px',
    'data-server-live-metrics',
    'data-live-rx',
    'data-live-tx',
]:
    assert token in tpl, token
client_js=(root/'panel/app/static/client.js').read_text()
assert 'setTimeout(load,5000)' in client_js
assert 'visibilitychange' in client_js
metrics=(root/'agent/natvps_agent/metrics.py').read_text()
assert '_CACHE_SECONDS = 3.0' in metrics
assert 'sqlite' not in metrics.lower()
assert 'write_text' not in metrics
print('v1.0.5 release matrix + version + dual-theme contracts: ok')
PY

echo "[5/5] v1.0.4 live-metrics behavior preserved"
grep -q '@app.get("/v1/instances/{instance_id}/metrics")' agent/natvps_agent/main.py
grep -q 'from .metrics import collect as collect_instance_metrics' agent/natvps_agent/main.py
grep -q 'data-metrics-url="/servers/{{ server.id }}/metrics"' panel/app/templates/server_detail.html
grep -q 'server-live-grid' panel/app/templates/server_detail.html
grep -q 'CPU 使用率' panel/app/templates/server_detail.html
grep -q '内存使用' panel/app/templates/server_detail.html
grep -q '硬盘使用' panel/app/templates/server_detail.html
grep -q '实时网络' panel/app/templates/server_detail.html

echo "v1.0.5 checks: ok"
