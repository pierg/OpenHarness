"""Orchestrator daemon — autonomous driver of the lab pipeline.

Inner loop, per top entry in `lab/roadmap.md > ## Up next`:

    1.  Spawn `lab-run-experiment` to implement the variant + kick off
        `scripts/exp/start.sh exec ...`.
    2.  Poll the run dir until `results/summary.md` exists.
    3.  `uv run lab ingest <run_dir>`.
    4.  Per uncritiqued trial → spawn `trial-critic`.
    5.  Per unseen task_checksum → spawn `task-features`.
    6.  Once all per-trial critiques land → spawn `experiment-critic`.
    7.  `uv run lab ingest-critiques` (refresh the derived cache).
    8.  Tree close-out (deterministic; no codex spawns):
        a.  `journal_synth.synthesize` — fill ### Aggregate / ###
            Mutation impact / ### Failure modes / ### Linked
            follow-ups in the journal entry.
        b.  `tree_ops.evaluate` + `tree.apply_diff` — write the
            ### Tree effect block; auto-apply AddBranch / Reject /
            NoOp; STAGE Graduate (awaiting `lab graduate confirm`).
    9.  Spawn `lab-reflect-and-plan` (tree-aware planner) to write
        0..N entries under `roadmap.md > ## Up next > ### Suggested`
        and `ideas.md > ## Auto-proposed`.
    10. Every Mth experiment → spawn `cross-experiment-critic`.
    11. Spawn `lab-plan-next` to move the entry to `## Done`.
    12. Sleep (or loop straight into the next entry).

The daemon is **single-tenant**: it acquires
`runs/lab/orchestrator.lock` at startup and refuses to run if
another holder is still live. Stop with `uv run lab daemon stop`
(SIGTERM the recorded pid) or remove the lock manually if known
stale.
"""

from __future__ import annotations

import json
import logging
import os
import re
import signal
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from openharness.lab import codex as codex_adapter
from openharness.lab import critic_io
from openharness.lab import daemon_state as ds
from openharness.lab import db as labdb
from openharness.lab import ingest as labingest
from openharness.lab import journal_synth
from openharness.lab import lab_docs
from openharness.lab import tree as labtree
from openharness.lab import tree_ops
from openharness.lab.paths import (
    EXPERIMENTS_RUNS_ROOT,
    LAB_ROOT,
    ORCHESTRATOR_LOCK_PATH,
    ensure_lab_runs_dir,
)

logger = logging.getLogger("openharness.lab.runner")

DEFAULT_POLL_INTERVAL_SEC = 60
DEFAULT_RUN_TIMEOUT_SEC = 4 * 60 * 60   # 4h cap on a single experiment run.
DEFAULT_IDLE_SLEEP_SEC = 5 * 60          # 5m between roadmap polls when idle.
DEFAULT_XEXP_EVERY = 1                   # cross-experiment-critic every M runs.


# ----- roadmap parsing ------------------------------------------------------


@dataclass(slots=True)
class RoadmapEntry:
    slug: str
    body: str
    idea_id: str | None
    hypothesis: str
    depends_on: list[str] = field(default_factory=list)


_ROADMAP_ENTRY_RE = re.compile(r"^### (\S+)\s*\n", re.MULTILINE)
_BULLET_RE = re.compile(r"^-\s*\*\*([^:]+):\*\*\s*(.*)$", re.MULTILINE)


def parse_up_next(roadmap_path: Path = LAB_ROOT / "roadmap.md") -> list[RoadmapEntry]:
    """Return entries under `## Up next` in priority order (top first)."""
    text = roadmap_path.read_text()
    sections = re.split(r"(?m)^## ", text)
    up_next_block: str | None = None
    for sec in sections:
        if sec.startswith("Up next"):
            up_next_block = sec[len("Up next") :].strip()
            break
    if not up_next_block or up_next_block.startswith("_(none)_"):
        return []
    entries: list[RoadmapEntry] = []
    matches = list(_ROADMAP_ENTRY_RE.finditer(up_next_block))
    for i, m in enumerate(matches):
        slug = m.group(1)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(up_next_block)
        body = up_next_block[start:end].strip()
        idea_id: str | None = None
        hypothesis = ""
        depends_on: list[str] = []
        for bm in _BULLET_RE.finditer(body):
            key, val = bm.group(1).strip(), bm.group(2).strip()
            if key == "Idea":
                m_id = re.search(r"\[`([^`]+)`\]", val)
                idea_id = m_id.group(1) if m_id else None
            elif key == "Hypothesis":
                hypothesis = val
            elif key == "Depends on":
                depends_on = re.findall(r"`([^`]+)`", val)
        entries.append(
            RoadmapEntry(
                slug=slug, body=body, idea_id=idea_id,
                hypothesis=hypothesis, depends_on=depends_on,
            )
        )
    return entries


