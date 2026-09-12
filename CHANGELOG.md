# Changelog

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
