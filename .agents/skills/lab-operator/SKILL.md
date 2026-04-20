---
name: lab-operator
description: >
  Operate the autonomous lab loop end-to-end: start, stop, attach,
  monitor, restart, and answer "how is it going?". Use when the user
  says "check the lab", "is the daemon running", "what's the
  orchestrator doing", "start the lab", "stop the daemon", "restart
  the lab", "attach to the lab", "show me lab logs", "how's the
  current experiment", "what's queued next", "kick off the next
  roadmap entry", or otherwise wants to drive the
  `uv run lab daemon` lifecycle without remembering the individual
  CLI commands. Routes to `lab-run-experiment` /
  `lab-plan-next` / etc. only when the user is asking for one of
  those specific actions; otherwise this skill is the single entry
  point. Companion reference: `lab/OPERATIONS.md`.
---

# Operating the autonomous lab

The autonomous lab is one daemon (`uv run lab daemon`) plus four
codex critic skills it spawns automatically. This skill is the
operator's playbook: every command you might want to run, in the
order you'd actually run them, with the expected output and what
to do when it isn't what you expected.

For the architecture, the inner loop, the DB schema, and "what is
this thing" questions, read [`lab/OPERATIONS.md`](../../../lab/OPERATIONS.md).
This skill is task-only.

## Quick orientation (always run first)

When the user invokes this skill, **always start with the same
six reads** so you know the current state before doing anything:

```bash
codex login status                       # MUST say 'Logged in using ChatGPT'
uv run lab daemon status
uv run lab info
uv run lab tree show                     # current trunk + branches
uv run lab query "SELECT slug, kind, applied, applied_by
                  FROM tree_diffs ORDER BY applied_at DESC NULLS LAST LIMIT 5"
uv run lab query "SELECT skill, exit_code, started_at
                  FROM spawns ORDER BY started_at DESC LIMIT 5"
```

Then summarise to the user in two-to-four lines: is the daemon
up, what's the trunk, are there any **staged Graduate verdicts
awaiting human confirmation** (`tree_diffs.kind = 'graduate' AND
applied = FALSE`), what was the last spawn, how many
trials/critiques are in the DB. Don't take any action until you've
reported this.

**Staged graduates are special** — they're the one place where the
autonomous loop blocks on a human. If `tree_diffs` shows one,
mention it explicitly and offer to invoke `lab-graduate-component`
to confirm or reject.

### Codex auth: ChatGPT subscription only (HARD RULE)

The lab is **not allowed** to use an OpenAI API key for codex.
ChatGPT-subscription auth is the only accepted mode (it has the
quota the user pays for; the API-key path bills against a separate
exhaustible balance and has burned us once already).

If `codex login status` does NOT say `Logged in using ChatGPT`,
**stop and report** before doing anything else. The fix is:

```bash
codex logout
codex login                              # interactive: pick "Sign in with ChatGPT"
```

The orchestrator already enforces this in code
(`codex._check_auth()` refuses `auth_mode='apikey'`, and
`codex.run()` strips `OPENAI_API_KEY` from the child env), so any
API-key auth will surface as a hard `CodexAdapterError` on the
first spawn — but flagging it up front saves a wasted tick.

## Common tasks

### "Is the lab doing anything?"

```bash
uv run lab daemon status
```

Three outcomes:

- `orchestrator: not running` → no daemon, nothing happening. Ask
  whether to start one.
- `orchestrator: running (pid=N)` → daemon is alive. Read the most
  recent log to see what it's on:
  ```bash
  ls -1t runs/lab/logs/ | head -5
  # tail -n 80 runs/lab/logs/<newest>.log
  ```
- `orchestrator lock present but unreadable` → corrupted lock,
  follow "Stuck or crashed" below.

### "What's queued / what just ran?"

```bash
uv run lab query "
  SELECT instance_id, count(*) AS legs
  FROM legs GROUP BY 1 ORDER BY max(started_at) DESC LIMIT 5"

uv run lab query "
  SELECT instance_id, leg_id,
         count(*) AS n,
         avg(CAST(passed AS INTEGER)) AS pass_rate
  FROM trials GROUP BY 1,2
  ORDER BY 1 DESC, 2 LIMIT 10"
```

