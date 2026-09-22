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
while IFS= read -r -d '' script; do bash -n "$script"; done < <(find scripts -type f \( -name '*.sh' -o -name 'xnat' -o -name 'xnat-firewall' \) -print0)

echo "[4/5] v1.0.8 release + Mobile API contracts"
"$PYTHON_BIN" - <<'PY'
from pathlib import Path
import json
root=Path('.')
meta=json.loads((root/'release.json').read_text())
assert (root/'VERSION').read_text().strip() == '1.0.8'
assert (root/'panel/VERSION').read_text().strip() == '1.0.8'
assert (root/'agent/VERSION').read_text().strip() == '1.0.4'
assert meta['release_version'] == '1.0.8'
assert meta['panel_version'] == '1.0.8'
assert meta['agent_version'] == '1.0.4'
assert str(meta['agent_api_version']) == '2'
assert str(meta['mobile_api_version']) == '1'
assert '__version__ = "1.0.8"' in (root/'panel/app/__init__.py').read_text()
assert '当前正式版本：v1.0.8' in (root/'README.md').read_text()
assert '"version": "1.0.8"' in (root/'panel/app/main.py').read_text()
assert 'XNAT v1.0.8 Multi-Node' in (root/'panel/app/templates/base.html').read_text()
assert '1.0.7) UPGRADE_PATH="verified-v1.0.7"' in (root/'scripts/upgrade-panel.sh').read_text()

mobile=(root/'panel/app/mobile_api.py').read_text()
assert '"auto_renew": bool(getattr(server, "auto_renew", False))' in mobile
assert '@router.post("/servers/{server_id}/auto-renew")' in mobile
assert 'server.auto_renew = enabled' in mobile
assert 'server.auto_renew.update.mobile' in mobile
docs=(root/'docs/MOBILE_API.md').read_text()
assert 'POST /api/v1/servers/{server_id}/auto-renew' in docs
assert 'auto_renew: boolean' in docs

jobs=(root/'panel/app/jobs.py').read_text()
assert 'provider.delete(server.provider_instance_id)' not in jobs
assert "'servers:auto_renew'" in (root/'scripts/upgrade-panel.sh').read_text()
print('v1.0.8 release + Mobile API contracts: ok')
PY

echo "[5/5] Existing service behavior preserved"
grep -q 'server.auto_renew.completed' panel/app/lifecycle.py
grep -q 'data-auto-renew-form' panel/app/templates/server_detail.html
grep -q '_CACHE_SECONDS = 3.0' agent/natvps_agent/metrics.py
echo "v1.0.8 checks: ok"
