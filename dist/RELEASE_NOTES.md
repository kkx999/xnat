# XNAT v1.0.3

系统镜像最低磁盘策略与后台配置能力更新。

- XNAT Release：v1.0.3
- Panel：v1.0.3
- Host Agent：v1.0.1
- Agent API：v2
- Mobile API：v1
- 系统镜像最低系统盘改为 Panel 后台逐镜像配置，不再按 Debian / Ubuntu 家族写死
- 默认 Debian 12 / 13 为 1 GiB、Ubuntu 22.04 / 24.04 为 2 GiB、Alpine 3.24 为 1 GiB
- LXC 直接使用后台配置；KVM 使用 max(镜像配置, 3 GiB)
- 管理后台系统镜像页面支持直接修改最低系统盘，新建镜像时也可指定
- 旧数据库仅执行一次兼容迁移，不会在后续启动中覆盖管理员自定义值
- Mobile API v1 的 /api/v1/system-images 新增 min_disk_gb 字段，现有客户端保持兼容
- v1.0.2 → v1.0.3 已作为正式验证的直接 Panel 升级路径
- Host Agent、Agent API、Panel 整体 UI 与主要业务交互保持不变

> Android v1.0.1 仍可继续使用；后续 Android 版本会改为直接读取 Panel 下发的 min_disk_gb。

**由 𝐍𝐀𝐌𝐄𝐋𝐄𝐒𝐒 和 GPT 倾力打造**
