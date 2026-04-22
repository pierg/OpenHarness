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

## Inner loop (one tick): the 6-phase pipeline

`src/openharness/lab/runner.py` drives a deterministic
**6-phase pipeline** per roadmap entry. Each phase has its own
record under `runs/lab/state/<slug>/phases.json` (status:
`pending` / `running` / `ok` / `failed` / `skipped`) so the daemon
can be restarted at any time and **resume from the first
unfinished phase** — no idempotency hacks bolted onto a monolithic
spawn. Inspect with `uv run lab phases show [<slug>]`.

For each tick the daemon picks the top ready entry from
`lab/roadmap.md > ## Up next` (skipping any whose `Depends on:`
slugs aren't all in `## Done`), holds `runs/lab/orchestrator.lock`,
loads-or-initialises `phases.json`, then walks the phases:

1.  **Phase 0 — preflight** (deterministic; `preflight.py`).
    Asserts the parent repo is clean (modulo `lab/*.md`), captures
    the current HEAD branch + SHA, optionally pushes if
    `LAB_AUTO_PUSH=1`, then **creates a git worktree** at
    `../OpenHarness.worktrees/lab-<slug>` on a fresh branch
    `lab/<slug>` based off that exact SHA (idempotent — adopts an
    existing worktree if the branch is already there). Writes the
    worktree path + base SHA into `phases.preflight.payload`.
    Phase 0 is **skipped for "baseline"-style entries** that don't
    need a code change (see `_BASELINE_IDEA_IDS`).

2.  **Phase 1 — design** (codex skill: `lab-design-variant`,
    sandbox `read-only`). Reads the idea + roadmap entry +
    relevant codebase context and produces
    `runs/lab/state/<slug>/design.md`: a concise design doc with
    the proposed change, files to touch, validation strategy, and
    risks. **No code edits** — this phase exists so a cheap
    read-only pass can catch "we shouldn't even build this" before
    we spend implementation tokens. Skipped for baseline entries.

3.  **Phase 2 — implement** (codex skill:
    `lab-implement-variant`, sandbox `workspace-write` scoped to
    the worktree). Reads `design.md`, **edits the worktree** to
    realise the variant, runs the local validations the design
    promised (typically `uv run pytest <focused>` and any
    component-specific smoke), and **commits** the changes. Writes
    `runs/lab/state/<slug>/implement.json` summarising the commits
    landed, validations run, and files touched. Skipped for
    baseline entries (worktree's HEAD is already the right code).

4.  **Phase 3 — run** (deterministic; `phase_run.py`). Resolves the
    experiment YAML from the worktree, appends the journal stub to
    `lab/experiments.md` (`## YYYY-MM-DD — <slug>` with placeholder
    Branch / Run bullets), then launches `uv run exec <spec>` as
    a **detached subprocess** (`Popen(..., start_new_session=True)`)
    so a daemon restart doesn't kill the in-flight run. Crucially
    the spawn passes `--root <parent-repo>` so all artifacts land in
    the parent repo's `runs/experiments/<id>/`, not scattered across
    worktrees. The phase polls
    `runs/experiments/<id>/results/summary.md` (4 h default) and
    rewrites the journal entry's `**Run:**` bullet to point at the
    real instance id via `lab experiments set-run-path`.

5.  **Phase 4 — critique** (deterministic + critic spawns; same
    sequence as before, just promoted to a phase boundary):
    -   `uv run lab ingest runs/experiments/<id>` — DB cache.
        `_scan_misconfigurations` emits one row per (trial × kind)
        into `misconfigurations` for every `unknown_id`,
        `architecture_mismatch`, `agent_mismatch`, or
        `conflicts_with`.
    -   Fan out **`trial-critic`** for each trial whose
        `<trial_dir>/critic/trial-critic.json` doesn't yet exist
        (one file per spawn; no DB writes).
    -   Fan out **`task-features`** for each `task_checksum` whose
        `runs/lab/task_features/<checksum>.json` doesn't yet exist.
    -   Once every trial has a critic file, spawn
        **`experiment-critic`** — writes one
        `<run_dir>/critic/comparisons/<task>.json` per task plus
        `<run_dir>/critic/experiment-critic.json` plus the
        human-facing `runs/experiments/<id>/results/critic_summary.md`
        (uses codex's `multi_agent` to fan per-task comparisons
        across subagents).
    -   `uv run lab ingest-critiques` — refresh the DB cache.
    -   Close the journal: `uv run lab experiments synthesize <slug>`
        fills `### Aggregate / Mutation impact / Failure modes /
        Linked follow-ups`; `uv run lab tree apply <slug>` runs
        `tree_ops.evaluate`, writes `### Tree effect`, and either
        auto-applies (AddBranch / Reject / NoOp) or stages
        (Graduate) the diff.
    -   Spawn **`lab-reflect-and-plan`** (tree-aware planner) and
        **`lab-plan-next`** (move the entry to `## Done` with a
        link to the journal entry).
    -   Every `xexp_every` completed experiments (default 1),
        spawn **`cross-experiment-critic`** — snapshots the apex
        view to `runs/lab/cross_experiment/<ts>__<spawn_id>.json`
        and may append follow-ups under `## Auto-proposed`. It does
        **not** write to the tree; only `tree apply` does.

6.  **Phase 5 — finalize** (codex skill: `lab-finalize-pr`, sandbox
    `workspace-write`). Reads the verdict (`AddBranch` / `Graduate`
    / `Reject` / `NoOp`) and decides what to do with the worktree's
    branch:
    -   **AddBranch / Graduate** — pushes `lab/<slug>`, opens a PR
        via `gh pr create`, then rewrites the journal entry's
        `**Branch:**` bullet to `[lab/<slug>](<pr_url>)` via
        `lab experiments set-branch`.
    -   **Reject / NoOp** — leaves the branch unpushed, sets
        `**Branch:**` to `lab/<slug> — not opened (<reason>)`,
        and signals worktree cleanup.
    Writes `runs/lab/state/<slug>/finalize.json`. The daemon then
    removes the worktree (`preflight.remove_worktree`) for
    cleanup-flagged finalisations.

After every phase that mutates `lab/*.md`, the daemon stages and
commits the change in the parent repo (`_commit_lab_changes`); set
`LAB_AUTO_PUSH=1` to also push. This produces a granular audit
trail in the parent repo's git history (one commit per phase, not
one bag of changes per experiment).

If any phase fails, the daemon marks it `failed`, **leaves the
roadmap entry in `## Up next`**, and the next tick picks up at the
first unfinished phase — reset just one phase with
`uv run lab phases reset <slug> --phase <name>` if you need to
force a redo. Nothing in the lab markdowns silently rots.

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
| `lab/experiments.md` entry header (Type / Trunk / Mutation / Hypothesis / Branch / Run) | no | yes | daemon Phase 3 (`lab experiments append-entry --branch lab/<slug>` + `set-run-path` once instance id is known) + Phase 5 / `lab-finalize-pr` (rewrites Branch bullet via `lab experiments set-branch`) |
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
| `runs/lab/state/<slug>/phases.json` | — | `phase_state.py` | per-slug pipeline state (one record per phase, status + payload + error). `uv run lab phases show/reset` operates on it. |
| `runs/lab/state/<slug>/design.md` | — | `lab-design-variant` (Phase 1) | the design doc consumed by Phase 2 |
| `runs/lab/state/<slug>/implement.json` | — | `lab-implement-variant` (Phase 2) | summary of commits / validations / files touched |
| `runs/lab/state/<slug>/finalize.json` | — | `lab-finalize-pr` (Phase 5) | verdict-routing decision + cleanup flag |
| `../OpenHarness.worktrees/lab-<slug>/` | — | `preflight.py` (Phase 0 + cleanup) | per-experiment git worktree on branch `lab/<slug>` |
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

## Web UI

Launch the operator console (FastAPI + HTMX, served on `127.0.0.1:8765` by default):

```bash
uv run lab webui
```

Pages: `/` (home), `/pending`, `/roadmap`, `/ideas`, `/tree`,
`/components`, `/components-perf`, `/experiments`,
`/experiments/<id>`, `/experiments/<id>/trials/<trial_id>`,
`/tasks`, `/tasks/<checksum>`, `/spawns`, `/daemon`, `/audit`.

All mutating buttons (Promote, Demote, Remove, Confirm trunk swap,
Move idea, Start/Stop daemon, …) `POST /api/cmd`, which shells out
to the same `uv run lab …` CLI you would run by hand. Successful
runs emit `HX-Trigger` events (`lab-roadmap-changed`,
`lab-ideas-changed`, `lab-pending-changed`, `lab-tree-changed`,
`lab-daemon-changed`) so the affected list refreshes without a
page reload.

Daemon controls live on `/` and `/daemon`: a green **Start** button
when stopped, an amber **Restart** + a red **Stop** when running.
Under the hood every button shells out to
`systemctl --user start|restart|stop openharness-daemon.service` —
systemd owns the process tree so journald gets stdout/stderr,
`Restart=on-failure` recovers crashes, and the operator can read
live logs with `journalctl --user -u openharness-daemon -f`.
The status panel auto-refreshes every 5 s as a backstop for state
changes that didn't originate from this browser tab.

The `/daemon` page additionally exposes:

- a **Services** panel — every supervised systemd unit
  (`openharness-lab` for the web UI, `openharness-daemon` for the
  orchestrator) with its current state and Start / Restart / Stop
  buttons. Restarting the web UI tears down the current HTMX session
  briefly, which the operator's browser handles transparently.
- a **Process tree** panel — live `psutil` view of the daemon's
  descendants (uv → python → codex → …), with per-row
  `[kill]` buttons that route through `kill-process` in the
  whitelist. The precheck refuses any PID that isn't a descendant
  of the running daemon, so operators can safely reap a wedged
  experiment subprocess without ever touching unrelated VM
  processes.

### Process supervision

The two long-running services run as **systemd `--user` units**:

| Unit | Purpose | Restart policy |
| --- | --- | --- |
| `openharness-lab.service` | FastAPI web UI | `Restart=always` |
| `openharness-daemon.service` | Orchestrator daemon (walks the roadmap) | `Restart=on-failure`, 60 s SIGTERM grace |

Install / refresh both from the repo:

```bash
bash scripts/systemd/install.sh         # install + start
bash scripts/systemd/install.sh --no-start
bash scripts/systemd/install.sh --uninstall
```

#### `lab svc` — canonical operator wrapper

Day-to-day operation goes through one Typer subcommand that hides
the verbose `systemctl --user` / `journalctl --user` syntax and adds
an at-a-glance health check. It's a real subcommand of the existing
`lab` CLI, so nothing extra to install or alias:

```bash
lab svc                       # = `lab svc status` (default)
lab svc status                # services + port + lock + log shortcuts
lab svc restart daemon        # restart the orchestrator
lab svc restart web           # restart the web UI (browser sees a brief gap)
lab svc restart               # both (defaults to "all")
lab svc stop  daemon
lab svc start daemon
lab svc logs  daemon          # last 100 lines from journalctl
lab svc logs  daemon -f       # follow live (Ctrl-C to detach)
lab svc tail  daemon          # alias for `logs daemon -f`
lab svc url                   # → http://127.0.0.1:8765/
lab svc install               # one-shot install/refresh of the units
```

Short unit aliases: `web` / `webui` / `ui` ↔ `openharness-lab`,
`daemon` / `d` / `orch` ↔ `openharness-daemon`, `all` / `both`
(default) ↔ both. Any other word is rejected so a typo can't
target an unrelated systemd unit.

`scripts/lab-svc.sh` is the same surface as a standalone bash script,
kept only for non-Python contexts (SSH ForceCommand, CI bootstrap,
recovery from a broken venv).

`lab svc status` example output:

```
Services
  ● web UI                  running (running)
    pid 2087351   since 2026-04-21 22:26:11 UTC
  ● orchestrator daemon     running (running)
    pid 2137756   since 2026-04-21 22:41:40 UTC

Web UI
  ● listening at http://127.0.0.1:8765/

Orchestrator lock
  ● held by pid 2137759 (since 2026-04-21T22:41:41+00:00)

Tail logs
  lab svc logs daemon -f   # orchestrator
  lab svc logs web -f      # web UI
```

#### Raw `systemctl` (when you want it)

```bash
systemctl --user status   openharness-daemon
systemctl --user restart  openharness-daemon
journalctl  --user -u openharness-daemon -f
```

#### Don't run `uv run lab webui` while the unit is up

The `lab webui` CLI refuses to bind if the systemd unit is already
running and tells you what to do instead — so the canonical "oops
port 8765 is in use" mistake is one error message, not a puzzling
uvicorn traceback.

#### Survive reboot when nobody is logged in

```bash
loginctl enable-linger $USER
```

### Auth

The webui has **two operating modes**, picked by env vars at
process start. All `GET` pages stay open in both modes (read-only
state lives in markdown files anyway); the modes only gate
`POST /api/cmd`. The startup banner prints which mode is active.

#### 1. `open` — default, loopback / SSH-tunnel

No env vars set. `/api/cmd` is unrestricted. The trust boundary is
the network — uvicorn binds to `127.0.0.1` and you reach it either
on the host directly or via `ssh -L 8765:127.0.0.1:8765 vm` from a
laptop you alone use. Binding to a non-loopback interface
(`--host 0.0.0.0`) in this mode prints a red warning at startup.

#### 2. `proxy` — SSO via Cloudflare Access / Google IAP

For sharing the lab with named collaborators. The webui delegates
*authentication* to a reverse proxy that already knows who you are
and then *authorises* the email against role allow-lists from env:

```bash
export LAB_TRUST_PROXY_AUTH=cloudflare-access     # or "iap"
export LAB_ADMIN_EMAILS="you@gmail.com,cofounder@gmail.com"
export LAB_VIEWER_EMAILS="prof@berkeley.edu"      # optional
uv run lab webui --host 127.0.0.1 --port 8765
```

| Email is in… | Role | What they see |
| --- | --- | --- |
| `LAB_ADMIN_EMAILS` | **admin** | Every page + every write button. Their email is recorded as `actor` in the audit log. |
| `LAB_VIEWER_EMAILS` | **viewer** | Every page renders, but write buttons are hidden and `/api/cmd` returns 403 with a "Read-only role" card. A blue banner names them. |
| neither | **anonymous** (rejected) | Read-only with an amber "Sign in / not authorised" banner; `/api/cmd` returns 403. |

The trusted header is `Cf-Access-Authenticated-User-Email` for
Cloudflare Access; `X-Goog-Authenticated-User-Email` (with the
`accounts.google.com:` prefix stripped) for IAP.

**Threat-model note**: the webui *trusts the header*. Always bind
to `127.0.0.1` (or a Unix socket) so only the local proxy can
inject it — anyone with shell on the VM could otherwise spoof the
header and bypass SSO. Misspellings of `LAB_TRUST_PROXY_AUTH` fall
back to `open` mode (and the `--host 0.0.0.0` warning fires if
applicable) rather than silently honouring the header.

Concrete deployment for `lab.pierg.dev`:

1. **cloudflared** runs as a system service (`sudo cloudflared
   service install`) and forwards `lab.pierg.dev → http://127.0.0.1:8765`.
2. **Cloudflare Access** policy on `lab.pierg.dev`: identity
   providers = Google (added once under *Zero Trust → Settings →
   Authentication → Login methods*); allow rule lists every email
   in `LAB_ADMIN_EMAILS ∪ LAB_VIEWER_EMAILS`. Everyone else hits
   Cloudflare's "you don't have access" page before the request
   reaches the origin.
3. **lab webui** runs as a `systemd --user` unit (see
   `~/.config/systemd/user/openharness-lab.service`). The unit's
   `Environment=` lines hold the three `LAB_*` env vars so the
   admin/viewer lists survive restarts.

To add a new collaborator:

- **Read-only access (e.g. a co-author):** add their email to
  `LAB_VIEWER_EMAILS` in the systemd unit + add an Include rule
  for it in the Cloudflare Access policy. Restart the service.
- **Admin access:** same, but `LAB_ADMIN_EMAILS`. Cloudflare's
  policy is the *outer* gate (can they see anything at all);
  `LAB_*_EMAILS` is the *inner* gate (what they can do). Both
  must list the email.

#### Audit trail

Every `/api/cmd` call that gets past the auth gate (validation
errors, exit-zero runs, exit-nonzero runs) appends a JSON row to
`runs/lab/web_commands.jsonl`. In proxy mode the `actor` field
is the authenticated email; in open mode it falls back to
`"human:webui"` (or whatever the operator set as `_actor` /
`X-Lab-Actor` / `LAB_USER`). The `/audit` page tails the latest
200 rows. Pre-auth rejections (401 / 403) are not audited here —
look at the uvicorn / `journalctl --user -u openharness-lab` log
for those, where Cloudflare Access also records the SSO email.

## When something goes wrong

| Symptom | First check | Fix |
| --- | --- | --- |
| `daemon start` complains "already running" | `systemctl --user status openharness-daemon` (preferred) or `uv run lab daemon status` | If the pid is gone, `rm runs/lab/orchestrator.lock`. Under systemd, `systemctl --user restart openharness-daemon` clears most of these. |
| `Address already in use` on `lab webui` start | `systemctl --user status openharness-lab` | The unit is already running — connect to `127.0.0.1:8765` directly, or `systemctl --user restart openharness-lab` to pick up code changes. |
| Daemon idles forever | `uv run lab daemon attach`; check the log line "no ready roadmap entries" | Either the queue is empty or every entry's `Depends on:` is unmet. |
| Phase `run` never produces a summary.md | tail `runs/experiments/<id>/legs/<leg>/harbor/.../trial.log`; `uv run lab phases show <slug>` to confirm `run: failed` | Same failure modes as a hand-launched experiment; the daemon timed out and left the slug pinned at `run: failed`. Fix the cause, then `uv run lab phases reset <slug> --phase run` (the next tick replays Phase 3 onwards). |
| Phase `design` / `implement` / `finalize` keeps failing | `uv run lab phases show <slug>`; tail the matching `runs/lab/logs/...lab-<phase>...log` for the prompt + events; for `implement` also `cd ../OpenHarness.worktrees/lab-<slug>` and `git status` / `git log --oneline` to see what landed | Resolve the root cause (e.g. update the SKILL.md, fix a syntax error the agent ran into, top up codex quota), then `uv run lab phases reset <slug> --phase <name>` to retry just that one phase on the next tick. |
| Worktree under `../OpenHarness.worktrees/lab-<slug>` is stale | `git worktree list` from the parent repo | `uv run lab preflight remove <slug>` (idempotent — also drops the `lab/<slug>` branch unless you pass `--keep-branch`). |
| Critic skill fails repeatedly for one trial | grep the matching log under `runs/lab/logs/` for the skill name + trial id | Re-run by hand: `codex exec` against the same skill+args; usually it's a JSON-shape mismatch we can fix in the SKILL.md. |
| `misconfigurations` keeps growing | `SELECT DISTINCT kind, component_id FROM misconfigurations` | Either fix the offending agent YAML, the component spec, or downgrade the check by relaxing `applies_to`. |
| Schema mismatch errors after a code update | run `uv run lab init` | Applies any new `src/openharness/lab/migrations/NNNN_*.sql`. |

## Skills involved

| Skill | Spawned by | Persists via |
| --- | --- | --- |
| [`lab-design-variant`](../.agents/skills/lab-design-variant/SKILL.md) | daemon Phase 1 | `runs/lab/state/<slug>/design.md` (read-only sandbox) |
| [`lab-implement-variant`](../.agents/skills/lab-implement-variant/SKILL.md) | daemon Phase 2 | git commits in `../OpenHarness.worktrees/lab-<slug>/` + `runs/lab/state/<slug>/implement.json` |
| [`lab-finalize-pr`](../.agents/skills/lab-finalize-pr/SKILL.md) | daemon Phase 5 | `git push` + `gh pr create` (AddBranch / Graduate) **or** unpushed branch (Reject / NoOp); `uv run lab experiments set-branch` rewrites the journal entry's Branch bullet either way; `runs/lab/state/<slug>/finalize.json` carries the cleanup flag |
| [`trial-critic`](../.agents/skills/trial-critic/SKILL.md) | daemon Phase 4 (per uncritiqued trial) | `uv run lab write-trial-critique` |
| [`task-features`](../.agents/skills/task-features/SKILL.md) | daemon Phase 4 (per unseen `task_checksum`) | `uv run lab write-task-features` |
| [`experiment-critic`](../.agents/skills/experiment-critic/SKILL.md) | daemon Phase 4 (after all per-trial critiques land) | `uv run lab write-comparison` + `uv run lab write-experiment-critique` + `runs/experiments/<id>/results/critic_summary.md` |
| [`cross-experiment-critic`](../.agents/skills/cross-experiment-critic/SKILL.md) | daemon Phase 4 (every `xexp_every` runs) | `uv run lab write-cross-experiment` + `uv run lab idea auto-propose` |
| [`lab-reflect-and-plan`](../.agents/skills/lab-reflect-and-plan/SKILL.md) | daemon Phase 4 (after `tree apply`) | `uv run lab roadmap suggest` + `uv run lab idea auto-propose` |
| [`lab-plan-next`](../.agents/skills/lab-plan-next/SKILL.md) | daemon Phase 4 (close-out) | `uv run lab roadmap done` |
| [`lab-graduate-component`](../.agents/skills/lab-graduate-component/SKILL.md) | human (Cursor) | `uv run lab graduate confirm <slug>` |
