## v1.0.2 - 2026-09-14

- Panel 1.0.2 / Host Agent 1.0.1 / Agent API 2; Mobile API remains v1.
- Added image/disk preflight on Web, Mobile, background jobs and Host Agent. Alpine keeps 1 GiB support; Debian/Ubuntu require 2 GiB; KVM requires at least 4 GiB disk.
- Fixed Alpine minimal-image SSH readiness by installing `iproute2` and using a fallback listener check.
- Provisioning is idempotent by XNAT server identity and reconciliation can recover matching orphaned instances.
- Job claiming now uses an atomic conditional update; NAT/SSH allocation uses short-lived unique Host port leases.
- Agent API 2 signs a nonce and rejects replayed mutations. Panel supports API 1 and 2 for staged upgrades.
- HTTPS Host connections pin the SHA-256 certificate fingerprint on first trusted contact and reject later certificate changes.
- Reinstall uses a rollback-safe blue/green flow: the old instance is retained until the replacement is fully ready.
- Cross-surface validation now lives in `panel/app/services`; templates, static assets, page layout and existing interaction flow are unchanged.
- Removed obsolete development-line Panel upgrade scripts and normalized release/version strings.

# Changelog

## v1.0.1 - 2026-09-13

- 修复卸载 Panel 后 Nginx 虚拟主机回退导致旧 Panel 域名可能显示同机 Komari / 其他站点内容的问题。
- Panel 卸载新增“保留数据备份”和“完全卸载”两种模式。
- 完全卸载会清理 Panel 数据库、`.env`、安装凭据、Panel 升级/卸载备份、Panel 诊断文件与 XNAT 托管的域名证书。
- Nginx、Certbot 以及 Komari / 其他虚拟主机不会被删除或改写；旧 Panel 域名会保留一个无业务数据的拒绝占位，必要时同时补充默认拒绝站点。
- Panel UI、页面布局、视觉风格和现有业务交互保持不变。
- Panel v1.0.1；Host v1.0.0；Agent API / Mobile API 继续保持 v1。

**由 𝐍𝐀𝐌𝐄𝐋𝐄𝐒𝐒 和 GPT 倾力打造**

## v1.0.0 - 2026-09-13

XNAT 正式基线重新整理。

- Panel v1.0.0，Host v1.0.0；Agent API / Mobile API 保持 v1。
- LXC Host 最低 1C / 1GB / 8GiB；KVM / 混合最低 1C / 1GB / 12GiB + `/dev/kvm`。
- LXC 为 Host 保留约 4GiB 总空间预算；KVM / 混合约 6GiB，预算包含系统/XNAT当前占用和后续余量。
- Host 安装器只让用户选择虚拟化模式和给小鸡使用的总硬盘，底层 natpool 与预留计算自动处理。
- Panel / Host 普通状态与更新界面不再展示内部 Release 版本。
- Panel 现有页面布局、视觉风格和交互逻辑保持不变。
- 旧开发阶段版本不提供到本版的原地升级兼容，请全新重装。

**由 𝐍𝐀𝐌𝐄𝐋𝐄𝐒𝐒 和 GPT 倾力打造**
