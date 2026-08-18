# XNAT

> 基于 **Incus + LVM Thin** 的多节点 NAT VPS 管理平台。

XNAT 采用 **Panel Server + Host Agent** 分离架构，用于管理 NAT VPS、多宿主机节点、套餐、用户、流量、通知及日常运维。

当前版本：**v1.4.1**


## 更新日志

版本更新记录已独立维护，首页仅保留项目能力、安装和当前版本升级说明。

> 📋 **[查看完整更新日志（CHANGELOG.md）](CHANGELOG.md)**

当前版本关系：**XNAT Release v1.4.1 / Panel v1.4.1 / Mobile API v1 / Host Agent v1.1.1 / Agent API v1**。

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

需要导出自动脱敏的系统诊断报告时：

```bash
xnat doctor report
```

报告默认保存到 `/root/xnat-diagnostics/`。

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
