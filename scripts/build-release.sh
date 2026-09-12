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

本次版本聚焦低资源 Host、Alpine LXC 与宿主机资源可视化，在保持现有业务与 Agent API v1 兼容的前提下降低部署门槛。

- Panel：v${PANEL_VERSION}
- Host Agent：v${AGENT_VERSION}
- Agent API：v${AGENT_API_VERSION}
- Mobile API：v1
- LXC Host 最低安装门槛调整为 1C / 1GB / 4.5GB 可用硬盘；4.5–5GB 区间会提示低容量警告
- KVM / 混合 Host 最低安装门槛调整为 1C / 1GB / 6.5GB 可用硬盘，并继续强制检查 /dev/kvm
- Host 安装菜单新增当前资源、最低门槛、系统安全预留与 natpool 自动建议，普通安装无需手算 natpool
- 新增 Alpine 3.24 LXC 镜像，Agent v${AGENT_VERSION} 支持 apk + OpenRC 的 SSH 初始化；Debian / Ubuntu 原有 apt + systemd 流程保持不变
- LXC 套餐允许最低 1C / 64MB / 128MB；管理员可自由向上配置，不写死 Alpine 套餐规格
- KVM 小鸡继续保留 512MB / 4GB 技术下限，避免产生不稳定的完整虚拟机配置
- 宿主机后台“可分配资源”同时参考逻辑已分配量与实际资源余量，存储展示区分逻辑分配与 natpool 实际余量
- 系统镜像与已有自定义镜像保持兼容，升级只补充缺失的 Alpine 镜像，不删除或改写现有镜像
- 正式支持 v1.4.3 → v${PANEL_VERSION} 原地升级；已有用户、余额、订单、VPS、Host、套餐、端口、工单、充值与通知数据保持不变
- Host Agent API 继续保持 v${AGENT_API_VERSION}，现有 Panel / Host 通信协议不做破坏性升级

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
