#!/usr/bin/env bash
set -Eeuo pipefail

REPO="${XNAT_REPO:-kkx999/xnat}"
VERSION="${XNAT_VERSION:-}"

die() {
  echo "[ERROR] $*" >&2
  exit 1
}

command -v curl >/dev/null 2>&1 || die "缺少 curl"
command -v tar >/dev/null 2>&1 || die "缺少 tar"

# If XNAT_VERSION is not explicitly specified, resolve GitHub's latest
# published Release and install that exact tag.
if [[ -z "${VERSION}" ]]; then
  LATEST_URL="$(
    curl -fsSL \
      -o /dev/null \
      -w '%{url_effective}' \
      "https://github.com/${REPO}/releases/latest"
  )"

  TAG="${LATEST_URL##*/}"

  [[ "${TAG}" == v* ]] || \
    die "无法解析最新稳定版本。请使用 XNAT_VERSION=x.y.z 指定版本。"

  VERSION="${TAG#v}"
fi

[[ "${VERSION}" =~ ^[0-9]+\.[0-9]+\.[0-9]+([.-][0-9A-Za-z.-]+)?$ ]] || \
  die "XNAT_VERSION 格式无效：${VERSION}"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

ARCHIVE="https://github.com/${REPO}/archive/refs/tags/v${VERSION}.tar.gz"

echo "XNAT repository: ${REPO}"
echo "XNAT version:    v${VERSION}"
echo "Downloading:     ${ARCHIVE}"

curl -fL --retry 3 --connect-timeout 15 \
  "${ARCHIVE}" \
  -o "$TMP/xnat.tar.gz"

TOP="$(tar -tzf "$TMP/xnat.tar.gz" | awk -F/ 'NR == 1 { print $1 }')"
[[ -n "${TOP}" ]] || die "无法解析源码压缩包"

tar -xzf "$TMP/xnat.tar.gz" -C "$TMP"

INSTALL_SCRIPT="scripts/install-panel.sh"
[[ -f "$TMP/$TOP/$INSTALL_SCRIPT" ]] || die "安装脚本不存在：$INSTALL_SCRIPT"

bash "$TMP/$TOP/$INSTALL_SCRIPT"
