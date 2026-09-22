# Changelog

## v1.0.7 - 2026-09-22

- XNAT Release / Panel 升级到 v1.0.7；Host Agent 保持 v1.0.4，Agent API v2、Mobile API v1 均不变。
- 登录、注册、找回密码、重置密码与 2FA 的认证视觉进一步优化：收紧标题比例和留白，放大并上移基础设施预览，增强玻璃层次、光影和组件精度。
- 桌面端认证页重新平衡左右视觉重心；移动端继续使用独立单栏布局，不把桌面双栏直接缩小。
- 服务器续费区域重新统一组件语言：自动续费与手动续费使用相同高度、圆角、边框、背景层次和交互动效。
- 手动续费按钮取消突兀的大面积高饱和蓝色填充，改为轻卡片主操作，续费金额独立弱强调，浅色 / 深色主题同步适配。
- 自动续费、手动续费在窄屏下继续纵向排列，并保持一致的触控尺寸与间距。
- v1.0.6 → v1.0.7 为 Panel UI 小版本更新；Host Agent v1.0.4 无需重复更新。

**由 𝐍𝐀𝐌𝐄𝐋𝐄𝐒𝐒 和 GPT 倾力打造**

## v1.0.6 - 2026-09-22

- XNAT Release / Panel 升级到 v1.0.6；Host Agent 保持 v1.0.4，Agent API v2、Mobile API v1 均不变。
- 服务器详情新增每台 VPS 独立的自动续费开关，采用 iOS 风格交互并即时保存；到期后余额充足时自动扣款续费 30 天。
- 删除服务器调整为只清理 Panel 记录，不再连接 Host Agent；Host 离线、重装或 TLS 证书变化时仍可正常清理面板记录，宿主机上的实际实例不会被删除。
- 管理员更新 Host Agent Token 或 API 地址时会清除旧 TLS TOFU 指纹，下次连接重新建立信任，解决 Host 重装后的证书指纹不一致。
- 系统重装 / 删除区域新增机器编号一键复制，保留手动粘贴编号确认，兼顾便利性与误操作保护。
- 登录、注册、找回密码、重置密码与 2FA 统一升级为新的认证 UI；桌面端采用左侧视觉区 + 右侧表单，移动端使用精简单栏布局。
- 统一自动续费、手动续费、复制按钮、输入框等控件的高度、圆角、间距和交互动效，减少页面组件之间的视觉割裂。
- v1.0.5 → v1.0.6 为 Panel 功能与 UI 小版本更新；Host Agent v1.0.4 无需重复更新。

**由 𝐍𝐀𝐌𝐄𝐋𝐄𝐒𝐒 和 GPT 倾力打造**

## v1.0.5 - 2026-09-14

- XNAT Release / Panel 升级到 v1.0.5；Host Agent 保持 v1.0.4，Agent API v2、Mobile API v1 均不变。
- 修复浅色主题下“服务器实时监控”标题、副标题和状态文字对比度过低的问题。
- 去除服务器详情折叠模块展开时突兀的深色分割线，亮色 / 深色主题分别使用更柔和的层级与边框。
- 降低详情模块大面积蓝色底的饱和度，让 Root 密码、NAT 端口、系统重装与删除实例区域更贴合当前主题。
- 同步优化深色主题的实时监控、详情容器、展开区与操作按钮，避免只修浅色造成主题回归。
- 实时监控布局、CPU / 内存 / 硬盘胶囊进度条、网络速率以及采样逻辑保持不变。
- v1.0.4 → v1.0.5 为正式验证的直接 Panel 升级路径；Host Agent v1.0.4 无需重复更新。

**由 𝐍𝐀𝐌𝐄𝐋𝐄𝐒𝐒 和 GPT 倾力打造**

## v1.0.4 - 2026-09-14

- XNAT Release 1.0.4 / Panel 1.0.4 / Host Agent 1.0.4；Agent API 保持 v2，Mobile API 保持 v1。
- 服务器详情新增一个整体实时资源监控区域，以 2×2 对称布局显示 CPU、内存、硬盘与实时下载 / 上传速率。
- CPU、内存、硬盘使用全圆角胶囊进度条；网络速率单独显示。
- Web 每 5 秒刷新，页面不可见时停止轮询。
- Host Agent 只使用内存短缓存和相邻采样，不写监控数据库、不保存历史。
- v1.0.3 → v1.0.4 为正式验证的直接 Panel 升级路径。

**由 𝐍𝐀𝐌𝐄𝐋𝐄𝐒𝐒 和 GPT 倾力打造**

## v1.0.3 - 2026-09-14

