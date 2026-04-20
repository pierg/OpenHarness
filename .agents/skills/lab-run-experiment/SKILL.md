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

Newest at the top. Single reverse-chronological list of dated
entries. **Each entry has a fixed header (5 bullets) and exactly
five canonical subsections, all written by deterministic helpers
— never by hand.**

```markdown
## YYYY-MM-DD — <slug>

-   **Type:** `paired-ablation` | `broad-sweep` | `smoke`
-   **Trunk at run-time:** [`trunk@<sha>`](../src/openharness/agents/configs/trunk.yaml)
-   **Mutation:** one-line description of what differs from trunk.
    _(omit for `broad-sweep`)_
-   **Hypothesis:** one sentence on what we expect to learn.
-   **Run:** [`runs/experiments/<instance-id>/`](../runs/experiments/<instance-id>/)

### Aggregate
### Mutation impact
### Failure modes
### Tree effect
### Linked follow-ups
```

Rules:

-   **Status is implicit.** An entry whose subsections are empty is
    in-progress; once `synthesize` and `tree apply` have populated
    them, the entry is complete. Never add a `Status:` field.
-   **Default type is `paired-ablation`** (trunk leg + 1 mutation
    leg). Use `broad-sweep` only when re-baselining (running every
    branch + trunk on the full slice).
-   **No `Held constant:` / `Notes:` / `Decision:` fields.** The
    `Mutation` bullet states what differs; `### Failure modes`
    captures qualitative notes; `### Tree effect` carries the
    verdict.
-   **No header rewriting.** When you add a new entry, append it
    above the previous newest entry; never touch existing entries.
-   **The `### Tree effect` block is the single source of truth for
    the verdict.** It is written by `uv run lab tree apply <slug>`,
    which calls `tree_ops.evaluate(<instance_id>)`. Do not hand-write
    a verdict.

## Instructions

### 1. Identify the experiment

Establish in this order:

1.  **Roadmap entry** (if any) — if the user said "run the next
    thing" or named a roadmap slug, read `lab/roadmap.md` and
    confirm which `## Up next` item you're picking up. The roadmap
    entry's `**Plan:**` line is the spec for steps 2–4 below.
2.  **Idea id** — must already exist in `lab/ideas.md`. If the user
    hasn't proposed it yet, run `lab-propose-idea` first, then
    continue here. Roadmap entries with
    `**Idea:** baseline snapshot` or `infrastructure` skip this.
3.  **Trunk** — read `uv run lab trunk show` (or the `## Trunk`
    section of `lab/configs.md`). The trunk is the canonical
    "leg A" of any paired ablation; the experiment is defined as
    "trunk + delta".
4.  **Mutation** — exactly what differs from the trunk (one
    sentence; this becomes the entry's `Mutation:` line). For a
    `broad-sweep` (e.g. re-baselining), there is no mutation —
    every leg is independent.
5.  **Type** — pick `paired-ablation` (default), `broad-sweep`
    (re-baselining), or `smoke` (1–3 cached tasks for wiring
    sanity).

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
the experiment was queued via `lab-plan-next`), promote it via
the CLI:

```bash
uv run lab idea move <idea-id> trying \
  --cross-ref "**Trying in:** [<slug>](roadmap.md#<slug>)"
```

If the experiment has no associated idea (baseline snapshot /
infrastructure), skip this step.

### 5. Append the journal entry at the top of lab/experiments.md

Use the CLI — it inserts the entry **above** all existing dated
sections with the canonical 5-bullet header and 5 empty
subsections (`### Aggregate`, `### Mutation impact`, `### Failure
modes`, `### Tree effect`, `### Linked follow-ups`):

```bash
uv run lab experiments append-entry <slug> \
  --type paired-ablation \
  --trunk "$(uv run lab trunk show)" \
  --mutation "<one-line description of the delta>" \
  --hypothesis "<one sentence — copy from the roadmap entry's Hypothesis>" \
  --run "runs/experiments/<instance-id>"
```

Use `--type broad-sweep` (and omit `--mutation`) for re-baselining
runs. The CLI errors if `<slug>` is already stubbed today, so
retries are safe.

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

### 9. Close out the journal entry and the tree

Once the run is complete, ingest the run dir, then let the
deterministic helpers fill in the journal subsections and apply
the verdict to the tree. **None of these steps are
agent-judgment.** Each one is a single CLI call.

```bash
# 9a. Mirror per-trial rows into the lab DB (idempotent on trial_id).
uv run lab ingest runs/experiments/<instance-id>

# 9b. Refresh the critic-derived cache so the next steps see the
#     experiment-critic / comparisons / task-features / tree_diffs
#     rows. This is also called automatically by the daemon.
uv run lab ingest-critiques runs/experiments/<instance-id>

# 9c. Fill the four narrative subsections from the critic JSONs +
#     DB stats. Idempotent; safe to re-run after a critic re-spawn.
uv run lab experiments synthesize <slug>

# 9d. Compute the TreeDiff via tree_ops.evaluate(<instance_id>),
#     write the `### Tree effect` block, and either auto-apply
#     (AddBranch / Reject / NoOp → mutate configs.md and forward-bump
#     unique-to-target atoms in components.md) or stage for human
#     (Graduate). Use --dry-run first if you want to see the verdict
#     before persisting.
uv run lab tree apply <slug>
```

`tree apply`'s output names the verdict, the affected target id,
and (for `graduate`) the slug + applied_by needed by `uv run lab
graduate confirm`. Surface that to the user.

If you want trajectory-level evidence to confirm a verdict before
running 9d, useful greps are:

```bash
rg -l "loop_guard_nudge" runs/experiments/<instance-id>/legs
rg -l "thought_signature" runs/experiments/<instance-id>/legs
rg "\"status\":\\s*429" runs/experiments/<instance-id>/legs
```

### 10. Hand off to planner + roadmap

```bash
# 10a. Tree-aware planner: reads the current tree + the latest
#      journal entries, writes 0..N entries under
#      roadmap.md > ## Up next > ### Suggested and 0..N entries
#      under ideas.md > ## Auto-proposed.
codex exec --skill lab-reflect-and-plan --instance <instance-id>

