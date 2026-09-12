# XNAT v1.6.2

本次版本修正 Panel 的 Host 套餐容量估算，并重构 GitHub 项目首页。

- Panel：v1.6.2
- Host Agent：v1.2.0
- Agent API：v1
- Mobile API：v1
- 套餐磁盘预计数量按逻辑剩余配额计算，不再把 Incus 镜像缓存 / LVM Thin metadata 当成已分配 VPS 磁盘
- natpool 实际使用率继续执行存储水位保护，安全调度边界不变
- 2.0GB natpool、实际占用 0.2GB、逻辑已分配 0GB 时，1GB 磁盘套餐正确显示约 2 台
- Host 卡片明确区分存储实际占用与套餐逻辑容量
- README 更新为当前版本架构、能力、支持环境、安装与升级首页
- v1.6.1 → v1.6.2 支持原地升级，业务数据保持不变
- Host Agent 核心保持 v1.2.0 / Agent API v1

**由 𝐍𝐀𝐌𝐄𝐋𝐄𝐒𝐒 和 GPT 倾力打造**

Panel 推荐升级命令：

    xnat update 1.6.2
