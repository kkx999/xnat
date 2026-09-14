from pathlib import Path
R=Path(__file__).resolve().parents[1]
p=R/'agent/natvps_agent/main.py'; s=p.read_text()
old='AGENT_VERSION = "1.0.3"'
new='AGENT_VERSION = "1.0.4"'
if old in s:
    s=s.replace(old,new,1)
elif new not in s:
    raise SystemExit('agent version anchor missing')
if 'import logging\n' not in s:
    s=s.replace('import json\n','import json\nimport logging\n',1)
marker='class _MetricsAccessFilter(logging.Filter):'
if marker not in s:
    anchor='app = FastAPI(title="NAT VPS Host Agent", version=AGENT_VERSION)\n'
    block='''app = FastAPI(title="NAT VPS Host Agent", version=AGENT_VERSION)\n\nclass _MetricsAccessFilter(logging.Filter):\n    def filter(self, record):\n        try:\n            return "/metrics HTTP/" not in record.getMessage()\n        except Exception:\n            return True\n\nlogging.getLogger("uvicorn.access").addFilter(_MetricsAccessFilter())\n'''
    if s.count(anchor)!=1:
        raise SystemExit('agent app anchor missing')
    s=s.replace(anchor,block,1)
p.write_text(s)
(R/'agent/VERSION').write_text('1.0.4\n')
q=R/'agent/natvps_agent/__init__.py'; t=q.read_text()
if '__version__ = "1.0.3"' in t:
    t=t.replace('__version__ = "1.0.3"','__version__ = "1.0.4"',1)
elif '__version__ = "1.0.4"' not in t:
    raise SystemExit('agent package version anchor missing')
q.write_text(t)
