# XNAT v1.0.1

Panel 卸载安全与数据清理修复。

- Panel：v1.0.1
- Host：v1.0.0
- Agent API：v1
- Mobile API：v1
- 修复卸载 Panel 后旧 Panel 域名可能落入同机 Komari / 其他 Nginx 站点的问题
- 卸载 Panel 可选择保留备份或完全清除 XNAT Panel 数据
- 完全卸载清理数据库、.env、安装凭据、Panel 备份、诊断文件与 XNAT 托管证书
- 旧 Panel 域名保留无业务数据的 Nginx 拒绝占位，避免跨站回退
- 不删除 Nginx、Certbot，也不修改 Komari 或其他站点配置
- Panel UI、页面布局、视觉风格和业务交互保持不变

**由 𝐍𝐀𝐌𝐄𝐋𝐄𝐒𝐒 和 GPT 倾力打造**
