# Contributing

欢迎提交 Bug 修复、文档改进和功能建议。

提交 Pull Request 前：

```bash
bash scripts/check.sh
```

原则：

- 不提交 `.env`、数据库、Token、证书私钥等秘密
- Panel 与 Host Agent 的 API 变更需要保持版本兼容或明确记录
- 对实例磁盘操作保持“禁止缩容”的安全策略
- 新增后台能力时优先保持用户端和管理员端权限边界
