# Methodology

The scientific contract every experiment in this lab must satisfy.
Skills enforce it at design time; `tree_ops.evaluate` enforces it at
verdict time; humans audit against it before promoting `## Suggested`
entries to the queue or confirming a `Graduate`. Conceptual model
lives in [`README.md`](README.md); operational runbook lives in
[`OPERATIONS.md`](OPERATIONS.md); this file is the **contract**.

> **Hard rule.** No experiment is launched, no verdict is rendered,
> and no trunk swap is honored unless §2-§6 of this document are
> satisfied. Sections marked **DEFERRED** describe rigour we have
> not yet built — they are tracked as concrete items in
> [`ideas.md > ## Framework`](ideas.md) and are honestly absent from
> today's enforcement.

---

## 1. The unit of evidence

One experiment proposes exactly one `TreeDiff` (`Graduate` /
`AddBranch` / `Reject` / `NoOp`). The verdict is bound by — and only
by — five declared dimensions:

| Dimension | Question | Declared in | Enforced by |
|-----------|----------|-------------|-------------|
| **Slice** (§2) | What population are we measuring? | `design.md > ## Slice > Full` | `lab-design-variant`; `tree_ops.evaluate` floor |
| **Legs** (§3) | What conditions are we comparing? | `experiments/<spec>.yaml > legs:` | `lab-design-variant`; `phase_run` |
| **Repetitions** (§4) | How many samples per `(leg, task)` cell? | `design.md > ## Slice > Repetitions` and the spec's `n_attempts:` | `lab-design-variant`; `phase_run` |
| **Control** (§5) | What is the comparison anchor? | `design.md > ## Slice > Control` | `lab-design-variant`; (DEFERRED for historical) |
| **Verdict** (§6) | What evidence threshold is required? | `tree_ops.evaluate` constants | `tree_ops.evaluate` |

Anything that affects the inferred causal claim must appear in one
of these five. A change that silently affects the agent (a tool
upgrade, a model checkpoint shift, a verifier patch) and is not
captured here is methodologically invisible — and therefore
forbidden between phases of the same experiment.

---

## 2. Slice — what population are we measuring?

The slice is the set of `(task, attempt)` cells the experiment runs
on. It must be declared in `design.md > ## Slice > Full` with a
shape and an explicit `n` per leg.

### Allowed shapes

| Shape | Definition | Use when | Pitfall |
|-------|------------|----------|---------|
| `full-bench` | Every task in the pinned bench (e.g. `terminal-bench@2.0` ≈ 89 tasks). | The variant claims to move the aggregate pass rate. The default. | Most expensive. |
| `cluster: <names>` | Tasks whose `task_features.category` is in `<names>`. | Variant addresses a specific failure mode. | **Cluster sizes in `tb2` are tiny** — see Appendix B. Most clusters have n=1. |
| `cluster_combined: <names>` | Tasks whose category is in any of `<names>`, treated as one slice. | Confirming a multi-cluster `AddBranch` use-when. | Honest about coverage; still must clear the verdict floor. |
| `near-miss` | Failing-by-≤K-turns or failing-with-uncaught-budget subset of a prior run. | Budget / compaction / loop-guard variants. | **Selection-biased** on prior failures → regression-to-the-mean noise (§5). |
| `regression: <task_ids>` | Explicit task-id list. | Targeting known failures. | Hardcodes the population; document the reasoning. |

### Required declarations (in `design.md > ## Slice > Full`)

1. **Shape and parameters** (e.g. `cluster_combined: security_certificates, system_administration, python_data`).
2. **Expected `n_tasks` per leg** computed from the shape (cite Appendix B for cluster sizes).
3. **Total `n_trials` per leg** = `n_tasks × n_attempts`. Must be ≥ `MIN_TRIALS_PER_LEG_FOR_VERDICT` (currently 5, see §6).
4. **Evidence justifying the shape**: cite the prior `instance_id` whose results motivate this slice (e.g. "max-turns-30 subset of `tb2-baseline-20260417-234913`"), or "first measurement, no prior" for genuinely new populations.

### Hard invariants

- **Both legs MUST share the task list.** Paired delta variance (§6)
  collapses if leg A and leg B run on different tasks. The spec's
  `task_filter:` produces the canonical list once and applies it to
  every leg.
- **Slice ⊆ pinned bench at the recorded version.** A bench re-pin
  invalidates every prior verdict that used it; flag explicitly
  whenever the bench pin moves.