- XNAT Release 1.0.3 / Panel 1.0.3；Host Agent 1.0.3，Agent API 保持 v2，Mobile API 保持 v1。
- 系统镜像最低系统盘改为 Panel 后台逐镜像配置，不再按 Debian / Ubuntu 家族写死。
- 默认 Debian 12 / 13 为 1 GiB、Ubuntu 22.04 / 24.04 为 2 GiB、Alpine 3.24 为 1 GiB。
- LXC 直接使用镜像 `min_disk_gb`；KVM 全局技术底线调整为 3 GiB。
- 管理后台支持编辑每个系统镜像的最低系统盘。
- 旧数据库执行一次性兼容迁移，之后不会覆盖管理员自定义值。
- Mobile API v1 `/api/v1/system-images` 新增 `min_disk_gb` 字段，保持向后兼容。
- v1.0.2 → v1.0.3 为正式验证的直接 Panel 升级路径。
- Host Agent v1.0.3 保留镜像磁盘策略修复，并修复安全重装预备阶段对单次 `incus move` 的硬依赖：rename 失败可回退到停止态临时副本，回滚路径同样支持 copy 恢复，并保留真实 Incus 错误用于定位。
- Agent API、Panel 整体 UI 与主要业务交互保持不变。

**由 𝐍𝐀𝐌𝐄𝐋𝐄𝐒𝐒 和 GPT 倾力打造**

## v1.0.2 - 2026-09-14

- Panel 1.0.2 / Host Agent 1.0.1 / Agent API 2；Mobile API 保持 v1。
- Web、Mobile、后台任务与 Host Agent 增加镜像/磁盘预检。Alpine 保持 1 GiB 支持，Debian / Ubuntu 至少 2 GiB，KVM 至少 4 GiB。
- 修复 Alpine 最小化镜像 SSH readiness，补充 `iproute2` 并增加监听检测兜底。
- Provision 按 XNAT Server 身份实现幂等，并可恢复匹配的孤儿实例。
- Job 抢占改为原子条件更新；NAT / SSH 分配使用短时唯一 Host 端口租约。
- Agent API 2 使用 nonce 签名并拒绝重放请求；Panel 同时支持 Agent API v1 / v2 便于分批升级。
- Panel 到 Host 的 HTTPS 连接首次可信接触后固定 SHA-256 TLS 指纹，并拒绝后续证书变化。
- 重装采用失败可恢复流程，替换实例完全就绪前保留旧实例。
- 跨 Web / Mobile / Job 的共享校验逻辑向 `panel/app/services` 收敛；模板、静态资源、页面布局和现有交互保持不变。
- 清理旧开发阶段升级脚本并统一版本标识。
- 修正正式发布元数据，确保 Panel v1.0.2、Host Agent v1.0.1、Agent API v2 与 `xnat` 显示保持一致。

## v1.0.1 - 2026-09-13

- 修复卸载 Panel 后 Nginx 虚拟主机回退导致旧 Panel 域名可能显示同机 Komari / 其他站点内容的问题。
- Panel 卸载新增“保留数据备份”和“完全卸载”两种模式。
- 完全卸载会清理 Panel 数据库、`.env`、安装凭据、Panel 升级/卸载备份、Panel 诊断文件与 XNAT 托管的域名证书。
- Nginx、Certbot 以及 Komari / 其他虚拟主机不会被删除或改写；旧 Panel 域名会保留一个无业务数据的拒绝占位，必要时同时补充默认拒绝站点。
- Panel UI、页面布局、视觉风格和现有业务交互保持不变。
- Panel v1.0.1；Host v1.0.0；Agent API / Mobile API 继续保持 v1。

**由 𝐍𝐀𝐌𝐄𝐋𝐄𝐒𝐒 和 GPT 倾力打造**

## v1.0.0 - 2026-09-13

XNAT 正式基线重新整理。

- Panel v1.0.0，Host v1.0.0；Agent API / Mobile API 保持 v1。
- LXC Host 最低 1C / 1GB / 8GiB；KVM / 混合最低 1C / 1GB / 12GiB + `/dev/kvm`。
- LXC 为 Host 保留约 4GiB 总空间预算；KVM / 混合约 6GiB，预算包含系统/XNAT当前占用和后续余量。
- Host 安装器只让用户选择虚拟化模式和给小鸡使用的总硬盘，底层 natpool 与预留计算自动处理。
- Panel / Host 普通状态与更新界面不再展示内部 Release 版本。
- Panel 现有页面布局、视觉风格和交互逻辑保持不变。
- 旧开发阶段版本不提供到本版的原地升级兼容，请全新重装。

**由 𝐍𝐀𝐌𝐄𝐋𝐄𝐒𝐒 和 GPT 倾力打造**
