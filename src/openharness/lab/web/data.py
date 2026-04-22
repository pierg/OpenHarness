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
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from openharness.lab import db as labdb
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
    ComparisonRow,
    ComponentDetail,
    ComponentPerfRow,
    DaemonStatus,
    DoneEntryView,
    ExperimentSummary,
    IdeaEntryView,
    JournalEntryView,
    LegSummary,
    PendingActions,
    ProcessNode,
    RoadmapEntryView,
    SpawnRow,
    SuggestedEntryView,
    TaskAggregateRow,
    TaskClusterRow,
    TaskFeatureView,
    TaskLeaderboardRow,
    TreeDiffRow,
    TrialCritique,
    TrialDetail,
    TrialRow,
    TrunkChangeRow,
    TurnCard,
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
            "tree_diffs",
            "trunk_changes",
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
        legacy lock file). One root node — the daemon itself — sits
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

        # Prefer systemd's view (it's authoritative when the unit is
        # installed); fall back to the lock file for the legacy
        # ad-hoc backgrounding path so this still works in dev.
        unit_pid = labsvc.status("openharness-daemon").main_pid
        legacy_pid = self.daemon_status().pid
        daemon_pid = unit_pid or legacy_pid
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
                    info = proc.as_dict(attrs=[
                        "pid", "ppid", "name", "username", "status",
                        "create_time", "cpu_percent", "memory_info",
                        "cmdline",
                    ])
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                # Process died between enumeration and inspection.
                continue
            cmd = info.get("cmdline") or [info.get("name") or ""]
            full = " ".join(cmd) if cmd else (info.get("name") or "")
            short = full if len(full) <= cmdline_max else full[:cmdline_max - 1] + "…"
            mem = info.get("memory_info")
            mem_mb = (mem.rss / (1024 * 1024)) if mem is not None else 0.0
            ts = info.get("create_time")
            started = (
                datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None
            )
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
        rows = self._qd(
            """
            SELECT spawn_id, skill, started_at, finished_at, exit_code,
                   cost_usd_estimate, log_path, args, notes
            FROM spawns
            ORDER BY started_at DESC NULLS LAST
            LIMIT ?
            """,
            [limit],
        )
        return [_row_to_spawn(r) for r in rows]

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

        verdicts = {r["instance_id"]: _row_to_diff(r) for r in self._qd(
            "SELECT * FROM tree_diffs"
        )}
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

    def task_pass_matrix(self, instance_id: str) -> tuple[list[str], list[str], dict[tuple[str, str], TrialRow]]:
        """Return (tasks, legs, cell_lookup) for the per-task heatmap."""
        trials = self.trials(instance_id)
        tasks = sorted({t.task_name for t in trials})
        legs = sorted({t.leg_id for t in trials})
        cells = {(t.task_name, t.leg_id): t for t in trials}
        return tasks, legs, cells

    # ---- tree / components / trunk_changes ------------------------------

    def tree(self) -> TreeSnapshot:
        return tree_snapshot()

    def components(self) -> ComponentsCatalog:
        return read_catalog()

    def trunk_history(self, limit: int = 50) -> list[TrunkChangeRow]:
        if not self._db_available:
            return []
        rows = self._qd(
            """
            SELECT at_ts, from_id, to_id, reason, applied_by, instance_id
            FROM trunk_changes
            ORDER BY at_ts DESC
            LIMIT ?
            """,
            [limit],
        )
        return [
            TrunkChangeRow(
                at_ts=_to_dt(r["at_ts"]) or datetime.now(timezone.utc),
                from_id=_opt_str(r.get("from_id")),
                to_id=str(r["to_id"]),
                reason=_opt_str(r.get("reason")),
                applied_by=str(r.get("applied_by") or "?"),
                instance_id=_opt_str(r.get("instance_id")),
            )
            for r in rows
        ]

    # ---- slug → instance_id resolution + verdict preview --------------

    def resolve_slug(self, slug: str) -> str | None:
        """Map an experiment slug to an ``experiments.instance_id``.

        Mirrors the resolution order in
        :func:`openharness.lab.cli._lookup_instance_for_slug` so the
        web UI's "Preview verdict" button finds the same instance the
        ``uv run lab tree apply <slug>`` CLI would. Read-only — the
        whole call uses the LabReader's read-only DuckDB connection.
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
            "SELECT instance_id FROM tree_diffs WHERE slug = ?", [slug]
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
            "SELECT instance_id, experiment_id FROM experiments "
            "ORDER BY created_at DESC"
        )
        for inst_id, eid in rows:
            if eid and slug.startswith(f"{eid}-"):  # type: ignore[operator]
                return str(inst_id)
        return None

    def experiments_without_diff(self, limit: int = 20) -> list[ExperimentSummary]:
        """Recent experiments that have no row in ``tree_diffs``.

        These are the typical "rescue" candidates for the
        ``tree apply`` button: the daemon either failed at the verdict
        step or the run was started by hand and never had a verdict
        computed. Sorted newest-first so the most relevant ones bubble
        up.
        """

        # Reuses ``experiments()`` (which already joins in ``verdict``)
        # rather than duplicating the SQL aggregate. Cheap because the
        # experiments table is small (one row per experiment, and the
        # lab typically holds a few dozen).
        return [e for e in self.experiments(limit=None) if e.verdict is None][:limit]

    def preview_diff(self, slug: str) -> dict[str, object] | None:
        """Recompute the TreeDiff for ``slug`` without applying it.

        Returns ``None`` when the slug doesn't resolve to any known
        instance, so the caller can render a clear "unknown experiment"
        message rather than a confusing empty diff. The diff itself is
        the dict form (``TreeDiff.to_dict()``) so templates don't need
        to import the tree_ops dataclass.
        """

        from openharness.lab import tree_ops as _tree_ops

        instance_id = self.resolve_slug(slug)
        if instance_id is None:
            return None
        diff = _tree_ops.evaluate(instance_id, db_conn=self._conn)
        out = diff.to_dict()
        # Echo the slug + resolved instance_id so the template doesn't
        # need to thread them in separately.
        out["slug"] = slug
        out["resolved_instance_id"] = instance_id
        return out

    def tree_diffs(self, *, applied: bool | None = None,
                   kind: str | None = None, limit: int = 100) -> list[TreeDiffRow]:
        if not self._db_available:
            return []
        sql = "SELECT * FROM tree_diffs WHERE 1=1"
        params: list[object] = []
        if applied is not None:
            sql += " AND applied = ?"
            params.append(applied)
        if kind is not None:
            sql += " AND kind = ?"
            params.append(kind)
        sql += " ORDER BY applied_at DESC NULLS LAST LIMIT ?"
        params.append(limit)
        return [_row_to_diff(r) for r in self._qd(sql, params)]

    # ---- markdown surfaces ---------------------------------------------

    def roadmap(self) -> tuple[list[RoadmapEntryView], list[SuggestedEntryView], list[DoneEntryView]]:
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
        staged = self.tree_diffs(applied=False, kind="graduate")
        _, suggested, _ = self.roadmap()
        auto = [i for i in self.ideas() if i.section == "Auto-proposed"]
        misconf = 0
        if self._db_available:
            rows = self._q("SELECT count(*) FROM misconfigurations WHERE created_at >= ?",
                           [datetime.now(timezone.utc) - recent_window])
            misconf = int(rows[0][0]) if rows else 0  # type: ignore[arg-type]
        failed = self.failed_spawns_since(datetime.now(timezone.utc) - recent_window)
        return PendingActions(
            staged_graduates=staged,
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
            return sec[len(name):].lstrip("\n")
    return None


def _split_subsection(body: str, name: str) -> str | None:
    """Within an already-extracted top-section body, return the body under ``### <name>``."""
    parts = re.split(r"(?m)^### ", body)
    for sec in parts:
        if sec.startswith(name + "\n") or sec.rstrip() == name:
            return sec[len(name):].lstrip("\n")
    return None


