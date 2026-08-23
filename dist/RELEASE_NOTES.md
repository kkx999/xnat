# XNAT v1.4.3

本次为 XNAT Panel 的套餐展示一致性修复。

- Panel：v1.4.3
- Host Agent：v1.1.1
- Agent API：v1
- Mobile API：v1
- 首页“在售套餐”补齐“服务器地区”和“网络线路”
- 首页与登录后的套餐中心统一为完整的 3×3 套餐规格布局
- 直接复用套餐已有字段，不新增数据库列，不改变套餐、库存或购买逻辑
- Mobile API v1 保持不变，XNAT Android 无需更新
- 正式支持 v1.4.2 → v1.4.3 原地升级
- 升级继续执行 SQLite quick_check、完整备份、健康检查与失败回滚；.env、用户、余额、订单、VPS、Host、套餐、端口、工单、充值和通知数据全部保留
- Host Agent v1.1.1 / Agent API v1 核心协议不变

**由 𝐍𝐀𝐌𝐄𝐋𝐄𝐒𝐒 和 GPT 倾力打造**

Panel / Host 推荐升级命令：

    xnat update 1.4.3

GitHub 发布时请创建并真正 Publish Tag `v1.4.3` 的 Release，不要只保留 Draft；无版本安装器通过 `releases/latest` 识别最新正式版。
