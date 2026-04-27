"""Read-only data access for the lab web UI.

One ``LabReader`` per request. It opens the DuckDB read-only and
reads the markdown audit surface + per-experiment artefacts on
demand. No mutations live here; ``app.py`` enforces that contract
at the route layer too.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from openharness.lab import db as labdb
from openharness.lab import ranking
from openharness.lab.components_doc import ComponentsCatalog, read_catalog
from openharness.lab.lab_docs import TreeSnapshot, tree_snapshot
from openharness.lab.paths import (
    EXPERIMENTS_RUNS_ROOT,
    LAB_DB_PATH,
    LAB_LOGS_DIR,
    LAB_ROOT,
    LAB_RUNS_ROOT,
    ORCHESTRATOR_LOCK_PATH,
)
from openharness.lab.web.models import (
    ActivityLogEntry,
    AgentLadderRow,
    CellRow,
    ClusterDeltaRow,
    ComparisonRow,
    ComponentDetail,
    ComponentPerfRow,
    DaemonIdleReason,
    DaemonStatus,
    DoneEntryView,
    EvaluationRow,
    ExperimentSummary,
    IdeaEntryView,
    LegSummary,
    LeaderboardView,
    JournalEntryView,
    PendingActions,
    PhaseView,
    PipelineView,
    PRStateRow,
    ProcessNode,
    RoadmapEntryView,
    SpawnRow,
    SuggestedEntryView,
    TaskAggregateRow,
    TaskClusterRow,
    TaskFeatureView,
    TaskLeaderboardRow,
    TreeVizNode,
    TrialCritique,
    TrialDetail,
    TrialRow,
    TurnCard,
    UsageSummaryRow,
    VerifierReport,
    VerifierTest,
)
from openharness.lab.web import services as labsvc

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Reader
# ---------------------------------------------------------------------------


class LabReader:
    """Per-request read-only handle to the lab.

    DuckDB connections are cheap to open but cannot be shared across
    threads safely, so we open per request and close on ``__exit__``.
    """

    def __init__(self) -> None:
        self._conn: object | None = None  # duckdb.DuckDBPyConnection at runtime
        self._db_available: bool = LAB_DB_PATH.exists()

    def __enter__(self) -> "LabReader":
        if self._db_available:
            self._conn = labdb.connect(read_only=True)
        return self

    def __exit__(self, *exc: object) -> None:
        if self._conn is not None:
            self._conn.close()  # type: ignore[attr-defined]
            self._conn = None

    @property
    def db_available(self) -> bool:
        return self._db_available

    @property
    def db_path(self) -> Path:
        return LAB_DB_PATH

    # ---- low-level -------------------------------------------------------

    def _q(self, sql: str, params: list[object] | None = None) -> list[tuple[object, ...]]:
        if self._conn is None:
            return []
        return self._conn.execute(sql, params or []).fetchall()  # type: ignore[attr-defined]

    def _qd(self, sql: str, params: list[object] | None = None) -> list[dict[str, object]]:
        if self._conn is None:
            return []
        cur = self._conn.execute(sql, params or [])  # type: ignore[attr-defined]
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def _table_columns(self, table: str) -> set[str]:
        if self._conn is None:
            return set()
        try:
            return {str(r[1]) for r in self._q(f"PRAGMA table_info('{table}')")}
        except Exception as exc:  # noqa: BLE001 — DB cache may predate migrations
            log.warning("failed to inspect %s columns: %s", table, exc)
            return set()

    # ---- DB info ---------------------------------------------------------

    def db_info(self) -> dict[str, int]:
        if not self._db_available:
            return {}
        out: dict[str, int] = {}
        for tbl in (
            "experiments",
            "legs",
            "trials",
            "trial_critiques",
            "comparisons",
            "task_features",
            "components_perf",
            "misconfigurations",
            "spawns",
            "experiment_evaluations",
        ):
            try:
                (n,) = self._q(f"SELECT count(*) FROM {tbl}")[0]
                out[tbl] = int(n)  # type: ignore[arg-type]
            except Exception as exc:  # noqa: BLE001 — DB cache shape may lag migrations
                log.warning("db_info: failed to count %s: %s", tbl, exc)
                out[tbl] = -1
        return out

    # ---- daemon ----------------------------------------------------------

    def daemon_status(self) -> DaemonStatus:
        running = False
        pid: int | None = None
        started_at: datetime | None = None
        lock_corrupted = False
        last_line: str | None = None
        log_path: str | None = None

        if ORCHESTRATOR_LOCK_PATH.is_file():
            try:
                payload = json.loads(ORCHESTRATOR_LOCK_PATH.read_text())
                pid = int(payload.get("pid") or 0) or None
                started_raw = payload.get("started_at")
                if isinstance(started_raw, str):
                    started_at = _parse_ts(started_raw)
                if pid is not None:
                    try:
                        import os

                        os.kill(pid, 0)
                        running = True
                    except (ProcessLookupError, PermissionError):
                        running = False
            except (OSError, json.JSONDecodeError, ValueError):
                lock_corrupted = True

        # Best-effort: orchestrator log is what tmux/nohup launchers write to.
        orch_log = LAB_LOGS_DIR / "orchestrator.out"
        if orch_log.is_file():
            log_path = str(orch_log)
            last_line = _tail_last_nonempty(orch_log)
        else:
            # Fall back to the newest spawn log so the user sees *something*
            # if the daemon is detached / running interactively.
            newest = _newest_log_file(LAB_LOGS_DIR)
            if newest is not None:
                log_path = str(newest)
                last_line = _tail_last_nonempty(newest)

        return DaemonStatus(
            running=running,
            pid=pid,
            started_at=started_at,
            lock_corrupted=lock_corrupted,
            lock_path=str(ORCHESTRATOR_LOCK_PATH),
            last_log_line=last_line,
            log_path=log_path,
        )

    def pipeline_view(self) -> PipelineView | None:
        """Return the "what is the daemon doing right now" snapshot.

        Priority for picking the slug to surface:

        1. ``daemon_state.active_tick.slug`` — the in-flight tick. The
           returned :class:`PipelineView` has ``is_active=True`` and
           the ``current_phase`` field set from the live tick.
        2. The most recently touched ``runs/lab/state/<slug>/`` dir —
           so the operator still sees pipeline history when the
           daemon is idle. ``is_active`` is False; ``current_phase``
           is the first non-``ok``/``skipped`` phase, or ``None`` if
           the slug closed cleanly.
        3. ``None`` — daemon has never run anything on this host
           (fresh checkout). The cockpit renders an empty state.

        Always returns seven phase rows in canonical order so the strip
        in :file:`_daemon_pipeline.html` can render unconditionally.
        Skipped phases (e.g. design+implement on a baseline) carry
        ``status="skipped"`` so they render dimmed rather than
        omitted, which would make the strip's geometry shift between
        runs and confuse the eye.
        """
        from openharness.lab import daemon_state as _ds
        from openharness.lab import phase_state as _ps
        from openharness.lab import preflight as _pf

        state = _ds.load()
        active = state.active_tick

        slug: str | None = None
        is_active = False
        if active is not None:
            slug = active.slug
            is_active = True
        else:
            # Idle: find the newest per-slug state directory so the
            # operator still sees what just happened.
            for s in _ps.all_slugs():
                slug = s
                break

        if slug is None:
            return None

        slug_state = _ps.load(slug)
        # `lab phases reset --all` may have removed the file mid-tick;
        # surface a minimal pipeline so the strip still renders rather
        # than 500-ing the cockpit.
        if slug_state is None:
            slug_state = _ps.SlugPhases(slug=slug)

        current_phase: str | None = None
        if is_active and active is not None:
            current_phase = active.phase
        else:
            current_phase = slug_state.first_unfinished()

        phases: list[PhaseView] = []
        for name in _ps.PHASE_ORDER:
            rec = slug_state.phases.get(name)
            if rec is None:
                phases.append(
                    PhaseView(
                        name=name,
                        status="pending",
                        started_at=None,
                        finished_at=None,
                        duration_sec=None,
                        error=None,
                        summary=None,
                        is_active=(current_phase == name and is_active),
                    )
                )
                continue
            started = _parse_ts(rec.started_at) if rec.started_at else None
            finished = _parse_ts(rec.finished_at) if rec.finished_at else None
            duration = (finished - started).total_seconds() if started and finished else None
            # Keep summary short — the strip cell only has room for one
            # line. Per-phase keys are documented in `phase_state.py`.
            summary: str | None = None
            payload = rec.payload or {}
            if name == "preflight" and payload.get("worktree"):
                base = payload.get("base_sha") or ""
                summary = f"@ {str(base)[:8]}" if base else "worktree ready"
            elif name == "implement" and payload.get("commits"):
                n = len(payload.get("commits") or [])
                summary = f"{n} commit{'' if n == 1 else 's'}"
            elif name == "run" and payload.get("instance_id"):
                summary = f"instance {str(payload['instance_id'])[:18]}"
            elif name == "critique" and payload.get("verdict_kind"):
                summary = f"verdict: {payload['verdict_kind']}"
            elif name == "replan" and rec.status == "ok":
                summary = payload.get("summary") or "roadmap updated"
            elif name == "finalize" and payload.get("merged"):
                exp_pr_url = payload.get("experiment_pr_url") or payload.get("pr_url")
                exp_state = str(payload.get("experiment_pr_state") or "synced").lower()
                if exp_pr_url:
                    pr = str(exp_pr_url).rsplit("/", 1)[-1]
                    summary = f"experiment PR #{pr} {exp_state}"
                else:
                    summary = "synced to main"
            elif rec.status == "skipped":
                summary = str(payload.get("skip_reason") or "skipped")
            phases.append(
                PhaseView(
                    name=name,
                    status=rec.status,
                    started_at=started,
                    finished_at=finished,
                    duration_sec=duration,
                    error=rec.error,
                    summary=summary,
                    is_active=(current_phase == name and is_active),
                )
            )

        # Hypothesis from the roadmap (best-effort: the slug may have
        # already been promoted to Done by the time the operator looks).
        hypothesis: str | None = None
        try:
            up_next, _suggested, _done = self.roadmap()
            for r in up_next:
                if r.slug == slug:
                    hypothesis = r.hypothesis or None
                    break
        except Exception:  # noqa: BLE001 — markdown read is best-effort
            hypothesis = None

        # Worktree path: prefer active-tick (it's the live truth),
        # otherwise reconstruct via the preflight helper so the
        # "Remove worktree" button on the idle view still works.
        worktree_path: str | None = None
        if active is not None and active.worktree_path:
            worktree_path = active.worktree_path
        else:
            try:
                wt = _pf._worktree_path_for(slug)
                if wt.exists():
                    worktree_path = str(wt)
            except Exception:  # noqa: BLE001
                worktree_path = None
        branch = f"lab/{slug}" if worktree_path else None

        spawn_log_basename: str | None = None
        if active is not None and active.log_path:
            spawn_log_basename = active.log_path.rsplit("/", 1)[-1]

        return PipelineView(
            slug=slug,
            hypothesis=hypothesis,
            is_active=is_active,
            needs_variant=slug_state.needs_variant,
            worktree_path=worktree_path,
            branch=branch,
            spawn_pid=active.spawn_pid if active else None,
            spawn_log_path=active.log_path if active else None,
            spawn_log_basename=spawn_log_basename,
            note=active.note if active else None,
            started_at=active.started_at
            if active
            else _parse_ts(slug_state.started_at)
            if slug_state.started_at
            else None,
            last_updated_at=_parse_ts(slug_state.last_updated_at)
            if slug_state.last_updated_at
            else None,
            current_phase=current_phase,
            phases=phases,
        )

    def daemon_state(self):
        """Return the runtime :class:`daemon_state.DaemonState` snapshot.

        Returns the live in-memory dataclass (not a serialized dict) so
        templates can iterate fields by attribute. Reading is cheap
        (single JSON file load) and idempotent; safe to call once per
        page render.

        Imported lazily to avoid pulling :mod:`daemon_state` into the
        readonly path during module init (the import is also done in
        :func:`runner.loop`, so loading it here would not actually
        save anything in production — but lazy keeps the readonly
        smoke-test surface narrower).
        """
        from openharness.lab import daemon_state as _ds

        return _ds.load()

    def tail_log(self, path: Path, n: int = 200) -> list[str]:
        if not path.is_file():
            return []
        # Resolve & guard against escaping the lab tree.
        rp = path.resolve()
        if LAB_RUNS_ROOT.resolve() not in rp.parents and rp != LAB_RUNS_ROOT.resolve():
            raise PermissionError(f"refusing to tail outside {LAB_RUNS_ROOT}")
        with rp.open("r", encoding="utf-8", errors="replace") as fh:
            return fh.readlines()[-n:]

    # ---- live process tree ---------------------------------------------

    def process_tree(self, *, cmdline_max: int = 160) -> list[ProcessNode]:
        """Return the live process tree rooted at the orchestrator.

        Walks ``psutil.Process(daemon_pid).children(recursive=True)``
        and rebuilds them as a tree. Returns an empty list if the
        daemon isn't running (under either the systemd unit or the
        lock file). One root node — the daemon itself — sits
        at the top so the operator sees the full picture.

        Why include the daemon root: gives a single visual anchor
        ("everything below this is yours to kill") and matches the
        precheck: the daemon's main_pid is what determines whether
        ``kill-process`` is allowed.
        """
        try:
            import psutil
        except ImportError:
            return []

        # Prefer systemd's view when the unit is installed; fall back
        # to the lock file so this also works for direct dev runs.
        unit_pid = labsvc.status("openharness-daemon").main_pid
        lock_pid = self.daemon_status().pid
        daemon_pid = unit_pid or lock_pid
        if daemon_pid is None:
            return []

        try:
            root = psutil.Process(daemon_pid)
            descendants = root.children(recursive=True)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return []

        # Build a {pid: ProcessNode} map first, then thread the tree
        # by linking each non-root node to its parent. Two-pass keeps
        # the algorithm O(n) and avoids surprises if psutil reports
        # children out of order.
        all_procs = [root, *descendants]
        nodes: dict[int, ProcessNode] = {}
        for proc in all_procs:
            try:
                with proc.oneshot():
                    info = proc.as_dict(
                        attrs=[
                            "pid",
                            "ppid",
                            "name",
                            "username",
                            "status",
                            "create_time",
                            "cpu_percent",
                            "memory_info",
                            "cmdline",
                        ]
                    )
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                # Process died between enumeration and inspection.
                continue
            cmd = info.get("cmdline") or [info.get("name") or ""]
            full = " ".join(cmd) if cmd else (info.get("name") or "")
            short = full if len(full) <= cmdline_max else full[: cmdline_max - 1] + "…"
            mem = info.get("memory_info")
            mem_mb = (mem.rss / (1024 * 1024)) if mem is not None else 0.0
            ts = info.get("create_time")
            started = datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None
            pid = int(info["pid"])
            is_root = pid == daemon_pid
            nodes[pid] = ProcessNode(
                pid=pid,
                ppid=int(info.get("ppid") or 0),
                name=info.get("name") or "",
                username=info.get("username"),
                status=info.get("status") or "",
                started_at=started,
                cpu_percent=float(info.get("cpu_percent") or 0.0),
                mem_rss_mb=round(mem_mb, 1),
                cmdline_short=short,
                cmdline_full=full,
                is_daemon_root=is_root,
                # Mirrors precheck in commands._precheck_kill_process:
                # only descendants (not the root itself) are killable.
                can_kill=not is_root,
                children=[],
            )

        # Link children → parents. A node whose parent isn't in our
        # map (race: psutil saw a great-grandchild before its parent
        # exited) is attached to the root so it doesn't disappear.
        roots: list[ProcessNode] = []
        for pid, node in nodes.items():
            if pid == daemon_pid:
                roots.append(node)
                continue
            parent = nodes.get(node.ppid)
            if parent is not None:
                parent.children.append(node)
            else:
                # Orphan — pin to the daemon root so the operator
                # still sees it.
                if daemon_pid in nodes:
                    nodes[daemon_pid].children.append(node)

        # Stable order: oldest first, so a long-lived shell sits
        # above a short-lived child. Nones (rare) sort last.
        def _key(n: ProcessNode) -> float:
            return n.started_at.timestamp() if n.started_at else float("inf")

        for node in nodes.values():
            node.children.sort(key=_key)
        roots.sort(key=_key)
        return roots

    # ---- spawns ----------------------------------------------------------

    def recent_spawns(self, limit: int = 50) -> list[SpawnRow]:
        if not self._db_available:
            return []
        cols = self._table_columns("spawns")
        if "spawn_id" not in cols:
            return []

        def col(name: str) -> str:
            return name if name in cols else f"NULL AS {name}"

        rows = self._qd(
            f"""
            SELECT spawn_id, skill, {col("provider")}, {col("model")},
                   started_at, finished_at, exit_code,
                   {col("input_tokens")},
                   {col("cached_input_tokens")},
                   {col("output_tokens")},
                   {col("reasoning_output_tokens")},
                   {col("total_tokens")},
                   cost_usd_estimate, log_path, args, notes
            FROM spawns
            ORDER BY started_at DESC NULLS LAST
            LIMIT ?
            """,
            [limit],
        )
        return [_row_to_spawn(r) for r in rows]

    def usage_summary(self) -> list[UsageSummaryRow]:
        if not self._db_available:
            return []

        out: list[UsageSummaryRow] = []
        spawn_cols = self._table_columns("spawns")
        if "spawn_id" in spawn_cols:
            if "total_tokens" in spawn_cols:
                out.extend(
                    _row_to_usage(r)
                    for r in self._qd(
                        """
                    SELECT 'pipeline' AS source,
                           provider,
                           model,
                           COALESCE(skill, 'unknown') AS step,
                           COUNT(*) AS calls,
                           SUM(input_tokens) AS input_tokens,
                           SUM(cached_input_tokens) AS cached_input_tokens,
                           SUM(output_tokens) AS output_tokens,
                           SUM(reasoning_output_tokens) AS reasoning_output_tokens,
                           SUM(total_tokens) AS total_tokens,
                           SUM(cost_usd_estimate) AS cost_usd
                    FROM spawns
                    WHERE provider IS NOT NULL
                       OR model IS NOT NULL
                       OR total_tokens IS NOT NULL
                       OR cost_usd_estimate IS NOT NULL
                    GROUP BY provider, model, COALESCE(skill, 'unknown')
                    ORDER BY cost_usd DESC NULLS LAST,
                             total_tokens DESC NULLS LAST,
                             step
                    """
                    )
                )
            else:
                out.extend(
                    _row_to_usage(r)
                    for r in self._qd(
                        """
                    SELECT 'pipeline' AS source,
                           NULL AS provider,
                           NULL AS model,
                           COALESCE(skill, 'unknown') AS step,
                           COUNT(*) AS calls,
                           NULL AS input_tokens,
                           NULL AS cached_input_tokens,
                           NULL AS output_tokens,
                           NULL AS reasoning_output_tokens,
                           NULL AS total_tokens,
                           SUM(cost_usd_estimate) AS cost_usd
                    FROM spawns
                    WHERE cost_usd_estimate IS NOT NULL
                    GROUP BY COALESCE(skill, 'unknown')
                    ORDER BY cost_usd DESC NULLS LAST, step
                    """
                    )
                )

        trial_cols = self._table_columns("trials")
        if {"trial_id", "model"}.issubset(trial_cols):
            out.extend(
                _row_to_usage(r)
                for r in self._qd(
                    """
                SELECT 'agent trials' AS source,
                       'openharness' AS provider,
                       model,
                       'agent trial' AS step,
                       COUNT(*) AS calls,
                       SUM(input_tokens) AS input_tokens,
                       SUM(cache_tokens) AS cached_input_tokens,
                       SUM(output_tokens) AS output_tokens,
                       NULL AS reasoning_output_tokens,
                       SUM(total_tokens) AS total_tokens,
                       SUM(cost_usd) AS cost_usd
                FROM trials
                WHERE model IS NOT NULL
                   OR total_tokens IS NOT NULL
                   OR cost_usd IS NOT NULL
                GROUP BY model
                ORDER BY cost_usd DESC NULLS LAST,
                         total_tokens DESC NULLS LAST,
                         model
                """
                )
            )
        return out

    def failed_spawns_since(self, since: datetime) -> int:
        if not self._db_available:
            return 0
        rows = self._q(
            "SELECT count(*) FROM spawns WHERE started_at >= ? AND exit_code != 0",
            [since],
        )
        return int(rows[0][0]) if rows else 0  # type: ignore[arg-type]

    # ---- experiments / legs / trials -------------------------------------

    def experiments(self, limit: int | None = None) -> list[ExperimentSummary]:
        if not self._db_available:
            return []
        sql = """
            SELECT e.instance_id, e.experiment_id, e.created_at, e.git_sha,
                   COUNT(DISTINCT t.leg_id)             AS n_legs,
                   COUNT(t.trial_id)                    AS n_trials,
                   SUM(CAST(t.passed AS INT))           AS n_passed,
                   ROUND(100.0*AVG(CAST(t.passed AS DOUBLE)), 2) AS pass_rate_pct,
                   ROUND(SUM(t.cost_usd), 2)            AS cost_usd
            FROM experiments e
            LEFT JOIN trials t USING (instance_id)
            GROUP BY e.instance_id, e.experiment_id, e.created_at, e.git_sha
            ORDER BY e.created_at DESC NULLS LAST
        """
        if limit is not None:
            sql += " LIMIT ?"
            rows = self._qd(sql, [limit])
        else:
            rows = self._qd(sql)

        verdicts = {
            r["instance_id"]: _row_to_evaluation(r)
            for r in self._qd("SELECT * FROM experiment_evaluations")
        }
        return [
            ExperimentSummary(
                instance_id=str(r["instance_id"]),
                experiment_id=str(r.get("experiment_id") or ""),
                created_at=_to_dt(r.get("created_at")),
                git_sha=_opt_str(r.get("git_sha")),
                n_legs=int(r.get("n_legs") or 0),
                n_trials=int(r.get("n_trials") or 0),
                n_passed=int(r.get("n_passed") or 0),
                pass_rate_pct=_opt_float(r.get("pass_rate_pct")),
                cost_usd=_opt_float(r.get("cost_usd")),
                verdict=verdicts.get(str(r["instance_id"])),
            )
            for r in rows
        ]

    def experiment(self, instance_id: str) -> ExperimentSummary | None:
        for e in self.experiments(limit=None):
            if e.instance_id == instance_id:
                return e
        return None

    def legs(self, instance_id: str) -> list[LegSummary]:
        if not self._db_available:
            return []
        rows = self._qd(
            """
            SELECT t.instance_id, t.leg_id,
                   MAX(l.agent_id)          AS agent_id,
                   MAX(l.model)             AS model,
                   MAX(l.components_active) AS components_active,
                   COUNT(*)                 AS n_trials,
                   SUM(CAST(t.passed AS INT)) AS n_passed,
                   SUM(CASE WHEN t.status = 'errored' THEN 1 ELSE 0 END) AS n_errored,
                   ROUND(100.0*AVG(CAST(t.passed AS DOUBLE)), 2) AS pass_rate_pct,
                   ROUND(SUM(t.cost_usd), 2) AS cost_usd,
                   SUM(t.total_tokens)       AS tokens_total,
                   ROUND(MEDIAN(t.duration_sec), 1) AS median_dur_sec
            FROM trials t
            LEFT JOIN legs l USING (instance_id, leg_id)
            WHERE t.instance_id = ?
            GROUP BY t.instance_id, t.leg_id
            ORDER BY pass_rate_pct DESC, t.leg_id
            """,
            [instance_id],
        )
        return [
            LegSummary(
                instance_id=str(r["instance_id"]),
                leg_id=str(r["leg_id"]),
                agent_id=_opt_str(r.get("agent_id")),
                model=_opt_str(r.get("model")),
                components_active=_decode_str_list(r.get("components_active")),
                n_trials=int(r.get("n_trials") or 0),
                n_passed=int(r.get("n_passed") or 0),
                n_errored=int(r.get("n_errored") or 0),
                pass_rate_pct=_opt_float(r.get("pass_rate_pct")),
                cost_usd=_opt_float(r.get("cost_usd")),
                tokens_total=_opt_int(r.get("tokens_total")),
                median_dur_sec=_opt_float(r.get("median_dur_sec")),
            )
            for r in rows
        ]

    def trials(self, instance_id: str) -> list[TrialRow]:
        if not self._db_available:
            return []
        rows = self._qd(
            """
            SELECT trial_id, instance_id, leg_id, task_name, task_checksum,
                   passed, score, status, error_phase, cost_usd,
                   duration_sec, n_turns, trial_dir
            FROM trials
            WHERE instance_id = ?
            ORDER BY task_name, leg_id
            """,
            [instance_id],
        )
        return [
            TrialRow(
                trial_id=str(r["trial_id"]),
                instance_id=str(r["instance_id"]),
                leg_id=str(r["leg_id"]),
                task_name=str(r["task_name"]),
                task_checksum=_opt_str(r.get("task_checksum")),
                passed=_opt_bool(r.get("passed")),
                score=_opt_float(r.get("score")),
                status=_opt_str(r.get("status")),
                error_phase=_opt_str(r.get("error_phase")),
                cost_usd=_opt_float(r.get("cost_usd")),
                duration_sec=_opt_float(r.get("duration_sec")),
                n_turns=_opt_int(r.get("n_turns")),
                trial_dir=str(r.get("trial_dir") or ""),
            )
            for r in rows
        ]

    def task_pass_matrix(
        self, instance_id: str
    ) -> tuple[list[str], list[str], dict[tuple[str, str], TrialRow]]:
        """Return (tasks, legs, cell_lookup) for the per-task heatmap."""
        trials = self.trials(instance_id)
        tasks = sorted({t.task_name for t in trials})
        legs = sorted({t.leg_id for t in trials})
        cells = {(t.task_name, t.leg_id): t for t in trials}
        return tasks, legs, cells

    # ---- config state / components / dynamic rankings --------------------

    def tree(self) -> TreeSnapshot:
        return tree_snapshot()

    def components(self) -> ComponentsCatalog:
        return read_catalog()

    def leaderboard(self) -> LeaderboardView:
        return LeaderboardView(
            policy_label="full-suite only; pass rate, then cost/task, tokens/task, and median duration",
            rows=self.model_leaderboard(),
        )

    def model_leaderboard(self) -> list[AgentLadderRow]:
        """Return the best full-suite configuration per model id."""
        if not self._db_available:
            return []
        return [
            AgentLadderRow(
                rank=row.rank,
                model_id=row.model_id,
                dataset=row.dataset,
                evidence_scope=row.evidence_scope,
                agent_id=row.agent_id,
                status=(
                    "ineligible"
                    if not row.eligible
                    else "best full-suite"
                    if row.rank == 1
                    else "ranked"
                ),
                evaluated_at=row.created_at,
                accepting_instance_id=row.instance_id,
                experiment_id=row.experiment_id,
                pass_rate_pct=row.pass_rate_pct,
                cost_per_task_usd=row.cost_per_task_usd,
                cost_per_pass_usd=row.cost_per_pass_usd,
                tokens_per_task=row.tokens_per_task,
                median_duration_sec=row.median_duration_sec,
                n_trials=row.n_trials,
                n_passed=row.n_passed,
                eligible=row.eligible,
                eligibility_reason=row.eligibility_reason,
                reason=row.rationale,
            )
            for row in ranking.best_by_model(
                self._conn,
                evidence_scope="full_suite",
            )
        ]

    # ---- slug → instance_id resolution + evaluation preview -------------

    def resolve_slug(self, slug: str) -> str | None:
        """Map an experiment slug to an ``experiments.instance_id``.

        Mirrors the resolution order in
        :func:`openharness.lab.cli._lookup_instance_for_slug` so the
        web UI's "Preview evaluation" button finds the same instance the
        ``uv run lab evaluation apply <slug>`` CLI would. Read-only —
        the whole call uses the LabReader's read-only DuckDB connection.
        """

        if not self._db_available:
            return None

        rows = self._q(
            "SELECT instance_id FROM experiments WHERE instance_id = ?",
            [slug],
        )
        if rows:
            return str(rows[0][0])

        rows = self._q(
            "SELECT instance_id FROM experiment_evaluations WHERE slug = ?",
            [slug],
        )
        if rows and rows[0][0]:
            return str(rows[0][0])

        rows = self._q(
            "SELECT instance_id FROM experiments "
            "WHERE instance_id LIKE ? || '-%' "
            "ORDER BY created_at DESC LIMIT 1",
            [slug],
        )
        if rows:
            return str(rows[0][0])

        rows = self._q(
            "SELECT instance_id FROM experiments "
            "WHERE experiment_id = ? "
            "ORDER BY created_at DESC LIMIT 1",
            [slug],
        )
        if rows:
            return str(rows[0][0])

        # Fall-through: substring match on experiment_id.
        rows = self._q(
            "SELECT instance_id, experiment_id FROM experiments ORDER BY created_at DESC"
        )
        for inst_id, eid in rows:
            if eid and slug.startswith(f"{eid}-"):  # type: ignore[operator]
                return str(inst_id)
        return None

    def experiments_without_evaluation(self, limit: int = 20) -> list[ExperimentSummary]:
        """Recent experiments that have no row in ``experiment_evaluations``.

        These are the typical "rescue" candidates for the evaluation
        apply button: the daemon either failed at the verdict step or
        the run was started by hand and never had a verdict computed.
        Sorted newest-first so the most relevant ones bubble up.
        """

        # Reuses ``experiments()`` (which already joins in ``verdict``)
        # rather than duplicating the SQL aggregate. Cheap because the
        # experiments table is small (one row per experiment, and the
        # lab typically holds a few dozen).
        return [e for e in self.experiments(limit=None) if e.verdict is None][:limit]

    def preview_evaluation(self, slug: str) -> dict[str, object] | None:
        """Load the experiment evaluation for ``slug`` without applying it.

        Returns ``None`` when the slug doesn't resolve to any known
        instance, so the caller can render a clear "unknown experiment"
        message rather than a confusing empty evaluation. The evaluation
        itself is returned as a dict so templates don't need to import
        the evaluation dataclass.
        """

        from openharness.lab import evaluation as _evaluation

        instance_id = self.resolve_slug(slug)
        if instance_id is None:
            return None
        try:
            evaluation = _evaluation.load_evaluation(instance_id, db_conn=self._conn)
        except (FileNotFoundError, ValueError):
            return None
        out = evaluation.to_dict()
        # Echo the slug + resolved instance_id so the template doesn't
        # need to thread them in separately.
        out["slug"] = slug
        out["resolved_instance_id"] = instance_id
        return out

    def evaluations(
        self, *, applied: bool | None = None, verdict: str | None = None, limit: int = 100
    ) -> list[EvaluationRow]:
        if not self._db_available:
            return []
        sql = "SELECT * FROM experiment_evaluations WHERE 1=1"
        params: list[object] = []
        if applied is not None:
            sql += " AND applied = ?"
            params.append(applied)
        if verdict is not None:
            sql += " AND verdict = ?"
            params.append(verdict)
        sql += " ORDER BY applied_at DESC NULLS LAST LIMIT ?"
        params.append(limit)
        return [_row_to_evaluation(r) for r in self._qd(sql, params)]

    # ---- markdown surfaces ---------------------------------------------

    def roadmap(
        self,
    ) -> tuple[list[RoadmapEntryView], list[SuggestedEntryView], list[DoneEntryView]]:
        path = LAB_ROOT / "roadmap.md"
        if not path.is_file():
            return [], [], []
        text = path.read_text()
        up_next, suggested, done = _parse_roadmap(text)
        # deps_satisfied via runner helper would re-read the file; we already
        # have it parsed, so do it inline.
        done_slugs = {d.slug for d in done}
        out: list[RoadmapEntryView] = []
        for r in up_next:
            sat = all(d in done_slugs for d in r.depends_on) if r.depends_on else True
            out.append(replace(r, deps_satisfied=sat))
        return out, suggested, done

    def ideas(self) -> list[IdeaEntryView]:
        path = LAB_ROOT / "ideas.md"
        if not path.is_file():
            return []
        return _parse_ideas(path.read_text())

    def journal(self) -> list[JournalEntryView]:
        path = LAB_ROOT / "experiments.md"
        if not path.is_file():
            return []
        return _parse_journal(path.read_text())

    def journal_entry(self, slug: str) -> JournalEntryView | None:
        for e in self.journal():
            if e.slug == slug:
                return e
        return None

    def journal_entry_for_instance(self, instance_id: str) -> JournalEntryView | None:
        # Journal entries link to ``runs/experiments/<instance_id>``.
        for e in self.journal():
            if e.run_link and instance_id in e.run_link:
                return e
        return None

    # ---- pending-actions inbox ------------------------------------------

    def pending_actions(self, *, recent_window: timedelta = timedelta(hours=24)) -> PendingActions:
        _, suggested, _ = self.roadmap()
        auto = [i for i in self.ideas() if i.section == "Auto-proposed"]
        misconf = 0
        if self._db_available:
            rows = self._q(
                "SELECT count(*) FROM misconfigurations WHERE created_at >= ?",
                [datetime.now(timezone.utc) - recent_window],
            )
            misconf = int(rows[0][0]) if rows else 0  # type: ignore[arg-type]
        failed = self.failed_spawns_since(datetime.now(timezone.utc) - recent_window)
        return PendingActions(
            suggested=suggested,
            auto_proposed=auto,
            misconfig_recent=misconf,
            failed_spawns_recent=failed,
        )

    # ---- cluster / cross-experiment views -------------------------------

    def task_clusters_for_instance(self, instance_id: str) -> list[TaskClusterRow]:
        if not self._db_available:
            return []
        rows = self._qd(
            """
            SELECT COALESCE(tf.category, 'unclassified') AS cluster,
                   COUNT(*)                 AS n_trials,
                   SUM(CAST(t.passed AS INT)) AS n_passed,
                   ROUND(100.0*AVG(CAST(t.passed AS DOUBLE)), 1) AS pass_rate_pct
            FROM trials t
            LEFT JOIN task_features tf USING (task_checksum)
            WHERE t.instance_id = ?
            GROUP BY 1
            ORDER BY n_trials DESC
            """,
            [instance_id],
        )
        return [
            TaskClusterRow(
                cluster=str(r["cluster"]),
                n_trials=int(r["n_trials"]),
                n_passed=int(r.get("n_passed") or 0),
                pass_rate_pct=float(r.get("pass_rate_pct") or 0.0),
            )
            for r in rows
        ]

    def components_perf(self) -> list[ComponentPerfRow]:
        if not self._db_available:
            return []
        rows = self._qd(
            """
            SELECT component_id, task_cluster, n_trials, win_rate,
                   cost_delta_pct, supporting_experiments, notes
            FROM components_perf
            ORDER BY n_trials DESC
            """
        )
        return [
            ComponentPerfRow(
                component_id=str(r["component_id"]),
                task_cluster=str(r["task_cluster"]),
                n_trials=int(r["n_trials"]),
                win_rate=_opt_float(r.get("win_rate")),
                cost_delta_pct=_opt_float(r.get("cost_delta_pct")),
                notes=_opt_str(r.get("notes")),
                supporting_experiments=_decode_str_list(r.get("supporting_experiments")),
            )
            for r in rows
        ]

    # ---- trial drill-down -----------------------------------------------

    def trial(self, instance_id: str, trial_id: str) -> TrialDetail | None:
        if not self._db_available:
            return None
        rows = self._qd(
            """
            SELECT t.*,
                   l.agent_id    AS leg_agent_id,
                   l.model       AS leg_model,
                   l.components_active AS leg_components
            FROM trials t
            LEFT JOIN legs l USING (instance_id, leg_id)
            WHERE t.instance_id = ? AND t.trial_id = ?
            LIMIT 1
            """,
            [instance_id, trial_id],
        )
        if not rows:
            return None
        r = rows[0]
        trial_dir = Path(str(r.get("trial_dir") or ""))
        critique = self.trial_critique(trial_id)
        verifier = _load_verifier_report(trial_dir) if trial_dir else None
        turns = _load_turns_from_trial(trial_dir) if trial_dir else []
        user_prompt = _first_user_text(turns)
        raw_files = _list_raw_files(trial_dir) if trial_dir else []
        return TrialDetail(
            trial_id=str(r["trial_id"]),
            instance_id=str(r["instance_id"]),
            leg_id=str(r["leg_id"]),
            task_name=str(r["task_name"]),
            task_checksum=_opt_str(r.get("task_checksum")),
            passed=_opt_bool(r.get("passed")),
            score=_opt_float(r.get("score")),
            status=_opt_str(r.get("status")),
            error_phase=_opt_str(r.get("error_phase")),
            cost_usd=_opt_float(r.get("cost_usd")),
            input_tokens=_opt_int(r.get("input_tokens")),
            cache_tokens=_opt_int(r.get("cache_tokens")),
            output_tokens=_opt_int(r.get("output_tokens")),
            total_tokens=_opt_int(r.get("total_tokens")),
            duration_sec=_opt_float(r.get("duration_sec")),
            n_turns=_opt_int(r.get("n_turns")),
            trial_dir=str(trial_dir),
            agent_id=_opt_str(r.get("leg_agent_id")),
            model=_opt_str(r.get("leg_model")),
            components_active=_decode_str_list(r.get("leg_components")),
            user_prompt=user_prompt,
            critique=critique,
            verifier=verifier,
            turns=turns,
            raw_files=raw_files,
        )

    def trial_critique(self, trial_id: str) -> TrialCritique | None:
        if not self._db_available:
            return None
        rows = self._qd(
            "SELECT * FROM trial_critiques WHERE trial_id = ? LIMIT 1",
            [trial_id],
        )
        if not rows:
            return None
        r = rows[0]
        return TrialCritique(
            trial_id=str(r["trial_id"]),
            task_summary=_opt_str(r.get("task_summary")),
            agent_strategy=_opt_str(r.get("agent_strategy")),
            key_actions=_decode_str_list(r.get("key_actions")),
            outcome=_opt_str(r.get("outcome")),
            root_cause=_opt_str(r.get("root_cause")),
            success_factor=_opt_str(r.get("success_factor")),
            anti_patterns=_decode_str_list(r.get("anti_patterns")),
            components_active=_decode_str_list(r.get("components_active")),
            task_features=_decode_json(r.get("task_features")),
            surprising_observations=_decode_str_list(r.get("surprising_observations")),
            confidence=_opt_float(r.get("confidence")),
            critic_model=_opt_str(r.get("critic_model")),
            created_at=_to_dt(r.get("created_at")),
        )

    # ---- task drill-down ------------------------------------------------

    def task_features(self, task_checksum: str) -> TaskFeatureView | None:
        if not self._db_available:
            return None
        rows = self._qd(
            "SELECT * FROM task_features WHERE task_checksum = ? LIMIT 1",
            [task_checksum],
        )
        if not rows:
            return None
        r = rows[0]
        return TaskFeatureView(
            task_checksum=str(r["task_checksum"]),
            task_name=str(r.get("task_name") or ""),
            category=_opt_str(r.get("category")),
            required_tools=_decode_str_list(r.get("required_tools")),
            env_complexity=_opt_str(r.get("env_complexity")),
            output_shape=_opt_str(r.get("output_shape")),
            keywords=_decode_str_list(r.get("keywords")),
        )

    def task_leaderboard(self, task_checksum: str) -> list[TaskLeaderboardRow]:
        """Every trial that ever ran this task, ranked best→worst."""
        if not self._db_available:
            return []
        rows = self._qd(
            """
            SELECT t.instance_id, t.leg_id, t.trial_id,
                   l.agent_id, l.model, l.components_active,
                   t.passed, t.score, t.cost_usd, t.duration_sec, t.n_turns,
                   e.created_at
            FROM trials t
            LEFT JOIN legs l USING (instance_id, leg_id)
            LEFT JOIN experiments e USING (instance_id)
            WHERE t.task_checksum = ?
            ORDER BY t.passed DESC NULLS LAST,
                     t.score DESC NULLS LAST,
                     t.cost_usd ASC NULLS LAST
            """,
            [task_checksum],
        )
        return [
            TaskLeaderboardRow(
                instance_id=str(r["instance_id"]),
                leg_id=str(r["leg_id"]),
                trial_id=str(r["trial_id"]),
                agent_id=_opt_str(r.get("agent_id")),
                model=_opt_str(r.get("model")),
                passed=_opt_bool(r.get("passed")),
                score=_opt_float(r.get("score")),
                cost_usd=_opt_float(r.get("cost_usd")),
                duration_sec=_opt_float(r.get("duration_sec")),
                n_turns=_opt_int(r.get("n_turns")),
                components_active=_decode_str_list(r.get("components_active")),
                created_at=_to_dt(r.get("created_at")),
            )
            for r in rows
        ]

    def tasks_index(self) -> list[TaskAggregateRow]:
        if not self._db_available:
            return []
        rows = self._qd(
            """
            SELECT
                t.task_checksum,
                MAX(t.task_name)               AS task_name,
                MAX(tf.category)               AS category,
                COUNT(*)                       AS n_trials,
                SUM(CAST(t.passed AS INT))     AS n_passed,
                ROUND(100.0*AVG(CAST(t.passed AS DOUBLE)), 1) AS pass_rate_pct,
                COUNT(DISTINCT t.leg_id)       AS n_legs,
                COUNT(DISTINCT t.instance_id)  AS n_experiments,
                MAX(e.created_at)              AS last_seen
            FROM trials t
            LEFT JOIN task_features tf USING (task_checksum)
            LEFT JOIN experiments e USING (instance_id)
            WHERE t.task_checksum IS NOT NULL
            GROUP BY t.task_checksum
            ORDER BY n_trials DESC, task_name
            """
        )
        return [
            TaskAggregateRow(
                task_checksum=str(r["task_checksum"]),
                task_name=str(r.get("task_name") or "?"),
                category=_opt_str(r.get("category")),
                n_trials=int(r["n_trials"]),
                n_passed=int(r.get("n_passed") or 0),
                pass_rate_pct=_opt_float(r.get("pass_rate_pct")),
                n_legs=int(r.get("n_legs") or 0),
                n_experiments=int(r.get("n_experiments") or 0),
                last_seen=_to_dt(r.get("last_seen")),
            )
            for r in rows
        ]

    def comparisons_for_instance(self, instance_id: str) -> list[ComparisonRow]:
        if not self._db_available:
            return []
        rows = self._qd(
            """
            SELECT instance_id, task_name, winning_leg, runner_up_leg,
                   delta_score, why, legs_compared, critic_model, created_at
            FROM comparisons
            WHERE instance_id = ?
            ORDER BY ABS(COALESCE(delta_score, 0)) DESC, task_name
            """,
            [instance_id],
        )
        return [_row_to_comparison(r) for r in rows]

    def comparisons_for_task(self, task_name: str) -> list[ComparisonRow]:
        if not self._db_available:
            return []
        rows = self._qd(
            """
            SELECT instance_id, task_name, winning_leg, runner_up_leg,
                   delta_score, why, legs_compared, critic_model, created_at
            FROM comparisons
            WHERE task_name = ?
            ORDER BY created_at DESC
            """,
            [task_name],
        )
        return [_row_to_comparison(r) for r in rows]

    # ---- component drill-down ------------------------------------------

    def component_detail(self, component_id: str) -> ComponentDetail | None:
        catalog = read_catalog()
        entry = catalog.find(component_id)
        if entry is None and not self._db_available:
            return None

        # Per-cluster perf rows.
        perfs: list[ComponentPerfRow] = []
        active_in: list[str] = []
        if self._db_available:
            perfs = [p for p in self.components_perf() if p.component_id == component_id]
            # ``legs.components_active`` is stored as a JSON column.
            # Cast to ``VARCHAR[]`` before list_contains; quote the
            # needle so it matches the JSON-encoded element.
            rows = self._q(
                """
                SELECT DISTINCT instance_id
                FROM legs
                WHERE list_contains(
                    CAST(components_active AS VARCHAR[]),
                    ?
                )
                ORDER BY instance_id
                """,
                [component_id],
            )
            active_in = [str(r[0]) for r in rows]

        return ComponentDetail(
            component_id=component_id,
            kind=entry.kind if entry else None,
            status=entry.status if entry else None,
            description=entry.description if entry else None,
            used_by=entry.used_by if entry else [],
            evidence=entry.evidence if entry else [],
            perf_rows=perfs,
            experiments_active_in=active_in,
            experiments_count=len(active_in),
        )

    # ---- PR cache + PR-aware view-models -------------------------------

    def pr_states(
        self,
        *,
        kinds: tuple[str, ...] = ("accept", "reject", "no_op"),
    ) -> list[PRStateRow]:
        """Snapshot of every canonical experiment PR known to the lab.

        Joins ``experiment_evaluations.pr_url`` (cheap, always present once
        :func:`cli.set_branch` has run) with cached ``gh pr view``
        output. Rejected and no-op outcomes point at the closed
        implementation PR, not any metadata-only bookkeeping PR. The
        cache TTL matches the runner's
        ``_PR_MERGE_CACHE_TTL_SEC`` (90 s) so the daemon and the web
        UI see consistent state without each having to spam the
        GitHub API.

        When ``gh`` is missing or the call fails, the row still
        appears with ``state=None`` + ``error="..."`` populated, so
        the operator sees "PR open · status unknown" rather than
        the row silently disappearing.
        """
        if not self._db_available:
            return []
        if not kinds:
            return []
        placeholders = ",".join("?" for _ in kinds)
        rows = self._qd(
            f"""
            SELECT slug, instance_id, verdict, pr_url
            FROM experiment_evaluations
            WHERE pr_url IS NOT NULL
              AND verdict IN ({placeholders})
            ORDER BY applied_at DESC NULLS LAST
            """,
            list(kinds),
        )
        out: list[PRStateRow] = []
        for r in rows:
            url = str(r["pr_url"])
            cached = _pr_cache_lookup(url)
            if cached is None:
                cached = _pr_cache_refresh(url)
            number = _pr_number_from_url(url)
            out.append(
                PRStateRow(
                    slug=str(r["slug"]),
                    instance_id=str(r["instance_id"]),
                    verdict=str(r["verdict"]),
                    pr_url=url,
                    pr_number=number,
                    state=cached.state,
                    is_merged=cached.is_merged,
                    mergeable=cached.mergeable,
                    checks_status=cached.checks_status,
                    auto_merge_enabled=cached.auto_merge_enabled,
                    title=cached.title,
                    head_sha=cached.head_sha,
                    checked_at=cached.checked_at,
                    error=cached.error,
                )
            )
        return out

    # ---- daemon idle reason ---------------------------------------------

    def idle_reason(
        self,
        *,
        ready_slugs: list[str] | None = None,
    ) -> DaemonIdleReason:
        """Why is the daemon doing what it's doing right now?

        Combines daemon status + state to produce one actionable
        badge. Computation order matches the runner's ``loop()``
        priority: status < paused < no_queue < manual_no_appr <
        running.
        """
        from openharness.lab import daemon_state as _ds

        status = self.daemon_status()
        if not status.running and not status.lock_corrupted:
            return DaemonIdleReason(
                code="stopped",
                detail="Orchestrator daemon is not running.",
            )

        state = _ds.load()
        if state.active_tick is not None:
            slug = state.active_tick.slug
            return DaemonIdleReason(
                code="running",
                detail=f"Tick in progress on {slug}.",
                slug=slug,
            )

        if state.mode == "paused":
            return DaemonIdleReason(
                code="paused",
                detail="Mode is paused. The daemon will not pick up entries.",
            )

        if ready_slugs is None:
            try:
                up_next, _suggested, done = self.roadmap()
                done_slugs = {d.slug for d in done}
                ready_slugs = [
                    r.slug for r in up_next if all(d in done_slugs for d in r.depends_on)
                ]
            except Exception:  # noqa: BLE001
                ready_slugs = []

        blocked = {
            slug
            for slug, rec in state.entry_failures.items()
            if rec.count >= state.max_failures_before_demote
        }
        blocked_ready = [slug for slug in ready_slugs if slug in blocked]
        ready_slugs = [slug for slug in ready_slugs if slug not in blocked]

        if not ready_slugs:
            if blocked_ready:
                return DaemonIdleReason(
                    code="blocked",
                    detail=(
                        f"{len(blocked_ready)} ready roadmap entr"
                        f"{'y is' if len(blocked_ready) == 1 else 'ies are'} "
                        "blocked by failure counters. Reset failures to retry, "
                        "or edit the roadmap through the normal PR flow."
                    ),
                    slug=blocked_ready[0],
                )
            return DaemonIdleReason(
                code="no_queue",
                detail="Roadmap has no entries with satisfied dependencies.",
            )

        if state.mode == "manual":
            approved = set(state.approved_slugs)
            for slug in ready_slugs:
                if slug in approved:
                    return DaemonIdleReason(
                        code="running",
                        detail=f"{slug} approved; next tick will pick it up.",
                        slug=slug,
                    )
            return DaemonIdleReason(
                code="manual_no_appr",
                detail=(
                    "Manual mode: no approved entries. Approve a roadmap "
                    "slug to let the daemon proceed."
                ),
            )

        return DaemonIdleReason(
            code="running",
            detail="Autonomous mode; next tick will pick up the queue.",
            slug=ready_slugs[0],
        )

    # ---- /activity timeline ---------------------------------------------

    def activity_log(
        self,
        *,
        limit: int = 200,
        kinds: tuple[str, ...] | None = None,
    ) -> list[ActivityLogEntry]:
        """Unified activity timeline across all the lab's audit surfaces.

        Folds together ``runs/lab/web_commands.jsonl`` (web commands),
        ``daemon_state.history`` (tick outcomes), DuckDB ``spawns``
        (codex skill spawns) and experiment evaluations.

        Sorted newest-first. ``kinds`` filters by row type.
        """
        from openharness.lab import daemon_state as _ds
        from openharness.lab.web import commands as _labcmd

        wanted = (
            set(kinds)
            if kinds
            else {
                "cmd",
                "tick",
                "spawn",
                "verdict",
            }
        )
        out: list[ActivityLogEntry] = []

        if "cmd" in wanted:
            for r in _labcmd.audit_tail(n=max(limit * 2, 200)):
                ts = _parse_ts(str(r.get("started_at") or "")) if r.get("started_at") else None
                if ts is None:
                    continue
                exit_code = r.get("exit_code")
                cmd_id = str(r.get("cmd_id") or "?")
                out.append(
                    ActivityLogEntry(
                        at_ts=ts,
                        kind="cmd",
                        actor=str(r.get("actor") or "?"),
                        title=cmd_id,
                        detail=(f"exit={exit_code}" if exit_code is not None else None),
                        success=(exit_code == 0) if exit_code is not None else None,
                    )
                )

        if "tick" in wanted:
            try:
                state = _ds.load()
                for h in reversed(state.history):
                    out.append(
                        ActivityLogEntry(
                            at_ts=h.ended_at,
                            kind="tick",
                            actor="daemon",
                            title=f"{h.slug} → {h.outcome}",
                            detail=(f"phase={h.phase_reached} dur={h.duration_sec:.1f}s"),
                            slug=h.slug,
                            success=(h.outcome == "ok"),
                        )
                    )
            except Exception:  # noqa: BLE001
                pass

        if "spawn" in wanted and self._db_available:
            for s in self.recent_spawns(limit=limit):
                if s.finished_at is None:
                    continue
                out.append(
                    ActivityLogEntry(
                        at_ts=s.finished_at,
                        kind="spawn",
                        actor="daemon",
                        title=s.skill,
                        detail=(
                            f"exit={s.exit_code}"
                            + (f" cost={s.cost_usd_estimate:.2f}" if s.cost_usd_estimate else "")
                        ),
                        success=(s.exit_code == 0) if s.exit_code is not None else None,
                    )
                )

        if "verdict" in wanted and self._db_available:
            for d in self.evaluations(limit=limit):
                out.append(
                    ActivityLogEntry(
                        at_ts=d.applied_at or datetime.now(timezone.utc),
                        kind="verdict",
                        actor=d.applied_by or "?",
                        title=f"{d.slug}: {d.verdict}",
                        detail=(
                            ("pending merge" if not d.applied else "merged to main")
                            + (f" → {d.target_id}" if d.target_id else "")
                        ),
                        slug=d.slug,
                        instance_id=d.instance_id,
                        href=f"/runs/{d.instance_id}",
                    )
                )

        out.sort(key=lambda e: e.at_ts, reverse=True)
        return out[:limit]

    # ---- experiment cell matrix + deltas -------------------------------

    def cells_for_instance(self, instance_id: str) -> list[CellRow]:
        """Per-task × per-leg cells for one experiment.

        Joins trials with task_features so the operator can group
        cells by cluster (task category) without a second query.
        """
        if not self._db_available:
            return []
        rows = self._qd(
            """
            SELECT t.task_name, t.task_checksum, t.leg_id, t.trial_id,
                   t.passed, t.score, t.status, t.cost_usd, t.duration_sec,
                   t.n_turns, t.trial_dir,
                   COALESCE(tf.category, 'unclassified') AS cluster
            FROM trials t
            LEFT JOIN task_features tf USING (task_checksum)
            WHERE t.instance_id = ?
            ORDER BY cluster, task_name, leg_id
            """,
            [instance_id],
        )
        out: list[CellRow] = []
        for r in rows:
            passed = _opt_bool(r.get("passed"))
            status = _opt_str(r.get("status"))
            glyph, border = _cell_glyph(passed, status)
            out.append(
                CellRow(
                    task_name=str(r["task_name"]),
                    task_checksum=_opt_str(r.get("task_checksum")),
                    leg_id=str(r["leg_id"]),
                    cluster=str(r["cluster"]),
                    trial_id=str(r["trial_id"]),
                    passed=passed,
                    score=_opt_float(r.get("score")),
                    status=status,
                    cost_usd=_opt_float(r.get("cost_usd")),
                    duration_sec=_opt_float(r.get("duration_sec")),
                    n_turns=_opt_int(r.get("n_turns")),
                    trial_dir=str(r.get("trial_dir") or ""),
                    status_glyph=glyph,
                    border_color=border,
                )
            )
        return out

    def cluster_deltas(
        self,
        instance_id: str,
        *,
        min_tasks_warning: int = 5,
    ) -> list[ClusterDeltaRow]:
        """Per-cluster paired Δ between every leg pair.

        For each task-cluster, computes ``leg_a_pass_rate -
        leg_b_pass_rate`` for every ordered pair (legA, legB).
        ``warning`` fires when the per-cluster sample is small enough
        that the row should be treated as suggestive rather than
        evaluation-grade evidence.
        """
        cells = self.cells_for_instance(instance_id)
        if not cells:
            return []

        legs = sorted({c.leg_id for c in cells})
        clusters = sorted({c.cluster for c in cells})
        out: list[ClusterDeltaRow] = []
        for cluster in clusters:
            in_cluster = [c for c in cells if c.cluster == cluster]
            tasks = sorted({c.task_name for c in in_cluster})
            n_tasks = len(tasks)
            for i, a in enumerate(legs):
                for b in legs[i + 1 :]:
                    pa, pb = _paired_pass_rate(in_cluster, a, b, tasks)
                    delta_pp: float | None = None
                    if pa is not None and pb is not None:
                        delta_pp = (pa - pb) * 100.0
                    out.append(
                        ClusterDeltaRow(
                            cluster=cluster,
                            n_tasks=n_tasks,
                            leg_a=a,
                            leg_b=b,
                            delta_pp=delta_pp,
                            warning=n_tasks < min_tasks_warning,
                        )
                    )
        return out

    # ---- tree visualisation --------------------------------------------

    def tree_viz_nodes(self) -> list[TreeVizNode]:
        """Flat list of every node in the configuration tree.

        Each node carries its associated PRStateRow (if any) so the
        template can paint badges without a second pass.
        """
        snapshot = self.tree()
        prs_by_branch = {pr.slug: pr for pr in self.pr_states()}
        nodes: list[TreeVizNode] = []
        nodes.append(
            TreeVizNode(
                node_id=snapshot.operational_baseline_id,
                role="operational_baseline",
                mutation="(operational baseline)",
                sketch=snapshot.operational_baseline_anchor,
            )
        )
        for r in snapshot.rejected:
            nodes.append(
                TreeVizNode(
                    node_id=r.branch_id,
                    role="rejected",
                    reason=r.reason,
                    sketch=r.evidence,
                    pr=prs_by_branch.get(r.branch_id),
                )
            )
        for p in snapshot.proposed:
            nodes.append(
                TreeVizNode(
                    node_id=p.branch_id,
                    role="proposed",
                    sketch=p.sketch,
                    linked_idea=p.linked_idea,
                )
            )
        return nodes


# ---------------------------------------------------------------------------
# PR cache (process-local, TTL-keyed)
# ---------------------------------------------------------------------------


@dataclass(eq=False, slots=True)
class _PRCacheEntry:
    state: str | None = None
    is_merged: bool = False
    mergeable: str | None = None
    checks_status: str | None = None
    auto_merge_enabled: bool = False
    title: str | None = None
    head_sha: str | None = None
    checked_at: datetime | None = None
    error: str | None = None


_PR_CACHE: dict[str, tuple[float, _PRCacheEntry]] = {}
_PR_CACHE_TTL_SEC = 90.0


def _pr_cache_lookup(url: str) -> _PRCacheEntry | None:
    """Return a cached entry if still fresh; else None."""
    import time as _time

    rec = _PR_CACHE.get(url)
    if rec is None:
        return None
    cached_at, entry = rec
    if (_time.monotonic() - cached_at) > _PR_CACHE_TTL_SEC:
        return None
    return entry


def _pr_cache_refresh(url: str) -> _PRCacheEntry:
    """Run ``gh pr view`` for ``url`` and cache the result.

    Errors (no ``gh`` on PATH, network failure, non-zero exit, JSON
    decode error) are recorded as a cache entry with ``error`` set
    so we don't re-spawn ``gh`` on every page render. The cache TTL
    still applies — a transient failure clears after 90 s.
    """
    import shutil
    import subprocess
    import time as _time

    entry = _PRCacheEntry(checked_at=datetime.now(timezone.utc))
    if not shutil.which("gh"):
        entry.error = "gh not on PATH"
        _PR_CACHE[url] = (_time.monotonic(), entry)
        return entry
    try:
        proc = subprocess.run(
            [
                "gh",
                "pr",
                "view",
                url,
                "--json",
                "state,mergedAt,mergeable,title,headRefOid,autoMergeRequest,statusCheckRollup",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except Exception as exc:  # noqa: BLE001
        entry.error = f"gh raised: {exc!r}"
        _PR_CACHE[url] = (_time.monotonic(), entry)
        return entry
    if proc.returncode != 0:
        entry.error = f"gh exit={proc.returncode}: {(proc.stderr or '').strip()[:160]}"
        _PR_CACHE[url] = (_time.monotonic(), entry)
        return entry
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        entry.error = "gh returned invalid JSON"
        _PR_CACHE[url] = (_time.monotonic(), entry)
        return entry

    entry.state = (str(data.get("state") or "")).upper() or None
    entry.is_merged = entry.state == "MERGED" and bool(data.get("mergedAt"))
    entry.mergeable = _opt_str(data.get("mergeable"))
    entry.title = _opt_str(data.get("title"))
    entry.head_sha = _opt_str(data.get("headRefOid"))
    entry.auto_merge_enabled = bool(data.get("autoMergeRequest"))
    rollup = data.get("statusCheckRollup")
    if isinstance(rollup, list) and rollup:
        states = [
            str(r.get("conclusion") or r.get("status") or "") for r in rollup if isinstance(r, dict)
        ]
        if any(s == "FAILURE" for s in states):
            entry.checks_status = "FAILURE"
        elif any(s in ("PENDING", "IN_PROGRESS", "QUEUED") for s in states):
            entry.checks_status = "PENDING"
        elif all(s in ("SUCCESS", "NEUTRAL", "SKIPPED") for s in states):
            entry.checks_status = "SUCCESS"

    _PR_CACHE[url] = (_time.monotonic(), entry)
    return entry


_PR_NUMBER_RE = re.compile(r"/pull/(\d+)(?:/|$)")


def _pr_number_from_url(url: str) -> int | None:
    m = _PR_NUMBER_RE.search(url)
    return int(m.group(1)) if m else None


def _cell_glyph(passed: bool | None, status: str | None) -> tuple[str, str]:
    """Map a trial outcome to (icon, Tailwind border-color class)."""
    if passed is True:
        return ("✓", "border-emerald-400")
    if passed is False:
        return ("✕", "border-rose-400")
    if status == "errored":
        return ("!", "border-rose-300")
    if status in ("pending", "running"):
        return ("…", "border-amber-300")
    return ("?", "border-slate-200")


def _paired_pass_rate(
    cells: list[CellRow],
    leg_a: str,
    leg_b: str,
    tasks: list[str],
) -> tuple[float | None, float | None]:
    """Return (pass-rate-A, pass-rate-B) over tasks where BOTH ran."""
    by_key = {(c.leg_id, c.task_name): c for c in cells}
    paired = [(by_key.get((leg_a, t)), by_key.get((leg_b, t))) for t in tasks]
    paired_present = [(a, b) for a, b in paired if a is not None and b is not None]
    if not paired_present:
        return None, None
    pa = sum(1.0 for a, _ in paired_present if a.passed) / len(paired_present)
    pb = sum(1.0 for _, b in paired_present if b.passed) / len(paired_present)
    return pa, pb


# ---------------------------------------------------------------------------
# Markdown parsing helpers (lab-flavoured; intentionally narrow)
# ---------------------------------------------------------------------------


_ROADMAP_ENTRY_RE = re.compile(r"^### (\S+)\s*\n", re.MULTILINE)
_SUGGESTED_ENTRY_RE = re.compile(r"^#### (\S+)\s*\n", re.MULTILINE)
_BULLET_RE = re.compile(r"^-\s*\*\*([^:]+):\*\*\s*(.*)$", re.MULTILINE)


def _split_top_section(text: str, name: str) -> str | None:
    """Return the body under ``## <name>`` (until the next ``## `` or EOF)."""
    parts = re.split(r"(?m)^## ", text)
    for sec in parts:
        if sec.startswith(name):
            return sec[len(name) :].lstrip("\n")
    return None


