from pathlib import Path

p = Path('scripts/check.sh')
s = p.read_text(encoding='utf-8')
replacements = [
    ("assert '当前正式版本：v1.0.0' in readme", "assert '当前正式版本：v1.0.1' in readme"),
    ("assert (root/'VERSION').read_text().strip() == '1.0.0'", "assert (root/'VERSION').read_text().strip() == '1.0.1'"),
    ("assert (root/'panel/VERSION').read_text().strip() == '1.0.0'", "assert (root/'panel/VERSION').read_text().strip() == '1.0.1'"),
    ("assert meta['release_version']=='1.0.0'", "assert meta['release_version']=='1.0.1'"),
    ("assert meta['panel_version']=='1.0.0'", "assert meta['panel_version']=='1.0.1'"),
    ("print('v1.0.0 baseline contracts: ok')", "print('v1.0.1 baseline contracts: ok')"),
]
for old, new in replacements:
    if s.count(old) != 1:
        raise SystemExit(f'check.sh expected one match for {old!r}, got {s.count(old)}')
    s = s.replace(old, new, 1)
p.write_text(s, encoding='utf-8')

docs = Path('docs/MOBILE_API.md')
d = docs.read_text(encoding='utf-8')
old = 'XNAT Panel `v1.0.0` 保持 **Mobile API v1**。'
new = 'XNAT Panel `v1.0.1` 保持 **Mobile API v1**。'
if d.count(old) != 1:
    raise SystemExit(f'MOBILE_API.md expected one Panel version marker, got {d.count(old)}')
docs.write_text(d.replace(old, new, 1), encoding='utf-8')
