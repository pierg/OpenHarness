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
  CLI commands.   Routes to per-phase skills (`lab-design-variant`,
  `lab-implement-variant`, `lab-finalize-pr`) and
  `lab-plan-next` / `lab-graduate-component` only when the user is
  asking for one of those specific actions; otherwise this skill is
  the single entry point and the daemon drives the full 6-phase
  pipeline. Companion reference: `lab/OPERATIONS.md`.
---

# Operating the autonomous lab

The autonomous lab is one daemon (`uv run lab daemon`) that drives
a deterministic **6-phase pipeline** per roadmap entry — preflight
(git worktree) → design (codex skill, read-only) → implement
(codex skill, worktree-write) → run (`uv run exec`) → critique
(trial/experiment-critic spawns) → finalize (codex skill, push
branch + open PR). Per-slug state lives in
`runs/lab/state/<slug>/phases.json` so any phase can be resumed
after a restart.

This skill is the operator's playbook: every command you might
want to run, in the order you'd actually run them, with the
expected output and what to do when it isn't what you expected.

For the architecture, the inner loop, the DB schema, and "what is
this thing" questions, read [`lab/OPERATIONS.md`](../../../lab/OPERATIONS.md).
This skill is task-only.

### Inspecting per-slug pipeline state

```bash
uv run lab phases show                    # every slug with state, one line each
uv run lab phases show <slug>             # full per-phase status for one slug
uv run lab phases reset <slug> --phase implement   # force a single phase to retry
uv run lab phases reset <slug>            # nuke the whole phases.json
uv run lab preflight list                 # every git worktree the parent repo knows about
uv run lab preflight remove <slug>        # tear down a stuck worktree (and lab/<slug>)
```

Use `phases show` first whenever a slug looks stuck — the answer is
usually "phase X is `failed` because <error>", and you can either
fix the underlying cause and let the daemon retry on the next tick
(it always picks the first unfinished phase), or `phases reset <slug>
--phase X` if the failure poisoned the recorded payload.

## Quick orientation (always run first)

When the user invokes this skill, **always start with the same
six reads** so you know the current state before doing anything:

```bash
codex login status                       # MUST say 'Logged in using ChatGPT'
uv run lab svc status                    # services + port + lock at a glance
uv run lab info
uv run lab tree show                     # current trunk + branches
uv run lab query "SELECT slug, kind, applied, applied_by
                  FROM tree_diffs ORDER BY applied_at DESC NULLS LAST LIMIT 5"
uv run lab query "SELECT skill, exit_code, started_at
                  FROM spawns ORDER BY started_at DESC LIMIT 5"
```

`lab svc status` shows both systemd units (web UI + orchestrator
daemon), their PIDs, the listening port, and the orchestrator lock
state in one call. If `systemctl --user` is unavailable (rare, e.g.
CI), fall back to `uv run lab daemon status`.

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

The orchestrator and the web UI both run as **systemd `--user`
units**. The wrapper hides the verbose `systemctl --user` syntax:

| Goal | Command |
| --- | --- |
| Confirm wiring with no spend | `uv run lab daemon start --foreground --once --dry-run` |
| First real tick on a cheap entry, watch it live | `uv run lab daemon start --foreground --once` |
| Long-running autonomous operation (canonical) | `uv run lab svc start daemon` |
| Both web UI + daemon at once | `uv run lab svc start` |

Always run the dry-run first if it's the start of a session or you
just edited `runner.py` / a roadmap entry. Foreground+once is the
right choice if the top of the roadmap costs <$1 and the user
wants to see what happens. The systemd path is the canonical
"long-running autonomous operation" — systemd owns the process
tree, journald captures stdout/stderr, and `Restart=on-failure`
recovers crashes.

If `lab status` shows the daemon is already running, **stop and
ask the user before starting another one** — the lock will refuse
(and systemd will refuse a duplicate start), and the user almost
always wants to know "you're already running, did you mean restart?".

`uv run lab daemon start --background` (the legacy tmux/nohup path)
still works but should only be used on machines where the systemd
units aren't installed; the `lab svc` Typer subcommand is the
canonical operational path.

