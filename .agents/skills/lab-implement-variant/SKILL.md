---
name: lab-implement-variant
description: >
  Phase 2 of the autonomous lab pipeline. Read the design doc
  produced by `lab-design-variant`, implement the changes inside the
  experiment worktree, validate them with deterministic checks, and
  commit. Use when invoked by the orchestrator daemon for a slug
  whose pipeline state shows `implement: pending`, or when the
  operator says "implement the variant for X" with a design already
  on disk. Companion skills: lab, lab-design-variant, lab-finalize-pr,
  lab-run-experiment (deprecated).
---

# Lab — Implement Variant

Turn `runs/lab/state/<slug>/design.md` into actual committed code in
the experiment worktree. This is the only phase with write access to
source files, so the discipline matters: stay strictly within the
design's "Files to touch" list, validate every change, and commit
small.

## When to Use

- The orchestrator daemon spawns you for a slug whose
  ``runs/lab/state/<slug>/phases.json`` has ``implement: pending``
  and ``design: ok``.
- The operator hands you a slug + an existing `design.md` and says
  "implement this".

Do **not** use this skill for:

- Producing the design doc itself → `lab-design-variant`.
- Running the experiment → the orchestrator does that
  deterministically as soon as you mark this phase done.
- Opening a PR → `lab-finalize-pr` (phase 5).

## Inputs

The orchestrator passes you (via the prompt):

- `slug` — also the branch name (`lab/<slug>`).
- `worktree` — absolute path to
  ``../OpenHarness.worktrees/lab-<slug>/``. **All edits and shell
  commands MUST run inside this worktree.** Editing the parent
  repo is a hard error; the orchestrator will reject the phase.
- `design_path` — absolute path to
  ``runs/lab/state/<slug>/design.md`` (in the parent repo's
  gitignored area).

## Output

Two artifacts:

1. One or more git commits on branch `lab/<slug>` in the worktree.
2. A JSON summary at ``runs/lab/state/<slug>/implement.json``:

```json
{
  "spec_name": "<spec name in experiments/>",
  "profile": null,
  "commits": ["<sha1>", "<sha2>"],
  "validations": {
    "components_validate": "ok | skipped | failed: <msg>",
    "plan": "ok | failed: <msg>",
    "pytest": "ok | skipped | failed: <msg>"
  },
  "files_touched": ["experiments/foo.yaml", "src/.../bar.py"]
}
```

The orchestrator reads `spec_name` and `profile` to launch phase 3,
and reads `validations` to refuse the run if anything failed.

## Instructions

1. **Cd into the worktree.** Every shell command must use
   `working_directory: <worktree>`. Confirm with
   `git rev-parse --show-toplevel` and `git branch --show-current`
   before doing anything else; abort if it doesn't match the
   expected worktree path and `lab/<slug>`.
2. **Read the design doc** in full. The "Files to touch",
   "Implementation sketch", and "Validation" sections are your
   contract.
3. **Implement the change.** Stay within the listed files. If you
   discover a missing file or a wrong path, update `design.md`
   first (it's read-only from your perspective normally, but you
   may patch it inside `runs/lab/state/<slug>/` if the design
   itself was wrong) and note the deviation in `implement.json`.
4. **Run validations.** From inside the worktree:

   ```bash
   uv run lab components --validate    # if components.md changed
   uv run plan <spec-name>             # always
   pytest tests/<area>                 # if tests exist for the change
   ```

   If a check fails, fix it before committing. Record each
   check's result in `implement.json`. Skipping a check requires a
   one-line reason in the JSON value (e.g. ``"skipped: no
   components changes"``).
5. **Commit.** Use small, focused commits with a structured prefix:

   ```
   lab(<slug>): <one-line summary>

   <optional body — what changed and why>
   ```

   Multiple commits are fine; the implementer keeps the history
   readable. Do NOT push from this skill; phase 5
   (`lab-finalize-pr`) handles push + PR.

6. **Write `implement.json`** with the structure above. Use
   ``git rev-list --reverse <base-sha>..HEAD`` (where ``<base-sha>``
   is in ``phases.json`` under the preflight payload) to enumerate
   the commit shas. Use ``git diff --name-only <base-sha>..HEAD``
   for ``files_touched``.

7. **Surface the result** in your final reply: the commit shas, the
   validation outcomes, and the spec name the run phase will use.

## Sandbox & guardrails

- You run with **workspace-write** sandbox scoped to the worktree
  path. Editing files outside the worktree (including `lab/*.md`
  in the parent repo) is the orchestrator's job, not yours.
- You may run `uv run …` and `pytest` freely inside the worktree.
- You may NOT push (`git push`) or open PRs. Phase 5 owns the
  remote interaction.
- If you cannot complete the implementation (missing context,
  ambiguous design, validation that fails repeatedly), refuse
  with a clear one-paragraph reason and exit without committing.
  The orchestrator counts the refusal toward the auto-demote
  threshold and (after enough refusals) demotes the entry.

## Anti-patterns

- **Don't drift.** A design.md says "touch 3 files"; you touch 7.
  This is the most common and most damaging failure mode — it
  contaminates the experiment with off-topic noise. If the design
  is genuinely incomplete, refuse and let the operator
  re-run `lab-design-variant`.
- **Don't skip validation.** A failing `uv run plan` after your
  edits is a guaranteed run-phase crash; catch it here.
- **Don't fix unrelated bugs.** Even if you spot one. Note it in
  the journal under `### Linked follow-ups` later (or as a fresh
  `lab-propose-idea`), but keep this branch focused.