For the queue itself, read [`lab/roadmap.md`](../../../lab/roadmap.md)
directly — `## Up next` is the daemon's worklist (top first),
`## Done` is what's closed.

### "Start the lab"

Pick the right launch mode by asking yourself (and the user, if
unclear):

| Goal | Command |
| --- | --- |
| Confirm wiring with no spend | `uv run lab daemon start --foreground --once --dry-run` |
| First real tick on a cheap entry, watch it live | `uv run lab daemon start --foreground --once` |
| Long-running autonomous operation | `uv run lab daemon start --background` |

Always run the dry-run first if it's the start of a session or you
just edited `runner.py` / a roadmap entry. Foreground+once is the
right choice if the top of the roadmap costs <$1 and the user
wants to see what happens. Background is for "I'm done babysitting,
just chew through the queue".

If `daemon status` already shows a running daemon, **stop and ask
the user before starting another one** — the lock will refuse and
that's the desired behaviour, but the user almost always wants to
know "you're already running, did you mean restart?".

### "Stop the lab"

```bash
uv run lab daemon stop
```

Sends SIGTERM to the recorded pid. The daemon finishes the
current spawn (if any) before exiting. Then verify:

```bash
uv run lab daemon status
ls runs/lab/orchestrator.lock 2>/dev/null  # should be gone
```

If the lock file lingers and `status` still shows a pid that
isn't actually running, see "Stuck or crashed".

### "Attach to it / show me what it's doing"

If the daemon was started with `--background` and tmux exists:

```bash
uv run lab daemon attach              # detach with Ctrl-b d
```

Otherwise tail the orchestrator log:

```bash
tail -f runs/lab/logs/orchestrator.out
```

For a live view of individual spawns:

```bash
ls -1t runs/lab/logs/ | head -10      # newest first
tail -f runs/lab/logs/<spawn-log>     # one specific spawn
```

### "Restart the lab"

```bash
uv run lab daemon stop
sleep 2
uv run lab daemon status              # confirm it really stopped
uv run lab daemon start --background  # or whichever launch mode
```

Don't skip the `status` check between stop and start — re-locking
while the previous pid is still finalising will fail.

### "What's the tree look like right now?"

```bash
uv run lab tree show                     # human-readable trunk + branches
uv run lab tree show --json              # for piping
uv run lab trunk show                    # just the trunk id
uv run lab query "
  SELECT at_ts, from_id, to_id, reason, applied_by
  FROM trunk_changes ORDER BY at_ts DESC LIMIT 10"
```

For a deeper view, read `lab/configs.md` (the configuration tree)
and `lab/components.md` (the catalog of building-block atoms with
their current statuses). The journal of what proposed each branch /
rejection is in `lab/experiments.md` under each entry's
`### Tree effect` block. Use `uv run lab components show` to see
which atoms are still `proposed` vs `experimental` vs `validated`.

### "Are there any pending verdicts I need to confirm?"

```bash
uv run lab query "
  SELECT instance_id, slug, target_id, rationale
  FROM tree_diffs WHERE kind = 'graduate' AND applied = FALSE"
```

Each row is a *staged* trunk swap waiting on the human. If any
exist, walk the user through them:

1.  Show the journal entry's `### Mutation impact` and
    `### Tree effect` blocks.
2.  Offer to invoke [`lab-graduate-component`](../lab-graduate-component/SKILL.md)
    to confirm (or reject by leaving it alone).

### "How did the latest experiment do?"

```bash
LATEST=$(uv run lab query "SELECT max(instance_id) FROM trials" --json | head -1)

uv run lab query "
  SELECT leg_id,
         count(*) AS n,
         avg(CAST(passed AS INTEGER)) AS pass_rate,
         sum(cost_usd) AS cost
  FROM trials
  WHERE instance_id = '$LATEST'
  GROUP BY 1 ORDER BY 1"
```

Then check the human-readable summary the experiment-critic wrote:

```bash
cat runs/experiments/$LATEST/results/summary.md
cat runs/experiments/$LATEST/results/critic_summary.md  # if present
```

