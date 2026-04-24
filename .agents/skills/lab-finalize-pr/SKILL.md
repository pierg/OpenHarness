---
name: lab-finalize-pr
description: >
  Phase 6 of the autonomous lab pipeline. After critique and replan
  have finished on the experiment worktree, reconcile the branch
  against `main`, create the required PR artifact(s), merge the
  experiment outcome back to `main`, update the journal Branch
  bullet, and write `runs/lab/state/<slug>/finalize.json`. Use when
  the orchestrator daemon invokes finalize for a slug whose
  `phases.json` shows `finalize: pending`.
---

# Lab — Finalize and Merge

This skill closes the loop for one experiment. Success means the
experiment's durable outcome is merged to `main`. Do not return a
successful finalize unless that happened.

## Pipeline contract

The new lab flow is:

1. parent repo starts on `main`
2. preflight creates `lab/<slug>` worktree from synced `main`
3. design / implement / run / critique / replan all happen on that worktree
4. finalize turns the outcome into 1 or more PR artifacts
5. finalize merges those PRs back to `main`
6. only then may the daemon advance to the next roadmap entry

There is no "open a PR and let the next tick wait on it" mode, and
there is no human `graduate confirm` gate in the normal path.

## Inputs

The orchestrator passes:

- `slug`
- `worktree`
- `branch` (`lab/<slug>`)
- `base-branch` (`main`)
- `verdict` (`add_branch`, `graduate`, `reject`, `no_op`)
- `instance-id`
- `finalize-json`
- zero or more `--lab-commit=<sha>` values
- optional repair arguments

The `--lab-commit` values are the worktree commits that only touch
`lab/` and belong in a metadata-only PR when the implementation
branch itself should not land.

## Output contract

You must write `finalize-json` with at least:

```json
{
  "merged": true,
  "cleanup_worktree": true
}
```

Add these keys when available:

- `pr_url`: the canonical PR URL when there was exactly one PR
- `pr_urls`: list of PR URLs when there were multiple
- `merged_sha`: merge commit SHA or resulting `main` SHA
- `discarded_sha`: HEAD of the discarded implementation branch for `reject` / `no_op`
- `mode`: `single-pr` or `metadata-only`
- `reason`: short one-line summary

## Decide the shape

### Accepted outcome: `add_branch` or `graduate`

Preferred path: **one PR** from `lab/<slug>`.

That PR should contain:

- the accepted implementation changes
- the `lab/` changes written during run / critique / replan

### Rejected or no-op outcome: `reject` or `no_op`

Preferred path: **one metadata-only PR**.

Do not merge rejected implementation code just to satisfy the "always
merge a PR" rule. Instead:

1. capture the implementation branch HEAD as `discarded_sha`
2. create a fresh branch from current `origin/main`
3. cherry-pick only the provided `--lab-commit` SHAs onto that branch
4. open and merge that metadata-only PR
5. record the PR URL plus `discarded_sha` on the journal Branch bullet

Branch naming convention for the metadata path:

- implementation branch remains `lab/<slug>`
- metadata branch should be `labmeta/<slug>`

## Required steps

### 1. Inspect and sync

Inside `worktree`:

```bash
git status --short
git fetch origin main
```

Fail if the worktree has unexpected uncommitted changes.

### 2. Reconcile with latest `main`

For the normal single-PR path:

```bash
git rebase origin/main
```

If conflicts happen, resolve them autonomously. Preserve the
experiment's intended outcome:

- keep accepted code + `lab/` changes for `add_branch` / `graduate`
- keep only `lab/` changes for metadata-only `reject` / `no_op`

### 3. Push the source branch

Single-PR path:

```bash
git push -u origin lab/<slug>
```

Metadata-only path:

```bash
git checkout -B labmeta/<slug> origin/main
# cherry-pick each --lab-commit sha
git push -u origin labmeta/<slug>
```

### 4. Open the PR

Use a title shaped like:

```text
lab(<slug>): <one-line outcome summary>
```

The body should be short and operational:

- what changed
- verdict
- run path `runs/experiments/<instance-id>/`
- journal entry link
- whether this is metadata-only

For metadata-only reject/no-op PRs, say explicitly that the
implementation branch was discarded and only the `lab/` outcome is
being merged.

### 5. Update the journal Branch bullet

Use the deterministic CLI helper.

Accepted single-PR path:

```bash
uv run lab experiments set-branch <slug> \
  --branch lab/<slug> \
  --pr-url <pr-url>
```

Metadata-only reject/no-op path:

```bash
uv run lab experiments set-branch <slug> \
  --branch lab/<slug> \
  --pr-url <pr-url> \
  --rejected-reason "<verdict>: <one-line evidence>" \
  --discarded-sha "<discarded_sha>"
```

### 6. Merge before returning

You are responsible for getting the PR merged to `main`.

Preferred command:

```bash
gh pr merge <pr-url> --squash --delete-branch
```

If checks are still pending and auto-merge is required, enable it and
poll until GitHub reports `state=MERGED`. Do not exit early with an
open PR.

If GitHub reports conflicts, fix them on the source branch, push
again, and complete the merge.

## Refuse when

- the worktree is dirty in a way that doesn't belong to the experiment
- `gh` auth is missing
- the PR cannot be created or merged after reasonable autonomous conflict resolution
- `finalize-json` cannot be written

## Anti-patterns

- Do not leave an open PR for the daemon to wait on later.
- Do not merge rejected implementation code to `main`.
- Do not modify experiment results or rerun the experiment here.
- Do not skip the journal Branch update.
