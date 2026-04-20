---
name: trial-critic
description: >
  Analyse a single experiment trial directory and persist a structured
  critique to the lab DB. Use when the user says "critique this trial",
  "what did the agent actually do on X", "explain why this trial
  passed/failed", points at a `runs/experiments/<id>/legs/.../<task>__*/`
  directory and asks for an evaluation, or when invoked autonomously
  by the orchestrator daemon. Reads the trial's trajectory, messages,
  events, and verifier output; writes one row to the lab DB's
  `trial_critiques` table via `uv run lab insert-critique`. Companion
  skills: experiment-critic, cross-experiment-critic, task-features.
---

# Trial Critic

Take one trial directory under `runs/experiments/<instance>/legs/<leg>/harbor/<inst>-<leg>/<task>__<id>/`
and produce a precise, evidence-grounded critique of what the agent
did, why it succeeded or failed, and what concretely could be
different next time. The critique is the per-trial input that the
`experiment-critic` and `cross-experiment-critic` skills aggregate
across legs and across experiments.

You are an autonomous codex agent. Use your tools (`read_file`,
`grep`, `bash`) to walk the artifacts directly. Never make claims
the artifacts don't support — the critique's value is its
groundedness.

## When to Use

- Orchestrator daemon (Phase 2) invokes one critic per trial after
  `uv run lab ingest` finishes for a new run. Trials needing a
  critique are returned by `uv run lab query-trials --needs-critique`.
- Human asks "critique this trial" or pastes a trial directory path.
- An experiment-level review surfaces a surprising trial and asks
  for a per-trial deep-dive.

Do **not** use this skill for:

- Cross-leg comparisons on a single task — that's `experiment-critic`.
- Identifying patterns across many experiments — that's
  `cross-experiment-critic`.
- Extracting reusable per-task features — that's `task-features`
  (one shot per `task_checksum`, not per trial).

## Inputs

You will be invoked with one positional argument: the absolute path
to the trial directory (or the trial id alone, in which case look it
up in the DB first).

```bash
# Operator / orchestrator invocation:
codex exec --skill trial-critic <trial_dir>

# Look up the trial dir from the DB if you only have the id:
uv run lab query "SELECT trial_id, instance_id, leg_id, task_name, trial_dir FROM trials WHERE trial_id = '<id>'"
```

## Artifacts to read (in this order)

Inside the trial directory:

1. `result.json` — verifier outcome, agent metadata, totals. The
   `verifier.metadata.parser_results.tests` array tells you exactly
   which assertions passed/failed. `agent_result.metadata.summary.final_text`
   is the agent's last words.
2. `agent/trajectory.json` — full message log including the system
   prompt, every assistant turn, every tool call and result. Read
   the early turns to understand the strategy; spot-read failure
   points if the trial errored.
3. `events.jsonl` — turn-by-turn `model_request` / `assistant_complete`
   / `tool_started` events with token usage and timing. Useful for
   counting turns, spotting empty assistant turns, and seeing where
   wall-clock went.
4. `messages.jsonl` (if present) — same conversation in another
   shape; only fall back to it if `trajectory.json` is missing.
5. `verifier/run.log` (if present) — the raw verifier transcript;
   use to confirm whether failure was the agent's output, the
   verifier's environment, or a flaky test.

The leg's exact agent config is at
`runs/experiments/<instance>/legs/<leg>/agent.resolved.yaml` —
read it to know which `components:` were active, what tools the
agent had, and what the model and budget were.

The task's instruction (the prompt the agent saw) is the first
`user` message in `trajectory.json` after the system prompt.

## What to look for

A critique is most useful when it isolates **the one or two
mechanisms** that decided the outcome. Lean on these probes:

- **Did the agent understand the task?** Look at the first 1–3
  assistant turns. Did it inspect the env (`ls`, `cat
  Dockerfile`, `pwd`) before editing? Did it parse the
  instruction into concrete success criteria?
- **What strategy did it pick?** Sequential edit-and-test, plan-then-
  execute, brute-force script generation, etc. Cite the turn where
  the strategy crystallised.
- **What went wrong (if it failed) or right (if it passed)?**
  - Wrong tool family for the task (used `bash` to edit files
    instead of `edit_file`)?
  - Token / turn budget exhausted? (Check `events.jsonl` for the
    last `model_request` turn vs `max_turns`.)
  - Loop / retry on the same failing command? Grep the trajectory
    for repeated tool inputs.
  - Hallucinated assertion ("the test passes") without running
    the verifier?
  - Verifier-side environment issue (tool missing, race,
    permissions)? Cross-check against `verifier/run.log`.
- **What component(s) were active and did they help, hurt, or
  no-op?** Cross-reference the leg's `components:` list against
  observable behaviour in the trajectory.
- **Which task features mattered?** Note any features that
  obviously gated the outcome (Dockerfile present, requires
  network, multi-file edit, etc.) — these will get aggregated by
  `cross-experiment-critic` later.

