from pathlib import Path
p=Path(__file__).resolve().parents[1]/'scripts/upgrade-panel.sh'
s=p.read_text()
line='  1.0.3) UPGRADE_PATH="verified-v1.0.3" ;;\n'
if line not in s:
    anchor='  1.0.2) UPGRADE_PATH="verified-v1.0.2" ;;\n'
    if anchor not in s: raise SystemExit('upgrade anchor missing')
    s=s.replace(anchor,line+anchor,1)
p.write_text(s)
