from pathlib import Path
p=Path(__file__).resolve().parents[1]/'panel/app/providers/base.py'
s=p.read_text()
a='    @abstractmethod\n    def resize_resources(self, instance_id: str, cpu: int, memory_mb: int, disk_gb: float) -> dict:\n        raise NotImplementedError\n'
b='    def instance_metrics(self, instance_id: str) -> dict:\n        return {"available": False, "status": "unavailable"}\n\n'+a
if s.count(a)!=1: raise SystemExit('anchor')
p.write_text(s.replace(a,b,1))
