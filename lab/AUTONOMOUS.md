# Autonomous lab loop

The lab files in [`lab/`](.) are the **human-curated audit
surface**. Once an idea is promoted to [`roadmap.md`](roadmap.md),
the rest of the loop — implementing the variant, running the
experiment, criticising every trial, mutating the configuration
tree ([`configs.md`](configs.md)), bumping component statuses in
the catalog ([`components.md`](components.md)), and appending the
journal entry ([`experiments.md`](experiments.md)) — runs
autonomously through one daemon and a handful of skills.

This document is the operational guide: the mental model, what the
daemon does each tick, the file-ownership matrix, how to
start/stop/monitor it, what the tables and skills mean, and how to
interpret the outputs. The high-level mental model lives in
[`README.md`](README.md); per-skill instructions live in
[`.agents/skills/`](../.agents/skills/).

> Component contracts (`schemas/component_contract.json`) ship in a
> deliberately coarse shape today — name-based `applies_to`, free-form
> `cost` tags, no predicate language. We will tighten it once we have
> 3-4 real components and enough trials to know which knobs matter.
> Don't model what we can't yet measure.

> The catalog ([`components.md`](components.md)) is **descriptive,
> not prescriptive**: today an agent YAML is still the unit of
> execution and the catalog records which atoms each composes plus
> the running status (proposed / experimental / branch / validated
> / rejected / superseded). When we want runtime composition, we
> can add a real composer behind the same vocabulary without
> touching the verdict machinery.

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
   scripts/exp/start.sh ──► harbor ───► trials, events.jsonl                      │ critic spawns write JSON files:
                                                    │                             │   <trial>/critic/trial-critic.json
                                                    │                             │   <run>/critic/comparisons/*.json
                                                    │                             │   <run>/critic/experiment-critic.json
                                                    │                             ▼   runs/lab/task_features/*.json
                                                    │                          (filesystem; no DB writes)
                                                    │                                                ▲
                                                    └────► uv run lab ingest <run_dir> ──────────────┤
                                                                                                     │
                                                          uv run lab ingest-critiques  ──────────────┘
                                                                                                     │
                                                                                                     ▼
                                                                                       runs/lab/trials.duckdb (cache)
                                                                                                     │
                                                                                                     ├──► uv run lab dashboard
                                                                                                     └──► uv run lab query "SELECT …"
```

Two simple rules that keep the seams clean:

1. **Agent code goes through codex; deterministic code goes through
   `uv run lab`. Nothing else.**
2. **Critic outputs are FILES (single source of truth). DuckDB is
   a derived cache** — rebuild it any time with
   `uv run lab ingest-critiques`. No critic spawn ever writes to
   the DB. This eliminates the writer-lock contention that
   silently dropped spawn telemetry under parallel fan-out.

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
`src/openharness/lab/codex.py`, which picks a model + reasoning
effort + reasoning summary + sandbox + ephemeral mode + timeout
per skill from a single `SKILL_PROFILES` table. Anything left
unset falls back to the lab defaults on `CodexConfig`
(`gpt-5.4` / `high` / `detailed` / `danger-full-access` /
ephemeral / 6 h safety-net timeout).

**Scope.** This table covers *only* the skills the orchestrator
invokes. Human-driven skills like `lab-propose-idea` (you curate
ideas) and `lab-graduate-component` (you decide when to promote)
live in the same `.agents/skills/` tree but are invoked from
Cursor against its own model — they never go through `codex exec`,
so they have no profile here.

**Design philosophy: signal density over throughput.** The lab
runs autonomously overnight; the human optimizes for accurate,
high-signal data because the design space is large and noisy
verdicts compound. We spend tokens where they buy accuracy and
treat the budget as generous. Reasoning summaries are kept
detailed on every analytical spawn so a human auditing
`runs/lab/logs/` can reconstruct *why* the model decided what
it did, not just what it decided.

**Sandbox.** Default is `danger-full-access` (translated to
`--dangerously-bypass-approvals-and-sandbox` on the CLI). The
loop runs unattended on a dedicated machine; any OS-level
sandbox restriction (landlock/seccomp on Linux) becomes a silent
failure with no human to approve the override. Per-skill
profiles can downgrade to `workspace-write` for hand-driven
debugging, but the orchestrator path stays unconfined.

| Skill | Model | Effort | Summary | Timeout | Notes |
| --- | --- | --- | --- | --- | --- |
| `trial-critic` | `gpt-5.4` | medium | concise | 2 h | bulk; one per trial; everything downstream is built on these verdicts |
| `task-features` | `gpt-5.4` | medium | concise | 2 h | bulk; one per task_checksum; feeds clustering / routing |
| `experiment-critic` | `gpt-5.4` | high | detailed | 6 h | one per experiment; produces the per-task `comparisons/*.json` + `experiment-critic.json`; `tree_ops.evaluate` reads these to compute the verdict |
| `cross-experiment-critic` | `gpt-5.4` | **xhigh** | detailed | 12 h | **singleton**; cross-experiment trends; proposes follow-up ideas under `## Auto-proposed`; does **not** mutate the tree |
| `lab-run-experiment` | `gpt-5.4` | high | detailed | 8 h | variant implementer + harness kickoff; code quality shapes what the experiment measures |
| `lab-reflect-and-plan` | `gpt-5.4` | high | detailed | 4 h | tree-aware planner; reads current tree + latest journal entries; writes to `roadmap.md > ### Suggested` and `ideas.md > ## Auto-proposed` |
| `lab-plan-next` | `gpt-5.4-mini` | low | none | 1 h | mechanical roadmap nudge — moves the just-finished entry to `## Done` |

Rationale per tier:

- **Bulk graders** (`trial-critic`, `task-features`) get the
  flagship at `medium`. They run hundreds of times per
  experiment; the temptation is to downgrade to mini for cost,
  but every aggregation, comparison, and component-impact
  calculation downstream is built on these verdicts. Cheap
  trial verdicts poison the entire tower of analysis.
- **Per-experiment aggregator** (`experiment-critic`) gets
  `high` effort and `detailed` summaries. Its output (the
  winning leg, the per-task pattern, the explanation) lands in
  the `comparisons` table and drives the next experiment's
  design.
- **Apex spawn** (`cross-experiment-critic`) gets `xhigh` —
  the only place we burn maximum effort. It integrates
  hundreds of facts across experiments to rewrite
  `components_perf` and propose follow-up ideas, shaping the
  whole roadmap. `singleton=True` so two concurrent runs
  cannot race on the `components_perf` table.
- **Variant implementer** (`lab-run-experiment`) gets `high`
  because the code it writes literally *is* the variant being
  measured. A buggy variant is a wasted experiment. Worth
  flipping to a `*-codex` model once we benchmark code-tuned
  variants on this exact workload.
- **Mechanical roadmap nudge** (`lab-plan-next`) is the only
  mechanical skill the orchestrator invokes — it just moves
  the just-finished entry into `## Done`. Stays on
  `gpt-5.4-mini` at `low`; more model intelligence does not
  improve the outcome.

**Timeouts are safety nets, not throughput knobs.** Each upper
bound is generous enough that a healthy spawn at the configured
effort level never approaches it, but tight enough that a wedged
subprocess can't sit forever on a pool slot and stall the
daemon. A spawn that hits its timeout is treated as a hung
process worth investigating, not a slow one worth waiting on.

**Effort floor is `low`, not `minimal`**, because codex 0.121
registers `web_search` under `--full-auto` and the API rejects
`minimal` whenever `web_search` is registered. If we ever
disable `web_search` per skill, the mechanical helpers can
drop to `minimal`.

To override for a one-off (e.g. while debugging a critic, or
running an A/B between effort levels on the same skill):

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
| [`lab-run-experiment`](../.agents/skills/lab-run-experiment/SKILL.md) | daemon (top of loop) | `uv run lab experiments append-entry` + `scripts/exp/start.sh` |
| [`trial-critic`](../.agents/skills/trial-critic/SKILL.md) | daemon (per uncritiqued trial) | `uv run lab write-trial-critique` |
| [`task-features`](../.agents/skills/task-features/SKILL.md) | daemon (per unseen `task_checksum`) | `uv run lab write-task-features` |
| [`experiment-critic`](../.agents/skills/experiment-critic/SKILL.md) | daemon (after all per-trial critiques land) | `uv run lab write-comparison` + `uv run lab write-experiment-critique` + `runs/experiments/<id>/results/critic_summary.md` |
| [`cross-experiment-critic`](../.agents/skills/cross-experiment-critic/SKILL.md) | daemon (every `xexp_every` runs) | `uv run lab write-cross-experiment` + `uv run lab idea auto-propose` |
| [`lab-reflect-and-plan`](../.agents/skills/lab-reflect-and-plan/SKILL.md) | daemon (after `tree apply`) | `uv run lab roadmap suggest` + `uv run lab idea auto-propose` |
| [`lab-plan-next`](../.agents/skills/lab-plan-next/SKILL.md) | daemon (close-out) | `uv run lab roadmap done` |
| [`lab-graduate-component`](../.agents/skills/lab-graduate-component/SKILL.md) | human (Cursor) | `uv run lab graduate confirm <slug>` |
