#!/usr/bin/env python3
from __future__ import annotations

import json
import py_compile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def text(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"[FAIL] {message}")
    print(f"[OK] {message}")


def compile_tree(rel: str) -> None:
    base = ROOT / rel
    files = sorted(base.rglob("*.py"))
    require(bool(files), f"{rel} contains Python files")
    for file in files:
        py_compile.compile(str(file), doraise=True)
    print(f"[OK] compiled {len(files)} Python files under {rel}")


release = json.loads(text("release.json"))
require(release.get("release_version") == "1.0.2", "release version is 1.0.2")
require(release.get("panel_version") == "1.0.2", "Panel version is 1.0.2")
require(release.get("agent_version") == "1.0.1", "Host Agent version is 1.0.1")
require(release.get("agent_api_version") == "2", "Agent API version is 2")
require(set(release.get("supported_agent_api_versions") or []) == {"1", "2"}, "Panel supports staged Agent API 1/2 upgrade")
require(release.get("mobile_api_version") == "1", "Mobile API remains v1")
require(text("VERSION").strip() == "1.0.2", "VERSION matches release")
require('__version__ = "1.0.2"' in text("panel/app/__init__.py"), "Panel package version matches release")

compile_tree("panel/app")
compile_tree("agent/natvps_agent")

agent = text("agent/natvps_agent/main.py")
for needle, label in [
    ('AGENT_VERSION = "1.0.1"', "Agent version constant"),
    ('AGENT_API_VERSION = "2"', "Agent API constant"),
    ('X-NAT-Nonce', "API2 nonce header"),
    ('_consume_nonce', "replay cache"),
    ('user.xnat.server_id', "stable XNAT instance identity"),
    ('idempotent_replay', "idempotent provision replay"),
    ('xnat-old-', "rollback-safe reinstall backup"),
    ('iproute2', "minimal-image ss/ip dependency"),
    ('validate_image_resources', "Agent image/disk final guard"),
    ('preflight_image', "Agent reinstall/provision preflight"),
]:
    require(needle in agent, label)

nodes = text("panel/app/nodes.py")
for needle, label in [
    ('SUPPORTED_AGENT_API_VERSIONS = {"1", "2"}', "Panel Agent API compatibility"),
    ('tls_fingerprint', "TLS certificate fingerprint pinning"),
    ('HostPortLease', "Host port lease allocator"),
    ('X-NAT-Nonce', "Panel API2 nonce signing"),
    ('PANEL_VERSION', "Panel User-Agent uses real version"),
]:
    require(needle in nodes, label)
require('XNAT-Panel/1.6.3' not in nodes, "stale 1.6.3 User-Agent removed")

models = text("panel/app/models.py")
require('class HostPortLease(Base):' in models, "Host port reservation model")
require('min_disk_gb' in models, "SystemImage minimum disk field")
require('tls_fingerprint' in models, "Host TLS fingerprint field")

jobs = text("panel/app/jobs.py")
require('_claim_next_job_id' in jobs and 'update(Job)' in jobs, "atomic background job claim")
require('validate_image_resources' in jobs, "background job image guard")

reconcile = text("panel/app/reconcile.py")
remote = text("panel/app/providers/remote.py")
require('recover_instance' in remote, "remote provider orphan recovery")
require('recover_instance' in reconcile, "reconciliation orphan recovery")

main = text("panel/app/main.py")
mobile = text("panel/app/mobile_api.py")
require('validate_image_resources' in main, "Web/admin prequeue image validation")
require('validate_image_resources' in mobile, "Mobile prequeue image validation")
require('from .services.image_policy' in main, "Web routes use service-layer policy")
require('from .services.image_policy' in mobile, "Mobile API uses service-layer policy")

for obsolete in (
    "scripts/upgrade-panel-from-v1.0.2.sh",
    "scripts/upgrade-panel-from-v1.1.0.sh",
    "scripts/upgrade-panel-from-v1.1.1.sh",
):
    require(not (ROOT / obsolete).exists(), f"obsolete dev upgrade script removed: {obsolete}")

print("[PASS] XNAT 1.0.2 release self-check complete")
