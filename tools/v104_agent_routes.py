from pathlib import Path
R=Path(__file__).resolve().parents[1]
p=R/'agent/natvps_agent/main.py'; s=p.read_text()
anchor='from pydantic import BaseModel, Field\n'
if s.count(anchor)!=1: raise SystemExit('import anchor')
s=s.replace(anchor,anchor+'from .metrics import collect as collect_instance_metrics\n',1)
route='''\n\n@app.get("/v1/instances/{instance_id}/metrics")\ndef metrics(instance_id: str):\n    require_instance(instance_id)\n    try:\n        return collect_instance_metrics(\n            instance_id, run=run, instance_exists=instance_exists,\n            instance_status=instance_status, resource_snapshot=instance_resource_snapshot,\n        )\n    except HTTPException:\n        raise\n    except Exception as exc:\n        raise HTTPException(503, f"实时资源暂不可用: {str(exc)[:500]}")\n'''
needle='\n\n@app.post("/v1/instances/{instance_id}/resources")\n'
if s.count(needle)!=1: raise SystemExit('resources anchor')
s=s.replace(needle,route+needle,1)
p.write_text(s)
