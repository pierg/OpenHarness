# Operations

Operating guide for the autonomous lab loop. Conceptual model
(three artifacts, invariants, mutation diagram) lives in
[`README.md`](README.md); per-skill instructions live in
[`.agents/skills/`](../.agents/skills/). This file covers what
the daemon does each tick, how to operate it, the file-ownership
matrix, the DB queries you'll re-run, and what to check when
something breaks.

Two seams that keep the loop clean:

1. **Agent work goes through codex; deterministic work goes
   through `uv run lab`. Nothing else.**
2. **Critic outputs are files (single source of truth); DuckDB is
   a derived cache** — rebuild any time with
   `uv run lab ingest-critiques`. No critic spawn ever writes to
   the DB.

## Inner loop (one tick)

`src/openharness/lab/runner.py`:

1.  **Parse** `lab/roadmap.md > ## Up next`. Skip entries whose
    `Depends on:` slugs aren't all in `## Done`. Pick the top.
2.  **Lock** `runs/lab/orchestrator.lock` to refuse a second daemon.
3.  **Spawn** `lab-run-experiment` (codex). The skill builds a
    paired ablation off the current trunk
    ([`src/openharness/agents/configs/trunk.yaml`](../src/openharness/agents/configs/trunk.yaml)),
    writes the variant onto a worktree, kicks off
    `scripts/exp/start.sh exec <slug>`, and reports the run id back.
    Broad-sweep experiments are opt-in via `type: broad-sweep` in
    the experiment YAML.
4.  **Poll** `runs/experiments/<id>/results/summary.md` until it
    exists or the per-run timeout fires (default 4 h).
5.  **Ingest** the run dir into DuckDB. `_scan_misconfigurations`
    runs per leg and emits one row per (trial × kind) into
    `misconfigurations` for every detected `unknown_id`,
    `architecture_mismatch`, `agent_mismatch`, or `conflicts_with`.
6.  **Fan out** `trial-critic` for each trial whose
    `<trial_dir>/critic/trial-critic.json` does NOT yet exist on disk.
    Each spawn writes one such file (no DB writes).
7.  **Fan out** `task-features` for each `task_checksum` whose
    `runs/lab/task_features/<checksum>.json` does NOT yet exist.
8.  Once every trial in this experiment has a critic file, **spawn**
    `experiment-critic`. Writes one
    `<run_dir>/critic/comparisons/<task>.json` per task plus
    `<run_dir>/critic/experiment-critic.json` and a human-facing
    `runs/experiments/<id>/results/critic_summary.md`. The skill
    uses codex's `multi_agent` feature to fan its per-task
    comparisons across subagents.
9.  **Refresh the DB cache** by calling `uv run lab ingest-critiques`
    so the tree-evaluation step below sees the new rows.
10. **Close the loop on the tree**, four steps, all deterministic:
    1.  `uv run lab experiments synthesize <slug>` — fills `###
        Aggregate / Mutation impact / Failure modes / Linked follow-ups`
        in the journal entry from the critic JSONs.
    2.  `uv run lab tree apply <slug>` — runs `tree_ops.evaluate`,
        writes the `### Tree effect` block, and either auto-applies
        the diff (AddBranch / Reject / NoOp → mutate
        [`configs.md`](configs.md), forward-bump unique-to-target
        atoms in [`components.md`](components.md)) or stages it for
        human confirmation (Graduate).
    3.  **Spawn** `lab-reflect-and-plan` — tree-aware planner. Reads
        the current tree (`uv run lab tree show --json`) plus the
        latest journal entries; writes 0..N entries under
        `roadmap.md > ## Up next > ### Suggested` and 0..N entries
        under `ideas.md > ## Auto-proposed`. Humans promote /
        accept; the daemon never edits the main `## Up next` queue.
    4.  **Spawn** `lab-plan-next` — moves the just-finished entry
        to `## Done` with a link to the journal entry.
11. Every `xexp_every` completed experiments (default 1), **spawn**
    `cross-experiment-critic`. Snapshots the apex view to
    `runs/lab/cross_experiment/<ts>__<spawn_id>.json` and may
    append follow-ups under `## Auto-proposed`. It does **not**
    write to the tree; only `tree apply` does.

If anything fails, the entry is **left in `## Up next`** so the
next tick retries; nothing in the lab markdowns silently rots.

## Operating it

All commands run from the repo root.

### Codex auth: ChatGPT subscription only

