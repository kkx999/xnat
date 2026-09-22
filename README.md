# XNAT

> 一个面向自建场景的 **多节点 NAT VPS 管理平台**，基于 **Incus + LVM Thin**，支持 LXC、KVM 与混合虚拟化。

**当前正式版本：v1.1.0**

<div align="center">

### [查看介绍](docs/INTRODUCTION.md)

</div>

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

Host 安装流程会自动完成依赖、Incus、LVM Thin、Host Agent、网络、防火墙与健康检查。

**NAT 用户端口池不在 Host 安装阶段填写。** Host 接入 Panel 后，再在后台节点配置端口范围并同步到 Agent。

---

## 管理、更新与诊断

Panel / Host 安装完成后统一使用：

```bash
xnat
```

升级到当前正式版时建议：

1. 先通过 `xnat` 更新 Panel 到 v1.1.0；
2. Host Agent 建议同步更新到 v1.0.5，以启用真实 proxy 端口占用检查和严格删除确认；
3. 在 Panel 中确认 Host 在线，并确认 Agent API 仍为 v2。

如果仍有 Host Agent API v1 的历史节点，确认 Panel 已经是 v1.1.0 后，在对应 Host 执行：

```bash
XNAT_ALLOW_AGENT_API_CHANGE=1 xnat
```

然后进入 **更新 → Host Agent 更新**。该变量只用于跨 API 升级，无需写入 `.env` 或永久设置。

导出自动脱敏诊断报告：

```bash
xnat doctor report
```

报告默认写入：

```text
/root/xnat-diagnostics/
```

---

## 文档

- [完整更新日志](CHANGELOG.md)
- [安装、节点接入、更新、备份与故障排查](docs/README.md)
- [Mobile API v1](docs/MOBILE_API.md)
- [安全说明](SECURITY.md)
- [XNAT Android](https://github.com/kkx999/XNAT-Android)

---

<div align="center">

### XNAT

**由 NAMELESS 和 GPT 倾力打造**

</div>
