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

XNAT v${RELEASE_VERSION} 支付与运营修复版本。

- Panel：v${PANEL_VERSION}
- Host Agent：v${AGENT_VERSION}
- Agent API：v${AGENT_API_VERSION}
- 用户 USDT 支付页面重构，隐藏用户端 Token 合约地址
- 新充值订单金额统一为 3 位小数，旧未支付订单保留原精确金额
- 后台新增 TxHash 校验补单与全局防重复保护
- 新增无 TxHash 强制补单，要求补单原因与 FORCE CREDIT 二次确认
- 补单按订单原人民币金额入账，并写入余额流水与审计日志
- 保留 v1.0.1 的折叠侧栏、统一 UI 与数据库上传/恢复能力
- Host、NAT、VPS 核心逻辑保持不变

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
