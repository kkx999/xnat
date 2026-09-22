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
bash "$ROOT/scripts/check-v109.sh"
(cd panel && zip -Dqr "$DIST/xnat-panel-v${PANEL_VERSION}.zip" . -x '.env' '.venv/*' 'data/*' '__pycache__/*' '*.pyc')
(cd agent && zip -Dqr "$DIST/xnat-host-agent-v${AGENT_VERSION}.zip" . -x '.env' '.venv/*' 'tls/*' '__pycache__/*' '*.pyc')
cp scripts/bootstrap-panel.sh "$DIST/xnat-bootstrap-panel-v${RELEASE_VERSION}.sh"
cp scripts/bootstrap-host.sh "$DIST/xnat-bootstrap-host-v${RELEASE_VERSION}.sh"
cp release.json "$DIST/release.json"
cat > "$DIST/RELEASE_NOTES.md" <<EOF_NOTES
# XNAT v${RELEASE_VERSION}

后台管理界面与用户前端视觉统一。

- XNAT Release：v${RELEASE_VERSION}
- Panel：v${PANEL_VERSION}
- Host Agent：v${AGENT_VERSION}（本版未修改 Host Agent）
- Agent API：v${AGENT_API_VERSION}
- Mobile API：v1
- 后台管理界面整体统一为与用户前端一致的 XNAT 蓝灰玻璃视觉语言
- 统一主按钮、次按钮、危险按钮、启用 / 停用、上架 / 下架、搜索、保存、创建与表格内操作按钮的尺寸、圆角、边框、配色和交互动效
- 系统镜像新增表单改为同一行对齐，表格内最低系统盘保存与状态操作保持紧凑
- 套餐、优惠码、用户余额、管理员手动开通与设置页同步统一卡片层级、输入框、折叠区域和操作反馈
- 后台表格、分页、焦点状态、浅色 / 深色主题与窄屏布局同步优化
- 本版仅调整 Panel 管理 UI；Android 继续保持 v1.0.4，无需更新
- v1.0.8 → v1.0.9 为 Panel UI 小版本更新；已是 Host Agent v1.0.4 的节点无需重复更新

**由 𝐍𝐀𝐌𝐄𝐋𝐄𝐒𝐒 和 GPT 倾力打造**
EOF_NOTES
(cd "$DIST" && sha256sum "xnat-panel-v${PANEL_VERSION}.zip" "xnat-host-agent-v${AGENT_VERSION}.zip" "xnat-bootstrap-panel-v${RELEASE_VERSION}.sh" "xnat-bootstrap-host-v${RELEASE_VERSION}.sh" release.json RELEASE_NOTES.md > SHA256SUMS.txt)
echo "Release assets created in: $DIST"
cat "$DIST/SHA256SUMS.txt"