def is_dependency_satisfied(entry: RoadmapEntry, *, roadmap_path: Path = LAB_ROOT / "roadmap.md") -> bool:
    """A dependency is satisfied if every depends-on slug already lives in `## Done`."""
    if not entry.depends_on:
        return True
    text = roadmap_path.read_text()
    done_block = text.split("## Done", 1)[-1] if "## Done" in text else ""
    for dep in entry.depends_on:
        if not re.search(rf"^### {re.escape(dep)}\b", done_block, re.MULTILINE):
            return False
    return True


# ----- run-dir polling -----------------------------------------------------


def find_latest_run_dir(*, since: datetime | None = None) -> Path | None:
    """Return the newest `runs/experiments/<id>/` whose mtime is after `since`."""
    if not EXPERIMENTS_RUNS_ROOT.is_dir():
        return None
    candidates: list[Path] = []
    for d in EXPERIMENTS_RUNS_ROOT.iterdir():
        if not d.is_dir():
            continue
        if since is not None and datetime.fromtimestamp(d.stat().st_mtime, tz=timezone.utc) < since:
            continue
        candidates.append(d)
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def wait_for_summary(run_dir: Path, *, timeout: int = DEFAULT_RUN_TIMEOUT_SEC,
                     poll: int = DEFAULT_POLL_INTERVAL_SEC) -> bool:
    """Block until `results/summary.md` appears or timeout expires."""
    deadline = time.monotonic() + timeout
    summary = run_dir / "results" / "summary.md"
    while time.monotonic() < deadline:
        if summary.is_file():
            return True
        time.sleep(poll)
    return summary.is_file()


# ----- inner loop ----------------------------------------------------------


@dataclass(slots=True)
class OrchestratorConfig:
    poll_interval_sec: int = DEFAULT_POLL_INTERVAL_SEC
    run_timeout_sec: int = DEFAULT_RUN_TIMEOUT_SEC
    idle_sleep_sec: int = DEFAULT_IDLE_SLEEP_SEC
    xexp_every: int = DEFAULT_XEXP_EVERY
    max_concurrency: int = codex_adapter.DEFAULT_MAX_CONCURRENCY
    once: bool = False
    dry_run: bool = False
    codex_cfg: codex_adapter.CodexConfig | None = None


def _codex_cfg(cfg: OrchestratorConfig) -> codex_adapter.CodexConfig:
    if cfg.codex_cfg is not None:
        return cfg.codex_cfg
    return codex_adapter.CodexConfig(
        max_concurrency=cfg.max_concurrency,
        enforce_orchestrator_lock=True,
        record_in_db=True,
    )


def trials_needing_critique(instance_id: str) -> list[tuple[str, str]]:
    """Trials in `instance_id` lacking `critic/trial-critic.json` on disk.

    The trials registry lives in DuckDB (cheap iteration) but the
    presence test is purely on-disk: a trial is "done" when the
    critic file exists next to its evidence. This avoids having to
    keep the DB cache up to date during a backfill.

    Returns `[(trial_id, trial_dir), ...]` ordered by task and leg
    so logs read predictably during a backfill.
    """
    with labdb.reader() as conn:
        rows = conn.execute(
            "SELECT trial_id, trial_dir FROM trials WHERE instance_id = ? "
            "ORDER BY task_name, leg_id",
            [instance_id],
        ).fetchall()
    out: list[tuple[str, str]] = []
    for trial_id, trial_dir in rows:
        if not critic_io.trial_critique_path(trial_dir).is_file():
            out.append((trial_id, trial_dir))
    return out


