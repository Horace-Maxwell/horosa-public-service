# 公共 MCP 的 Linux 容器

本项目把 Horosa 公共 MCP 打成一个 `linux/amd64` 镜像：Python 排盘服务 + Java 聚合层 + 本包的 `horosa-public serve`，由
`deploy/public/entrypoint.py` 监管。所有输入都是发布件或本仓库文件，构建过程不读 skill 的源码树。

Codex 阶段的验收记录（基于 skill 分支与 v0.40.0 种子）保留在 `notes-local/plugin-directory/`；本页描述的是本项目的新构建，
验收状态以 `src/horosa_public/contracts/public_container.json` 的 `verified_on` 为准（`null` = 尚未在 Linux 上验收）。

## 构建输入与边界

| 输入 | 来源 | 固定方式 |
| --- | --- | --- |
| Horosa Skill 运行时种子 | GitHub Release v0.47.1 `horosa-runtime-darwin-arm64-v0.47.1.tar.gz` | 契约 `seed.sha256` / `size`，与发布页 `SHA256SUMS.txt` 一致 |
| Horosa Skill 本体 | GitHub Release v0.47.1 wheel | `uv.lock` 记录 sha256，`mcp-requirements.txt` 以 `--require-hashes` 安装 |
| 本包 `horosa-public` | `uv build --wheel` 产物，放进构建上下文 | 容器内 `--no-deps --no-index` 安装 |
| Node 22、Temurin 17 JRE | 官方发行 | 契约 `node` / `java` 的 sha256 |
| 需编译的两个 Python 包（pyswisseph、sxtwl） | PyPI sdist | 契约 `source_builds` 的 sha256；`--no-build-isolation --no-index` 构建 |
| 其余排盘依赖 | 种子里的纯 Python 包逐字节复制；原生包按 `runtime_python_lock.json` 重拉 Linux wheel | 锁文件来自 tag v0.47.1（`vendor/horosa-skill-v0.47.1/`） |
| JS 引擎 | 种子自带的 `horosa-core-js`（含 `node_modules`，无原生模块） | 构建时不运行 npm、不联网拉 JS 包 |
| 星历文件 | 种子自带 `Horosa-Web/flatlib-ctrad2/flatlib/resources/swefiles` | 排盘服务进程设 `SE_EPHE_PATH` 指向它（契约 `ephemeris_path`） |

基础镜像固定到 digest（`python_image` / `python_build_image`）。运行用户 `10001:10001`，根目录 `/opt/horosa` 不可写，
请求缓存根 `/opt/horosa/no-store` 为空、root 所有、不可写；Java 的文件回退写入会失败，内存缓存随进程消失。

## 准备与构建

需要 Python 3.12、`uv`、能构建 `linux/amd64` 镜像的 Docker。Apple Silicon 宿主用 Colima（vz + Rosetta）仿真；
验收是否通过只看实测。以下命令在仓库根目录执行：

```bash
set -e
export DOCKER_CONTEXT=colima-horosa-plugin
uv run python scripts/prepare_public_container.py --check-docker-space
uv run python scripts/prepare_public_container.py \
  --seed "$HOME/horosa-release/v0.47.1/horosa-runtime-darwin-arm64-v0.47.1.tar.gz" --force
docker build --platform linux/amd64 -t horosa-public:local build/public-container
```

`prepare_public_container.py` 会：核对种子的 sha256 与内容清单（复用 skill 的发布校验），复制平台无关树，
导出 `mcp-requirements.txt`（本项目 `uv.lock` 的 Linux 闭包，含 skill wheel 的 URL + hash），构建本包 wheel，
下载并核对 Node / JRE / sdist。镜像第二阶段只装 `/opt/horosa` 与两份系统库。

## 仅在本机运行

只把 MCP 发布到宿主回环上的随机端口；排盘服务与 Java 只监听容器内回环，不发布。

```bash
docker run --detach --name horosa-public \
  --platform linux/amd64 \
  --read-only \
  --tmpfs /tmp:rw,nosuid,nodev,size=64m \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  --pids-limit 256 \
  --publish 127.0.0.1::8765 \
  horosa-public:local
docker port horosa-public 8765/tcp
```

就绪后请求该地址的 `/healthz`，再用 MCP 客户端连接同一地址的 `/mcp`。不要发布到所有网卡，也不要映射 8877 / 9977。
容器可读的环境变量只有三项：`HOROSA_MCP_ALLOWED_HOSTS`（公网主机名，例如 `mcp.horosa.org`）、
`HOROSA_MCP_MAX_CONCURRENT_TOOLS`、`HOROSA_OPENAI_CHALLENGE`（域名验证令牌）。其余一律不透传。

