---
name: experiment-critic
description: >
  Aggregate per-trial critiques across the legs of one experiment and
  produce per-task A/B (A/B/C…) comparisons explaining differential
  outcomes. Use when an experiment finishes and the human or
  orchestrator asks "which leg actually won, and why?", "what tasks
  did leg X solve that leg Y didn't?", or "summarise this run beyond
  the aggregate pass rate". Reads per-trial critic JSON files under
  `<run_dir>/legs/.../<trial>/critic/trial-critic.json` plus the
  trials registry in DuckDB; writes one file per task to
  `<run_dir>/critic/comparisons/<task>.json` via
  `uv run lab write-comparison`, an experiment summary to
  `<run_dir>/critic/experiment-critic.json` via
  `uv run lab write-experiment-critique`, and a human-facing
  `runs/experiments/<id>/results/critic_summary.md`. Companion
  skills: trial-critic (must have run first), cross-experiment-critic,
  task-features.
---

# Experiment Critic

The aggregate `### Aggregate` table that lands in the experiment's
journal entry (`lab/experiments.md`) says nothing about *which tasks*
one leg won that another didn't, or *why*. This skill closes that
gap by joining per-trial critiques across legs of a single
experiment instance and producing a per-task verdict.

The output of this skill is what `tree_ops.evaluate(<instance_id>)`
reads (alongside the per-task `comparisons/*.json`) to compute the
`### Tree effect` block — i.e. the **TreeDiff** that `lab tree
apply` writes back to `lab/experiments.md` and (for AddBranch /
Reject / NoOp) to `lab/configs.md`, with the unique-to-target atoms
forward-bumped in `lab/components.md`. **Be precise about
mutation_impact and failure_modes** — those fields directly drive
`### Mutation impact` and `### Failure modes` in the journal.

You are an autonomous codex agent. You have read access to the lab
DB via `uv run lab query …`; you read per-trial critiques as files
under each trial's `critic/trial-critic.json`; and you write back
via `uv run lab write-comparison …`,
`uv run lab write-experiment-critique …`, and a single Markdown
file (`results/critic_summary.md`).

### Subagent fan-out (multi_agent enabled)

This skill runs with codex's `multi_agent` feature enabled. The
per-task analysis is naturally parallel: you have N independent
tasks to compare across legs, and each comparison only depends on
that task's per-trial critiques. **Delegate to subagents** in
batches (e.g. 8 tasks per subagent) so the wall-clock stays
bounded as N grows. The synthesis step (§4 below) must remain in
the parent agent; only the per-task comparisons (§3) parallelize.

## When to Use

- Orchestrator finishes ingesting a run and dispatching all
  per-trial `trial-critic` invocations; it then invokes this
  skill once per experiment instance.
- Human asks "where did `<leg>` actually win on this experiment?"
  or "why did the pass rate split the way it did?".

Do **not** use this skill:

- Before all per-trial critiques are present. Pre-flight by
  walking the run dir for missing files:
  ```bash
  find <run_dir>/legs -type d -name 'harbor' -prune -o -type d \
    -path '*/legs/*/harbor/*' -print | while read d; do
      [ -f "$d/critic/trial-critic.json" ] || echo "missing: $d"
    done | head
  ```
  If any trial dir is missing its `critic/trial-critic.json`,
  refuse and report which trials still need critiques.
- For cross-experiment patterns. That's `cross-experiment-critic`.

## Inputs

```bash
codex exec --skill experiment-critic <instance_id>
# e.g.
codex exec --skill experiment-critic tb2-baseline-20260417-234913
```

The skill always operates on exactly one experiment instance.

## What to do

### 1. Sanity-check the experiment

```bash
uv run lab query "
  SELECT leg_id, count(*) AS n_trials, sum(CAST(passed AS INT)) AS n_passed,
         ROUND(100.0*avg(CAST(passed AS DOUBLE)),1) AS pass_pct,
         ROUND(sum(cost_usd),2) AS cost_usd
  FROM trials WHERE instance_id = '<instance>' GROUP BY leg_id ORDER BY leg_id"
```

