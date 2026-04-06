# Fork Integration

This document explains the modules and features that remain specific to this fork, what came from upstream, and how the two sides integrate today.

The short version:

- upstream now provides most of the control plane
- this fork adds execution-time composition, native Gemini and Vertex support, Harbor integration points, and Langfuse observability
- the merged codebase is upstream-first for orchestration, and fork-first for compositional execution

## Integration Strategy

The merge was not handled as two equal systems glued together. The current shape is:

- upstream owns routing, coordination, auth, interactive runtime, swarm transport, mailbox, permission sync, worktree support, and most of the user-facing control plane
- the fork owns YAML composition, reusable agent architectures, Harbor-related workspace integration, and the repo-local observability layer

This keeps the control plane close to upstream while preserving the fork’s differentiated execution features.

## What Comes From Upstream

These areas are primarily upstream-owned in the merged tree:

- coordinator mode and the agent catalog under `src/openharness/coordinator`
- swarm runtime, backends, mailbox, permission sync, and worktrees under `src/openharness/swarm`
- `agent` and `send_message` tool flow under `src/openharness/tools/agent_tool.py` and `src/openharness/tools/send_message_tool.py`
- provider registry, auth manager, and setup/auth flows under `src/openharness/api/registry.py` and `src/openharness/auth`
- interactive runtime and TUI/backend wiring under `src/openharness/ui`
- scheduling, cron, and session backend plumbing under `src/openharness/services`

These pieces are the base the fork now builds on.

## What Remains Fork-Specific

### 1. YAML Agent System

Fork-owned modules:

- `src/openharness/agents/config.py`
- `src/openharness/agents/catalog.py`
- `src/openharness/agents/factory.py`
- `src/openharness/agents/architectures`
- `src/openharness/runtime/workflow.py`
- `src/openharness/runtime/session.py`

What this adds:

- declarative YAML agent configs
- reusable composition patterns
- nested subagents
- structured outputs for planner and critic style flows
- project-local and user-local agent catalogs

How it integrates with upstream:

- YAML configs are projected into upstream-visible `AgentDefinition` values in `src/openharness/coordinator/agent_definitions.py`
- `agent_tool` can spawn a YAML-backed teammate by setting `runner="yaml_workflow"`
- the swarm runner dispatches into `Workflow` in `src/openharness/swarm/runner.py`

This is the most important retained fork feature. It is where the fork adds execution-time value rather than just configuration or transport value.

### 2. Native Gemini And Vertex Support

Fork-owned modules:

- `src/openharness/api/gemini_client.py`
- `src/openharness/api/factory.py`
- parts of `src/openharness/api/provider.py`
- settings fields in `src/openharness/config/settings.py`

What this adds:

- a native Gemini streaming client
- support for API-key Gemini and Vertex AI project/location credentials
- provider-specific message and tool-result translation for Gemini

How it integrates with upstream:

- upstream’s provider registry and auth/config stack remain the main control-plane entrypoints
- `AgentRuntime` resolves provider/model selection once, and then the fork’s Gemini client satisfies the same streaming protocol as other providers
- coordinator and swarm code do not need special Gemini logic; they just pass model names through

This means the fork adds provider breadth without forking the coordinator or swarm stack.

### 3. Harbor Workspace And Harbor Agent Bridge

Fork-owned modules:

- `src/openharness/workspace/harbor.py`
- `src/openharness/harbor/agent.py`
- `src/openharness/harbor/runner.py`
- `src/openharness/harbor/specs.py`
- `examples/harbor_fix_bug/run.py`

What this adds:

- Harbor-backed workspace access
- Harbor job specs and runner utilities
- an OpenHarness Harbor agent adapter

How it integrates with upstream:

- Harbor uses the same YAML catalog and the same `AgentRuntime` contracts
- Harbor paths can use the same observability layer
- `runner="harbor"` exists in the shared contracts

Current gap:

- Harbor is not yet wired as a fully implemented swarm teammate runner in `src/openharness/swarm/runner.py`

