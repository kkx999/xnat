# Changelog

## v1.0.0

XNAT 首个正式版本。

- Panel Server + Host Agent 多节点架构
- Incus + LVM Thin NAT VPS
- Host 交互式安装：Panel 公网 IP + natpool
- NAT 端口池改由 Panel 后台在节点接入后配置
- NAT 端口池自动同步 Agent，并由 Agent 二次校验
- 节点端口池使用量 / 剩余量统计
- Panel 域名、Nginx、Let's Encrypt、Cloudflare
- Cloudflare 真实 IP 与源站防绕过
- XNAT 敏感管理端口防火墙
- `xnat` Panel / Host 统一管理菜单
- 组件独立版本更新与 Agent API 兼容机制
- 数据库备份 / 恢复
- Agent Token 轮换
- 系统诊断
- VPS 流量额度可直接调整，并支持重置当前流量周期
- VPS 到期时间支持自定义续期天数或直接指定日期时间
- 磁盘缩容前端提示与后端双重保护
- USDT 自动充值从站点设置独立为业务页面
- 通知服务从站点设置独立，支持 SMTP / Telegram 测试、全站通知规则与发送记录
- Multi-Node NAT 端口占用按 Host + 协议隔离，不再使用全局端口唯一约束
