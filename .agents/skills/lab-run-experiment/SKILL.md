---
name: lab-run-experiment
description: >
  DEPRECATED. The single mega-skill that used to scaffold + implement
  + run + critique an experiment has been split into a 6-phase
  deterministic pipeline (preflight, design, implement, run, critique,
  finalize). Use the orchestrator daemon (`uv run lab daemon start`)
  for autonomous runs, or the new per-phase skills if you're driving
  one phase by hand: `lab-design-variant`, `lab-implement-variant`,
  `lab-finalize-pr`. The deterministic phases (preflight, run,
  critique) live in Python and have no skill — invoke them via
  `uv run lab preflight run <slug>` or by running the daemon.
---

# Lab — Run Experiment (DEPRECATED)

This skill no longer exists as a runnable agent. The autonomous lab
loop now uses a **6-phase pipeline** driven by
`src/openharness/lab/runner.py`, with each phase resumable
independently via `runs/lab/state/<slug>/phases.json`:

| Phase | Owner | Sandbox | Skill |
|-------|-------|---------|-------|
| 0. preflight | deterministic (`runner.py` + `preflight.py`) | n/a | — |
| 1. design    | codex spawn | read-only | [`lab-design-variant`](../lab-design-variant/SKILL.md) |
| 2. implement | codex spawn | workspace-write (worktree only) | [`lab-implement-variant`](../lab-implement-variant/SKILL.md) |
| 3. run       | deterministic (`runner.py` + `phase_run.py`) | n/a | — |
| 4. critique  | deterministic (`runner.py`; spawns trial/experiment-critic) | n/a | — |
| 5. finalize  | codex spawn | workspace-write (git + gh) | [`lab-finalize-pr`](../lab-finalize-pr/SKILL.md) |

If you used to invoke `lab-run-experiment`:

- **For autonomous runs**: queue the entry on `lab/roadmap.md`
  (via `lab-plan-next`) and `uv run lab daemon start`.
- **For ad-hoc runs**: invoke the per-phase skills in order, or
  run `uv run lab preflight run <slug>` then drive
  `lab-implement-variant` / `lab-finalize-pr` by hand.

See `lab/OPERATIONS.md` for the full operator's guide.
