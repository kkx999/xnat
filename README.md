# XNAT

> 基于 **Incus + LVM Thin** 的多节点 NAT VPS 管理平台。

XNAT 采用 **Panel Server + Host Agent** 分离架构，用于管理 NAT VPS、多宿主机节点、套餐、用户、流量、通知及日常运维。

当前版本：**v1.0.2**

---

## 主要能力

- 多节点 Panel + Host Agent
- Incus + LVM Thin
- NAT VPS 自动开通、重装、删除
- CPU / 内存 / 磁盘 / 带宽管理
- TCP / UDP NAT 端口
- 流量统计与超额限速
- 套餐、库存、用户与订单
- USDT 充值
- Telegram / SMTP 通知
- 工单、审计与数据库备份
- 管理员可调整 VPS 流量额度与自定义到期时间
- USDT 自动充值与通知服务独立管理
- Panel 域名、HTTPS、Cloudflare
- XNAT 敏感管理端口自动保护
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

# 指定 v1.0.2 安装

Panel：

```bash
XNAT_VERSION=1.0.2 \
bash <(curl -fsSL https://raw.githubusercontent.com/kkx999/xnat/main/scripts/bootstrap-panel.sh)
```

Host：

```bash
XNAT_VERSION=1.0.2 \
bash <(curl -fsSL https://raw.githubusercontent.com/kkx999/xnat/main/scripts/bootstrap-host.sh)
```

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
