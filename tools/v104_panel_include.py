from pathlib import Path
p=Path(__file__).resolve().parents[1]/'panel/app/main.py'
s=p.read_text()
a='from .mobile_api import router as mobile_api_router\n'
b=a+'from .live_metrics import router as live_metrics_router\n'
if s.count(a)!=1: raise SystemExit('import anchor')
s=s.replace(a,b,1)
a='app.include_router(mobile_api_router)\n'
b=a+'app.include_router(live_metrics_router)\n'
if s.count(a)!=1: raise SystemExit('router anchor')
s=s.replace(a,b,1)
p.write_text(s)
