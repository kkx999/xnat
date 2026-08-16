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

本次为较大的 Panel UI / UX 与用户自助能力升级。

- Panel：v${PANEL_VERSION}
- Host Agent：v${AGENT_VERSION}
- Agent API：v${AGENT_API_VERSION}
- 用户前端与管理后台新增独立深色 / 柔和明亮主题
- 全面优化明亮主题对比度并清理暗色组件残留
- 重构服务器管理与宿主机卡片，统一按钮、表单、Switch、Toast 与折叠交互
- 宿主机新增剩余可分配资源展示，套餐绑定改为 iOS 风格 Switch
- VPS 重装期间显示“重装中”，完成后自动恢复运行状态
- 套餐购买优惠码与后台套餐表单重新整理并统一对齐
- 新增付费自助流量重置；支持套餐独立配置重置价格、余额扣费、订单与余额流水
- 新增统一 XNAT 网页确认 Modal，替代浏览器原生 confirm 弹窗
- 正式支持 v1.1.1 → v1.2.0 原地升级；升级前自动备份并执行 additive schema migration
- 升级保留 .env、SQLite、用户、余额、订单、VPS、Host、套餐、支付、通知、公告及已读记录
- Host Agent 无需升级，继续保持 v1.0.0 / Agent API v1

v1.1.1 Panel 推荐升级命令：

    xnat update 1.2.0

也可执行 xnat 后选择“检查 / 更新 Panel”。
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
