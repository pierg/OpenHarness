# Fork Integration

This document explains the modules and features that remain specific to this fork, what came from upstream, and how the two sides integrate today.

The short version:

- upstream now provides most of the control plane
- this fork adds execution-time composition, native Gemini and Vertex support, Harbor integration points, and Langfuse observability
- the merged codebase is upstream-first for orchestration, and fork-first for compositional execution

## Current Sync Status

As of 2026-04-07:

- the active integration branch is `codex/merge-upstream-main`
- it already merged upstream through commit `aa2e024` (`docs(readme): add chinese translation`) in merge commit `c9e7df1`
- after fetching the current upstream head `69c85e4` (`fix(ui): avoid blocking paste and permission responses in terminal`), this branch is `32` commits behind upstream and `46` commits ahead

That matters because the hard part is no longer "import upstream's big architecture". That already happened in `c9e7df1`. The remaining upstream delta is mostly hardening and provider/runtime fixes, not another full control-plane rewrite.

## Upstream Features To Adopt, Not Rebuild

The current upstream-only delta is concentrated in a few areas:

- auth and config hardening
  - profile-scoped credential precedence for custom compatible profiles
  - `OPENAI_BASE_URL` and related env precedence fixes
  - better provider inference and newer token-field compatibility
- provider breadth and compatibility
  - native Moonshot/Kimi support
  - follow-up fixes around built-in base URLs for saved profiles
- runtime and UI robustness
  - terminal paste / permission-response unblocking
  - EIO handling and other startup/runtime polish
- security and reliability
  - `web_fetch` URL validation + untrusted-content banner
  - MCP disconnected-server handling
  - sensitive path protection in `PermissionChecker`
- `ohmo` follow-up fixes
  - gateway and session-storage changes layered on top of the earlier upstream import

These are exactly the kinds of changes the fork should pull from upstream rather than re-implement locally.

## Fork Features Still Worth Preserving

The branch-local work that is still genuinely fork-specific is:

- YAML/compositional agent runtime
- Harbor workspace + Harbor runner integration
- Langfuse tracing / observability facade
- native Gemini / Vertex client support
- run-artifact helpers and fork-specific examples
- the newer swarm orchestration layer and workflow-to-swarm unification work on this branch

The goal is to keep these execution-time extensions while continuing to consume upstream control-plane fixes.

## Merge Probe Result

A clean merge probe of `upstream/main` into `codex/merge-upstream-main` was run in a temporary worktree on 2026-04-07.

Result:

- the merge is feasible
- only `4` files hit textual conflicts
- the rest auto-merged

Conflict files:

- `src/openharness/config/settings.py`
- `src/openharness/engine/query.py`
- `src/openharness/ui/runtime.py`
- `tests/test_config/test_settings.py`

Auto-merged but high-review files:

- `src/openharness/auth/external.py`
- `src/openharness/auth/manager.py`
- `src/openharness/api/openai_client.py`
- `src/openharness/cli.py`
- `src/openharness/tools/web_fetch_tool.py`
- `src/openharness/mcp/client.py`
- `src/openharness/permissions/checker.py`
- `src/openharness/ui/backend_host.py`
- `ohmo/gateway/runtime.py`
- `ohmo/gateway/service.py`
- `ohmo/session_storage.py`

This is an important signal: the branch is not in a state where a rebase/cherry-pick rewrite is necessary. A normal upstream merge with deliberate conflict resolution is the right move.

## Integration Strategy

The merge was not handled as two equal systems glued together. The current shape is:

- upstream owns routing, coordination, auth, interactive runtime, swarm transport, mailbox, permission sync, worktree support, and most of the user-facing control plane
- the fork owns YAML composition, reusable agent architectures, Harbor-related workspace integration, and the repo-local observability layer

This keeps the control plane close to upstream while preserving the fork’s differentiated execution features.

## Practical Merge Playbook

Use this sequence whenever syncing this fork forward:

1. Save local work first.
   - Do not merge on top of an uncommitted working tree.
   - This repo currently has local modifications on `codex/merge-upstream-main`, so those should be committed or stashed before doing the real merge.

2. Merge into the integration branch, not directly into `main`.
   - Start from `codex/merge-upstream-main` or a fresh branch cut from it.
   - This branch already contains the upstream-first architectural import point (`c9e7df1`), so it is the correct continuation point.

3. Use an upstream-first merge policy for control-plane files.
   - Prefer upstream behavior in auth, UI runtime, MCP, `ohmo`, permission enforcement, and web fetching.
   - Re-apply fork hooks only where they extend behavior instead of replacing upstream machinery.

4. Use a fork-first merge policy for execution extensions.
   - Preserve YAML agent catalogs, Harbor integration, Langfuse observers, Gemini/Vertex support, and swarm orchestration additions.

5. Resolve the four known conflicts with explicit intent.
   - `src/openharness/config/settings.py`
     - keep the fork's profile-slot / multi-provider logic
     - also keep upstream's auth precedence fixes
   - `src/openharness/engine/query.py`
     - keep fork tracing and hook integration
     - also carry upstream logging and permission-flow improvements
   - `src/openharness/ui/runtime.py`
     - keep the fork's `create_api_client()` abstraction and tracing hooks
     - also preserve upstream runtime UX fixes instead of re-expanding provider-specific logic inline
   - `tests/test_config/test_settings.py`
     - keep both the fork credential-profile coverage and the upstream env-precedence coverage

6. Review the auto-merged hotspots, especially auth/runtime/security files.
   - These merged cleanly textually, but they sit on behaviorally sensitive paths.

7. Run targeted validation before promoting the branch.
   - `tests/test_config/test_settings.py`
   - `tests/test_auth/test_external.py`
   - `tests/test_api/test_openai_client.py`
   - `tests/test_tools/test_web_fetch_tool.py`
   - `tests/test_mcp/test_client_errors.py`
   - `tests/test_ohmo/test_gateway.py`
   - `tests/test_ohmo/test_ohmo_session_storage.py`
   - `tests/test_permissions/test_checker.py`
   - fork-specific suites:
     - `tests/test_agents/test_agents.py`
     - `tests/test_harbor/test_harbor_runner.py`
     - `tests/test_observability/test_langfuse.py`
     - `tests/test_swarm/test_orchestration.py`

8. Only after the integration branch is green should it replace or merge back into fork `main`.

The key discipline is simple:

- consume upstream fixes in the control plane
- keep fork code concentrated in execution-time extensions
- avoid parallel local rewrites of auth, runtime, provider plumbing, and security paths when upstream already owns them

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
- `examples/README.md`

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
- fail-fast required tracing for examples via `OPENHARNESS_LANGFUSE_REQUIRED=1`
- trace URLs persisted in run manifests when Langfuse is active
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
  - simplest local run path
  - loads the shared YAML bug-fix agent through the workspace catalog
  - defines the task inline in Python
  - uses `run_local_agent(...)`
  - writes canonical artifacts under `runs/<generated-run-id>/`
  - prints the local Langfuse trace URL

- `examples/local_workflow_coordinator_worker_fix_bug/run.py`
  - exercises `TeamOrchestrator`
  - spawns persistent workers
  - coordinates through mailboxes
  - runs an inline coordinator workflow
  - propagates one generated run ID to the coordinator and workers
  - prints the local Langfuse trace URL

- `examples/harbor_fix_bug/run.py`
  - demonstrates the Harbor integration layer
  - uses the same shared YAML bug-fix agent as the local example
  - receives the task from Harbor's task directory instead of inline Python
  - links host-side run artifacts to Harbor's external `result.json`
  - passes local Langfuse credentials into the Harbor agent
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
