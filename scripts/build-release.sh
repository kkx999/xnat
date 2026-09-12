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

本次版本修正 Panel 对 Incus/LVM natpool 微小对齐损耗的逻辑容量判断。

- Panel：v${PANEL_VERSION}
- Host Agent：v${AGENT_VERSION}
- Agent API：v${AGENT_API_VERSION}
- Mobile API：v1
- 逻辑 natpool 容量在距离下一个 0.125GiB 分配边界不超过 32MiB 时安全归一化
- 2GiB 名义 natpool 即使实际上报约 1.99GiB，1GiB 套餐在 100% 存储调度阈值下可正确预计并调度 2 台
- 90% 等较低存储调度阈值仍按管理员设置生效，不会被归一化绕过
- 原始物理 natpool 总量 / 使用量不修改，真实存储水位保护继续生效
- v1.6.2 → v${PANEL_VERSION} 支持原地升级，业务数据保持不变
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
