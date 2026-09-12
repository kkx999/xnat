from pathlib import Path
import json


def read(path):
    return Path(path).read_text(encoding="utf-8")


def write(path, text):
    Path(path).write_text(text, encoding="utf-8")


def replace_once(path, old, new):
    text = read(path)
    if old not in text:
        raise SystemExit(f"anchor missing in {path}: {old[:180]!r}")
    write(path, text.replace(old, new, 1))


# ---------------------------------------------------------------------------
# Release metadata. v1.6.3 is Panel-only; Host Agent/API remain unchanged.
# ---------------------------------------------------------------------------
write("VERSION", "1.6.3\n")
write("panel/VERSION", "1.6.3\n")
replace_once("panel/app/__init__.py", '__version__ = "1.6.2"', '__version__ = "1.6.3"')
replace_once("panel/app/main.py", '"version": "1.6.2"', '"version": "1.6.3"')
replace_once("panel/app/templates/base.html", "XNAT v1.6.2 Multi-Node", "XNAT v1.6.3 Multi-Node")
replace_once("panel/app/nodes.py", '"User-Agent": "XNAT-Panel/1.6.2",', '"User-Agent": "XNAT-Panel/1.6.3",')
replace_once("docs/MOBILE_API.md", "XNAT Panel `v1.6.2`", "XNAT Panel `v1.6.3`")

meta = json.loads(read("release.json"))
meta["release_version"] = "1.6.3"
meta["panel_version"] = "1.6.3"
assert meta["agent_version"] == "1.2.0"
assert str(meta["agent_api_version"]) == "1"
assert str(meta["mobile_api_version"]) == "1"
write("release.json", json.dumps(meta, ensure_ascii=False, indent=2) + "\n")

replace_once(
    "scripts/upgrade-panel.sh",
    'case "$CURRENT_VERSION" in\n  1.6.1) UPGRADE_PATH="verified-v1.6.1" ;;',
    'case "$CURRENT_VERSION" in\n  1.6.2) UPGRADE_PATH="verified-v1.6.2" ;;\n  1.6.1) UPGRADE_PATH="verified-v1.6.1" ;;',
)


# ---------------------------------------------------------------------------
# v1.6.3: normalize tiny LVM/Incus capacity alignment loss for logical quota.
#
# Host installer creates natpool in whole GiB, but Incus/LVM may report a few
# MiB less after extent/metadata alignment. Treating 1.99 GiB as a hard logical
# total makes a 1 GiB plan floor to one instance and can also reject the second
# instance at a 100% storage scheduling limit. Physical storage usage must stay
# based on the raw Agent report, so the normalization is deliberately narrow.
# ---------------------------------------------------------------------------
replace_once(
    "panel/app/nodes.py",
    "import json\nimport time\n",
    "import json\nimport math\nimport time\n",
)

anchor = '''def host_allocated_disk_gb(db, host_id: int) -> int:\n    return db.scalar(\n        select(func.coalesce(func.sum(func.coalesce(Server.disk_gb, Plan.disk_gb, 0)), 0))\n        .select_from(Server)\n        .join(Plan, Server.plan_id == Plan.id)\n        .where(\n            Server.host_id == host_id,\n            Server.deleted_at.is_(None),\n            Server.status.in_(["provisioning", "running", "stopped"]),\n        )\n    ) or 0\n\n\n'''
helper = anchor + '''STORAGE_QUOTA_QUANTUM_GB = 0.125\nSTORAGE_QUOTA_ALIGNMENT_TOLERANCE_GB = 32 / 1024\n\n\ndef normalized_storage_quota_total_gb(reported_total_gb: float | int | None) -> float:\n    """Return the logical natpool quota total without tiny LVM alignment loss.\n\n    XNAT allocates guest disks in 0.125 GiB increments. Incus/LVM may report a\n    configured pool a few MiB below that boundary (for example 1.99 instead of\n    2.00 GiB) because of extent/metadata alignment. Only snap upward when the\n    raw value is within 32 MiB of the next 0.125 GiB boundary. The raw Agent\n    total/used values continue to drive physical storage percentages/watermarks.\n    """\n    try:\n        raw = max(0.0, float(reported_total_gb or 0))\n    except (TypeError, ValueError):\n        return 0.0\n    if raw < 1.0:\n        return raw\n\n    quantum = STORAGE_QUOTA_QUANTUM_GB\n    units = math.ceil((raw - 1e-9) / quantum)\n    aligned = units * quantum\n    delta = aligned - raw\n    if 0 < delta <= STORAGE_QUOTA_ALIGNMENT_TOLERANCE_GB + 1e-9:\n        return round(aligned, 3)\n    return raw\n\n\n'''
replace_once("panel/app/nodes.py", anchor, helper)

