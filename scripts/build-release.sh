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
bash "$ROOT/scripts/check.sh"
(cd panel && zip -Dqr "$DIST/xnat-panel-v${PANEL_VERSION}.zip" . -x '.env' '.venv/*' 'data/*' '__pycache__/*' '*.pyc')
(cd agent && zip -Dqr "$DIST/xnat-host-agent-v${AGENT_VERSION}.zip" . -x '.env' '.venv/*' 'tls/*' '__pycache__/*' '*.pyc')
cp scripts/bootstrap-panel.sh "$DIST/xnat-bootstrap-panel-v${RELEASE_VERSION}.sh"
cp scripts/bootstrap-host.sh "$DIST/xnat-bootstrap-host-v${RELEASE_VERSION}.sh"
cp release.json "$DIST/release.json"
cat > "$DIST/RELEASE_NOTES.md" <<EOF_NOTES
# XNAT v${RELEASE_VERSION}

系统镜像最低磁盘策略与后台配置能力更新。

- XNAT Release：v${RELEASE_VERSION}
- Panel：v${PANEL_VERSION}
- Host Agent：v${AGENT_VERSION}
- Agent API：v${AGENT_API_VERSION}
- Mobile API：v1
- 系统镜像最低系统盘改为 Panel 后台逐镜像配置，不再按 Debian / Ubuntu 家族写死
- 默认 Debian 12 / 13 为 1 GiB、Ubuntu 22.04 / 24.04 为 2 GiB、Alpine 3.24 为 1 GiB
- LXC 直接使用后台配置；KVM 使用 max(镜像配置, 3 GiB)
- 管理后台系统镜像页面支持直接修改最低系统盘，新建镜像时也可指定
- 旧数据库仅执行一次兼容迁移，不会在后续启动中覆盖管理员自定义值
- Mobile API v1 的 /api/v1/system-images 新增 min_disk_gb 字段，现有客户端保持兼容
- v1.0.2 → v1.0.3 已作为正式验证的直接 Panel 升级路径
- Host Agent、Agent API、Panel 整体 UI 与主要业务交互保持不变

> Android v1.0.1 仍可继续使用；后续 Android 版本会改为直接读取 Panel 下发的 min_disk_gb。

**由 𝐍𝐀𝐌𝐄𝐋𝐄𝐒𝐒 和 GPT 倾力打造**
EOF_NOTES
(cd "$DIST" && sha256sum "xnat-panel-v${PANEL_VERSION}.zip" "xnat-host-agent-v${AGENT_VERSION}.zip" "xnat-bootstrap-panel-v${RELEASE_VERSION}.sh" "xnat-bootstrap-host-v${RELEASE_VERSION}.sh" release.json RELEASE_NOTES.md > SHA256SUMS.txt)
echo "Release assets created in: $DIST"
cat "$DIST/SHA256SUMS.txt"
