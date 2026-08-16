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

XNAT v${RELEASE_VERSION} 运营可靠性与用户体验版本。

- Panel：v${PANEL_VERSION}
- Host Agent：v${AGENT_VERSION}
- Agent API：v${AGENT_API_VERSION}
- 新增节点维护 / Drain 和 CPU、内存、natpool 调度水位保护
- 新增 Host 离线 / 恢复、资源水位、任务最终失败和数据库备份失败通知
- 新增到期提醒、宽限期、自动停机、续费恢复与可选延迟自动删除
- 自动删除默认关闭，必须由管理员主动开启
- 流量周期独立于 VPS 到期，支持每 30 天滚动或每月固定日期重置
- 用户控制台加入克制的卡片反馈、按钮 Loading、复制反馈、Toast 和流量动画
- 新增独立公告中心、历史公告、未读提示与首次登录重点公告；后台公告管理从站点设置独立
- v1.1.0 数据库改动为 additive migration，可兼容 v1.0.x SQLite 备份
- v1.0.2 → v1.1.0 原地升级会自动备份并保留 .env、SQLite 数据和现有 Host/VPS
- v1.0.2 USDT 三位小数和手动补单能力继续保留

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