def checksums_needing_features(instance_id: str) -> list[str]:
    """`task_checksum`s touched by `instance_id` lacking a feature file.

    Features are keyed by checksum (deduped across experiments), so a
    backfill of one experiment may discover checksums that were ALSO
    touched by other experiments and never extracted. We still scope
    by `instance_id` here because the orchestrator only ever asks
    "what does THIS run need"; a global pass uses
    `lab analyze --include-cross-experiment` plus its own walk.
    """
    with labdb.reader() as conn:
        rows = conn.execute(
            "SELECT DISTINCT task_checksum FROM trials "
            "WHERE instance_id = ? AND task_checksum IS NOT NULL",
            [instance_id],
        ).fetchall()
    out: list[str] = []
    for (checksum,) in rows:
        if not critic_io.task_features_path(checksum).is_file():
            out.append(checksum)
    return out


def comparison_exists(instance_id: str) -> bool:
    """True iff `<run_dir>/critic/comparisons/` has at least one file."""
    run_dir = critic_io.run_dir_from_instance(instance_id)
    if run_dir is None:
        return False
    cmp_dir = run_dir / critic_io.CRITIC_DIRNAME / "comparisons"
    if not cmp_dir.is_dir():
        return False
    return any(cmp_dir.glob("*.json"))


def instance_exists(instance_id: str) -> bool:
    with labdb.reader() as conn:
        (n,) = conn.execute(
            "SELECT count(*) FROM experiments WHERE instance_id = ?",
            [instance_id],
        ).fetchone()
    return int(n) > 0


# Back-compat aliases so existing call sites keep working until they
# migrate to the public names above.
_trials_needing_critique = trials_needing_critique
_checksums_needing_features = checksums_needing_features


def _completed_runs_count() -> int:
    with labdb.reader() as conn:
        (n,) = conn.execute("SELECT count(*) FROM experiments").fetchone()
    return int(n)


@dataclass(slots=True)
class TickResult:
    """Outcome of one full ``_process_entry`` call.

    ``loop`` consults ``outcome`` to decide what to record in
    history and whether to fire the auto-demote gate. The legacy
    boolean return path is retained for callers (smoke tests) that
    only care about success.
    """

    ok: bool
    outcome: ds.TickOutcome
    summary: str | None = None


