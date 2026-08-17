#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

RELEASE_VERSION="$(tr -d '[:space:]' < VERSION)"
PANEL_VERSION="$(tr -d '[:space:]' < panel/VERSION)"
AGENT_VERSION="$(tr -d '[:space:]' < agent/VERSION)"
DIST="$ROOT/dist"

# Keep historical release assets already tracked in dist; overwrite only the
# files for the release being built and the current release metadata.
mkdir -p "$DIST"

bash "$ROOT/scripts/check.sh"

(
  cd panel
  zip -qr "$DIST/xnat-panel-v${PANEL_VERSION}.zip" . \
    -x '.env' '.venv/*' 'data/*' '__pycache__/*' '*.pyc'
)

(
  cd agent
  zip -qr "$DIST/xnat-host-agent-v${AGENT_VERSION}.zip" . \
    -x '.env' '.venv/*' 'tls/*' '__pycache__/*' '*.pyc'
)

cp scripts/bootstrap-panel.sh "$DIST/xnat-bootstrap-panel-v${RELEASE_VERSION}.sh"
cp scripts/bootstrap-host.sh "$DIST/xnat-bootstrap-host-v${RELEASE_VERSION}.sh"
cp release.json "$DIST/release.json"
chmod +x "$DIST"/*.sh

AGENT_API_VERSION="$(python3 -c 'import json; print(json.load(open("release.json"))["agent_api_version"])')"
cat > "$DIST/RELEASE_NOTES.md" <<EOF_NOTES
# XNAT v${RELEASE_VERSION}

本次为 XNAT 运维可靠性、诊断能力与前后台交互体验升级。

- Panel：v${PANEL_VERSION}
- Host Agent：v${AGENT_VERSION}
- Agent API：v${AGENT_API_VERSION}
- 保留 v1.3.3 已实机验证的 Panel / Host 统一 xnat CLI 交互
- 更新流程新增升级预检：下载并验证 Release 后，在写入系统前检查 Debian 12、磁盘、备份目录、systemd 与升级脚本完整性
- Panel 预检新增 SQLite quick_check 和已启用 Host 的 Agent API 兼容检查
- Host 预检新增 Incus、Agent TLS / .env 与 Agent API 检查
- Panel 系统诊断新增数据库大小、最近备份、Nginx、HTTPS 证书剩余天数等检查
- Host 系统诊断新增 Agent 端口、实例数量、NAT 端口池使用量、LXC/KVM、/dev/kvm 与 Panel 白名单检查
- 新增 xnat doctor report，一键生成 root-only 的脱敏诊断报告，包含版本、服务、磁盘、数据库/Incus、防火墙与最近日志
- 诊断报告自动屏蔽 Token、Password、Secret、API Key、Authorization/Bearer、JWT、Bot Token 与 URL 凭据
- 管理后台工单显示完整历史会话，用户与管理员消息按时间顺序呈现，并支持按消息正文搜索
- 工单后台按处理状态拆分为“待处理 / 进行中”与“已关闭归档”；活动工单优先显示，已关闭工单默认折叠，不再与待处理队列混排
- 客户端侧栏视觉进一步收口：保留桌面展开与移动端独立折叠逻辑，移除分类展开后的整块蓝色底板，改为留白分组、柔和分隔与圆角菜单项
- 账户设置中的登录会话与最近登录默认仅展示最近 3 条，其余安全记录可按需展开，避免长列表占满页面
- 管理后台补齐与用户前端一致的微交互反馈：导航、卡片、按钮、表单、折叠区、主题切换与页面跳转更柔和，同时保持原有后端接口和 CSRF / 确认流程不变
- 保留实机验证通过的完整客户端微交互、主题过渡与页面跳转反馈，并增加发布检查防止后续 UI 增量回退前端手感
- 账户安全双卡强制等高，折叠按钮改为 CSS 几何箭头；通知发送记录默认折叠且每页最多 12 条
- 站点设置按功能分区折叠，默认仅展开“站点与注册”，并移除保存操作上方突兀的硬分隔线
- 账户通知偏好改为等尺寸 Switch 卡片；登录会话与最近登录折叠保持独立，展开一侧不再把另一侧视觉拉高
- 管理后台通知服务将渠道配置、通知规则、发送测试与最近发送记录分层折叠，并移除通知规则上方遗留的硬分隔线
- 管理后台用户余额操作明确拆分为“增加余额 / 扣除余额”，两个按钮等宽等高；扣除前使用统一确认弹窗，且不会允许账户余额被扣成负数
- 管理后台用户列表进一步收紧余额操作控件尺寸，并修复搜索输入框与搜索按钮的垂直错位；不改变余额操作后端语义与审计流程
- SQLite 备份列表支持手动删除，输入 yes 二次确认并记录审计，同时显示备份总数量与占用空间
- Panel v${PANEL_VERSION} 不引入破坏性数据库变更；Mobile API 继续保持 v1，Web Session + CSRF 与购买链路保持兼容
- Host Agent v${AGENT_VERSION} / Agent API v${AGENT_API_VERSION} 核心协议保持不变；Host 更新用于同步本 Release 的 CLI、预检和诊断能力
- 正式支持 v1.3.3 → v${PANEL_VERSION}，并继续允许 v1.3.2 直接使用 additive migration 路径升级

**由 𝐍𝐀𝐌𝐄𝐋𝐄𝐒𝐒 和 GPT 倾力打造**

Panel / Host 推荐升级命令：

    xnat update ${RELEASE_VERSION}

升级前会自动执行预检；通过后才询问是否继续，并在实际写入前保持原有自动备份 / 健康检查 / 回滚保护。
EOF_NOTES

(
  cd "$DIST"
  sha256sum \
    "xnat-panel-v${PANEL_VERSION}.zip" \
    "xnat-host-agent-v${AGENT_VERSION}.zip" \
    "xnat-bootstrap-panel-v${RELEASE_VERSION}.sh" \
    "xnat-bootstrap-host-v${RELEASE_VERSION}.sh" \
    release.json \
    > SHA256SUMS.txt
)

echo "Release assets created in: $DIST"
cat "$DIST/SHA256SUMS.txt"
