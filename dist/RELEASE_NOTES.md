# XNAT v1.6.3

本次版本修正 Panel 对 Incus/LVM natpool 微小对齐损耗的逻辑容量判断。

- Panel：v1.6.3
- Host Agent：v1.2.0
- Agent API：v1
- Mobile API：v1
- 逻辑 natpool 容量在距离下一个 0.125GiB 分配边界不超过 32MiB 时安全归一化
- 2GiB 名义 natpool 即使实际上报约 1.99GiB，1GiB 套餐在 100% 存储调度阈值下可正确预计并调度 2 台
- 90% 等较低存储调度阈值仍按管理员设置生效，不会被归一化绕过
- 原始物理 natpool 总量 / 使用量不修改，真实存储水位保护继续生效
- v1.6.2 → v1.6.3 支持原地升级，业务数据保持不变
- Host Agent 核心保持 v1.2.0 / Agent API v1

**由 𝐍𝐀𝐌𝐄𝐋𝐄𝐒𝐒 和 GPT 倾力打造**

Panel 推荐升级命令：

    xnat update 1.6.3
