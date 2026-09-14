# XNAT

> 一个面向自建场景的 **多节点 NAT VPS 管理平台**，基于 **Incus + LVM Thin**，支持 LXC、KVM 与混合虚拟化。

XNAT 把用户、套餐、订单、Host、实例、NAT 端口、流量、生命周期、充值、通知和日常运维集中到一套 Panel 中管理。整体采用 **Panel Server + Host Agent** 分离架构：Panel 负责业务、调度与数据一致性，Host Agent 负责每台母机上的实例、网络、存储和底层执行。

如果你希望自己搭建一套可持续维护的 NAT VPS 平台，而不是依赖大量手工命令分别管理每台宿主机，XNAT 就是为这个场景设计的。

**当前正式版本：v1.0.2**

| 组件 | 版本 |
| --- | --- |
| XNAT Panel | v1.0.2 |
| XNAT Host Agent | v1.0.1 |
| Agent API | v2 |
| Mobile API | v1 |
| XNAT Android | v1.0.1 |

> v1.0.2 是版本元数据热修复：Panel 仍为 v1.0.2、Host Agent 仍为 v1.0.1、Agent API 仍为 v2、Mobile API 仍为 v1。本次只修正 v1.0.2 发布包中的 Host Agent 版本标识和 `xnat` 更新识别，不修改 VPS、Incus、NAT、Panel UI 或业务逻辑。

> v1.0.2 是一次稳定性、安全性与一致性更新。重点完善了开通幂等、任务原子抢占、Host 端口租约、Agent API 2 防重放、TLS 指纹校验、重装失败保护、镜像最低磁盘校验和最小化系统 SSH 兼容性，同时保持现有 Web UI 与主要交互不变。

> Panel v1.0.2 仍兼容 Agent API v1 / v2，便于分批升级 Host；完整的新安全与幂等能力需要 Host Agent v1.0.1 / Agent API v2。

---

## XNAT 能做什么

- **统一管理多台 Host**：一个 Panel 对接多台 Host Agent，集中查看节点状态、资源、水位和实例。
- **支持 LXC / KVM / 混合模式**：安装时自动检测机器条件与 `/dev/kvm`，只开放实际可用的模式。
- **自动开通 NAT VPS**：用户购买后由 Panel 调度 Host，自动完成实例创建、网络、端口与业务记录，并通过稳定实例身份与幂等操作降低重复开通和孤儿实例风险。
- **完整生命周期管理**：支持开机、关机、重启、重装、扩容、删除、到期、宽限、自动停机与延迟删除；重装会在破坏性操作前完成资源和镜像预检，并保留失败状态与回滚保护。
- **NAT 端口管理**：每台 Host 独立维护 TCP / UDP 端口池，通过 Host 级端口租约与数据库唯一约束降低并发分配冲突，并支持同步与余量告警。
- **镜像资源兼容校验**：购买、重装、Mobile API 与 Agent 侧统一校验系统镜像最低磁盘需求，避免把明显不兼容的任务送进执行队列。
- **真实容量与调度保护**：同时考虑 CPU、内存、磁盘、natpool、逻辑已分配资源和套餐需求，避免盲目超卖。
- **流量与带宽控制**：支持流量周期、用量统计、超额限速和付费自助流量重置。
- **业务能力**：包含套餐、库存、用户、余额、订单、USDT 充值、工单与审计。
- **通知能力**：支持 Telegram / SMTP，可覆盖 Host 离线、任务、备份、到期和容量告警等事件。
- **运维与安全**：支持 HTTPS、Cloudflare、管理端口保护、SQLite 备份、升级预检、失败回滚与脱敏诊断；Panel ↔ Host 支持 TLS 指纹校验、HMAC、nonce / request-id 防重放和短时间重放缓存。
- **Web + Mobile API**：Web Panel 与 Mobile API v1 共用同一套用户和业务数据；官方原生 Android 客户端当前为 v1.0.1。

---

## 架构

