# XNAT

> 基于 **Incus + LVM Thin** 的多节点 NAT VPS 管理平台。

XNAT 采用 **Panel Server + Host Agent** 分离架构，用于管理 NAT VPS、多宿主机节点、套餐、用户、流量、通知及日常运维。

当前版本：**v1.4.1**


## v1.4.1 机器展示、充值与通知体验

v1.4.1 是基于 v1.4.0 的兼容性增量更新，重点收口机器展示身份、套餐展示信息、充值订单状态与 Telegram 用户通知引导。数据库升级继续采用 additive migration，不重建旧表。

- 修复后台“扣除余额”在浏览器提交参数丢失时可能被错误解释为增加余额的问题；新版余额表单缺失/非法操作类型时 fail closed，并保留旧 signed amount 兼容路径。
- Host 节点使用“前端展示旗帜 + 机器编号前缀”管理用户侧身份；Panel 生成稳定展示编号（如 `TYO-0002`），Host 内部 `nat-*` 实例名保持不变。
- 套餐保留服务器地区、网络线路与 NAT 端口作为购买页展示元数据；已开通服务器保存地区/线路快照，后续编辑套餐不会改写旧机器展示。
- 用户服务器卡片/详情顶部使用本地独立 SVG 国旗资产和稳定编号；详情概览精简为公网主机、私网 IPv4、SSH、配置、虚拟化、当前带宽、NAT 端口、剩余流量。
- 充值订单支持用户主动取消；已取消订单若随后检测到链上付款会进入异常支付并停止自动入账，避免并发/延迟付款造成余额异常。
- Telegram 通知增加配置状态联动：管理员保存 Bot Token 时通过 `getMe` 验证并识别机器人用户名；用户端提供 Start 引导、打开机器人和测试消息，未配置 Bot 时不能误开启通知。
- CSS / JS 与国旗资源使用内容指纹 cache-bust，减少浏览器复用旧静态资源造成的界面回退错觉。
- Mobile API 继续保持 v1；新增展示字段均为向后兼容字段，旧客户端仍可继续使用内部 `name`，重装确认同时兼容稳定编号与旧内部实例名。

升级：

```bash
xnat update 1.4.1
```

版本关系：**XNAT Release v1.4.1 / Panel v1.4.1 / Mobile API v1 / Host Agent v1.1.1 / Agent API v1**。Host Agent 核心协议不变。


## v1.4.0 运维、交互与后台管理

v1.4.0 在 v1.3.3 已验证的统一 `xnat` CLI 基础上，重点强化升级安全与服务器排障能力。Panel 与 Host 的“系统诊断”现在可检查磁盘、服务、端口、防火墙及组件关键状态，并可一键导出自动脱敏的诊断报告。

升级流程新增 **升级预检**：正式写入文件前先下载并验证 Release 源码，检查 Debian 12、磁盘空间、备份目录、systemd、SQLite、Agent API 兼容、Incus/TLS 与升级脚本完整性；存在阻断问题时不会继续更新。

同时，本版本完成了前后台体验收口：管理后台工单可查看完整历史会话并按状态分区归档；SQLite 备份支持安全删除；账户登录记录默认仅显示最近 3 条并可独立展开；通知偏好改为等尺寸 Switch；通知服务、站点设置与长记录页面按功能折叠；后台用户余额操作明确拆分为“增加余额 / 扣除余额”，扣除不会允许余额变负。用户前端和管理后台均保留实机验证通过的 hover / press / focus / 页面跳转进度与主题过渡反馈。

Panel 与 Host 都建议同步到本 Release：

```bash
xnat update 1.4.0
```

需要导出排障信息时：

```bash
xnat doctor report
```

报告默认保存到 `/root/xnat-diagnostics/`，并自动脱敏 Token、密码、API Key、Authorization/Bearer、JWT 等敏感内容。

版本关系：**XNAT Release v1.4.0 / Panel v1.4.0 / Mobile API v1 / Host Agent v1.1.1 / Agent API v1**。Host Agent 运行核心与 Agent API 不变；Host 更新主要同步 v1.4.0 的管理 CLI、预检和诊断能力。


## v1.3.2 Android / Mobile API v1

v1.3.2 将此前为 XNAT Android 开发的 Mobile API v1 正式纳入 Panel Release。浏览器 Web Panel 继续使用原有 Session + CSRF；Android 使用 `/api/v1` Bearer Token 接口。

正式能力包括：

- Android 登录、账户、概览和服务器状态；
- VPS 开机 / 关机 / 重启、NAT 端口添加删除、系统重装；
- 账务与充值记录读取、工单创建/回复/关闭；
- 套餐目录、优惠码试算、余额购买、自动调度开通；
- 购买 `request_id` 幂等保护，避免网络重试造成重复扣款或重复开通。

