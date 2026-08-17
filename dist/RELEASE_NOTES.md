# XNAT v1.3.2

本次为 Panel + XNAT Android 正式接口整合更新。

- Panel：v1.3.2
- Host Agent：v1.1.0
- Agent API：v1
- 正式集成 Mobile API v1，供 XNAT Android v1.0.0 使用
- Bearer Token 登录沿用既有账号、登录失败封禁、TOTP 与 LoginSession 安全策略
- Android 支持账户/概览、服务器列表与详情、开机/关机/重启、NAT 端口管理、系统重装
- Android 支持账务、工单、套餐目录、优惠码试算、余额购买与自动开通
- 购买接口包含 request_id 幂等保护，避免网络重试造成重复扣款或重复开通
- 浏览器 Web Panel 原有 Session + CSRF 流程保持不变
- Mobile API v1 不改变 Agent API；Host Agent 继续保持 v1.1.0 / Agent API v1
- 本次不包含破坏性数据库结构变更；Mobile API 复用现有业务表和任务队列
- 正式支持 v1.3.1 → v1.3.2 原地升级，并继续保留 v1.3.0 / v1.2.0 additive migration 兼容路径
- 升级前自动执行 SQLite quick_check，并备份数据库、.env、旧代码、systemd 与管理命令
- 健康检查或完整性检查失败时使用既有自动回滚逻辑

v1.3.1 Panel 推荐升级命令：

    xnat update 1.3.2

Host 已经运行 Agent v1.1.0 时，无需因为本次 Panel 更新重装或升级 Host。
