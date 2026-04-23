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

The orchestrator passes you via CLI arguments:

- `slug` (positional) — also the branch name (`lab/<slug>`).
- `--worktree=<path>` — absolute path to
  ``../OpenHarness.worktrees/lab-<slug>/``. **All edits and shell
  commands MUST run inside this worktree.** Editing the parent
  repo is a hard error; the orchestrator will reject the phase.
- `--design-path=<path>` — absolute path to
  ``runs/lab/state/<slug>/design.md`` (in the parent repo's
  gitignored area). Read-only from your perspective.
- `--implement-json=<path>` — absolute path to write your
  ``implement.json`` to. The orchestrator pre-creates the
  directory; you just write the file.
- `--base-sha=<sha>` — the merge-base your branch was forked from.
  Use it to compute commit list and files-touched (``git rev-list
  --reverse <base-sha>..HEAD`` and ``git diff --name-only
  <base-sha>..HEAD``).

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
  "smoke": {
    "spec_name": "<same as top-level spec_name>",
    "profile": "smoke",
    "instance_id": "<run id of the smoke exec, e.g. 20260422-153012>",
    "run_dir": "runs/experiments/<spec>-smoke-<ts>",
    "legs": [
      {"leg_id": "basic",     "trials_run": 2, "trials_passed": 1, "errored": false},
      {"leg_id": "<variant>", "trials_run": 2, "trials_passed": 0, "errored": false}
    ],
    "errors": []
  },
  "files_touched": ["experiments/foo.yaml", "src/.../bar.py"]
}
```

The orchestrator reads:

- `spec_name` and `profile` (must be `null`) to launch phase 3 on
  the **full** slice. Phase 3 never runs the smoke profile.
- `validations` to refuse the run phase if any check failed.
- `smoke.errors` to refuse the run phase if non-empty (i.e. the
  smoke run errored). `smoke.legs[*].trials_passed` is informational
  only — passing nothing on smoke is still allowed.

If `smoke` is missing or `errors` is non-empty, the implement phase
is marked `failed` and the run phase is **not** spawned.

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
4. **Run static validations.** From inside the worktree:

   ```bash
   uv run lab components --validate    # if components.md changed
   uv run plan <spec-name>             # always
   pytest tests/<area>                 # if tests exist for the change
   ```

   If a check fails, fix it before committing. Record each
   check's result in `implement.json`. Skipping a check requires a
   one-line reason in the JSON value (e.g. ``"skipped: no
   components changes"``).

5. **Run the smoke validation.** From inside the worktree:

   ```bash
   uv run exec <spec-name> --profile smoke
   ```

   This must complete without an uncaught Python exception, every
   leg must reach a non-`ERRORED` status, and at least one trial
   per leg must complete (pass or fail). **Pass-rate is not a
   criterion — smoke is wiring validation only, never a verdict**
   (see [`lab/METHODOLOGY.md`](../../../lab/METHODOLOGY.md) §8 / §9).

   Inspect the resulting `runs/experiments/<spec>-smoke-<ts>/`
   directory:

   - `summary.json` (or equivalent leg roll-up) — confirms every
     leg has `n_trials >= 1` and no leg has `status == "ERRORED"`.
   - `events.jsonl` — grep for `"event": "trial_failed"` with an
     uncaught `Exception:` payload; those count as crashes.

   Populate the `smoke` block of `implement.json`. If the smoke
   run errored, **fix the bug, drop a fresh commit, and re-run**.
   Do not paper over crashes; the run phase will be much more
   expensive and the failure will repeat.

6. **Commit.** Use small, focused commits with a structured prefix:

   ```
   lab(<slug>): <one-line summary>

   <optional body — what changed and why>
   ```

   Multiple commits are fine; the implementer keeps the history
   readable. Do NOT push from this skill; phase 5
   (`lab-finalize-pr`) handles push + PR.

7. **Write `implement.json`** with the structure above. Use
   ``git rev-list --reverse <base-sha>..HEAD`` (where ``<base-sha>``
   is in ``phases.json`` under the preflight payload) to enumerate
   the commit shas. Use ``git diff --name-only <base-sha>..HEAD``
   for ``files_touched``. The `smoke` block is **mandatory** — the
   orchestrator refuses to advance to phase 3 if it's missing.

8. **Surface the result** in your final reply: the commit shas, the
   validation outcomes, the smoke instance id, and the spec name
   the run phase will use.

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
- **Don't skip the smoke run.** It costs ~$0.10–$1 and catches
  the failure modes the static checks can't (LLM provider auth,
  spec/agent integration, runtime component crashes). The full
  slice is 10–100× more expensive — never let the orchestrator
  spawn it on an unsmoked variant.
- **Don't make smoke do too much.** Smoke is *validation*, not
  *experimentation*. Two cheap tasks per leg is the right order
  of magnitude. If you find yourself wanting "just one more task"
  to be confident in the verdict, that belongs in the design's
  `## Slice > Full` definition, not in the smoke profile. The
  separation is enforced by [`lab/METHODOLOGY.md`](../../../lab/METHODOLOGY.md)
  §8 (forbidden anti-pattern: "drawing a verdict from a smoke run").
- **Don't fix unrelated bugs.** Even if you spot one. Note it in
  the journal under `### Linked follow-ups` later (or as a fresh
  `lab-propose-idea`), but keep this branch focused.