def _process_entry(entry: RoadmapEntry, cfg: OrchestratorConfig) -> TickResult:
    """Run one full pipeline cycle for one roadmap entry.

    Side-effects on ``daemon-state.json``:

    - Calls :func:`daemon_state.update_tick` at every phase boundary
      so the web UI's `Current tick` panel is always meaningful.
    - Returns a :class:`TickResult` whose ``outcome`` field maps 1:1
      to :data:`daemon_state.TickOutcome` so the caller can record
      one history entry without re-deriving the outcome from logs.

    Does NOT call :func:`daemon_state.begin_tick` /
    :func:`end_tick` — that bracketing is the caller's job (so a
    crash inside this function still leaves a clean history row).
    """
    started_at = datetime.now(timezone.utc)
    cx = _codex_cfg(cfg)
    log = logger.bind(slug=entry.slug) if hasattr(logger, "bind") else logger
    log.info("starting roadmap entry %s", entry.slug)

    if cfg.dry_run:
        log.info("[dry-run] would invoke lab-run-experiment for %s", entry.slug)
        return TickResult(ok=True, outcome="ok", summary="dry-run")

    # 1. variant impl + run kickoff (the skill itself backgrounds harbor
    # via scripts/exp/start.sh, then the agent's final OK/REFUSE summary
    # is what we capture).
    ds.update_tick(phase="spawning", note="lab-run-experiment")
    res = codex_adapter.run(
        "lab-run-experiment",
        [entry.slug, f"hypothesis={entry.hypothesis}",
         f"idea={entry.idea_id or 'baseline'}"],
        cfg=cx,
        expected_orchestrator_pid=os.getpid(),
    )
    log.info(
        "lab-run-experiment exit=%s last=%r log=%s",
        res.exit_code, (res.last_message or "")[:120], res.log_path,
    )
    ds.update_tick(
        phase="post-processing",
        log_path=str(res.log_path) if res.log_path else None,
        note=(res.last_message or "")[:200],
    )

    # Distinguish REFUSE from a hard skill failure. The codex skill
    # returns exit 0 even on a deliberate REFUSE; only the message
    # body tells us. We treat REFUSE as its own outcome class so the
    # exit gate can demote-after-N rather than retrying forever.
    last = (res.last_message or "").strip()
    if last.upper().startswith("REFUSE"):
        msg = f"skill refused: {last[:160]}"
        log.warning("lab-run-experiment REFUSED for %s: %s", entry.slug, last[:160])
        return TickResult(ok=False, outcome="refuse", summary=msg)
    if not res.ok:
        msg = f"spawn exit={res.exit_code}: {last[:160]}"
        log.error("lab-run-experiment failed for %s; aborting cycle", entry.slug)
        return TickResult(ok=False, outcome="error", summary=msg)

    # 2. discover the run directory created by the run skill.
    run_dir = find_latest_run_dir(since=started_at)
    if run_dir is None:
        msg = "skill returned OK but no new runs/experiments/* directory was created"
        log.error("no run directory created for %s; aborting", entry.slug)
        return TickResult(ok=False, outcome="no-run-dir", summary=msg)
    log.info("polling run dir %s", run_dir)
    ds.update_tick(phase="running", note=f"polling {run_dir.name}/results/summary.md")
    if not wait_for_summary(run_dir, timeout=cfg.run_timeout_sec, poll=cfg.poll_interval_sec):
        msg = f"results/summary.md never landed in {run_dir.name} within {cfg.run_timeout_sec}s"
        log.error("run %s did not produce results/summary.md within timeout", run_dir)
        return TickResult(ok=False, outcome="timeout", summary=msg)
    ds.update_tick(phase="post-processing", note=f"ingesting {run_dir.name}")

    # 3. ingest into the lab DB.
    summary = labingest.ingest_run(run_dir)
    log.info(
        "ingested instance=%s legs=%d trials=%d",
        summary.instance_id, summary.legs_inserted, summary.trials_inserted,
    )

    # 4. fan out per-trial critic.
    needing = _trials_needing_critique(summary.instance_id)
    if needing:
        codex_adapter.run_many(
            [("trial-critic", [trial_dir]) for _, trial_dir in needing],
            cfg=cx, parent_run_dir=run_dir,
        )

    # 5. per unseen task_checksum → task-features (one-shot, cached).
    unseen = _checksums_needing_features(summary.instance_id)
    if unseen:
        codex_adapter.run_many(
            [("task-features", [c]) for c in unseen],
            cfg=cx, parent_run_dir=run_dir,
        )

    # 6. once all critiques land, run experiment-critic.
    still_needing = _trials_needing_critique(summary.instance_id)
    if still_needing:
        log.warning(
            "%d trials still missing critiques; skipping experiment-critic",
            len(still_needing),
        )
    else:
        codex_adapter.run(
            "experiment-critic", [summary.instance_id],
            cfg=cx, parent_run_dir=run_dir,
        )

    # (Cross-experiment-critic moved below; it must run AFTER
    #  ingest-critiques + tree apply so it sees the latest state.)

    # 8. refresh the DB cache from the on-disk critic artifacts.
    # The critic spawns above wrote files (single source of truth);
    # this materializes them into the DuckDB cache so the
    # tree-evaluation step below sees the new rows.
    cache_counts = labingest.ingest_critiques([run_dir])
    log.info(
        "ingest-critiques after %s: %s", entry.slug,
        ", ".join(f"{k}={v}" for k, v in cache_counts.items() if v),
    )

    # 9. close the loop on the tree (deterministic; no codex spawns).
    #
    # 9a. Synthesize the journal entry's narrative subsections
    #     (### Aggregate / Mutation impact / Failure modes /
    #      Linked follow-ups) from the critic JSONs we just landed.
    try:
        sections = journal_synth.synthesize(
            slug=entry.slug, instance_id=summary.instance_id,
        )
        log.info(
            "synthesize wrote %d section(s) into '%s': %s",
            len(sections), entry.slug, ", ".join(sections),
        )
    except Exception:
        log.exception("journal synthesize failed for %s", entry.slug)

    # 9b. Compute the TreeDiff and apply it. AddBranch / Reject /
    #     NoOp auto-apply (mutating configs.md and bumping the
    #     status of any uniquely-introduced atoms in components.md).
    #     Graduate is STAGED — written to the journal but not
    #     applied to trunk.yaml until a human runs `lab graduate
    #     confirm`.
    try:
        diff = tree_ops.evaluate(summary.instance_id)
        result = labtree.apply_diff(
            slug=entry.slug, diff=diff, applied_by="auto:daemon",
        )
        log.info(
            "tree apply %s: kind=%s applied=%s target=%s",
            entry.slug, diff.kind, result.applied, diff.target_id,
        )
        if diff.kind == "graduate" and not result.applied:
            log.warning(
                "STAGED graduate for %s → %s; awaiting `uv run lab "
                "graduate confirm %s --applied-by human:<name>`",
                entry.slug, diff.target_id, entry.slug,
            )
    except Exception:
        log.exception("tree apply failed for %s", entry.slug)

    # 9c. Tree-aware planner: reads the just-updated tree + the
    #     latest journal entries; writes 0..N entries under
    #     `roadmap.md > ## Up next > ### Suggested` and 0..N
    #     entries under `ideas.md > ## Auto-proposed`. Humans
    #     review and promote.
    codex_adapter.run(
        "lab-reflect-and-plan",
        [f"--instance={summary.instance_id}"],
        cfg=cx, parent_run_dir=run_dir,
    )

    # 10. cross-experiment-critic (still gated on xexp_every).
    if _completed_runs_count() % max(cfg.xexp_every, 1) == 0:
        codex_adapter.run("cross-experiment-critic", [], cfg=cx, parent_run_dir=run_dir)

    # 11. close the loop on the roadmap.
    codex_adapter.run(
        "lab-plan-next",
        ["done", entry.slug,
         f"--ran=runs/experiments/{summary.instance_id}",
         "--outcome=auto"],
        cfg=cx, parent_run_dir=run_dir,
    )
    log.info("roadmap entry %s closed", entry.slug)
    ds.update_tick(phase="done", note=f"closed runs/experiments/{summary.instance_id}")
    return TickResult(
        ok=True, outcome="ok",
        summary=f"runs/experiments/{summary.instance_id}",
    )


