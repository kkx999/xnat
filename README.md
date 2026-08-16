# XNAT

> 基于 **Incus + LVM Thin** 的多节点 NAT VPS 管理平台。

XNAT 采用 **Panel Server + Host Agent** 分离架构，用于管理 NAT VPS、多宿主机节点、套餐、用户、流量、通知及日常运维。

当前版本：**v1.1.0**

---

## 主要能力

- 多节点 Panel + Host Agent
- Incus + LVM Thin
- NAT VPS 自动开通、重装、删除
- CPU / 内存 / 磁盘 / 带宽管理
- TCP / UDP NAT 端口
- 流量统计、独立流量周期与超额限速
- 节点维护 / Drain 与资源水位调度保护
- 到期提醒、宽限期、自动停机与可选延迟删除
- Host 离线、natpool、任务和备份异常通知
- 套餐、库存、用户与订单
- USDT 充值
- Telegram / SMTP 通知
- 工单、审计与数据库备份
- 管理员可调整 VPS 流量额度与自定义到期时间
- USDT 自动充值与通知服务独立管理
- Panel 域名、HTTPS、Cloudflare
- XNAT 敏感管理端口自动保护
- 用户控制台交互反馈、复制、Toast 与轻量状态动画
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

# 指定 v1.1.0 安装

Panel：

```bash
XNAT_VERSION=1.1.0 \
bash <(curl -fsSL https://raw.githubusercontent.com/kkx999/xnat/main/scripts/bootstrap-panel.sh)
```

Host：

```bash
XNAT_VERSION=1.1.0 \
bash <(curl -fsSL https://raw.githubusercontent.com/kkx999/xnat/main/scripts/bootstrap-host.sh)
```

> 注：`XNAT_VERSION=1.1.0` 指 XNAT Release 版本。XNAT v1.1.0 继续使用 **Host Agent v1.0.0 / Agent API v1**，无需单独升级 Host Agent。

---

# 从 v1.0.2 升级到 v1.1.0

正式兼容基线是 **XNAT Panel v1.0.2 final**。现有 v1.0.2 Panel 推荐直接执行：

```bash
xnat update 1.1.0
```

v1.0.2 自带的 `xnat update` 会下载 v1.1.0 Tag 源码并调用 v1.1.0 的 `scripts/upgrade-panel.sh`；该升级器明确识别 v1.0.2 兼容路径。

如果使用手动源码包升级，也可以执行：

```bash
cd /root/xnat-v1.1.0
bash scripts/upgrade-panel-from-v1.0.2.sh
```

升级流程会自动：

- 校验当前 Panel 确实为 v1.0.2；
- 对现有 SQLite 执行 `PRAGMA quick_check`；
- 备份 `panel.db`、`.env`、当前 Panel 代码、systemd 单元和 XNAT 管理命令；
- 保留现有 Panel 监听地址与端口；
- 原地更新 Panel 到 v1.1.0；
- 只通过 `ALTER TABLE ... ADD COLUMN` 补齐 v1.1.0 字段；
- 保留用户、余额、订单、VPS、Host、套餐、支付和通知数据；
- 自动删除策略仍保持默认关闭；
- 健康检查或数据库检查失败时自动尝试回滚。

本次 Panel 升级**不要求重装 Host Agent**；Host Agent 继续保持 v1.0.0 / Agent API v1。

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
