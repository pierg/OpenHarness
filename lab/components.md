# Components

Components are reusable agent building blocks declared one-per-file
in [`components/`](../components/) at the repo root. Each
`components/<id>.yaml` is the source of truth for that component's
schema, conflicts, evidence, and runtime wiring; this file is the
human-readable index of which ones are real today.

Reference one or more from any agent YAML:

```yaml
components: [loop-guard]
```

The agent loader resolves each id, runs conflict checks, and
merges the component's `wires:` payload into the agent before
pydantic validation. See [`components/README.md`](../components/README.md)
for the field-by-field schema and lifecycle.

Validate the full registry with:

```bash
uv run python -m openharness.agents.components --validate
```

## Active

_(none — no proposed component has been graduated yet.)_

## Proposed

| ID | Description | Evidence |
| --- | --- | --- |
| [`loop-guard`](../components/loop-guard.yaml) | Detects no-progress turns and steers toward recovery before the turn budget runs out. | idea: [`loop-guard`](ideas.md#loop-guard); experiments: pending `loop-guard-tb2-paired` |

## Retired

_(none)_
