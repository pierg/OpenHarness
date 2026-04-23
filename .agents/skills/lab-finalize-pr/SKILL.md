---
name: lab-finalize-pr
description: >
  Phase 5 of the autonomous lab pipeline. After the experiment has
  run and the deterministic critique + tree-apply phase has produced
  a verdict, push the experiment branch and open (or skip) a pull
  request, then update the journal entry's Branch bullet to point at
  the PR. Use when invoked by the orchestrator daemon for a slug
  whose pipeline state shows `finalize: pending`, or when the
  operator says "finalize the PR for X" / "open the PR for X".
  Companion skills: lab, lab-design-variant, lab-implement-variant.
---

# Lab — Finalize PR

Close out an experiment by deciding whether to open a pull request,
doing it (or not) deterministically, and recording the outcome on
the journal entry.

## When to Use

- The orchestrator daemon spawns you for a slug whose
  ``runs/lab/state/<slug>/phases.json`` has ``finalize: pending``,
  ``critique: ok``, and a verdict recorded in the
  ``critique`` payload.
- The operator says "finalize X" with a closed-out experiment that
  hasn't had its branch pushed / its PR opened yet.

Do **not** use this skill for:

- The actual code changes → `lab-implement-variant` (phase 2).
- Filling the journal narrative sections → `experiments synthesize`
  (deterministic, runs in phase 4).
- Confirming a graduate verdict → `lab-graduate-component`.

## Inputs

The orchestrator passes you (via the prompt):

- `slug` — also the branch name (`lab/<slug>`).
- `worktree` — absolute path to the per-experiment worktree (still
  exists at this point).
- `verdict` — one of:
  - `add_branch` → open a PR, mark as informational.
  - `graduate` → open a PR, mark as graduation-candidate; the
    operator runs `lab-graduate-component` to actually swap trunk.
  - `reject` → do NOT open a PR; mark the journal entry as
    "branch not pushed (verdict: reject)" and schedule worktree
    cleanup.
  - `noop` → do NOT open a PR; mark "no measurable effect" and
    schedule worktree cleanup.
- `verdict_evidence` — a short blurb (rendered into the PR body) that
  the critique phase wrote into the verdict payload.
- `instance_id` — the run id, so the PR body can link to it.

## Output

Depending on `verdict`:

| Verdict | Side effects |
|---|---|
| `add_branch` | (a) `git push origin lab/<slug>` from inside the worktree. (b) `gh pr create …` with the title and body specified below. (c) `gh pr merge <url> --auto --squash --delete-branch` so CI gates the merge and keeps `main` and `lab/configs.md` in lock-step. (d) `uv run lab experiments set-branch <slug> --branch lab/<slug> --pr-url <url>`. |
| `graduate` | (a) `git push origin lab/<slug>`. (b) `gh pr create … --label graduate-candidate`. (c) **Do NOT** enable auto-merge — `lab graduate confirm` will refuse until a human merges the PR (see [`lab/METHODOLOGY.md`](../../../lab/METHODOLOGY.md) §6). (d) `uv run lab experiments set-branch <slug> --branch lab/<slug> --pr-url <url>`. |
| `reject` / `noop`        | (a) Capture the branch HEAD: `discarded_sha=$(git rev-parse HEAD)` from inside the worktree. (b) `uv run lab experiments set-branch <slug> --branch lab/<slug> --rejected-reason "<verdict>: <one-line evidence>" --discarded-sha "$discarded_sha"`. (c) Mark the worktree for cleanup by writing ``runs/lab/state/<slug>/finalize.json`` with ``{"cleanup_worktree": true}``. The orchestrator does the actual `git worktree remove` after this skill exits. |

The orchestrator marks `finalize: ok` once `set-branch` has run and
either the PR url is recorded or the cleanup flag is set.

## Instructions

### For `add_branch` / `graduate`

1. **Cd into the worktree.** All git operations run from there.
2. **Verify the branch is healthy.** Run `git status` (must be
   clean — implement phase committed everything) and
   `git log --oneline <base-sha>..HEAD` to enumerate commits.
3. **Push the branch:**

   ```bash
   git push -u origin lab/<slug>
   ```

   If the push fails (auth, network), refuse with the error so
   the orchestrator records a failure rather than silently
   leaving an unpushed branch.

