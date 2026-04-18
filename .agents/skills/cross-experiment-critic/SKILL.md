---
name: cross-experiment-critic
description: >
  Look across all experiments in the lab DB to find component × task-
  cluster patterns, update the `components_perf` table, and surface
  human-curated follow-up suggestions in `lab/ideas.md > ## Auto-
  proposed`. Use after multiple experiments have completed and the
  user (or the orchestrator) wants to know "which component actually
  helps which kind of task" or "what should we try next given what we
  know". Reads the entire lab DB; writes `components_perf` rows and
  appends to `lab/ideas.md` via `uv run lab append-followup-idea`.
  Companion skills: trial-critic, experiment-critic, task-features.
---

# Cross-Experiment Critic

The `experiment-critic` answers "which leg won this run". This
skill answers "across every run, which component / strategy /
prompt-shape actually helps, and on what kind of task". It is the
feedback loop that closes the gap between "the agent finished N
runs" and "the next idea worth queueing".

You are an autonomous codex agent. You have read access to the full
lab DB, write access to two specific tables (`components_perf` and
`misconfigurations` once Phase 3b lands), and write access to a
single dedicated section in `lab/ideas.md` (`## Auto-proposed`).

## When to Use

- The orchestrator invokes this skill every M experiments (default
  M=1 for now; configurable in `runner.py`).
- Human asks "what's our current best component", "which agent
  config dominates on `build-*` tasks", or "what should we queue
  next".

Do **not** use this skill:

- Before there are at least 2 experiment instances in the DB. With
  only one instance, there is no cross-experiment signal —
  everything degenerates to `experiment-critic`. Pre-flight:
  ```bash
  uv run lab query "SELECT count(*) FROM experiments"
  ```
- To touch `## Proposed` ideas. The human owns `## Proposed`. This
  skill only ever writes to `## Auto-proposed`. The human promotes
  follow-ups manually.

## Inputs

```bash
codex exec --skill cross-experiment-critic
```

No positional argument. Optional flags (parsed by you, not by the
codex CLI):

- `--component <id>` — restrict the analysis to one component.
- `--cluster <task_feature>` — restrict to tasks carrying a
  given feature tag.
- `--since <YYYY-MM-DD>` — ignore experiments older than this
  date.

## What to do

### 1. Survey the DB

```bash
uv run lab query "
  SELECT count(DISTINCT instance_id) AS n_runs,
         count(*) AS n_trials,
         count(DISTINCT task_name) AS n_tasks,
         sum(CASE WHEN c.trial_id IS NULL THEN 1 ELSE 0 END) AS missing_critiques
  FROM trials t LEFT JOIN trial_critiques c USING (trial_id)"
```

If `missing_critiques > 0`, log a warning but continue — partial
data is fine for cross-experiment trends. Note the gap in your
final report.

### 2. Cluster tasks

Use `task_features` if present; otherwise cluster by task-name
prefix (`build-*`, `git-*`, `cancel-*`, `convert-*`, …) and by
the `category` column on `task_features`. Persist your chosen
cluster vocabulary in your final report — `cross-experiment-
critic` runs are the only place new clusters get introduced, so
be deliberate.

```bash
uv run lab query "
  SELECT category, count(*) AS n FROM task_features
  GROUP BY category ORDER BY n DESC"
```

If the `task_features` table is sparse, queue
`task-features` invocations for the missing checksums (record the
list in your report; the orchestrator will pick them up).

### 3. Compute per-(component, cluster) performance

For each (component_id, task_cluster) pair with at least 5 trials:

- `n_trials`: number of trials where `components_active` contained
  the component AND the trial's task is in the cluster.
- `win_rate`: fraction of those trials where the trial's score
  beat the per-task max score from any leg lacking that
  component (i.e. the component was decisive). Define ties as
  losses to avoid inflating wins.
- `cost_delta_pct`: percentage difference in mean `cost_usd`
  between trials with vs without the component on the same
  task_cluster.
- `supporting_experiments`: list of `instance_id`s contributing.
- `notes`: 1–2 sentences with the salient anti-patterns it
  reduces / introduces (drawn from `trial_critiques`).

Write each row via:

```bash
uv run lab upsert-component-perf <component_id> <task_cluster> --json - <<'JSON'
{
  "n_trials":               42,
  "win_rate":               0.36,
  "cost_delta_pct":         12.5,
  "supporting_experiments": ["tb2-baseline-...", "tb2-loop-guard-..."],
  "notes":                  "..."
}
JSON
```

### 4. Identify hypothesis gaps → suggest follow-ups

A "hypothesis gap" is a question the existing experiments don't
answer cleanly. Examples:

- A component shows a strong positive on cluster X but no
  experiment has tested it on cluster Y where Y has similar
  task_features.
- An anti-pattern dominates failures across legs (e.g.
  `repeated_failed_command` in 60% of `build-*` failures) but
  no proposed idea targets it.
- A class of tasks (e.g. anything carrying `needs_network`)
  fails universally, suggesting a missing tool or component.

For each gap, append a follow-up idea to `lab/ideas.md > ##
Auto-proposed`:

```bash
uv run lab append-followup-idea <kebab-id> \
  --motivation "<one sentence — observed pattern>" \
  --sketch     "<one or two sentences — concrete experiment to disambiguate>" \
  --source     "cross-experiment-critic@$(date +%Y-%m-%d)"
```

The CLI:

- Refuses if `<kebab-id>` collides with any existing entry across
  all four lab files. Pick a different id and retry.
- Auto-creates the `## Auto-proposed` section the first time.
- Never touches `## Proposed`, `## Trying`, `## Graduated`, or
  `## Rejected`.

### 5. Report

Reply with:

- The clusters you used (vocabulary).
- Number of `components_perf` rows updated.
- Number of follow-up ideas appended (and their ids).
- Any gaps in the data: missing critiques, missing task_features,
  components with < 5 supporting trials.

## Constraints

- Never edit `## Proposed`, `## Trying`, `## Graduated`,
  `## Rejected` in `lab/ideas.md`. Only `## Auto-proposed` is
  yours.
- Never edit `lab/roadmap.md`. Auto-proposed ideas are not
  auto-queued — the human reads them and decides whether to
  promote.
- Never edit `lab/experiments.md` or `lab/components.md`. Those
  are owned by `lab-run-experiment` and `lab-graduate-component`
  respectively.
- Never modify `runs/experiments/*` artefacts.
- Confidence-tag any suggested follow-up that's based on < 10
  trials per side; the human needs to know when the cluster is
  thin.

## Example

```
$ codex exec --skill cross-experiment-critic

OK; 4 experiments analysed (3 baseline-shape, 1 loop-guard ablation).
   Updated 7 components_perf rows.
   Appended 3 follow-up ideas to lab/ideas.md > ## Auto-proposed:
     - loop-guard-on-build-cluster
     - planner-rerank-against-multi-file-edits
     - retry-budget-on-needs-network-tasks
   Note: task_features missing for 41 task_checksums; queued for
   `task-features` skill on the next orchestrator pass.
```
