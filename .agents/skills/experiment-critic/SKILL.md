---
name: experiment-critic
description: >
  Aggregate per-trial critiques across the legs of one experiment and
  produce per-task A/B (A/B/C…) comparisons explaining differential
  outcomes. Use when an experiment finishes and the human or
  orchestrator asks "which leg actually won, and why?", "what tasks
  did leg X solve that leg Y didn't?", or "summarise this run beyond
  the aggregate pass rate". Reads the lab DB (`trials`,
  `trial_critiques`, `task_features`); writes one row per task to the
  `comparisons` table via `uv run lab insert-comparison`, plus a
  human-facing `runs/experiments/<id>/results/critic_summary.md`.
  Companion skills: trial-critic (must have run first), cross-
  experiment-critic, task-features.
---

# Experiment Critic

The aggregate "pass rate per leg" table that lands in
`lab/experiments.md` says nothing about *which tasks* one leg won
that another didn't, or *why*. This skill closes that gap by joining
per-trial critiques across legs of a single experiment instance and
producing a per-task verdict.

You are an autonomous codex agent. You have read access to the lab
DB via `uv run lab query …`; you write back via `uv run lab
insert-comparison …` and a single Markdown file.

## When to Use

- Orchestrator finishes ingesting a run and dispatching all
  per-trial `trial-critic` invocations; it then invokes this
  skill once per experiment instance.
- Human asks "where did `<leg>` actually win on this experiment?"
  or "why did the pass rate split the way it did?".

Do **not** use this skill:

- Before all per-trial critiques are present. Pre-flight:
  ```bash
  uv run lab query "
    SELECT leg_id, count(*) AS missing
    FROM trials t LEFT JOIN trial_critiques c USING (trial_id)
    WHERE t.instance_id = '<instance>' AND c.trial_id IS NULL
    GROUP BY leg_id"
  ```
  If any row's `missing > 0`, refuse and report which trials still
  need critiques (`uv run lab query-trials --instance <id> --needs-critique`).
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

- The score and outcome on each leg.
- Each leg's per-trial critique (`task_summary`, `agent_strategy`,
  `outcome`, `root_cause` / `success_factor`, `anti_patterns`,
  `key_actions`, `surprising_observations`).
- The task's features from `task_features` (if present).

Helpful query:

```bash
uv run lab query "
  SELECT t.task_name, t.leg_id, t.passed, t.score, t.cost_usd,
         c.outcome, c.root_cause, c.success_factor,
         c.agent_strategy, c.anti_patterns, c.key_actions
  FROM trials t LEFT JOIN trial_critiques c USING (trial_id)
  WHERE t.instance_id = '<instance>'
  ORDER BY t.task_name, t.leg_id"
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
uv run lab insert-comparison <instance_id> <task_name> \
  --critic-model "<your model>" --json - <<'JSON'
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

### 5. Emit the human-facing summary

Write a Markdown file at
`runs/experiments/<instance_id>/results/critic_summary.md`. Suggested
shape (you may adapt):

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
- Path to per-task DB rows: `uv run lab query "SELECT * FROM
  comparisons WHERE instance_id = '<id>'"`.

## Refusal cases

- Pre-flight critique check fails (any leg has `missing > 0`
  trial_critique rows). Report which trials are missing.
- Only one leg exists. Emit a stripped `critic_summary.md` (header
  + headline only) and exit, but write **no** `comparisons` rows.
- DB has zero trials for this instance. Run
  `uv run lab ingest <run_dir>` first.

## Constraints

- Never edit `lab/experiments.md`, `lab/ideas.md`, `lab/roadmap.md`,
  or `lab/components.md`. The aggregate table in `experiments.md`
  is owned by `lab-run-experiment`; follow-up ideas in
  `## Auto-proposed` are owned by `cross-experiment-critic`.
- Never ingest, never run new agents, never modify any
  `runs/experiments/*` artifact other than `results/critic_summary.md`.
- Quote per-trial critiques and `key_actions`; do not paraphrase.

## Example

```
$ codex exec --skill experiment-critic tb2-baseline-20260417-234913

# After 89 comparisons inserted:
OK; 89 tasks compared (all 3 legs), 64 tasks where all legs failed,
   25 tasks won by ≥1 leg. Wrote
   runs/experiments/tb2-baseline-20260417-234913/results/critic_summary.md.
```
