from pathlib import Path
p=Path(__file__).resolve().parents[1]/'panel/app/providers/remote.py'
s=p.read_text()
anchor='''    def resize_resources(self, instance_id: str, cpu: int, memory_mb: int, disk_gb: float) -> dict:\n'''
method='''    def instance_metrics(self, instance_id: str) -> dict:\n        host = self._host_for_instance(instance_id)\n        data = host_request(host, "GET", f"/v1/instances/{instance_id}/metrics", timeout=18)\n        return dict(data or {})\n\n'''
if s.count(anchor)!=1: raise SystemExit(f'anchor {s.count(anchor)}')
p.write_text(s.replace(anchor,method+anchor,1))
