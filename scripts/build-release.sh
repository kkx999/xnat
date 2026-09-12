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

本次版本修复母机系统兼容和 Host 安装容量判断，让低配 LXC Host 按真实资源计算，而不是把母机总盘门槛误当成安装后的剩余空间。

- Panel：v${PANEL_VERSION}
- Host Agent：v${AGENT_VERSION}
- Agent API：v${AGENT_API_VERSION}
- Mobile API：v1
- Panel / Host 支持 Debian 12/13、Ubuntu 22.04/24.04/26.04 LTS
- Host 菜单显示 CPU、总/可用内存、总/已用/可用硬盘、KVM 与各模式预计 natpool
- LXC：1C / 1GB / 4.5GiB 总盘基线，约 1GiB 系统/XNAT 预留后动态计算 natpool
- KVM / 混合：1C / 1GB / 6.5GiB 总盘、/dev/kvm、至少 4GiB natpool
- 默认 Host 模式改为 LXC；不满足模式会在选择前显示原因
- Zabbly Incus 源按发行版 codename 自动配置并优先使用 Zabbly 包
- v1.5.0 → v${PANEL_VERSION} 支持原地升级，业务数据保持不变
- Host Agent 核心保持 v${AGENT_VERSION} / Agent API v${AGENT_API_VERSION}

**由 𝐍𝐀𝐌𝐄𝐋𝐄𝐒𝐒 和 GPT 倾力打造**

Panel / Host 推荐升级命令：

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
