# XNAT

> 基于 **Incus + LVM Thin** 的多节点 NAT VPS 管理平台。

XNAT 采用 **Panel Server + Host Agent** 分离架构，用于管理 NAT VPS、多宿主机节点、套餐、用户、流量、通知及日常运维。

当前版本：**v1.2.0**

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
- `xnat` 统一管理命令

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
2. **natpool 大小**：用于存放用户 VPS 磁盘；脚本会检测磁盘并给出推荐值。

**NAT 用户端口池不在 Host 安装时填写。**

Host 连接 Panel 成功后，在 Panel 后台节点设置中配置 NAT 端口范围，并自动同步到 Agent。

---

# 指定 v1.2.0 安装

Panel：

```bash
XNAT_VERSION=1.2.0 \
bash <(curl -fsSL https://raw.githubusercontent.com/kkx999/xnat/main/scripts/bootstrap-panel.sh)
```

Host：

```bash
XNAT_VERSION=1.2.0 \
bash <(curl -fsSL https://raw.githubusercontent.com/kkx999/xnat/main/scripts/bootstrap-host.sh)
```

> `XNAT_VERSION=1.2.0` 指 XNAT Release 版本。XNAT v1.2.0 继续使用 **Host Agent v1.0.0 / Agent API v1**，Host Agent 无需单独升级。

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
