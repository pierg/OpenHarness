---
name: lab-graduate-component
description: >
  Confirm a staged trunk swap. Use when the user says "graduate X",
  "promote X to trunk", "X became the new trunk", "confirm the
  graduate verdict for X", "swap trunk to X". Edits trunk.yaml,
  lab/configs.md (## Trunk + ## Branches), lab/components.md (bumps
  atoms in the new trunk to `validated`), lab/ideas.md (move to
  ## Graduated), and records the swap in the trunk_changes audit
  log. Refuses unless `lab tree apply` has previously staged a
  Graduate diff for the named slug. Companion skills: lab,
  lab-run-experiment, lab-plan-next, lab-reflect-and-plan.
---

# Lab — Graduate Component (Confirm Trunk Swap)

In the tree+journal model, "graduate" means **swap the trunk**.
The daemon never does this on its own — `tree_ops.evaluate` may
emit a `Graduate` verdict, `lab tree apply` writes it as a *staged*
`### Tree effect` block (Verdict: **Graduate** — staged, awaiting
human), and the human (you, via this skill) decides whether to
actually rotate the trunk.

This is the **only** asymmetric action in the autonomous loop —
everything else (AddBranch / Reject / NoOp) auto-applies. The
trunk swap is gated on a human because:

1.  Every future paired ablation is "trunk + delta", so swapping
    the trunk is the most consequential single mutation in the
    system.
2.  The staged journal entry already contains the full evidence
    (`### Mutation impact`, `### Failure modes`, `### Tree effect`)
    so you can audit the verdict before confirming.

## When to Use

-   User says "graduate X", "promote X to trunk", "swap the trunk
    to X", "confirm the graduate verdict for `<slug>`".
-   You see a staged `### Tree effect` block in `lab/experiments.md`
    that reads `Verdict: **Graduate** — staged, awaiting human` and
    the user wants to act on it.

Do **not** use this skill when:

-   The verdict is anything other than `Graduate`. AddBranch /
    Reject / NoOp are already auto-applied by the daemon.
-   No `lab tree apply` has been run for the slug yet → run
    `uv run lab tree apply <slug>` first.
-   The user is proposing or running an experiment → that's
    `lab-propose-idea` / `lab-run-experiment`.

## What "graduate" actually does

`uv run lab graduate confirm <slug> --applied-by human:<name>`:

1.  Re-runs `tree_ops.evaluate(<instance_id>)`; refuses if the
    fresh verdict isn't still `Graduate` (drift guard).
2.  Copies `src/openharness/agents/configs/<target_id>.yaml` over
    `trunk.yaml` (preserving the trunk-banner header comment).
3.  Updates `lab/configs.md > ## Trunk` to point at the new target
    (alias of `<target_id>`); moves the previous trunk into
    `## Branches` (or `## Rejected` if the same diff also rejects
    it).
4.  Bumps every component in the new trunk to `validated` in
    `lab/components.md` (forward-only — already-validated rows are
    untouched).
5.  Inserts an audit row into the `trunk_changes` table:
    `(at_ts, from_id, to_id, reason, applied_by, instance_id)`.
6.  Updates the staged `### Tree effect` block in
    `lab/experiments.md` to read `Verdict: **Graduate** —
    confirmed by <applied_by> at <ts>`.

## Instructions

### 1. Locate the staged graduate

```bash
uv run lab tree show
uv run lab query "
  SELECT instance_id, slug, target_id, applied, applied_by
  FROM tree_diffs WHERE kind = 'graduate' AND applied = FALSE"
```

If nothing comes back, refuse: there is no staged graduate. Tell
the user the most recent verdicts:

```bash
uv run lab query "
  SELECT slug, kind, target_id, applied, applied_by
  FROM tree_diffs ORDER BY applied_at DESC NULLS LAST LIMIT 10"
```

If a staged graduate exists, show the user the journal entry's
`### Mutation impact` and `### Tree effect` blocks and ask for an
explicit go/no-go before confirming.

### 2. Verify the experiment PR is merged

The trunk swap rotates `trunk.yaml` and bumps components in
`lab/components.md` to `validated` — both reference code that
must already live on `main`. **Merge the PR first** (the one
recorded in the journal entry's Branch bullet, also visible via
`gh pr view`). When the PR has merged, the
`graduate confirm` gate flips green automatically.

If you're certain the PR is already merged but `gh pr view`
disagrees (e.g. the URL is stale, or the PR was merged via the
GitHub UI without auto-merge), pass `--skip-pr-merge-check` —
this records `pr_merge_check_skipped=true` in the audit row so
future audits can attribute the bypass.

### 3. Confirm the swap

```bash
uv run lab graduate confirm <slug> \
  --applied-by "human:$USER" \
  [--reason "<one line — why we're rotating>"]
```

The CLI refuses if:

-   The slug doesn't resolve to an instance.
-   The fresh verdict isn't `Graduate` (e.g. a rerun of
    `experiment-critic` flipped it to `NoOp`).
