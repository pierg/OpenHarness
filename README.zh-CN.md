# OpenHarness Fork 中文说明

本仓库是 [HKUDS/OpenHarness](https://github.com/HKUDS/OpenHarness) 的 fork。

当前 fork 的权威说明以英文版 [README.md](README.md) 和 [docs/](docs/README.md) 为准。这个中文页只保留简短导航，避免重复维护两套过期文档。

## 本 Fork 增加了什么

- YAML agent 配置和可组合的 agent runtime。
- Google Gemini 和 Vertex AI client 支持。
- 运行开始时自动生成 run ID：`run-oh-MMDD-HHMMSS-xxxx`。
- 每次运行的 workspace、messages、events、results、metrics 都写入 `runs/<run_id>/`。
- 示例强制使用本地 Langfuse，并在运行开始时打印 trace URL。
- Harbor task 可以通过同一套 YAML agent 配置端到端运行。
- coordinator/worker 示例共享同一个 run ID 和 trace。

## 快速开始

```bash
git clone <this-fork-url>
cd OpenHarness_fork
uv sync --extra dev --extra harbor
source .venv/bin/activate

export GOOGLE_API_KEY=...
export LANGFUSE_PUBLIC_KEY=...
export LANGFUSE_SECRET_KEY=...
export LANGFUSE_BASE_URL=http://localhost:3000
```

如果使用 Vertex AI，可以设置 `VERTEX_PROJECT` 或 `GOOGLE_CLOUD_PROJECT`，并按需设置 `VERTEX_LOCATION` 或 `GOOGLE_CLOUD_LOCATION`。

运行示例：

```bash
.venv/bin/python examples/local_fix_bug/run.py
.venv/bin/python examples/local_workflow_coordinator_worker_fix_bug/run.py
.venv/bin/python examples/harbor_fix_bug/run.py
```

## 文档

- [README.md](README.md)：精简入口说明。
- [docs/features.md](docs/features.md)：本 fork 的核心功能。
- [docs/architecture.md](docs/architecture.md)：runtime、workflow、coordinator、Harbor 的数据流。
- [docs/runs.md](docs/runs.md)：run ID、artifact layout、Langfuse trace、Harbor metadata。
- [docs/examples.md](docs/examples.md)：当前保留哪些示例，以及每个示例展示什么。
