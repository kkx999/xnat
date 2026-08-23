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

本次为 XNAT Panel 的套餐展示一致性修复。

- Panel：v${PANEL_VERSION}
- Host Agent：v${AGENT_VERSION}
- Agent API：v${AGENT_API_VERSION}
- Mobile API：v1
- 首页“在售套餐”补齐“服务器地区”和“网络线路”
- 首页与登录后的套餐中心统一为完整的 3×3 套餐规格布局
- 直接复用套餐已有字段，不新增数据库列，不改变套餐、库存或购买逻辑
- Mobile API v1 保持不变，XNAT Android 无需更新
- 正式支持 v1.4.2 → v${PANEL_VERSION} 原地升级
- 升级继续执行 SQLite quick_check、完整备份、健康检查与失败回滚；.env、用户、余额、订单、VPS、Host、套餐、端口、工单、充值和通知数据全部保留
- Host Agent v${AGENT_VERSION} / Agent API v${AGENT_API_VERSION} 核心协议不变

**由 𝐍𝐀𝐌𝐄𝐋𝐄𝐒𝐒 和 GPT 倾力打造**

Panel / Host 推荐升级命令：

    xnat update ${RELEASE_VERSION}

GitHub 发布时请创建并真正 Publish Tag \`v${RELEASE_VERSION}\` 的 Release，不要只保留 Draft；无版本安装器通过 \`releases/latest\` 识别最新正式版。
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