**Hard rule:** the lab orchestrator runs codex against the user's
ChatGPT subscription, never against an OpenAI API key. The
ChatGPT path has the generous quota the user pays for; the API-key
path bills against a separate (and easily-exhausted) OpenAI Platform
balance and burned us once already.

Two layers enforce this:

1. `codex._check_auth()` reads `~/.codex/auth.json` and refuses to
   spawn unless `auth_mode == "chatgpt"`.
2. `codex.run()` strips `OPENAI_API_KEY`, `OPENAI_KEY`,
   `OPENAI_ORG_ID`, and `OPENAI_ORGANIZATION` from the env it hands
   to the child `codex exec` — even if a parent process (Cursor,
   shells, etc.) leaks them in, codex itself never sees them.

To set up or recover:

```bash
codex login status                       # must say 'Logged in using ChatGPT'
# If it doesn't:
codex logout
codex login                              # pick "Sign in with ChatGPT"
```

If you ever see `auth_mode='apikey'` in `~/.codex/auth.json`, the
orchestrator will refuse to spawn. That is the intended behaviour;
log out and re-login with ChatGPT.

### First-time sanity checks

```bash
uv run lab info                          # paths, DB row counts per table
uv run lab daemon status                 # 'orchestrator: not running' before first start
uv run python -m openharness.agents.components --validate
uv run python scripts/validate_contracts.py
codex login status                       # must say 'Logged in using ChatGPT'
```

### Foreground dry-run (recommended before any real run)

```bash
uv run lab daemon start --foreground --once --dry-run
```

Walks through roadmap-parse, dependency check, lock-acquisition,
and "would invoke lab-run-experiment for <slug>" without spending a
single codex call. If this exits clean, the moving parts are wired.

### Single real entry, attached to terminal

```bash
uv run lab daemon start --foreground --once
```

Picks the top `## Up next` entry, runs the full loop once, exits.
Best smoke for a fresh skill or a roadmap entry you're not sure
about.

### Long-running daemon (tmux session)

```bash
uv run lab daemon start --background     # tmux session 'openharness-lab'
uv run lab daemon attach                 # attach to it
uv run lab daemon status                 # check pid + lock
uv run lab daemon stop                   # SIGTERM the recorded pid
```

Falls back to a detached `nohup` writing to
`runs/lab/logs/orchestrator.out` if tmux isn't installed.

### Watching what's happening

```bash
ls -lt runs/lab/logs/ | head            # newest spawn logs first
uv run lab query "SELECT skill, exit_code, duration_sec
                  FROM spawns ORDER BY started_at DESC LIMIT 20"
uv run lab dashboard                    # Streamlit, http://127.0.0.1:8501
```

### Backfilling an existing experiment

```bash
uv run lab analyze <instance_id> --dry-run               # preview the plan
uv run lab analyze <instance_id> --limit-trials 1        # smoke 1 trial first
uv run lab analyze <instance_id> -j 8                    # full backfill
uv run lab analyze <instance_id> --include-cross-experiment   # +Phase C
```

`analyze` mirrors the daemon's post-ingest pipeline (steps 4-7)
but targets one instance and runs without holding the
orchestrator lock — safe to run while the daemon is idle, and
intended for filling in critic data for experiments that landed
before a critic skill existed (or re-running after a skill
update). Three phases:

| Phase | Skills | Concurrency |
| --- | --- | --- |
| A | `trial-critic` over uncritiqued trials + `task-features` over unseen task_checksums | parallel, capped by `--concurrency` |
| B | `experiment-critic <instance_id>` (only if every trial now has a critique) | sequential |
| C | `cross-experiment-critic` (opt-in via `--include-cross-experiment`) | singleton |

C is opt-in because the apex spawn analyzes the WHOLE database,
not just this instance — usually you only want to run it once
after a series of backfills, not after each one.

## File-ownership matrix (the autonomy contract)

