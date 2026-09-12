from pathlib import Path
import json


def read(path):
    return Path(path).read_text(encoding="utf-8")


def write(path, text):
    Path(path).write_text(text, encoding="utf-8")


def replace_once(path, old, new):
    text = read(path)
    if old not in text:
        raise SystemExit(f"anchor missing in {path}: {old[:160]!r}")
    write(path, text.replace(old, new, 1))


# ---------------------------------------------------------------------------
# Release / component metadata. v1.6.2 is Panel-only; Host Agent/API stay put.
# ---------------------------------------------------------------------------
write("VERSION", "1.6.2\n")
write("panel/VERSION", "1.6.2\n")
replace_once("panel/app/__init__.py", '__version__ = "1.6.1"', '__version__ = "1.6.2"')
replace_once("panel/app/main.py", '"version": "1.6.1"', '"version": "1.6.2"')
replace_once("panel/app/templates/base.html", "XNAT v1.6.1 Multi-Node", "XNAT v1.6.2 Multi-Node")
replace_once("panel/app/nodes.py", '"User-Agent": "XNAT-Panel/1.6.1",', '"User-Agent": "XNAT-Panel/1.6.2",')
replace_once("docs/MOBILE_API.md", "XNAT Panel `v1.6.1`", "XNAT Panel `v1.6.2`")

meta = json.loads(read("release.json"))
meta["release_version"] = "1.6.2"
meta["panel_version"] = "1.6.2"
assert meta["agent_version"] == "1.2.0"
assert str(meta["agent_api_version"]) == "1"
assert str(meta["mobile_api_version"]) == "1"
write("release.json", json.dumps(meta, ensure_ascii=False, indent=2) + "\n")

replace_once(
    "scripts/upgrade-panel.sh",
    'case "$CURRENT_VERSION" in\n  1.6.0) UPGRADE_PATH="verified-v1.6.0" ;;',
    'case "$CURRENT_VERSION" in\n  1.6.1) UPGRADE_PATH="verified-v1.6.1" ;;\n  1.6.0) UPGRADE_PATH="verified-v1.6.0" ;;',
)


# ---------------------------------------------------------------------------
# v1.6.2 capacity semantics.
#
# Quota capacity must be based on logical storage allocation. Incus image cache
# and LVM thin metadata are real physical usage, but are not VPS quota already
# assigned to customers. Physical natpool usage remains a hard scheduling
# watermark through host_schedule_state; it simply no longer subtracts from the
# quota count twice.
# ---------------------------------------------------------------------------
replace_once(
    "panel/app/nodes.py",
    '''    This is a Panel-only display helper. It uses the same Host scheduling state\n    and conservative remaining memory/storage values as real placement, then\n    also caps by max_vps and remaining NAT ports (one SSH port is required per\n    newly provisioned instance). CPU stays a live watermark just like the\n    scheduler; without changing Agent API v1 we intentionally do not invent a\n    physical-core count.\n''',
    '''    This is a Panel-only display helper. It uses the same Host scheduling state.\n    Memory remains conservative (logical/physical minimum), while disk quota count\n    uses logical remaining storage so Incus image cache and LVM thin metadata are\n    not mistaken for customer VPS quota. Real natpool usage still gates scheduling\n    through the storage watermark. max_vps and remaining NAT ports also cap the\n    count. CPU stays a live watermark because Agent API v1 does not report a total\n    physical-core capacity.\n''',
)
replace_once(
    "panel/app/nodes.py",
    '''        limits = {\n            "内存": max(0, int(float(cap.get("remaining_memory_mb") or 0) // memory_mb)),\n            "存储": max(0, int(float(cap.get("remaining_disk_gb") or 0) // disk_gb)),\n            "NAT端口": max(0, port_remaining),\n        }''',
    '''        limits = {\n            "内存": max(0, int(float(cap.get("remaining_memory_mb") or 0) // memory_mb)),\n            # v1.6.2: disk count is quota capacity, not raw thin-pool bytes.\n            # Physical natpool usage is still enforced by host_schedule_state's\n            # storage watermark before this estimator is allowed to return > 0.\n            "存储": max(0, int(float(cap.get("logical_remaining_disk_gb") or 0) // disk_gb)),\n            "NAT端口": max(0, port_remaining),\n        }''',
)

