# XNAT v1.6.4

本次版本为 Host 运维修复版本。

- Panel：v1.6.3（业务组件保持不变）
- Host Agent：v1.2.1
- Agent API：v1
- Mobile API：v1
- Host Agent 新增根分区低空间保护：低于安全阈值时先执行白名单安全清理，仍不足则明确阻止创建/重装/端口映射
- Host 菜单新增“清理 Host 系统空间”，仅清理 APT 下载缓存、受限 journal 历史与 XNAT 临时健康检查文件
- 不删除 Incus storage、镜像、实例磁盘、natpool、VPS、Agent Token/TLS 或用户数据
- Host 安装提示区分最低配置与建议配置：LXC 建议 8GiB+，KVM / 混合建议 12GiB+
- Host 版本检查以 Host Agent 版本为主，repo-wide Release 仅作为管理脚本发布来源
- v1.2.0 → v1.2.1 支持原地升级，Agent API 保持 v1

**由 𝐍𝐀𝐌𝐄𝐋𝐄𝐒𝐒 和 GPT 倾力打造**

Host 推荐升级命令：

    xnat update 1.6.4
