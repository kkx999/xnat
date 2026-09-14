from pathlib import Path
p=Path(__file__).resolve().parents[1]/'panel/app/providers/remote.py'
s=p.read_text()
s=s.replace('from __future__ import annotations\n\n','from __future__ import annotations\n\nimport threading\nimport time\n\n',1)
a='from .base import NetworkStats, Provider, ProviderState, ProvisionResult\n'
if s.count(a)!=1: raise SystemExit('import anchor')
s=s.replace(a,a+'\n_METRICS_LOCK = threading.Lock()\n_METRICS_CACHE = {}\n',1)
anchor='    def resize_resources(self, instance_id: str, cpu: int, memory_mb: int, disk_gb: float) -> dict:\n'
method='''    def instance_metrics(self, instance_id: str) -> dict:\n        now = time.monotonic()\n        with _METRICS_LOCK:\n            cached = _METRICS_CACHE.get(instance_id)\n            if cached and now - cached[0] < 3.0:\n                return dict(cached[1])\n        host = self._host_for_instance(instance_id)\n        data = host_request(host, "GET", f"/v1/instances/{instance_id}/metrics", timeout=18)\n        payload = dict(data or {})\n        with _METRICS_LOCK:\n            _METRICS_CACHE[instance_id] = (now, payload)\n        return dict(payload)\n\n'''
if s.count(anchor)!=1: raise SystemExit('resize anchor')
p.write_text(s.replace(anchor,method+anchor,1))
