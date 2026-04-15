# Fork Features

This fork adds execution and evaluation features on top of upstream OpenHarness. Upstream owns the CLI harness, coordinator, permissions, tools, skills, plugins, and swarm transport. This fork owns the YAML runtime, run artifacts, Langfuse integration, Google Gemini and Vertex AI support, and Harbor bridge.

## 1. YAML Agent Configs

Agents can be defined declaratively in YAML.

Example:

```yaml
name: bugfix_agent
architecture: simple
model: gemini-2.5-flash
max_turns: 8
max_tokens: 8192

tools:
  - bash
  - read_file
  - edit_file
  - grep

prompts:
  system: |
    {{ openharness_system_context }}

    Make the smallest correct code change and verify it.
  user: |
    {{ instruction }}
```

Why it matters:

- agents become inspectable and reusable
- local, swarm, and Harbor paths can use the same config
- prompt/runtime behavior is no longer hidden inside a launcher script

Key files:

```text
src/openharness/agents/config.py
src/openharness/agents/catalog.py
src/openharness/agents/factory.py
examples/_shared/agent_configs/
```

## 2. Composable Agent Runtime

YAML configs are executed by `Workflow` and `AgentRuntime`.

Supported architecture names include:

- `simple`
- `planner_executor`
- `reflection`
- `react`

Why it matters:

- architecture choice lives in YAML
- nested agents can share one runtime contract
- the coordinator can spawn YAML-backed agents without knowing their internals

Key files:

```text
src/openharness/runtime/workflow.py
src/openharness/runtime/session.py
src/openharness/agents/architectures/
```

## 3. Google Gemini And Vertex AI Support

Gemini models run through the native Google Gen AI SDK while keeping the same streaming client protocol as the rest of the engine.

Supported credential paths:

```text
GOOGLE_API_KEY or GEMINI_API_KEY
VERTEX_PROJECT or GOOGLE_CLOUD_PROJECT
VERTEX_LOCATION or GOOGLE_CLOUD_LOCATION
```

Why it matters:

- the engine can run Gemini models without provider-specific query-loop code
- Google AI Studio and Vertex AI both use the same OpenHarness client surface
- examples can pin `gemini-2.5-flash` without relying on hidden defaults

Key files:

```text
src/openharness/api/gemini_client.py
src/openharness/api/factory.py
src/openharness/api/provider.py
src/openharness/config/settings.py
tests/test_api/test_gemini_client.py
```

## 4. Canonical Run Artifacts

Every launched run owns one folder:

```text
runs/<run_id>/
```

Run IDs are generated at runtime:

```text
run-oh-MMDD-HHMMSS-xxxx
```

Why it matters:

- runs are easy to inspect after completion
- examples and programmatic launchers share one layout
- `run_id` becomes the shared identity for artifacts and traces

Key files:

```text
src/openharness/services/runs.py
src/openharness/runs/context.py
src/openharness/runs/local.py
src/openharness/runs/harbor.py
```

## 5. Langfuse Observability

Examples require Langfuse and log the trace URL at startup.

Example startup output:

```text
Run started: run-oh-0413-142654-42bc
Run dir:     /path/to/repo/runs/run-oh-0413-142654-42bc
Workspace:   /path/to/repo/runs/run-oh-0413-142654-42bc/workspace
Trace URL:   http://localhost:3000/project/.../traces/...
```

Why it matters:

- traces are available while the agent is still running
- missing tracing setup fails fast in examples
- `run.json` carries both `trace_id` and `trace_url`

Key files:

```text
src/openharness/observability/langfuse.py
src/openharness/runs/context.py
```

## 6. Harbor Integration

The Harbor adapter runs an OpenHarness YAML agent inside a Harbor task.

Why it matters:

- the same YAML agent can solve local and Harbor tasks
- Harbor scores are linked back to host-side OpenHarness artifacts
- trace identity survives the Harbor boundary

Key files:

```text
src/openharness/harbor/agent.py
src/openharness/harbor/runner.py
src/openharness/harbor/specs.py
src/openharness/workspace/harbor.py
examples/harbor_fix_bug/run.py
examples/harbor_registry_task/run.py
```

## 7. Coordinator And Workflow Bridge

YAML configs can be projected into upstream-visible `AgentDefinition` values and run through the `yaml_workflow` runner.

Why it matters:

- upstream coordinator tools can route to fork-defined agents
- fork-defined workers can use upstream swarm transport and mailboxes
- one run ID and trace can cover a multi-agent workflow

Key files:

```text
src/openharness/coordinator/agent_definitions.py
src/openharness/swarm/runner.py
src/openharness/swarm/orchestration.py
examples/local_workflow_coordinator_worker_fix_bug/run.py
```
