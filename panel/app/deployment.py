from __future__ import annotations

import json
import os
from datetime import timezone
from pathlib import Path
from urllib.parse import urlparse

from cryptography import x509


STATE_FILE = Path(os.getenv("XNAT_DEPLOYMENT_STATE", "/etc/xnat/deployment.json"))


def _certificate_expiry(path: str) -> str:
    if not path:
        return ""
    cert_path = Path(path)
    if not cert_path.is_file():
        return ""
    try:
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        expiry = getattr(cert, "not_valid_after_utc", None)
        if expiry is None:
            expiry = cert.not_valid_after.replace(tzinfo=timezone.utc)
        return expiry.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return ""


def _domain_from_base_url(base_url: str) -> str:
    try:
        parsed = urlparse((base_url or "").strip())
        return parsed.hostname or ""
    except Exception:
        return ""


def deployment_status(public_base_url: str = "") -> dict:
    """Read XNAT's root-managed deployment state without executing system commands.

    The web application only displays this information. Nginx, certificates and
    domain changes remain root-only operations through the `xnat` CLI.
    """
    data: dict = {}
    try:
        if STATE_FILE.is_file():
            loaded = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
    except Exception:
        data = {}

    domain = str(data.get("domain") or "").strip() or _domain_from_base_url(public_base_url)
    certificate = str(data.get("certificate") or "").strip()
    https_enabled = bool(data.get("https_enabled"))
    if not https_enabled and (public_base_url or "").lower().startswith("https://"):
        https_enabled = True

    origin_host = str(data.get("origin_host") or os.getenv("PANEL_BIND_HOST", "127.0.0.1"))
    try:
        origin_port = int(data.get("origin_port") or os.getenv("PANEL_PORT", "8000"))
    except (TypeError, ValueError):
        origin_port = 8000

    return {
        "managed": bool(data.get("nginx_managed")),
        "domain": domain,
        "https_enabled": https_enabled,
        "certificate_type": str(data.get("certificate_type") or ("Let's Encrypt" if certificate else "")),
        "certificate": certificate,
        "certificate_expires": _certificate_expiry(certificate),
        "cloudflare_detected": bool(data.get("cloudflare_detected")),
        "origin_lock_enabled": bool(data.get("origin_lock_enabled")),
        "origin": f"{origin_host}:{origin_port}",
        "updated_at": str(data.get("updated_at") or ""),
    }
