# XNAT 项目介绍

XNAT 是一个面向自建场景的 **多节点 NAT VPS 管理平台**，基于 **Incus + LVM Thin**，支持 LXC、KVM 与混合虚拟化。

> **项目状态：测试阶段** — 当前项目仍在持续开发与验证中，部分功能和接口可能调整，暂不建议用于关键生产环境。

XNAT 将用户、套餐、订单、Host、实例、NAT 端口、流量、生命周期、充值、通知和日常运维集中到一套 Panel 中管理。整体采用 **Panel Server + Host Agent** 分离架构：Panel 负责业务、调度与数据一致性，Host Agent 负责每台母机上的实例、网络、存储和底层执行。

## 当前版本

| 组件 | 版本 |
| --- | --- |
| XNAT Release | v1.1.0 |
| XNAT Panel | v1.1.0 |
| XNAT Host Agent | v1.0.5 |
| Agent API | v2 |
| Mobile API | v1 |
| XNAT Android | v1.0.5 |

## 版本说明

v1.1.0 重点修正服务器删除与 NAT / SSH 端口安全：普通删除会先永久删除 Host 上真实实例，并在 Host 与 Panel 双重确认实例不存在后才清理 Panel；管理员保留独立“强制从 Panel 移除”入口，用于 Host 已重装、失联或凭据不可恢复等特殊场景。

Host Agent v1.0.5 增加实际 Incus proxy 端口占用查询与严格删除确认。Panel 分配新 SSH / NAT 公网端口时会避开 Host 实际已占用端口，Provision 遇到明确端口冲突时可自动换端口重试。

Web、Mobile API 与 Android 普通删除语义保持一致。Android 当前版本为 v1.0.5；Agent API 继续保持 v2，Mobile API 继续保持 v1。

## XNAT 能做什么

- **统一管理多台 Host**：一个 Panel 对接多台 Host Agent，集中查看节点状态、资源、水位和实例。
- **支持 LXC / KVM / 混合模式**：安装时自动检测机器条件与 `/dev/kvm`，只开放实际可用的模式。
- **自动开通 NAT VPS**：用户购买后由 Panel 调度 Host，自动完成实例创建、网络、端口与业务记录。
- **完整生命周期管理**：支持开机、关机、重启、重装、扩容、删除、到期、宽限、自动停机与延迟删除。
- **NAT 端口管理**：每台 Host 独立维护 TCP / UDP 端口池，并通过 Host 级端口租约与唯一约束降低并发分配冲突。
- **镜像资源兼容校验**：购买、重装、Mobile API 与 Agent 侧统一校验系统镜像最低磁盘需求。
- **真实容量与调度保护**：同时考虑 CPU、内存、磁盘、natpool、逻辑已分配资源和套餐需求。
- **流量与带宽控制**：支持流量周期、用量统计、超额限速和付费自助流量重置。
- **业务能力**：包含套餐、库存、用户、余额、订单、USDT 充值、工单与审计。
- **通知能力**：支持 Telegram / SMTP，可覆盖 Host 离线、任务、备份、到期和容量告警等事件。
- **运维与安全**：支持 HTTPS、Cloudflare、管理端口保护、SQLite 备份、升级预检、失败回滚与脱敏诊断；Panel ↔ Host 支持 TLS 指纹校验、HMAC、nonce / request-id 防重放。
- **Web + Mobile API**：Web Panel 与 Mobile API v1 共用同一套用户和业务数据。

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

## 适合的部署场景

XNAT 适合个人、小型团队或自建服务场景，例如：

- 一台 Panel 管理多台不同地区的 Host；
- 使用独立服务器或支持嵌套虚拟化的 VPS 作为母机；
- 需要自动销售、开通和管理 NAT VPS；
- 同时提供 LXC 与 KVM 套餐；
- 希望把端口、流量、账务、工单和通知统一进一套系统。

如果 Host 本身运行在 VPS 中，而你还需要在其中创建 KVM VM，上层宿主机必须开启 Nested Virtualization，并把 `/dev/kvm` 提供给当前 Host。

## 支持环境

Panel 与 Host 当前支持：

```text
Debian 12 Bookworm
Debian 13 Trixie
Ubuntu 22.04 LTS Jammy
Ubuntu 24.04 LTS Noble
Ubuntu 26.04 LTS Resolute
```

### Host 最低配置