def _split_subsection(body: str, name: str) -> str | None:
    """Within an already-extracted top-section body, return the body under ``### <name>``."""
    parts = re.split(r"(?m)^### ", body)
    for sec in parts:
        if sec.startswith(name + "\n") or sec.rstrip() == name:
            return sec[len(name) :].lstrip("\n")
    return None


def _parse_bullets(body: str) -> dict[str, str]:
    return {m.group(1).strip(): m.group(2).strip() for m in _BULLET_RE.finditer(body)}


def _parse_roadmap(
    text: str,
) -> tuple[list[RoadmapEntryView], list[SuggestedEntryView], list[DoneEntryView]]:
    up_next_body = _split_top_section(text, "Up next") or ""
    done_body = _split_top_section(text, "Done") or ""

    # Suggested lives nested *inside* `## Up next` as `### Suggested`. Strip
    # it before parsing the main `### <slug>` entries so we don't double-parse.
    sug_body = _split_subsection(up_next_body, "Suggested") or ""
    if sug_body:
        # Cut the suggested block out of up_next_body for the main parse.
        up_next_main = re.sub(r"(?ms)^### Suggested\b.*", "", up_next_body).rstrip() + "\n"
    else:
        up_next_main = up_next_body

    up_next: list[RoadmapEntryView] = []
    matches = list(_ROADMAP_ENTRY_RE.finditer(up_next_main))
    for i, m in enumerate(matches):
        slug = m.group(1)
        if slug == "Suggested":
            continue
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(up_next_main)
        body = up_next_main[start:end].strip()
        bullets = _parse_bullets(body)
        depends_on = re.findall(r"`([^`]+)`", bullets.get("Depends on", ""))
        idea_match = re.search(r"\[`([^`]+)`\]", bullets.get("Idea", ""))
        up_next.append(
            RoadmapEntryView(
                slug=slug,
                idea_id=idea_match.group(1) if idea_match else (bullets.get("Idea") or None),
                hypothesis=bullets.get("Hypothesis", ""),
                plan=bullets.get("Plan", ""),
                depends_on=depends_on,
                cost=bullets.get("Cost") or None,
                body_md=body,
                deps_satisfied=False,  # filled in by reader
            )
        )

    suggested: list[SuggestedEntryView] = []
    sug_matches = list(_SUGGESTED_ENTRY_RE.finditer(sug_body))
    for i, m in enumerate(sug_matches):
        slug = m.group(1)
        start = m.end()
        end = sug_matches[i + 1].start() if i + 1 < len(sug_matches) else len(sug_body)
        body = sug_body[start:end].strip()
        bullets = _parse_bullets(body)
        suggested.append(
            SuggestedEntryView(
                slug=slug,
                hypothesis=bullets.get("Hypothesis", ""),
                source=bullets.get("Source") or None,
                cost=bullets.get("Cost") or None,
                body_md=body,
            )
        )

    done: list[DoneEntryView] = []
    done_matches = list(_ROADMAP_ENTRY_RE.finditer(done_body))
    for i, m in enumerate(done_matches):
        slug = m.group(1)
        start = m.end()
        end = done_matches[i + 1].start() if i + 1 < len(done_matches) else len(done_body)
        body = done_body[start:end].strip()
        bullets = _parse_bullets(body)
        done.append(
            DoneEntryView(
                slug=slug,
                body_md=body,
                ran_link=bullets.get("Ran") or None,
                outcome=bullets.get("Outcome") or None,
            )
        )

    return up_next, suggested, done


