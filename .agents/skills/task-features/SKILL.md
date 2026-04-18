---
name: task-features
description: >
  Extract a one-shot set of semantic features for a single task
  (`task_checksum`) — category, required tools, env complexity,
  output shape, keyword tags — and persist to the lab DB. Use when
  the orchestrator says "we have trials for a task whose features
  we've never extracted", when `cross-experiment-critic` reports
  missing features, or when the human pastes a task instruction and
  asks "what kind of task is this". Cached forever per
  `task_checksum`. Companion skills: trial-critic, experiment-critic,
  cross-experiment-critic.
---

# Task Features

Extract a small, durable feature record for one task so downstream
analysis can cluster and reason at the right granularity. This is
the only place `task_features` rows are produced; once a row exists
for a `task_checksum` it is never re-extracted unless the human
explicitly forces it.

You are an autonomous codex agent. Your job is to read the task's
inputs (instruction, Dockerfile, setup files) once, extract a small
JSON record, and persist it via `uv run lab insert-task-features`.

## When to Use

- Orchestrator pre-flight ("we have trials but no features for these
  N checksums").
- `cross-experiment-critic` reports missing features.
- Human asks "what kind of task is `<task_name>`".

Do **not** use this skill:

- For trials. Per-trial analysis is `trial-critic`.
- To re-extract an already-cached `task_checksum` unless the human
  asks. The cache is the whole point.

## Inputs

```bash
codex exec --skill task-features <task_checksum>
# or, more usefully when invoked by a human, on a task name:
codex exec --skill task-features --task-name <task_name>
```

If only a `task_name` is given, look up *one* representative trial
and use its `task_checksum`:

```bash
uv run lab query "
  SELECT task_checksum, trial_dir, task_path
  FROM trials
  WHERE task_name = '<name>' AND task_checksum IS NOT NULL
  LIMIT 1"
```

Refuse if no trial exists yet — the task hasn't been run, so
there's nothing canonical to ground the features in.

## What to read

Inside any one trial directory for the task:

1. The first user message of `agent/trajectory.json` — that is the
   task instruction, byte-for-byte.
2. `task/Dockerfile` (or `task_path/Dockerfile`) if present — tells
   you the required runtime, base image, system packages.
3. `task/run-tests.sh` / `task/tests/` — tells you the verifier's
   contract (what file/output it expects).
4. Any setup scripts under `task/setup/` — tells you how the env is
   prepared and what tools the agent will likely need.

If you only have the task instruction (no Dockerfile, no tests),
that's still enough — record what you can and leave structural
features null.

## Output schema

```bash
uv run lab insert-task-features <task_checksum> \
  --extracted-by "<your model>" --json - <<'JSON'
{
  "task_name":      "cancel-async-tasks",
  "category":       "python_async",
  "required_tools": ["python", "pytest", "asyncio"],
  "env_complexity": "low|medium|high",
  "output_shape":   "modifies_existing_file|creates_new_file|writes_stdout|installs_package|configures_env",
  "keywords":       ["async", "cancellation", "pytest", "single_file"],
  "extra":          {"dockerfile_present": true, "needs_network": false, "n_setup_files": 2}
}
JSON
```

Field rules:

- `category` is a coarse cluster. Use existing values if any
  apply (`python_async`, `python_data`, `c_build`, `bash_pipeline`,
  `git_workflow`, `web_scrape`, `dockerfile_authoring`, …). Add a
  new value only when none fit; keep them snake_case.
- `env_complexity`:
  - `low` — single language, no system packages beyond defaults.
  - `medium` — one or two system packages; standard build chain.
  - `high` — custom toolchain, GPU, network-dependent install.
- `required_tools` is a flat list of the *minimum* tools the
  agent needs. Don't list everything available; list what the
  task forces.
- `output_shape` is the verifier's contract, not the agent's
  freeform output.
- `keywords` are short snake_case tags; they are what
  `cross-experiment-critic` clusters on. Be consistent across
  tasks (prefer reusing tags you've used before — query
  ```bash
  uv run lab query "SELECT DISTINCT unnest(json_extract_string(keywords)) AS kw
                    FROM task_features ORDER BY kw"
  ```
  to see the existing vocabulary).
- `extra` is a free-form JSON object for anything else worth
  recording — booleans, counts, file lists.

## Refusal cases

- No trial in the DB carries this `task_checksum` (run
  `uv run lab ingest <run_dir>` first).
- The trial directory is missing both `agent/trajectory.json` and
  any explicit task instruction file (you have nothing to extract
  from).

## Example

```
$ codex exec --skill task-features f3a8b9...

# Reads trial dir, finds:
#   instruction: "Implement run_tasks(...) handling cancellation cleanup."
#   Dockerfile: python:3.13-slim, no system packages.
#   tests:      pytest -q tests/test_run.py
# Persists:
#   {"category": "python_async", "env_complexity": "low",
#    "required_tools": ["python", "pytest"], "output_shape": "modifies_existing_file",
#    "keywords": ["async", "cancellation", "single_file"]}
OK; cached features for f3a8b9... (cancel-async-tasks).
```

## Constraints

- One row per `task_checksum`. The CLI upserts, but the orchestrator
  only invokes you when no row exists, so the common case is insert.
- Do not edit any markdown under `lab/`.
- Do not modify `runs/experiments/*` artifacts.
- Do not run trials.