def _select_next_entry(
    ready: list[RoadmapEntry], state: ds.DaemonState,
) -> RoadmapEntry | None:
    """Pick the next entry to process given current state.

    - ``paused``      → never picks anything.
    - ``manual``      → picks the highest-priority entry whose slug
                        is in ``approved_slugs``. (Order = roadmap
                        order, not approval order, so the operator
                        can approve out of order without changing
                        the queue.)
    - ``autonomous``  → picks ``ready[0]`` (legacy behaviour).
    """
    if state.mode == "paused":
        return None
    if state.mode == "autonomous":
        return ready[0] if ready else None
    # manual mode
    approved = set(state.approved_slugs)
    for e in ready:
        if e.slug in approved:
            return e
    return None


def _idle_log(reason: str, sleep_sec: int) -> None:
    """Log an idle reason at most once per minute to avoid journal spam."""
    now = time.monotonic()
    last = getattr(_idle_log, "_last_at", 0.0)
    last_reason = getattr(_idle_log, "_last_reason", None)
    if now - last > 60 or reason != last_reason:
        logger.info("%s; sleeping %ds", reason, sleep_sec)
        _idle_log._last_at = now            # type: ignore[attr-defined]
        _idle_log._last_reason = reason     # type: ignore[attr-defined]


def loop(cfg: OrchestratorConfig | None = None) -> None:
    """Main daemon loop. Consults :mod:`daemon_state` every tick.

    The loop never directly mutates the markdown roadmap (that's the
    skills' job via deterministic ``lab roadmap …`` helpers). The
    one exception is the auto-demote gate: when an entry has failed
    too many times in a row, the loop calls
    :func:`lab_docs.demote_to_suggested` directly so the daemon can
    keep moving without waiting on a codex spawn.
    """
    cfg = cfg or OrchestratorConfig()
    while True:
        state = ds.load()
        entries = parse_up_next()
        ready = [e for e in entries if is_dependency_satisfied(e)]
        entry = _select_next_entry(ready, state)
        if entry is None:
            if cfg.once:
                logger.info("nothing ready, exiting (--once)")
                return
            if state.mode == "paused":
                _idle_log("daemon mode=paused", cfg.idle_sleep_sec)
            elif state.mode == "manual" and ready and not state.approved_slugs:
                _idle_log(
                    f"daemon mode=manual; {len(ready)} ready entries but no approvals",
                    cfg.idle_sleep_sec,
                )
            elif state.mode == "manual":
                _idle_log(
                    "daemon mode=manual; no approved+ready entry to run",
                    cfg.idle_sleep_sec,
                )
            else:
                _idle_log("no ready roadmap entries", cfg.idle_sleep_sec)
            time.sleep(cfg.idle_sleep_sec)
            continue

        # Manual mode: consume the approval up front. If anything
        # explodes inside _process_entry, we'd rather force the
        # operator to re-approve than have the daemon retry on its
        # own — that's the whole point of "consumed" approvals.
        if state.mode == "manual":
            ds.consume_approval(entry.slug, actor="daemon")

        ds.begin_tick(
            ds.ActiveTick(
                slug=entry.slug,
                phase="spawning",
                started_at=datetime.now(timezone.utc),
            ),
            actor="daemon",
        )

        result: TickResult
        try:
            result = _process_entry(entry, cfg)
        except codex_adapter.CodexAdapterError as exc:
            logger.error("codex adapter error for %s: %s", entry.slug, exc)
            result = TickResult(ok=False, outcome="error", summary=f"codex: {exc}")
        except Exception as exc:
            logger.exception("unhandled error processing %s", entry.slug)
            result = TickResult(ok=False, outcome="error", summary=f"unhandled: {exc}")

        # End-of-tick bookkeeping (history + failure counter).
        _, failure_rec = ds.end_tick(
            outcome=result.outcome,
            summary=result.summary,
            actor="daemon",
        )

        # Exit gate: applies in BOTH modes. In manual mode the
        # operator already paid attention by approving; we still
        # don't want a busted entry to keep eating approvals if it
        # keeps failing.
        if failure_rec is not None and failure_rec.count >= state.max_failures_before_demote:
            try:
                lab_docs.demote_to_suggested(slug=entry.slug)
                logger.warning(
                    "auto-demoted %s to Suggested after %d consecutive %s failures: %s",
                    entry.slug, failure_rec.count, failure_rec.last_outcome,
                    failure_rec.last_error,
                )
                # Reset the counter so the next operator promotion
                # of the same slug starts fresh.
                ds.reset_failures(entry.slug, actor="daemon")
                # And record the demotion as a synthetic history row
                # so the UI shows what happened, not just "refuse"
                # twice in a row.
                with ds.mutate(actor="daemon") as st:
                    st.history.append(
                        ds.TickHistoryEntry(
                            slug=entry.slug,
                            started_at=datetime.now(timezone.utc),
                            ended_at=datetime.now(timezone.utc),
                            outcome="auto-demoted",
                            phase_reached="done",
                            duration_sec=0.0,
                            summary=(
                                f"auto-demoted to Suggested after "
                                f"{failure_rec.count} {failure_rec.last_outcome} failures"
                            ),
                        )
                    )
            except Exception:
                logger.exception(
                    "auto-demote failed for %s; entry stays in Up next "
                    "(operator should investigate)",
                    entry.slug,
                )

        if cfg.once:
            return