从 v1.3.1 升级 Panel：

```bash
xnat update 1.3.2
```

**升级兼容性：** v1.3.1 的 `.env`、SQLite 数据库、用户、余额、订单、VPS、Host、套餐、端口、工单和支付数据全部原地保留；升级前自动备份，失败自动尝试回滚。本次没有破坏性数据库迁移。Host Agent 继续保持 **v1.1.0 / Agent API v1**，无需因为 v1.3.2 升级 Host。

XNAT Android v1.0.0 对应 Panel v1.3.2 / Mobile API v1。

## v1.3.1 Mobile Navigation

v1.3.1 是 Panel 的兼容性与移动端交互更新：手机端用户中心改为可折叠 off-canvas Drawer，并修复透明遮罩拦截点击、Android 底部手势栏遮挡账户区域等问题。

从 v1.3.0 升级 Panel：

```bash
xnat update 1.3.1
```

Host Agent 仍为 **v1.1.0 / Agent API v1**。如果 Host 已经是 Agent v1.1.0，无需因 v1.3.1 再次升级。

## v1.3.0 Hybrid Virtualization

XNAT Host 现在支持 **LXC / KVM / LXC + KVM**。全新 Host 安装时会检测 `/dev/kvm` 并交互选择模式；套餐可指定 LXC 或 KVM，调度器只会选择匹配且 KVM 实际可用的节点。Host Agent 版本升级为 **v1.1.0**，Agent API 继续保持 v1。

从 v1.2.0 升级：

```bash
xnat update 1.3.0
```

Panel 与 Host 都需要执行更新；Host 首次升级到 Agent v1.1.0 时会保存虚拟化模式。

---

## 主要能力

- 多节点 Panel + Host Agent
- Incus + LVM Thin
- NAT VPS 自动开通、重装、删除
- CPU / 内存 / 磁盘 / 带宽管理
- TCP / UDP NAT 端口
- 流量统计、独立流量周期、超额限速与付费自助流量重置
- 节点维护 / Drain 与资源水位调度保护
- 宿主机剩余可分配资源展示与紧凑节点管理
- 到期提醒、宽限期、自动停机与可选延迟删除
- Host 离线、natpool、任务和备份异常通知
- 套餐、库存、用户与订单
- USDT 充值
- Telegram / SMTP 通知
- 工单、审计与数据库备份
- Panel 域名、HTTPS、Cloudflare
- XNAT 敏感管理端口自动保护
- 用户端与管理后台独立深色 / 明亮主题
- 统一 Toast、Switch、网页确认 Modal 与操作反馈
- 独立公告中心：历史公告、未读提示、首次登录重点公告与后台公告管理
- `xnat` 统一管理命令、升级预检、增强系统诊断与脱敏诊断报告

---

## 环境要求

```text
Debian 12 Bookworm
```

Panel 与 Host Node 建议分开部署。

Host 需要支持 Incus / LXC 所需的虚拟化能力。

---

# Panel 一键安装

全新 Debian 12：

```bash
apt-get update && apt-get install -y curl ca-certificates && \
bash <(curl -fsSL https://raw.githubusercontent.com/kkx999/xnat/main/scripts/bootstrap-panel.sh)
```

安装过程中可直接配置 Panel 域名、HTTPS 与 Cloudflare。

---

# Host 一键安装

全新 Debian 12：

```bash
apt-get update && apt-get install -y curl ca-certificates && \
bash <(curl -fsSL https://raw.githubusercontent.com/kkx999/xnat/main/scripts/bootstrap-host.sh)
```

Host 安装器会一步一步询问：

1. **Panel Server 的真实公网 IPv4**：用于限制 Host Agent 管理端口，只允许 Panel 访问。
2. **虚拟化模式**：自动检测 `/dev/kvm`，可选择 LXC、KVM 或 LXC + KVM；没有可访问的 `/dev/kvm` 时只允许 LXC。
3. **natpool 大小**：用于存放用户 VPS 磁盘；脚本会检测磁盘并给出推荐值。

> 如果 Host 自身是一台 KVM VPS，想在里面继续创建 KVM VM，需要上层宿主机开放 Nested Virtualization，并让 `/dev/kvm` 在 Host 内可访问。

**NAT 用户端口池不在 Host 安装时填写。**

Host 连接 Panel 成功后，在 Panel 后台节点设置中配置 NAT 端口范围，并自动同步到 Agent。

---

# 指定 v1.4.1 安装

Panel：

```bash
XNAT_VERSION=1.4.1 \
bash <(curl -fsSL https://raw.githubusercontent.com/kkx999/xnat/main/scripts/bootstrap-panel.sh)
```

Host：

