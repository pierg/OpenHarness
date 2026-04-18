# Autonomous lab loop

The four lab markdowns in [`lab/`](.) are the **human-curated audit
surface**. Once an idea is promoted to [`roadmap.md`](roadmap.md),
the rest of the loop — implementing the variant, running the
experiment, criticising every trial, ingesting results, and closing
the roadmap entry — runs autonomously through one daemon and four
critic skills.

This document is the operational guide: what the daemon does each
tick, how to start/stop/monitor it, what the new tables and skills
mean, and how to interpret the outputs. For the **why** (gap
analysis, locked decisions) see the planning doc cited inline.

> Component contracts (`schemas/component_contract.json`) ship in a
> deliberately coarse shape today — name-based `applies_to`, free-form
> `cost` tags, no predicate language. We will tighten it once we have
> 3-4 real components and enough trials to know which knobs matter.
> Don't model what we can't yet measure.

## Architecture in one picture

```
       lab/ideas.md                    (human owns)
            │
            ▼ promote
       lab/roadmap.md  ──── parsed by ────►  src/openharness/lab/runner.py  (daemon)
                                                    │
              ┌─────────────────────────────────────┼─────────────────────────────┐
              ▼                                     ▼                             ▼
   .agents/skills/lab-run-experiment       runs/experiments/<id>/      .agents/skills/{trial,
        (codex)                            results/summary.md          experiment,cross-experiment}-critic
              │                                     │                  .agents/skills/task-features
              ▼                                     ▼                             │
   scripts/exp/start.sh ──► harbor ───► trials, events.jsonl    ──► uv run lab insert-* ──► runs/lab/trials.duckdb
                                                    │                                                ▲
                                                    └────► uv run lab ingest <run_dir> ──────────────┘

   runs/lab/trials.duckdb  ──► uv run lab dashboard  (Streamlit, read-only)
                           ──► uv run lab query "SELECT …"
```

Single rule that keeps the seams clean: **agent code goes through
codex; deterministic code goes through `uv run lab`. Nothing else.**

## Inner loop (one tick)

`src/openharness/lab/runner.py`:

1. **Parse** `lab/roadmap.md > ## Up next`. Skip entries whose
   `Depends on:` slugs aren't all in `## Done`. Pick the top.
2. **Lock** `runs/lab/orchestrator.lock` to refuse a second daemon.
3. **Spawn** `lab-run-experiment` (codex). The skill writes the
   variant onto a worktree, kicks off `scripts/exp/start.sh exec
   <slug>`, and reports the run id back.
4. **Poll** `runs/experiments/<id>/results/summary.md` until it
   exists or the per-run timeout fires (default 4 h).
5. **Ingest** the run dir into DuckDB. `_scan_misconfigurations`
   runs per leg and emits one row per (trial × kind) into
   `misconfigurations` for every detected `unknown_id`,
   `architecture_mismatch`, `agent_mismatch`, or `conflicts_with`.
6. **Fan out** `trial-critic` for each newly-ingested trial. Each
   spawn writes one row into `trial_critiques`.
7. **Fan out** `task-features` for each unseen `task_checksum` so
   tasks pick up category / required-tools / output-shape labels.
8. Once every trial in this experiment has a critique, **spawn**
   `experiment-critic`. Writes per-task winners into `comparisons`
   and a `runs/experiments/<id>/results/critic_summary.md`.
9. Every `xexp_every` completed experiments (default 1), **spawn**
   `cross-experiment-critic`. Updates `components_perf` and appends
   any follow-ups to `lab/ideas.md > ## Auto-proposed` for human
   review (the daemon never queues them itself).
10. **Close** the roadmap entry by spawning `lab-plan-next` to move
    it to `## Done` with a link to the run.

If anything fails, the entry is **left in `## Up next`** so the
next tick retries; nothing in the lab markdowns silently rots.

## Operating it

All commands run from the repo root.

### First-time sanity checks

```bash
uv run lab info                          # paths, DB row counts per table
uv run lab daemon status                 # 'orchestrator: not running' before first start
uv run python -m openharness.agents.components --validate
uv run python scripts/validate_contracts.py
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

## Where things land

| Path | Owned by | Notes |
| --- | --- | --- |
| `lab/ideas.md` | human (+ `cross-experiment-critic` for `## Auto-proposed`) | only `## Auto-proposed` is daemon-touched |
| `lab/roadmap.md` | human + daemon (via `lab-plan-next`) | daemon only moves entries to `## Done` |
| `lab/experiments.md` | daemon (via `lab-run-experiment` → `uv run lab experiments fill`) | append-only |
| `lab/components.md` | human (today) | flips to daemon ownership once `lab-graduate-component` runs |
| `runs/experiments/<id>/` | harbor | per-trial artifacts, summary.md |
| `runs/lab/trials.duckdb` | `uv run lab ingest` + critic skills | source of truth for trial-grain data |
| `runs/lab/logs/<utc>__<skill>__<short>.log` | `codex.py` adapter | full prompt + raw json events + last message + exit code |
| `runs/lab/orchestrator.lock` | `runner.py` | `{pid, owner, started_at}` json |
| `components/<id>.yaml` | human | source-of-truth for each component |

