from pathlib import Path
import json, re


def replace_once(path, old, new):
    p = Path(path)
    s = p.read_text(encoding="utf-8")
    if s.count(old) != 1:
        raise SystemExit(f"{path}: expected exactly one match for {old!r}, got {s.count(old)}")
    p.write_text(s.replace(old, new, 1), encoding="utf-8")


Path("VERSION").write_text("1.0.1\n", encoding="utf-8")
Path("panel/VERSION").write_text("1.0.1\n", encoding="utf-8")
meta = json.loads(Path("release.json").read_text(encoding="utf-8"))
meta["release_version"] = "1.0.1"
meta["panel_version"] = "1.0.1"
meta["agent_version"] = "1.0.0"
Path("release.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

replace_once("panel/app/__init__.py", '__version__ = "1.0.0"', '__version__ = "1.0.1"')
replace_once("panel/app/main.py", '"version": "1.0.0"', '"version": "1.0.1"')

up = Path("scripts/upgrade-panel.sh")
s = up.read_text(encoding="utf-8")
needle = 'case "$CURRENT_VERSION" in\n  1.6.2) UPGRADE_PATH="verified-v1.6.2" ;;'
repl = 'case "$CURRENT_VERSION" in\n  1.0.0) UPGRADE_PATH="verified-v1.0.0" ;;\n  1.6.2) UPGRADE_PATH="verified-v1.6.2" ;;'
if needle not in s:
    raise SystemExit("upgrade-panel.sh: version switch anchor missing")
up.write_text(s.replace(needle, repl, 1), encoding="utf-8")

p = Path("scripts/xnat")
s = p.read_text(encoding="utf-8")
replace_anchor = 'NODE_CONFIG_FILE="${XNAT_NODE_CONFIG_FILE:-/etc/xnat/node.json}"\n'
vars_block = '''NODE_CONFIG_FILE="${XNAT_NODE_CONFIG_FILE:-/etc/xnat/node.json}"
PANEL_CRED_FILE="${XNAT_PANEL_CRED_FILE:-/root/xnat-panel-credentials.txt}"
NGINX_UNINSTALL_GUARD="${XNAT_NGINX_UNINSTALL_GUARD:-/etc/nginx/conf.d/00-xnat-default-deny.conf}"
NGINX_UNINSTALL_GUARD_SSL_DIR="${XNAT_NGINX_UNINSTALL_GUARD_SSL_DIR:-/etc/nginx/ssl/xnat-default-deny}"
'''
if s.count(replace_anchor) != 1:
    raise SystemExit("scripts/xnat: variable anchor mismatch")
s = s.replace(replace_anchor, vars_block, 1)

helper = r'''remove_uninstall_nginx_guard(){
  rm -f "$NGINX_UNINSTALL_GUARD"
  rm -rf "$NGINX_UNINSTALL_GUARD_SSL_DIR"
}

nginx_default_port_exists(){
  local port="$1"
  nginx -T 2>/dev/null | awk -v p="$port" '
    /^[[:space:]]*listen[[:space:]]/ && /default_server/ {
      token=$2
      gsub(/;/, "", token)
      if (token == p || token ~ (":" p "$")) found=1
    }
    END { exit(found ? 0 : 1) }
  '
}

write_uninstall_nginx_guard(){
  have nginx || return 0
  local need80=true need443=true

  rm -f "$NGINX_UNINSTALL_GUARD"
  nginx_default_port_exists 80 && need80=false || true
  nginx_default_port_exists 443 && need443=false || true
  if [[ "$need80" != true && "$need443" != true ]]; then
    rm -rf "$NGINX_UNINSTALL_GUARD_SSL_DIR"
    return 0
  fi

  install -d -m 0755 "$(dirname "$NGINX_UNINSTALL_GUARD")"
  if [[ "$need443" == true ]]; then
    install -d -m 0700 "$NGINX_UNINSTALL_GUARD_SSL_DIR"
    if [[ ! -s "$NGINX_UNINSTALL_GUARD_SSL_DIR/fullchain.pem" || ! -s "$NGINX_UNINSTALL_GUARD_SSL_DIR/privkey.pem" ]]; then
      openssl req -x509 -nodes -newkey rsa:2048 -sha256 -days 3650 \
        -subj '/CN=localhost' \
        -keyout "$NGINX_UNINSTALL_GUARD_SSL_DIR/privkey.pem" \
        -out "$NGINX_UNINSTALL_GUARD_SSL_DIR/fullchain.pem" >/dev/null 2>&1
      chmod 0600 "$NGINX_UNINSTALL_GUARD_SSL_DIR/privkey.pem"
      chmod 0644 "$NGINX_UNINSTALL_GUARD_SSL_DIR/fullchain.pem"
    fi
  else
    rm -rf "$NGINX_UNINSTALL_GUARD_SSL_DIR"
  fi

  {
    if [[ "$need80" == true ]]; then
      cat <<'EOF_GUARD_80'
# Installed by XNAT after Panel removal to prevent unmatched hostnames from
# falling through to another virtual host on the same Nginx instance.
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;
    return 444;
}
EOF_GUARD_80
    fi
    if [[ "$need443" == true ]]; then
      cat <<EOF_GUARD_443
server {
    listen 443 ssl default_server;
    listen [::]:443 ssl default_server;
    server_name _;
    ssl_certificate ${NGINX_UNINSTALL_GUARD_SSL_DIR}/fullchain.pem;
    ssl_certificate_key ${NGINX_UNINSTALL_GUARD_SSL_DIR}/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    return 444;
}
EOF_GUARD_443
    fi
  } > "$NGINX_UNINSTALL_GUARD"
  chmod 0644 "$NGINX_UNINSTALL_GUARD"

  if nginx -t >/dev/null 2>&1; then
    systemctl reload nginx >/dev/null 2>&1 || true
  else
    warn "卸载后的 Nginx 默认拒绝站点配置失败，已回滚该保护配置。"
    rm -f "$NGINX_UNINSTALL_GUARD"
    rm -rf "$NGINX_UNINSTALL_GUARD_SSL_DIR"
    nginx -t >/dev/null 2>&1 && systemctl reload nginx >/dev/null 2>&1 || true
  fi
}

'''
anchor = "proxy_locations(){\n"
if s.count(anchor) != 1:
    raise SystemExit("scripts/xnat: proxy anchor mismatch")
s = s.replace(anchor, helper + anchor, 1)

writers = (
    'write_ip_nginx(){\n  ensure_layout',
    'write_domain_http_nginx(){\n  local domain="$1"\n  ensure_layout',
    'write_domain_https_nginx(){\n  local domain="$1" origin_lock="${2:-false}"',
)
for item in writers:
    if item not in s:
        raise SystemExit(f"scripts/xnat: nginx writer anchor missing: {item}")
s = s.replace('write_ip_nginx(){\n  ensure_layout', 'write_ip_nginx(){\n  remove_uninstall_nginx_guard\n  ensure_layout', 1)
s = s.replace('write_domain_http_nginx(){\n  local domain="$1"\n  ensure_layout', 'write_domain_http_nginx(){\n  local domain="$1"\n  remove_uninstall_nginx_guard\n  ensure_layout', 1)
s = s.replace('write_domain_https_nginx(){\n  local domain="$1" origin_lock="${2:-false}"', 'write_domain_https_nginx(){\n  local domain="$1" origin_lock="${2:-false}"\n  remove_uninstall_nginx_guard', 1)

new_uninstall = r'''cmd_uninstall(){
  need_root
  local role phrase expected backup="" uninstall_mode="backup" choice="" panel_domain=""
  role="$(detect_role)"
  if [[ "$role" == panel ]]; then
    echo "此操作会卸载 XNAT Panel、XNAT systemd 服务和 Panel 的 Nginx 站点。"
    echo
    echo "  1  卸载并保留数据备份"
    echo "  2  完全卸载，删除所有 XNAT Panel 数据"
    echo "  0  取消"
    echo
    read -r -p "请选择 [0-2] [1]: " choice
    choice="${choice:-1}"
    case "$choice" in
      1) uninstall_mode="backup"; expected="REMOVE PANEL" ;;
      2)
        uninstall_mode="purge"
        expected="PURGE PANEL"
        echo "警告：完全卸载会删除 Panel 数据库、.env、安装凭据、Panel 升级/卸载备份和 XNAT 托管的域名证书。"
        echo "不会删除 Nginx、Certbot 软件，也不会修改 Komari 或其他站点配置。"
        ;;
      0) echo "已取消。"; return 0 ;;
      *) echo "无效选择，已取消。"; return 0 ;;
    esac
    panel_domain="$(state_get domain)"
  else
    expected="REMOVE AGENT"
    echo "此操作只卸载 XNAT Host Agent。"
    echo "Incus、natpool、Bridge 和现有 VPS 不会删除。"
  fi
  [[ -t 0 ]] || die "卸载必须在交互终端中执行"
  read -r -p "请输入 ${expected} 确认: " phrase
  [[ "$phrase" == "$expected" ]] || { echo "确认内容不匹配，已取消。"; return 0; }

  if [[ "$role" == host || "$uninstall_mode" == backup ]]; then
    backup="/root/xnat-backups/uninstall-${role}-$(date +'%Y%m%d-%H%M%S')"
    install -d -m 0700 "$backup"
  fi

  if [[ "$role" == panel ]]; then
    if [[ "$uninstall_mode" == backup ]]; then
      [[ -f "$PANEL_DB" ]] && sqlite3 "$PANEL_DB" ".backup '${backup}/panel.db'" || true
      [[ -f "$PANEL_ENV" ]] && cp -a "$PANEL_ENV" "$backup/.env" || true
      [[ -f "$PANEL_CRED_FILE" ]] && cp -a "$PANEL_CRED_FILE" "$backup/credentials.txt" || true
      chmod 0600 "$backup"/* "$backup"/.env 2>/dev/null || true
    fi

    systemctl disable --now xnat-panel.service xnat-maintenance.timer >/dev/null 2>&1 || true
    rm -f /etc/systemd/system/xnat-panel.service /etc/systemd/system/xnat-maintenance.service /etc/systemd/system/xnat-maintenance.timer
    rm -f "$NGINX_LINK" "$NGINX_SITE" "$CF_SNIPPET" "$CF_ORIGIN_MAP" "$WS_MAP"
    rm -f "$CF_TIMER_SERVICE" "$CF_TIMER" "$CERTBOT_DEPLOY_HOOK"
    rm -rf "$PANEL_DIR" "$ACME_WEBROOT"
    rm -f "$PANEL_CRED_FILE"

    if [[ "$uninstall_mode" == purge ]]; then
      if [[ -n "$panel_domain" ]] && have certbot; then
        certbot delete --cert-name "$panel_domain" --non-interactive >/dev/null 2>&1 || true
      fi
      if [[ -d /root/xnat-backups ]]; then
        find /root/xnat-backups -maxdepth 1 -mindepth 1 -type d \
          \( -name 'uninstall-panel-*' -o -name 'panel-*' \) -exec rm -rf -- {} +
        rmdir /root/xnat-backups 2>/dev/null || true
      fi
      if [[ -d "$DIAGNOSTIC_DIR" ]]; then
        find "$DIAGNOSTIC_DIR" -maxdepth 1 -type f -name 'xnat-diagnostic-panel-*' -delete
        rmdir "$DIAGNOSTIC_DIR" 2>/dev/null || true
      fi
    fi

    write_uninstall_nginx_guard
  else
    [[ -f "$AGENT_ENV" ]] && cp -a "$AGENT_ENV" "$backup/.env" || true
    [[ -d "$AGENT_DIR/tls" ]] && cp -a "$AGENT_DIR/tls" "$backup/tls" || true
    systemctl disable --now xnat-host-agent.service >/dev/null 2>&1 || true
    rm -f /etc/systemd/system/xnat-host-agent.service
    rm -rf "$AGENT_DIR"
  fi

  have xnat-firewall && xnat-firewall disable || true
  rm -f /etc/systemd/system/xnat-firewall.service
  systemctl daemon-reload
  rm -rf "$XNAT_ETC_DIR"
  rm -f /usr/local/sbin/xnat-firewall
  echo "XNAT $(component_label "$role") 已卸载。"
  if [[ -n "$backup" ]]; then
    echo "备份：${backup}"
  elif [[ "$role" == panel ]]; then
    echo "已执行完全卸载：未保留 XNAT Panel 数据备份。"
  fi
  rm -f /usr/local/sbin/xnat
}
'''
pat = re.compile(r"cmd_uninstall\(\)\{\n.*?\n\}\n\ninteractive_domain\(\)\{", re.S)
if len(pat.findall(s)) != 1:
    raise SystemExit("scripts/xnat: uninstall block mismatch")
s = pat.sub(new_uninstall + "\ninteractive_domain(){", s, count=1)
p.write_text(s, encoding="utf-8")

readme = Path("README.md")
r = readme.read_text(encoding="utf-8")
r = r.replace("**当前正式版本：v1.0.0**", "**当前正式版本：v1.0.1**", 1)
r = r.replace("| XNAT Panel | v1.0.0 |", "| XNAT Panel | v1.0.1 |", 1)
baseline = "> v1.0.0 是重新整理后的正式基线。Panel 与 Host 统一从 v1.0.0 开始；旧开发阶段版本不提供原地升级兼容，建议在全新系统上部署。"
if baseline not in r:
    raise SystemExit("README baseline anchor missing")
r = r.replace(baseline, baseline + "\n\n> v1.0.1 修复 Panel 卸载后的 Nginx 虚拟主机串站问题，并新增‘保留备份 / 完全卸载’两种卸载方式。", 1)
readme.write_text(r, encoding="utf-8")

Path("panel/README.md").write_text("""# XNAT Panel v1.0.1

XNAT 控制平面正式组件。v1.0.1 基于重新整理后的 v1.0.0 正式基线，修复 Panel 与其他 Nginx 站点共存时的卸载后虚拟主机串站问题，并提供“保留数据备份 / 完全卸载”两种明确的卸载模式。

本次不修改 Panel 页面布局、视觉风格、业务交互、Agent API 或 Mobile API。Host Agent 继续保持 v1.0.0。
""", encoding="utf-8")

changelog = Path("CHANGELOG.md")
old = changelog.read_text(encoding="utf-8")
entry = """# Changelog

## v1.0.1 - 2026-09-13

- 修复卸载 Panel 后 Nginx 缺少默认拒绝站点，导致旧 Panel 域名可能显示同机 Komari / 其他站点内容的问题。
- Panel 卸载新增“保留数据备份”和“完全卸载”两种模式。
- 完全卸载会清理 Panel 数据库、`.env`、安装凭据、Panel 升级/卸载备份、Panel 诊断文件与 XNAT 托管的域名证书。
- Nginx、Certbot 以及 Komari / 其他虚拟主机不会被删除或改写；必要时仅保留一个无业务数据的默认拒绝站点，避免 Host 头串站。
- Panel UI、页面布局、视觉风格和现有业务交互保持不变。
- Panel v1.0.1；Host v1.0.0；Agent API / Mobile API 继续保持 v1。

**由 𝐍𝐀𝐌𝐄𝐋𝐄𝐒𝐒 和 GPT 倾力打造**

"""
if not old.startswith("# Changelog\n\n"):
    raise SystemExit("CHANGELOG header mismatch")
changelog.write_text(entry + old[len("# Changelog\n\n"):], encoding="utf-8")

br = Path("scripts/build-release.sh")
b = br.read_text(encoding="utf-8")
start = 'cat > "$DIST/RELEASE_NOTES.md" <<EOF_NOTES\n'
end = 'EOF_NOTES\n(cd "$DIST" && sha256sum '
a = b.find(start)
z = b.find(end, a)
if a < 0 or z < 0:
    raise SystemExit("build-release notes block not found")
notes = '''cat > "$DIST/RELEASE_NOTES.md" <<EOF_NOTES
# XNAT v${RELEASE_VERSION}

Panel 卸载安全与数据清理修复。

- Panel：v${PANEL_VERSION}
- Host：v${AGENT_VERSION}
- Agent API：v1
- Mobile API：v1
- 修复卸载 Panel 后旧 Panel 域名可能落入同机 Komari / 其他 Nginx 站点的问题
- 卸载 Panel 可选择保留备份或完全清除 XNAT Panel 数据
- 完全卸载清理数据库、.env、安装凭据、Panel 备份、诊断文件与 XNAT 托管证书
- 不删除 Nginx、Certbot，也不修改 Komari 或其他站点配置
- Panel UI、页面布局、视觉风格和业务交互保持不变

**由 𝐍𝐀𝐌𝐄𝐋𝐄𝐒𝐒 和 GPT 倾力打造**
EOF_NOTES
'''
b = b[:a] + notes + b[z + len("EOF_NOTES\n"):]
br.write_text(b, encoding="utf-8")

ck = Path("scripts/check.sh")
c = ck.read_text(encoding="utf-8")
marker = 'grep -q \'1.6.0) UPGRADE_PATH="verified-v1.6.0"\' scripts/upgrade-panel.sh\n'
extra = '''grep -q '1.0.0) UPGRADE_PATH="verified-v1.0.0"' scripts/upgrade-panel.sh
grep -q 'write_uninstall_nginx_guard' scripts/xnat
grep -q '完全卸载，删除所有 XNAT Panel 数据' scripts/xnat
grep -q '00-xnat-default-deny.conf' scripts/xnat
'''
if marker not in c:
    raise SystemExit("check.sh anchor missing")
c = c.replace(marker, marker + extra, 1)
ck.write_text(c, encoding="utf-8")
