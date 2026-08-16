#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

RELEASE_VERSION="$(tr -d '[:space:]' < VERSION)"
PANEL_VERSION="$(tr -d '[:space:]' < panel/VERSION)"
AGENT_VERSION="$(tr -d '[:space:]' < agent/VERSION)"
DIST="$ROOT/dist"

rm -rf "$DIST"
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

XNAT v${RELEASE_VERSION} 公告中心交互与后台一致性维护版本。

- Panel：v${PANEL_VERSION}
- Host Agent：v${AGENT_VERSION}
- Agent API：v${AGENT_API_VERSION}
- 优化后台公告中心布局，移除冗余规则说明并统一操作区尺寸与对齐
- “置顶显示 / 首次登录重点弹出”改为统一可见的 Switch 交互
- 公告支持永久删除，并同步清理对应公告已读记录
- 后台 Flash 与前端统一为右上角 Toast，默认 3 秒自动消失
- 静态资源缓存版本更新，避免升级后浏览器继续加载旧 UI
- 正式验收 v1.1.0 → v1.1.1 原地升级路径
- 升级自动备份并保留 .env、SQLite、用户、余额、订单、VPS、Host、支付、通知、公告及已读记录
- v1.1.1 沿用 additive schema 兼容机制，不重建旧表
- Host Agent 无需升级，继续保持 v1.0.0 / Agent API v1

v1.1.0 Panel 推荐升级命令：

    xnat update 1.1.1

详细介绍请查看项目 README，完整部署与运维说明请查看 docs/README.md。
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