```text
                    ┌─────────────────────────────┐
                    │          XNAT Panel         │
                    │ 用户 / 套餐 / 订单 / 调度   │
                    │ 充值 / 通知 / 工单 / 备份   │
                    └──────────────┬──────────────┘
                                   │
                 HTTPS + TLS Pin + HMAC / Agent API v2
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

Android 客户端只连接 Panel 的 Mobile API v1，不直接连接 Host Agent，也不持有 Host Agent Token。

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

LXC 套餐最低可配置到 **1C / 64MB / 128MB**；KVM Guest 保留 **512MB / 4GB** 技术下限。系统镜像自身还可能有更高的最低系统盘要求，购买和重装时会按镜像策略再次校验。

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

## 管理、更新与诊断

Panel / Host 安装完成后统一使用：

```bash
xnat
```

可在交互菜单中完成状态查看、服务重启、日志、更新、备份、网络与诊断等常用操作。

升级到当前正式版时，建议顺序为：

1. 先通过 `xnat` 更新 Panel 到 v1.0.2；
2. 再逐台 Host 通过 `xnat` 更新 Host Agent 到 v1.0.1；
3. 在 Panel 中确认 Host 在线，并确认 Agent API 已切换到 v2。

如果 Host 已执行过 v1.0.2 更新但 `xnat` 仍显示 v1.0.0 / Agent API v1，请在确认 Panel 已为 v1.0.2 后执行：

```bash
XNAT_ALLOW_AGENT_API_CHANGE=1 xnat
```

然后重新进入 Host Agent 更新，v1.0.2 会同步正确的版本元数据。

导出自动脱敏诊断报告：

```bash
xnat doctor report
```

报告默认写入：

```text
/root/xnat-diagnostics/
```

---

## v1.0.2 更新

v1.0.2 在不改变现有 Web UI 与主要交互的前提下，重点处理跨层稳定性与安全边界：

- Provision 使用稳定身份与操作幂等机制，减少 Panel 超时后重复开通和孤儿实例风险；Panel 只有在 Host 明确确认实例不存在时才自动退款。
- Job 抢占改为原子状态切换，降低多个 Worker 同时执行同一任务的风险。
- NAT / SSH 公网端口使用 Host 级租约与唯一约束，降低并发分配冲突。
- Panel ↔ Host 加入 TLS 证书指纹固定与持久化；Agent API v2 使用 nonce / request-id 强化 HMAC 防重放。
- 重装流程增加破坏性操作前预检、失败状态和恢复保护，并为旧实例补齐 XNAT 身份元数据。
- 系统镜像增加最低磁盘策略，Web、Mobile、Worker 与 Agent 多层校验；当前内置策略兼容 Alpine 小容量实例，并阻止明显不足的 Debian / Ubuntu / KVM 系统盘配置。
- Host Agent 在最小化系统中补齐 SSH readiness 所需依赖与兼容检测，避免缺少 `ss` 等诊断工具导致误判。
- 版本体系统一为 Panel v1.0.2 / Host Agent v1.0.1 / Agent API v2 / Mobile API v1。
- 新增和共享的业务策略向 service 层收敛，减少 Web、Mobile 与 Job 之间的重复校验逻辑；不修改现有模板、主题和前端交互结构。

---

## v1.0.1 更新

v1.0.1 修复 Panel 卸载后的 Nginx 虚拟主机串站问题，并新增“保留备份 / 完全卸载”两种卸载方式。旧 Panel 域名会保留无业务数据的拒绝占位，避免误显示同机 Komari 或其他站点。

---

## v1.0.0 基线说明

v1.0.0 作为重新整理后的正式基线发布：Panel 与 Host 均从 v1.0.0 开始，Agent API 与 Mobile API 当时均为 v1。

这一基线保留现有 Panel 页面布局、视觉风格与业务能力，同时重新整理 Host 安装容量规划、公开版本展示、安装文案和发布结构。旧开发阶段版本不作为正式升级来源；切换到 v1.0.0 时请使用全新系统重新安装 Panel / Host。

---

## 文档

- [完整更新日志](CHANGELOG.md)
- [安装、节点接入、更新、备份与故障排查](docs/README.md)
- [Mobile API v1](docs/MOBILE_API.md)
- [安全说明](SECURITY.md)
- [XNAT Android](https://github.com/kkx999/XNAT-Android)

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