```bash
XNAT_VERSION=1.4.1 \
bash <(curl -fsSL https://raw.githubusercontent.com/kkx999/xnat/main/scripts/bootstrap-host.sh)
```

> `XNAT_VERSION=1.4.1` 指 XNAT Release 版本。XNAT v1.4.1 使用 **Panel v1.4.1 / Mobile API v1 / Host Agent v1.1.1 / Agent API v1**。

---

# 从 v1.4.0 升级到 v1.4.1

Panel：

```bash
xnat update 1.4.1
```

Host：

```bash
xnat update 1.4.1
```

更新命令会先执行 v1.4.1 升级预检并验证下载的 Release，再询问是否继续。Panel 会保留 `.env`、SQLite、用户、余额、订单、VPS、Host、套餐、端口、工单与支付数据，并继续执行升级前后 `PRAGMA quick_check`、完整备份、健康检查与失败回滚。Host 会保留 Agent Token、TLS、Incus、natpool、虚拟化配置和现有 VPS。

Host Agent 仍为 **v1.1.1 / Agent API v1**；本次没有 Host Agent 协议变更。Host 可执行 `xnat update 1.4.1` 同步 Release 元数据与管理脚本。


---

# 从 v1.3.1 升级到 v1.3.2

Panel：

```bash
xnat update 1.3.2
```

升级器会先执行 SQLite `PRAGMA quick_check`，备份 `panel.db`、`.env`、旧代码、systemd 单元和管理命令，然后替换 Panel 代码、执行 additive schema 检查并验证 `/health` 返回 v1.3.2。升级失败会尝试恢复升级前快照。

如果当前 v1.3.1 已经手工安装过 Mobile API dev1～dev5，同样可以直接升级；正式 v1.3.2 会覆盖为统一的 Mobile API v1 实现，现有数据库和登录数据不需要重建。

Host Agent 仍为 **v1.1.0 / Agent API v1**，Host 无需更新。

---

# 从 v1.3.0 升级到 v1.3.1

Panel：

```bash
xnat update 1.3.1
```

本次没有数据库破坏性变更，升级器会先备份 SQLite、`.env`、旧代码与 systemd 配置，再原地更新并执行 additive schema 校验。Host Agent 仍为 v1.1.0，已经运行 v1.1.0 的 Host 无需更新。

也支持 v1.2.0 Panel 直接执行 `xnat update 1.3.1`；若 Host 仍是旧 Agent，则需要把 Host 更新到本 Release 对应的 Agent v1.1.0。

---

# 从 v1.2.0 升级到 v1.3.0

Panel：

```bash
xnat update 1.3.0
```

Host 也需要升级到本 Release 的 **Host Agent v1.1.0**。首次从旧 Agent 升级时会让你选择 LXC / KVM / LXC + KVM；旧节点默认保持 LXC，不会因为升级自动改成 KVM。

---

# 从 v1.1.1 升级到 v1.2.0

正式兼容基线是 **XNAT Panel v1.1.1**。现有 v1.1.1 Panel 直接执行：

```bash
xnat update 1.2.0
```

也可以执行：

```bash
xnat
```

然后选择 **检查 / 更新 Panel**。

v1.1.1 自带的 `xnat update` 会下载 v1.2.0 Tag 源码并调用 v1.2.0 的 `scripts/upgrade-panel.sh`。升级流程会自动：

- 确认 v1.1.1 → v1.2.0 正式升级路径；
- 对 SQLite 执行 `PRAGMA quick_check`；
- 备份 `panel.db`、`.env`、旧 Panel 代码、systemd 单元与管理命令；
- 保留原有监听地址与端口；
- 原地更新 Panel 到 v1.2.0；
- 执行 additive schema migration，不重建旧业务表；
- 保留用户、余额、订单、VPS、Host、套餐、支付、通知、公告及公告已读记录；
- 健康检查或数据库检查失败时自动尝试回滚。

手动源码包升级：

```bash
cd /root/xnat-main
bash scripts/upgrade-panel-from-v1.1.1.sh
```

本次仅升级 Panel；**Host Agent 保持 v1.0.0 / Agent API v1，无需升级或重装。**

> 如果仍在更早版本，推荐先按正式 Release 链升级到 v1.1.1，再执行 `xnat update 1.2.0`。

---

# 管理

安装后执行：

```bash
xnat
```

Panel 与 Host 会自动显示对应的管理菜单。

---

# 文档

详细安装、节点接入、域名、HTTPS、Cloudflare、防火墙、更新、备份、Token 与故障排查：

[查看 docs/README.md](docs/README.md)

---

## License

MIT License

---

<div align="center">

### XNAT

**由 𝐍𝐀𝐌𝐄𝐋𝐄𝐒𝐒 和 GPT 倾力打造**

</div>
