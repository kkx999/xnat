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

本次为 Panel 移动端导航与升级兼容性更新。

- Panel：v${PANEL_VERSION}
- Host Agent：v${AGENT_VERSION}
- Agent API：v${AGENT_API_VERSION}
- 手机端用户中心改为 off-canvas Drawer，桌面端侧边栏保持原布局
- “概览 / 服务 / 账务 / 支持 / 账户 / 管理”支持独立折叠，当前页面分类自动展开
- 分类展开状态在当前标签页记忆；支持遮罩、关闭按钮、ESC、导航跳转和左滑关闭
- 修复透明 backdrop 关闭后仍可能拦截页面点击的问题
- 修复 hamburger 三条横线未对齐
- 使用 visualViewport.height + 100svh fallback 处理移动端真实可视高度
- 增加 Android gesture/navigation bar 底部安全空间，保证余额 / 首页 / 退出完整可见
- 中间导航区域独立滚动，底部账户区域固定在可视区域
- 深色 / 明亮主题均适配新版移动端 Drawer
- scripts/check.sh 自动优先使用已安装 Panel 的 .venv，并新增移动端导航回归守卫
- 正式支持 v1.3.0 → v1.3.1 Panel 原地升级
- 继续支持 v1.2.0 → v1.3.1 additive schema 直接升级
- 升级前自动备份 SQLite、.env、代码、systemd 与管理命令；失败时继续使用既有回滚逻辑
- Host Agent 保持 v${AGENT_VERSION}；已经运行 Agent v${AGENT_VERSION} 的 Host 无需因本次 Panel 更新重装或升级

v1.3.0 Panel 推荐升级命令：

    xnat update ${RELEASE_VERSION}

如果从 v1.2.0 直接升级到本 Release，Panel 可直接执行同一命令；Host 若仍为旧 Agent，则再将 Host 更新到本 Release 对应的 Agent v${AGENT_VERSION}。
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
