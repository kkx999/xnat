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

本次核心升级为 Hybrid Virtualization Host：同一套 XNAT 可按节点能力运行 LXC、KVM 或 LXC + KVM。

- Panel：v${PANEL_VERSION}
- Host Agent：v${AGENT_VERSION}
- Agent API：v${AGENT_API_VERSION}
- Host 安装新增虚拟化模式交互：LXC / KVM / LXC + KVM
- 安装时自动检测 /dev/kvm；KVM 不可用时阻止错误选择并提示检查 Nested Virtualization
- KVM 模式安装时创建临时 Incus VM 做启动、网络与 incus-agent 验证
- Host Agent /health 与 /v1/status 上报 virtualization_modes 与 kvm_available
- 套餐新增虚拟化类型，支持独立销售 LXC 套餐与 KVM 套餐
- 调度器强制匹配套餐虚拟化类型；KVM 套餐不会下发到 LXC-only 或 /dev/kvm 不可用节点
- Server 保存虚拟化类型快照，后续重装不会因套餐修改而改变实例类型
- KVM 实例通过 Incus --vm 创建，LXC 保持原创建路径
- KVM 创建时显式等待 VM 内 incus-agent 就绪，解决 VM 已 RUNNING 但 Guest Agent 尚未 Ready 的时序问题
- Host Agent 错误回传保留 exit code 与有效 stderr/stdout，过滤 apt-utils 的无害 debconf 警告
- KVM 公网 SSH 使用 Incus proxy NAT（nat=true），已按真实 Host 链路验证公网端口 → VM:22
- KVM 客户机网卡不再假设为 eth0，可正确识别 enp5s0 等 VM 网卡名并统计流量
- SSH 初始化强制校验 PermitRootLogin / PasswordAuthentication 的最终生效值，避免镜像默认配置覆盖
- 修复 SSH 校验管道在 pipefail 下出现 SIGPIPE / exit 141 的误失败
- 正式支持 v1.2.0 → v1.3.0 Panel 原地升级；数据库使用 additive schema migration
- Host Agent 升级为 v1.1.0；首次从旧 Agent 升级时可选择 Host 虚拟化模式
- 用户端服务器卡片采用标准响应式网格：宽屏三列、中屏两列、移动端一列；末行按正常顺序左对齐，不再居中或拉伸；套餐卡片继续自适应宽屏排列
- 套餐购买页始终显示优惠码（可选）输入框，避免折叠交互造成入口不明显或不可见
- 管理后台“套餐与库存”改为逐套餐折叠编辑，收起时保留核心规格、售价和库存摘要
- 管理后台 KVM 套餐和实例资源调整采用 512MB / 4GB 前后端双重限制，并给出明确错误提示
- KVM 重置 root 密码会等待 Guest Agent Ready，避免刚开机/重启后的瞬时失败
- 全量状态校验新增 LXC/KVM 类型以及 CPU / 内存 / 磁盘配置漂移检查
- 用户端服务器列表与控制台补充 LXC/KVM 类型标签；后台手动开通和节点套餐绑定同步显示虚拟化类型
- 危险操作文案统一使用“实例”，避免 KVM 页面继续显示“容器”旧文案

v1.2.0 Panel 推荐升级命令：

    xnat update 1.3.0

Host 也需要升级到本 Release 的 Agent v1.1.0，KVM 功能才会生效。
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