# Keep the display explicit: real storage usage and quota estimate are different
# concepts, but both remain visible on the card.
replace_once(
    "panel/app/templates/admin.html",
    '<div><span>存储</span><strong>{{ \'%.1f\'|format(h.storage_used_gb or 0) }} / {{ \'%.1f\'|format(h.storage_total_gb or 0) }} GB</strong></div>',
    '<div><span>存储实际</span><strong>{{ \'%.1f\'|format(h.storage_used_gb or 0) }} / {{ \'%.1f\'|format(h.storage_total_gb or 0) }} GB</strong></div>',
)
replace_once(
    "panel/app/templates/admin.html",
    '内存 / 存储 / VPS上限 / NAT端口取最小值；CPU按调度水位实时判定',
    '内存 / 逻辑存储 / VPS上限 / NAT端口取最小值；实际存储继续水位保护',
)


# ---------------------------------------------------------------------------
# README: current-project landing page instead of carrying old upgrade manuals.
# Historical details remain in CHANGELOG.md and docs/.
# ---------------------------------------------------------------------------
readme = r'''# XNAT

> 基于 **Incus + LVM Thin** 的多节点 NAT VPS 管理平台。

XNAT 采用 **Panel Server + Host Agent** 分离架构，面向自建 NAT VPS 场景统一管理宿主机、LXC/KVM 实例、套餐、用户、端口、流量、生命周期、通知与运维。

**当前正式版本：XNAT v1.6.2**

| 组件 | 版本 |
| --- | --- |
| XNAT Release | v1.6.2 |
| Panel | v1.6.2 |
| Host Agent | v1.2.0 |
| Agent API | v1 |
| Mobile API | v1 |

> v1.6.2 为 Panel 修复版本：修正 Host “按套餐预计可开”磁盘口径。VPS 磁盘数量按逻辑配额计算，Incus 镜像缓存 / LVM Thin metadata 等真实占用继续由 natpool 存储水位保护，不再被误当成已分配给小鸡的套餐磁盘。

---

## 架构

```text
用户 / 管理员
      │
      ▼
┌──────────────┐
│ XNAT Panel   │  用户、套餐、订单、调度、通知、备份
└──────┬───────┘
       │ HTTPS + HMAC / Agent API v1
       ▼
┌──────────────┐      ┌──────────────┐
│ Host Agent A │ ...  │ Host Agent N │
└──────┬───────┘      └──────┬───────┘
       │                     │
       ▼                     ▼
 Incus + LVM Thin       Incus + LVM Thin
 LXC / KVM              LXC / KVM
```

Panel 与 Host Node 建议分开部署。Host Agent 管理端口默认只允许 Panel Server 访问。

---

## 核心能力

- **多节点调度**：Panel + 多 Host Agent，支持节点启停、维护 / Drain、最大 VPS 与资源水位保护。
- **LXC / KVM / 混合模式**：Host 安装时检测 `/dev/kvm`，按机器条件开放可用虚拟化模式。
- **真实 Host 容量**：展示 CPU、实际内存、natpool 实际占用、逻辑已分配资源与调度余量。
- **按套餐预计可开**：常驻显示每个有效套餐当前预计还能创建多少台，并显示容量瓶颈。
- **NAT VPS 生命周期**：自动开通、开关机、重装、扩容、删除，到期提醒、宽限、自动停机与可选延迟删除。
- **NAT 端口**：Host 独立 TCP / UDP 端口池，Panel 配置后同步到 Agent，并处理端口冲突与余量告警。
- **资源与流量**：CPU / 内存 / 磁盘 / 带宽、流量周期、超额限速、付费自助流量重置。
- **套餐与业务**：套餐、库存、用户、余额、订单、USDT 充值、工单与审计。
- **通知**：Telegram / SMTP，覆盖 Host 离线、natpool 水位、任务、备份、到期等事件。
- **运维与安全**：HTTPS / Cloudflare、敏感管理端口保护、SQLite 备份、升级预检、失败回滚、`xnat doctor` 脱敏诊断。
- **Web / Mobile API**：用户端与管理后台独立明暗主题；Mobile API v1 与 Web Panel 共用同一业务数据。

---

## 支持环境

Panel 与 Host 当前支持：

```text
Debian 12 Bookworm
Debian 13 Trixie
Ubuntu 22.04 LTS Jammy
Ubuntu 24.04 LTS Noble
Ubuntu 26.04 LTS Resolute
```

Host 安装器会先检测系统、CPU、总/可用内存、总/已用/可用硬盘与 `/dev/kvm`，再显示每种虚拟化模式能否安装及原因。

### Host 基线

| 模式 | Host 基线 | 说明 |
| --- | --- | --- |
| LXC | 1C / 1GB / 4.5GiB 总硬盘 | 从当前可用空间预留约 1GiB 给系统/XNAT，再计算 natpool |
| KVM | 1C / 1GB / 6.5GiB 总硬盘 | 需要可用 `/dev/kvm`，预留约 1.5GiB，natpool 至少 4GiB |
| LXC + KVM | 同 KVM | 同时开放两种实例类型 |

LXC 套餐最低可配置到 **1C / 64MB / 128MB**；KVM Guest 保留 **512MB / 4GB** 技术下限。

> 如果 Host 自身也是一台 VPS，而你希望在其中继续运行 KVM VM，上层宿主机必须开放 Nested Virtualization，并让 `/dev/kvm` 对当前 Host 可用。

---

## Panel 一键安装

在受支持的全新系统上执行：

```bash
apt-get update && apt-get install -y curl ca-certificates && \
bash <(curl -fsSL https://raw.githubusercontent.com/kkx999/xnat/main/scripts/bootstrap-panel.sh)
```

安装过程中可配置 Panel 域名、HTTPS 与 Cloudflare。

---

## Host 一键安装

```bash
apt-get update && apt-get install -y curl ca-certificates && \
bash <(curl -fsSL https://raw.githubusercontent.com/kkx999/xnat/main/scripts/bootstrap-host.sh)
```

安装器会依次处理：

1. Panel Server 真实公网 IPv4，用于限制 Host Agent 管理入口。
2. LXC / KVM / LXC + KVM 模式检测与选择。
3. Host 真实资源检测与 natpool 安全建议。
4. Incus、LVM Thin、Bridge、Host Agent、防火墙与健康检查。

**NAT 用户端口池不在 Host 安装阶段填写。** Host 连接 Panel 后，在后台节点卡片配置端口范围并同步到 Agent。

---

## 升级到 v1.6.2

现有 **v1.6.1 Panel**：

```bash
xnat update 1.6.2
```

升级器会执行 Release 校验、SQLite `PRAGMA quick_check`、备份、原地更新、健康检查与失败回滚，并保留 `.env`、数据库、用户、余额、订单、VPS、Host、套餐、端口、支付、通知、工单等数据。

本次 **Host Agent 仍为 v1.2.0 / Agent API v1**，Host 不需要重装，也不要求升级 Agent 核心。

更早版本的升级历史与兼容说明请查看 [CHANGELOG.md](CHANGELOG.md) 和 [docs/README.md](docs/README.md)。

---

## 管理与诊断

统一管理入口：

```bash
xnat
```

导出自动脱敏诊断报告：

```bash
xnat doctor report
```

报告默认写入 `/root/xnat-diagnostics/`。

---

## 文档

- [完整更新日志](CHANGELOG.md)
- [安装、节点接入、更新、备份与故障排查](docs/README.md)
- [Mobile API v1](docs/MOBILE_API.md)
- [安全说明](SECURITY.md)

---

## License

MIT License

---

<div align="center">

### XNAT

**由 𝐍𝐀𝐌𝐄𝐋𝐄𝐒𝐒 和 GPT 倾力打造**

</div>

---

## 免责声明

本项目仅供学习、研究与技术交流使用。使用者应遵守所在地法律法规，不得将本项目用于任何违法或未经授权的用途。因使用本项目产生的任何违法行为、损失或法律责任均由使用者自行承担，与项目作者及贡献者无关。
'''
write("README.md", readme)