- **No selection-biased slice may be used for a `Graduate` verdict.**
  `near-miss` and `regression:` slices can produce `AddBranch` /
  `Reject` / `NoOp` only. A `Graduate` requires `full-bench` or a
  `cluster_combined:` covering ≥ 30% of the bench.

---

## 3. Legs — what conditions are we comparing?

Each leg is one agent configuration run against the slice. The
default shape is **paired ablation** (control + 1 mutation = 2
legs); other counts are valid when the structure of the question
demands them.

| `n_legs` | Shape | When |
|---------:|-------|------|
| 1 | Pure measurement | Anchor a new agent, measure trunk drift. **Yields no verdict** — `tree_ops.evaluate` returns `no_op` because there is no comparison. Use for baselining only. |
| 2 | Paired ablation (control vs treatment) | The default. Isolate one variable. |
| 3 | Multi-arm | Variable has > 2 levels (budget = 30 / 60 / 120), OR two independent variables share the slice and can be tested in one run for ~50% marginal cost. Strongly preferred over two separate 2-leg experiments when feasible. |
| 4+ | Factorial / sweep | A × B factorial or hyperparameter scan. Justify the cost; usually only worth it when slice cost dominates per-leg cost. |

### Required declarations

- **Each leg's `agent_id` and config hash** in the spec. Differences
  between legs must be **explicit and minimal** — one variable per
  pairwise contrast. Adding a second axis without declaring it is
  a confounded experiment.
- **The control leg.** Either trunk (default) or a named branch
  (e.g. for a runtime-flag ablation on `planner_executor`, the
  control is `planner_executor` with the flag off).

### Anti-pattern

