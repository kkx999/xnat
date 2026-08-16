#!/usr/bin/env bash
set -Eeuo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_DIR="${XNAT_PANEL_DIR:-/opt/xnat/panel}"

die(){ echo "[ERROR] $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "请使用 root 运行"
[[ -f "${TARGET_DIR}/app/__init__.py" ]] || die "找不到现有 XNAT Panel：${TARGET_DIR}"
[[ -f "${TARGET_DIR}/data/panel.db" ]] || die "找不到现有 XNAT 数据库：${TARGET_DIR}/data/panel.db"

CURRENT_VERSION="$(grep -E '^__version__[[:space:]]*=' "${TARGET_DIR}/app/__init__.py" | head -n1 | cut -d'"' -f2 || true)"
[[ "$CURRENT_VERSION" == "1.1.0" ]] || die "此脚本只用于 v1.1.0 → v1.1.1；当前检测到 v${CURRENT_VERSION:-unknown}"
[[ "$(tr -d '[:space:]' < "${REPO_ROOT}/VERSION")" == "1.1.1" ]] || die "当前源码包不是 XNAT v1.1.1"

echo "已确认升级路径：XNAT Panel v1.1.0 → v1.1.1"
echo "现有 .env、SQLite 数据库、用户/余额/订单/VPS/Host/支付/通知/公告数据将全部保留。"
echo "升级脚本会先创建可回滚备份，再执行数据库兼容检查并更新 Panel 文件。"
echo "Host Agent 保持 v1.0.0 / Agent API v1，无需重装。"
echo

exec bash "${REPO_ROOT}/scripts/upgrade-panel.sh"
