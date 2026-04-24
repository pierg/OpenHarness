---
name: lab-graduate-component
description: >
  Legacy escape hatch for historical staged-graduate rows. The normal
  daemon flow no longer requires a human graduate-confirm step because
  finalize now merges accepted experiment outcomes back to `main`
  directly. Use this only when repairing or closing out experiments
  created under the older staged-graduate workflow.
---

# Lab — Graduate Component (Legacy)

This skill is no longer part of the normal autonomous loop.

Use it only when:

- a historical experiment already produced a staged `graduate`
  `tree_diffs` row under the old workflow
- the user explicitly wants to repair that legacy state

For new experiments, do not use this skill. The accepted path is:

`critique` writes the verdict on the experiment branch and
`lab-finalize-pr` merges the experiment outcome back to `main`.

If you do have to use this skill, inspect the historical row carefully
and prefer the deterministic CLI:

```bash
uv run lab graduate confirm <slug> --applied-by human:<name>
```

Treat it as migration support, not normal operation.