replace_once(
    "panel/app/nodes.py",
    '''    memory_limit_percent = thresholds["memory"] or 100\n    storage_limit_percent = thresholds["storage"] or 100\n    cpu_limit_percent = thresholds["cpu"] or 100\n    memory_allocatable_mb = int((host.memory_total_mb or 0) * memory_limit_percent / 100)\n    storage_allocatable_gb = float((host.storage_total_gb or 0) * storage_limit_percent / 100)\n    logical_memory_remaining = max(0, memory_allocatable_mb - allocated_memory)\n    logical_storage_remaining = max(0.0, storage_allocatable_gb - allocated_disk)\n    physical_memory_remaining = max(0, memory_allocatable_mb - int(host.memory_used_mb or 0))\n    physical_storage_remaining = max(0.0, storage_allocatable_gb - float(host.storage_used_gb or 0))\n''',
    '''    memory_limit_percent = thresholds["memory"] or 100\n    storage_limit_percent = thresholds["storage"] or 100\n    cpu_limit_percent = thresholds["cpu"] or 100\n    memory_allocatable_mb = int((host.memory_total_mb or 0) * memory_limit_percent / 100)\n\n    # Logical disk quota uses a narrowly normalized total so a configured 2 GiB\n    # LVM pool reported as 1.99 GiB does not lose an entire 1 GiB VPS slot.\n    # Physical capacity/watermarks deliberately keep the raw Agent total.\n    reported_storage_total_gb = max(0.0, float(host.storage_total_gb or 0))\n    quota_storage_total_gb = normalized_storage_quota_total_gb(reported_storage_total_gb)\n    quota_storage_allocatable_gb = quota_storage_total_gb * storage_limit_percent / 100\n    physical_storage_allocatable_gb = reported_storage_total_gb * storage_limit_percent / 100\n\n    logical_memory_remaining = max(0, memory_allocatable_mb - allocated_memory)\n    logical_storage_remaining = max(0.0, quota_storage_allocatable_gb - allocated_disk)\n    physical_memory_remaining = max(0, memory_allocatable_mb - int(host.memory_used_mb or 0))\n    physical_storage_remaining = max(0.0, physical_storage_allocatable_gb - float(host.storage_used_gb or 0))\n''',
)

replace_once(
    "panel/app/nodes.py",
    '''        "allocated_disk_gb": round(allocated_disk, 3),\n        "remaining_disk_gb": round(min(logical_storage_remaining, physical_storage_remaining), 3),\n        "logical_remaining_disk_gb": round(logical_storage_remaining, 3),\n        "physical_remaining_disk_gb": round(physical_storage_remaining, 3),\n        "storage_limit_percent": storage_limit_percent,\n''',
    '''        "allocated_disk_gb": round(allocated_disk, 3),\n        "reported_storage_total_gb": round(reported_storage_total_gb, 3),\n        "quota_storage_total_gb": round(quota_storage_total_gb, 3),\n        "remaining_disk_gb": round(min(logical_storage_remaining, physical_storage_remaining), 3),\n        "logical_remaining_disk_gb": round(logical_storage_remaining, 3),\n        "physical_remaining_disk_gb": round(physical_storage_remaining, 3),\n        "storage_limit_percent": storage_limit_percent,\n''',
)