_IDEAS_TOP_SECTIONS = ("Proposed", "Trying", "Accepted", "Rejected", "Auto-proposed")
_THEME_RE = re.compile(r"^### (.+)\s*$", re.MULTILINE)
_IDEA_HEADING_RE = re.compile(r"^#### (\S+)\s*\n", re.MULTILINE)


def _parse_ideas(text: str) -> list[IdeaEntryView]:
    out: list[IdeaEntryView] = []
    for section in _IDEAS_TOP_SECTIONS:
        body = _split_top_section(text, section)
        if not body:
            continue
        # `Proposed` has `### <Theme>` subsections; others are flat.
        theme_segments: list[tuple[str | None, str]]
        if section == "Proposed":
            theme_segments = []
            theme_matches = list(_THEME_RE.finditer(body))
            if not theme_matches:
                theme_segments.append((None, body))
            else:
                for i, m in enumerate(theme_matches):
                    theme = m.group(1).strip()
                    start = m.end()
                    end = theme_matches[i + 1].start() if i + 1 < len(theme_matches) else len(body)
                    theme_segments.append((theme, body[start:end]))
        else:
            theme_segments = [(None, body)]

        for theme, segment in theme_segments:
            idea_matches = list(_IDEA_HEADING_RE.finditer(segment))
            for i, m in enumerate(idea_matches):
                idea_id = m.group(1)
                start = m.end()
                end = idea_matches[i + 1].start() if i + 1 < len(idea_matches) else len(segment)
                ibody = segment[start:end].strip()
                bullets = _parse_bullets(ibody)
                # Cross-refs are any **Foo:** bullets we didn't classify as
                # motivation/sketch (e.g. **Trying in:** [...]).
                cross_refs = [
                    f"**{k}:** {v}" for k, v in bullets.items() if k not in {"Motivation", "Sketch"}
                ]
                out.append(
                    IdeaEntryView(
                        idea_id=idea_id,
                        section=section,
                        theme=theme,
                        motivation=bullets.get("Motivation") or None,
                        sketch=bullets.get("Sketch") or None,
                        cross_refs=cross_refs,
                    )
                )
    return out