You need ≥ 2 legs to produce comparisons. If only one leg exists,
emit a single-leg `critic_summary.md` (no comparison rows) and exit
cleanly — there is nothing to compare against.

### 2. Pull the per-task picture

For every task in this experiment, gather:

- The score and outcome on each leg (from the trials registry).
- Each leg's per-trial critique (`task_summary`, `agent_strategy`,
  `outcome`, `root_cause` / `success_factor`, `anti_patterns`,
  `key_actions`, `surprising_observations`) — read from
  `<trial_dir>/critic/trial-critic.json`.
- The task's features from `runs/lab/task_features/<checksum>.json`
  if present.

Locate per-trial files for one task:

```bash
uv run lab query "
  SELECT t.task_name, t.leg_id, t.passed, t.score, t.cost_usd, t.trial_dir
  FROM trials t WHERE t.instance_id = '<instance>'
  ORDER BY t.task_name, t.leg_id"
# then for each row, read $trial_dir/critic/trial-critic.json
```

### 3. Write one comparison per task

For each `task_name`, decide:

- **winning_leg**: the leg with the highest score; tie-break by
  lower cost, then by lower wall-clock.
- **runner_up_leg**: the next leg by score (only meaningful if
  there are ≥ 3 legs).
- **delta_score**: `winning_leg.score - max(other_legs.score)`.
  When all legs failed, `delta_score = 0`; the `why` paragraph
  must call out that this is a wash.
- **why**: 2–4 sentences pinning the differential to a concrete
  mechanism — drawn from the per-trial critiques, not invented.
  Quote anti_patterns / strategy fragments where they explain
  the split. If both legs failed for the *same* reason, say so —
  this is a signal to `cross-experiment-critic`.
- **evidence**: a small JSON object linking each leg to its trial id
  and one or two key_actions snippets so the comparison is
  auditable. Include `trial_id`, `outcome`, `key_action_quote`.
- **legs_compared**: list of `leg_id`s included.

Persist via:

```bash
uv run lab write-comparison <run_dir> <task_name> \
  --critic-model "$OPENHARNESS_CODEX_MODEL" --json - <<'JSON'
{
  "winning_leg":   "basic",
  "runner_up_leg": "react",
  "delta_score":   1.0,
  "why":           "...",
  "evidence":      {"basic": {...}, "react": {...}},
  "legs_compared": ["basic", "planner_executor", "react"]
}
JSON
```

The CLI writes `<run_dir>/critic/comparisons/<task_name>.json`.
The `comparisons` DB table is rebuilt from these files on demand
by `uv run lab ingest-critiques`.

### 4. Synthesise patterns at the experiment level

After all per-task comparisons are written, look for groupings:

- **Tasks where one leg consistently wins.** Cluster on
  task_features (if present) or task name prefixes (`build-*`,
  `git-*`, `cancel-*`).
- **Tasks where every leg fails for the same reason.** These are
  the strongest candidates for `cross-experiment-critic` to turn
  into follow-up ideas. Tag them.
- **Anti-patterns more frequent in one leg than another.** E.g.
  `planner_executor` exhibits `hallucinated_success` 3× more than
  `basic`.

### 5. Persist the experiment-level summary

Persist the synthesis as a structured file too — this is what
`cross-experiment-critic` reads:

```bash
uv run lab write-experiment-critique <run_dir> \
  --critic-model "$OPENHARNESS_CODEX_MODEL" --json - <<'JSON'
{
  "instance_id":       "<instance>",
  "headline":          "<2-3 sentences: what decided this run>",
  "mutation_impact":   "<2-4 sentences: did the mutation help, hurt, or wash? where? why? Reference deltas in pp.>",
  "leg_winners":       [{"leg": "basic", "tasks": ["a","b","c"], "common_factor": "..."}],
  "all_legs_failed":   ["task1", "task2"],
  "failure_modes":     [{"name": "tool-output-overflow", "count": 12, "description": "..."}, ...],
  "anti_pattern_skew": [{"leg": "planner_executor", "anti_pattern": "hallucinated_success", "ratio_vs_baseline": 3.0}],
  "follow_up_seeds":   ["...", "..."]
}
JSON
```