| File / section | Human writes | Daemon writes | Tool |
|----------------|--------------|---------------|------|
| `lab/ideas.md > ## Proposed / Trying / Graduated / Rejected` | yes | no | `lab idea move/append`, `lab-propose-idea` (Cursor) |
| `lab/ideas.md > ## Auto-proposed` | read-only | yes | `lab idea auto-propose` (`cross-experiment-critic`, `lab-reflect-and-plan`) |
| `lab/roadmap.md > ## Up next` (main queue) | yes | `## Done` move only | `lab roadmap add/done`, `lab-plan-next` |
| `lab/roadmap.md > ## Up next > ### Suggested` | promote to main queue | yes | `lab roadmap suggest`, `lab roadmap promote` (`lab-reflect-and-plan`) |
| `lab/experiments.md` entry header (Type / Trunk / Mutation / Hypothesis / Run) | no | yes | `lab-run-experiment` (= daemon) |
| `lab/experiments.md > ### Aggregate / Mutation impact / Failure modes / Linked follow-ups` | no | yes | `lab experiments synthesize`, `lab-reflect-and-plan` |
| `lab/experiments.md > ### Tree effect` (AddBranch / Reject / NoOp) | no | yes | `lab tree apply` (auto) |
| `lab/experiments.md > ### Tree effect` (Graduate) | `graduate confirm` flips Applied → human | stages the proposal | `lab tree apply`, `lab graduate confirm` |
| `lab/configs.md > ## Branches / ## Rejected / ## Proposed` | rare manual edits | yes | `lab tree apply` |
| `lab/configs.md > ## Trunk` | via `lab trunk set` or `lab graduate confirm` | only via `graduate confirm` | `lab trunk set`, `lab graduate confirm` |
| `lab/components.md` (any kind) | `lab components upsert` / `lab components set-status` | yes (forward-only status bump) | `lab tree apply`, `lab components` |
| [`src/openharness/agents/configs/trunk.yaml`](../src/openharness/agents/configs/trunk.yaml) | rare | only via `graduate confirm` | `lab graduate confirm <slug>` |
| `runs/experiments/<id>/` | — | harbor | `scripts/exp/start.sh` |
| `runs/experiments/<id>/critic/...` | — | critic skills | trial-critic / experiment-critic / task-features |
| `runs/lab/trials.duckdb` | — | `lab ingest`, `lab ingest-critiques` | DERIVED CACHE |
| `runs/lab/logs/<utc>__<skill>__<short>.log` | — | `codex.py` adapter | full prompt + events + exit code |
| `runs/lab/orchestrator.lock` | — | `runner.py` | `{pid, owner, started_at}` json |
| `components/<id>.yaml` | yes | no | source-of-truth per component |

`runs/` is gitignored end-to-end — the DB, logs, lock, and
per-trial artifacts never leak into the repo. The DB tables
`trunk_changes` and `tree_diffs` are *derived caches* of the
journal's `### Tree effect` blocks and `lab/configs.md > ## Trunk`;
rebuild them with `uv run lab ingest-critiques`.

### Component status lattice

Statuses in `lab/components.md` move forward only via automation:

```
proposed  →  experimental  →  branch  →  validated
```

with two terminal states:

```
rejected   (component caused or contributed to a Reject verdict)
superseded (component replaced by another in active use)
```

`lab tree apply` only ever pushes statuses forward through the
linear chain. Demotions and entry into terminal states require
`uv run lab components set-status` (humans only) — the lab never
silently retires an atom because of one bad experiment.

## DB tables a human actually queries

```sql
-- pass rate per leg, per experiment
SELECT instance_id, leg_id,
       count(*) AS n,
       avg(CAST(passed AS INTEGER)) AS pass_rate
FROM trials GROUP BY 1,2 ORDER BY 1,2;

-- per-task A/B, restricted to the latest experiment
SELECT task_name, leg_id, passed, score, cost_usd, duration_sec
FROM trials WHERE instance_id = (SELECT max(instance_id) FROM trials)
ORDER BY task_name, leg_id;

-- which components have ever shipped, with running stats
SELECT * FROM components_perf ORDER BY n_trials DESC;

-- everything the orchestrator has ever spawned
SELECT skill, count(*) AS n, avg(duration_sec) AS avg_sec,
       sum(CAST(exit_code != 0 AS INTEGER)) AS n_failed
FROM spawns GROUP BY skill ORDER BY n DESC;

-- did any leg get flagged for misconfiguration on ingest?
SELECT trial_id, component_id, kind, detail FROM misconfigurations LIMIT 50;
```

The Streamlit dashboard already shows the first three of these;
extend `lab/dashboard/app.py` as you find queries you re-run by
hand.

## Codex tunings per skill

Every orchestrator-invoked spawn goes through
[`src/openharness/lab/codex.py`](../src/openharness/lab/codex.py).
The per-skill model / reasoning effort / reasoning summary /
sandbox / timeout live in the `SKILL_PROFILES` table there —
that's the source of truth, this doc does not duplicate it.
Anything unset falls back to `CodexConfig` defaults (`gpt-5.4` /
`high` / `detailed` / `danger-full-access` / ephemeral / 6 h
safety-net timeout).