### "Stop the lab"

```bash
uv run lab svc stop daemon            # canonical (systemd)
# or, on machines without the systemd unit:
uv run lab daemon stop                # SIGTERM to the recorded pid
```

The systemd path sends SIGTERM with a 60 s grace; the daemon
catches it cleanly and releases the orchestrator lock. Verify:

```bash
uv run lab svc status                 # daemon should show ○ stopped
ls runs/lab/orchestrator.lock 2>/dev/null  # should be gone
```

If the lock file lingers, see "Stuck or crashed".

### "Attach to it / show me what it's doing"

```bash
uv run lab svc logs daemon -f         # follow live (journalctl)
uv run lab svc logs daemon            # last 100 lines
```

For a live view of individual spawns (the daemon's children):

```bash
ls -1t runs/lab/logs/ | head -10      # newest first
tail -f runs/lab/logs/<spawn-log>     # one specific spawn
```

The `/daemon` page in the web UI also shows a live process tree of
the daemon's descendants with per-PID kill buttons (descendants
only — random VM PIDs are refused by the precheck).

### "Restart the lab"

```bash
uv run lab svc restart daemon
```

systemd handles the start-after-stop ordering for you. The orchestrator
lock is released cleanly because the daemon installs a SIGTERM
handler.

If the systemd unit isn't installed on this machine:

```bash
uv run lab daemon stop
sleep 2
uv run lab daemon status              # confirm it really stopped
uv run lab daemon start --background
```

### "Restart the web UI" (e.g. picked up a code change)

```bash
uv run lab svc restart web
```

The browser sees a brief network error before HTMX reconnects.

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
| `lab svc status` shows the orchestrator lock as stale | `rm runs/lab/orchestrator.lock`, then `uv run lab svc restart daemon`. |
| `uv run lab webui` errors with "address already in use" | The systemd unit already has the port. `uv run lab svc status` shows it. Either visit the running instance, or `uv run lab svc restart web` to pick up code changes. The CLI now prints this hint itself. |
| `lab svc status` shows daemon `failed` | `uv run lab svc logs daemon` for the traceback. Common cause: lock left behind by a previous crash — `rm runs/lab/orchestrator.lock` then `uv run lab svc restart daemon`. |
| Daemon idles forever, log says "no ready roadmap entries" | Either `## Up next` is empty or every entry's `Depends on:` is unmet. Ask the user. |
| One trial-critic keeps failing | Read the matching log under `runs/lab/logs/`; usually a JSON-shape mismatch fixable in the SKILL.md. Re-run by hand: `codex exec` against that one skill+args. |
| Phase `run` never produces summary.md | Tail `runs/experiments/<id>/legs/<leg>/harbor/.../trial.log` — same failure modes as a hand-launched experiment. The daemon times out the `run` phase and leaves the slug pinned at `run: failed` in `phases.json`; rerun with `uv run lab phases reset <slug> --phase run` once you've fixed the cause. |
| Phase `design` or `implement` keeps failing | `uv run lab phases show <slug>`; tail the matching `runs/lab/logs/...lab-design-variant...log` or `...lab-implement-variant....log`; for `implement`, also `cd <worktree>` and `git status` / `git log --oneline` to see what landed. Reset just that phase (`uv run lab phases reset <slug> --phase design`) once fixed. |
| A worktree under `../OpenHarness.worktrees/lab-<slug>` is stale | `uv run lab preflight remove <slug>` (idempotent; also deletes the `lab/<slug>` branch unless you pass `--keep-branch`). |
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
  components — those are separate skills (`lab-design-variant`,
  `lab-implement-variant`, `lab-finalize-pr`, `trial-critic`,
  `lab-propose-idea`, `lab-graduate-component`).
- Touch `lab/ideas.md > ## Proposed`. The human owns that pile.
- Edit `lab/roadmap.md > ## Up next`. Use `lab-plan-next` for
  promotions.
- Decide whether a flaky run is "really failed" or "just retry".
  Surface the evidence and let the user say.
