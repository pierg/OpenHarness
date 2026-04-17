---
name: lab-run-experiment
description: >
  Scaffold, run, and log a concrete agent experiment. Use when the
  user says "let's try X", "run an experiment for X", "test this on
  tb2-baseline", "compare A vs B", "run the next thing on the
  roadmap", or asks for a paired ablation. Wires up a worktree (for
  risky work), edits lab/ideas.md and lab/experiments.md, hands off
  to lab-plan-next to move the roadmap entry to Done, launches the
  run in the background (either via the Shell tool's background mode
  or the tmux-backed `scripts/exp/start.sh`, picked per situation),
  polls until `results/summary.md` lands, then fills in the results
  table from the run artifacts. Companion skills: lab,
  lab-propose-idea, lab-plan-next, lab-graduate-component.
---

# Lab — Run Experiment

Take a roadmap entry (or an idea, or a baseline snapshot) and turn
it into a logged experiment with hypothesis, results, and a
decision. Edits `lab/experiments.md`, optionally `lab/ideas.md` and
a git worktree, then hands the roadmap entry off to `## Done` at
the end.

The lab markdowns are deliberately stripped of self-documenting
prose. The entry shape and structural rules below live in this skill
— never copy them back into the markdown.

## When to Use

- User asks to actually try an idea ("let's try planner-rerank now",
  "run an A/B for loop-guard").
