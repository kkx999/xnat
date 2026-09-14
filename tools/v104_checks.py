from pathlib import Path
p=Path(__file__).resolve().parents[1]/'scripts/check.sh'
s=p.read_text()
repls={
"if grep -RInE 'v1\\.0\\.4|testing-v|":"if grep -RInE 'v1\\.0\\.5|testing-v|",
"assert '当前正式版本：v1.0.3' in readme":"assert '当前正式版本：v1.0.4' in readme",
"assert (root/'VERSION').read_text().strip() == '1.0.3'":"assert (root/'VERSION').read_text().strip() == '1.0.4'",
"assert (root/'panel/VERSION').read_text().strip() == '1.0.3'":"assert (root/'panel/VERSION').read_text().strip() == '1.0.4'",
"assert (root/'agent/VERSION').read_text().strip() == '1.0.3'":"assert (root/'agent/VERSION').read_text().strip() == '1.0.4'",
"assert meta['release_version']=='1.0.3'":"assert meta['release_version']=='1.0.4'",
"assert meta['panel_version']=='1.0.3'":"assert meta['panel_version']=='1.0.4'",
"assert meta['agent_version']=='1.0.3'":"assert meta['agent_version']=='1.0.4'",
"print('v1.0.3 baseline contracts: ok')":"print('v1.0.4 baseline contracts: ok')",
}
for a,b in repls.items():
    if a not in s: raise SystemExit(f'missing check anchor: {a}')
    s=s.replace(a,b,1)
extra=r'''

# v1.0.4 live server metrics contract
grep -q 'AGENT_VERSION = "1.0.4"' agent/natvps_agent/main.py
grep -q 'from .metrics import collect as collect_instance_metrics' agent/natvps_agent/main.py
grep -q '@app.get("/v1/instances/{instance_id}/metrics")' agent/natvps_agent/main.py
grep -q '_CACHE_SECONDS = 3.0' agent/natvps_agent/metrics.py
grep -q '_DISK_FALLBACK_SECONDS = 30.0' agent/natvps_agent/metrics.py
grep -q 'network_rx_bps' agent/natvps_agent/metrics.py
grep -q 'network_tx_bps' agent/natvps_agent/metrics.py
grep -q 'def instance_metrics' panel/app/providers/base.py
grep -q 'def instance_metrics' panel/app/providers/remote.py
grep -q 'request.query_params.get("metrics") == "1"' panel/app/main.py
grep -q 'request.query_params.get("metrics") == "1"' panel/app/mobile_api.py
grep -q 'data-server-live-metrics' panel/app/templates/server_detail.html
grep -q 'data-metrics-url="/servers/{{ server.id }}?metrics=1"' panel/app/templates/server_detail.html
grep -q 'server-live-grid' panel/app/static/style.css
grep -q 'border-radius:999px' panel/app/static/style.css
grep -q 'setTimeout(load,5000)' panel/app/static/client.js
grep -q 'visibilitychange' panel/app/static/client.js
grep -q 'metrics=1' panel/app/main.py
grep -q '"/metrics" not in record.getMessage()' agent/natvps_agent/main.py
grep -q '1.0.3) UPGRADE_PATH="verified-v1.0.3"' scripts/upgrade-panel.sh
python3 - <<'PYV104METRICS'
from pathlib import Path
import json
root=Path('.')
meta=json.loads((root/'release.json').read_text())
assert meta['release_version'] == meta['panel_version'] == '1.0.4'
assert meta['agent_version'] == '1.0.4'
assert str(meta['agent_api_version']) == '2'
assert str(meta['mobile_api_version']) == '1'
tpl=(root/'panel/app/templates/server_detail.html').read_text()
assert tpl.index('data-server-live-metrics') < tpl.index('traffic-usage-panel')
css=(root/'panel/app/static/style.css').read_text().split('v1.0.4 server live metrics',1)[1]
assert 'grid-template-columns:repeat(2,minmax(0,1fr))' in css
assert '.server-live-progress{height:8px' in css
assert 'border-radius:999px' in css
agent_metrics=(root/'agent/natvps_agent/metrics.py').read_text()
assert 'write_text' not in agent_metrics and 'sqlite' not in agent_metrics.lower()
assert '_CACHE_SECONDS = 3.0' in agent_metrics
js=(root/'panel/app/static/client.js').read_text()
assert 'document.hidden' in js and 'pagehide' in js
print('v1.0.4 live server metrics contract: ok')
PYV104METRICS
'''
if 'v1.0.4 live server metrics contract' in s: raise SystemExit('guard already present')
s=s.rstrip()+extra+'\n'
p.write_text(s)
