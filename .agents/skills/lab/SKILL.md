---
name: lab
description: >
  Router for the OpenHarness fork's agent-iteration framework. Use
  when the user mentions "lab/", asks how to track an agent idea,
  asks how the experimentation framework works, asks where ideas /
  experiments / components / roadmap are tracked, or you encounter
  the lab/ directory and need to know how to interact with it. Owns
  the structural conventions for the four lab files; all entry shapes
  and edit rules are documented here. Points at five action skills:
  lab-propose-idea, lab-plan-next, lab-run-experiment,
  lab-graduate-component, lab-operator (drives the autonomous
  daemon: start/stop/status/restart/monitor).
---

# Lab — Agent Iteration Framework

The `lab/` folder at the repo root is the audit trail for agent
improvements. The markdowns are deliberately stripped of any
self-documenting prose — no templates, no how-to, no per-file
conventions — so a human reader sees only entries. **All structural
rules live in this skill and the four action skills.** Never put
templates, rules, or how-to back into the lab markdowns.

## Files

| File | Sections | Mutability |
|------|----------|------------|
| `lab/README.md` | Workflow + current state at a glance. Human-facing intro. | Edit when the framework changes shape, or when "current state" needs refreshing (after a sweep lands, a component graduates, the baseline shifts). |
| `lab/ideas.md` | `## Proposed` (themed) → `## Trying` → `## Graduated` → `## Rejected` | Append-only entries. State changes by **moving entries between sections** and appending cross-reference bullets. Never rewrite an existing bullet. |
| `lab/roadmap.md` | `## Up next` → `## Done` | **Mutable.** `## Up next` is reordered freely. Entries move to `## Done` when the experiment runs (regardless of outcome). |
| `lab/experiments.md` | One reverse-chronological list of `## YYYY-MM-DD — <slug>` entries below an optional reset note. | Append-only. A new entry is added at the top in `in-progress` shape (no Results table); the same entry is filled in once the run completes. Never rewritten after that. |
| `lab/components.md` | `## Active` → `## Retired` | Append-only sections. Status lines and impact lines are appended, not replaced. |