# ----- foreground / daemon entrypoints (called by `uv run lab daemon …`) ---


def _foreground_log_init(level: int = logging.INFO) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )


def _install_signal_cleanup() -> None:
    """Convert SIGTERM/SIGINT into ``KeyboardInterrupt`` so the lock
    context manager's ``finally`` block runs and unlinks the lock.

    Without this, the default Python SIGTERM handler exits the
    interpreter without running ``__exit__``, leaving a stale
    ``runs/lab/orchestrator.lock`` behind and breaking
    ``systemctl --user restart openharness-daemon`` with
    "Orchestrator lock already held".

    SIGTERM is what systemd (and ``daemon stop``) sends; SIGINT is
    Ctrl-C in the foreground case.
    """

    def _raise(_signum: int, _frame: object) -> None:
        # Raising KeyboardInterrupt here propagates out of ``loop``,
        # unwinds through ``orchestrator_lock``'s finally, then
        # bubbles up so the typer CLI exits with the conventional
        # 130 (SIGINT) status. systemd sees that as a clean stop
        # because we used ``Restart=on-failure`` (not always).
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _raise)
    signal.signal(signal.SIGINT, _raise)


def start(*, foreground: bool = True, once: bool = False, dry_run: bool = False) -> None:
    """Start the orchestrator in the current process.

    `once=True` runs at most one roadmap entry and exits — useful for
    smoke tests. `foreground=True` keeps the loop attached to the
    terminal; the typer CLI shells out via tmux for true daemon mode.
    """
    ensure_lab_runs_dir()
    _foreground_log_init()
    _install_signal_cleanup()
    cfg = OrchestratorConfig(once=once, dry_run=dry_run)
    try:
        with codex_adapter.orchestrator_lock(owner="lab-runner"):
            # Boot-time hygiene on daemon-state.json:
            # - Clear any leftover active_tick from a previous crash;
            #   the tick that was in flight is gone, no point pretending.
            # - The mode survives restarts (it's the operator's
            #   declared intent, not a runtime fact).
            ds.clear_active_tick(actor="daemon-boot")
            state = ds.load()
            logger.info(
                "orchestrator started (pid=%d, once=%s, dry_run=%s, mode=%s, "
                "approvals=%d, max_failures_before_demote=%d)",
                os.getpid(), once, dry_run, state.mode,
                len(state.approved_slugs), state.max_failures_before_demote,
            )
            loop(cfg)
    except KeyboardInterrupt:
        # Caught here (rather than letting it propagate to the typer
        # entry point) so we get one tidy log line + exit 0. The lock
        # has already been released by orchestrator_lock's finally.
        # Also drop any in-flight active_tick — the codex spawn under
        # it (if any) was killed by systemd's SIGTERM cascade.
        try:
            ds.clear_active_tick(actor="daemon-shutdown")
        except Exception:
            logger.exception("failed to clear active_tick during shutdown")
        logger.info("orchestrator received signal, exiting cleanly")


