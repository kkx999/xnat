#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
RELEASE_VERSION="$(tr -d '[:space:]' < VERSION)"
PANEL_VERSION="$(tr -d '[:space:]' < panel/VERSION)"
AGENT_VERSION="$(tr -d '[:space:]' < agent/VERSION)"
AGENT_API_VERSION="$(tr -d '[:space:]' < agent/API_VERSION)"
DIST="$ROOT/dist"
mkdir -p "$DIST"
find "$DIST" -maxdepth 1 -type f -delete
bash "$ROOT/scripts/check-v106.sh"
(cd panel && zip -Dqr "$DIST/xnat-panel-v${PANEL_VERSION}.zip" . -x '.env' '.venv/*' 'data/*' '__pycache__/*' '*.pyc')
(cd agent && zip -Dqr "$DIST/xnat-host-agent-v${AGENT_VERSION}.zip" . -x '.env' '.venv/*' 'tls/*' '__pycache__/*' '*.pyc')
cp scripts/bootstrap-panel.sh "$DIST/xnat-bootstrap-panel-v${RELEASE_VERSION}.sh"
cp scripts/bootstrap-host.sh "$DIST/xnat-bootstrap-host-v${RELEASE_VERSION}.sh"
cp release.json "$DIST/release.json"
cat > "$DIST/RELEASE_NOTES.md" <<EOF_NOTES
# XNAT v${RELEASE_VERSION}

服务控制与认证界面更新。

- XNAT Release：v${RELEASE_VERSION}
- Panel：v${PANEL_VERSION}
- Host Agent：v${AGENT_VERSION}（本版未修改 Host Agent）
- Agent API：v${AGENT_API_VERSION}
- Mobile API：v1
- 服务器详情新增每台 VPS 独立自动续费开关，采用 iOS 风格交互并即时保存；到期后余额充足时自动扣款续费 30 天
- 删除服务器调整为只清理 Panel 记录，不再连接 Host Agent；Host 离线、重装或 TLS 证书变化时仍可正常删除面板记录，宿主机实际实例不会被删除
- 管理员更新 Host Agent Token 或 API 地址时清除旧 TLS TOFU 指纹，下次连接重新建立信任，解决 Host 重装后的证书指纹不一致
- 系统重装 / 删除区域新增机器编号一键复制，保留手动粘贴编号确认，兼顾便利性与误操作保护
- 登录、注册、找回密码、重置密码与 2FA 统一升级认证 UI；桌面端采用左侧视觉区 + 右侧表单，移动端使用精简单栏布局
- 统一自动续费、手动续费、复制按钮和输入框的高度、圆角、间距与交互动效，减少页面组件之间的视觉割裂
- v1.0.5 → v1.0.6 为 Panel 功能与 UI 小版本更新；已是 Host Agent v1.0.4 的节点无需重复更新

> Android v1.0.2 无需升级，本次不修改 Mobile API。

**由 𝐍𝐀𝐌𝐄𝐋𝐄𝐒𝐒 和 GPT 倾力打造**
EOF_NOTES
(cd "$DIST" && sha256sum "xnat-panel-v${PANEL_VERSION}.zip" "xnat-host-agent-v${AGENT_VERSION}.zip" "xnat-bootstrap-panel-v${RELEASE_VERSION}.sh" "xnat-bootstrap-host-v${RELEASE_VERSION}.sh" release.json RELEASE_NOTES.md > SHA256SUMS.txt)
echo "Release assets created in: $DIST"
cat "$DIST/SHA256SUMS.txt"
