---
name: lab-reflect-and-plan
description: >
  Tree-aware planner. After an experiment lands and `tree apply`
  has computed its verdict, this skill reads the current
  configuration tree (`uv run lab tree show --json`), the latest
  journal entries (`lab/experiments.md`), and the cross-experiment
  view (`runs/lab/cross_experiment/*.json`), then proposes the
  most-informative next experiments — writing concrete entries
  under `roadmap.md > ## Up next > ### Suggested` and abstract
  ones under `ideas.md > ## Auto-proposed`. Use when the user
  says "what should we run next?", "reflect on the latest
  results", "extend the plan", "what's missing?", or when the
  daemon invokes it after each tick.  Companion skills:
  experiment-critic (must have run first), cross-experiment-critic,
  lab-plan-next.
---

# Lab — Reflect and Plan

`tree_ops.evaluate` answers "what was the verdict on this one
experiment?". `cross-experiment-critic` answers "across all
experiments, which components help on which clusters?". This skill
answers the third question: **"given the current tree and the
latest evidence, what's the most-informative experiment to run
next?"**

You are an autonomous codex agent. You are **read-only against
the lab markdowns** except for two narrow write zones:

-   `lab/roadmap.md > ## Up next > ### Suggested` — concrete
    follow-up roadmap entries.
-   `lab/ideas.md > ## Auto-proposed` — abstract follow-up ideas
    (when not yet ready to commit to a roadmap entry).

Both zones are reviewed and promoted by humans (`lab-plan-next`).
The daemon never moves your suggestions into the main `## Up
next` queue on its own.

## When to Use

-   The orchestrator invokes this skill once per tick, immediately
    after `lab tree apply`, before `lab-plan-next`.
-   Human asks "what should we run next?", "what does the tree
    suggest?", "extend the roadmap with the latest learnings",
    "is the current branch list well-covered?".
-   Human just confirmed a trunk swap (`lab graduate confirm
    <slug>`) and wants to know which pending ablations should be
    re-run against the new trunk.

Do **not** use this skill:

-   Before `tree apply` has run on the latest experiment. The
    `### Tree effect` block is your most-recent signal; without it
    you'd be planning on stale evidence.
-   To touch the human-curated `## Up next` queue, `## Proposed`
    ideas, or `## Done`. Only the two write zones above are yours.
-   To rewrite `lab/configs.md` or `lab/components.md`. The
    configuration tree is mutated only by `lab tree apply` and
    `lab graduate confirm`; the catalog is mutated only by
    `lab tree apply` (auto bumps) and `lab components` (explicit).

## Inputs

```bash
codex exec --skill lab-reflect-and-plan [--instance <instance_id>]
```

`--instance` (optional) anchors the reflection on one specific
experiment. Without it, you reflect on the most recent N (default
3) journal entries.

## What to do

### 1. Read the current state

```bash
uv run lab tree show --json
uv run lab trunk show
uv run lab components show --json
uv run lab query "
  SELECT slug, kind, target_id, applied, applied_by, applied_at
  FROM tree_diffs ORDER BY applied_at DESC NULLS LAST LIMIT 10"
uv run lab query "
  SELECT instance_id, experiment_id, created_at
  FROM experiments ORDER BY created_at DESC LIMIT 5"
```

The components catalog tells you which atoms are still `proposed`
(never tried) and which are `experimental` (tried but inconclusive)
— prime targets for the next experiment.

Read the most recent N journal entries from `lab/experiments.md`
in full — including the `### Aggregate / Mutation impact /
Failure modes / Tree effect` blocks — so you know what just
happened.

Also read:

-   The most recent `runs/lab/cross_experiment/*.json` snapshot
    (sort by mtime; take newest).
-   `lab/roadmap.md > ## Up next` to avoid duplicating queued work.
-   `lab/ideas.md` to avoid colliding kebab-ids.

### 2. Identify the highest-signal next experiments

Apply the following heuristics (in priority order). Each yields
zero or more candidate follow-ups:

#### 2a. Resolve a NoOp into a clean signal

If the latest verdict is `no_op` with `confidence < 0.8`, the
experiment was inconclusive (small n, mixed clusters, or close
delta). Propose:

-   Re-run the same paired ablation on a **focused cluster**
    where the mutation showed the strongest delta (positive *or*
    negative). This usually flips a `no_op` into either `add_branch`
    or `reject` decisively.

#### 2b. Test an AddBranch on a different cluster

If the latest verdict was `add_branch` with the `use_when`
predicate citing 2-3 clusters, propose:

-   A targeted re-test on **one of the named clusters** (smaller
    n, focused selector) to confirm the win-rate isn't an artefact
    of small-sample noise.
-   A test on a **sibling cluster** (same `category` family in
    `task_features`) to check whether the predicate generalises.