def stop() -> None:
    """SIGTERM the recorded orchestrator pid."""
    if not ORCHESTRATOR_LOCK_PATH.is_file():
        logger.info("no orchestrator lock at %s; nothing to stop", ORCHESTRATOR_LOCK_PATH)
        return
    payload = json.loads(ORCHESTRATOR_LOCK_PATH.read_text())
    pid = int(payload.get("pid") or 0)
    if not pid:
        logger.warning("malformed lock; removing")
        ORCHESTRATOR_LOCK_PATH.unlink()
        return
    try:
        os.kill(pid, signal.SIGTERM)
        logger.info("sent SIGTERM to orchestrator pid=%d", pid)
    except ProcessLookupError:
        logger.warning("pid %d gone; removing stale lock", pid)
        ORCHESTRATOR_LOCK_PATH.unlink()


def status() -> dict:
    if not ORCHESTRATOR_LOCK_PATH.is_file():
        return {"running": False}
    try:
        payload = json.loads(ORCHESTRATOR_LOCK_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {"running": False, "lock_corrupted": True}
    pid = int(payload.get("pid") or 0)
    alive = False
    if pid:
        try:
            os.kill(pid, 0)
            alive = True
        except (ProcessLookupError, PermissionError):
            alive = False
    return {"running": alive, "pid": pid, "lock": payload}
