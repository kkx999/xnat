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

本次为 XNAT v${RELEASE_VERSION} 的 Mobile API 向后兼容增量更新，重点为 XNAT Android v1.2.0 补齐原生接口。

- Panel：v${PANEL_VERSION}
- Host Agent：v${AGENT_VERSION}
- Agent API：v${AGENT_API_VERSION}
- Mobile API：v1
- 服务器接口新增套餐名称、中文状态、流量周期、流量重置价格/可用状态等向后兼容字段
- 新增 Mobile API 删除机器，继续复用稳定展示编号确认与既有 delete_server Job
- 新增 Mobile API 付费流量重置；Web 与 App 共用同一业务逻辑，统一订单、扣费、周期、带宽、审计与通知
- 新增原生 USDT 充值 API：配置、创建订单、订单详情、取消和人工模式 TxHash；既有异常支付保护保持不变
- Billing 支持 month=YYYY-MM 自然月读取与 available_months，并补齐订单、流水和充值中文状态标签
- 修复 Mobile API total_spend_cents 漏计 paid 新购订单的问题
- Mobile API 继续保持 v1；旧 XNAT Android v1.1.0 不受新增字段和新增路由影响
- Panel 数据库迁移保持 additive，本轮不新增数据库列、不删除旧字段、不重建旧表
- 正式支持 v1.4.1 → v${PANEL_VERSION} 原地升级，并支持已安装 v1.4.2-dev1 的测试机收口到正式版
- v1.4.2-dev1 已完成实机 API 验收：health、登录、账户、套餐参数、月份账务、充值创建/查询/取消，以及删除/流量重置不存在服务器错误路径均通过
- 升级继续执行 SQLite quick_check、完整备份、健康检查与失败回滚；.env、用户、余额、订单、VPS、Host、套餐、端口、工单、充值和通知数据全部保留
- Host Agent v${AGENT_VERSION} / Agent API v${AGENT_API_VERSION} 核心协议不变

**由 𝐍𝐀𝐌𝐄𝐋𝐄𝐒𝐒 和 GPT 倾力打造**

Panel / Host 推荐升级命令：

    xnat update ${RELEASE_VERSION}

已经安装 v1.4.2-dev1 的测试机首次收口正式版时，由于旧 dev1 CLI 的 Debian 版本比较行为，请使用：

    XNAT_ALLOW_DOWNGRADE=1 xnat update ${RELEASE_VERSION}

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