| 模式 | 最低配置 | 说明 |
| --- | --- | --- |
| LXC | 1C / 1GB / 8GiB 总硬盘 | 自动给 Host 保留稳定运行空间 |
| KVM | 1C / 1GB / 12GiB 总硬盘 + `/dev/kvm` | 需要可用 KVM 硬件虚拟化 |
| LXC + KVM | 1C / 1GB / 12GiB 总硬盘 + `/dev/kvm` | 同时开放两种实例类型 |

内部容量规划中，LXC 会为 Host 保留约 **4GiB 总空间预算**，KVM / 混合模式约 **6GiB**。LXC 套餐最低可配置到 **1C / 64MB / 128MB**；KVM Guest 保留 **512MB / 3GB** 技术下限。系统镜像自身还可能有更高的最低系统盘要求。

## 更新记录

### v1.0.4

- 服务器详情新增 CPU / 内存 / 硬盘 / 实时下载与上传速率。
- 一个整体监控容器，内部 2×2 对称布局；CPU、内存、硬盘使用全圆角胶囊进度条。
- 页面每 5 秒刷新，不可见时停止请求。
- Host Agent 使用约 3 秒内存短缓存；CPU 与网速通过相邻采样计算，磁盘必要时低频兜底采样。
- 不写监控数据库、不保存历史曲线。
- Panel Web 与 Mobile API v1 共用同一套实时指标来源。
- v1.0.3 → v1.0.4 为正式验证的直接 Panel 升级路径。

### v1.0.3

- 系统镜像最低系统盘改为 Panel 后台逐镜像配置。
- 默认 Debian 12 / 13 为 1 GiB、Ubuntu 22.04 / 24.04 为 2 GiB、Alpine 3.24 为 1 GiB。
- LXC 直接使用后台配置；KVM 使用 `max(镜像配置, 3 GiB)`。
- 系统镜像后台支持编辑最低系统盘，新建镜像时也可指定。
- 旧数据库只执行一次安全迁移，不会持续覆盖管理员自定义值。
- Mobile API v1 系统镜像响应新增 `min_disk_gb`。
- v1.0.2 → v1.0.3 标记为正式验证的直接升级路径。
- Host Agent v1.0.3 保留 v1.0.2 的镜像磁盘策略修复，并增强安全重装：Incus 本地 rename 失败时可使用停止态临时副本兜底，回滚同样支持 copy 恢复；预备失败会返回真实 Incus 错误且不直接删除原实例。
- Agent API 与 Panel 整体 UI 保持不变。

### v1.0.2

- Provision 使用稳定身份与操作幂等机制，减少 Panel 超时后重复开通和孤儿实例风险；Panel 只有在 Host 明确确认实例不存在时才自动退款。
- Job 抢占改为原子状态切换，降低多个 Worker 同时执行同一任务的风险。
- NAT / SSH 公网端口使用 Host 级租约与唯一约束，降低并发分配冲突。
- Panel ↔ Host 加入 TLS 证书指纹固定与持久化；Agent API v2 使用 nonce / request-id 强化 HMAC 防重放。
- 重装流程增加破坏性操作前预检、失败状态和恢复保护，并为旧实例补齐 XNAT 身份元数据。
- 系统镜像增加最低磁盘策略，Web、Mobile、Worker 与 Agent 多层校验。
- Host Agent 在最小化系统中补齐 SSH readiness 所需依赖与兼容检测。
- 修正正式发布元数据，确保 Host Agent v1.0.1 / Agent API v2 被 `xnat` 正确识别。
- Panel UI、页面布局、视觉风格和主要业务交互保持不变。

### v1.0.1

修复 Panel 卸载后的 Nginx 虚拟主机串站问题，并新增“保留备份 / 完全卸载”两种卸载方式。旧 Panel 域名会保留无业务数据的拒绝占位，避免误显示同机 Komari 或其他站点。

### v1.0.0

v1.0.0 作为重新整理后的正式基线发布：Panel 与 Host 均从 v1.0.0 开始，Agent API 与 Mobile API 当时均为 v1。

这一基线保留现有 Panel 页面布局、视觉风格与业务能力，同时重新整理 Host 安装容量规划、公开版本展示、安装文案和发布结构。旧开发阶段版本不作为正式升级来源。

---

**由 NAMELESS 和 GPT 倾力打造**