def _parse_bullets(body: str) -> dict[str, str]:
    return {m.group(1).strip(): m.group(2).strip() for m in _BULLET_RE.finditer(body)}


def _parse_roadmap(text: str) -> tuple[list[RoadmapEntryView], list[SuggestedEntryView], list[DoneEntryView]]:
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
        up_next.append(RoadmapEntryView(
            slug=slug,
            idea_id=idea_match.group(1) if idea_match else (bullets.get("Idea") or None),
            hypothesis=bullets.get("Hypothesis", ""),
            plan=bullets.get("Plan", ""),
            depends_on=depends_on,
            cost=bullets.get("Cost") or None,
            body_md=body,
            deps_satisfied=False,  # filled in by reader
        ))

    suggested: list[SuggestedEntryView] = []
    sug_matches = list(_SUGGESTED_ENTRY_RE.finditer(sug_body))
    for i, m in enumerate(sug_matches):
        slug = m.group(1)
        start = m.end()
        end = sug_matches[i + 1].start() if i + 1 < len(sug_matches) else len(sug_body)
        body = sug_body[start:end].strip()
        bullets = _parse_bullets(body)
        suggested.append(SuggestedEntryView(
            slug=slug,
            hypothesis=bullets.get("Hypothesis", ""),
            source=bullets.get("Source") or None,
            cost=bullets.get("Cost") or None,
            body_md=body,
        ))

    done: list[DoneEntryView] = []
    done_matches = list(_ROADMAP_ENTRY_RE.finditer(done_body))
    for i, m in enumerate(done_matches):
        slug = m.group(1)
        start = m.end()
        end = done_matches[i + 1].start() if i + 1 < len(done_matches) else len(done_body)
        body = done_body[start:end].strip()
        bullets = _parse_bullets(body)
        done.append(DoneEntryView(
            slug=slug,
            body_md=body,
            ran_link=bullets.get("Ran") or None,
            outcome=bullets.get("Outcome") or None,
        ))

    return up_next, suggested, done