Quote 1–2 short turn snippets in the critique when they justify a
claim. Do not paraphrase — quote.

## Output schema

Persist the critique by piping a JSON object on stdin to the lab
CLI. The CLI writes a file at
`<trial_dir>/critic/trial-critic.json` — that file is the canonical
record. The DuckDB cache (`trial_critiques` table) is rebuilt from
these files on demand by `uv run lab ingest-critiques`.

```bash
uv run lab write-trial-critique <trial_dir> \
  --critic-model "$OPENHARNESS_CODEX_MODEL" --json - <<'JSON'
{
  "schema_version": 1,
  "task_summary":            "<2-3 sentences: what the task asks for>",
  "agent_strategy":          "<2-4 sentences: the approach the agent took>",
  "key_actions":             ["turn 3: ran `ls /app`; saw …", "turn 7: edited run.py …"],
  "outcome":                 "passed|failed|errored",
  "root_cause":              "<one sentence (failed/errored only)>",
  "success_factor":          "<one sentence (passed only)>",
  "anti_patterns":           ["repeated_failed_command", "no_pre_edit_inspection"],
  "components_active":       ["loop-guard", "tool-result-summariser"],
  "task_features":           ["dockerfile_present", "multi_file_edit", "needs_network"],
  "surprising_observations": ["agent ran tests with the wrong python (3.10 vs 3.13)"],
  "confidence":              0.85
}
JSON
```

The CLI overwrites any existing `trial-critic.json`, so re-running
this skill on the same trial replaces the previous critique
cleanly.

Field rules:

- `outcome` ∈ `passed | failed | errored`. `errored` covers
  infrastructure failures (env_setup, agent crashed) where the
  agent never had a fair shot. Cross-check with `result.json`'s
  `error.phase`.
- Exactly one of `root_cause` or `success_factor` is required;
  leave the other null.
- `key_actions` is at most 6 items, each starting with `turn N:`.
- `anti_patterns` and `task_features` are short kebab-case tags;
  the cross-experiment-critic clusters on these so be consistent.
  Common anti-patterns: `repeated_failed_command`,
  `no_pre_edit_inspection`, `hallucinated_success`,
  `wrong_tool_family`, `gave_up_too_early`,
  `timeout_no_recovery`.
-   `confidence` ∈ `[0, 1]`. Lower it (≤ 0.5) when the trajectory
  is missing data, the verifier output is ambiguous, or your
  reasoning relies on a single fragment of evidence.

## Refusal cases

Refuse (and report what's missing) if any of these holds:

- The trial directory does not contain `result.json`.
- `agent/trajectory.json` is missing AND `messages.jsonl` is
  missing (no agent behaviour to critique).
- The DB has no `trials` row for this `trial_id` — run
  `uv run lab ingest <run_dir>` first.

## Examples

### Example: orchestrator-driven (autonomous)

Input (subprocess args):

```
trial-critic /path/to/runs/experiments/tb2-baseline-20260417-234913/legs/basic/harbor/tb2-baseline-20260417-234913-basic/cancel-async-tasks__rGqDyp4
```

1. Read `result.json` → `outcome: failed`, `score: 0.0`. Verifier
   `parser_results.tests` shows the cancellation cleanup test
   never ran.
2. Read `agent/trajectory.json` → agent jumped straight to writing
   `run.py` without inspecting `tests/` or the existing function
   signature. Two attempts overwrote each other.
3. `events.jsonl` shows 8 assistant turns out of `max_turns: 30`,
   so budget was not the issue.
4. Construct critique JSON with `outcome=failed`,
   `root_cause="Agent skipped pre-edit inspection and overwrote
   tests/test_cancel.py while iterating on run.py."`,
   `anti_patterns=["no_pre_edit_inspection","repeated_failed_command"]`,
   `task_features=["multi_file_edit","async_python"]`.
5. Pipe via `uv run lab write-trial-critique
   /path/to/runs/experiments/.../cancel-async-tasks__rGqDyp4 --json -`.
6. Report one line back to the orchestrator: `OK; outcome=failed,
   root_cause=...`.

### Example: human-driven on a passing trial

Input: the user pastes a trial path that passed.

1. Same artifact walk.
2. `outcome=passed`, `success_factor="Agent ran the test suite
   first to identify the failing assertion before touching code."`
3. `anti_patterns=[]`. Note any near-misses or wasted turns in
   `surprising_observations`.

## Operational notes

- This skill is invoked one-trial-at-a-time. The orchestrator
  bounds concurrency in `src/openharness/lab/codex.py`; do not
  spawn parallel critics from inside this skill.
- Do not edit any markdown under `lab/`. Only the `experiment-critic`
  and `cross-experiment-critic` skills are allowed to call the
  `uv run lab idea ...` / `uv run lab append-followup-idea` helpers.
- Do not modify the trial directory itself. The artifacts are
  immutable inputs.
