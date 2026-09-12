# XNAT Panel v1.0.1

XNAT 控制平面正式组件。v1.0.1 基于重新整理后的 v1.0.0 正式基线，修复 Panel 与其他 Nginx 站点共存时的卸载后虚拟主机串站问题，并提供“保留数据备份 / 完全卸载”两种明确的卸载模式。

卸载后会为旧 Panel 域名保留一个不含业务数据的 Nginx 拒绝占位，避免该域名落入 Komari 或其他虚拟主机；重新配置 XNAT 域名时会自动移除该占位。

本次不修改 Panel 页面布局、视觉风格、业务交互、Agent API 或 Mobile API。Host Agent 继续保持 v1.0.0。
