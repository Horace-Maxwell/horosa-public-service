#!/usr/bin/env bash
# 在 Mac mini（Apple 芯片）上准备托管 Horosa 公共 MCP 的环境。
# 只做安全、可重复运行的事：装 Rosetta、用 Homebrew 装 colima / docker / cloudflared、建与验收一致的虚拟机。
# 不改系统设置、不登录 Cloudflare、不碰其它虚拟机或容器。
set -euo pipefail

say() { printf '\n== %s\n' "$*"; }

[ "$(uname -s)" = "Darwin" ] || { echo "这台不是 macOS，停止。"; exit 1; }
ARCH="$(uname -m)"
echo "芯片架构：${ARCH}"

say "1/4 Rosetta（虚拟机里运行 amd64 镜像要用）"
if [ "${ARCH}" != "arm64" ]; then
  echo "不是 Apple 芯片，不需要 Rosetta，跳过。"
elif /usr/bin/arch -x86_64 /usr/bin/true 2>/dev/null; then
  echo "已安装。"
else
  echo "安装 Rosetta（会显示许可协议并自动同意）……"
  softwareupdate --install-rosetta --agree-to-license
fi

say "2/4 Homebrew"
if ! command -v brew >/dev/null 2>&1; then
  echo "还没有 Homebrew。打开 https://brew.sh ，复制首页那一行安装命令到终端运行（会要你的开机密码），"
  echo "装完按它最后提示的两行命令把 brew 加进 PATH，关掉终端重开，再运行本脚本。"
  exit 1
fi
echo "已安装：$(brew --version | head -1)"

say "3/4 安装 colima、docker 命令行、cloudflared"
brew install colima docker cloudflared

say "4/4 虚拟机 horosa-plugin（与验收一致：aarch64 · vz · Rosetta · 4 核 / 6 GB / 80 GB）"
if colima list 2>/dev/null | awk 'NR>1 {print $1}' | grep -qx "horosa-plugin"; then
  echo "已存在，启动它。"
  colima start horosa-plugin
elif [ "${ARCH}" = "arm64" ]; then
  colima start horosa-plugin --arch aarch64 --vm-type vz --vz-rosetta \
    --cpu 4 --memory 6 --disk 80 --runtime docker
else
  colima start horosa-plugin --cpu 4 --memory 6 --disk 80 --runtime docker
fi
docker --context colima-horosa-plugin info --format '虚拟机里的 Docker：{{.ServerVersion}}（{{.Architecture}}）'

say "完成"
echo "下一步：bash \"$(dirname "$0")/load_image.sh\" <镜像 .tar.gz 的路径>   —— 校验并导入镜像"
echo "然后：  bash \"$(dirname "$0")/run_container.sh\" horosa-public:0.1.5   —— 起容器（只绑本机回环）"