> Two legs that differ in N > 1 axes (e.g. "leg A = trunk; leg B =
> trunk + new_planner + new_tools + new_prompt"). The verdict is
> uninterpretable: any delta could come from any of the changes.
> If the variant is a composition, run a 4-leg factorial or split
> into N sequential experiments.

---

## 4. Repetitions — how many samples per cell?

`n_attempts` is the number of times each `(leg, task)` cell runs.
It bounds **per-cell noise** (LLM sampling, tool race conditions);
it does **not** extend population coverage. Re-rolling the same
3 tasks 5× each gives `n_trials = 15` but the effective
independent-sample count for cluster generalisation remains ~3.

### Allowed modes (declare in `design.md > ## Slice > Repetitions`)

| Mode | `n_attempts` | When |
|------|-------------:|------|
| `single-shot` | 1 | Slice ≥ 30 trials/leg AND mechanism is deterministic-ish (config tweak, prompt edit). Default for `full-bench`. |
| `paired-double` | 2 | Slice 5–30 trials/leg, OR mechanism has stochastic internal state (loop-guard nudges, sampled plans, retry tools). Default for cluster slices. |
| `replication: r` | r × full re-run | Reserved for the **Graduate gate** (§7). Not used for routine ablations. |
| `adaptive: max=k` | starts at 1, tops up to k on borderline cells | DEFERRED — see [`ideas.md > ## Framework > adaptive-repetitions`](ideas.md). |

### Decision matrix

```
                   small slice           large slice
                   (cluster, regression)  (full-bench)
high noise   ┃ paired-double (≥2)   ┃ single-shot, top up if noisy
mechanism    ┃ ideally adaptive     ┃ (paired-double only if §6 floor close)
             ┣━━━━━━━━━━━━━━━━━━━━━━╋━━━━━━━━━━━━━━━━━━━━━━━━━━━━
low noise    ┃ paired-double        ┃ single-shot
mechanism    ┃ (to clear §6 floor)  ┃
```

### Required reporting

`tree_ops.evaluate` and `experiment-critic` must report **per-cell**
pass-rate (`n_pass / n_attempts` for each `(leg, task)`), not only
per-leg averages. Per-cell variance is the only honest input to a
re-roll decision in `lab-reflect-and-plan`.

### Anti-patterns

- **Mismatched `n_attempts` across legs in one experiment.** Variance
  of the paired delta becomes uninterpretable. The spec's
  `n_attempts:` is global; cell-level overrides are forbidden.
- **Inflating `n_trials` to "clear" the floor by repeating one task
  many times.** The floor (§6) is a *coverage* check disguised as
  a count. A 2-task slice with `n_attempts=3` (n_trials=6) is
  formally over the floor but evidentially weak — flag this case
  and demand a wider slice.

---

## 5. Control — fresh or historical?

The control leg is what we attribute the delta against. Today the
only enforced shape is `fresh`: every experiment re-runs its
control alongside the treatment. The DEFERRED shapes (historical
control + replay) are sketched here so the design template can
declare its intent now and tighten enforcement later.

### Allowed modes (declare in `design.md > ## Slice > Control`)

| Mode | Definition | Status |
|------|------------|--------|
| `fresh` | Control leg runs new trials in this experiment alongside the treatment. | **ENFORCED today.** Default. |
| `historical: <instance_id>/<leg_id>` | Control trials are borrowed from a prior experiment's matching `(instance_id, leg_id, task_filter)` rows. | DEFERRED — see [`ideas.md > ## Framework > historical-control-shape`](ideas.md). |
| `historical+replay: <instance_id>/<leg_id>` | Borrow as above PLUS run a third leg that replays the control config on the slice, to bound regression-to-the-mean. | DEFERRED. |

### Drift guards (required before any historical mode is honored)

When DEFERRED modes ship, the implement phase will block the run
phase unless **all** of the following byte-match between the
referenced prior experiment and the current one:

- Control config hash (`agent_id` + composed YAML SHA).
- Bench version pin.
- Verifier hash.
- Model pin (vendor + checkpoint).
- `n_attempts` (or both sides slice to attempt 1).

If any drift, the design must fall back to `control: fresh`. A
trunk graduation **invalidates all historical references to the old
trunk**; the DB will mark them stale and the design phase will
reject them.

### Selection-bias warning

`near-miss` and `regression:` slices are derived from a prior run's
**outcomes**. Re-running the same control config on those tasks
will see some pass purely by stochastic re-rolling — pure
regression to the mean, not a real lift. Two consequences:

1. For these slices, prefer `control: fresh` even when historical is
   available; the re-rolled fresh control absorbs the RTM noise.
2. When the DEFERRED `historical+replay` mode lands, it is the
   recommended shape for derived slices: borrow the historical
   control AND run a fresh replay of the same config to size the
   noise floor.

---

## 6. Verdict — what counts as evidence?

`tree_ops.evaluate` is the deterministic gate from "experiment
finished" to "TreeDiff applied or staged". Constants live in
[`src/openharness/lab/tree_ops.py`](../src/openharness/lab/tree_ops.py);
this section pins their semantic meaning.

### Floors (refuse to render a verdict)

| Constant | Value today | Meaning |
|----------|------------:|---------|
| `MIN_TRIALS_PER_LEG_FOR_VERDICT` | **5** | If the smallest leg has fewer trials than this, `evaluate` returns `no_op:insufficient_data`. The journal still records the trend, no tree mutation is applied. The floor is a **per-leg coverage check** — `n_trials` includes any `n_attempts > 1` re-rolls; see §4 anti-pattern about gaming this. |
| `SMALLEST_MEANINGFUL_EFFECT_PP` | **5.0** | Effect sizes below this collapse the verdict's `confidence` to ≈ 0; `lab-reflect-and-plan` uses that to decide whether to queue a wider re-run. |

### Verdict thresholds (paired with the trunk leg)

| Verdict | Conditions (all must hold) |
|---------|----------------------------|
| `Graduate` (HUMAN-CONFIRMED) | Δ pass-rate ≥ +5pp; Δ $/pass ≤ +10%; no per-cluster regression ≥ 3pp; **slice is `full-bench` or `cluster_combined:` ≥ 30% of bench** (§2 invariant). |
| `AddBranch` (AUTO) | Mutation wins ≥ +5pp on ≥ 2 distinct clusters with non-trivial `n` per cluster; trunk wins overall. The `use_when` predicate is derived from those clusters. |
| `Reject` (AUTO) | Δ pass-rate ≤ −2pp OR Δ $/pass ≥ +50%, AND no positive cluster. |
| `NoOp` (AUTO) | None of the above; `confidence` records how surprised we'd be by a different verdict on a re-run. |

### Required computations

- **Paired delta within tasks.** For each task `T` in the slice,
  compute `legA(T) - legB(T)` and aggregate. Variance of the
  paired delta is much smaller than the unpaired difference of
  averages. The §2 invariant "both legs share the task list" is
  what makes this possible.
- **Per-cluster Δpp + cluster `n`.** `add_branch` rationale must
  cite the contributing clusters with their `n` so a human reviewer
  can spot a `+100pp on n=1` claim.
- **`cluster_evidence` block** stored on every TreeDiff so the
  journal entry's `### Tree effect` is self-contained.

### Anti-patterns the verdict logic must refuse

- Drawing a verdict from a smoke run (smoke is wiring-only, see §9).
- Mutation-vs-mutation with no trunk reference (`evaluate` falls
  back to ad-hoc trunk selection but `confidence` is downweighted).
- Claiming `add_branch` on clusters whose individual `n` is below
  the floor — the per-cluster `n` is reported on the rationale so
  this is at least visible; future tightening (an explicit
  per-cluster floor) is DEFERRED.

### Verdict ↔ PR lifecycle (the trunk-must-be-on-`main` invariant)

A verdict is **only ever as real as the code that backs it**.
`lab/configs.md` and `trunk.yaml` are committed to `main` directly
by the daemon, so they describe an aspirational tree the moment a
verdict applies; the supporting code lives on the experiment branch
`lab/<slug>` until its PR merges. The methodology pins the
following invariant:

> Whenever `lab/configs.md` (or `trunk.yaml`) on `main` references
> a node, the code that defines that node must also be on `main`.

Each verdict honours the invariant differently:

| Verdict     | Code path           | When does code land on `main`? |
|-------------|---------------------|--------------------------------|
| `AddBranch` | branch is added to `## Branches` in `lab/configs.md` immediately. | `lab-finalize-pr` opens the PR with `gh pr merge --auto --squash --delete-branch` enabled, so it lands as soon as required CI passes. |
| `Graduate`  | trunk swap is **STAGED**; `trunk.yaml` and `## Trunk` only change when a human runs `lab graduate confirm <slug>`. | `lab-finalize-pr` opens the PR but **does not** enable auto-merge. `lab graduate confirm` refuses until the PR shows `state=MERGED`; the override flag `--skip-pr-merge-check` is recorded in the audit row when used. |
| `Reject` / `NoOp` | nothing on `main` references the branch. | Branch is deleted; the SHA is recorded in `tree_diffs.branch_sha` and surfaced in the journal Branch bullet (`head=<sha7>`) so the deleted work can be resurrected with `git fetch origin <sha>:retro/<slug>`. |

The autonomous daemon enforces a soft variant of the invariant by
**idling whenever any AddBranch PR is still open** (the pre-tick
check in `runner.loop`). This protects the next experiment's
preflight from forking off a `main` whose `lab/configs.md` already
describes branches whose code hasn't landed.

---

## 7. Replication — when do we re-run an entire experiment?

Distinct from §4 (which re-runs cells within an experiment),
replication re-runs the **whole experiment** with different seeds
to bound the verdict's reproducibility.

### Today (ENFORCED)

- Cell-level repeats live in §4 (`single-shot` → `paired-double`).
  This is "repetition", not "replication".
- No automatic replication is performed for any verdict.

### DEFERRED — Graduate gate

Before a `Graduate` TreeDiff is honored, `lab graduate confirm
<slug>` will require **one full-experiment replication** on the same
slice with a fresh random seed. The replication's verdict must
agree (a `Graduate` or `AddBranch` outcome with `Δ ≥ +3pp`) for
the trunk swap to commit. Currently `lab graduate confirm` is the
human gate with no replication requirement — see
[`ideas.md > ## Framework > graduate-replication-gate`](ideas.md).

---

## 8. Anti-patterns this contract forbids

The following are concrete failure modes the methodology has been
revised to prevent. Each one has a code or skill enforcement point.

| Anti-pattern | Why it's wrong | Enforcement |
|--------------|----------------|-------------|
| Drawing a verdict from a smoke run. | Smoke is 1–4 cached tasks for wiring validation; `n_trials` is far below the §6 floor. | `runner._phase_implement` smoke-block gate; `tree_ops.evaluate` floor; OPERATIONS Phase 2/3 split. |
| Cluster slice without checking cluster size. | Most `tb2` clusters have n=1; the slice silently fails the §6 floor. | `lab-design-variant` template requires citing Appendix B; `tree_ops.evaluate` floor catches it. |
| Mismatched `n_attempts` across legs. | Paired-delta variance becomes uninterpretable. | Spec's `n_attempts:` is global by construction; reviewers reject any per-leg override. |
| Comparing against a historical control without drift checks. | Silent drift on trunk / bench / verifier / model invalidates the comparison. | DEFERRED — historical control isn't enabled until the drift-guard implementation lands. |
| Reporting unpaired aggregate deltas when paired data is available. | Throws away the paired-variance reduction; inflates apparent uncertainty. | `tree_ops.evaluate` paired-delta computation. |
| `n_legs = 2` differing in > 1 axis (confounded ablation). | Verdict can't be attributed to a single change. | `lab-design-variant > ## Mutation summary` must name exactly one variable; reviewers reject otherwise. |
| Treating `task_filter:` as advisory. | Legs running on different task lists destroy the §2 paired invariant. | Spec evaluation produces one canonical task list per experiment; `phase_run` uses it for every leg. |

---

## 9. Roles and enforcement

| Role | What it owns under this contract |
|------|----------------------------------|
| [`lab-design-variant`](../.agents/skills/lab-design-variant/SKILL.md) | Produces `## Slice` (§2), `## Slice > Repetitions` (§4), `## Slice > Control` (§5) with cited evidence. Refuses if the proposed slice cannot clear the §6 floor. |
| [`lab-implement-variant`](../.agents/skills/lab-implement-variant/SKILL.md) | Realises the spec; runs the **smoke** profile for wiring validation only (§8). The smoke run never produces a verdict. |
| `phase_run` (deterministic) | Resolves the spec, applies the canonical `task_filter`, runs the **full** slice. Never runs the smoke profile. |
| [`experiment-critic`](../.agents/skills/experiment-critic/SKILL.md) | Reports per-cell pass-rates (§4), per-cluster Δpp + n (§6), paired deltas (§6). |
| `tree_ops.evaluate` | Applies §6 floors and verdict thresholds; returns the single TreeDiff justified by the evidence. |
| `lab graduate confirm <slug>` | The HUMAN gate for trunk swaps. Replication gate is DEFERRED (§7). |
| Human reviewer (`## Up next` promotion, `Graduate` confirmation) | Reads the design and audits §2-§5 declarations against this contract before the experiment runs. |

The autonomy contract: the daemon may auto-apply `AddBranch` /
`Reject` / `NoOp`. `Graduate` always requires a human (and, when
§7 lands, a passed replication). No exceptions.

---

## Appendix A — Glossary

- **Slice**: the set of `(task, attempt)` cells the experiment runs on.
- **Leg**: one agent configuration run against the slice.
- **Cell**: one `(leg, task)` combination; `n_attempts` re-rolls the same cell.
- **Trial**: one execution of a cell — the atomic row in the `trials` DB table.
- **Paired delta**: `legA(T) - legB(T)` aggregated across tasks `T` in the shared slice.
- **`use_when`**: the predicate `AddBranch` writes into `configs.md` to route trunk-vs-branch at runtime.
- **`task_features.category`**: the cluster label per task; what `cluster:` slices select on.
- **Verdict floor**: §6 `MIN_TRIALS_PER_LEG_FOR_VERDICT`. Below it, `evaluate` returns `no_op:insufficient_data`.

---

## Appendix B — Per-bench slice catalog

### `terminal-bench@2.0` cluster sizes (89 tasks total)

The contract requires citing this table whenever a `cluster:` or
`cluster_combined:` slice is declared. Re-derive with:

```sql
SELECT category, count(*) AS n_tasks
FROM task_features
GROUP BY category
ORDER BY n_tasks DESC, category;
```

| n_tasks | clusters |
|--------:|----------|
| 7 | `python_data`, `python_ml` |
| 6 | `c_build` |
| 4 | `git_workflow` |
| 3 | `regex_programming`, `system_administration`, `vm_orchestration` |
| 2 | `binary_analysis`, `bioinformatics_primer_design`, `cryptanalysis`, `polyglot_programming`, `python_pytorch_distributed`, `scientific_computing` |
| 1 | 42 clusters (every other category) |

Implication for `cluster:` slices: only `python_data` and `python_ml`
clear the §6 floor as single-cluster slices at `n_attempts=1`. Any
other cluster slice **must** use `cluster_combined:` (DEFERRED
shape — see [`ideas.md > ## Framework`](ideas.md)) or
`paired-double` repetitions, and must report total `n_trials/leg`
in the design.