_IDEAS_TOP_SECTIONS = ("Proposed", "Trying", "Graduated", "Rejected", "Auto-proposed")
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
                    f"**{k}:** {v}"
                    for k, v in bullets.items()
                    if k not in {"Motivation", "Sketch"}
                ]
                out.append(IdeaEntryView(
                    idea_id=idea_id,
                    section=section,
                    theme=theme,
                    motivation=bullets.get("Motivation") or None,
                    sketch=bullets.get("Sketch") or None,
                    cross_refs=cross_refs,
                ))
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
        trunk = bullets.get("Trunk at run-time")
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

        out.append(JournalEntryView(
            slug=slug,
            date=date,
            type_=type_,
            trunk_at_runtime=trunk,
            mutation=mutation,
            hypothesis=hypothesis,
            run_link=run_link,
            body_md=body,
            sections=sections,
            instance_id=instance_id,
        ))
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
        started_at=started,
        finished_at=finished,
        duration_sec=duration,
        exit_code=_opt_int(r.get("exit_code")),
        cost_usd_estimate=_opt_float(r.get("cost_usd_estimate")),
        log_path=_opt_str(r.get("log_path")),
        args=_decode_json(r.get("args")),
        notes=_opt_str(r.get("notes")),
    )


def _row_to_diff(r: dict[str, object]) -> TreeDiffRow:
    return TreeDiffRow(
        instance_id=str(r["instance_id"]),
        slug=str(r.get("slug") or ""),
        kind=str(r.get("kind") or ""),
        target_id=str(r.get("target_id") or ""),
        rationale=_opt_str(r.get("rationale")),
        use_when=_decode_json(r.get("use_when")),
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
                        "tests", "passed", "failed", "skipped", "pending", "other",
                    }:
                        summary[k] = int(v)
            for t in results.get("tests") or []:
                if not isinstance(t, dict):
                    continue
                tests.append(VerifierTest(
                    name=str(t.get("name") or "?"),
                    status=str(t.get("status") or "?"),
                    duration_sec=_opt_float(t.get("duration")),
                    message=_opt_str(t.get("message") or t.get("trace")),
                ))

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

            blocks = content if isinstance(content, list) else (
                [{"type": "text", "text": content}] if isinstance(content, str) else []
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
                    tool_calls.append({
                        "id": b.get("id"),
                        "name": b.get("name"),
                        "input": b.get("input"),
                    })
                elif btype == "tool_result":
                    text = b.get("content")
                    if isinstance(text, list):
                        text = "\n".join(
                            str(c.get("text", "")) for c in text if isinstance(c, dict)
                        )
                    text = str(text or "")
                    if len(text) > max_chars_per_block:
                        text = text[:max_chars_per_block] + "\n…[truncated]…"
                    tool_results.append({
                        "tool_use_id": b.get("tool_use_id"),
                        "is_error": b.get("is_error", False),
                        "content": text,
                    })

            n_chars = sum(len(t) for t in texts) + sum(
                len(str(t.get("content", ""))) for t in tool_results
            )
            turns.append(TurnCard(
                index=idx,
                role=role,
                texts=texts,
                tool_calls=tool_calls,
                tool_results=tool_results,
                n_chars=n_chars,
            ))
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
