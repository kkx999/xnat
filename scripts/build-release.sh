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

rm -f "$DIST/xnat-panel-v${PANEL_VERSION}.zip"
(
  cd panel
  zip -Dqr "$DIST/xnat-panel-v${PANEL_VERSION}.zip" . \
    -x '.env' '.venv/*' 'data/*' '__pycache__/*' '*.pyc'
)

if [[ ! -f "$DIST/xnat-host-agent-v${AGENT_VERSION}.zip" ]]; then
  (
    cd agent
    zip -Dqr "$DIST/xnat-host-agent-v${AGENT_VERSION}.zip" . \
      -x '.env' '.venv/*' 'tls/*' '__pycache__/*' '*.pyc'
  )
fi

cp scripts/bootstrap-panel.sh "$DIST/xnat-bootstrap-panel-v${RELEASE_VERSION}.sh"
cp scripts/bootstrap-host.sh "$DIST/xnat-bootstrap-host-v${RELEASE_VERSION}.sh"
cp release.json "$DIST/release.json"

AGENT_API_VERSION="$(python3 -c 'import json; print(json.load(open("release.json"))["agent_api_version"])')"
cat > "$DIST/RELEASE_NOTES.md" <<EOF_NOTES
# XNAT v${RELEASE_VERSION}

本次版本增强 Panel 的 Host 容量可视化：按当前真实可分配资源，直接显示每个套餐预计还能开多少台小鸡。

- Panel：v${PANEL_VERSION}
- Host Agent：v${AGENT_VERSION}
- Agent API：v${AGENT_API_VERSION}
- Mobile API：v1
- Host 卡片“预计可开”常驻显示，不放入折叠菜单
- 每个在售/有效套餐显示虚拟化类型、规格、预计台数和当前瓶颈
- 计算继续使用保守的逻辑/物理内存与存储余量，并受最大 VPS、NAT 端口、调度状态和虚拟化兼容约束
- CPU 继续按实时调度水位保护，不伪造 Host Agent v1 未上报的物理核心总数
- v1.6.0 → v${PANEL_VERSION} 支持原地升级，业务数据保持不变
- Host Agent 核心保持 v${AGENT_VERSION} / Agent API v${AGENT_API_VERSION}

**由 𝐍𝐀𝐌𝐄𝐋𝐄𝐒𝐒 和 GPT 倾力打造**

Panel 推荐升级命令：

    xnat update ${RELEASE_VERSION}
EOF_NOTES

(
  cd "$DIST"
  sha256sum \
    "xnat-panel-v${PANEL_VERSION}.zip" \
    "xnat-host-agent-v${AGENT_VERSION}.zip" \
    "xnat-bootstrap-panel-v${RELEASE_VERSION}.sh" \
    "xnat-bootstrap-host-v${RELEASE_VERSION}.sh" \
    release.json \
    RELEASE_NOTES.md \
    > SHA256SUMS.txt
)

echo "Release assets created in: $DIST"
cat "$DIST/SHA256SUMS.txt"