#### 2c. Re-baseline branches against the new trunk

If the trunk just swapped (`tree_diffs.kind = 'graduate'` and
`applied_at` within the last 3 ticks), every branch's `Last
verified` date is now stale. Propose:

-   Paired ablations of each existing branch against the new
    trunk (one slug per branch). These are cheap if cached and
    re-anchor the tree.

#### 2d. Test a Proposed config or atom

If `lab/configs.md > ## Proposed` has entries that haven't been
the subject of any roadmap slug, propose:

-   A paired ablation for the highest-priority Proposed config
    (judged by `motivation` strength + estimated cost). Use its
    `Linked idea` as `--idea`.

If `lab/components.md` has any rows with `status = proposed`
(catalog atoms we've thought of but never run), prefer the one
whose evidence column already lists a queued or done roadmap slug
— it's the cheapest to schedule because the roadmap entry exists.

#### 2e. Promote a `### Suggested` from cross-experiment-critic

If `cross-experiment-critic` recently appended a follow-up that
matches a Proposed component or an obvious gap, **don't
duplicate it** — note it in your report instead and let the
human promote it via `lab-plan-next`.

### 3. Write the suggestions

Each candidate becomes either a concrete roadmap suggestion (you
know the cost, slice, and variant) or an abstract idea (you only
know "we should explore X").

**Concrete → roadmap suggestion:**

```bash
uv run lab roadmap suggest <slug> \
  --hypothesis "<one sentence — what this run will tell us>" \
  --source "lab-reflect-and-plan@$(date +%Y-%m-%d)" \
  [--cost "~$X for the full slice"]
```

The CLI:

-   Refuses if `<slug>` already exists anywhere in `lab/roadmap.md`
    (main `## Up next`, `### Suggested`, or `## Done`).
-   Auto-creates the `### Suggested` substream the first time.
-   Never touches the main `## Up next` queue.

**Abstract → auto-proposed idea:**

```bash
uv run lab idea auto-propose <kebab-id> \
  --motivation "<one sentence — observed gap>" \
  --sketch "<one or two sentences — how we'd test it>" \
  --source "lab-reflect-and-plan@$(date +%Y-%m-%d)"
```

The CLI refuses if `<kebab-id>` collides with any existing entry
across all four lab files.

### 4. Cross-link suggestions back to the journal entry that motivated them

For each suggestion you write, append a `### Linked follow-ups`
bullet to the journal entry it was reflecting on. Use the
synthesize helper, which already knows how to stitch follow-ups
into the right subsection:

```bash
uv run lab experiments synthesize <slug> --section "Linked follow-ups"
```

This re-renders only the `### Linked follow-ups` section from
your fresh writes, keeping the rest of the entry untouched.

### 5. Report

Reply with:

-   The `instance_id` (or set of instance_ids) you reflected on.
-   The verdict and confidence of each.
-   The number of `### Suggested` roadmap entries you wrote (with
    slugs).
-   The number of `## Auto-proposed` ideas you wrote (with ids).
-   Any **gaps** you noticed but did not act on (e.g. "trunk just
    swapped but I refused to re-baseline branches because no
    branches exist yet").
-   The `lab-plan-next` invocation the human can run to promote
    your top suggestion: `uv run lab roadmap promote <slug>`.

## Constraints

-   Read-only against `lab/configs.md`, `lab/components.md`, `lab/experiments.md`,
    `## Up next` (main), `## Done`, `## Proposed / Trying /
    Graduated / Rejected` in ideas. **Only** `### Suggested` and
    `## Auto-proposed` are write zones.
-   Never propose more than 5 concrete `### Suggested` entries per
    invocation. Quality > quantity; humans need a manageable
    review surface.
-   Never propose anything whose hypothesis you can't tie back to
    a quote from the journal entries you read.
-   Never modify `runs/experiments/*` or `runs/lab/*`.

## Example

```
$ codex exec --skill lab-reflect-and-plan --instance loop-guard-tb2-paired-20260420-091230

Reflecting on `loop-guard-tb2-paired` (verdict: add_branch, target_id=loop-guard,
  use_when=cluster ∈ {build, python_async}, confidence=0.92).

Wrote 2 ### Suggested entries to lab/roadmap.md:
  - loop-guard-on-build-cluster-targeted   (~$2 full cluster slice)
  - loop-guard-on-needs-network-cluster    (~$2 full cluster slice; sibling)

Wrote 1 ## Auto-proposed idea to lab/ideas.md:
  - loop-guard-on-trunk-swap-rebaseline    (only relevant after the next graduate)

Did not propose: a re-test on `python_async` (cross-experiment-critic
already appended `loop-guard-on-python-async` two days ago; let it land
first).

To run the top suggestion: `uv run lab roadmap promote loop-guard-on-build-cluster-targeted`.
```
