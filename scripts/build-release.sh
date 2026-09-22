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
bash "$ROOT/scripts/check-v105.sh"
(cd panel && zip -Dqr "$DIST/xnat-panel-v${PANEL_VERSION}.zip" . -x '.env' '.venv/*' 'data/*' '__pycache__/*' '*.pyc')
(cd agent && zip -Dqr "$DIST/xnat-host-agent-v${AGENT_VERSION}.zip" . -x '.env' '.venv/*' 'tls/*' '__pycache__/*' '*.pyc')
cp scripts/bootstrap-panel.sh "$DIST/xnat-bootstrap-panel-v${RELEASE_VERSION}.sh"
cp scripts/bootstrap-host.sh "$DIST/xnat-bootstrap-host-v${RELEASE_VERSION}.sh"
cp release.json "$DIST/release.json"
cat > "$DIST/RELEASE_NOTES.md" <<EOF_NOTES
# XNAT v${RELEASE_VERSION}

服务器详情双主题视觉修复。

- XNAT Release：v${RELEASE_VERSION}
- Panel：v${PANEL_VERSION}
- Host Agent：v${AGENT_VERSION}（本版未修改 Host Agent）
- Agent API：v${AGENT_API_VERSION}
- Mobile API：v1
- 修复浅色主题下“服务器实时监控”标题、副标题、状态与指标辅助文字对比度不足
- 去除 Root 密码、NAT 端口、系统重装与删除实例展开区域突兀的深色细分割线
- 降低详情区域偏蓝底色饱和度，使容器、展开区、操作按钮和进度条轨道更贴合整体主题
- 深色主题同步调整实时监控、详情容器、展开区与操作按钮，不做单边主题修复
- CPU / 内存 / 硬盘继续使用全圆角胶囊进度条；网络实时速率和采样逻辑保持不变
- Web 继续每 5 秒刷新、页面不可见时停止轮询；Host Agent 继续使用约 3 秒内存缓存且不保存历史
- v1.0.5 → v1.0.6 为 Panel 功能与认证 UI 更新；已是 Host Agent v1.0.4 的节点无需重复更新

> Android v1.0.2 无需升级，本次不修改 Mobile API。

**由 𝐍𝐀𝐌𝐄𝐋𝐄𝐒𝐒 和 GPT 倾力打造**
EOF_NOTES
(cd "$DIST" && sha256sum "xnat-panel-v${PANEL_VERSION}.zip" "xnat-host-agent-v${AGENT_VERSION}.zip" "xnat-bootstrap-panel-v${RELEASE_VERSION}.sh" "xnat-bootstrap-host-v${RELEASE_VERSION}.sh" release.json RELEASE_NOTES.md > SHA256SUMS.txt)
echo "Release assets created in: $DIST"
cat "$DIST/SHA256SUMS.txt"