If `critic_summary.md` is missing, the experiment-critic hasn't run
(usually because some trial-critic spawns failed). Check:

```bash
uv run lab query "
  SELECT count(*) AS trials_total,
         count(c.trial_id) AS trials_critiqued
  FROM trials t LEFT JOIN trial_critiques c USING (trial_id)
  WHERE t.instance_id = '$LATEST'"
```

### "Is anything stuck?"

```bash
uv run lab query "
  SELECT skill, args, started_at, exit_code, log_path
  FROM spawns
  WHERE finished_at IS NULL
  ORDER BY started_at LIMIT 10"

uv run lab query "
  SELECT skill, count(*) AS n_failed
  FROM spawns
  WHERE exit_code != 0
  GROUP BY skill ORDER BY n_failed DESC"
```

A few `unknown_id` or `architecture_mismatch` rows in
`misconfigurations` are also worth flagging back to the user:

```bash
uv run lab query "SELECT trial_id, component_id, kind FROM misconfigurations LIMIT 20"
```

### "Queue something new" / "rerun something"

This skill does **not** edit the queue itself — the user owns
`lab/ideas.md`. Hand the request to the right neighbour:

- The user wants to add a new experiment to the queue → invoke the
  [`lab-plan-next`](../lab-plan-next/SKILL.md) skill.
- The user wants to write up a new idea first → invoke
  [`lab-propose-idea`](../lab-propose-idea/SKILL.md).
- The user wants to rerun an entry that's already in `## Done` →
  ask them to either (a) edit the slug and re-add to `## Up next`
  via `uv run lab roadmap add`, or (b) run the underlying
  experiment by hand: `uv run exec <slug>`. Don't silently
  duplicate roadmap entries.

### Stuck or crashed

| Symptom | Action |
| --- | --- |
| `daemon status` says running but no real pid | `rm runs/lab/orchestrator.lock`, then start fresh. |
| Daemon idles forever, log says "no ready roadmap entries" | Either `## Up next` is empty or every entry's `Depends on:` is unmet. Ask the user. |
| One trial-critic keeps failing | Read the matching log under `runs/lab/logs/`; usually a JSON-shape mismatch fixable in the SKILL.md. Re-run by hand: `codex exec` against that one skill+args. |
| `lab-run-experiment` never produces summary.md | Tail `runs/experiments/<id>/legs/<leg>/harbor/.../trial.log` — same failure modes as a hand-launched experiment. The orchestrator just times out and leaves the roadmap entry in place. |
| Schema migration error after `git pull` | `uv run lab init` (idempotent; applies any new migrations under `src/openharness/lab/migrations/`). |

## Reporting back to the user

Every operator turn ends with a **short status block** in your
reply, even if the user only asked one question. Two patterns:

**After "is anything happening?":**
> Daemon: running (pid 12345), started 14m ago.
> Trunk: `basic` (anchored 2026-04-17 by `tb2-baseline-full-sweep`).
> Last spawn: `trial-critic` exit 0, 32s ago, on
> `cancel-async-tasks__rGqDyp4` in `tb2-baseline-…`.
> DB: 267 trials, 234 critiqued, 0 misconfigurations.
> Pending: 1 staged graduate (`loop-guard-tb2-paired` →
> `loop-guard`); run `lab-graduate-component` to confirm.

**After "start it" / "restart it":**
> Started in tmux session `openharness-lab` (pid 23456).
> First entry on the queue: `loop-guard-tb2-paired`
> (cost ~$0.50 smoke). Attach with `uv run lab daemon attach`.

The user shouldn't have to ask "and how's it going?" after every
action — bake the status into the reply.

## What this skill does NOT do

- Implement variants, write critics, propose ideas, or graduate
  components — those are separate skills (`lab-run-experiment`,
  `trial-critic`, `lab-propose-idea`, `lab-graduate-component`).
- Touch `lab/ideas.md > ## Proposed`. The human owns that pile.
- Edit `lab/roadmap.md > ## Up next`. Use `lab-plan-next` for
  promotions.
- Decide whether a flaky run is "really failed" or "just retry".
  Surface the evidence and let the user say.