Tier-1 changes (bug fixes, small prompt tweaks we'd never revert) go
into [`CHANGELOG.md`](../../../CHANGELOG.md) instead — not the lab.

## When to Use

Use this skill (and pick the right action skill below) when the user:

- says "I have an idea for the agent" / "what if we…" → use
  **`lab-propose-idea`**
- says "queue X for next" / "add X to the roadmap" / "what's the next
  experiment?" / "reorder the queue" → use **`lab-plan-next`**
- says "let's try X" / "run an experiment for X" / "test this on
  tb2-baseline" / "run the next thing on the roadmap" → use
  **`lab-run-experiment`**
- says "promote X" / "graduate X" / "X worked, let's adopt it" →
  use **`lab-graduate-component`**
- says "check the lab" / "is the daemon running" / "what's the
  orchestrator doing" / "start the lab" / "stop the daemon" /
  "restart the lab" / "attach to the lab" / "how's the current
  experiment going" → use **`lab-operator`**
- asks "how is X tracked?" or "is X already wired up?" → answer by
  reading the lab files; no action skill needed.
- asks "what's our experimentation framework?" → explain the
  workflow below in 2–3 sentences and point at `lab/README.md`.

## Workflow

```
ideas.md "Proposed"
   │  promote to the queue (lab-plan-next)
   ▼
roadmap.md "Up next"
   │  run it (lab-run-experiment)
   ▼
experiments.md  (new dated entry, status implicit by Results table)
   │
   ├──► roadmap.md "Done"   (link back to the experiment)
   │
   ├──► ideas.md "Graduated"  ──► components.md   (lab-graduate-component)
   │
   └──► ideas.md "Rejected"                       (no value, with reason)
```

## Conventions Common to All Lab Skills

### Stable kebab-case ids

- Idea ids: `loop-guard`, `planner-rerank`, `episodic-memory`.
- Roadmap slugs: `<idea-id>-<short-context>` or just the idea id if
  unique. E.g. `loop-guard-tb2-paired`,
  `tb2-baseline-full-sweep`. For meta-experiments, set
  `**Idea:**` to `baseline snapshot` or `infrastructure` — these
  don't need a backing idea entry.
- Experiment slugs: `YYYY-MM-DD — <roadmap-slug>` (the date
  prefixes the slug; the slug itself usually matches the roadmap
  entry it came from).
- Component ids: same kebab-case as the idea they came from.
- **Once an id appears in any lab file, never reuse it for
  something else.** Check collisions across all four lab files
  before introducing a new id:

  ```bash
  rg -n "^####? " lab/ideas.md lab/components.md
  rg -n "^### " lab/roadmap.md lab/experiments.md
  ```

### Themes in `ideas.md`

`## Proposed` is grouped under four `### <Theme>` subsections, in
this order:

| Theme | Use for… |
|-------|----------|
| **Architecture** | new agent shapes, planner/executor/critic compositions, reranking, parallel sampling. |
| **Runtime** | mid-loop mechanisms (loop-guard, context compaction, budget tweaks, retry policies, tool-output summarisation). |
| **Tools** | new tools wired into agents, or ablations of existing tool wiring. |
| **Memory** | cross-task or cross-run state (skill notes, episodic stores, retrieval). |

Pick the theme by where the change actually lives. If none fit
cleanly, add a new `### <NewTheme>` heading under `## Proposed` —
but err toward the existing four.

### Entry shapes (canonical)

Each action skill owns the detailed shape of the file it edits. The
condensed forms:

**`ideas.md` entry** (under `## Proposed > <Theme>`):

```markdown
#### <kebab-id>

-   **Motivation:** one sentence on why we'd want this.
-   **Sketch:** one or two sentences on what the change actually is.
```

When the entry moves to a new section, **append cross-ref bullets**
at the end (do not edit the existing two):

```markdown
#### <kebab-id>

-   **Motivation:** ...
-   **Sketch:** ...
-   **Trying in:** [<roadmap-slug>](roadmap.md#<roadmap-slug>)
-   **Graduated as:** [`<component-id>`](components.md#<component-id>)
-   **Rejected:** YYYY-MM-DD — <one-line reason>; see [<experiment-slug>](experiments.md#YYYY-MM-DD--<slug>)
```

Only the bullets relevant to the entry's current section need to be
present.

**`roadmap.md` entry** (under `## Up next` or `## Done`):

```markdown
### <slug>

-   **Idea:** [`<idea-id>`](ideas.md#<idea-id>)   _(or: baseline snapshot / infrastructure)_
-   **Hypothesis:** one sentence on what we expect to learn.
-   **Plan:** one paragraph — agents, slice, what varies vs the current baseline.
-   **Depends on:** `<other-slug>`   _(omit if nothing)_
-   **Cost:** ~$X, ~Y hours wall-clock   _(omit if smoke / unknown)_
```

When moved to `## Done`, **append two bullets**:

```markdown
-   **Ran:** [<experiment-slug>](experiments.md#YYYY-MM-DD--<slug>)
-   **Outcome:** one sentence — headline pass rates + decision (graduate / iterate / reject).
```

**`experiments.md` entry** (newest at top):

```markdown
## YYYY-MM-DD — <slug>

-   **Hypothesis:** one sentence.
-   **Variant:** what differs vs the current baseline   _(or: "leg A vs leg B" for paired runs)_
-   **Run:** [`runs/experiments/<instance-id>/`](../runs/experiments/<instance-id>/)

### Results

| Leg | Trials | Passed | Errored | Pass rate | Total tokens | Cost (USD) |
|-----|-------:|-------:|--------:|----------:|-------------:|-----------:|
| ... |        |        |         |           |              |            |

### Notes

-   3–6 short bullets of qualitative observations.

### Decision

graduate `<id>`   _(or: iterate — see follow-up `<slug>` / reject)_
```

**Status is implicit:** an entry without a Results table (or with an
empty table) is in-progress; an entry with a populated table is
complete. Never add a `Status:` field.

**`components.md` entry** (under `## Active`):

```markdown
### <component-id>

-   **Scope:** `<files where the component is implemented or wired>`
-   **Applies to:** `<agents that activate it>`
-   **Hypothesis:** one sentence on what the component is for.
-   **Wired in:** [<experiment-slug>](experiments.md#YYYY-MM-DD--<slug>)
-   **Impact:** one or two sentences citing headline numbers.
```

Lifecycle in `components.md` is `wired` → `validated` → `adopted` →
`retired`. The status is encoded by which section the entry sits in
(`## Active` vs `## Retired`) plus an optional **Status:** bullet
when finer detail matters. Multiple supporting experiments append
new `**Wired in:**` bullets — don't replace.

### Mutability rules (one paragraph)

- `ideas.md`, `experiments.md`, `components.md` are **append-only**.
  To change state on an idea, move the entry between sections and
  append cross-ref bullets; never rewrite the existing bullets.
  Experiments are never rewritten once results land.
- `roadmap.md` is **mutable**. Reorder `## Up next` freely. Move
  entries to `## Done` when their experiment runs.

### What never goes into the lab markdowns

These rules exist so the lab markdowns stay clean for human review:

- No `## How to use` / `## Template` / `## Conventions` sections.
- No `Status:` field on any entry — state lives in section
  membership.
- No "what each file does" prose at the top of `ideas.md`,
  `roadmap.md`, or `experiments.md` — the README does that once.
- No `## Current baseline` table in `experiments.md` — the README
  carries it.
- No "Expected experiment" field on `ideas.md` entries — that
  detail moves into the roadmap entry the moment the idea is
  queued.
- No `Held constant:` field on `experiments.md` entries — the
  baseline is defined once in `README.md`; the entry's `Variant:`
  states what differs.

If you're tempted to add explanatory prose to a markdown, add it to
the relevant skill instead.

## Deterministic mutations go through `uv run lab`

All file mutations on `lab/*.md` (and on the lab DuckDB) flow through
the `uv run lab` Typer CLI defined in
[`src/openharness/lab/cli.py`](../../../src/openharness/lab/cli.py).
The five `lab*` skills do the *judgment* (which idea, which decision,
how to phrase results); the CLI does the *editing* (validating section
membership, preserving entry shape, never silently corrupting). This
means humans, Cursor, codex, and the Phase 2 orchestrator daemon all
mutate the lab via the same code path.

Quick reference (each `lab*` skill below covers its own subset):

```bash
# ideas.md
uv run lab idea append <id> --theme Runtime --motivation "..." --sketch "..."
uv run lab idea move <id> trying --cross-ref "**Trying in:** [<slug>](roadmap.md#<slug>)"
uv run lab append-followup-idea <id> --motivation "..." --sketch "..." --source "cross-experiment-critic@<date>"

# roadmap.md
uv run lab roadmap add <slug> --idea <id> --hypothesis "..." --plan "..." [--depends-on <slug>] [--cost "..."]
uv run lab roadmap done <slug> --ran "[<exp-slug>](experiments.md#<date>--<slug>)" --outcome "..."

# experiments.md
uv run lab experiments stub <slug> --hypothesis "..." --variant "..."
uv run lab experiments fill <slug> --run-path runs/experiments/<id> \
                                   --from-summary runs/experiments/<id>/results/summary.md \
                                   --note "..." --note "..." --decision "..."

# DB I/O (called by critic skills + the orchestrator)
uv run lab init                       # create DB + apply migrations
uv run lab ingest runs/experiments/<id>
uv run lab info
uv run lab query "SELECT ..."         # read-only ad-hoc SQL
uv run lab query-trials --instance <id> --leg <leg> [--needs-critique]
uv run lab insert-critique <trial_id> --json -
uv run lab insert-comparison <instance> <task> --json -
uv run lab insert-task-features <task_checksum> --json -
uv run lab upsert-component-perf <component> <task_cluster> --json -
uv run lab dashboard                  # Streamlit, opens DB read-only
```

The CLI refuses unsafe edits (unknown idea id, duplicate slug,
malformed kebab-case, missing experiment entry) — surface the error
to the user instead of trying to "fix" the file by hand.

## Other Useful Reads

- [`lab/README.md`](../../../lab/README.md) — the human-facing
  workflow + current state snapshot.
- [`docs/runs.md`](../../../docs/runs.md) — how `runs/experiments/`
  is laid out (each `lab/experiments.md` entry should link a
  matching directory there).
- [`experiments/tb2-baseline.yaml`](../../../experiments/tb2-baseline.yaml)
  — the canonical experiment spec used by `uv run exec`.
- [`scripts/exp/README.md`](../../../scripts/exp/README.md) — a
  `tmux`-backed background job manager. One of two equally-valid
  ways to background an experiment, alongside the agent's own
  `Shell` background mode. `lab-run-experiment` documents when to
  pick which (short-form: tmux when the human wants
  attach/list/stop or when the run should outlive the agent loop;
  `Shell` background otherwise). Either way, never block on `uv run
  exec` in the foreground.
