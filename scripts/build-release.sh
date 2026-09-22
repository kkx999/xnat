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
bash "$ROOT/scripts/check-v108.sh"
(cd panel && zip -Dqr "$DIST/xnat-panel-v${PANEL_VERSION}.zip" . -x '.env' '.venv/*' 'data/*' '__pycache__/*' '*.pyc')
(cd agent && zip -Dqr "$DIST/xnat-host-agent-v${AGENT_VERSION}.zip" . -x '.env' '.venv/*' 'tls/*' '__pycache__/*' '*.pyc')
cp scripts/bootstrap-panel.sh "$DIST/xnat-bootstrap-panel-v${RELEASE_VERSION}.sh"
cp scripts/bootstrap-host.sh "$DIST/xnat-bootstrap-host-v${RELEASE_VERSION}.sh"
cp release.json "$DIST/release.json"
cat > "$DIST/RELEASE_NOTES.md" <<EOF_NOTES
# XNAT v${RELEASE_VERSION}

Mobile API 自动续费配套更新。

- XNAT Release：v${RELEASE_VERSION}
- Panel：v${PANEL_VERSION}
- Host Agent：v${AGENT_VERSION}（本版未修改 Host Agent）
- Agent API：v${AGENT_API_VERSION}
- Mobile API：v1
- 服务器列表与详情新增 `auto_renew` 状态字段，保持向后兼容
- 新增 `POST /api/v1/servers/{server_id}/auto-renew`，供 Android 客户端开启或关闭单台 VPS 自动续费
- 自动续费设置继续由 Panel 保存和执行，到期扣款 / 续费不依赖 Host Agent
- 为 XNAT Android v1.0.4 配套提供自动续费接口；旧 Android 客户端仍可正常使用 Mobile API v1
- v1.0.7 → v1.0.8 为 Panel Mobile API 增量更新；已是 Host Agent v1.0.4 的节点无需重复更新

**由 𝐍𝐀𝐌𝐄𝐋𝐄𝐒𝐒 和 GPT 倾力打造**
EOF_NOTES
(cd "$DIST" && sha256sum "xnat-panel-v${PANEL_VERSION}.zip" "xnat-host-agent-v${AGENT_VERSION}.zip" "xnat-bootstrap-panel-v${RELEASE_VERSION}.sh" "xnat-bootstrap-host-v${RELEASE_VERSION}.sh" release.json RELEASE_NOTES.md > SHA256SUMS.txt)
echo "Release assets created in: $DIST"
cat "$DIST/SHA256SUMS.txt"
