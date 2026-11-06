#!/usr/bin/env bash
# 下载 Horosa Skill v0.47.1 的发布文件（插件项目的固定依赖），并按发布页的 SHA256SUMS.txt 逐个核对。
# 用法：bash fetch_skill_release.sh [保存目录]   默认 ~/horosa-release/v0.47.1，约 550 MB；可重复运行，已下好的不重下。
set -euo pipefail

VER="0.47.1"
TAG="v${VER}"
DEST="${1:-${HOME}/horosa-release/${TAG}}"
BASE="https://github.com/Horace-Maxwell/horosa-skill/releases/download/${TAG}"
FILES=("horosa_skill-${VER}-py3-none-any.whl" "horosa-runtime-darwin-arm64-${TAG}.tar.gz" "runtime-manifest.json")

mkdir -p "${DEST}"
cd "${DEST}"
curl -fsSL --retry 3 -o SHA256SUMS.txt "${BASE}/SHA256SUMS.txt"
for f in "${FILES[@]}"; do
  if [ -s "${f}" ] && grep -E "  ${f}\$" SHA256SUMS.txt | shasum -a 256 -c - >/dev/null 2>&1; then
    echo "已有且校验通过：${f}"
  else
    echo "下载：${f}"
    curl -fL --retry 3 -o "${f}" "${BASE}/${f}"
  fi
done

echo "核对 SHA256："
for f in "${FILES[@]}"; do
  if grep -qE "  ${f}\$" SHA256SUMS.txt; then
    grep -E "  ${f}\$" SHA256SUMS.txt | shasum -a 256 -c -
  else
    echo "${f}: 不在 SHA256SUMS.txt 里（清单文件本身由发布流水线单独校验），跳过"
  fi
done
echo "完成：${DEST}"