-   The experiment's PR is not yet merged (see step 2 above).
    Override with `--skip-pr-merge-check` only when the PR was
    merged out-of-band.
-   `<target_id>.yaml` doesn't exist under
    `src/openharness/agents/configs/`.

If it refuses, surface the error verbatim — do not "fix" the
tree by hand.

### 4. Move the idea to `## Graduated`

If the swap was driven by a previously-proposed idea (the common
case), move that idea to `## Graduated`:

```bash
uv run lab idea move <idea-id> graduated \
  --cross-ref "**Graduated as trunk:** [\`<target-id>\`](configs.md#trunk)"
```

This is mechanically distinct from the trunk swap itself — the
ideas file tracks lifecycle of the *proposal*, not of the trunk.

### 5. Sanity checks

The trunk swap is a config rotation, not a code change, so the
test suite shouldn't be sensitive — but the next experiment will
be. Run:

```bash
# Quick: show the resolved trunk agent.
uv run python -m openharness.agents.components --validate
uv run python -c "
from openharness.agents.config import load_agent_config
print(load_agent_config('trunk'))
"

# (optional) confirm the trunk_changes audit row landed.
uv run lab query "
  SELECT at_ts, from_id, to_id, reason, applied_by
  FROM trunk_changes ORDER BY at_ts DESC LIMIT 3"
```

### 6. Report

Finish with:

-   Old trunk → new trunk (ids).
-   The journal entry that justified it (with link).
-   The `applied_by` and `at_ts` from `trunk_changes`.
-   Files touched: `trunk.yaml`, `lab/configs.md`, `lab/components.md`
    (status bumps), `lab/experiments.md`, `lab/ideas.md` (if step 3
    ran).
-   The next concrete step (usually: re-run any pending paired
    ablations against the new trunk — `lab-reflect-and-plan` will
    have noticed the swap and may have already proposed
    `### Suggested` follow-ups).

Do **not**:

-   Edit `lab/experiments.md > ### Tree effect` by hand —
    `graduate confirm` is the only writer.
-   Edit other journal entries — they're append-only history.
-   Touch `roadmap.md` — that's `lab-plan-next`'s job.
-   Commit or push unless the user asks.

## Examples

### Example: confirm a staged graduate

Input: "Confirm the graduate for `loop-guard-tb2-paired`."

Output:

1.  `uv run lab query "SELECT slug, target_id, applied FROM
    tree_diffs WHERE slug = 'loop-guard-tb2-paired'"` → confirms a
    staged `Graduate` for `target_id='loop-guard'`.
2.  Show the user the `### Mutation impact` and `### Tree effect`
    blocks of the journal entry.
3.  On user confirmation: `uv run lab graduate confirm
    loop-guard-tb2-paired --applied-by human:alice --reason
    "+12.4 pp pass-rate at 0.8x cost on tb2"`.
4.  `uv run lab idea move loop-guard graduated --cross-ref ...`.
5.  Sanity-check the new trunk loads.
6.  Report.

### Example: refuse — no staged graduate

Input: "Graduate `episodic-memory`."

Output:

1.  `uv run lab query` shows no `tree_diffs` row for any
    `episodic-memory*` slug.
2.  Refuse: "No staged graduate for `episodic-memory`. The
    autonomous loop hasn't surfaced one. Run an experiment first
    (`lab-run-experiment`), then `tree apply` will compute the
    verdict; if it's `Graduate`, this skill can confirm it."

### Example: refuse — verdict drifted

Input: "Confirm the graduate for `<slug>`."

Output:

1.  `graduate confirm` exits non-zero with: "Fresh verdict is
    `no_op` (delta_pp dropped to +0.4 after critic re-spawn);
    refusing to swap trunk."
2.  Surface the error to the user. Suggest re-running
    `experiment-critic` or `tree apply --dry-run` to inspect the
    new verdict.
