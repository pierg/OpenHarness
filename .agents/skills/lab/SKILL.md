---
name: lab
description: >
  Router for the OpenHarness fork's autonomous-research lab. Use
  when the user mentions "lab/", asks how the experimentation
  framework works, asks where ideas / experiments / components /
  roadmap are tracked, asks about the trunk / branches / verdicts,
  or you encounter the lab/ directory and need to know how to
  interact with it. Owns the structural conventions for the four
  lab files (the "tree-vs-journal" model) and the file-ownership
  contract; all entry shapes and edit rules are documented here.
  Points at action skills: lab-propose-idea, lab-plan-next,
  lab-design-variant, lab-implement-variant, lab-finalize-pr,
  lab-graduate-component, lab-reflect-and-plan, lab-operator
  (drives the autonomous daemon's 6-phase pipeline end-to-end).
---

# Lab — Autonomous Agent-Research Framework

The `lab/` folder at the repo root is a small, high-signal audit
surface in front of an autonomous research loop. **Three artifacts
have completely different lifetimes:**

| Artifact | What it is | Lifetime |
|----------|------------|----------|
| `lab/configs.md` | **The configuration tree** — trunk + branches + rejected + proposed *agent configs* (composed harnesses we've actually run). The current best knowledge. | Persistent state, mutated by `lab tree apply`. |
| `lab/components.md` | **The catalog of atoms** — every architectural / runtime / tools / prompt / model building block we've experimented with, plus its current status (proposed / experimental / branch / validated / rejected / superseded). The vocabulary we compose from. | Persistent state, mutated as a side-effect of `lab tree apply` (and via `lab components` for explicit edits). |
| `lab/experiments.md` | **The journal** — append-only log of dated events. Each entry records exactly one experiment that proposed one diff to the tree. | Append-only, never edited. |

Configs.md and components.md describe the same evidence at two
levels: configs.md records *which compositions* are validated
together; components.md records *which atoms* are validated, where
they're used, and why they were rejected. Verdicts are computed
from agent-config legs (configs.md), and component statuses are
auto-bumped from those verdicts (forward-only — never demoted).

Two human-curated planning surfaces feed the loop:

| File | Contents |
|------|----------|
| `lab/ideas.md` | Themed backlog. Humans own `## Proposed / Trying / Graduated / Rejected`; daemon owns `## Auto-proposed`. |
| `lab/roadmap.md` | Priority queue. Humans own `## Up next`; daemon writes follow-ups under `## Up next > ### Suggested` (humans promote to the main queue). |

The lab markdowns are deliberately stripped of any self-documenting
prose — no templates, no how-to, no per-file conventions. **All
structural rules live in this skill and the action skills below.**
Never put templates, rules, or how-to back into the lab markdowns.

## Mental model (one picture)

```
ideas.md ───► roadmap.md ───► [orchestrator daemon: 6-phase pipeline]
                                  │
                                  │  Phase 0  preflight     (deterministic; clean repo + worktree)
                                  │  Phase 1  design        (codex skill: lab-design-variant, read-only)
                                  │  Phase 2  implement     (codex skill: lab-implement-variant, worktree-write)
                                  │  Phase 3  run           (deterministic; uv run exec, --root parent repo)
                                  │  Phase 4  critique      (deterministic + trial/experiment-critic spawns)
                                  │  Phase 5  finalize      (codex skill: lab-finalize-pr → push branch + open PR)
                                  ▼
                                runs/experiments/<id>/
                                          │
                                          ▼
                                experiment-critic + tree_ops.evaluate
                                          │
                                          ▼
                                  TreeDiff
                          (graduate | add_branch | reject | no_op)
                                          │
                          ┌───────────────┴───────────────┐
                          ▼                               ▼
              configs.md (the configuration tree)        experiments.md (the journal)
              ── Trunk (1)                                                   ── ## YYYY-MM-DD — <slug>
              ── Branches (n; specializations)                                  ── ### Aggregate
              ── Rejected (n; with reason)                                      ── ### Mutation impact
              ── Proposed (untested)                                            ── ### Failure modes
                          │                                                     ── ### Tree effect      ◄── one per entry
                          │ side-effect (forward-only bump)                     ── ### Linked follow-ups
                          ▼
              components.md (the catalog of atoms)
              ── Architecture | Runtime | Tools | Prompt | Model
              ── statuses: proposed → experimental → branch → validated;  rejected | superseded
```

Key invariants:

1.  **Configs = state. Journal = log. Components = derived view.**
    Experiments don't branch; each proposes one TreeDiff over agent
    configs that the tree may or may not absorb. Component statuses
    are auto-bumped from those verdicts (never demoted by automation).
2.  **One trunk at a time.**
    `src/openharness/agents/configs/trunk.yaml` is the source of
    truth for the current best agent. `lab/configs.md > ## Trunk`
    points at the same agent id.
3.  **Asymmetric autonomy.** Daemon auto-applies AddBranch / Reject
    / NoOp. Trunk swaps require `uv run lab graduate confirm <slug>`
    (or the `lab-graduate-component` skill in Cursor).
4.  **Default experiment = paired ablation** (trunk leg + 1 mutation
    leg). `type: broad-sweep` is opt-in for re-baselining.
5.  **Composition is descriptive today, prescriptive later.** Agent
    YAMLs are still the unit of execution; components.md just
    *describes* which atoms each composes. A future runtime-
    composition path can add a real composer when we want it without
    touching the verdict machinery.

## When to Use

Use this skill (and pick the right action skill below) when the user:

-   says "I have an idea for the agent" / "what if we…" → use
    **`lab-propose-idea`**.
-   says "queue X for next" / "add X to the roadmap" / "what's the
    next experiment?" / "reorder the queue" / "promote a Suggested
    entry" → use **`lab-plan-next`**.
-   says "let's try X" / "run the next thing on the roadmap" / "run
    the lab" / "drive the next experiment end-to-end" → use
    **`lab-operator`** (which drives the orchestrator daemon's full
    6-phase pipeline). For *one specific phase* by hand:
    -   designing a variant (read-only) → **`lab-design-variant`**
    -   implementing it on a worktree → **`lab-implement-variant`**
    -   pushing the branch / opening the PR after a verdict →
        **`lab-finalize-pr`**
    The deterministic phases (preflight, run, critique) have no
    skill — call `uv run lab preflight run <slug>` or `uv run exec
    <slug>` directly.
-   says "promote X" / "graduate X" / "X worked, let's adopt it" /
    "confirm the staged trunk swap" → use **`lab-graduate-component`**.
-   says "what should we run next?" / "reflect on the latest results"
    → use **`lab-reflect-and-plan`**.
-   says "check the lab" / "is the daemon running" / "what's the
    orchestrator doing" / "start the lab" / "stop the daemon" /
    "restart the lab" / "how's the current experiment going" → use
    **`lab-operator`**.
-   asks "what's our experimentation framework?" → explain the
    tree-vs-journal model in 2–3 sentences and point at
    `lab/README.md`.

## Files at a glance

| File | Sections | Mutability |
|------|----------|------------|
| `lab/README.md` | Mental model + workflow. Human-facing intro. | Edit when the framework changes shape. |
| `lab/OPERATIONS.md` | Operating guide for the daemon: tick, file-ownership matrix, codex auth, model profiles, troubleshooting. | Edit when the daemon's behaviour or skill profiles change. |
| `lab/ideas.md` | `## Proposed` (themed) → `## Trying` → `## Graduated` → `## Rejected` → `## Auto-proposed`. | Append-only. State changes by **moving entries between sections** + appending cross-ref bullets. Never rewrite an existing bullet. |
| `lab/roadmap.md` | `## Up next` (with `### Suggested` substream) → `## Done`. | Mutable. `## Up next` is reordered freely; `### Suggested` is daemon-only; entries move to `## Done` when their experiment runs. |
| `lab/experiments.md` | One reverse-chronological list of `## YYYY-MM-DD — <slug>` entries; each has 5 `### …` subsections. | Append-only. New entries appended at the top with empty subsections; subsections are filled in by deterministic helpers and never manually rewritten. |
| `lab/configs.md` | `## Trunk` → `## Branches` → `## Rejected` → `## Proposed`. The tree of composed agent configs. | Trunk swaps via `lab graduate confirm` only. Branches/Rejected/Proposed mutated by `lab tree apply`. Rare manual edits OK. |
| `lab/components.md` | `## Architecture` → `## Runtime` → `## Tools` → `## Prompt` → `## Model`. One row per atom. | Auto-bumped (forward only) by `lab tree apply`. Manual edits via `lab components upsert` / `lab components set-status`. |

Tier-1 changes (bug fixes, small prompt tweaks we'd never revert) go
into `CHANGELOG.md` instead — not the lab.

## File ownership (the autonomy contract)

The complete matrix lives in `lab/OPERATIONS.md`. Most-relevant rows:

| File / section | Human writes | Daemon writes |
|----------------|--------------|---------------|
| `lab/ideas.md > ## Proposed / Trying / Graduated / Rejected` | yes | no |
| `lab/ideas.md > ## Auto-proposed` | read-only | `cross-experiment-critic`, `lab-reflect-and-plan` |
| `lab/roadmap.md > ## Up next` (main queue) | yes | `## Done` move only |
| `lab/roadmap.md > ## Up next > ### Suggested` | promote to main queue | `lab-reflect-and-plan` |
| `lab/experiments.md` (whole entry) | no | orchestrator daemon (Phase 3 appends header + Branch / Run bullets via `lab experiments append-entry` + `set-branch` + `set-run-path`) + `experiments synthesize` (Phase 4 sections) + `tree apply` (Phase 4 ### Tree effect) + `lab-finalize-pr` (Phase 5 updates Branch bullet with PR URL) |
| `lab/configs.md > ## Branches / ## Rejected / ## Proposed` | rare | `lab tree apply` |
| `lab/configs.md > ## Trunk` | via `lab trunk set` or `lab graduate confirm` | only via `graduate confirm` |
| `lab/components.md` (any kind) | `lab components upsert` / `lab components set-status` | `lab tree apply` (forward-only status bump as a verdict side-effect) |
| `src/openharness/agents/configs/trunk.yaml` | rare | only via `lab graduate confirm` |

If you find yourself wanting to write to a daemon-only zone by hand,
stop — there's a CLI for it (see below).

## Conventions Common to All Lab Skills

### Stable kebab-case ids

-   Idea ids: `loop-guard`, `planner-rerank`, `episodic-memory`.
-   Roadmap slugs: `<idea-id>-<short-context>` or just the idea id if
    unique. E.g. `loop-guard-tb2-paired`, `tb2-baseline-full-sweep`.
-   Experiment slugs: same as the roadmap entry that spawned them
    (the date prefixes the journal entry header, not the slug).
-   Component / branch / agent ids: kebab-case; same id across the
    tree and the agent YAMLs.
-   **Once an id appears in any lab file, never reuse it for
    something else.** Check collisions across all files:

    ```bash
    rg -n "^####? " lab/ideas.md
    rg -n "^### " lab/roadmap.md
    rg -n "^## " lab/experiments.md
    rg -n "^\| \`" lab/configs.md lab/components.md
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
at the end (do not edit the existing two).

**`roadmap.md` entry** (under `## Up next` or `## Done`):

```markdown
### <slug>

-   **Idea:** [`<idea-id>`](ideas.md#<idea-id>)   _(or: baseline snapshot / infrastructure)_
-   **Hypothesis:** one sentence on what we expect to learn.
-   **Plan:** one paragraph — agents, slice, what varies vs the current trunk.
-   **Depends on:** `<other-slug>`   _(omit if nothing)_
-   **Cost:** ~$X, ~Y hours wall-clock   _(omit if smoke / unknown)_
```

When moved to `## Done`, **append two bullets**: `**Ran:**` and
`**Outcome:**`.

**`roadmap.md > ## Up next > ### Suggested` entry** (daemon-only):

```markdown
#### <slug>

-   **Hypothesis:** one sentence.
-   **Source:** `<source-tag>` (e.g. `lab-reflect-and-plan@2026-04-18`).
-   **Cost:** ~$X   _(optional)_
```

Humans review and run `uv run lab roadmap promote <slug>` to move
into the main queue.

**`experiments.md` entry** (newest at top):

```markdown
## YYYY-MM-DD — <slug>

-   **Type:** paired-ablation | broad-sweep | smoke
-   **Trunk at run-time:** [`trunk@<sha>`](../src/openharness/agents/configs/trunk.yaml)
-   **Mutation:** <one-liner>          (omit for broad-sweep)
-   **Hypothesis:** one sentence.
-   **Branch:** `lab/<slug>` _(set by daemon Phase 0; rewritten to_
    _`[lab/<slug>](<pr_url>)` by `lab-finalize-pr` in Phase 5,_
    _or to `lab/<slug> — not opened (<reason>)` if the verdict_
    _was Reject / NoOp)._
-   **Run:** [`runs/experiments/<id>`](../runs/experiments/<id>)
    _(set by daemon Phase 3 once the run starts; placeholder until then)._

### Aggregate           ← `lab experiments synthesize`
### Mutation impact     ← `lab experiments synthesize`
### Failure modes       ← `lab experiments synthesize` (from experiment-critic.json)
### Tree effect         ← `lab tree apply` (single source of truth for the verdict)
### Linked follow-ups   ← `lab-reflect-and-plan` (writes the cross-refs back)
```

The `### Tree effect` block always names: the verdict (Graduate /
AddBranch / Reject / NoOp), the target id, the rationale, the
delta numbers, and the application status (auto / staged / human).

**`configs.md`** (the configuration tree; not an "entry" file — four sections):

```markdown
## Trunk
-   **Agent:** [`trunk`](../src/openharness/agents/configs/trunk.yaml) (alias of `<id>`)
-   **Why:** ...
-   **Anchored by:** [`<journal-entry>`](experiments.md#YYYY-MM-DD--<slug>)

## Branches
| ID | Mutation vs trunk | Use-when predicate | Last verified |

## Rejected
| ID | Reason | Evidence |

## Proposed
| ID | Sketch | Linked idea |
```

**`components.md`** (the catalog of atoms; one table per kind):

```markdown
## Architecture | ## Runtime | ## Tools | ## Prompt | ## Model

| ID | Status | Description | Used by | Evidence |
```

Status lattice: `proposed → experimental → branch → validated`
(forward-only via `lab tree apply` and `lab components upsert`),
plus terminal `rejected` / `superseded` reachable only via
`lab components set-status`.

### Mutability rules (one paragraph)

-   `ideas.md`, `experiments.md` are **append-only**. To change an
    idea's state, move the entry between sections and append
    cross-ref bullets. Journal entries are filled in once and
    never rewritten.
-   `roadmap.md`, `configs.md`, `components.md` are **mutable** but
    only via the right tool — `lab roadmap *` for roadmap,
    `lab tree *` / `lab trunk *` / `lab graduate confirm` for the
    configuration tree, `lab components *` for the catalog.

### What never goes into the lab markdowns

These rules exist so the lab markdowns stay clean for human review:

-   No `## How to use` / `## Template` / `## Conventions` sections.
-   No "what each file is for" prose at the top of any lab file —
    `lab/README.md` carries it (and only it).
-   No `Status:` field on any entry — state lives in section
    membership.
-   No `## Current baseline` table in `experiments.md` — the trunk
    section of `configs.md` carries that.

If you're tempted to add explanatory prose to a lab markdown, add
it to the relevant SKILL.md or to `lab/README.md` instead.

## Deterministic mutations go through `uv run lab`

All mutations on `lab/*.md`, `trunk.yaml`, and the lab DuckDB flow
through the `uv run lab` Typer CLI defined in
[`src/openharness/lab/cli.py`](../../../src/openharness/lab/cli.py).
The action skills do the *judgment* (which idea, which decision,
how to phrase results); the CLI does the *editing* (validating
section membership, preserving entry shape, never silently
corrupting). This means humans, Cursor, codex, and the orchestrator
daemon all mutate the lab via the same code path.

Quick reference (each action skill below covers its own subset):

```bash
# Configuration tree (configs.md)
uv run lab tree show [--json]
uv run lab tree apply <slug> [--instance <id>] [--dry-run]
uv run lab trunk show
uv run lab trunk set <agent-id> --reason "..."
uv run lab graduate confirm <slug> --applied-by human:<name> [--reason "..."]

# Components catalog (components.md)
uv run lab components show [--kind Architecture|Runtime|Tools|Prompt|Model] [--json]
uv run lab components upsert <id> --kind <Kind> [--description "..."] [--status <s>] [--used-by "a,b"] [--evidence "..."]
uv run lab components set-status <id> <status> [--evidence "..."]   # humans only — bypasses bump lattice

# Journal (experiments.md) — header is appended by lab-run-experiment;
# all sections except `### Tree effect` are filled by `synthesize`;
# the verdict block is filled by `tree apply`.
uv run lab experiments synthesize <slug>          # narrative sections
uv run lab tree apply <slug>                      # ### Tree effect

# Roadmap
uv run lab roadmap add <slug> --idea <id> --hypothesis "..." --plan "..." [--depends-on <slug>] [--cost "..."]
uv run lab roadmap done <slug> --ran "[<exp-slug>](experiments.md#<date>--<slug>)" --outcome "..."
uv run lab roadmap suggest <slug> --hypothesis "..." --source "..."
uv run lab roadmap promote <slug>                  # human only

# Ideas
uv run lab idea append <id> --theme Runtime --motivation "..." --sketch "..."
uv run lab idea move <id> trying --cross-ref "..."
uv run lab idea auto-propose <id> --motivation "..." --sketch "..." --source "..."

# DB I/O (DB is a derived cache; never the source of truth)
uv run lab init                                    # create DB + apply migrations
uv run lab ingest runs/experiments/<id>            # run dir → DB
uv run lab ingest-critiques [<run_dir>...]         # critic files → DB
uv run lab info
uv run lab query "SELECT ..."

# Critic outputs are FILES; critics write them via these helpers.
uv run lab write-trial-critique     <trial_dir>            --json -
uv run lab write-comparison         <run_dir> <task_name>  --json -
uv run lab write-experiment-critique <run_dir>             --json -
uv run lab write-task-features      <task_checksum>        --json -
uv run lab write-cross-experiment   <spawn_id>             --json -

uv run lab dashboard                               # Streamlit, opens DB read-only
```

The CLI refuses unsafe edits (unknown slug, duplicate id, missing
journal entry, kind mismatch on `graduate confirm`) — surface the
error to the user instead of trying to "fix" the file by hand.

## Other Useful Reads

-   [`lab/README.md`](../../../lab/README.md) — the human-facing
    mental model.
-   [`lab/OPERATIONS.md`](../../../lab/OPERATIONS.md) — the
    operating guide for the daemon (tick, file-ownership matrix,
    codex auth, model profiles, troubleshooting).
-   [`docs/runs.md`](../../../docs/runs.md) — how
    `runs/experiments/` is laid out (each journal entry should link
    a matching directory there).
-   [`scripts/exp/README.md`](../../../scripts/exp/README.md) — the
    `tmux`-backed background job manager. The daemon prefers a
    detached `subprocess.Popen` (`start_new_session=True`) so a
    daemon restart doesn't kill the in-flight experiment; the tmux
    path is for hand-driven runs.
