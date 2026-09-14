from pathlib import Path
R=Path(__file__).resolve().parents[1]
p=R/'agent/natvps_agent/main.py'; s=p.read_text()
def one(a,b):
 global s
 if s.count(a)!=1: raise SystemExit(f'anchor {s.count(a)} {a[:60]}')
 s=s.replace(a,b,1)
one('import json\n','import json\nimport logging\n')
one('AGENT_VERSION = "1.0.3"','AGENT_VERSION = "1.0.4"')
one('_SEEN_NONCES: dict[str, int] = {}\n','_SEEN_NONCES: dict[str, int] = {}\n_METRICS_LOCK = threading.Lock()\n_METRICS_CACHE_SECONDS = 3.0\n_INSTANCE_METRICS_CACHE: dict[str, tuple[float, dict]] = {}\n_INSTANCE_METRICS_SAMPLE: dict[str, tuple[float, int, int, int]] = {}\n_INSTANCE_DISK_SAMPLE: dict[str, tuple[float, int, int]] = {}\n')
one('app = FastAPI(title="NAT VPS Host Agent", version=AGENT_VERSION)\n','app = FastAPI(title="NAT VPS Host Agent", version=AGENT_VERSION)\n\nclass _MetricsAccessFilter(logging.Filter):\n    def filter(self, record):\n        try: return "/metrics" not in record.getMessage()\n        except Exception: return True\n\nlogging.getLogger("uvicorn.access").addFilter(_MetricsAccessFilter())\n')
p.write_text(s)
(R/'agent/VERSION').write_text('1.0.4\n')
q=R/'agent/natvps_agent/__init__.py'; t=q.read_text(); t=t.replace('__version__ = "1.0.3"','__version__ = "1.0.4"'); q.write_text(t)
