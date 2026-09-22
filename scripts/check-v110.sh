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

echo "[1/6] Python syntax"
"$PYTHON_BIN" -m compileall -q panel/app agent/natvps_agent

echo "[2/6] Jinja templates"
"$PYTHON_BIN" - <<'PY'
from jinja2 import Environment, FileSystemLoader
from pathlib import Path
root=Path('panel/app/templates')
env=Environment(loader=FileSystemLoader(str(root)))
for name in env.list_templates():
    env.get_template(name)
print(f"templates: {len(env.list_templates())}")
PY

echo "[3/6] Shell syntax"
while IFS= read -r -d '' script; do
  bash -n "$script"
done < <(find scripts -type f \( -name '*.sh' -o -name 'xnat' -o -name 'xnat-firewall' \) -print0)

echo "[4/6] Release metadata"
"$PYTHON_BIN" - <<'PY'
from pathlib import Path
import json
root=Path('.')
meta=json.loads((root/'release.json').read_text())
assert (root/'VERSION').read_text().strip() == '1.1.0'
assert (root/'panel/VERSION').read_text().strip() == '1.1.0'
assert (root/'agent/VERSION').read_text().strip() == '1.0.5'
assert (root/'agent/API_VERSION').read_text().strip() == '2'
assert meta['release_version'] == '1.1.0'
assert meta['panel_version'] == '1.1.0'
assert meta['agent_version'] == '1.0.5'
assert str(meta['agent_api_version']) == '2'
assert str(meta['mobile_api_version']) == '1'
assert '__version__ = "1.1.0"' in (root/'panel/app/__init__.py').read_text()
agent_init=(root/'agent/natvps_agent/__init__.py').read_text()
assert '__version__ = "1.0.5"' in agent_init
assert '__api_version__ = "2"' in agent_init
assert '当前正式版本：v1.1.0' in (root/'README.md').read_text()
assert '"version": "1.1.0"' in (root/'panel/app/main.py').read_text()
assert 'XNAT v1.1.0 Multi-Node' in (root/'panel/app/templates/base.html').read_text()
assert '1.0.9) UPGRADE_PATH="verified-v1.0.9"' in (root/'scripts/upgrade-panel.sh').read_text()
print('metadata: ok')
PY

echo "[5/6] Delete + port safety contracts"
"$PYTHON_BIN" - <<'PY'
from pathlib import Path
jobs=Path('panel/app/jobs.py').read_text()
deletion=Path('panel/app/deletion.py').read_text()
main=Path('panel/app/main.py').read_text()
nodes=Path('panel/app/nodes.py').read_text()
agent=Path('agent/natvps_agent/main.py').read_text()
detail=Path('panel/app/templates/server_detail.html').read_text()
admin=Path('panel/app/templates/admin.html').read_text()
mobile_docs=Path('docs/MOBILE_API.md').read_text()

run_delete=jobs[jobs.index('def _run_delete'):jobs.index('def _claim_next_job_id')]
assert 'provider.delete(instance_id)' in run_delete
assert run_delete.index('provider.delete(instance_id)') < run_delete.index('finalize_panel_server_removal(')
assert '"panel_only": False' in run_delete
assert '"host_contacted": True' in run_delete
assert 'VPS 删除未完成' in jobs
assert 'server.provision.ssh_port_reallocated' in jobs
assert '_reallocate_ssh_port_after_conflict' in jobs

assert 'panel_only: bool = False' in deletion
assert 'timedelta(days=30)' in deletion
assert 'HostPortLease' in deletion

assert '@app.post("/admin/servers/{server_id}/force-remove")' in main
assert 'admin.server.force_remove_panel' in main
force=main[main.index('def admin_force_remove_server'):main.index('@app.post("/admin/servers/{server_id}/reconcile")')]
assert 'finalize_panel_server_removal(' in force
assert 'panel_only=True' in force
assert 'provider.delete' not in force
assert 'host_request' not in force

assert 'def _live_proxy_ports_on_host' in nodes
assert '"/v1/ports/used"' in nodes
assert 'port in live_used' in nodes
assert '@app.get("/v1/ports/used")' in agent
assert 'current_proxy_public_ports_by_protocol' in agent
delete_ep=agent[agent.index('@app.delete("/v1/instances/{instance_id}")'):agent.index('@app.post("/v1/instances/{instance_id}/ports")')]
assert '_strict_delete_instance(instance_id)' in delete_ep
assert 'verified_absent' in delete_ep

assert '先永久删除宿主机上的真实实例' in detail
assert '强制从 Panel 移除' in admin
assert 'Host-first' in mobile_docs
print('delete + port safety: ok')
PY

echo "[6/6] Existing contracts preserved"
"$PYTHON_BIN" - <<'PY'
from pathlib import Path
mobile=Path('panel/app/mobile_api.py').read_text()
assert '@router.post("/servers/{server_id}/auto-renew")' in mobile
assert '"auto_renew": bool(getattr(server, "auto_renew", False))' in mobile
assert '@router.post("/servers/{server_id}/delete")' in mobile
assert '_CACHE_SECONDS = 3.0' in Path('agent/natvps_agent/metrics.py').read_text()
css=Path('panel/app/static/style.css').read_text()
assert 'Admin / Client visual unification' in css
assert 'v1.1.0 deletion modes' in css
print('existing contracts: ok')
PY

echo "v1.1.0 checks: ok"