So Harbor is integrated at the execution and workspace layer, but not yet fully integrated into upstream swarm spawning.

### 4. Langfuse Observability

Fork-owned modules:

- `src/openharness/observability/langfuse.py`
- `src/openharness/observability/__init__.py`

What this adds:

- one repo-local tracing facade that can degrade to `NullTraceObserver`
- Langfuse integration for local, runtime, swarm, and Harbor paths
- optional live flushing for long-running local examples
- a minimal span model tailored to this repo

How it integrates with upstream:

- tracing wraps upstream coordinator/swarm boundaries without coupling the rest of the codebase directly to the Langfuse SDK
- the query loop, runtime, swarm runners, and Harbor bridge all use the same observer contract

Current trace model:

```text
session
└── agent:<name>
    ├── model
    ├── tool:<name>
    └── ...
```

For stateful swarm teammates:

```text
session
└── turn
    └── agent:<name>
        ├── model
        └── tool:<name>
```

This was intentionally simplified from the earlier deeper hierarchy.

### 5. Run Artifacts And Local Run Helpers

Fork-owned module:

- `src/openharness/services/runs.py`

What this adds:

- a local run-artifact layer separate from upstream session/coordinator state

How it integrates with upstream:

- it sits alongside the upstream session backend rather than replacing it
- the query/runtime stack can emit outputs that later run-artifact tooling can consume

This area is lower-impact than the YAML, Gemini, Harbor, and observability work, but it is still fork-owned.

## How The Pieces Fit Together

The current architecture is easiest to understand in four layers.

### Layer 1: Discovery And Routing

Owned mostly by upstream:

- coordinator mode
- `AgentDefinition`
- `agent_tool`
- `send_message`
- backend registry

Fork contribution:

- YAML configs project into `AgentDefinition`, so compositional agents become first-class coordinator targets

### Layer 2: Transport And Teammate Lifecycle

Owned by upstream:

- swarm backends
- mailbox
- permission sync
- worktree support

Fork contribution:

- `yaml_workflow` runner plugs into the same teammate contract

### Layer 3: Execution

Owned mostly by the fork:

- YAML catalog
- `AgentFactory`
- `Workflow`
- `AgentRuntime`
- compositional architectures

Upstream contribution:

- shared tool ecosystem
- provider/auth/config base
- coordinator-compatible tool entrypoints

### Layer 4: Observability

Owned by the fork:

- `TraceObserver`
- Langfuse integration
- live local tracing example

Integrated across all of the above.

## Examples By Feature Area

Use these examples to understand the merged system in practice:

- `examples/local_fix_bug/run.py`
  - simplest local runtime path
  - no swarm
  - good for understanding `AgentRuntime` and query flow

- `examples/local_langfuse_live_single_agent/run.py`
  - same basic local path
  - adds live Langfuse visibility
  - good for understanding the minimal trace model

- `examples/local_coordinator_swarm_fix_bug/run.py`
  - exercises upstream coordinator/swarm style spawning
  - resolves a projected YAML definition
  - routes through `TeammateSpawnConfig`
  - executes a YAML-backed agent through the swarm runtime

- `examples/harbor_fix_bug/run.py`
  - demonstrates the Harbor integration layer
  - useful for understanding the Harbor-specific fork surface

## Current Boundaries And Open Gaps

The current merged system is coherent, but there are still explicit boundaries:

- Harbor is present in contracts, but not fully implemented as a swarm teammate runner yet
- YAML composition is integrated into upstream control-plane discovery, but upstream coordinator code does not need to understand YAML internals
- full provider history is still resent every turn because provider APIs are stateless; observability now hides that by recording only the latest turn delta

These are good boundaries, not accidental ones. They keep upstream orchestration stable and keep fork-specific complexity concentrated in the execution layer.

## Practical Summary

If you want a quick mental model, use this:

- upstream gives us a stronger operating system for agents
- the fork gives us a stronger runtime for composing agents
- `AgentDefinition` is the routing contract
- `AgentConfig` is the composition contract
- `runner` is the handoff point
- Langfuse is the common visibility layer across both sides
