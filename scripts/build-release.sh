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
bash "$ROOT/scripts/check-v107.sh"
(cd panel && zip -Dqr "$DIST/xnat-panel-v${PANEL_VERSION}.zip" . -x '.env' '.venv/*' 'data/*' '__pycache__/*' '*.pyc')
(cd agent && zip -Dqr "$DIST/xnat-host-agent-v${AGENT_VERSION}.zip" . -x '.env' '.venv/*' 'tls/*' '__pycache__/*' '*.pyc')
cp scripts/bootstrap-panel.sh "$DIST/xnat-bootstrap-panel-v${RELEASE_VERSION}.sh"
cp scripts/bootstrap-host.sh "$DIST/xnat-bootstrap-host-v${RELEASE_VERSION}.sh"
cp release.json "$DIST/release.json"
cat > "$DIST/RELEASE_NOTES.md" <<EOF_NOTES
# XNAT v${RELEASE_VERSION}

登录页与续费区视觉优化。

- XNAT Release：v${RELEASE_VERSION}
- Panel：v${PANEL_VERSION}
- Host Agent：v${AGENT_VERSION}（本版未修改 Host Agent）
- Agent API：v${AGENT_API_VERSION}
- Mobile API：v1
- 登录、注册、找回密码、重置密码与 2FA 的认证视觉进一步优化，收紧标题比例和留白，放大并上移基础设施预览
- 桌面端重新平衡左右视觉重心，增强玻璃层次、光影和组件精度；移动端继续采用独立单栏布局
- 服务器续费区域重新统一组件语言，自动续费与手动续费使用相同高度、圆角、边框、背景层次和交互动效
- 手动续费按钮取消突兀的大面积高饱和蓝色填充，改为轻卡片主操作，续费金额独立弱强调
- 浅色 / 深色主题同步适配；窄屏下自动续费与手动续费继续纵向排列并保持一致触控尺寸
- v1.0.6 → v1.0.7 为 Panel UI 小版本更新；已是 Host Agent v1.0.4 的节点无需重复更新

> Android v1.0.2 无需升级，本次不修改 Mobile API。

**由 𝐍𝐀𝐌𝐄𝐋𝐄𝐒𝐒 和 GPT 倾力打造**
EOF_NOTES
(cd "$DIST" && sha256sum "xnat-panel-v${PANEL_VERSION}.zip" "xnat-host-agent-v${AGENT_VERSION}.zip" "xnat-bootstrap-panel-v${RELEASE_VERSION}.sh" "xnat-bootstrap-host-v${RELEASE_VERSION}.sh" release.json RELEASE_NOTES.md > SHA256SUMS.txt)
echo "Release assets created in: $DIST"
cat "$DIST/SHA256SUMS.txt"
