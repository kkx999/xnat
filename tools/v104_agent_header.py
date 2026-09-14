from pathlib import Path
R=Path(__file__).resolve().parents[1]
p=R/'agent/natvps_agent/main.py'; s=p.read_text()
old='AGENT_VERSION = "1.0.3"'
new='AGENT_VERSION = "1.0.4"'
if old in s:
    s=s.replace(old,new,1)
elif new not in s:
    raise SystemExit('agent version anchor missing')
p.write_text(s)
(R/'agent/VERSION').write_text('1.0.4\n')
q=R/'agent/natvps_agent/__init__.py'; t=q.read_text()
if '__version__ = "1.0.3"' in t:
    t=t.replace('__version__ = "1.0.3"','__version__ = "1.0.4"',1)
elif '__version__ = "1.0.4"' not in t:
    raise SystemExit('agent package version anchor missing')
q.write_text(t)
