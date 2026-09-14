from pathlib import Path
p=Path(__file__).resolve().parents[1]/'panel/app/main.py'
s=p.read_text()
if 'import logging\n' not in s:
    s=s.replace('import json\n','import json\nimport logging\n',1)
a='app = FastAPI(title=APP_NAME, lifespan=lifespan)\n'
b='''app = FastAPI(title=APP_NAME, lifespan=lifespan)\n\nclass _LiveMetricsAccessFilter(logging.Filter):\n    def filter(self, record):\n        try:\n            return "metrics=1" not in record.getMessage()\n        except Exception:\n            return True\n\nlogging.getLogger("uvicorn.access").addFilter(_LiveMetricsAccessFilter())\n'''
if s.count(a)!=1: raise SystemExit(f'app anchor {s.count(a)}')
s=s.replace(a,b,1)
p.write_text(s)
