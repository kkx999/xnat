#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

# This helper is release-build-only and is removed before the final merge.
root = Path(__file__).resolve().parents[1]
schema_path = root / "panel/app/schema.py"
s = schema_path.read_text(encoding="utf-8")

replacements = {
    '''                    'WHERE LOWER("alias") LIKE 'images:alpine/%''\n''': '''                    "WHERE LOWER(\\"alias\\") LIKE 'images:alpine/%'"\n''',
    '''                    'WHERE LOWER("alias") LIKE 'images:ubuntu/%' OR LOWER("alias") LIKE 'images:debian/%''\n''': '''                    "WHERE LOWER(\\"alias\\") LIKE 'images:ubuntu/%' OR LOWER(\\"alias\\") LIKE 'images:debian/%'"\n''',
}
changed = False
for old, new in replacements.items():
    if old in s:
        s = s.replace(old, new)
        changed = True

if changed:
    schema_path.write_text(s, encoding="utf-8")
    print("[repair] generated schema image-policy SQL quoting")
else:
    print("[skip] generated schema quoting already valid or markers absent")
