## v1.0.3 - 2026-09-14

Host Agent 发布元数据热修复。

- 修复 v1.0.2 发布包中 `agent/VERSION`、`agent/API_VERSION` 与 `agent/natvps_agent/__init__.py` 未同步的问题。
- Host Agent 实际运行代码保持 v1.0.1 / Agent API v2，不修改 VPS、Incus、NAT、Panel UI 或现有业务逻辑。
- 修复 `xnat` 更新成功后仍把 Host Agent 显示为 v1.0.0 / Agent API v1 的问题。
- Panel 继续为 v1.0.2；Host Agent 继续为 v1.0.1；Agent API 继续为 v2；Mobile API 继续为 v1。
- 已经执行过 v1.0.2 Host 更新但仍显示 v1.0.0 的节点，可在 Panel 已升级到 v1.0.2 后再次执行 `XNAT_ALLOW_AGENT_API_CHANGE=1 xnat`，重新进入 Host Agent 更新完成元数据同步。
- 本次为发布与版本识别热修复，不改变 Web UI、页面布局、视觉风格和主要交互。

**由 𝐍𝐀𝐌𝐄𝐋𝐄𝐒𝐒 和 GPT 倾力打造**

## v1.0.2 - 2026-09-14

- Panel 1.0.2 / Host Agent 1.0.1 / Agent API 2；Mobile API 保持 v1。
- Web、Mobile、后台任务与 Host Agent 增加镜像/磁盘预检。Alpine 保持 1 GiB 支持，Debian / Ubuntu 至少 2 GiB，KVM 至少 4 GiB。
- 修复 Alpine 最小化镜像 SSH readiness，补充 `iproute2` 并增加监听检测兜底。
- Provision 按 XNAT Server 身份实现幂等，并可恢复匹配的孤儿实例。
- Job 抢占改为原子条件更新；NAT / SSH 分配使用短时唯一 Host 端口租约。
- Agent API 2 使用 nonce 签名并拒绝重放请求；Panel 同时支持 Agent API v1 / v2 便于分批升级。
- Panel 到 Host 的 HTTPS 连接首次可信接触后固定 SHA-256 TLS 指纹，并拒绝后续证书变化。
- 重装采用失败可恢复流程，替换实例完全就绪前保留旧实例。
- 跨 Web / Mobile / Job 的共享校验逻辑向 `panel/app/services` 收敛；模板、静态资源、页面布局和现有交互保持不变。
- 清理旧开发阶段升级脚本并统一版本标识。

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
