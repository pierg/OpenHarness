# Examples

The examples are deliberately small. They all solve the same bug so the execution path is the only real difference.

Task:

```text
sum_evens.py should sum even numbers.
It starts by summing odd numbers.
The correct result for [1, 2, 3, 4, 5, 6] is 12.
```

## Prerequisites

```bash
source .venv/bin/activate

export GOOGLE_API_KEY=...
export LANGFUSE_PUBLIC_KEY=...
export LANGFUSE_SECRET_KEY=...
export LANGFUSE_BASE_URL=http://localhost:3000
```

Docker is required for the Docker sandbox and Harbor examples.

## Shared Files

Shared task and helper code:

```text
examples/_shared/bugfix_task.py
```

Shared YAML configs:

```text
examples/_shared/agent_configs/bugfix_agent.yaml
examples/_shared/agent_configs/workflow_coordinator.yaml
examples/_shared/agent_configs/workflow_worker.yaml
```

All example configs pin:

```text
gemini-3.1-flash-lite-preview
```

## 1. Local Bug Fix

Run:

```bash
.venv/bin/python examples/local_fix_bug/run.py
```

Shows:

- inline Python task definition
- YAML agent config copied into the workspace catalog
- `run_local_agent(...)`
- generated run ID
- local workspace editing
- run artifacts
- live Langfuse trace URL

Use this as the baseline example.

## 2. Workflow Coordinator And Workers

Run:

```bash
.venv/bin/python examples/local_workflow_coordinator_worker_fix_bug/run.py
```

Shows:

- `TeamOrchestrator`
- persistent workers
- mailboxes
- inline coordinator workflow
- shared run ID
- shared trace observer
- shared run artifacts

Use this to inspect multi-agent behavior without Harbor.

## 3. Docker Sandbox Bug Fix

Run:

```bash
.venv/bin/python examples/local_docker_sandbox_fix_bug/run.py
```

Shows:

- same YAML config as the local example
- OpenHarness Docker sandbox backend
- bash tool execution routed through the sandbox container
- normal local run artifacts under `runs/<run_id>/`
- distinction between runtime sandboxing and Harbor evaluation containers

If Docker is not running, this example should exit with a clear prerequisite message.

Use this to inspect tool sandboxing without introducing Harbor.

## 4. Harbor Bug Fix

Run:

```bash
.venv/bin/python examples/harbor_fix_bug/run.py
```

Shows:

- Harbor task source
- Docker execution
- OpenHarness Harbor agent adapter
- same YAML config as the local example
- host-side run artifacts
- Harbor aggregate and trial results
- trace URL propagation across the Harbor boundary

If Docker is not running, this example should exit with a clear prerequisite message.

## 5. Harbor Registry Task

Run:

```bash
.venv/bin/python examples/harbor_registry_task/run.py
```

By default this evaluates the YAML-defined `harbor_registry_agent` on the existing
Harbor registry task `cookbook/hello-world`.

Shows:

- Harbor registry task source
- Docker execution
- OpenHarness Harbor agent adapter
- YAML config passed into Harbor through `agent_config_yaml`
- host-side run artifacts
- Harbor aggregate and trial results
- trace URL propagation across the Harbor boundary

If Docker is not running, this example should exit with a clear prerequisite message.

## Smoke Test

Run all examples:

```bash
.venv/bin/python examples/local_fix_bug/run.py
.venv/bin/python examples/local_workflow_coordinator_worker_fix_bug/run.py
.venv/bin/python examples/local_docker_sandbox_fix_bug/run.py
.venv/bin/python examples/harbor_fix_bug/run.py
.venv/bin/python examples/harbor_registry_task/run.py
```

Expected:

- each started run prints run ID, run directory, workspace, and trace URL before the agent works
- local example passes
- workflow example passes
- Docker sandbox example passes when Docker is running
- Docker sandbox example exits cleanly when Docker is not running
- Harbor example passes when Docker is running
- Harbor example exits cleanly when Docker is not running
- Harbor registry example passes when Docker is running
- Harbor registry example exits cleanly when Docker is not running

## Adding Examples

Add an example only when it shows a new end-to-end feature.

Good reasons:

- new task source
- new execution substrate
- new coordination pattern
- new artifact behavior
- new observability behavior

Bad reasons:

- only a different prompt
- only a different model
- only another agent name
- a demo that does not create useful artifacts

When adding an example:

- use the shared task unless the task itself is the feature
- generate run ID at runtime
- write under `runs/<run_id>/`
- print trace URL at startup
- use Gemini unless the example is specifically about provider selection
- add a short entry to this document
