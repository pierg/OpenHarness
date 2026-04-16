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
- Docker for the Docker sandbox and Harbor examples

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

Upstream CLI setup is still available from this fork:

```bash
oh setup
# On Windows PowerShell, use: openh setup
```

Windows support is native. In PowerShell, use `openh` instead of `oh` because `oh` can resolve to the built-in `Out-Host` alias.

## Run The Examples

```bash
.venv/bin/python examples/local_fix_bug/run.py
.venv/bin/python examples/local_workflow_coordinator_worker_fix_bug/run.py
.venv/bin/python examples/local_docker_sandbox_fix_bug/run.py
.venv/bin/python examples/harbor_fix_bug/run.py
.venv/bin/python examples/harbor_registry_task/run.py
```

## Running Experiments

OpenHarness includes a powerful declarative experiment runner for orchestrating evaluations across Harbor tasks. You define your agents, overrides, and dataset in a YAML file, and the CLI automatically spins up isolated parallel environments to execute them. 

The experiments CLI is fail-soft (if one agent crashes, the others keep going), resumable, and guarantees all paths inside the experiment manifest are portable relative paths, meaning you can easily zip up an experiment folder and analyze it on a different machine.

### Core CLI Commands

We provide convenient global shorthands through `uv run` to manage your experiments:

#### 1. Plan an experiment (`uv run plan`)

Preview the resolved execution plan, agent configs, and merged overrides before starting:

```bash
uv run plan tb2-baseline
```
*(Looks for `experiments/tb2-baseline.yaml` or a direct path).*

#### 2. Execute an experiment (`uv run exec`)

Run the experiment. The runner handles parallel Harbor container provisioning and trial execution automatically. 

```bash
uv run exec tb2-baseline
```

**Common Flags:**
- `--profile`: Applies overriding configurations defined under the `profiles` block in your YAML file. E.g. `--profile demo`. It automatically names the run directory `runs/experiments/<name>-<profile>`.
- `--dry-run`: Creates the full directory structure, resolved manifests, and empty run logs without actually invoking Harbor. Perfect for validating your YAML overrides.
- `--no-resume`: By default, the runner skips trials that already successfully completed. Pass this flag to force a complete re-run from scratch.
- `--fail-fast`: Abort the entire experiment if a single leg fails.
- `--no-results`: Skip generating the `.csv`, `.json`, and `.md` summaries at the end of the run.

#### 3. View status (`uv run status`)

Check the status of an ongoing or completed experiment:

```bash
uv run status tb2-baseline
```
This prints the timestamps and the state (`RUNNING`, `SUCCEEDED`, `FAILED`, etc.) of each leg in the experiment.

#### 4. Export results (`uv run results`)

Generate and print summary metrics across all legs.

```bash
# Print a Markdown summary table
uv run results tb2-baseline --fmt md

# Export the raw JSON result rows
uv run results tb2-baseline --fmt json

# Export as CSV
uv run results tb2-baseline --fmt csv
```

### Experiment Directory Structure

All experiment outputs are strictly contained inside `runs/experiments/<instance-id>/`. Every path that OpenHarness writes into `experiment.json`, `leg.json`, and `rows.{csv,json}` is relative to the experiment root, so you can zip the directory up, move it across machines, and reload it without any rewriting.

```
runs/experiments/tb2-baseline/
├── experiment.json              # Authoritative typed manifest (schema_version=2)
├── config.source.yaml           # Verbatim copy of your input YAML (bytes preserved)
├── config.resolved.yaml         # Final config after applying profiles + overrides
├── logs/
│   └── runner.log               # Runner execution logs
├── results/
│   ├── rows.csv                 # Flat results suitable for Pandas/Excel
│   ├── rows.json                # Raw JSON result rows (with structured errors)
│   └── summary.md               # Markdown table summary
└── legs/
    └── <agent-id>/
        ├── leg.json             # Status, trial aggregate, and result_status
        ├── agent.resolved.yaml  # Concrete AgentConfig sent to Harbor
        └── harbor/
            └── <harbor_run_id>/
                ├── result.json              # Harbor-authored (as-is)
                └── <task>__<trial>/
                    ├── run.json             # OpenHarness manifest, paths relative to trial dir
                    ├── result.json          # Harbor-authored (absolute paths possible)
                    └── result.portable.json # Portable twin, paths relative to experiment_root
```

Key portability contracts:

- `experiment.json` / `leg.json`: every `*_path` and `trial_dir` field is a POSIX path **relative to the experiment root**.
- `run.json`: top-level `paths.anchor: run_dir`; every path is **relative to the trial directory**, with absolute fallbacks only when the referenced path is outside the run dir (e.g. sidecar workspaces).
- `result.portable.json`: re-rooted twin of Harbor's `result.json`; safe to consume from any machine.
- `task_filter.n_tasks` caps the number of tasks per agent **after** `include_tasks` / `exclude_tasks` filtering.

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
