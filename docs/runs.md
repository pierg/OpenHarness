# Runs And Observability

Runs are the main reproducibility unit in this fork.

Every local, workflow, or Harbor launch should create one run folder and one trace identity at the beginning of execution.

## Run ID Format

Generated run IDs use:

```text
run-oh-MMDD-HHMMSS-xxxx
```

Example:

```text
run-oh-0413-142654-42bc
```

The timestamp is local time. The suffix is four lowercase hex characters.

Implementation:

```text
src/openharness/services/runs.py
```

## Artifact Layout

Standard layout:

```text
runs/<run_id>/
  workspace/
  run.json
  messages.jsonl
  events.jsonl
  results.json
  metrics.json
```

Files:

- `workspace/`: editable task workspace for the run
- `run.json`: manifest, status, artifact paths, trace identity, metadata
- `messages.jsonl`: persisted conversation messages
- `events.jsonl`: stream and tool execution events
- `results.json`: run output summary
- `metrics.json`: usage and timing metrics

## Startup Logging

Runs should log the run identity immediately:

```text
Run started: <run_id>
Run dir:     <repo>/runs/<run_id>
Workspace:   <repo>/runs/<run_id>/workspace
Trace URL:   <langfuse trace URL>
```

This lets a developer open the trace while the agent is still working.

The shared helper is:

```text
RunContext.log_start()
```

## RunContext

`RunContext` owns:

- `run_id`
- run directory
- artifact paths
- workspace path
- status
- trace ID
- trace URL
- manifest writes
- result and metric writes

Key module:

```text
src/openharness/runs/context.py
```

## Langfuse

Langfuse is a base dependency in this fork.

Examples configure:

```text
OPENHARNESS_LANGFUSE_REQUIRED=1
OPENHARNESS_LANGFUSE_FLUSH_MODE=live
```

Required mode means:

- missing SDK fails fast
- missing keys fail fast
- failed auth check fails fast
- missing trace URL fails fast

Environment:

```bash
export LANGFUSE_PUBLIC_KEY=...
export LANGFUSE_SECRET_KEY=...
export LANGFUSE_BASE_URL=http://localhost:3000
```

`LANGFUSE_HOST` can be used instead of `LANGFUSE_BASE_URL`.

If both are missing, examples default to:

```text
http://localhost:3000
```

## Trace Identity

Langfuse trace IDs are deterministic from the run ID.

That means:

- the run folder name is the trace seed
- `run.json` records the final `trace_id`
- `run.json` records the Langfuse `trace_url`
- `results.json` may also record trace identity for Harbor runs

The helper that precomputes trace identity is:

```text
resolve_langfuse_trace_identity(...)
```

Key module:

```text
src/openharness/observability/langfuse.py
```

## Harbor Metadata

Harbor writes multiple result files:

```text
runs/<run_id>/harbor_jobs/<run_id>/result.json
runs/<run_id>/harbor_jobs/<run_id>/<trial>/result.json
```

The top-level result is aggregate stats.

The trial result contains agent metadata:

```text
agent_result.metadata.trace_id
agent_result.metadata.trace_url
```

The host runner scans trial result files after Harbor completes and copies trace metadata back into host-side `run.json` and `results.json`.

## What A Healthy Run Should Have

For local and workflow examples:

- run ID logged at startup
- trace URL logged at startup
- workspace under `runs/<run_id>/workspace`
- `run.json` with `trace_url`
- non-empty `messages.jsonl`
- non-empty `events.jsonl`
- `results.json`
- `metrics.json`

For Harbor:

- same host-side files
- `harbor_jobs/<run_id>/result.json`
- at least one per-trial result
- trace URL in host `run.json`
- trace URL in host `results.json`
