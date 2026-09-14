from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text()


def write(path: str, text: str) -> None:
    (ROOT / path).write_text(text)


def replace_required(path: str, old: str, new: str) -> None:
    text = read(path)
    if old not in text:
        raise SystemExit(f"{path}: missing required token: {old}")
    write(path, text.replace(old, new, 1))


write("VERSION", "1.0.3\n")
write("panel/VERSION", "1.0.3\n")
write("panel/app/__init__.py", '__version__ = "1.0.3"\n')

meta = json.loads(read("release.json"))
meta["release_version"] = "1.0.3"
meta["panel_version"] = "1.0.3"
assert meta["agent_version"] == "1.0.1"
assert str(meta["agent_api_version"]) == "2"
assert meta["supported_agent_api_versions"] == ["1", "2"]
assert str(meta["mobile_api_version"]) == "1"
write("release.json", json.dumps(meta, ensure_ascii=False, indent=2) + "\n")

replace_required("panel/app/main.py", '"version": "1.0.2"', '"version": "1.0.3"')
replace_required("panel/app/templates/base.html", "XNAT v1.0.2 Multi-Node", "XNAT v1.0.3 Multi-Node")
replace_required(
    "scripts/upgrade-panel.sh",
    '  1.0.2) UPGRADE_PATH="legacy-v1.0.2"; warn "当前为 v1.0.2；建议按正式版本链先升级到 v1.1.1，再升级到 v${COMPONENT_VERSION}。此升级器仍保留 additive schema 兼容检查。" ;;',
    '  1.0.2) UPGRADE_PATH="verified-v1.0.2" ;;',
)

check = read("scripts/check.sh")
check = check.replace("assert '当前正式版本：v1.0.2' in readme", "assert '当前正式版本：v1.0.3' in readme", 1)
check = check.replace(
    "if grep -RInE 'v1\\.0\\.3|v1\\.0\\.4|testing-v|(^|[^A-Za-z])RC[0-9]+|(^|[^A-Za-z])rc[0-9]+|候选版本' \\",
    "if grep -RInE 'v1\\.0\\.4|testing-v|(^|[^A-Za-z])RC[0-9]+|(^|[^A-Za-z])rc[0-9]+|候选版本' \\",
    1,
)
check = check.replace(
    "--exclude-dir=.git --exclude-dir=__pycache__ --exclude='check.sh' . >/tmp/xnat-old-version.txt; then",
    "--exclude-dir=.git --exclude-dir=.github --exclude-dir=__pycache__ --exclude='check.sh' . >/tmp/xnat-old-version.txt; then",
    1,
)
if "v1.0.3 configurable image disk policy contract" not in check:
    check += """

# v1.0.3 configurable image disk policy contract
grep -q 'KVM_MIN_DISK_GB = 3.0' panel/app/services/image_policy.py
grep -q 'return max(configured, KVM_MIN_DISK_GB)' panel/app/services/image_policy.py
grep -q '/admin/system-images/{image_id}/disk' panel/app/main.py
grep -q '最低系统盘 (GiB)' panel/app/templates/admin.html
grep -q '"min_disk_gb": float(row.min_disk_gb or 1.0)' panel/app/mobile_api.py
grep -q 'image_disk_policy_configurable_v1' panel/app/schema.py
grep -q '1.0.2) UPGRADE_PATH="verified-v1.0.2"' scripts/upgrade-panel.sh
"""
for token in [
    "assert (root/'VERSION').read_text().strip() == '1.0.3'",
    "assert (root/'panel/VERSION').read_text().strip() == '1.0.3'",
    "assert meta['release_version']=='1.0.3'",
    "assert meta['panel_version']=='1.0.3'",
]:
    if token not in check:
        raise SystemExit(f"scripts/check.sh baseline not aligned: {token}")
write("scripts/check.sh", check)

readme = read("README.md")
for old, new in [
    ("**当前正式版本：v1.0.2**", "**当前正式版本：v1.0.3**"),
    ("更新 Panel 到 v1.0.2", "更新 Panel 到 v1.0.3"),
    ("Panel 已经是 v1.0.2", "Panel 已经是 v1.0.3"),
]:
    if old not in readme:
        raise SystemExit(f"README.md missing: {old}")
    readme = readme.replace(old, new, 1)
write("README.md", readme)

replace_required("docs/MOBILE_API.md", "XNAT Panel `v1.0.2`", "XNAT Panel `v1.0.3`")

intro = read("docs/INTRODUCTION.md")
for old, new in [
    ("| XNAT Release | v1.0.2 |", "| XNAT Release | v1.0.3 |"),
    ("| XNAT Panel | v1.0.2 |", "| XNAT Panel | v1.0.3 |"),
]:
    if old not in intro:
        raise SystemExit(f"docs/INTRODUCTION.md missing: {old}")
    intro = intro.replace(old, new, 1)
