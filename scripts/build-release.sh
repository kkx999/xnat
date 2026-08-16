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

XNAT v${RELEASE_VERSION} 稳定修复版本。

- Panel：v${PANEL_VERSION}
- Host Agent：v${AGENT_VERSION}
- Agent API：v${AGENT_API_VERSION}
- 管理后台侧边栏改为折叠分类并统一深蓝灰视觉
- 后台卡片、表格、输入框、按钮与文字对比度统一优化
- 数据库备份支持本地 .db 上传校验与后台直接恢复
- 恢复前自动创建安全快照，失败自动回滚
- 数据库备份 / 下载 / 上传 / 恢复操作写入审计日志
- 修复 xnat update 在 pipefail 环境下可能下载后提前退出的问题
- 保持 v1.0.0 已验证的 Host、NAT、VPS 生命周期逻辑不变

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
