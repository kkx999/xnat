# XNAT v1.6.0

本次版本修复母机系统兼容和 Host 安装容量判断，让低配 LXC Host 按真实资源计算，而不是把母机总盘门槛误当成安装后的剩余空间。

- Panel：v1.6.0
- Host Agent：v1.2.0
- Agent API：v1
- Mobile API：v1
- Panel / Host 支持 Debian 12/13、Ubuntu 22.04/24.04/26.04 LTS
- Host 菜单显示 CPU、总/可用内存、总/已用/可用硬盘、KVM 与各模式预计 natpool
- LXC：1C / 1GB / 4.5GiB 总盘基线，约 1GiB 系统/XNAT 预留后动态计算 natpool
- KVM / 混合：1C / 1GB / 6.5GiB 总盘、/dev/kvm、至少 4GiB natpool
- 默认 Host 模式改为 LXC；不满足模式会在选择前显示原因
- Zabbly Incus 源按发行版 codename 自动配置并优先使用 Zabbly 包
- v1.5.0 → v1.6.0 支持原地升级，业务数据保持不变
- Host Agent 核心保持 v1.2.0 / Agent API v1

**由 𝐍𝐀𝐌𝐄𝐋𝐄𝐒𝐒 和 GPT 倾力打造**

Panel / Host 推荐升级命令：

    xnat update 1.6.0
