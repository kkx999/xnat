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

echo "[1/7] Python syntax"
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

echo "[3/7] JavaScript syntax"
node --check panel/app/static/v106.js

echo "[4/7] Shell syntax"
while IFS= read -r -d '' script; do
  bash -n "$script"
done < <(find scripts -type f \( -name '*.sh' -o -name 'xnat' -o -name 'xnat-firewall' \) -print0)

echo "[5/7] Release/version matrix"
"$PYTHON_BIN" - <<'PY'
from pathlib import Path
import json
root=Path('.')
meta=json.loads((root/'release.json').read_text())
assert (root/'VERSION').read_text().strip() == '1.0.6'
assert (root/'panel/VERSION').read_text().strip() == '1.0.6'
assert (root/'agent/VERSION').read_text().strip() == '1.0.4'
assert meta['release_version'] == '1.0.6'
assert meta['panel_version'] == '1.0.6'
assert meta['agent_version'] == '1.0.4'
assert str(meta['agent_api_version']) == '2'
assert [str(x) for x in meta['supported_agent_api_versions']] == ['1','2']
assert str(meta['mobile_api_version']) == '1'
assert '__version__ = "1.0.6"' in (root/'panel/app/__init__.py').read_text()
assert '__version__ = "1.0.4"' in (root/'agent/natvps_agent/__init__.py').read_text()
assert '__api_version__ = "2"' in (root/'agent/natvps_agent/__init__.py').read_text()
assert '当前正式版本：v1.0.6' in (root/'README.md').read_text()
main=(root/'panel/app/main.py').read_text()
assert '"version": PANEL_VERSION' in main
assert '1.0.5) UPGRADE_PATH="verified-v1.0.5"' in (root/'scripts/upgrade-panel.sh').read_text()
print('release matrix: ok')
PY

echo "[6/7] v1.0.6 safety and UI contracts"
"$PYTHON_BIN" - <<'PY'
from pathlib import Path
root=Path('.')
models=(root/'panel/app/models.py').read_text()
schema=(root/'panel/app/schema.py').read_text()
life=(root/'panel/app/lifecycle.py').read_text()
jobs=(root/'panel/app/jobs.py').read_text()
main=(root/'panel/app/main.py').read_text()
detail=(root/'panel/app/templates/server_detail.html').read_text()
base=(root/'panel/app/templates/base.html').read_text()
css=(root/'panel/app/static/v106.css').read_text()
js=(root/'panel/app/static/v106.js').read_text()

for token in ['auto_renew: Mapped[bool]', 'auto_renew_last_expiry_at']:
    assert token in models, token
for token in ['"auto_renew"', '"auto_renew_last_expiry_at"']:
    assert token in schema, token
for token in ['AUTO_RENEW_WINDOW = timedelta(hours=24)', 'Server.auto_renew.is_(True)', 'kind="auto_renewal"']:
    assert token in life, token

assert 'provider.delete(server.provider_instance_id)' not in jobs
for token in ['"panel_only": True', '"host_contacted": False', 'server.provider_instance_id = None']:
    assert token in jobs, token

for token in ['row.tls_fingerprint = None', '@app.post("/servers/{server_id}/auto-renew")']:
    assert token in main, token
for token in ['server-auto-renew-switch', 'data-machine-copy', 'renew-actions-v106']:
    assert token in detail, token
for token in ['auth-v106-body', "path='/v106.css'", "path='/v106.js'"]:
    assert token in base, token
for token in ['grid-template-columns:minmax(0,1fr) minmax(300px,360px)', 'client-danger-grid form button.danger', 'height:44px']:
    assert token in css, token
for token in ['data-auto-renew-form', 'data-machine-copy', 'data-auth-password-toggle']:
    assert token in js, token

for name in ['login.html','register.html','login_2fa.html','forgot_password.html','reset_password.html','register_closed.html']:
    text=(root/'panel/app/templates'/name).read_text()
    assert 'extends "auth_base.html"' in text, name
print('v1.0.6 safety/UI contracts: ok')
PY

echo "[7/7] v1.0.4 live-metrics behavior preserved"
grep -q '@app.get("/v1/instances/{instance_id}/metrics")' agent/natvps_agent/main.py
grep -q 'from .metrics import collect as collect_instance_metrics' agent/natvps_agent/main.py
grep -q 'data-metrics-url="/servers/{{ server.id }}/metrics"' panel/app/templates/server_detail.html
grep -q 'setTimeout(load,5000)' panel/app/static/client.js
grep -q 'visibilitychange' panel/app/static/client.js

echo "v1.0.6 checks: ok"
