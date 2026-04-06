# Workflow System

This document defines the first-class workflow/topology layer added on top of the existing agent and swarm modules.

## Design Rule

Keep behavior in Python and instances in YAML.

- Python defines reusable semantics.
- YAML binds and configures those semantics.
- Control-flow logic lives in code, not in a YAML DSL.

That keeps the system testable, modular, and composable.

## First-Class Components

### Agent Architecture

Single-agent execution template.

Examples:

- `simple`
- `planner_executor`
- `react`
- `reflection`

Defined in Python under `src/openharness/agents/architectures`.

### Agent Spec

Declarative instance of an architecture.

Contains:

- model
- tools
- prompts
- limits
- nested child agents

Defined in YAML and loaded by the existing agent catalog.

### Workflow Topology

Multi-agent orchestration template.

Examples:

- `single`
- `fanout_join`
- `coordinator_worker`

Defined in Python under `src/openharness/workflows/topologies`.

### Workflow Spec

Declarative instance of a topology.

Contains:

- roles
- role-to-agent bindings
- routing policy
- coordination policy
- topology-specific configuration

Defined in YAML under `workflow_configs`.

### Coordination Services

Operational services used by topologies:

- mailbox transport
- leader-brokered permissions
- worker lifecycle
- backend launch
- optional worktree isolation

Implemented by the existing swarm modules and exposed to workflow code through `WorkflowRuntime`.

## Package Layout

The new workflow layer lives under:

- `src/openharness/workflows/specs.py`
- `src/openharness/workflows/catalog.py`
- `src/openharness/workflows/contracts.py`
- `src/openharness/workflows/runtime.py`
- `src/openharness/workflows/engine.py`
- `src/openharness/workflows/topologies/*`

The workflow layer reuses the existing execution plane:

- agent catalog and `AgentFactory`
- `AgentRuntime`
- YAML-backed agent execution
- swarm mailbox and backend registry

## YAML Surface

Workflow YAML is intentionally declarative.

Example:

```yaml
kind: workflow
name: coordinator_worker_bugfix
topology: coordinator_worker
entry_role: coordinator

roles:
  coordinator:
    agent: workflow_coordinator
    mode: inline

  implementer:
    agent: workflow_worker
    mode: spawned
    backend: in_process

coordination:
  messaging:
    transport: mailbox
  permissions:
    mode: leader_brokered
```

This YAML selects:

- which topology runs
- which roles exist
- which agent spec fills each role
- which coordination policies apply

It does not define orchestration semantics itself.

## Execution Flow

The runtime path is:

1. Load a `WorkflowSpec` from the merged catalog
2. Resolve the named topology implementation in Python
3. Build a `WorkflowRuntime`
4. Let the topology orchestrate roles through the runtime
5. Reuse the existing agent and swarm infrastructure underneath

The key boundary is:

- topologies coordinate roles
- the runtime provides message passing, spawn, mailbox reads, and workspace resolution
- agent specs describe what each role actually runs

## Current Built-In Topologies

### `single`

Runs one inline role to completion.

Best for:

- simplest workflows
- single-agent tasks
- migration from existing `Workflow.run(...)`

### `fanout_join`

Runs several inline roles in parallel, then runs a join role with their outputs.

Best for:

- parallel analysis
- implementation plus review
- compare-and-merge reasoning

### `coordinator_worker`

Pre-spawns persistent worker roles, then runs the coordinator inline.

Best for:

- long-lived worker lifecycle
- mailbox-based coordination
- explicit use of `send_message` and `mailbox_read`

## Mailbox Read Path

The workflow layer adds a `mailbox_read` tool so coordinators can consume mailbox state during a run.

That is what turns the mailbox from a hidden transport primitive into a usable coordination surface.

Without that read path, persistent workers are much harder to exploit from an agent loop.

## Authoring Rule

Use this split consistently:

- new reusable orchestration pattern -> Python topology
- new reusable single-agent reasoning pattern -> Python architecture
- task/project-specific instance -> YAML spec
- transport/lifecycle/permissions/worktree behavior -> coordination runtime

That preserves modularity and makes composition explicit.