replace_once(
    "panel/app/nodes.py",
    '''        if host.storage_total_gb:\n            projected_disk = (allocated_disk + float(plan.disk_gb or 0)) * 100 / host.storage_total_gb\n            limit = thresholds["storage"] or 100\n            if projected_disk > limit:\n                return {"allowed": False, "code": "capacity_storage", "label": "存储不足", "reason": f"开通后逻辑磁盘分配将达到 {projected_disk:.1f}%（上限 {limit}%）", "capacity": capacity}\n''',
    '''        if quota_storage_total_gb:\n            projected_disk = (allocated_disk + float(plan.disk_gb or 0)) * 100 / quota_storage_total_gb\n            limit = thresholds["storage"] or 100\n            if projected_disk > limit + 1e-9:\n                return {"allowed": False, "code": "capacity_storage", "label": "存储不足", "reason": f"开通后逻辑磁盘分配将达到 {projected_disk:.1f}%（上限 {limit}%）", "capacity": capacity}\n''',
)

replace_once(
    "panel/app/nodes.py",
    '''        disk_ratio = allocated_disk / max(host.storage_total_gb, 1.0)\n''',
    '''        disk_ratio = allocated_disk / max(normalized_storage_quota_total_gb(host.storage_total_gb), 1.0)\n''',
)

replace_once(
    "panel/app/nodes.py",
    '''            # v1.6.2: disk count is quota capacity, not raw thin-pool bytes.\n            # Physical natpool usage is still enforced by host_schedule_state's\n            # storage watermark before this estimator is allowed to return > 0.\n''',
    '''            # v1.6.3: logical quota already includes the narrow LVM alignment\n            # normalization; raw physical natpool usage still gates scheduling.\n''',
)


# ---------------------------------------------------------------------------
# Documentation / release notes.
# ---------------------------------------------------------------------------
replace_once("README.md", "**当前正式版本：XNAT v1.6.2**", "**当前正式版本：XNAT v1.6.3**")
replace_once("README.md", "| XNAT Release | v1.6.2 |", "| XNAT Release | v1.6.3 |")
replace_once("README.md", "| Panel | v1.6.2 |", "| Panel | v1.6.3 |")
replace_once(
    "README.md",
    "> v1.6.2 为 Panel 修复版本：修正 Host “按套餐预计可开”磁盘口径。VPS 磁盘数量按逻辑配额计算，Incus 镜像缓存 / LVM Thin metadata 等真实占用继续由 natpool 存储水位保护，不再被误当成已分配给小鸡的套餐磁盘。",
    "> v1.6.3 为 Panel 修复版本：修正 Incus/LVM 对齐造成的 natpool 微小容量误差。接近套餐分配边界的名义容量按安全容差归一化用于逻辑配额和调度，真实物理占用仍按 Host Agent 原始上报值执行存储水位保护。",
)
replace_once("README.md", "## 升级到 v1.6.2", "## 升级到 v1.6.3")
replace_once("README.md", "现有 **v1.6.1 Panel**：", "现有 **v1.6.2 Panel**：")
replace_once("README.md", "xnat update 1.6.2", "xnat update 1.6.3")

changelog = read("CHANGELOG.md")
entry = '''## v1.6.3\n\n- 修正 Incus/LVM Thin extent / metadata 对齐导致 natpool 实际上报略低于名义容量时，套餐预计数量少算一台的问题。\n- 逻辑配额仅在距离下一个 0.125GiB 分配边界不超过 32MiB 时向上归一化；不会把明显不足的存储容量强行补齐。\n- 实际调度与“预计可开”共用同一逻辑容量口径，避免 2GiB 名义池上报约 1.99GiB 时第二台 1GiB VPS 被错误判定超过 100% 上限。\n- Host Agent 上报的原始 natpool 总量 / 实际使用量保持不变，物理存储水位保护继续使用真实值。\n- Panel 升级至 v1.6.3；Host Agent 保持 v1.2.0、Agent API v1、Mobile API v1。\n- 正式支持 v1.6.2 → v1.6.3 原地升级。\n\n'''
if not changelog.startswith("# Changelog\n\n"):
    raise SystemExit("unexpected CHANGELOG header")