start = intro.index("## 版本说明")
end = intro.index("## XNAT 能做什么")
version_block = """## 版本说明

v1.0.3 是镜像最低系统盘策略与后台配置能力更新。Panel 为 v1.0.3，Host Agent 保持 v1.0.1，Agent API 保持 v2，Mobile API 保持 v1。

系统镜像的最低系统盘现在由 Panel 后台每个镜像自己的 `min_disk_gb` 决定，不再按 Debian / Ubuntu 家族写死。默认 Debian 为 1 GiB、Ubuntu 为 2 GiB、Alpine 为 1 GiB；LXC 直接使用后台配置值，KVM 仅保留 3 GiB 全局技术底线。

Mobile API v1 的 `/api/v1/system-images` 新增 `min_disk_gb` 字段，属于向后兼容的字段扩展。Panel v1.0.3 继续兼容 Agent API v1 / v2。

如果 Host Agent 从 API v1 升级到 API v2，请先确认 Panel 已升级到 v1.0.3，再在对应 Host 执行：

```bash
XNAT_ALLOW_AGENT_API_CHANGE=1 xnat
```

随后进入 **更新 → Host Agent 更新**。该变量只用于本次跨 API 升级，无需写入 `.env` 或永久设置。

"""
intro = intro[:start] + version_block + intro[end:]
marker = "## 更新记录\n\n### v1.0.2"
entry = """## 更新记录

### v1.0.3

- 系统镜像最低系统盘改为 Panel 后台逐镜像配置。
- 默认 Debian 12 / 13 为 1 GiB、Ubuntu 22.04 / 24.04 为 2 GiB、Alpine 3.24 为 1 GiB。
- LXC 直接使用后台配置；KVM 使用 `max(镜像配置, 3 GiB)`。
- 系统镜像后台支持编辑最低系统盘，新建镜像时也可指定。
- 旧数据库只执行一次安全迁移，不会持续覆盖管理员自定义值。
- Mobile API v1 系统镜像响应新增 `min_disk_gb`。
- v1.0.2 → v1.0.3 标记为正式验证的直接升级路径。
- Host Agent、Agent API 与 Panel 整体 UI 保持不变。

### v1.0.2"""
if marker not in intro:
    raise SystemExit("docs/INTRODUCTION.md update marker missing")
write("docs/INTRODUCTION.md", intro.replace(marker, entry, 1))

changelog = read("CHANGELOG.md")
old_head = "## v1.0.2 - 2026-09-14\n"
if not changelog.startswith(old_head):
    raise SystemExit("CHANGELOG.md unexpected header")
new_head = """# Changelog

## v1.0.3 - 2026-09-14

- XNAT Release 1.0.3 / Panel 1.0.3；Host Agent 保持 1.0.1，Agent API 保持 v2，Mobile API 保持 v1。
- 系统镜像最低系统盘改为 Panel 后台逐镜像配置，不再按 Debian / Ubuntu 家族写死。
- 默认 Debian 12 / 13 为 1 GiB、Ubuntu 22.04 / 24.04 为 2 GiB、Alpine 3.24 为 1 GiB。
- LXC 直接使用镜像 `min_disk_gb`；KVM 全局技术底线调整为 3 GiB。
- 管理后台支持编辑每个系统镜像的最低系统盘。
- 旧数据库执行一次性兼容迁移，之后不会覆盖管理员自定义值。
- Mobile API v1 `/api/v1/system-images` 新增 `min_disk_gb` 字段，保持向后兼容。
- v1.0.2 → v1.0.3 为正式验证的直接 Panel 升级路径。
- Host Agent、Agent API、Panel 整体 UI 与主要业务交互保持不变。

**由 𝐍𝐀𝐌𝐄𝐋𝐄𝐒𝐒 和 GPT 倾力打造**

## v1.0.2 - 2026-09-14
"""
changelog = new_head + changelog[len(old_head):]
changelog = changelog.replace("\n# Changelog\n\n## v1.0.1", "\n## v1.0.1", 1)
write("CHANGELOG.md", changelog)

build = read("scripts/build-release.sh")
a = build.index('cat > "$DIST/RELEASE_NOTES.md" <<EOF_NOTES')
b = build.index('(cd "$DIST" && sha256sum', a)
notes = """cat > "$DIST/RELEASE_NOTES.md" <<EOF_NOTES
# XNAT v${RELEASE_VERSION}

系统镜像最低磁盘策略与后台配置能力更新。

- XNAT Release：v${RELEASE_VERSION}
- Panel：v${PANEL_VERSION}
- Host Agent：v${AGENT_VERSION}
- Agent API：v${AGENT_API_VERSION}
- Mobile API：v1
- 系统镜像最低系统盘改为 Panel 后台逐镜像配置，不再按 Debian / Ubuntu 家族写死
- 默认 Debian 12 / 13 为 1 GiB、Ubuntu 22.04 / 24.04 为 2 GiB、Alpine 3.24 为 1 GiB
- LXC 直接使用后台配置；KVM 使用 max(镜像配置, 3 GiB)
- 管理后台系统镜像页面支持直接修改最低系统盘，新建镜像时也可指定
- 旧数据库仅执行一次兼容迁移，不会在后续启动中覆盖管理员自定义值
- Mobile API v1 的 /api/v1/system-images 新增 min_disk_gb 字段，现有客户端保持兼容
- v1.0.2 → v1.0.3 已作为正式验证的直接 Panel 升级路径
- Host Agent、Agent API、Panel 整体 UI 与主要业务交互保持不变

> Android v1.0.1 仍可继续使用；后续 Android 版本会改为直接读取 Panel 下发的 min_disk_gb。

**由 𝐍𝐀𝐌𝐄𝐋𝐄𝐒𝐒 和 GPT 倾力打造**
EOF_NOTES
"""
write("scripts/build-release.sh", build[:a] + notes + build[b:])

print("v1.0.3 preparation complete")
