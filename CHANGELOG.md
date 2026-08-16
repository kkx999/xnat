# Changelog

## v1.0.1

- 管理后台左侧导航改为统一视觉的折叠分类，当前分类自动展开。
- 后台整体配色统一为深蓝灰体系，移除旧紫色选中态并提升文字对比度。
- 任务队列、套餐与库存、用户管理、宿主机节点、表格、输入框与操作按钮统一 UI。
- 数据库备份页新增本地 `.db` 上传、SQLite 完整性与结构校验。
- 服务器已有备份支持直接从后台恢复。
- 恢复前自动创建 `pre-restore` 安全快照，恢复失败自动回滚。
- 数据库上传、下载、恢复操作写入审计日志。
- 数据库备份 / 恢复页面按钮、文件选择器、危险确认区域重新统一视觉。
- 修复较矮桌面视口中侧边栏底部入口不易访问的问题。
- 修复 `xnat update` 在 `pipefail` 环境下解析 GitHub 源码包时可能提前退出的问题。
- Host Agent 保持 v1.0.0，Agent API 保持 v1。

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
