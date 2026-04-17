# Fork Development Notes

This repository is a personal fork of [HKUDS/OpenHarness](https://github.com/HKUDS/OpenHarness). It is not the primary place for upstream OpenHarness contributions.

This file is a maintenance checklist for changes made on this fork.

## Fork Scope

This fork currently focuses on:

- YAML-configured agents
- Google Gemini and Vertex AI support
- runtime-generated run IDs
- canonical `runs/<run_id>/` artifacts
- local Langfuse traces
- Harbor task execution
- maintained end-to-end examples

Changes outside this scope should usually stay small, or go upstream if they belong to the base OpenHarness CLI/runtime.

## Local Setup

```bash
git clone <this-fork-url>
cd OpenHarness_fork
uv sync --extra dev --extra harbor
source .venv/bin/activate
```

For the Harbor example, Docker must be running.

For the example suite, configure Google AI Studio or Vertex AI credentials and local Langfuse:

```bash
export GOOGLE_API_KEY=...
export LANGFUSE_PUBLIC_KEY=...
export LANGFUSE_SECRET_KEY=...
export LANGFUSE_BASE_URL=http://localhost:3000
```

For Vertex AI, set `VERTEX_PROJECT` or `GOOGLE_CLOUD_PROJECT`, and optionally `VERTEX_LOCATION` or `GOOGLE_CLOUD_LOCATION`.

## Required Checks

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

If the terminal frontend changes, also run:

```bash
cd frontend/terminal
npm ci
npx tsc --noEmit
```

## Example Policy

The maintained examples are documented in [`docs/examples.md`](docs/examples.md).

Keep examples only when they show distinct end-to-end behavior. Do not add examples that only change the prompt, model, or agent name.

Every maintained example should:

- generate its run ID at runtime
- write artifacts under `runs/<run_id>/`
- log the run directory and Langfuse trace URL at startup
- use YAML agent configs
- use Google Gemini unless the example is specifically about provider selection

## Docs Policy

Keep [README.md](README.md) concise. Put feature details in [`docs/`](docs/README.md).

Update docs when behavior changes for:

- run IDs or artifact layout
- Langfuse tracing
- Harbor execution
- YAML agent configs
- examples
- Gemini or Vertex AI support

Add a short entry under `Unreleased` in [`CHANGELOG.md`](CHANGELOG.md) for user-visible changes.
