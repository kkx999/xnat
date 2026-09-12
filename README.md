# XNAT

> 基于 **Incus + LVM Thin** 的多节点 NAT VPS 管理平台。

XNAT 采用 **Panel Server + Host Agent** 分离架构，面向自建 NAT VPS 场景统一管理宿主机、LXC/KVM 实例、套餐、用户、端口、流量、生命周期、通知与运维。

**当前正式版本：XNAT v1.6.5**

| 组件 | 版本 |
| --- | --- |
| XNAT Release | v1.6.5 |
| Panel | v1.6.3 |
| Host Agent | v1.2.1 |
| Agent API | v1 |
| Mobile API | v1 |

> v1.6.5 修复 Host 安装阶段的磁盘规划：先安装 Incus/LVM/Python 与 Host Agent Runtime 并清理 APT 缓存，再按真实剩余空间计算 natpool；LXC 强制为 Host 长期保留约 2GiB，KVM/混合保留约 3GiB。Panel v1.6.3、Host Agent v1.2.1、Agent API v1 与 Mobile API v1 均保持兼容。

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

| 模式 | 最低安装 | 建议长期运行 | 说明 |
| --- | --- | --- | --- |
| LXC | 1C / 1GB / 4.5GiB 总硬盘 | 1C / 1GB / 8GiB+ | 先完成 Host 基础依赖与 Agent Runtime 安装，再按真实剩余空间计算 natpool；natpool 满载后仍为 Host 保留约 2GiB |
| KVM | 1C / 1GB / 6.5GiB 总硬盘 | 2C / 2GB / 12GiB+ | 需要可用 `/dev/kvm`；依赖与 Agent Runtime 安装完成后再计算，natpool 满载后仍为 Host 保留约 3GiB，natpool 至少 4GiB |
| LXC + KVM | 同 KVM | 2C / 2GB / 12GiB+ | 同时开放两种实例类型 |

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
3. 完成 Host 基础依赖 / Incus / Agent Runtime 安装后重新读取真实可用硬盘，再计算 natpool 安全上限。
4. Incus、LVM Thin、Bridge、Host Agent、防火墙与健康检查。

**NAT 用户端口池不在 Host 安装阶段填写。** Host 连接 Panel 后，在后台节点卡片配置端口范围并同步到 Agent。

---

## 升级到 v1.6.5

现有 Host 可直接原地同步本次 Release：

```bash
xnat update 1.6.5
```

v1.6.5 不修改 Panel 业务组件，也不修改 Host Agent 运行时 API：Panel 继续为 v1.6.3，Host Agent 继续为 v1.2.1，Agent API / Mobile API 继续为 v1。现有 VPS、Agent Token、TLS、Incus、natpool、端口和 `/etc/xnat/node.json` 均保持不变。

本版修复的是 **全新 Host 安装器的 natpool 容量规划**：Incus 与 Host Agent Runtime 安装完成后才读取根分区真实剩余空间；LXC 以约 2GiB、KVM/混合以约 3GiB 作为长期 Host 预留，并在创建 LVM Thin 前再次校验，避免把“安装前空闲空间”误算给 natpool。

> 已经创建好的 natpool 不会被升级器自动缩容。自动缩小现有 LVM Thin 风险很高，因此旧 Host 如果已经因为 natpool 过大而接近满盘，应扩容 Host 系统盘，或迁移/重装 Host 后按 v1.6.5 新算法重新分配；不要删除 `/var/lib/incus/disks`。

v1.6.4 引入的 512MiB 根分区保护、白名单安全清理和 HTTP 507 fail-closed 机制继续保留。

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