# ---------------------------------------------------------------------------
# Changelog and release notes.
# ---------------------------------------------------------------------------
changelog = read("CHANGELOG.md")
entry = '''## v1.6.2\n\n- 修正 Host“按套餐预计可开”磁盘口径：套餐磁盘按逻辑已分配 / 逻辑剩余容量计算，不再把 Incus 镜像缓存、LVM Thin metadata 等物理占用重复扣成 VPS 配额。\n- natpool 实际占用继续参与存储水位保护；达到阈值时仍停止新实例调度，不降低现有安全边界。\n- Host 卡片把物理池指标明确标注为“存储实际”，预计数量说明同步区分逻辑存储与实际存储水位。\n- 重构 GitHub README 首页，集中展示当前架构、支持系统、LXC/KVM Host 基线、核心能力、安装与 v1.6.2 升级方式；旧版升级细节继续保留在 CHANGELOG / docs。\n- Panel 升级至 v1.6.2；Host Agent 保持 v1.2.0、Agent API v1、Mobile API v1。\n- 正式支持 v1.6.1 → v1.6.2 原地升级。\n\n'''
if not changelog.startswith("# Changelog\n\n"):
    raise SystemExit("unexpected CHANGELOG header")
write("CHANGELOG.md", "# Changelog\n\n" + entry + changelog[len("# Changelog\n\n"):])

