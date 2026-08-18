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

本次为 XNAT v${RELEASE_VERSION} 的兼容性增量更新，重点收口机器展示身份、充值订单安全和 Telegram 通知引导。

- Panel：v${PANEL_VERSION}
- Host Agent：v${AGENT_VERSION}
- Agent API：v${AGENT_API_VERSION}
- Mobile API：v1
- 修复后台余额“扣除”操作在 submitter 参数丢失时可能被错误解释为“增加”的问题；新版余额表单 fail closed
- Host 节点前端字段收口为“前端展示旗帜 / 机器编号前缀”，稳定展示编号与 Host 内部 nat-* 实例名分离
- 30 个常用国家/地区使用独立本地 SVG 国旗资产，香港固定映射 HK；国旗与 CSS/JS 均使用内容 SHA-256 指纹防缓存回退
- 套餐购买页保留服务器地区、网络线路、NAT 端口展示；已开通机器保存地区/线路快照，后续修改套餐不会改写旧机器
- 用户服务器卡片使用状态点、国旗、稳定编号、系统和虚拟化标签；服务器详情概览精简为 8 个核心信息卡片
- 充值订单支持用户主动取消；取消后的延迟链上付款进入异常支付，不自动增加余额
- 充值 TxHash 复用校验与管理员补单幂等保护加强，余额流水继续关联充值订单
- Telegram Bot Token 保存时通过 getMe 验证并自动识别机器人用户名；用户端增加 Start 引导、打开机器人和测试消息
- Bot 未配置或 Chat ID 无效时 Telegram 通知无法被误开启；Telegram API 错误文本不会暴露 Bot Token URL
- Mobile API 继续保持 v1；display_id / country / region / region_code / network_line / nat_port 等为向后兼容增量字段
- 旧客户端继续可使用 server.name，重装确认同时兼容稳定编号与旧内部实例名
- Panel 数据库迁移保持 additive：只新增字段/索引并回填展示快照，不删除旧字段、不重建旧表
- 正式支持 v1.4.0 → v${PANEL_VERSION} 原地升级，并继续保留更早版本的既有 additive migration 路径
- 升级继续执行 SQLite quick_check、完整备份、健康检查与失败回滚；.env、用户、余额、订单、VPS、Host、套餐、端口、工单、充值和通知数据全部保留
- Host Agent v${AGENT_VERSION} / Agent API v${AGENT_API_VERSION} 核心协议不变；Host 可同步本 Release 的 CLI 与 Release 元数据

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
    > SHA256SUMS.txt
)

echo "Release assets created in: $DIST"
cat "$DIST/SHA256SUMS.txt"