write("CHANGELOG.md", "# Changelog\n\n" + entry + changelog[len("# Changelog\n\n"):])

build = read("scripts/build-release.sh")
start_marker = 'cat > "$DIST/RELEASE_NOTES.md" <<EOF_NOTES'
start = build.find(start_marker)
if start < 0:
    raise SystemExit("release notes start missing")
end_marker = "\nEOF_NOTES"
end = build.find(end_marker, start)
if end < 0:
    raise SystemExit("release notes end missing")
end += len(end_marker)
release_block = '''cat > "$DIST/RELEASE_NOTES.md" <<EOF_NOTES
# XNAT v${RELEASE_VERSION}

本次版本修正 Panel 对 Incus/LVM natpool 微小对齐损耗的逻辑容量判断。

- Panel：v${PANEL_VERSION}
- Host Agent：v${AGENT_VERSION}
- Agent API：v${AGENT_API_VERSION}
- Mobile API：v1
- 逻辑 natpool 容量在距离下一个 0.125GiB 分配边界不超过 32MiB 时安全归一化
- 2GiB 名义 natpool 即使实际上报约 1.99GiB，1GiB 套餐在 100% 存储调度阈值下可正确预计并调度 2 台
- 90% 等较低存储调度阈值仍按管理员设置生效，不会被归一化绕过
- 原始物理 natpool 总量 / 使用量不修改，真实存储水位保护继续生效
- v1.6.2 → v${PANEL_VERSION} 支持原地升级，业务数据保持不变
- Host Agent 核心保持 v${AGENT_VERSION} / Agent API v${AGENT_API_VERSION}

**由 𝐍𝐀𝐌𝐄𝐋𝐄𝐒𝐒 和 GPT 倾力打造**

Panel 推荐升级命令：

    xnat update ${RELEASE_VERSION}
EOF_NOTES'''
write("scripts/build-release.sh", build[:start] + release_block + build[end:])


# ---------------------------------------------------------------------------
# Regression contracts.
# ---------------------------------------------------------------------------
replace_once(
    "scripts/check.sh",
    "assert '当前正式版本：XNAT v1.6.2' in readme",
    "assert '当前正式版本：XNAT v1.6.3' in readme",
)
replace_once(
    "scripts/check.sh",
    "print('v1.6.2 logical quota capacity contract: ok')\nPYV162\n",
    "print('v1.6.2 logical quota capacity contract: ok')\nPYV162\n\n# v1.6.3 narrow LVM alignment normalization must affect logical quota and\n# projected scheduling only; raw physical capacity/watermark stays untouched.\ngrep -q 'def normalized_storage_quota_total_gb' panel/app/nodes.py\ngrep -q 'quota_storage_total_gb' panel/app/nodes.py\ngrep -q 'reported_storage_total_gb' panel/app/nodes.py\ngrep -q '1.6.2) UPGRADE_PATH=\"verified-v1.6.2\"' scripts/upgrade-panel.sh\npython3 - <<'PYV163'\nfrom panel.app.nodes import normalized_storage_quota_total_gb\nassert normalized_storage_quota_total_gb(1.99) == 2.0\nassert normalized_storage_quota_total_gb(1.97) == 2.0\nassert normalized_storage_quota_total_gb(1.96) == 1.96\nassert normalized_storage_quota_total_gb(3.99) == 4.0\nassert normalized_storage_quota_total_gb(2.01) == 2.01\nprint('v1.6.3 LVM alignment normalization contract: ok')\nPYV163\n",
)

print("v1.6.3 patch applied")