- User asks for a paired comparison or ablation.
- User asks to run the top item on the roadmap ("run the next
  thing", "kick off the full sweep").
- User asks to record a baseline run as a reference point.

Do **not** use this skill for:

- Just capturing an idea on paper → `lab-propose-idea`.
- Adding/reordering items in the planning queue →
  `lab-plan-next`.
- Promoting a validated idea to a component →
  `lab-graduate-component`.

## Experiments.md entry shape

Newest at the top. The header section in `lab/experiments.md` may
contain a one-time reset note and nothing else; below it sits a
single reverse-chronological list of dated entries.

```markdown
## YYYY-MM-DD — <slug>

-   **Hypothesis:** one sentence.
-   **Variant:** what differs vs the current baseline   _(or: "leg A vs leg B" for paired runs)_
-   **Run:** [`runs/experiments/<instance-id>/`](../runs/experiments/<instance-id>/)

### Results

| Leg | Trials | Passed | Errored | Pass rate | Total tokens | Cost (USD) |
|-----|-------:|-------:|--------:|----------:|-------------:|-----------:|
| ... |        |        |         |           |              |            |

### Notes

-   3–6 short bullets of qualitative observations.

### Decision

graduate `<id>`   _(or: iterate — see follow-up `<slug>` / reject)_
```

Rules:

- **Status is implicit.** A new entry inserted above existing ones
  with an empty Results table is in-progress; once the table is
  populated and the Decision line filled in, it's complete. Never
  add a `Status:` field.
- **No `Held constant:` field.** The current baseline is defined
  once in `lab/README.md > ## Current state`. The entry's
  `Variant:` line states what differs.
- **No header rewriting.** When you add a new entry, append it
  above the previous newest entry; never touch existing entries.

## Instructions

### 1. Identify the experiment

Establish in this order:

1. **Roadmap entry** (if any) — if the user said "run the next
   thing" or named a roadmap slug, read `lab/roadmap.md` and
   confirm which `## Up next` item you're picking up. The roadmap
   entry's `**Plan:**` line is the spec for steps 2–4 below.
2. **Idea id** — must already exist in `lab/ideas.md`. If the user
   hasn't proposed it yet, run `lab-propose-idea` first, then
   continue here. Roadmap entries with `**Idea:** baseline snapshot`
   or `infrastructure` skip this.
3. **Baseline** — what you compare against. The current baseline
   is documented in `lab/README.md > ## Current state`. Either
   compare against it implicitly or against a prior entry in
   `lab/experiments.md`.
4. **Variant** — exactly what differs from the baseline (one
   sentence; this becomes the entry's `Variant:` line).
5. **Run plan** — single run, or paired run (two legs that differ
   only in the variable under test).

State the plan back to the user in 3–5 lines and **wait for
confirmation** before doing anything destructive.

### 2. Pick a slug and date

```bash
date +%Y-%m-%d
```

If there's a matching roadmap entry, reuse its slug. Otherwise the
slug is `<idea-or-topic>-<short-context>`, e.g.
`loop-guard-tb2-paired`,
`planner-rerank-tb2-smoke`,
`tb2-baseline-full-sweep`.

### 3. Optional: isolate in a git worktree

For anything riskier than a one-off baseline (especially when
editing agent prompts, components, or configs), use a worktree.
Skip for pure runs that only invoke `uv run exec` against an
existing spec.

```bash
WORKTREE=../OpenHarness.worktrees/lab-<slug>
git worktree add "$WORKTREE" -b lab/<slug>
cd "$WORKTREE"
```

Tell the user the worktree path and branch. All subsequent edits
and runs happen inside the worktree until the experiment is
decided.

### 4. Move the idea to "Trying" in lab/ideas.md

If the experiment is testing a previously-proposed idea **and the
idea isn't already in `## Trying`** (it would already be there if
the experiment was queued via `lab-plan-next`), edit
`lab/ideas.md`:

- Cut the `#### <idea-id>` entry from its theme subsection under
  `## Proposed`.
- Paste it under `## Trying`.
- Append one bullet to the entry:
  `-   **Trying in:** [<roadmap-slug>](roadmap.md#<roadmap-slug>)`.
- Don't rewrite the existing Motivation / Sketch bullets.

If the experiment has no associated idea (baseline snapshot /
infrastructure), skip this step.

### 5. Add a stub entry at the top of lab/experiments.md

Insert the new section **above** all existing dated sections (newest
on top). Use the entry shape documented above, with an empty
Results table and placeholder Notes / Decision:

```markdown
## YYYY-MM-DD — <slug>

-   **Hypothesis:** <one sentence — copy from the roadmap entry's Hypothesis>
-   **Variant:** <what differs from the baseline>
-   **Run:** _(filled after the run completes)_

### Results

| Leg | Trials | Passed | Errored | Pass rate | Total tokens | Cost (USD) |
|-----|-------:|-------:|--------:|----------:|-------------:|-----------:|
|     |        |        |         |           |              |            |

### Notes

-   _(filled after the run completes)_

### Decision

_(filled after the run completes)_
```

### 6. Make the experiment-specific edits

Apply only the changes the experiment requires. Examples:

- Toggle a component on a leg (e.g. duplicate the leg in
  `experiments/tb2-baseline.yaml`, one with the component listed
  in `components:` and one without; or flip a runtime flag).
- Add a new agent variant under `src/openharness/agents/configs/`.
- Tweak a prompt section under test.

Keep the diff tight. Do not also fix unrelated bugs in this
worktree.

### 7. Run the experiment in the background

**Never block on `uv run exec` in the foreground.** Even smoke runs
take minutes, and any synchronous call ties up the agent loop for
the whole run, can hit the agent's wall-clock, and loses the run
if the calling shell dies.

There are two equally valid ways to background a run. Pick whichever
fits the situation — both end at the same "done" signal
(`runs/experiments/<instance-id>/results/summary.md` exists).

#### 7a. Pick the path

| Situation | Path |
|-----------|------|
| Agent is driving the whole run end-to-end and will stay around to poll. | **A — `Shell` background.** |
| Run is short (smoke, single ablation leg, < ~20 min). | **A — `Shell` background.** |
| Long sweep where the human may want to attach and watch Harbor's TUI live. | **B — `scripts/exp/start.sh`.** |
| The agent might die / the user may want to take over later. | **B — `scripts/exp/start.sh`.** |
| Multiple concurrent runs the human wants to manage from a normal shell. | **B — `scripts/exp/start.sh`.** |

Both paths use the same `uv run exec` underneath. The differences
are who manages the process (agent's `Shell` tool vs `tmux`) and how
the human can interact with it.

#### 7b. Path A — `Shell` background mode (default for agent-driven runs)

Kick off with the agent's `Shell` tool using `block_until_ms: 0` so
it returns immediately and the run streams to a terminal file the
agent can re-read. Examples:

```bash
uv run exec tb2-baseline                                  # full sweep
uv run exec tb2-baseline --profile smoke                  # smoke
uv run exec experiments/<your-spec>.yaml                  # custom spec
uv run rerun <instance-id>                                # resume
```

Capture the returned `task_id`. Poll completion using `Await` (see
7d) and check the run directory for `results/summary.md`.

#### 7c. Path B — `scripts/exp/start.sh` (tmux-backed, hand-off friendly)

Use this when the run should outlive the agent loop or when the
human wants `attach`/`list`/`stop` ergonomics. See
[`scripts/exp/README.md`](../../../scripts/exp/README.md) for the
full surface.

```bash
scripts/exp/start.sh exec tb2-baseline                    # full sweep
scripts/exp/start.sh exec tb2-baseline --profile smoke    # smoke
scripts/exp/start.sh exec experiments/<your-spec>.yaml    # custom
scripts/exp/start.sh rerun <instance-id>                  # resume
```

The script prints a session name (e.g. `tb2-baseline-20260418-091230`)
and a log path (`/tmp/<session>.log`). Capture both and surface them
to the user — the session name is what `attach.sh` / `stop.sh` need.

The human can then:

```bash
scripts/exp/list.sh                # see active sessions
scripts/exp/attach.sh <session>    # attach (Ctrl-b d to detach)
scripts/exp/stop.sh <session>      # abort
```

The agent itself, even on path B, polls via `status.sh` rather than
attaching — `status.sh` is non-interactive and parses cleanly into
the chat reply.

#### 7d. Find the run directory and poll for completion

The `runs/experiments/<instance-id>/` directory is created within a
few seconds of kicking off the run, on either path. Locate it:

```bash
ls -dt runs/experiments/* | head -1
```

The run is complete when
`runs/experiments/<instance-id>/results/summary.md` exists. That's
the unambiguous "done" signal regardless of path.

For path B, `scripts/exp/status.sh <instance-id>` is the most
informative single command — it prints the manifest-level status,
per-leg progress (with `LATEST_ACTIVITY` timestamp), recent retry /
429 / 503 signals from `events.jsonl`, and the summary itself once
the run completes. It also works on path A (status.sh only reads the
run directory; it doesn't care who started the process), so feel
free to use it either way.

**Polling cadence guidance** (use the `Await` tool between polls):

| Run type | First check after | Then poll every | Expected total |
|----------|-------------------|-----------------|----------------|
| Smoke (1–3 cached tasks) | 60 s | 60–120 s | 2–10 min |
| Demo (small profile) | 2 min | 2 min | 5–20 min |
| Full sweep (~89 tasks, 3 legs) | 10 min | 10 min | 1–3 h |
| Stronger-model small slice | 2 min | 5 min | 10–30 min |

If the per-leg `LATEST_ACTIVITY` timestamp isn't advancing for 2× the
expected per-trial wall-clock (or you see a spike of 429s/503s with
no recovery), investigate before continuing to wait. Path A: read
the terminal file directly. Path B: `scripts/exp/attach.sh <session>`
or grep `events.jsonl`.

#### 7e. Stopping a run

- Path A: kill the backgrounded `Shell` task by its `task_id` (or
  let it run if it's safe — partial results stay on disk).
- Path B: `scripts/exp/stop.sh <session>`.

Either way, on-disk artifacts (`events.jsonl`, already-completed
trials) survive and can be inspected or resumed via
`uv run rerun <instance-id>` (path A) or
`scripts/exp/start.sh rerun <instance-id>` (path B).

### 8. Confirm the run directory and artifacts

Once `results/summary.md` exists, you have the canonical run
directory: `runs/experiments/<instance-id>/`. Inside:

- `experiment.json` — schema-versioned summary of legs and trials.
- `results/summary.md` — per-leg pass/fail/tokens table.
- `results/rows.csv`, `results/rows.json` — flat per-trial rows.
- `legs/<agent>/agent.resolved.yaml` — exact agent config used,
  including the `components:` list.
- `legs/<agent>/harbor/<instance>-<agent>/<task>/` — per-trial
  artifacts (`run.json`, `result.json`, trajectories).

### 9. Fill in the experiment entry

Read `runs/experiments/<instance-id>/results/summary.md` and the
relevant `legs/*/harbor/.../result.json` files. Update the entry
in `lab/experiments.md` **without rewriting the existing bullets**:

- **Run:** replace the `_(filled after the run completes)_`
  placeholder with the path link.
- **Results table:** copy numbers from `results/summary.md`.
- **Notes:** 3–6 short bullets — qualitative observations from
  trajectories (no `400` errors, planner hallucination count,
  loop-guard nudges fired, agent timeouts, 429 rate-limit hits in
  `events.jsonl`, etc.). For trajectory-level evidence, grep:

  ```bash
  rg -l "loop_guard_nudge" runs/experiments/<instance-id>/legs
  rg -l "thought_signature" runs/experiments/<instance-id>/legs
  rg "\"status\":\\s*429" runs/experiments/<instance-id>/legs
  ```

- **Decision:** one of:
  - `graduate <id>` — invoke `lab-graduate-component` next.
  - `iterate — see follow-up <slug>` — record the follow-up and
    consider queueing it via `lab-plan-next`.
  - `reject` — the idea entry will move from `## Trying` to
    `## Rejected` in `lab/ideas.md` (append a `**Rejected:**`
    bullet with the date and reason; link to this experiment).

### 10. Hand off to the roadmap

If the experiment came from a `lab/roadmap.md` entry, hand off to
`lab-plan-next` to move that entry from `## Up next` to `## Done`
with `**Ran:**` and `**Outcome:**` bullets pointing at the new
experiments.md entry. Do this **regardless** of the decision —
the roadmap records the plan, not the outcome.

### 11. Tidy up

- If you used a worktree and the experiment is complete:
  - Stage and commit the experiment-specific edits + the lab
    edits on the experiment branch (`lab/<slug>`). Do not push.
  - If the decision is `reject`, optionally delete the worktree:
    `git worktree remove "$WORKTREE"` from the main checkout.
- If the decision is `graduate`, hand off to
  `lab-graduate-component` with the slug.

Always finish with:

- The slug + date.
- The run directory path (repo-relative).
- Headline numbers (pass rates per leg).
- The decision and the next concrete step (often: queue the
  follow-up via `lab-plan-next`).

## Examples

### Example: kick off the next roadmap item (long sweep — path B)

Input: "Run the next thing on the roadmap."

Output:

1. Read `lab/roadmap.md`. Top of `## Up next` is
   `tb2-baseline-full-sweep`. State the plan back: 3 legs ×
   ~89 tasks, expected ~$15-25 over 1–3 hours. Pick **path B**
   (tmux) so the user can attach to Harbor's TUI and so the run
   survives if the agent loop is interrupted.
2. Confirm with user, recommending a `uv run plan tb2-baseline`
   sanity check first.
3. No worktree (pure baseline run, no agent edits).
4. No idea move (it's a `baseline snapshot`).
5. Insert the stub at the top of `lab/experiments.md`.
6. Kick off: `scripts/exp/start.sh exec tb2-baseline`. Capture
   the printed session name and log path. Surface them to the
   user along with `scripts/exp/attach.sh <session>` so they can
   peek anytime.
7. Find the run dir: `ls -dt runs/experiments/* | head -1`.
8. Poll loop: every 10 minutes, run
   `scripts/exp/status.sh <instance-id>` until
   `runs/experiments/<instance-id>/results/summary.md` exists.
   Use `Await` between polls.
9. Fill in the experiment entry from `results/summary.md`.
10. Hand off to `lab-plan-next` to move
    `tb2-baseline-full-sweep` to `## Done`.

### Example: paired ablation on smoke (short — path A)

Input: "Run a loop-guard ablation on tb2 smoke with planner_executor."

Output:

1. Propose plan: slug `loop-guard-tb2-paired`, leg A =
   `planner_executor` with `LoopGuardConfig.enabled=False`
   (current default), leg B = same agent with `enabled=True` plus
   `loop-guard` listed in `components:`. Variant line: "leg B has
   loop-guard enabled, leg A has it disabled". Smoke run, agent
   manages it end-to-end → **path A**. Confirm with user.
2. Create worktree `lab/loop-guard-tb2-paired`.
3. Add a new leg in a copy of `experiments/tb2-baseline.yaml` for
   the experimental variant.
4. (If not already there) move `loop-guard` from
   `## Proposed > Runtime` to `## Trying` in `lab/ideas.md`.
5. Insert the stub at the top of `lab/experiments.md`.
6. Kick off via the agent's `Shell` tool with `block_until_ms: 0`:
   `uv run exec <copied-spec> --profile smoke`. Capture the
   `task_id`.
7. Find the run dir, then poll with `Await` every 60–120 s until
   `results/summary.md` exists (smoke is fast — usually 2–10 min).
   `scripts/exp/status.sh <instance-id>` works fine here too if a
   richer status snapshot is wanted.
8. Read `runs/experiments/<instance>/results/summary.md`, fill the
   entry, write decision.
9. Hand off to `lab-plan-next` to move `loop-guard-tb2-paired` to
   `## Done`.

### Example: trying a brand-new idea (path A, may switch to B)

Input: "Let's try `tool-result-summariser`."

Output:

1. If the idea isn't yet in `lab/ideas.md` → invoke
   `lab-propose-idea` first.
2. (Optional) hand off to `lab-plan-next` to record the queue
   entry, then immediately pick it up. Or skip the queue and run
   directly — both are fine for one-off explorations.
3. Slug `tool-result-summariser-smoke`. Confirm a minimal smoke
   plan with the user (e.g. smoke profile, `planner_executor`,
   summariser injected at threshold K=2000 tokens). Smoke → start
   on **path A**; if the user asks to graduate this to a full
   sweep next, switch to path B for that run.
4. Create worktree.
5. Move the idea to `## Trying`.
6. Implement the summariser behind a toggle.
7. Stub the experiment entry.
8. Kick off via `Shell` background mode:
   `uv run exec ... --profile smoke`. Poll with `Await` until
   `results/summary.md` exists.
9. Fill in results, write decision.
