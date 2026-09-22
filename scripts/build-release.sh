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
bash "$ROOT/scripts/check-v110.sh"
(cd panel && zip -Dqr "$DIST/xnat-panel-v${PANEL_VERSION}.zip" . -x '.env' '.venv/*' 'data/*' '__pycache__/*' '*.pyc')
(cd agent && zip -Dqr "$DIST/xnat-host-agent-v${AGENT_VERSION}.zip" . -x '.env' '.venv/*' 'tls/*' '__pycache__/*' '*.pyc')
cp scripts/bootstrap-panel.sh "$DIST/xnat-bootstrap-panel-v${RELEASE_VERSION}.sh"
cp scripts/bootstrap-host.sh "$DIST/xnat-bootstrap-host-v${RELEASE_VERSION}.sh"
cp release.json "$DIST/release.json"
cat > "$DIST/RELEASE_NOTES.md" <<EOF_NOTES
# XNAT v${RELEASE_VERSION}

服务器删除与 NAT / SSH 端口安全修复。

- XNAT Release：v${RELEASE_VERSION}
- Panel：v${PANEL_VERSION}
- Host Agent：v${AGENT_VERSION}
- Agent API：v${AGENT_API_VERSION}
- Mobile API：v1
- 正常删除改为 Host-first：真实实例删除并复核成功后，Panel 才清理服务器记录
- Host 离线、TLS / Token 异常或删除失败时，Panel 记录保留，不会出现“面板已删但 Host 还活着”的假成功
- 管理员新增“强制从 Panel 移除”，仅用于 Host 已重装 / 失联等恢复场景；该操作永远不联系 Host
- 强制移除后相关 SSH / NAT 端口在 Panel 侧隔离 30 天
- Host Agent v${AGENT_VERSION} 新增真实 Incus proxy 端口占用查询，Panel 分配新端口时避开 Host 实际占用
- Host Agent 删除接口使用严格删除并复核实例不存在，Panel 再执行一次 inspect 复核
- Provision 遇到明确 SSH 端口占用冲突时自动换端口并重试
- v1.0.9 的后台 UI 统一优化全部保留
- Android 对应版本 v1.0.5；Mobile API 仍保持 v1

**由 𝐍𝐀𝐌𝐄𝐋𝐄𝐒𝐒 和 GPT 倾力打造**
EOF_NOTES
(cd "$DIST" && sha256sum "xnat-panel-v${PANEL_VERSION}.zip" "xnat-host-agent-v${AGENT_VERSION}.zip" "xnat-bootstrap-panel-v${RELEASE_VERSION}.sh" "xnat-bootstrap-host-v${RELEASE_VERSION}.sh" release.json RELEASE_NOTES.md > SHA256SUMS.txt)
echo "Release assets created in: $DIST"
cat "$DIST/SHA256SUMS.txt"
