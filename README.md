# OpenHarness Fork

This repository is a fork of [HKUDS/OpenHarness](https://github.com/HKUDS/OpenHarness).

Upstream OpenHarness provides the base CLI harness: the interactive shell, tool loop, permissions, skills, plugins, coordinator mode, swarm transport, ohmo, sandboxing, and the React terminal UI. This fork keeps that foundation and adds a reproducible execution layer for YAML-configured agents, Google Gemini and Vertex AI execution, run artifacts, Langfuse traces, and Harbor evaluation.

## What This Fork Adds

- YAML agent configs and composable agent architectures.
- Google Gemini and Vertex AI client support.
- Runtime-generated run IDs: `run-oh-MMDD-HHMMSS-xxxx`.
- Canonical run folders under `runs/<run_id>/`.
- Live Langfuse trace URLs logged at run start.
- Harbor task execution through the OpenHarness Harbor adapter.
- A coordinator/worker example that shares one run ID and trace across the team.

Detailed feature docs live in [docs/features.md](docs/features.md).

## Setup

Requirements:

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- Google AI Studio or Vertex AI credentials
- Langfuse credentials for the examples
- Docker for the Harbor example

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

For Vertex AI, set `VERTEX_PROJECT` or `GOOGLE_CLOUD_PROJECT`, and optionally `VERTEX_LOCATION` or `GOOGLE_CLOUD_LOCATION`.

If neither `LANGFUSE_HOST` nor `LANGFUSE_BASE_URL` is set, examples default to `http://localhost:3000`.

## Run The Examples

```bash
.venv/bin/python examples/local_fix_bug/run.py
.venv/bin/python examples/local_workflow_coordinator_worker_fix_bug/run.py
.venv/bin/python examples/harbor_fix_bug/run.py
```

Each run logs the run folder and trace URL immediately:

```text
Run started: run-oh-0413-142654-42bc
Run dir:     <repo>/runs/run-oh-0413-142654-42bc
Workspace:   <repo>/runs/run-oh-0413-142654-42bc/workspace
Trace URL:   <langfuse trace URL>
```

Artifacts are written to:

```text
runs/<run_id>/
  workspace/
  run.json
  messages.jsonl
  events.jsonl
  results.json
  metrics.json
```

## Documentation

- [docs/README.md](docs/README.md): documentation map
- [docs/features.md](docs/features.md): fork-specific features
- [docs/architecture.md](docs/architecture.md): control plane, execution plane, and data flow
- [docs/runs.md](docs/runs.md): run IDs, artifacts, Langfuse, and Harbor metadata
- [docs/examples.md](docs/examples.md): current examples and what each one demonstrates
- [examples/README.md](examples/README.md): quick example reference

## Development Checks

```bash
ruff check examples src/openharness tests

uv run --extra dev --extra harbor python -m pytest \
  tests/test_runs/test_local.py \
  tests/test_services/test_runs.py \
  tests/test_harbor/test_harbor_runner.py \
  tests/test_harbor/test_harbor_agent.py \
  tests/test_swarm/test_orchestration.py \
  tests/test_observability/test_langfuse.py \
  tests/test_api/test_gemini_client.py
```

## License

This fork keeps the upstream MIT license. See [LICENSE](LICENSE).