Supervisor 只有在排盘服务预热记录满足 `loaded > 0`、`failed = 0`，`/healthz` 为 `warm`，且 Java 身份端点可达后才启动公共 MCP。
`docker inspect` 的健康检查调用同一入口 `--healthcheck`。

## 请求数据与日志的实际行为

- 公共进程不建记忆库、不写 runs、不产生 trace、不接云端决策（`horosa_public.config` 在启动前拒绝相反的环境）。
- 排盘服务、Java、MCP 的标准输出与错误输出都丢弃；Java 用 `Null` appender 的 log4j2 配置且日志根不可写。
- 准确的说法是「设计上不持久化请求缓存；服务进程可暂存计算数据，进程结束后该缓存消失」，不能写成「没有缓存」。
- `/tmp` 是 64 MiB 的内存文件系统，供运行时使用；它的内容同样属于验收检查范围。

## 验收清单（每次重建镜像后）

1. `docker build` 成功，`horosa-public --version` 与 `horosa_skill.__version__` 符合契约。
2. 冷启动到 `/healthz` 200 的时间；`tools/list` 恰好 24 个工具，全部 `noauth` 与只读注解。
3. 一次本命盘、一次黄历、一次不带位置的奇门（应按钟表时并带告警），输出里没有请求值回显、没有 id 与时间戳。
4. 文件 / 日志 canary：运行前后容器内无新增文件内容；`docker diff` 为空。
5. 把结果写回 `public_container.json` 的 `verified_on`，并把证据放到 `notes-local/`。

第 1–4 条可一次跑完：`bash scripts/accept_container.sh horosa-public:<版本>`。它报出两个版本号，跑 tools/list 和六次调用：本命盘、黄历、无位置奇门、
带地点八字、无位置卜卦（应拒）、带坐标塔罗（应拒）。再查禁出键、奇门的位置回显和 `docker diff`。
任何一项不符就退出 1；证据与 `summary.json` 写到 `notes-local/build/acceptance-<UTC>/`，跑完删掉自己建的容器。

## 验收记录

| 日期 | 镜像 | 宿主 | 结果 |
| --- | --- | --- | --- |
| 2026-10-08 | `horosa-public:0.1.0`（skill 0.40.1；`bc218da16aaf`） | MacBook Pro M5 Max，Colima vz + Rosetta 仿真 amd64 | 冷启动到 `/healthz` 30 s；24 工具全 `noauth` / 只读；本命盘 / 黄历 / 八字（带地点）/ 奇门（无地点→钟表时并告警，无坐标回显）均 ok 且无 id / 时间戳键；卜卦无位置拒绝；塔罗带坐标拒绝；调用后 `docker diff` 为空；约 834 MiB / 145 PIDs。证据 `notes-local/build/acceptance-20261009T011733Z/` |
| 2026-10-16 | `horosa-public:0.1.1`（skill 0.41.0；`e698b332bef2`） | 同上 | `scripts/accept_container.sh` 全过：冷启动 31 s；24 工具全 `noauth` / 只读；六次调用结果同上（卜卦无位置拒绝、塔罗带坐标拒绝）；禁出键 0、奇门无位置回显、`docker diff` 空；约 840 MiB / 144 PID。与 0.1.0 逐个调用比对：结构化输出只差 `technique_card.versions.skill`，`tools/list` 完全相同。证据 `notes-local/build/acceptance-20261016T170411Z` |
| 2026-10-22 | `horosa-public:0.1.2`（skill 0.41.2；`f7150bfe660a`） | 同上 | `scripts/accept_container.sh` 全过：冷启动 30 s；24 工具全 `noauth` / 只读；六次调用结果同上；禁出键 0、奇门无位置回显、`docker diff` 空；约 839 MiB / 144 PID。与 0.1.1 逐个调用比对：结构化输出只差 `technique_card.versions.skill`，`tools/list` 完全相同。证据 `notes-local/build/acceptance-20261022T182830Z` |
| 2026-11-06 | `horosa-public:0.1.3`（skill 0.47.1，mcp 2.3.0；`4ef2ad037c89`） | 同上 | `scripts/accept_container.sh` 全过：冷启动 46 s；24 工具全 `noauth` / 只读，线上顶层与 `_meta` 都有 `securitySchemes`；六次调用结果同上；禁出键 0、奇门无位置回显、`docker diff` 空；约 847 MiB / 138 PID。与 0.1.2 逐个调用比对：`tools/list` 只差过宫 `doubingSu28` 的说明；段数相同，内容差在版本号与上游改动（奇门换日默认 23 点、本命盘气候带文案、八字天医口径行）。证据 `notes-local/build/acceptance-20261106T180658Z` |

仿真下的数字不代表 Mac mini（M4）或原生 x86_64；换机器后按「验收清单」重跑。
