# XNAT v1.1.1

XNAT v1.1.1 公告中心交互与后台一致性维护版本。

- Panel：v1.1.1
- Host Agent：v1.0.0
- Agent API：v1
- 优化后台公告中心布局，移除冗余规则说明并统一操作区尺寸与对齐
- “置顶显示 / 首次登录重点弹出”改为统一可见的 Switch 交互
- 公告支持永久删除，并同步清理对应公告已读记录
- 后台 Flash 与前端统一为右上角 Toast，默认 3 秒自动消失
- 静态资源缓存版本更新，避免升级后浏览器继续加载旧 UI
- 正式验收 v1.1.0 → v1.1.1 原地升级路径
- 升级自动备份并保留 .env、SQLite、用户、余额、订单、VPS、Host、支付、通知、公告及已读记录
- v1.1.1 沿用 additive schema 兼容机制，不重建旧表
- Host Agent 无需升级，继续保持 v1.0.0 / Agent API v1

v1.1.0 Panel 推荐升级命令：

    xnat update 1.1.1

详细介绍请查看项目 README，完整部署与运维说明请查看 docs/README.md。
