---
name: lab-run-experiment
description: >
  DEPRECATED. The old one-shot experiment skill has been replaced by
  a resumable 7-phase pipeline: preflight, design, implement, run,
  critique, replan, finalize. Use the daemon (`uv run lab daemon
  start`) or the per-phase skills instead.
---

# Lab — Run Experiment (Deprecated)

Do not use this skill for new work.

The orchestrator now owns a 7-phase state machine in
`src/openharness/lab/runner.py`, with durable phase state in
`runs/lab/state/<slug>/phases.json`.

Current phase ownership:

| Phase | Owner |
|-------|-------|
| `preflight` | deterministic Python |
| `design` | `lab-design-variant` |
| `implement` | `lab-implement-variant` |
| `run` | deterministic Python |
| `critique` | deterministic Python + critic skills |
| `replan` | `lab-replan-roadmap` |
| `finalize` | `lab-finalize-pr` |

If the user wants an experiment to run, use:

- `lab-operator` for the daemon path
- the per-phase skills for manual intervention