4. **Open the PR.** Title format:

   ```
   lab(<slug>): <one-line mutation summary>
   ```

   Body format (use a heredoc to preserve newlines). Include the
   verdict, the evidence, the methodology contract that justified
   it, and the cluster_evidence table from the TreeDiff (if the
   verdict surfaced one):

   ````markdown
   <one-paragraph description of what this branch does, copied or
   summarised from the journal entry's Mutation: bullet>

   **Verdict:** `<verdict>` — <verdict_evidence>

   **Methodology:** [`lab/METHODOLOGY.md` §6 — Verdict thresholds](../blob/<base-branch>/lab/METHODOLOGY.md#6-verdict-thresholds)

   **Cluster evidence** (if present in the TreeDiff):

   | cluster | n | Δ pass-rate | source |
   |---|---|---|---|
   | python_data | 7 | +14pp | runs/experiments/<instance_id>/critic/comparisons/* |
   | …  | … | … | … |

   **Run:** `runs/experiments/<instance_id>/`

   **Journal entry:** [`lab/experiments.md` — <slug>](../blob/<base-branch>/lab/experiments.md#<anchor-for-slug>)

   ---

   Auto-generated by the OpenHarness lab orchestrator.
   ````

   Use:

   ```bash
   gh pr create --title "lab(<slug>): <…>" --body "$(cat <<'EOF'
   …body…
   EOF
   )"
   ```

   For `verdict=graduate`, also pass `--label graduate-candidate`
   so the human review knows trunk swap is queued.

5. **For `add_branch` only — enable auto-merge.** `lab/configs.md`
   on `main` already references this branch as a node in the
   configuration tree; if the code never lands the tree diverges
   from the reachable graph. Auto-merge keeps them in lock-step:

   ```bash
   gh pr merge <pr-url> --auto --squash --delete-branch
   ```

   GitHub merges as soon as required CI checks pass. **Do NOT**
   enable auto-merge for `verdict=graduate` — `lab graduate
   confirm` requires the PR be merged FIRST (manually, after
   human review), then it swaps `trunk.yaml`.

6. **Record the PR URL** in the journal AND the DB cache:

   ```bash
   uv run lab experiments set-branch <slug> \
     --branch lab/<slug> \
     --pr-url <https://github.com/…/pull/N>
   ```

   This rewrites the entry's ``**Branch:**`` bullet to
   ``Branch: [`lab/<slug>`](<pr-url>)`` and mirrors the URL into
   `tree_diffs.pr_url` so the web UI and the daemon's
   block-on-unmerged check can find it.

### For `reject` / `noop`

1. **Do NOT push.** The branch stays local; the orchestrator will
   delete it shortly after this skill returns.
2. **Capture the branch HEAD** so the deleted branch can be
   resurrected later for forensic review:

   ```bash
   discarded_sha=$(git -C <worktree> rev-parse HEAD)
   ```

3. **Update the journal AND the DB cache:**

   ```bash
   uv run lab experiments set-branch <slug> \
     --branch lab/<slug> \
     --rejected-reason "<verdict>: <one-line evidence>" \
     --discarded-sha "$discarded_sha"
   ```

   The journal renders `Branch: lab/<slug> — not opened
   (<verdict>: …; head=<short-sha>)`. The full SHA goes into
   `tree_diffs.branch_sha` so a human can later run
   `git fetch origin <sha>:retro/<slug>` to inspect what was
   tried.

4. **Write `finalize.json`:**

   ```json
   {"cleanup_worktree": true, "reason": "<verdict>"}
   ```

## Sandbox & guardrails

- You run with **workspace-write** sandbox: you may run `git`,
  `gh`, and `uv run lab experiments set-branch` freely, but you
  may NOT make code changes here. Implementation drift is
  phase 2's job, not yours.
- You may NOT delete the worktree yourself; the orchestrator
  controls it via the cleanup flag so the audit trail stays
  consistent (the daemon writes a structured `tick history`
  entry that includes the cleanup decision).
- Refuse and exit cleanly if the verdict is unrecognised — the
  orchestrator only ever passes the four documented values.

## Anti-patterns

- **Don't write narrative**. The PR body should reference the
  journal entry, not duplicate it. Anyone who needs the full
  details follows the link.
- **Don't open a PR for `reject`/`noop`.** Open PRs are noise
  for verdicts that the orchestrator has already decided not to
  pursue. The branch + worktree are deliberately discarded so
  future searches don't surface dead ends.
- **Don't enable auto-merge on `graduate` PRs.** Graduate is the
  ONE asymmetric verdict in the loop — it changes trunk for
  every future experiment. A human runs `lab graduate confirm
  <slug>`, which (a) requires the PR be merged first, (b) swaps
  `trunk.yaml`, (c) writes a `trunk_changes` audit row.
  Auto-merging the PR would skip the human review the workflow
  exists to enforce.
- **Don't skip `--discarded-sha` for `reject`/`noop`.** The SHA
  is the only audit trail left after the worktree is wiped — it
  lets a curious human (or a future cross-experiment-critic)
  fetch the branch back and look at what was actually tried.
- **Don't re-run the experiment from this skill.** If the run
  artefacts are missing, refuse — that's a sign the orchestrator
  spawned this phase out of order, not something this skill
  should fix.
