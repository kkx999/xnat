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

本次版本为 Host 运维修复版本。

- Panel：v${PANEL_VERSION}（业务组件保持不变）
- Host Agent：v${AGENT_VERSION}
- Agent API：v${AGENT_API_VERSION}
- Mobile API：v1
- Host Agent 新增根分区低空间保护：低于安全阈值时先执行白名单安全清理，仍不足则明确阻止创建/重装/端口映射
- Host 菜单新增“清理 Host 系统空间”，仅清理 APT 下载缓存、受限 journal 历史与 XNAT 临时健康检查文件
- 不删除 Incus storage、镜像、实例磁盘、natpool、VPS、Agent Token/TLS 或用户数据
- Host 安装提示区分最低配置与建议配置：LXC 建议 8GiB+，KVM / 混合建议 12GiB+
- Host 版本检查以 Host Agent 版本为主，repo-wide Release 仅作为管理脚本发布来源
- v1.2.0 → v${AGENT_VERSION} 支持原地升级，Agent API 保持 v${AGENT_API_VERSION}

**由 𝐍𝐀𝐌𝐄𝐋𝐄𝐒𝐒 和 GPT 倾力打造**

Host 推荐升级命令：

    xnat update ${RELEASE_VERSION}
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
