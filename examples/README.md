# OpenHarness Examples

For the maintained examples guide, see [`../docs/examples.md`](../docs/examples.md).

The examples use one small bug-fix task so the control flow is easy to compare:
`sum_evens.py` should print `12`, but starts by summing odd numbers.

Every example starts a new run, generates the run ID at the beginning of that
run, and prints the generated run ID with the artifact paths. The run owns the
editable workspace and artifacts under `runs/<generated-run-id>/`.
Generated run IDs use `run-oh-MMDD-HHMMSS-xxxx`, where `xxxx` is a four-character
random suffix.

Examples require a live local Langfuse project. If `LANGFUSE_HOST` or
`LANGFUSE_BASE_URL` is not set, examples default to `http://localhost:3000`.
Set `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` from that local project
before running them. Each successful run prints the Langfuse trace URL and
persists it in `run.json`.
The run ID, run folder, workspace, and trace URL are also logged as soon as the
run starts so the trace can be opened while the agent is still working.

The local bug-fix examples use the same YAML-configured agent:
`examples/_shared/agent_configs/bugfix_agent.yaml`. The difference is the
execution path: `local_fix_bug` runs directly, `local_docker_sandbox_fix_bug`
runs local tools through the OpenHarness Docker sandbox, and `harbor_fix_bug`
passes a Harbor task directory. `harbor_registry_task` uses
`examples/_shared/agent_configs/harbor_registry_agent.yaml` to run an existing
task from the Harbor registry.

All example YAML configs pin `gemini-3.1-flash-lite-preview` directly.

## What Each Example Shows

1. `local_fix_bug/run.py`
   - YAML agent config loaded through the project catalog
   - the high-level `run_local_agent(...)` API
   - manually defined inline task
   - generated run ID
   - workspace rooted at `runs/<generated-run-id>/workspace`
   - canonical artifacts: `run.json`, `messages.jsonl`, `events.jsonl`, `results.json`, `metrics.json`

2. `local_workflow_coordinator_worker_fix_bug/run.py`
   - `TeamOrchestrator`
   - persistent workers
   - mailbox coordination
   - one run ID propagated through the coordinator and workers

3. `local_docker_sandbox_fix_bug/run.py`
   - the same YAML agent config as the local example
   - OpenHarness Docker sandbox backend for bash tool execution
   - sandbox session started before the agent run
   - generated run ID and canonical run artifacts
   - exits cleanly with a prerequisite message when Docker is not running

4. `harbor_fix_bug/run.py`
   - the same YAML agent config passed into Harbor
   - Harbor Docker execution
   - OpenHarness Harbor agent adapter
   - Harbor task source instead of an inline task
   - copied Harbor task workspace under `runs/<generated-run-id>/workspace`
   - host-side run artifacts linked to Harbor's external `result.json`
   - exits cleanly with a prerequisite message when Docker is not running

5. `harbor_registry_task/run.py`
   - YAML-defined OpenHarness agent passed into Harbor
   - existing Harbor registry task source, defaulting to `cookbook/hello-world`
   - no copied local task directory
   - host-side run artifacts linked to Harbor's external `result.json`
   - exits cleanly with a prerequisite message when Docker is not running

Langfuse is not a separate example because it does not change the task flow. The
same runs use the generated run ID as the trace identity.

## Run Them

```bash
uv run python examples/local_fix_bug/run.py
uv run python examples/local_workflow_coordinator_worker_fix_bug/run.py
uv run python examples/local_docker_sandbox_fix_bug/run.py
uv run python examples/harbor_fix_bug/run.py
uv run python examples/harbor_registry_task/run.py
```

After a local run, inspect:

```text
./runs/<generated-run-id>/workspace/
./runs/<generated-run-id>/run.json
./runs/<generated-run-id>/messages.jsonl
./runs/<generated-run-id>/events.jsonl
./runs/<generated-run-id>/results.json
./runs/<generated-run-id>/metrics.json
```