# 10b. Move the just-finished entry to `## Done`.
codex exec --skill lab-plan-next --slug <slug>
```

If the verdict was `graduate`, also tell the user:

> "Trunk swap is staged. Run
> `uv run lab graduate confirm <slug> --applied-by human:<you>` —
> or invoke the `lab-graduate-component` skill in Cursor — to
> apply it to `trunk.yaml`."

### 11. Tidy up

-   If you used a worktree and the experiment is complete:
    -   Stage and commit the experiment-specific edits + the lab
        edits on the experiment branch (`lab/<slug>`). Do not push.
    -   If the verdict is `reject`, optionally delete the worktree:
        `git worktree remove "$WORKTREE"` from the main checkout.
-   If the verdict is `graduate`, hand off to
    `lab-graduate-component` with the slug.

Always finish with:

-   The slug + date.
-   The run directory path (repo-relative).
-   Headline numbers (pass rates per leg).
-   The verdict (from `### Tree effect`) and the next concrete
    step (auto-applied → nothing for the human; staged
    `graduate` → `lab graduate confirm <slug>`; queued follow-ups
    → check `roadmap.md > ### Suggested`).

## Examples

### Example: kick off the next roadmap item (broad sweep — path B)

Input: "Run the next thing on the roadmap."

Output:

1.  Read `lab/roadmap.md`. Top of `## Up next` is
    `tb2-baseline-full-sweep`. Type = `broad-sweep` (re-baselining
    every branch on the full slice). State the plan back: 3 legs ×
    ~89 tasks, expected ~$15-25 over 1–3 hours. Pick **path B**
    (tmux) so the user can attach to Harbor's TUI and so the run
    survives if the agent loop is interrupted.
2.  Confirm with user, recommending a `uv run plan tb2-baseline`
    sanity check first.
3.  No worktree (pure baseline run, no agent edits).
4.  No idea move (it's a `baseline snapshot`).
5.  `uv run lab experiments append-entry tb2-baseline-full-sweep
    --type broad-sweep --hypothesis "..." --run runs/experiments/<id>`
6.  Kick off: `scripts/exp/start.sh exec tb2-baseline`. Capture
    the printed session name and log path. Surface them to the
    user along with `scripts/exp/attach.sh <session>` so they can
    peek anytime.
7.  Poll loop with `Await`; the run is done when
    `runs/experiments/<id>/results/summary.md` exists.
8.  Run `uv run lab ingest`, `ingest-critiques`, `experiments
    synthesize`, `tree apply` (in that order).
9.  Hand off to `lab-reflect-and-plan` (writes Suggested
    follow-ups) and `lab-plan-next` (moves the roadmap entry to
    `## Done`).

### Example: paired ablation on smoke (short — path A)

Input: "Run a loop-guard ablation on tb2 smoke with planner_executor."

Output:

1.  Propose plan: slug `loop-guard-tb2-paired`, type
    `paired-ablation`, leg A = trunk (`planner_executor` branch
    via `uv run lab tree show`), leg B = same agent with
    `loop-guard` enabled. Mutation line: "loop-guard component
    enabled on top of planner_executor branch". Smoke run, agent
    manages it end-to-end → **path A**. Confirm with user.
2.  Create worktree `lab/loop-guard-tb2-paired`.
3.  Add a new leg in a copy of `experiments/tb2-baseline.yaml` for
    the experimental variant.
4.  (If not already there) move `loop-guard` from
    `## Proposed > Runtime` to `## Trying` in `lab/ideas.md`.
5.  `uv run lab experiments append-entry loop-guard-tb2-paired
    --type paired-ablation --trunk basic --mutation "loop-guard on
    planner_executor branch" --hypothesis "..." --run
    runs/experiments/<id>`
6.  Kick off via the agent's `Shell` tool with `block_until_ms: 0`:
    `uv run exec <copied-spec> --profile smoke`. Capture the
    `task_id`.
7.  Poll with `Await` every 60–120 s until `results/summary.md`
    exists.
8.  Run `uv run lab ingest`, `ingest-critiques`, `experiments
    synthesize`, `tree apply`.
9.  Hand off to `lab-reflect-and-plan` + `lab-plan-next`. If the
    verdict was `graduate`, surface the `lab graduate confirm`
    command to the user.

### Example: trying a brand-new idea (path A, may switch to B)

Input: "Let's try `tool-result-summariser`."

Output:

1.  If the idea isn't yet in `lab/ideas.md` → invoke
    `lab-propose-idea` first.
2.  (Optional) hand off to `lab-plan-next` to record the queue
    entry, then immediately pick it up. Or skip the queue and run
    directly — both are fine for one-off explorations.
3.  Slug `tool-result-summariser-smoke`, type `smoke`. Confirm a
    minimal plan with the user (e.g. smoke profile, current trunk
    + summariser at threshold K=2000 tokens). Smoke → start on
    **path A**; if the user asks to graduate to a full sweep,
    switch to path B for that run.
4.  Create worktree.
5.  Move the idea to `## Trying`.
6.  Implement the summariser behind a toggle.
7.  `uv run lab experiments append-entry ...`.
8.  Kick off via `Shell` background mode; poll with `Await`.
9.  Run `uv run lab ingest`, `ingest-critiques`, `experiments
    synthesize`, `tree apply`. Hand off to `lab-reflect-and-plan`
    + `lab-plan-next`.
