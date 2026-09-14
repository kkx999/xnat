#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

p = Path(__file__).resolve().parent / "apply-release-1.0.2-panel.py"
s = p.read_text(encoding="utf-8")
start_marker = "nodes = regex_once(\n    nodes,\n    r'''def _signature"
end_marker = "nodes = regex_once(\n    nodes,\n    r'''def host_request"
start = s.find(start_marker)
end = s.find(end_marker, start + 1)
if start < 0 or end < 0:
    if "old_signature = (" in s:
        print("[skip] signature patcher already repaired")
        raise SystemExit(0)
    raise SystemExit("cannot locate signature transformation block")

replacement = r"""old_signature = (
    'def _signature(token: str, timestamp: str, method: str, path: str, body: bytes) -> str:\n'
    '    digest = hashlib.sha256(body).hexdigest()\n'
    '    message = f"{timestamp}\\n{method.upper()}\\n{path}\\n{digest}".encode("utf-8")\n'
    '    return hmac.new(token.encode("utf-8"), message, hashlib.sha256).hexdigest()\n'
)
nodes = replace_once(
    nodes,
    old_signature,
    '''def _signature(token: str, timestamp: str, method: str, path: str, body: bytes, nonce: str = "") -> str:
    digest = hashlib.sha256(body).hexdigest()
    if nonce:
        message = f"{timestamp}\\n{nonce}\\n{method.upper()}\\n{path}\\n{digest}".encode("utf-8")
    else:
        message = f"{timestamp}\\n{method.upper()}\\n{path}\\n{digest}".encode("utf-8")
    return hmac.new(token.encode("utf-8"), message, hashlib.sha256).hexdigest()


def _peer_certificate_fingerprint(base_url: str, timeout: float = 8.0) -> str:
    parsed = urlparse(base_url)
    if parsed.scheme.lower() != "https":
        return ""
    hostname = parsed.hostname
    if not hostname:
        raise HostAPIError("宿主机 HTTPS URL 缺少主机名")
    port = int(parsed.port or 443)
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    with socket.create_connection((hostname, port), timeout=timeout) as raw:
        with context.wrap_socket(raw, server_hostname=hostname) as tls:
            cert = tls.getpeercert(binary_form=True)
    if not cert:
        raise HostAPIError("无法读取 Host Agent TLS 证书")
    return hashlib.sha256(cert).hexdigest()


def _verify_or_pin_certificate(host: HostNode, base_url: str) -> None:
    if not base_url.lower().startswith("https://"):
        return
    observed = _peer_certificate_fingerprint(base_url)
    pinned = str(host.tls_fingerprint or "").strip().lower().replace(":", "")
    if pinned and not hmac.compare_digest(pinned, observed):
        raise HostAPIError(
            f"Host Agent TLS 证书指纹不匹配：期望 {pinned[:16]}…，实际 {observed[:16]}…"
        )
    if not pinned:
        host.tls_fingerprint = observed
''',
    "signature and certificate pinning",
)
"""
s = s[:start] + replacement + s[end:]
p.write_text(s, encoding="utf-8")
print("[repair] release patcher signature transform")
