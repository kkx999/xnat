from __future__ import annotations

from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    (ROOT / path).write_text(text, encoding="utf-8")


def replace(path: str, old: str, new: str, *, count: int | None = None) -> None:
    text = read(path)
    if old not in text:
        raise SystemExit(f"missing expected text in {path}: {old[:140]!r}")
    text = text.replace(old, new) if count is None else text.replace(old, new, count)
    write(path, text)


write("VERSION", "1.0.5\n")
write("panel/VERSION", "1.0.5\n")
replace("panel/app/__init__.py", '__version__ = "1.0.4"', '__version__ = "1.0.5"')
replace("panel/app/main.py", '"version": "1.0.4"', '"version": "1.0.5"')
replace("panel/app/templates/base.html", "XNAT v1.0.4 Multi-Node", "XNAT v1.0.5 Multi-Node")

meta = json.loads(read("release.json"))
meta["release_version"] = "1.0.5"
meta["panel_version"] = "1.0.5"
meta["agent_version"] = "1.0.4"
write("release.json", json.dumps(meta, ensure_ascii=False, indent=2) + "\n")

readme = read("README.md")
if "**当前正式版本：v1.0.4**" not in readme:
    raise SystemExit("README current version marker missing")
readme = readme.replace("**当前正式版本：v1.0.4**", "**当前正式版本：v1.0.5**", 1)
old_steps = "1. 先通过 `xnat` 更新 Panel 到 v1.0.4；\n2. 再逐台 Host 更新 Host Agent 到 v1.0.4；\n3. 在 Panel 中确认 Host 在线，并确认 Agent API 已切换到 v2。"
new_steps = "1. 先通过 `xnat` 更新 Panel 到 v1.0.5；\n2. Host Agent 已是 v1.0.4 的节点无需重复更新；\n3. 在 Panel 中确认 Host 在线，并确认 Agent API 仍为 v2。"
if old_steps not in readme:
    raise SystemExit("README upgrade steps marker missing")
readme = readme.replace(old_steps, new_steps, 1)
readme = readme.replace("确认 Panel 已经是 v1.0.4 后", "确认 Panel 已经是 v1.0.5 后", 1)
write("README.md", readme)

changelog = read("CHANGELOG.md")
if not changelog.startswith("# Changelog\n\n"):
    raise SystemExit("unexpected CHANGELOG header")
entry = """## v1.0.5 - 2026-09-14

- XNAT Release / Panel 升级到 v1.0.5；Host Agent 保持 v1.0.4，Agent API v2、Mobile API v1 均不变。
- 修复浅色主题下“服务器实时监控”标题、副标题和状态文字对比度过低的问题。
- 去除服务器详情折叠模块展开时突兀的深色分割线，亮色 / 深色主题分别使用更柔和的层级与边框。
- 降低详情模块大面积蓝色底的饱和度，让 Root 密码、NAT 端口、系统重装与删除实例区域更贴合当前主题。
- 同步优化深色主题的详情容器、展开区、操作按钮与实时监控轨道，避免只修浅色造成主题回归。
- 实时监控采样、5 秒前端刷新、3 秒 Agent 内存缓存及无历史存储逻辑保持不变。

**由 𝐍𝐀𝐌𝐄𝐋𝐄𝐒𝐒 和 GPT 倾力打造**

"""
if "## v1.0.5 - 2026-09-14" not in changelog:
    changelog = changelog.replace("# Changelog\n\n", "# Changelog\n\n" + entry, 1)
write("CHANGELOG.md", changelog)

upgrade = read("scripts/upgrade-panel.sh")
if '1.0.4) UPGRADE_PATH="verified-v1.0.4"' not in upgrade:
    marker = '  1.0.3) UPGRADE_PATH="verified-v1.0.3" ;;'
    if marker not in upgrade:
        raise SystemExit("upgrade marker missing")
    upgrade = upgrade.replace(marker, '  1.0.4) UPGRADE_PATH="verified-v1.0.4" ;;\n' + marker, 1)
write("scripts/upgrade-panel.sh", upgrade)

