# Components registry

This directory is the **single source of truth** for agent
components. Each `<id>.yaml` declares one reusable building block
that can be referenced from an agent YAML via:

```yaml
components: [<id>, <id2>]
```

When `AgentConfig.from_yaml(...)` loads a config, the loader
(`openharness.agents.components.resolve_components`) reads each id
from this directory, validates conflicts, and merges the
component's `wires:` payload into the resolved config.

## File shape

```yaml
id: loop-guard                       # must match the filename
description: One-line summary.
status: proposed                     # proposed | active | retired
version: 0.1.0
applies_to:
  architectures: [simple, planner_executor, react, reflection]
  agents: []                         # optional name allowlist
provides:
  runtime_flags:
    loop_guard.enabled: true
conflicts_with: []                   # other component ids
cost: [no_runtime_overhead]          # free-form tags
evidence:
  ideas: ["loop-guard"]              # ids in lab/ideas.md
  experiments: []                    # slugs in lab/experiments.md
wires:
  tools_add: []                      # extra tool names
  prompts_append: {}                 # name -> text appended to that prompt
  extras: {}                         # passed through to AgentConfig.extras
```

## Lifecycle

1. **Idea** — first written up in `lab/ideas.md`.
2. **Proposed component** — `<id>.yaml` lands here with
   `status: proposed`. The lab daemon may exercise it via paired
   ablations queued in `lab/roadmap.md`.
3. **Validated** — once an accepted experiment records measured
   impact, the entry is bumped to `status: validated` in
   `lab/components.md`.
4. **Retired** — flipped to `status: retired` (kept on disk for
   reproducibility) and moved to `lab/components.md > ## Retired`.

## Validation

`uv run python -m openharness.agents.components --validate`
loads every entry, runs the conflict graph, and exits non-zero on
errors. The pre-commit hook (Phase 3b) wires this in.
