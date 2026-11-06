# Horosa 星阙 public service

The public MCP service behind the Horosa 星阙 plugin for ChatGPT and Codex: anonymous, read-only and stateless. It exposes 24 chart and divination tools computed by the open-source Horosa engines. It has no accounts, no database and no request logs.

星阙公共 MCP 服务的完整源码，匿名、只读、不保存请求。这里发布的是按 AGPL-3.0-only 第 13 条提供给网络用户的对应源码。

## What is here

| Path | Contents |
| --- | --- |
| `src/horosa_public/` | The MCP server, the public tool surface and its contracts |
| `deploy/public/` | Container image definition |
| `deploy/mini/` | Scripts that run the container on a Mac host |
| `scripts/` | Build, acceptance and packaging scripts |
| `vendor/` | Files copied unchanged from the pinned Horosa Skill release |
| `plugins/openai/horosa/` | The plugin package: manifest, skill and icons |
| `site/` | The horosa.org pages |
| `tests/` | The test suite |

## Build and test

You need Python 3.12, [uv](https://docs.astral.sh/uv/) and a Docker daemon that can build `linux/amd64` images.

```bash
uv sync
```

```bash
uv run pytest
```

```bash
bash scripts/fetch_skill_release.sh
```

The container steps and the acceptance checklist are in [docs/PUBLIC_CONTAINER.md](docs/PUBLIC_CONTAINER.md).

## The engines

The calculation engines are not in this repository. `pyproject.toml` pins a release wheel of [Horosa Skill](https://github.com/Horace-Maxwell/horosa-skill), and the image is built from that release's runtime. Source for that release is in the Horosa Skill repository.

## Licence

AGPL-3.0-only. See [LICENSE](LICENSE).

## Support

Open an issue or see [horosa.org/support](https://horosa.org/support). Outputs are cultural and entertainment references, not medical, legal or financial advice.