**Required fields the journal renderer reads directly** (do not omit
or rename — `journal_synth.synthesize` falls back to
DB-only stats when these are missing, which produces less useful
journal entries):

-   `mutation_impact`: **string** (preferred) or list/dict. Drives
    `### Mutation impact`. State the headline delta in percentage
    points, the cluster(s) where it shifted most, and a one-sentence
    causal hypothesis.
-   `failure_modes`: list of objects with `{name, count, description}`
    (or list of strings, or a single string). Drives `### Failure
    modes`. Roll up the per-trial `anti_patterns` and `outcome`
    fields into the 3–6 dominant modes; cite counts.
-   `follow_up_seeds`: list of short strings. Drives `### Linked
    follow-ups`. Each seed becomes a candidate `## Auto-proposed`
    idea — write them as concrete, testable hypotheses.

Then write a Markdown file at
`runs/experiments/<instance_id>/results/critic_summary.md`.
Suggested shape (you may adapt):

```markdown
# Critic summary — <instance_id>

## Headline
- Trials per leg, pass rates per leg (re-stating the existing
  `summary.md` table).
- One-paragraph "what actually decided this run".

## Where each leg won (per-task highlights)
- **basic** won on N tasks {{a, b, c}}. Common factor: …
- **planner_executor** won on M tasks {{…}}. Common factor: …

## Common failure modes
- All legs failed on K tasks with reason …
- Anti-pattern X dominated leg Y …

## Suggested follow-ups (for cross-experiment-critic)
- Tag X is over-represented in failures → consider exploring …
- Cost-per-pass ratio for leg Z is N× the others …
```

This file is human-facing and lives next to `summary.md`. It is the
single artefact a person can read to understand the run beyond the
aggregate table — keep it tight (≤ 1 screen if possible).

### 6. Report

Reply to the orchestrator / user with:

- The instance id processed.
- Number of tasks compared, number of "all legs failed" tasks.
- Path to `critic_summary.md`.
- Path to per-task comparison files:
  `<run_dir>/critic/comparisons/`.

## Refusal cases

- Pre-flight critique check fails (any trial dir is missing its
  `critic/trial-critic.json`). Report which trials are missing.
- Only one leg exists. Emit a stripped `critic_summary.md` (header
  + headline only) and exit, but write **no** `comparisons/` files.
- DB has zero trials for this instance. Run
  `uv run lab ingest <run_dir>` first.

## Constraints

-   Never edit `lab/experiments.md`, `lab/ideas.md`, `lab/roadmap.md`,
    `lab/configs.md`, or `lab/components.md`. The journal entry's narrative subsections
    (`### Aggregate / Mutation impact / Failure modes / Linked
    follow-ups`) are written by `uv run lab experiments synthesize`,
    which reads the `experiment-critic.json` you wrote here. The
    `### Tree effect` block is written by `uv run lab tree apply`,
    which calls `tree_ops.evaluate`. Follow-up ideas in
    `## Auto-proposed` are written by `cross-experiment-critic` /
    `lab-replan-roadmap`. **You only write JSON files.**
-   Never ingest, never run new agents, never modify any
    `runs/experiments/*` artifact other than
    `results/critic_summary.md` and the files under `critic/`.
-   Quote per-trial critiques and `key_actions`; do not paraphrase.

## Example

```
$ codex exec --skill experiment-critic tb2-baseline-20260417-234913

# After 89 comparisons inserted:
OK; 89 tasks compared (all 3 legs), 64 tasks where all legs failed,
   25 tasks won by ≥1 leg. Wrote
   runs/experiments/tb2-baseline-20260417-234913/results/critic_summary.md.
```