build = read("scripts/build-release.sh")
start_marker = 'cat > "$DIST/RELEASE_NOTES.md" <<EOF_NOTES'
start = build.find(start_marker)
if start < 0:
    raise SystemExit("release note block start missing")
end_marker = "\nEOF_NOTES"
end = build.find(end_marker, start)
if end < 0:
    raise SystemExit("release note block end missing")
end += len(end_marker)
release_block = '''cat > "$DIST/RELEASE_NOTES.md" <<EOF_NOTES
# XNAT v${RELEASE_VERSION}

本次版本修正 Panel 的 Host 套餐容量估算，并重构 GitHub 项目首页。

- Panel：v${PANEL_VERSION}
- Host Agent：v${AGENT_VERSION}
- Agent API：v${AGENT_API_VERSION}
- Mobile API：v1
- 套餐磁盘预计数量按逻辑剩余配额计算，不再把 Incus 镜像缓存 / LVM Thin metadata 当成已分配 VPS 磁盘
- natpool 实际使用率继续执行存储水位保护，安全调度边界不变
- 2.0GB natpool、实际占用 0.2GB、逻辑已分配 0GB 时，1GB 磁盘套餐正确显示约 2 台
- Host 卡片明确区分存储实际占用与套餐逻辑容量
- README 更新为当前版本架构、能力、支持环境、安装与升级首页
- v1.6.1 → v${PANEL_VERSION} 支持原地升级，业务数据保持不变
- Host Agent 核心保持 v${AGENT_VERSION} / Agent API v${AGENT_API_VERSION}

**由 𝐍𝐀𝐌𝐄𝐋𝐄𝐒𝐒 和 GPT 倾力打造**

Panel 推荐升级命令：

    xnat update ${RELEASE_VERSION}
EOF_NOTES'''
write("scripts/build-release.sh", build[:start] + release_block + build[end:])


# ---------------------------------------------------------------------------
# Regression contract: preserve v1.6.1 visible UI and assert the v1.6.2 split
# between logical quota capacity and physical natpool watermark.
# ---------------------------------------------------------------------------
replace_once(
    "scripts/check.sh",
    '''print('v1.6.1 visible Host plan capacity contract: ok')\nPYV161\n\n# v1.3.2 Mobile API v1 contract for XNAT Android v1.0.0.''',
    '''print('v1.6.1 visible Host plan capacity contract: ok')\nPYV161\n\n# v1.6.2 quota-capacity semantics: disk estimates use logical quota, while\n# physical natpool usage remains available for scheduling watermarks.\ngrep -q 'logical_remaining_disk_gb' panel/app/nodes.py\ngrep -q '存储实际' panel/app/templates/admin.html\ngrep -q '1.6.1) UPGRADE_PATH="verified-v1.6.1"' scripts/upgrade-panel.sh\npython3 - <<'PYV162'\nfrom pathlib import Path\nnodes=Path('panel/app/nodes.py').read_text()\nfn=nodes.split('def host_plan_capacity_estimates',1)[1].split('def select_host_for_plan',1)[0]\nassert 'cap.get("logical_remaining_disk_gb")' in fn, 'disk estimator must use logical quota capacity'\nassert 'cap.get("remaining_disk_gb")' not in fn, 'physical/min storage must not be double-counted as VPS quota'\nadmin=Path('panel/app/templates/admin.html').read_text()\nassert '实际存储继续水位保护' in admin, 'physical storage watermark explanation missing'\nreadme=Path('README.md').read_text()\nassert '当前正式版本：XNAT v1.6.2' in readme\nassert '指定 v1.4.3 安装' not in readme, 'legacy upgrade manual returned to project landing page'\nprint('v1.6.2 logical quota capacity contract: ok')\nPYV162\n\n# v1.3.2 Mobile API v1 contract for XNAT Android v1.0.0.''',
)

print("v1.6.2 patch applied")