_JOURNAL_ENTRY_RE = re.compile(r"^## (\d{4}-\d{2}-\d{2})\s+—\s+(\S+)\s*\n", re.MULTILINE)
_JOURNAL_SECTION_RE = re.compile(r"^### (.+?)\s*$", re.MULTILINE)
_RUN_LINK_INSTANCE_RE = re.compile(r"runs/experiments/([A-Za-z0-9_.\-]+)")


def _parse_journal(text: str) -> list[JournalEntryView]:
    out: list[JournalEntryView] = []
    matches = list(_JOURNAL_ENTRY_RE.finditer(text))
    for i, m in enumerate(matches):
        date = m.group(1)
        slug = m.group(2)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].rstrip()

        # Header bullets (before the first ### subsection).
        header_end_match = _JOURNAL_SECTION_RE.search(body)
        header_body = body[: header_end_match.start()] if header_end_match else body
        bullets = _parse_bullets(header_body)
        run_link = bullets.get("Run")
        type_ = bullets.get("Type")
        baseline_at_runtime = bullets.get("Baseline at run-time")
        mutation = bullets.get("Mutation")
        hypothesis = bullets.get("Hypothesis")

        # Subsections.
        sections: dict[str, str] = {}
        sec_matches = list(_JOURNAL_SECTION_RE.finditer(body))
        for j, sm in enumerate(sec_matches):
            sec_name = sm.group(1).strip()
            sstart = sm.end()
            send = sec_matches[j + 1].start() if j + 1 < len(sec_matches) else len(body)
            sections[sec_name] = body[sstart:send].strip()

        instance_id: str | None = None
        if run_link:
            m_id = _RUN_LINK_INSTANCE_RE.search(run_link)
            if m_id:
                instance_id = m_id.group(1)

        out.append(
            JournalEntryView(
                slug=slug,
                date=date,
                type_=type_,
                baseline_at_runtime=baseline_at_runtime,
                mutation=mutation,
                hypothesis=hypothesis,
                run_link=run_link,
                body_md=body,
                sections=sections,
                instance_id=instance_id,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Misc helpers
# ---------------------------------------------------------------------------


def _row_to_spawn(r: dict[str, object]) -> SpawnRow:
    started = _to_dt(r.get("started_at"))
    finished = _to_dt(r.get("finished_at"))
    duration: float | None = None
    if started is not None and finished is not None:
        duration = max(0.0, (finished - started).total_seconds())
    return SpawnRow(
        spawn_id=str(r["spawn_id"]),
        skill=str(r["skill"]),
        provider=_opt_str(r.get("provider")),
        model=_opt_str(r.get("model")),
        started_at=started,
        finished_at=finished,
        duration_sec=duration,
        exit_code=_opt_int(r.get("exit_code")),
        input_tokens=_opt_int(r.get("input_tokens")),
        cached_input_tokens=_opt_int(r.get("cached_input_tokens")),
        output_tokens=_opt_int(r.get("output_tokens")),
        reasoning_output_tokens=_opt_int(r.get("reasoning_output_tokens")),
        total_tokens=_opt_int(r.get("total_tokens")),
        cost_usd_estimate=_opt_float(r.get("cost_usd_estimate")),
        log_path=_opt_str(r.get("log_path")),
        args=_decode_json(r.get("args")),
        notes=_opt_str(r.get("notes")),
    )


def _row_to_usage(r: dict[str, object]) -> UsageSummaryRow:
    return UsageSummaryRow(
        source=str(r.get("source") or ""),
        provider=_opt_str(r.get("provider")),
        model=_opt_str(r.get("model")),
        step=str(r.get("step") or ""),
        calls=int(r.get("calls") or 0),
        input_tokens=_opt_int(r.get("input_tokens")),
        cached_input_tokens=_opt_int(r.get("cached_input_tokens")),
        output_tokens=_opt_int(r.get("output_tokens")),
        reasoning_output_tokens=_opt_int(r.get("reasoning_output_tokens")),
        total_tokens=_opt_int(r.get("total_tokens")),
        cost_usd=_opt_float(r.get("cost_usd")),
    )


def _row_to_evaluation(r: dict[str, object]) -> EvaluationRow:
    return EvaluationRow(
        instance_id=str(r["instance_id"]),
        slug=str(r.get("slug") or ""),
        verdict=str(r.get("verdict") or ""),
        target_id=str(r.get("target_id") or ""),
        rationale=_opt_str(r.get("rationale")),
        confidence=_opt_float(r.get("confidence")),
        applied=bool(r.get("applied")),
        applied_by=_opt_str(r.get("applied_by")),
        applied_at=_to_dt(r.get("applied_at")),
    )


def _to_dt(v: object) -> datetime | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    if isinstance(v, str):
        return _parse_ts(v)
    return None


def _parse_ts(s: str) -> datetime | None:
    s = s.strip()
    if not s:
        return None
    for fmt in (None,):
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            continue
    return None


def _opt_str(v: object) -> str | None:
    if v is None:
        return None
    s = str(v)
    return s if s else None


def _opt_int(v: object) -> int | None:
    if v is None:
        return None
    try:
        return int(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _opt_float(v: object) -> float | None:
    if v is None:
        return None
    try:
        return float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _opt_bool(v: object) -> bool | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        return v.lower() in ("true", "t", "1", "yes")
    return None


def _decode_json(v: object) -> object:
    if v is None:
        return None
    if isinstance(v, (dict, list)):
        return v
    if isinstance(v, str):
        try:
            return json.loads(v)
        except json.JSONDecodeError:
            return v
    return v


def _decode_str_list(v: object) -> list[str]:
    decoded = _decode_json(v)
    if isinstance(decoded, list):
        return [str(x) for x in decoded]
    if isinstance(decoded, str) and decoded:
        return [decoded]
    return []


def _newest_log_file(directory: Path) -> Path | None:
    if not directory.is_dir():
        return None
    candidates = [p for p in directory.glob("*.log") if p.is_file()]
    candidates += [p for p in directory.glob("*.out") if p.is_file()]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _tail_last_nonempty(path: Path, max_bytes: int = 64 * 1024) -> str | None:
    try:
        size = path.stat().st_size
    except OSError:
        return None
    if size == 0:
        return None
    with path.open("rb") as fh:
        fh.seek(max(0, size - max_bytes))
        chunk = fh.read().decode("utf-8", errors="replace")
    for line in reversed(chunk.splitlines()):
        s = line.strip()
        if s:
            return s
    return None


# ---------------------------------------------------------------------------
# Run-dir filesystem helpers (used by the experiment detail page)
# ---------------------------------------------------------------------------


def run_dir_for(instance_id: str) -> Path | None:
    candidate = EXPERIMENTS_RUNS_ROOT / instance_id
    return candidate if candidate.is_dir() else None


def critic_summary_md(instance_id: str) -> str | None:
    run = run_dir_for(instance_id)
    if run is None:
        return None
    p = run / "results" / "critic_summary.md"
    return p.read_text() if p.is_file() else None


def summary_md(instance_id: str) -> str | None:
    run = run_dir_for(instance_id)
    if run is None:
        return None
    p = run / "results" / "summary.md"
    return p.read_text() if p.is_file() else None


def list_log_files(limit: int = 50) -> Iterator[tuple[str, datetime, int]]:
    if not LAB_LOGS_DIR.is_dir():
        return iter(())
    files = [p for p in LAB_LOGS_DIR.iterdir() if p.is_file()]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    for p in files[:limit]:
        st = p.stat()
        yield (p.name, datetime.fromtimestamp(st.st_mtime, tz=timezone.utc), st.st_size)


# ---------------------------------------------------------------------------
# Trial filesystem loaders
# ---------------------------------------------------------------------------


def _row_to_comparison(r: dict[str, object]) -> ComparisonRow:
    return ComparisonRow(
        instance_id=str(r["instance_id"]),
        task_name=str(r["task_name"]),
        winning_leg=str(r.get("winning_leg") or ""),
        runner_up_leg=_opt_str(r.get("runner_up_leg")),
        delta_score=_opt_float(r.get("delta_score")),
        why=_opt_str(r.get("why")),
        legs_compared=_decode_str_list(r.get("legs_compared")),
        critic_model=_opt_str(r.get("critic_model")),
        created_at=_to_dt(r.get("created_at")),
    )


def _load_verifier_report(trial_dir: Path) -> VerifierReport | None:
    if not trial_dir.is_dir():
        return None
    vdir = trial_dir / "verifier"
    if not vdir.is_dir():
        return None

    tool_name: str | None = None
    summary: dict[str, int] = {}
    tests: list[VerifierTest] = []
    ctrf = vdir / "ctrf.json"
    if ctrf.is_file():
        try:
            data = json.loads(ctrf.read_text())
        except json.JSONDecodeError:
            data = {}
        results = data.get("results") or {}
        if isinstance(results, dict):
            tool = results.get("tool") or {}
            if isinstance(tool, dict):
                tool_name = _opt_str(tool.get("name"))
            raw_summary = results.get("summary") or {}
            if isinstance(raw_summary, dict):
                for k, v in raw_summary.items():
                    if isinstance(v, (int, float)) and k in {
                        "tests",
                        "passed",
                        "failed",
                        "skipped",
                        "pending",
                        "other",
                    }:
                        summary[k] = int(v)
            for t in results.get("tests") or []:
                if not isinstance(t, dict):
                    continue
                tests.append(
                    VerifierTest(
                        name=str(t.get("name") or "?"),
                        status=str(t.get("status") or "?"),
                        duration_sec=_opt_float(t.get("duration")),
                        message=_opt_str(t.get("message") or t.get("trace")),
                    )
                )

    reward_text: str | None = None
    rwd = vdir / "reward.txt"
    if rwd.is_file():
        reward_text = rwd.read_text(encoding="utf-8", errors="replace").strip() or None

    stdout_excerpt: str | None = None
    stdout = vdir / "test-stdout.txt"
    if stdout.is_file():
        raw = stdout.read_text(encoding="utf-8", errors="replace")
        if len(raw) > 16_000:
            raw = raw[:8_000] + "\n\n…[truncated]…\n\n" + raw[-8_000:]
        stdout_excerpt = raw or None

    return VerifierReport(
        tool_name=tool_name,
        summary=summary,
        tests=tests,
        reward_text=reward_text,
        stdout_excerpt=stdout_excerpt,
    )


def _load_turns_from_trial(trial_dir: Path, *, max_chars_per_block: int = 6_000) -> list[TurnCard]:
    """Parse messages.jsonl into one TurnCard per assistant/tool boundary.

    The harness records:
      - ``role: "user"``       — initial prompt + tool results when the model
        is reasoning over function-tool outputs.
      - ``role: "assistant"``  — agent reply (text + tool_use blocks).
      - ``role: "tool"`` (rare; varies by SDK)

    We turn each line into one card with role-tinted content. Tool calls
    on assistant messages are split out separately so the template can
    render bash/edit blocks distinctly from prose.
    """
    msgs_path = trial_dir / "messages.jsonl"
    if not msgs_path.is_file():
        return []

    turns: list[TurnCard] = []
    with msgs_path.open("r", encoding="utf-8", errors="replace") as fh:
        for idx, line in enumerate(fh):
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            role = str(msg.get("role") or "?")
            content = msg.get("content")
            texts: list[str] = []
            tool_calls: list[dict[str, object]] = []
            tool_results: list[dict[str, object]] = []

            blocks = (
                content
                if isinstance(content, list)
                else ([{"type": "text", "text": content}] if isinstance(content, str) else [])
            )
            for b in blocks:
                if not isinstance(b, dict):
                    continue
                btype = b.get("type")
                if btype == "text":
                    text = str(b.get("text") or "")
                    if len(text) > max_chars_per_block:
                        text = text[:max_chars_per_block] + "\n…[truncated]…"
                    texts.append(text)
                elif btype == "tool_use":
                    tool_calls.append(
                        {
                            "id": b.get("id"),
                            "name": b.get("name"),
                            "input": b.get("input"),
                        }
                    )
                elif btype == "tool_result":
                    text = b.get("content")
                    if isinstance(text, list):
                        text = "\n".join(
                            str(c.get("text", "")) for c in text if isinstance(c, dict)
                        )
                    text = str(text or "")
                    if len(text) > max_chars_per_block:
                        text = text[:max_chars_per_block] + "\n…[truncated]…"
                    tool_results.append(
                        {
                            "tool_use_id": b.get("tool_use_id"),
                            "is_error": b.get("is_error", False),
                            "content": text,
                        }
                    )

            n_chars = sum(len(t) for t in texts) + sum(
                len(str(t.get("content", ""))) for t in tool_results
            )
            turns.append(
                TurnCard(
                    index=idx,
                    role=role,
                    texts=texts,
                    tool_calls=tool_calls,
                    tool_results=tool_results,
                    n_chars=n_chars,
                )
            )
    return turns


def _first_user_text(turns: list[TurnCard]) -> str | None:
    for t in turns:
        if t.role == "user" and t.texts:
            return t.texts[0]
    return None


def _list_raw_files(trial_dir: Path) -> list[str]:
    if not trial_dir.is_dir():
        return []
    out: list[str] = []
    for entry in sorted(trial_dir.iterdir()):
        if entry.name.startswith("."):
            continue
        out.append(entry.name + ("/" if entry.is_dir() else ""))
    return out


def find_trial_dir(instance_id: str, trial_id: str) -> Path | None:
    """Walk the run dir looking for a directory that ends with ``trial_id``.

    The DB row's ``trial_dir`` is authoritative when present; this
    helper exists for trials we surface from filesystem-only fallback
    paths.
    """
    run = run_dir_for(instance_id)
    if run is None:
        return None
    for p in run.rglob(trial_id):
        if p.is_dir():
            return p
    return None