Design constraints worth knowing:

-   **Signal density over throughput.** The lab runs unattended
    overnight; we spend tokens where they buy accuracy because
    noisy verdicts compound. Bulk graders (`trial-critic`,
    `task-features`) stay on the flagship at `medium` because
    everything downstream is built on their verdicts; the apex
    `cross-experiment-critic` runs at `xhigh`. The only
    intentionally cheap spawn is `lab-plan-next` (mechanical
    roadmap nudge).
-   **Sandbox: `danger-full-access` by default.** The loop runs
    unattended on a dedicated machine; any OS-level sandbox
    restriction becomes a silent failure with no human to
    approve the override. Per-skill profiles can downgrade to
    `workspace-write` for hand-driven debugging, but the
    orchestrator path stays unconfined.
-   **Timeouts are safety nets, not throughput knobs.** A spawn
    that hits its timeout is treated as a hung process worth
    investigating, not a slow one worth waiting on.
-   **Effort floor is `low`, not `minimal`.** codex 0.121
    registers `web_search` under `--full-auto` and the API
    rejects `minimal` whenever `web_search` is registered.

Override for a one-off (debugging a critic, A/B between effort
levels on the same skill):

```python
from openharness.lab import codex as cx
profile = cx.SkillProfile(reasoning_effort="xhigh")
cx.run("trial-critic", [trial_dir], profile_override=profile)
```

Every spawn writes its effective settings to the log header so
each file under `runs/lab/logs/` is self-describing:

```text
# effective_settings: {"skill": "trial-critic", "model": "gpt-5.4",
#                      "reasoning_effort": "medium", ...}
```

## When something goes wrong

| Symptom | First check | Fix |
| --- | --- | --- |
| `daemon start` complains "already running" | `uv run lab daemon status` | If the pid is gone, `rm runs/lab/orchestrator.lock`. |
| Daemon idles forever | `uv run lab daemon attach`; check the log line "no ready roadmap entries" | Either the queue is empty or every entry's `Depends on:` is unmet. |
| `lab-run-experiment` never produces a summary.md | tail `runs/experiments/<id>/legs/<leg>/harbor/.../trial.log` | Same failure modes as a hand-launched experiment; the orchestrator just times out and leaves the roadmap entry unmoved. |
| Critic skill fails repeatedly for one trial | grep the matching log under `runs/lab/logs/` for the skill name + trial id | Re-run by hand: `codex exec` against the same skill+args; usually it's a JSON-shape mismatch we can fix in the SKILL.md. |
| `misconfigurations` keeps growing | `SELECT DISTINCT kind, component_id FROM misconfigurations` | Either fix the offending agent YAML, the component spec, or downgrade the check by relaxing `applies_to`. |
| Schema mismatch errors after a code update | run `uv run lab init` | Applies any new `src/openharness/lab/migrations/NNNN_*.sql`. |

## Skills involved

| Skill | Spawned by | Persists via |
| --- | --- | --- |
| [`lab-run-experiment`](../.agents/skills/lab-run-experiment/SKILL.md) | daemon (top of loop) | `uv run lab experiments append-entry` + `scripts/exp/start.sh` |
| [`trial-critic`](../.agents/skills/trial-critic/SKILL.md) | daemon (per uncritiqued trial) | `uv run lab write-trial-critique` |
| [`task-features`](../.agents/skills/task-features/SKILL.md) | daemon (per unseen `task_checksum`) | `uv run lab write-task-features` |
| [`experiment-critic`](../.agents/skills/experiment-critic/SKILL.md) | daemon (after all per-trial critiques land) | `uv run lab write-comparison` + `uv run lab write-experiment-critique` + `runs/experiments/<id>/results/critic_summary.md` |
| [`cross-experiment-critic`](../.agents/skills/cross-experiment-critic/SKILL.md) | daemon (every `xexp_every` runs) | `uv run lab write-cross-experiment` + `uv run lab idea auto-propose` |
| [`lab-reflect-and-plan`](../.agents/skills/lab-reflect-and-plan/SKILL.md) | daemon (after `tree apply`) | `uv run lab roadmap suggest` + `uv run lab idea auto-propose` |
| [`lab-plan-next`](../.agents/skills/lab-plan-next/SKILL.md) | daemon (close-out) | `uv run lab roadmap done` |
| [`lab-graduate-component`](../.agents/skills/lab-graduate-component/SKILL.md) | human (Cursor) | `uv run lab graduate confirm <slug>` |
