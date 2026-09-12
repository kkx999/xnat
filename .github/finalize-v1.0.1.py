from pathlib import Path

p = Path('scripts/xnat')
s = p.read_text(encoding='utf-8')

repls = [
    (
        'local retired_domain="${1:-}" need80=true need443=true need_cert=false cert_cn="localhost"',
        'local retired_domain="${1:-}" need80=true need443=true need_cert=false retired_https=true cert_cn="localhost"',
    ),
    (
        '        rm -rf "$NGINX_UNINSTALL_GUARD_SSL_DIR"\n        need443=false\n        retired_domain=""',
        '        rm -rf "$NGINX_UNINSTALL_GUARD_SSL_DIR"\n        need443=false\n        retired_https=false',
    ),
    (
        '''    if [[ -n "$retired_domain" ]]; then
      cat <<EOF_RETIRED_DOMAIN
# XNAT retired Panel hostname guard. It intentionally contains no Panel data.
server {
    listen 80;
    listen [::]:80;
    server_name ${retired_domain};
    return 444;
}

server {
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name ${retired_domain};
    ssl_certificate ${NGINX_UNINSTALL_GUARD_SSL_DIR}/fullchain.pem;
    ssl_certificate_key ${NGINX_UNINSTALL_GUARD_SSL_DIR}/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    return 444;
}
EOF_RETIRED_DOMAIN
    fi
''',
        '''    if [[ -n "$retired_domain" ]]; then
      cat <<EOF_RETIRED_DOMAIN_HTTP
# XNAT retired Panel hostname guard. It intentionally contains no Panel data.
server {
    listen 80;
    listen [::]:80;
    server_name ${retired_domain};
    return 444;
}
EOF_RETIRED_DOMAIN_HTTP
    fi
    if [[ -n "$retired_domain" && "$retired_https" == true ]]; then
      cat <<EOF_RETIRED_DOMAIN_HTTPS
server {
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name ${retired_domain};
    ssl_certificate ${NGINX_UNINSTALL_GUARD_SSL_DIR}/fullchain.pem;
    ssl_certificate_key ${NGINX_UNINSTALL_GUARD_SSL_DIR}/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    return 444;
}
EOF_RETIRED_DOMAIN_HTTPS
    fi
''',
    ),
    (
        "    if [[ \"$need443\" == true ]]; then\n      cat <<EOF_GUARD_443",
        "    if [[ \"$need443\" == true && \"$retired_https\" == true ]]; then\n      cat <<EOF_GUARD_443",
    ),
    (
        '    systemctl disable --now xnat-panel.service xnat-maintenance.timer >/dev/null 2>&1 || true\n',
        '    systemctl disable --now xnat-panel.service xnat-maintenance.timer >/dev/null 2>&1 || true\n    systemctl disable --now xnat-cloudflare-refresh.timer >/dev/null 2>&1 || true\n',
    ),
]

for old, new in repls:
    if s.count(old) != 1:
        raise SystemExit(f'scripts/xnat expected one patch anchor, got {s.count(old)} for {old[:80]!r}')
    s = s.replace(old, new, 1)

p.write_text(s, encoding='utf-8')

ck = Path('scripts/check.sh')
c = ck.read_text(encoding='utf-8')
anchor = "grep -q 'XNAT retired Panel hostname guard' scripts/xnat\n"
extra = "grep -q 'retired_https=false' scripts/xnat\ngrep -q 'systemctl disable --now xnat-cloudflare-refresh.timer' scripts/xnat\n"
if c.count(anchor) != 1:
    raise SystemExit('scripts/check.sh v1.0.1 uninstall guard anchor missing')
c = c.replace(anchor, anchor + extra, 1)
ck.write_text(c, encoding='utf-8')
