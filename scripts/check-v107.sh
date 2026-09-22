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

echo "[4/5] v1.0.7 release + UI contracts"
"$PYTHON_BIN" - <<'PY'
from pathlib import Path
import json
root=Path('.')
meta=json.loads((root/'release.json').read_text())
assert (root/'VERSION').read_text().strip() == '1.0.7'
assert (root/'panel/VERSION').read_text().strip() == '1.0.7'
assert (root/'agent/VERSION').read_text().strip() == '1.0.4'
assert meta['release_version'] == '1.0.7'
assert meta['panel_version'] == '1.0.7'
assert meta['agent_version'] == '1.0.4'
assert str(meta['agent_api_version']) == '2'
assert [str(x) for x in meta['supported_agent_api_versions']] == ['1','2']
assert str(meta['mobile_api_version']) == '1'
assert '__version__ = "1.0.7"' in (root/'panel/app/__init__.py').read_text()
assert '__version__ = "1.0.4"' in (root/'agent/natvps_agent/__init__.py').read_text()
assert '当前正式版本：v1.0.7' in (root/'README.md').read_text()
assert '"version": "1.0.7"' in (root/'panel/app/main.py').read_text()
assert 'XNAT v1.0.7 Multi-Node' in (root/'panel/app/templates/base.html').read_text()
assert '1.0.6) UPGRADE_PATH="verified-v1.0.6"' in (root/'scripts/upgrade-panel.sh').read_text()
assert "'servers:auto_renew'" in (root/'scripts/upgrade-panel.sh').read_text()

tpl=(root/'panel/app/templates/server_detail.html').read_text()
assert 'server-renew-button-copy' in tpl
assert 'server-renew-price' in tpl
assert '手动续费' in tpl
assert 'data-auto-renew-form' in tpl

css=(root/'panel/app/static/style.css').read_text()
for token in [
    'v1.0.7 visual refinement',
    '.server-renew-button-copy',
    '.server-renew-price',
    '.auth-visual::before',
    'grid-template-columns:minmax(0,1.16fr) minmax(430px,.84fr)',
    'font-size:clamp(40px,4vw,58px)',
]:
    assert token in css, token

jobs=(root/'panel/app/jobs.py').read_text()
assert 'provider.delete(server.provider_instance_id)' not in jobs
main=(root/'panel/app/main.py').read_text()
assert '@app.post("/servers/{server_id}/auto-renew")' in main
assert 'row.tls_fingerprint = None' in main
print('v1.0.7 release + UI contracts: ok')
PY

echo "[5/5] Existing service behavior preserved"
grep -q 'data-metrics-url="/servers/{{ server.id }}/metrics"' panel/app/templates/server_detail.html
grep -q 'setTimeout(load,5000)' panel/app/static/client.js
grep -q 'visibilitychange' panel/app/static/client.js
grep -q '_CACHE_SECONDS = 3.0' agent/natvps_agent/metrics.py
grep -q 'server.auto_renew.completed' panel/app/lifecycle.py

echo "v1.0.7 checks: ok"
