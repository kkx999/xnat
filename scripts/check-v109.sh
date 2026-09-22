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

echo "[4/5] v1.0.9 release + admin UI contracts"
"$PYTHON_BIN" - <<'PY'
from pathlib import Path
import json
root=Path('.')
meta=json.loads((root/'release.json').read_text())
assert (root/'VERSION').read_text().strip() == '1.0.9'
assert (root/'panel/VERSION').read_text().strip() == '1.0.9'
assert (root/'agent/VERSION').read_text().strip() == '1.0.4'
assert meta['release_version'] == '1.0.9'
assert meta['panel_version'] == '1.0.9'
assert meta['agent_version'] == '1.0.4'
assert str(meta['agent_api_version']) == '2'
assert str(meta['mobile_api_version']) == '1'
assert '__version__ = "1.0.9"' in (root/'panel/app/__init__.py').read_text()
assert '当前正式版本：v1.0.9' in (root/'README.md').read_text()
assert '"version": "1.0.9"' in (root/'panel/app/main.py').read_text()
assert 'XNAT v1.0.9 Multi-Node' in (root/'panel/app/templates/base.html').read_text()
assert '1.0.8) UPGRADE_PATH="verified-v1.0.8"' in (root/'scripts/upgrade-panel.sh').read_text()

admin=(root/'panel/app/templates/admin.html').read_text()
for token in [
    'admin-primary-action',
    'admin-state-action',
    'admin-compact-form',
    'admin-compact-save',
]:
    assert token in admin, token

css=(root/'panel/app/static/style.css').read_text()
for token in [
    'Admin / Client visual unification',
    '--admin-control-h:40px',
    'Mirror the customer-area palette',
    '.admin-primary-action',
    '.admin-state-action.disable',
    '.admin-state-action.enable',
    '.admin-compact-form',
    'Fix the system-images form',
    'Manual provision mirrors',
    'Light admin = same softened paper system',
]:
    assert token in css, token

mobile=(root/'panel/app/mobile_api.py').read_text()
assert '@router.post("/servers/{server_id}/auto-renew")' in mobile
assert '"auto_renew": bool(getattr(server, "auto_renew", False))' in mobile
jobs=(root/'panel/app/jobs.py').read_text()
assert 'provider.delete(server.provider_instance_id)' not in jobs
print('v1.0.9 release + admin UI contracts: ok')
PY

echo "[5/5] Existing service behavior preserved"
grep -q 'server.auto_renew.completed' panel/app/lifecycle.py
grep -q 'data-auto-renew-form' panel/app/templates/server_detail.html
grep -q '_CACHE_SECONDS = 3.0' agent/natvps_agent/metrics.py
echo "v1.0.9 checks: ok"
