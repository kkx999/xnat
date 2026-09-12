#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
RELEASE_VERSION="$(tr -d '[:space:]' < VERSION)"
PANEL_VERSION="$(tr -d '[:space:]' < panel/VERSION)"
AGENT_VERSION="$(tr -d '[:space:]' < agent/VERSION)"
DIST="$ROOT/dist"
mkdir -p "$DIST"
find "$DIST" -maxdepth 1 -type f -delete
bash "$ROOT/scripts/check.sh"
(cd panel && zip -Dqr "$DIST/xnat-panel-v${PANEL_VERSION}.zip" . -x '.env' '.venv/*' 'data/*' '__pycache__/*' '*.pyc')
(cd agent && zip -Dqr "$DIST/xnat-host-agent-v${AGENT_VERSION}.zip" . -x '.env' '.venv/*' 'tls/*' '__pycache__/*' '*.pyc')
cp scripts/bootstrap-panel.sh "$DIST/xnat-bootstrap-panel-v${RELEASE_VERSION}.sh"
cp scripts/bootstrap-host.sh "$DIST/xnat-bootstrap-host-v${RELEASE_VERSION}.sh"
cp release.json "$DIST/release.json"
cat > "$DIST/RELEASE_NOTES.md" <<EOF_NOTES
# XNAT v${RELEASE_VERSION}

XNAT 正式基线重新整理。

- Panel：v${PANEL_VERSION}
- Host：v${AGENT_VERSION}
- Agent API：v1
- Mobile API：v1
- LXC Host 最低 1C / 1GB / 8GiB；KVM / 混合最低 1C / 1GB / 12GiB + /dev/kvm
- Host 安装器只需选择模式和给小鸡使用的总硬盘，系统稳定空间由 XNAT 自动保护
- 普通状态与更新界面不再展示内部 Release 版本
- Panel 现有页面布局、视觉风格和交互逻辑保持不变
- 旧开发阶段版本不提供原地升级兼容，请全新重装

**由 𝐍𝐀𝐌𝐄𝐋𝐄𝐒𝐒 和 GPT 倾力打造**
EOF_NOTES
(cd "$DIST" && sha256sum "xnat-panel-v${PANEL_VERSION}.zip" "xnat-host-agent-v${AGENT_VERSION}.zip" "xnat-bootstrap-panel-v${RELEASE_VERSION}.sh" "xnat-bootstrap-host-v${RELEASE_VERSION}.sh" release.json RELEASE_NOTES.md > SHA256SUMS.txt)
echo "Release assets created in: $DIST"
cat "$DIST/SHA256SUMS.txt"
