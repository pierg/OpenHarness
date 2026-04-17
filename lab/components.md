# Components

Agent building blocks that have graduated from [`ideas.md`](ideas.md)
via one or more experiments in [`experiments.md`](experiments.md).
Each component has a stable id, a toggle (lives in the agent YAML
under `components:`), a one-line hypothesis, and — once measured —
an impact summary.

## How to use

-   **Graduating an idea into a component**: add a section below
    with the id, scope, hypothesis, and link to the first
    experiment that wired it up. Status starts at `wired`.
-   **Once a paired experiment shows positive impact**: flip
    status to `validated` and fill in the "Impact" line.
-   **If a component becomes always-on in baselines**: flip status
    to `adopted`.
-   **If retired**: flip status to `retired` but keep the id and
    the historical notes — never reuse an id.
-   The list under each agent's `components:` in its YAML must
    match the ids here; enforcement is by convention (fast feedback
    when you grep).

Statuses: `wired` • `validated` • `adopted` • `retired`.

## Template

```markdown
### <component-id>

**Status:** wired
**Scope:** `<files touched>`
**Applies to:** `<agents that activate it>`
**Hypothesis:** one sentence.
**Wired in:** experiments.md#<slug>
**Impact:** _(pending)_
```

---

## Active

_(empty — no components have been validated yet. The baseline
ships with no opt-in components; everything candidate lives in
[`ideas.md`](ideas.md) until an experiment proves it earns its
place here.)_

## Retired

_(empty)_
