---
name: cross-experiment-critic
description: >
  Look across all experiments to find component × task-cluster
  patterns, refresh the `components_perf` files, and surface human-
  curated follow-up suggestions in `lab/ideas.md > ## Auto-proposed`.
  Use after multiple experiments have completed and the user (or
  the orchestrator) wants to know "which component actually helps
  which kind of task" or "what should we try next given what we
  know". Reads the entire lab DB plus all per-trial / per-task
  critic JSON files; writes
  `runs/lab/components_perf/<component>__<cluster>.json` via
  `uv run lab write-component-perf`, a snapshot to
  `runs/lab/cross_experiment/<ts>__<spawn_id>.json` via
  `uv run lab write-cross-experiment`, and appends to `lab/ideas.md`
  via `uv run lab append-followup-idea`. Companion skills:
  trial-critic, experiment-critic, task-features.
---

# Cross-Experiment Critic

The `experiment-critic` answers "which leg won this run". This
skill answers "across every run, which component / strategy /
prompt-shape actually helps, and on what kind of task". It is the
feedback loop that closes the gap between "the agent finished N
runs" and "the next idea worth queueing".

You are an autonomous codex agent. You have read access to the full
lab DB plus every per-trial critic file under
`runs/experiments/*/legs/.../<trial>/critic/trial-critic.json`,
every per-task feature file under `runs/lab/task_features/`, and
every per-experiment summary under
`runs/experiments/*/critic/experiment-critic.json`. You write
`runs/lab/components_perf/*.json`,
`runs/lab/cross_experiment/*.json`, and a single dedicated section
in `lab/ideas.md` (`## Auto-proposed`).

### Subagent fan-out (multi_agent enabled)

This skill runs with codex's `multi_agent` feature enabled.
Decompose the analysis along independent axes — one subagent per
component (or per task cluster) — and have each return its slice
of the (component × cluster) win-rate table. The synthesis
(§3 → §4 → §5) stays in the parent agent so follow-up ideas come
out of one coherent view.

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
uv run lab write-component-perf <component_id> <task_cluster> --json - <<'JSON'
{
  "n_trials":               42,
  "win_rate":               0.36,
  "cost_delta_pct":         12.5,
  "supporting_experiments": ["tb2-baseline-...", "tb2-loop-guard-..."],
  "notes":                  "..."
}
JSON
```

The CLI writes
`runs/lab/components_perf/<component_id>__<task_cluster>.json`.
The DuckDB cache (`components_perf` table) is rebuilt from these
files on demand by `uv run lab ingest-critiques`.

After all per-component rows are written, persist a single
snapshot of the entire cross-experiment view (the apex artifact)
via:

```bash
uv run lab write-cross-experiment "$OPENHARNESS_LAB_SPAWN_ID" \
  --critic-model "$OPENHARNESS_CODEX_MODEL" --json - <<'JSON'
{
  "n_experiments":     4,
  "n_trials":          512,
  "clusters_used":     ["python_async","build","needs_network","..."],
  "headline":          "<2-3 sentences: what the cross-run picture says>",
  "components_summary":[{"component_id":"loop-guard","best_cluster":"build","win_rate":0.61}],
  "follow_up_ideas":   ["loop-guard-on-build-cluster", "..."]
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
uv run lab idea auto-propose <kebab-id> \
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

-   Never edit `## Proposed`, `## Trying`, `## Graduated`,
    `## Rejected` in `lab/ideas.md`. Only `## Auto-proposed` is
    yours (shared with `lab-reflect-and-plan`).
-   Never edit `lab/roadmap.md`. Suggesting roadmap entries is
    `lab-reflect-and-plan`'s job; this skill stays in the
    `## Auto-proposed` lane of `lab/ideas.md`.
-   Never edit `lab/experiments.md` (journal entries are written
    by `lab-run-experiment` + `experiments synthesize` + `tree
    apply`), `lab/configs.md` (the configuration tree is mutated
    only by `lab tree apply` and `lab graduate confirm`), or
    `lab/components.md` (the catalog is bumped automatically by
    `lab tree apply` and edited explicitly via `lab components`).
    **In particular, do not write any verdict, AddBranch, Reject,
    or Graduate suggestion** — those are computed deterministically
    by `tree_ops.evaluate` from `experiment-critic.json` +
    `comparisons/*.json`, not proposed by you.
-   Never modify `runs/experiments/*` artefacts.
-   Confidence-tag any suggested follow-up that's based on < 10
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
