"""Orchestrator daemon — autonomous driver of the lab pipeline.

Inner loop, per top entry in `lab/roadmap.md > ## Up next`:

    1. Spawn `lab-run-experiment` to implement the variant + kick off
       `scripts/exp/start.sh exec ...`.
    2. Poll the run dir until `results/summary.md` exists.
    3. `uv run lab ingest <run_dir>`.
    4. Per uncritiqued trial → spawn `trial-critic`.
    5. Per unseen task_checksum → spawn `task-features`.
    6. Once all per-trial critiques land → spawn `experiment-critic`.
    7. Every Mth experiment → spawn `cross-experiment-critic`.
    8. Spawn `lab-plan-next` to move the entry to `## Done`.
    9. Sleep (or loop straight into the next entry).

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
from openharness.lab import db as labdb
from openharness.lab import ingest as labingest
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


def _trials_needing_critique(instance_id: str) -> list[tuple[str, str]]:
    with labdb.reader() as conn:
        rows = conn.execute(
            """
            SELECT t.trial_id, t.trial_dir
            FROM trials t LEFT JOIN trial_critiques c USING (trial_id)
            WHERE t.instance_id = ? AND c.trial_id IS NULL
            ORDER BY t.task_name, t.leg_id
            """,
            [instance_id],
        ).fetchall()
    return [(r[0], r[1]) for r in rows]


def _checksums_needing_features(instance_id: str) -> list[str]:
    with labdb.reader() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT t.task_checksum
            FROM trials t LEFT JOIN task_features f USING (task_checksum)
            WHERE t.instance_id = ? AND t.task_checksum IS NOT NULL
              AND f.task_checksum IS NULL
            """,
            [instance_id],
        ).fetchall()
    return [r[0] for r in rows]


def _completed_runs_count() -> int:
    with labdb.reader() as conn:
        (n,) = conn.execute("SELECT count(*) FROM experiments").fetchone()
    return int(n)


def _process_entry(entry: RoadmapEntry, cfg: OrchestratorConfig) -> bool:
    """Run one full pipeline cycle for one roadmap entry. Returns success."""
    started_at = datetime.now(timezone.utc)
    cx = _codex_cfg(cfg)
    log = logger.bind(slug=entry.slug) if hasattr(logger, "bind") else logger
    log.info("starting roadmap entry %s", entry.slug)

    if cfg.dry_run:
        log.info("[dry-run] would invoke lab-run-experiment for %s", entry.slug)
        return True

    # 1. variant impl + run kickoff (the skill itself backgrounds harbor
    # via scripts/exp/start.sh, then the agent's final OK/REFUSE summary
    # is what we capture).
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
    if not res.ok:
        log.error("lab-run-experiment failed for %s; aborting cycle", entry.slug)
        return False

    # 2. discover the run directory created by the run skill.
    run_dir = find_latest_run_dir(since=started_at)
    if run_dir is None:
        log.error("no run directory created for %s; aborting", entry.slug)
        return False
    log.info("polling run dir %s", run_dir)
    if not wait_for_summary(run_dir, timeout=cfg.run_timeout_sec, poll=cfg.poll_interval_sec):
        log.error("run %s did not produce results/summary.md within timeout", run_dir)
        return False

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

    # 7. every Mth completed experiment → cross-experiment-critic.
    if _completed_runs_count() % max(cfg.xexp_every, 1) == 0:
        codex_adapter.run("cross-experiment-critic", [], cfg=cx, parent_run_dir=run_dir)

    # 8. close the loop on the roadmap.
    codex_adapter.run(
        "lab-plan-next",
        ["done", entry.slug,
         f"--ran=runs/experiments/{summary.instance_id}",
         "--outcome=auto"],
        cfg=cx, parent_run_dir=run_dir,
    )
    log.info("roadmap entry %s closed", entry.slug)
    return True


def loop(cfg: OrchestratorConfig | None = None) -> None:
    cfg = cfg or OrchestratorConfig()
    while True:
        entries = parse_up_next()
        ready = [e for e in entries if is_dependency_satisfied(e)]
        if not ready:
            if cfg.once:
                logger.info("dry pass: nothing ready, exiting (--once)")
                return
            logger.info(
                "no ready roadmap entries; sleeping %ds", cfg.idle_sleep_sec
            )
            time.sleep(cfg.idle_sleep_sec)
            continue
        entry = ready[0]
        try:
            _process_entry(entry, cfg)
        except codex_adapter.CodexAdapterError as exc:
            logger.error("codex adapter error for %s: %s", entry.slug, exc)
        except Exception:
            logger.exception("unhandled error processing %s", entry.slug)
        if cfg.once:
            return


# ----- foreground / daemon entrypoints (called by `uv run lab daemon …`) ---


def _foreground_log_init(level: int = logging.INFO) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )


def start(*, foreground: bool = True, once: bool = False, dry_run: bool = False) -> None:
    """Start the orchestrator in the current process.

    `once=True` runs at most one roadmap entry and exits — useful for
    smoke tests. `foreground=True` keeps the loop attached to the
    terminal; the typer CLI shells out via tmux for true daemon mode.
    """
    ensure_lab_runs_dir()
    _foreground_log_init()
    cfg = OrchestratorConfig(once=once, dry_run=dry_run)
    with codex_adapter.orchestrator_lock(owner="lab-runner"):
        logger.info("orchestrator started (pid=%d, once=%s, dry_run=%s)",
                    os.getpid(), once, dry_run)
        loop(cfg)


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