css = read("panel/app/static/style.css")
polish = r'''

/* ========================================================================== 
   v1.0.5 server-detail dual-theme polish
   Keep the v1.0.4 layout/metrics behavior; normalize contrast, separators and
   surface tinting for both client themes.
   ========================================================================== */

/* Dark theme: reduce the detached blue cast while preserving hierarchy. */
body.client-body .server-live-panel{
  border-color:#2a394a;
  background:linear-gradient(180deg,#121c28 0%,#101923 100%);
  box-shadow:0 10px 28px rgba(0,0,0,.12),inset 0 1px 0 rgba(255,255,255,.016);
}
body.client-body .server-live-head{border-bottom-color:#2a394a}
body.client-body .server-live-head>div:first-child span{color:#edf3fb}
body.client-body .server-live-head>div:first-child small,
body.client-body .server-live-status,
body.client-body .server-live-value>span,
body.client-body .server-live-network-rates span{color:#91a0b3}
body.client-body .server-live-item:nth-child(odd),
body.client-body .server-live-item:nth-child(-n+2){border-color:#2a394a}
body.client-body .server-live-value>strong{color:#eef4fb}
body.client-body .server-live-item>small{color:#7f90a5}
body.client-body .server-live-network-rates strong{color:#dce7f3}
body.client-body .server-live-progress{
  background:#1b2734;
  box-shadow:inset 0 1px 2px rgba(0,0,0,.20);
}
body.client-body .server-live-progress>span{background:linear-gradient(90deg,#537fae,#6f9bc8)}

body.client-body .client-detail-section{
  border-color:#293745;
  background:linear-gradient(180deg,#121a24 0%,#101821 100%);
  box-shadow:0 8px 24px rgba(0,0,0,.10),inset 0 1px 0 rgba(255,255,255,.014);
}
body.client-body .client-detail-section[open]>summary{border-bottom:0}
body.client-body .client-detail-body{
  border-top:0!important;
  background:#0f1720;
}
body.client-body .client-detail-section>summary .detail-summary-main strong{color:#edf2f8}
body.client-body .client-detail-section>summary .detail-summary-main span{color:#8f9daf}
body.client-body .detail-summary-main::before{background:#638db8;box-shadow:none}
body.client-body .detail-summary-action{
  background:#151f2a;
  border-color:#334454;
  color:#9eafc1!important;
}
body.client-body .client-detail-section[open] .detail-summary-action{
  background:#182430;
  border-color:#3b5064;
  color:#c9d6e3!important;
}
body.client-body .client-password-display code{
  border-color:#334351;
  background:#121b24;
  color:#edf3f8;
}
body.client-body .client-port-row{border-bottom-color:#293745}
body.client-body .client-port-add,
body.client-body .client-danger-grid form{background:#111922;border-color:#2c3945}

/* Light theme: neutral surfaces, readable titles, no dark hairline separators. */
html[data-client-theme="light"] body.client-body .server-live-panel{
  border-color:#d9e1e9;
  background:linear-gradient(180deg,#ffffff 0%,#fafbfd 100%);
  box-shadow:0 7px 22px rgba(39,61,86,.045);
}
html[data-client-theme="light"] body.client-body .server-live-head{border-bottom-color:#e3e8ee}
html[data-client-theme="light"] body.client-body .server-live-head>div:first-child span{color:#263b52!important}
html[data-client-theme="light"] body.client-body .server-live-head>div:first-child small{color:#6f8195!important}
html[data-client-theme="light"] body.client-body .server-live-status{color:#667a90!important}
html[data-client-theme="light"] body.client-body .server-live-value>span,
html[data-client-theme="light"] body.client-body .server-live-network-rates span{color:#64788f!important}
html[data-client-theme="light"] body.client-body .server-live-value>strong{color:#1f344b!important}
html[data-client-theme="light"] body.client-body .server-live-item>small{color:#71849a!important}
html[data-client-theme="light"] body.client-body .server-live-network-rates strong{color:#28415a!important}
html[data-client-theme="light"] body.client-body .server-live-item:nth-child(odd),
html[data-client-theme="light"] body.client-body .server-live-item:nth-child(-n+2){border-color:#e3e8ee}
html[data-client-theme="light"] body.client-body .server-live-progress{
  background:#e9eef3;
  box-shadow:inset 0 1px 2px rgba(45,67,91,.08);
}
html[data-client-theme="light"] body.client-body .server-live-progress>span{background:linear-gradient(90deg,#5588bd,#72a1d0)}

html[data-client-theme="light"] body.client-body .client-detail-section{
  border-color:#dce3ea;
  background:linear-gradient(180deg,#ffffff 0%,#fbfcfd 100%);
  box-shadow:0 6px 18px rgba(42,62,84,.035);
}
html[data-client-theme="light"] body.client-body .client-detail-section[open]>summary{border-bottom:0!important}
html[data-client-theme="light"] body.client-body .client-detail-body{
  border-top:0!important;
  background:#fafbfc;
}
html[data-client-theme="light"] body.client-body .client-detail-section>summary .detail-summary-main strong{color:#263a4f!important}
html[data-client-theme="light"] body.client-body .client-detail-section>summary .detail-summary-main span{color:#74869a!important}
html[data-client-theme="light"] body.client-body .detail-summary-main::before{background:#5c86ae;box-shadow:none}
html[data-client-theme="light"] body.client-body .detail-summary-action{
  background:#f6f8fa;
  border-color:#d4dce4;
  color:#657b91!important;
}
html[data-client-theme="light"] body.client-body .client-detail-section[open] .detail-summary-action{
  background:#f2f5f8;
  border-color:#cbd6df;
  color:#456985!important;
}
html[data-client-theme="light"] body.client-body .client-password-display,
html[data-client-theme="light"] body.client-body .client-port-add,
html[data-client-theme="light"] body.client-body .client-danger-grid form{
  background:#fbfcfd;
  border-color:#e0e6ec;
}
html[data-client-theme="light"] body.client-body .client-password-display code{
  color:#263b52;
  background:#ffffff;
  border-color:#d8e0e8;
}
html[data-client-theme="light"] body.client-body .client-port-row{
  background:transparent;
  border-color:#e4e9ee;
}
html[data-client-theme="light"] body.client-body .client-help-text,
html[data-client-theme="light"] body.client-body .client-port-row em{color:#74869a}

@media(max-width:620px){
  body.client-body .server-live-item:nth-child(-n+3){border-bottom-color:#2a394a}
  html[data-client-theme="light"] body.client-body .server-live-item:nth-child(-n+3){border-bottom-color:#e3e8ee}
}
'''
if "v1.0.5 server-detail dual-theme polish" not in css:
    css += polish
