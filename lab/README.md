# Lab

Audit trail for agent improvements. Three append-only markdown files,
no validation code:

| File | Purpose |
|------|---------|
| [`ideas.md`](ideas.md) | Backlog of things to try. Cheap to add. |
| [`experiments.md`](experiments.md) | Append-only log of concrete runs (newest at top). |
| [`components.md`](components.md) | Ideas that graduated into building blocks, with measured impact. |

Tier-1 changes (bug fixes, small prompt tweaks we'd never revert) go
into [`../CHANGELOG.md`](../CHANGELOG.md), not here.

## Lifecycle

```
ideas.md "Proposed"
   │  user says "let's try it"
   ▼
ideas.md "Trying"  ─────►  experiments.md "<date> — <slug>"
   │  experiment shows positive impact            │
   ▼                                              │
ideas.md "Graduated"  ◄───────────────────────────┘
   │
   ▼
components.md "Active"  (id appears in agent YAML "components:")
```

## Agent skills

The flow is automated by four skills under
[`../.agents/skills/`](../.agents/skills):

| Skill | Use when… |
|-------|-----------|
| [`lab`](../.agents/skills/lab/SKILL.md) | router / overview |
| [`lab-propose-idea`](../.agents/skills/lab-propose-idea/SKILL.md) | "I have an idea…" |
| [`lab-run-experiment`](../.agents/skills/lab-run-experiment/SKILL.md) | "let's try X" / "run an A/B for X" |
| [`lab-graduate-component`](../.agents/skills/lab-graduate-component/SKILL.md) | "X worked, promote it" |

## Conventions

- Append-only. Never rewrite an old entry; supersede with a new one
  and link back.
- Stable kebab-case ids. Once an id appears in any lab file,
  never reuse it.
- Promotion requires citation. An idea graduates into a component
  only if at least one entry in `experiments.md` justifies it.
- Component ids surface in agent YAMLs as `components: [...]`.
  Plain metadata, no validation — keep it accurate by convention.