`runs/` is gitignored end-to-end — the DB, logs, lock, and
per-trial artifacts never leak into the repo.

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

## When something goes wrong

| Symptom | First check | Fix |
| --- | --- | --- |
| `daemon start` complains "already running" | `uv run lab daemon status` | If the pid is gone, `rm runs/lab/orchestrator.lock`. |
| Daemon idles forever | `uv run lab daemon attach`; check the log line "no ready roadmap entries" | Either the queue is empty or every entry's `Depends on:` is unmet. |
| `lab-run-experiment` never produces a summary.md | tail `runs/experiments/<id>/legs/<leg>/harbor/.../trial.log` | Same failure modes as a hand-launched experiment; the orchestrator just times out and leaves the roadmap entry unmoved. |
| Critic skill fails repeatedly for one trial | grep the matching log under `runs/lab/logs/` for the skill name + trial id | Re-run by hand: `codex exec` against the same skill+args; usually it's a JSON-shape mismatch we can fix in the SKILL.md. |
| `misconfigurations` keeps growing | `SELECT DISTINCT kind, component_id FROM misconfigurations` | Either fix the offending agent YAML, the component spec, or downgrade the check by relaxing `applies_to`. |
| Schema mismatch errors after a code update | run `uv run lab init` | Applies any new `src/openharness/lab/migrations/NNNN_*.sql`. |

## What the autonomous loop is *not*

- It does **not** propose ideas. The human owns `lab/ideas.md > ##
  Proposed`. Cross-experiment-critic only suggests follow-ups in
  `## Auto-proposed`, never in the queue.
- It does **not** edit `lab/roadmap.md > ## Up next`. Promotions to
  the queue stay manual through `uv run lab roadmap add`.
- It does **not** pick a model or a configuration mid-experiment.
  Codex picks the model for the agent spawn; the experiment YAML
  picks the model for the variant.
- It does **not** retry forever on a failed roadmap entry. Failures
  leave the entry in place and surface in the spawn log; a human
  decides whether to fix the spec, lower the cost, or drop the
  entry.

## Suggested first runs

1. **Smoke the wiring with no spend**:
   `uv run lab daemon start --foreground --once --dry-run`.
2. **Smoke the wiring on a cheap entry**: queue a smoke-only
   variant (e.g. `loop-guard-tb2-paired` already on the roadmap) and
   run `--foreground --once` so you can read every log line as it
   happens.
3. **Promote to `--background`** once the inner loop has worked
   end-to-end at least once.
4. **Open the dashboard** (`uv run lab dashboard`) before walking
   away — that's the fastest way to see whether the run actually
   produced what you expected.

## Skills involved

| Skill | Spawned by | Persists via |
| --- | --- | --- |
| [`lab-run-experiment`](../.agents/skills/lab-run-experiment/SKILL.md) | daemon (top of loop) | `uv run lab experiments stub/fill` + `uv run lab ingest` + `scripts/exp/start.sh` |
| [`trial-critic`](../.agents/skills/trial-critic/SKILL.md) | daemon (per uncritiqued trial) | `uv run lab insert-critique` |
| [`task-features`](../.agents/skills/task-features/SKILL.md) | daemon (per unseen `task_checksum`) | `uv run lab insert-task-features` |
| [`experiment-critic`](../.agents/skills/experiment-critic/SKILL.md) | daemon (after all per-trial critiques land) | `uv run lab insert-comparison` + `runs/experiments/<id>/results/critic_summary.md` |
| [`cross-experiment-critic`](../.agents/skills/cross-experiment-critic/SKILL.md) | daemon (every `xexp_every` runs) | `uv run lab upsert-component-perf` + `uv run lab append-followup-idea` |
| [`lab-plan-next`](../.agents/skills/lab-plan-next/SKILL.md) | daemon (close-out) | `uv run lab roadmap done` |
| [`lab-graduate-component`](../.agents/skills/lab-graduate-component/SKILL.md) | human (today; daemon path is future work) | `uv run lab idea move … graduated` |
