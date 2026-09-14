from pathlib import Path
p=Path(__file__).resolve().parents[1]/'panel/app/main.py'
s=p.read_text()
anchor='''        user = login_required(request, db)\n        server = active_server_for_user(db, user, server_id)\n\n        traffic_stat = None\n'''
insert='''        user = login_required(request, db)\n        server = active_server_for_user(db, user, server_id)\n\n        if request.query_params.get("metrics") == "1":\n            if not server.provider_instance_id:\n                return JSONResponse({"available": False, "status": server.status or "provisioning"})\n            try:\n                data = provider.instance_metrics(server.provider_instance_id)\n            except Exception:\n                return JSONResponse({"available": False, "status": "unavailable"})\n            allowed = {"available", "status", "cpu_percent", "memory_used_bytes", "memory_total_bytes", "memory_percent", "disk_used_bytes", "disk_total_bytes", "disk_percent", "network_rx_bps", "network_tx_bps", "sampled_at", "sampling"}\n            return JSONResponse({key: value for key, value in dict(data or {}).items() if key in allowed})\n\n        traffic_stat = None\n'''
if s.count(anchor)!=1: raise SystemExit(f'anchor {s.count(anchor)}')
p.write_text(s.replace(anchor,insert,1))