write("panel/app/static/style.css", css)

build = read("scripts/build-release.sh")
start = build.index('cat > "$DIST/RELEASE_NOTES.md" <<EOF_NOTES')
end = build.index("EOF_NOTES", start) + len("EOF_NOTES")
notes = '''cat > "$DIST/RELEASE_NOTES.md" <<EOF_NOTES
# XNAT v${RELEASE_VERSION}

服务器详情双主题视觉修复。

- XNAT Release：v${RELEASE_VERSION}
- Panel：v${PANEL_VERSION}
- Host Agent：v${AGENT_VERSION}（本版未改 Host Agent）
- Agent API：v${AGENT_API_VERSION}
- Mobile API：v1
- 修复浅色主题下实时监控标题、副标题、状态和指标辅助文字对比度不足
- 去除 Root 密码、NAT 端口、系统重装与删除实例展开区域突兀的深色细分割线
- 降低详情区域偏蓝底色饱和度，使容器、展开区和操作按钮更贴合整体主题
- 深色主题同步调整容器、边框、文字、操作按钮和实时监控轨道，不做单边主题修复
- CPU / 内存 / 硬盘胶囊进度条、网络实时速率及采样逻辑保持不变
- v1.0.4 → v1.0.5 为正式验证的直接 Panel 升级路径；Host Agent v1.0.4 无需重复更新

**由 𝐍𝐀𝐌𝐄𝐋𝐄𝐒𝐒 和 GPT 倾力打造**
EOF_NOTES'''
build = build[:start] + notes + build[end:]
write("scripts/build-release.sh", build)

checks = read("scripts/check.sh")
replacements = [
    (
        "== '1.0.4'\nassert (root/'panel/VERSION').read_text().strip() == '1.0.4'\nassert (root/'agent/VERSION').read_text().strip() == '1.0.4'",
        "== '1.0.5'\nassert (root/'panel/VERSION').read_text().strip() == '1.0.5'\nassert (root/'agent/VERSION').read_text().strip() == '1.0.4'",
    ),
    (
        "assert meta['release_version']=='1.0.4'\nassert meta['panel_version']=='1.0.4'\nassert meta['agent_version']=='1.0.4'",
        "assert meta['release_version']=='1.0.5'\nassert meta['panel_version']=='1.0.5'\nassert meta['agent_version']=='1.0.4'",
    ),
    (
        "assert meta['release_version'] == meta['panel_version'] == '1.0.4'\nassert meta['agent_version'] == '1.0.4'",
        "assert meta['release_version'] == meta['panel_version'] == '1.0.5'\nassert meta['agent_version'] == '1.0.4'",
    ),
    ("assert '当前正式版本：v1.0.4' in readme", "assert '当前正式版本：v1.0.5' in readme"),
    ("'v1\\.0\\.5|testing-v|", "'v1\\.0\\.6|testing-v|"),
    ("print('v1.0.4 baseline contracts: ok')", "print('v1.0.5 baseline contracts: ok')"),
]
for old, new in replacements:
    if old not in checks:
        raise SystemExit(f"check marker missing: {old[:120]!r}")
    checks = checks.replace(old, new, 1)

guard = r'''

# v1.0.5 dual-theme server-detail polish guard.
python3 - <<'PYV105THEME'
from pathlib import Path
import json
root=Path('.')
meta=json.loads((root/'release.json').read_text())
assert meta['release_version'] == meta['panel_version'] == '1.0.5'
assert meta['agent_version'] == '1.0.4'
assert '1.0.4) UPGRADE_PATH="verified-v1.0.4"' in (root/'scripts/upgrade-panel.sh').read_text()
css=(root/'panel/app/static/style.css').read_text()
block=css.split('v1.0.5 server-detail dual-theme polish',1)[1]
for token in [
    'body.client-body .server-live-head>div:first-child span{color:#edf3fb}',
    'html[data-client-theme="light"] body.client-body .server-live-head>div:first-child span{color:#263b52!important}',
    'body.client-body .client-detail-section[open]>summary{border-bottom:0}',
    'html[data-client-theme="light"] body.client-body .client-detail-section[open]>summary{border-bottom:0!important}',
    'background:#fafbfc',
    'background:#0f1720',
    'border-radius:999px',
]:
    assert token in block, token
print('v1.0.5 dual-theme server-detail polish: ok')
PYV105THEME
'''
if "v1.0.5 dual-theme server-detail polish guard" not in checks:
    checks += guard
write("scripts/check.sh", checks)

print("v1.0.5 transforms applied")
