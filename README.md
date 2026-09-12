# XNAT

> 一个面向自建场景的 **多节点 NAT VPS 管理平台**，基于 **Incus + LVM Thin**，支持 LXC、KVM 与混合虚拟化。

XNAT 把用户、套餐、订单、Host、实例、NAT 端口、流量、生命周期、充值、通知和日常运维集中到一套 Panel 中管理。整体采用 **Panel Server + Host Agent** 分离架构：Panel 负责业务与调度，Host Agent 负责每台母机上的实例、网络和存储操作。

如果你希望自己搭建一套可持续维护的 NAT VPS 平台，而不是依赖大量手工命令分别管理每台宿主机，XNAT 就是为这个场景设计的。

**当前正式版本：v1.0.1**

| 组件 | 版本 |
| --- | --- |
| XNAT Panel | v1.0.1 |
| XNAT Host | v1.0.0 |
| Agent API | v1 |
| Mobile API | v1 |

> v1.0.0 是重新整理后的正式基线。Panel 与 Host 统一从 v1.0.0 开始；旧开发阶段版本不提供原地升级兼容，建议在全新系统上部署。

> v1.0.1 修复 Panel 卸载后的 Nginx 虚拟主机串站问题，并新增‘保留备份 / 完全卸载’两种卸载方式。旧 Panel 域名会保留无业务数据的拒绝占位，避免误显示同机 Komari 或其他站点。

---

## XNAT 能做什么

- **统一管理多台 Host**：一个 Panel 对接多台 Host Agent，集中查看节点状态、资源、水位和实例。
- **支持 LXC / KVM / 混合模式**：安装时自动检测机器条件与 `/dev/kvm`，只开放实际可用的模式。
- **自动开通 NAT VPS**：用户购买后由 Panel 调度 Host，自动完成实例创建、网络、端口与业务记录。
- **完整生命周期管理**：支持开机、关机、重启、重装、扩容、删除、到期、宽限、自动停机与延迟删除。
- **NAT 端口管理**：每台 Host 独立维护 TCP / UDP 端口池，支持冲突检测、同步与余量告警。
- **真实容量与调度保护**：同时考虑 CPU、内存、磁盘、natpool、逻辑已分配资源和套餐需求，避免盲目超卖。
- **流量与带宽控制**：支持流量周期、用量统计、超额限速和付费自助流量重置。
- **业务能力**：包含套餐、库存、用户、余额、订单、USDT 充值、工单与审计。
- **通知能力**：支持 Telegram / SMTP，可覆盖 Host 离线、任务、备份、到期和容量告警等事件。
- **运维与安全**：支持 HTTPS、Cloudflare、管理端口保护、SQLite 备份、升级预检、失败回滚与脱敏诊断。
- **Web + Mobile API**：Web Panel 与 Mobile API v1 共用同一套用户和业务数据，可供原生客户端接入。

---

## 架构

```text
                    ┌─────────────────────────────┐
                    │          XNAT Panel         │
                    │ 用户 / 套餐 / 订单 / 调度   │
                    │ 充值 / 通知 / 工单 / 备份   │
                    └──────────────┬──────────────┘
                                   │
                         HTTPS + HMAC / API v1
                                   │
             ┌─────────────────────┴─────────────────────┐
             │                                           │
             ▼                                           ▼
    ┌─────────────────┐                         ┌─────────────────┐
    │  Host Agent A   │           ...           │  Host Agent N   │
    └────────┬────────┘                         └────────┬────────┘
             │                                           │
             ▼                                           ▼
      Incus + LVM Thin                            Incus + LVM Thin
        LXC / KVM                                   LXC / KVM
```

Panel 与 Host Node 建议分开部署。Host Agent 管理入口默认只允许指定的 Panel Server 访问。

---

## 适合的部署场景

XNAT 适合个人、小型团队或自建服务场景，例如：

- 一台 Panel 管理多台不同地区的 Host；
- 使用独立服务器或支持嵌套虚拟化的 VPS 作为母机；
- 需要自动销售、开通和管理 NAT VPS；
- 同时提供 LXC 与 KVM 套餐；
- 希望把端口、流量、账务、工单和通知统一进一套系统。

如果 Host 本身运行在 VPS 中，而你还需要在其中创建 KVM VM，上层宿主机必须开启 Nested Virtualization，并把 `/dev/kvm` 提供给当前 Host。

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

Host 安装器会在创建存储池之前检测系统、CPU、内存、磁盘与 `/dev/kvm`，并根据真实资源判断可用虚拟化模式。

### Host 最低配置

| 模式 | 最低配置 | 说明 |
| --- | --- | --- |
| LXC | 1C / 1GB / 8GiB 总硬盘 | 自动给 Host 保留稳定运行空间 |
| KVM | 1C / 1GB / 12GiB 总硬盘 + `/dev/kvm` | 需要可用 KVM 硬件虚拟化 |
| LXC + KVM | 1C / 1GB / 12GiB 总硬盘 + `/dev/kvm` | 同时开放两种实例类型 |

内部容量规划中，LXC 会为 Host 保留约 **4GiB 总空间预算**，KVM / 混合模式约 **6GiB**。这个预算包含系统与 XNAT 已经使用的空间以及后续运行余量，并不是额外要求始终保持 4 / 6GiB 空闲。

LXC 套餐最低可配置到 **1C / 64MB / 128MB**；KVM Guest 保留 **512MB / 4GB** 技术下限。

---

## 一键安装 Panel

请在支持的全新系统上执行：

```bash
apt-get update && apt-get install -y curl ca-certificates && \
bash <(curl -fsSL https://raw.githubusercontent.com/kkx999/xnat/main/scripts/bootstrap-panel.sh)
```

安装过程中可配置 Panel 域名、HTTPS 与 Cloudflare。

---

## 一键安装 Host

```bash
apt-get update && apt-get install -y curl ca-certificates && \
bash <(curl -fsSL https://raw.githubusercontent.com/kkx999/xnat/main/scripts/bootstrap-host.sh)
```

Host 安装流程会自动完成依赖、Incus、LVM Thin、Host Agent、网络、防火墙与健康检查。普通安装只需要确认 Panel Server、选择虚拟化模式，并填写准备给所有小鸡共享的总硬盘。

XNAT 会在主要依赖和 Host Runtime 安装完成后重新读取真实磁盘空间，再计算小鸡可用容量，并在真正创建存储池前再次检查，避免把 Host 系统盘压到危险水位。

**NAT 用户端口池不在 Host 安装阶段填写。** Host 接入 Panel 后，再在后台节点配置端口范围并同步到 Agent。

---

## 管理与诊断

Panel / Host 安装完成后统一使用：

```bash
xnat
```

可在交互菜单中完成状态查看、服务重启、日志、更新、备份、网络与诊断等常用操作。

导出自动脱敏诊断报告：

```bash
xnat doctor report
```

报告默认写入：

```text
/root/xnat-diagnostics/
```

---

## v1.0.0 基线说明

v1.0.0 作为重新整理后的正式基线发布：Panel 与 Host 均从 v1.0.0 开始，Agent API 与 Mobile API 保持 v1。

这一基线保留现有 Panel 页面布局、视觉风格与业务能力，同时重新整理 Host 安装容量规划、公开版本展示、安装文案和发布结构。旧开发阶段版本不作为正式升级来源；切换到 v1.0.0 时请使用全新系统重新安装 Panel / Host。

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
