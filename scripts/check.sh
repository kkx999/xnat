#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

cleanup(){
  find panel agent -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true
  find panel agent -type f -name '*.pyc' -delete 2>/dev/null || true
}
trap cleanup EXIT

echo "[1/7] Python syntax"
python3 -m compileall -q panel/app agent/natvps_agent

echo "[2/7] Jinja templates"
python3 - <<'PY'
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
grep -q 'xnat doctor' README.md || true

# v1.0.0 Host UX contract: NAT user port range is configured only after the
# node connects to Panel, not during Host installation.
! grep -q 'HOST_PORT_START' scripts/install-host.sh
! grep -q 'HOST_PORT_END' scripts/install-host.sh
grep -q '/v1/config/nat-port-pool' agent/natvps_agent/main.py
grep -q '尚未配置 NAT 端口池' panel/app/nodes.py
grep -q '保存并同步到 Agent' panel/app/templates/admin.html
# v1.0.0 final admin UX contracts.
grep -q '/admin/servers/{server_id}/traffic/quota' panel/app/main.py
grep -q '/admin/servers/{server_id}/expiry' panel/app/main.py
grep -q '磁盘仅支持扩容' panel/app/templates/admin.html
grep -q 'USDT 自动充值' panel/app/templates/admin.html
grep -q 'section == "notifications"' panel/app/templates/admin.html
grep -q '发送 Telegram 测试' panel/app/templates/admin.html
! grep -q 'UniqueConstraint("public_port", "protocol"' panel/app/models.py

echo "[5/7] Clean baseline guard"
if find . -maxdepth 3 -type f | grep -Ei '(testing|rc[0-9]|patch-panel|patch-management|upgrade-from-v1\.0\.3)' >/tmp/xnat-clean-guard.txt; then
  echo "[ERROR] Found testing/RC/legacy artifacts:"
  cat /tmp/xnat-clean-guard.txt
  exit 1
fi
if grep -RInE 'v1\.0\.3|v1\.0\.4|testing-v|RC1|候选版本' \
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

echo "XNAT repository checks passed."
