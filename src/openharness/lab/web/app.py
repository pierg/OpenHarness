"""FastAPI app for the lab web UI.

Read-only Phase 1. Routes mirror the IA in the design doc:

    /                    operator console (home)
    /pending             focused inbox (drawer's full view)
    /tree                configuration tree
    /components          catalog of atoms
    /experiments         journal index (from DB + lab/experiments.md)
    /experiments/{id}    one run (legs, per-task heatmap, journal entry)
    /ideas               themed backlog
    /roadmap             priority queue
    /spawns              model skill spawn audit log
    /usage               token / cost usage summary
    /spawns/{name}       one log file (text/plain)
    /daemon              daemon status + log tail
    /api/pending         JSON for the right-rail HTMX poll
    /healthz             liveness

All HTML is rendered with Jinja2 + HTMX + Tailwind (CDN). No build
step. Mutations are NOT exposed in this slice — the design contract
keeps writes flowing through ``uv run lab ...`` until Phase 3.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from openharness.lab.paths import LAB_LOGS_DIR
from openharness.lab.web import auth as labauth
from openharness.lab.web import commands as labcmd
from openharness.lab.web import data as labdata
from openharness.lab.web import markdown as labmd
from openharness.lab.web import services as labsvc

log = logging.getLogger(__name__)

_HERE = Path(__file__).parent
_TEMPLATES_DIR = _HERE / "templates"
_STATIC_DIR = _HERE / "static"


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_app() -> FastAPI:
    app = FastAPI(
        title="OpenHarness lab",
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )

    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
    templates.env.globals["render_md"] = labmd.render
    templates.env.globals["fmt_dt"] = _fmt_dt
    templates.env.globals["fmt_delta"] = _fmt_delta
    templates.env.globals["fmt_elapsed"] = _fmt_elapsed
    templates.env.globals["fmt_money"] = _fmt_money
    templates.env.globals["fmt_int"] = _fmt_int
    templates.env.globals["pct_color"] = _pct_color
    templates.env.globals["status_color"] = _status_color
    templates.env.globals["verdict_color"] = _verdict_color
    templates.env.globals["cmd_specs"] = labcmd.COMMANDS
    # Auth posture exposed to templates so the header badge can render
    # the active mode without re-importing the auth module per page.
    templates.env.globals["auth_mode"] = labauth.configured_mode

    if _STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    # ---- request-scoped reader -------------------------------------------

    def _reader_ctx(request: Request) -> labdata.LabReader:
        # Open per-request to keep DuckDB usage thread-safe.
        return labdata.LabReader().__enter__()

    def _close_reader(request: Request, reader: labdata.LabReader) -> None:
        reader.__exit__(None, None, None)

    def _render(request: Request, template: str, **ctx: Any) -> HTMLResponse:
        reader: labdata.LabReader = ctx.pop("_reader")
        try:
            ctx.setdefault("pending", reader.pending_actions())
            ctx.setdefault("nav_active", "")
            ctx.setdefault("db_available", reader.db_available)
            ctx.setdefault("identity", labauth.identify(request))
            return templates.TemplateResponse(request, template, ctx)
        finally:
            _close_reader(request, reader)

    # ---- routes ---------------------------------------------------------

    @app.get("/healthz", response_class=PlainTextResponse)
    def healthz() -> str:
        return "ok"

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request) -> HTMLResponse:
        # New /-as-status-room. The "Now / Waiting on / You owe" zones
        # are HTMX-mounted partials so each refreshes independently
        # without re-rendering the whole control room. We still
        # populate them on the first render so the page is meaningful
        # before the first HTMX swap completes.
        reader = _reader_ctx(request)
        try:
            recent_exp = reader.experiments(limit=8)
            return _render(
                request,
                "home.html",
                _reader=reader,
                nav_active="status",
                recent_experiments=recent_exp,
                db_info=reader.db_info(),
                db_path=str(reader.db_path),
                status=reader.daemon_status(),
                daemon_state=reader.daemon_state(),
                pipeline=reader.pipeline_view(),
                idle_reason=reader.idle_reason(),
            )
        except Exception:
            _close_reader(request, reader)
            raise

    @app.get("/pending", response_class=HTMLResponse)
    def pending(request: Request) -> HTMLResponse:
        # Legacy: kept so external bookmarks keep working. The sidebar
        # surfaces the same content via the home page's "You owe" zone.
        reader = _reader_ctx(request)
        return _render(request, "pending.html", _reader=reader, nav_active="pending")

    @app.get("/tree", response_class=HTMLResponse)
    def tree(request: Request) -> HTMLResponse:
        reader = _reader_ctx(request)
        try:
            pr_rows = reader.pr_states()
            pr_by_slug = {pr.slug: pr for pr in pr_rows}
            pr_by_instance = {pr.instance_id: pr for pr in pr_rows}
            return _render(
                request,
                "tree.html",
                _reader=reader,
                nav_active="tree",
                snapshot=reader.tree(),
                trunk_history=reader.trunk_history(limit=20),
                pending_merge=reader.tree_diffs(applied=False, limit=20),
                pending_eval=reader.experiments_without_diff(limit=10),
                recent_diffs=reader.tree_diffs(applied=True, limit=10),
                pr_by_slug=pr_by_slug,
                pr_by_instance=pr_by_instance,
            )
        except Exception:
            _close_reader(request, reader)
            raise

    @app.get("/components", response_class=HTMLResponse)
    def components(request: Request) -> HTMLResponse:
        reader = _reader_ctx(request)
        try:
            cat = reader.components()
            perf = reader.components_perf()
            perf_by_id: dict[str, list[Any]] = {}
            for row in perf:
                perf_by_id.setdefault(row.component_id, []).append(row)
            return _render(
                request,
                "components.html",
                _reader=reader,
                nav_active="components",
                catalog=cat,
                perf_by_id=perf_by_id,
            )
        except Exception:
            _close_reader(request, reader)
            raise

    @app.get("/experiments", response_class=HTMLResponse)
    def experiments(request: Request) -> HTMLResponse:
        reader = _reader_ctx(request)
        try:
            return _render(
                request,
                "experiments_list.html",
                _reader=reader,
                nav_active="experiments",
                experiments=reader.experiments(limit=200),
                journal=reader.journal(),
            )
        except Exception:
            _close_reader(request, reader)
            raise

    @app.get("/experiments/{instance_id}", response_class=HTMLResponse)
    def experiment_detail(request: Request, instance_id: str) -> HTMLResponse:
        reader = _reader_ctx(request)
        try:
            exp = reader.experiment(instance_id)
            if exp is None:
                # If the run dir exists but never got ingested, we still want
                # to surface what we can.
                if labdata.run_dir_for(instance_id) is None:
                    _close_reader(request, reader)
                    raise HTTPException(404, f"unknown instance {instance_id}")
            legs = reader.legs(instance_id)
            tasks, leg_ids, cells = reader.task_pass_matrix(instance_id)
            verdict = next((d for d in reader.tree_diffs() if d.instance_id == instance_id), None)
            journal = reader.journal_entry_for_instance(instance_id)
            clusters = reader.task_clusters_for_instance(instance_id)
            comparisons = reader.comparisons_for_instance(instance_id)
            critic_md = labdata.critic_summary_md(instance_id)
            sum_md = labdata.summary_md(instance_id)
            pr_for_run = next(
                (pr for pr in reader.pr_states() if pr.instance_id == instance_id),
                None,
            )
            return _render(
                request,
                "experiment_detail.html",
                _reader=reader,
                nav_active="experiments",
                instance_id=instance_id,
                experiment=exp,
                legs=legs,
                tasks=tasks,
                leg_ids=leg_ids,
                cells=cells,
                verdict=verdict,
                journal=journal,
                clusters=clusters,
                comparisons=comparisons,
                critic_md=critic_md,
                summary_md=sum_md,
                pr_for_run=pr_for_run,
            )
        except HTTPException:
            raise
        except Exception:
            _close_reader(request, reader)
            raise

    @app.get("/ideas", response_class=HTMLResponse)
    def ideas(request: Request) -> HTMLResponse:
        reader = _reader_ctx(request)
        try:
            ideas_list = reader.ideas()
            grouped: dict[str, dict[str | None, list[Any]]] = {}
            for i in ideas_list:
                grouped.setdefault(i.section, {}).setdefault(i.theme, []).append(i)
            return _render(
                request,
                "ideas.html",
                _reader=reader,
                nav_active="ideas",
                grouped=grouped,
            )
        except Exception:
            _close_reader(request, reader)
            raise

    @app.get("/roadmap", response_class=HTMLResponse)
    def roadmap(request: Request) -> HTMLResponse:
        reader = _reader_ctx(request)
        try:
            up_next, suggested, done = reader.roadmap()
            return _render(
                request,
                "roadmap.html",
                _reader=reader,
                nav_active="roadmap",
                up_next=up_next,
                suggested=suggested,
                done=done,
                # daemon_state powers the per-row "queued / running"
                # badges and the Approve/Revoke buttons. Cheap to
                # load; pulled here so the body partial can render
                # the same affordances on its standalone refresh.
                daemon_state=reader.daemon_state(),
            )
        except Exception:
            _close_reader(request, reader)
            raise

    @app.get("/spawns", response_class=HTMLResponse)
    def spawns(request: Request, limit: int = 100) -> HTMLResponse:
        reader = _reader_ctx(request)
        try:
            return _render(
                request,
                "spawns.html",
                _reader=reader,
                nav_active="spawns",
                spawns=reader.recent_spawns(limit=limit),
                logs=list(labdata.list_log_files(limit=50)),
                limit=limit,
            )
        except Exception:
            _close_reader(request, reader)
            raise

    @app.get("/usage", response_class=HTMLResponse)
    def usage(request: Request) -> HTMLResponse:
        reader = _reader_ctx(request)
        try:
            rows = reader.usage_summary()
            pipeline_rows = [r for r in rows if r.source == "pipeline"]
            trial_rows = [r for r in rows if r.source == "agent trials"]
            return _render(
                request,
                "usage.html",
                _reader=reader,
                nav_active="usage",
                rows=rows,
                pipeline_rows=pipeline_rows,
                trial_rows=trial_rows,
            )
        except Exception:
            _close_reader(request, reader)
            raise

    @app.get("/experiments/{instance_id}/trials/{trial_id}", response_class=HTMLResponse)
    def trial_detail(request: Request, instance_id: str, trial_id: str) -> HTMLResponse:
        reader = _reader_ctx(request)
        try:
            trial = reader.trial(instance_id, trial_id)
            if trial is None:
                _close_reader(request, reader)
                raise HTTPException(404, f"unknown trial {trial_id} in {instance_id}")
            return _render(
                request,
                "trial_detail.html",
                _reader=reader,
                nav_active="experiments",
                instance_id=instance_id,
                trial=trial,
            )
        except HTTPException:
            raise
        except Exception:
            _close_reader(request, reader)
            raise

    @app.get("/tasks", response_class=HTMLResponse)
    def tasks(request: Request) -> HTMLResponse:
        reader = _reader_ctx(request)
        try:
            return _render(
                request,
                "tasks_list.html",
                _reader=reader,
                nav_active="tasks",
                tasks=reader.tasks_index(),
            )
        except Exception:
            _close_reader(request, reader)
            raise

    @app.get("/tasks/{checksum}", response_class=HTMLResponse)
    def task_detail(request: Request, checksum: str) -> HTMLResponse:
        reader = _reader_ctx(request)
        try:
            features = reader.task_features(checksum)
            board = reader.task_leaderboard(checksum)
            task_name = (
                features.task_name if features else (board[0].leg_id if board else checksum[:16])
            )
            if board:
                # Pull the canonical task name from any trial row.
                trials_with_checksum = [
                    t for t in reader.trials(board[0].instance_id) if t.task_checksum == checksum
                ]
                if trials_with_checksum:
                    task_name = trials_with_checksum[0].task_name
            comparisons = reader.comparisons_for_task(task_name) if task_name else []
            return _render(
                request,
                "task_detail.html",
                _reader=reader,
                nav_active="tasks",
                checksum=checksum,
                task_name=task_name,
                features=features,
                board=board,
                comparisons=comparisons,
            )
        except Exception:
            _close_reader(request, reader)
            raise

    @app.get("/components-perf", response_class=HTMLResponse)
    def components_perf(request: Request) -> HTMLResponse:
        reader = _reader_ctx(request)
        try:
            rows = reader.components_perf()
            components_set = sorted({r.component_id for r in rows})
            clusters_set = sorted({r.task_cluster for r in rows})
            cell_lookup: dict[tuple[str, str], Any] = {
                (r.component_id, r.task_cluster): r for r in rows
            }
            return _render(
                request,
                "components_perf.html",
                _reader=reader,
                nav_active="components-perf",
                rows=rows,
                components_axis=components_set,
                clusters_axis=clusters_set,
                cells=cell_lookup,
            )
        except Exception:
            _close_reader(request, reader)
            raise

    @app.get("/components/{component_id}", response_class=HTMLResponse)
    def component_detail(request: Request, component_id: str) -> HTMLResponse:
        reader = _reader_ctx(request)
        try:
            detail = reader.component_detail(component_id)
            if detail is None:
                _close_reader(request, reader)
                raise HTTPException(404, f"unknown component {component_id}")
            return _render(
                request,
                "component_detail.html",
                _reader=reader,
                nav_active="components",
                detail=detail,
            )
        except HTTPException:
            raise
        except Exception:
            _close_reader(request, reader)
            raise

    @app.get("/spawns/log/{name}", response_class=PlainTextResponse)
    def spawn_log(name: str) -> PlainTextResponse:
        # Only allow filenames inside LAB_LOGS_DIR.
        path = (LAB_LOGS_DIR / name).resolve()
        if not path.is_file() or LAB_LOGS_DIR.resolve() not in path.parents:
            raise HTTPException(404, "log not found")
        return PlainTextResponse(path.read_text(encoding="utf-8", errors="replace"))

    @app.get("/daemon", response_class=HTMLResponse)
    def daemon(request: Request) -> HTMLResponse:
        reader = _reader_ctx(request)
        try:
            status = reader.daemon_status()
            tail: list[str] = []
            if status.log_path:
                tail = reader.tail_log(Path(status.log_path), n=300)
            return _render(
                request,
                "daemon.html",
                _reader=reader,
                nav_active="daemon",
                status=status,
                tail=tail,
                services=labsvc.all_status(),
                services_available=labsvc.available(),
                process_tree=reader.process_tree(),
                daemon_state=reader.daemon_state(),
                pipeline=reader.pipeline_view(),
            )
        except Exception:
            _close_reader(request, reader)
            raise

    # ---- HTMX partials --------------------------------------------------

    @app.get("/_hx/pending", response_class=HTMLResponse)
    def hx_pending(request: Request) -> HTMLResponse:
        reader = _reader_ctx(request)
        try:
            return _render(
                request,
                "_drawer.html",
                _reader=reader,
                # Suppress drawer->drawer recursion.
                pending=reader.pending_actions(),
            )
        except Exception:
            _close_reader(request, reader)
            raise

    @app.get("/_hx/daemon-tail", response_class=HTMLResponse)
    def hx_daemon_tail(request: Request) -> HTMLResponse:
        reader = _reader_ctx(request)
        try:
            status = reader.daemon_status()
            tail: list[str] = []
            if status.log_path:
                tail = reader.tail_log(Path(status.log_path), n=300)
            return _render(
                request,
                "_log_tail.html",
                _reader=reader,
                status=status,
                tail=tail,
            )
        except Exception:
            _close_reader(request, reader)
            raise

    @app.get("/_hx/daemon-active-spawn", response_class=HTMLResponse)
    def hx_daemon_active_spawn(request: Request) -> HTMLResponse:
        """The "what is the daemon actually running" surface.

        Picks one log path with this priority and returns its tail:

          1. ``daemon_state.active_tick.log_path`` — the codex spawn
             attached to the in-flight tick, if any. This is the
             *live* output the operator wants when something is
             happening right now.
          2. The newest ``*.log`` under ``runs/lab/logs/`` — the most
             recent finished spawn. Useful immediately after a tick
             ended (so the operator can read the verdict) and as a
             fallback when the daemon has nothing in flight.
          3. None — render an empty-state placeholder.

        Distinct from ``/_hx/daemon-journal`` (which is the daemon's
        OWN systemd output) and from the per-row "view spawn log"
        disclosures in the history panel (which are the per-tick
        archive view).
        """
        from openharness.lab import paths as _paths
        from openharness.lab import daemon_state as _ds

        state = _ds.load()
        chosen: Path | None = None
        source: str = "idle"
        if state.active_tick and state.active_tick.log_path:
            p = Path(state.active_tick.log_path)
            if p.is_file():
                chosen = p
                source = "active"
        if chosen is None:
            newest = labdata._newest_log_file(_paths.LAB_LOGS_DIR)
            if newest is not None:
                chosen = newest
                source = "newest"

        tail = ""
        truncated = False
        size = 0
        if chosen is not None:
            max_bytes = 16 * 1024
            with chosen.open("rb") as fh:
                try:
                    fh.seek(-max_bytes, os.SEEK_END)
                    truncated = True
                except OSError:
                    fh.seek(0)
                tail = fh.read().decode("utf-8", errors="replace")
            size = chosen.stat().st_size

        return HTMLResponse(
            templates.get_template("_daemon_active_spawn.html").render(
                request=request,
                source=source,
                log_path=str(chosen) if chosen else "",
                log_basename=chosen.name if chosen else "",
                tail=tail,
                truncated=truncated,
                size=size,
                active_slug=(state.active_tick.slug if state.active_tick else ""),
            )
        )

    @app.get("/_hx/daemon-journal", response_class=HTMLResponse)
    def hx_daemon_journal(
        request: Request,
        lines: int = 300,
    ) -> HTMLResponse:
        """Tail of `journalctl --user -u openharness-daemon`.

        This is the operator's "what is the daemon doing right now"
        feed — orchestrator loop iterations, signal wake-ups, exit-gate
        decisions, ingest summaries, anything the runner logs at INFO
        or above. Distinct from `_hx/daemon-tail`, which is a stale
        legacy path that reads `runs/lab/orchestrator.out` (only
        populated under tmux/nohup).

        The `lines` query param is bounded to a sensible range so a
        rogue caller can't request 10 M lines and OOM the page render.
        """
        n = max(50, min(int(lines), 2000))
        text = labsvc.journal("openharness-daemon", lines=n)
        return _render(
            request,
            "_daemon_journal.html",
            _reader=_reader_ctx(request),
            journal_text=text,
            requested_lines=n,
            unit_id="openharness-daemon",
        )

    @app.get("/_hx/daemon-status", response_class=HTMLResponse)
    def hx_daemon_status(request: Request) -> HTMLResponse:
        reader = _reader_ctx(request)
        try:
            return _render(
                request,
                "_daemon_status.html",
                _reader=reader,
                status=reader.daemon_status(),
            )
        except Exception:
            _close_reader(request, reader)
            raise

    @app.get("/_hx/home-daemon", response_class=HTMLResponse)
    def hx_home_daemon(request: Request) -> HTMLResponse:
        reader = _reader_ctx(request)
        try:
            return _render(
                request,
                "_home_daemon.html",
                _reader=reader,
                daemon=reader.daemon_status(),
            )
        except Exception:
            _close_reader(request, reader)
            raise

    @app.get("/_hx/services", response_class=HTMLResponse)
    def hx_services(request: Request) -> HTMLResponse:
        """Just the services panel — refreshed on `lab-services-changed`
        (start/stop/restart) or every 10 s as a heartbeat."""
        reader = _reader_ctx(request)
        try:
            return _render(
                request,
                "_services_panel.html",
                _reader=reader,
                services=labsvc.all_status(),
                services_available=labsvc.available(),
            )
        except Exception:
            _close_reader(request, reader)
            raise

    @app.get("/_hx/process-tree", response_class=HTMLResponse)
    def hx_process_tree(request: Request) -> HTMLResponse:
        """Just the process-tree panel — refreshed every 5 s plus on
        `lab-processes-changed` so a kill-process call updates the
        view immediately."""
        reader = _reader_ctx(request)
        try:
            return _render(
                request,
                "_process_tree.html",
                _reader=reader,
                process_tree=reader.process_tree(),
            )
        except Exception:
            _close_reader(request, reader)
            raise

    # ---- daemon cockpit partials --------------------------------
    #
    # Each one renders a single panel of /daemon by re-loading the
    # daemon-state.json snapshot. Reads are cheap (one JSON read +
    # tiny parse), so we don't bother caching. All four take exactly
    # the same context shape: a single `daemon_state` keyword.

    @app.get("/_hx/daemon-control-bar", response_class=HTMLResponse)
    def hx_daemon_control_bar(request: Request) -> HTMLResponse:
        """Combined status + mode + start/stop/restart row.

        Replaces the previous split between the bottom-of-page
        ``/_hx/daemon-status`` panel and the top-of-page
        ``/_hx/daemon-mode`` toggle. The legacy endpoints stay
        registered so external dashboards / tests that target them
        keep working; the cockpit just doesn't use them anymore.
        """
        reader = _reader_ctx(request)
        try:
            return _render(
                request,
                "_daemon_control_bar.html",
                _reader=reader,
                status=reader.daemon_status(),
                daemon_state=reader.daemon_state(),
            )
        except Exception:
            _close_reader(request, reader)
            raise

    @app.get("/_hx/daemon-pipeline", response_class=HTMLResponse)
    def hx_daemon_pipeline(request: Request) -> HTMLResponse:
        """The 7-phase pipeline strip + per-slug action bar.

        Pulls a :class:`PipelineView` for the active slug (or the
        most-recent one when idle) and hands it to the template, so
        the pipeline panel renders the *same* shape regardless of
        whether the daemon is running or paused. That stability is
        the whole point of the redesign — operators always see the
        same widget, not "running" vs "idle" page chrome flips.
        """
        reader = _reader_ctx(request)
        try:
            return _render(
                request,
                "_daemon_pipeline.html",
                _reader=reader,
                pipeline=reader.pipeline_view(),
                daemon_state=reader.daemon_state(),
            )
        except Exception:
            _close_reader(request, reader)
            raise

    @app.get("/_hx/daemon-mode", response_class=HTMLResponse)
    def hx_daemon_mode(request: Request) -> HTMLResponse:
        reader = _reader_ctx(request)
        try:
            return _render(
                request,
                "_daemon_mode.html",
                _reader=reader,
                daemon_state=reader.daemon_state(),
            )
        except Exception:
            _close_reader(request, reader)
            raise

    @app.get("/_hx/daemon-active-tick", response_class=HTMLResponse)
    def hx_daemon_active_tick(request: Request) -> HTMLResponse:
        reader = _reader_ctx(request)
        try:
            return _render(
                request,
                "_daemon_active_tick.html",
                _reader=reader,
                daemon_state=reader.daemon_state(),
            )
        except Exception:
            _close_reader(request, reader)
            raise

    @app.get("/_hx/daemon-approvals", response_class=HTMLResponse)
    def hx_daemon_approvals(request: Request) -> HTMLResponse:
        reader = _reader_ctx(request)
        try:
            return _render(
                request,
                "_daemon_approvals.html",
                _reader=reader,
                daemon_state=reader.daemon_state(),
            )
        except Exception:
            _close_reader(request, reader)
            raise

    @app.get("/_hx/daemon-failures", response_class=HTMLResponse)
    def hx_daemon_failures(request: Request) -> HTMLResponse:
        reader = _reader_ctx(request)
        try:
            return _render(
                request,
                "_daemon_failures.html",
                _reader=reader,
                daemon_state=reader.daemon_state(),
            )
        except Exception:
            _close_reader(request, reader)
            raise

    @app.get("/_hx/daemon-history", response_class=HTMLResponse)
    def hx_daemon_history(request: Request) -> HTMLResponse:
        reader = _reader_ctx(request)
        try:
            return _render(
                request,
                "_daemon_history.html",
                _reader=reader,
                daemon_state=reader.daemon_state(),
            )
        except Exception:
            _close_reader(request, reader)
            raise

    @app.get("/_hx/daemon-log/{filename}", response_class=HTMLResponse)
    def hx_daemon_log(request: Request, filename: str) -> HTMLResponse:  # noqa: PLR0913
        # noqa above is preemptive: this body grew because security
        # validation lives inline (no separate helper).
        """Inline tail of a codex spawn log, surfaced from the cockpit.

        Security: ``filename`` is validated against a strict regex
        (only the basenames lab logs actually use) and the resolved
        path is confirmed to live inside ``LAB_LOGS_DIR`` — no
        traversal, no symlink escapes. We deliberately do NOT
        ``send_file`` the raw log: keeping it inside the HTMX layout
        means the rest of the cockpit stays interactive.

        The body is the last ~12 KB. That's enough to capture both
        the final stderr block (root-cause for exit-code failures)
        and the OK/REFUSE summary line (root-cause for         the codex-said-
        no failure mode), without dumping a 2 MB jsonl event stream
        into the DOM.
        """
        # Allow only the canonical lab-log basenames:
        #   <ts>__<skill>__<spawn_id>.log
        # and the optional sibling .last.txt (final-message snapshot).
        if not re.fullmatch(r"[A-Za-z0-9._-]{8,256}\.(log|last\.txt)", filename):
            raise HTTPException(status_code=400, detail="invalid log filename")
        # Re-import on each request so test fixtures that reload
        # ``paths`` (with a tmp repo root) see the override. The cost
        # is negligible: a module attribute lookup, not a re-import.
        from openharness.lab import paths as _paths

        logs_dir = _paths.LAB_LOGS_DIR
        target = (logs_dir / filename).resolve()
        # Defense in depth: confirm the resolved path is inside the
        # logs dir even after symlink resolution (CVE-class concern).
        try:
            target.relative_to(logs_dir.resolve())
        except ValueError:
            raise HTTPException(status_code=400, detail="path escapes lab logs dir")
        if not target.is_file():
            raise HTTPException(status_code=404, detail=f"log {filename} not found")

        max_bytes = 12 * 1024
        with target.open("rb") as fh:
            try:
                fh.seek(-max_bytes, os.SEEK_END)
                truncated = True
            except OSError:
                fh.seek(0)
                truncated = False
            tail_bytes = fh.read()
        tail = tail_bytes.decode("utf-8", errors="replace")
        size = target.stat().st_size

        # The log-tail partial is intentionally chrome-less (no nav,
        # no pending-actions sidebar) so it can be swapped into a
        # disclosure pane inline. We render it directly via the
        # template environment to bypass _render's reader requirement.
        return HTMLResponse(
            templates.get_template("_daemon_log_tail.html").render(
                request=request,
                filename=filename,
                tail=tail,
                truncated=truncated,
                size=size,
                absolute_path=str(target),
            )
        )

    @app.get("/_hx/roadmap-body", response_class=HTMLResponse)
    def hx_roadmap_body(request: Request) -> HTMLResponse:
        reader = _reader_ctx(request)
        try:
            up_next, suggested, done = reader.roadmap()
            return _render(
                request,
                "_roadmap_body.html",
                _reader=reader,
                up_next=up_next,
                suggested=suggested,
                done=done,
                daemon_state=reader.daemon_state(),
            )
        except Exception:
            _close_reader(request, reader)
            raise

    @app.get("/_hx/ideas-body", response_class=HTMLResponse)
    def hx_ideas_body(request: Request) -> HTMLResponse:
        reader = _reader_ctx(request)
        try:
            ideas_list = reader.ideas()
            grouped: dict[str, dict[str | None, list[Any]]] = {}
            for i in ideas_list:
                grouped.setdefault(i.section, {}).setdefault(i.theme, []).append(i)
            return _render(
                request,
                "_ideas_body.html",
                _reader=reader,
                grouped=grouped,
            )
        except Exception:
            _close_reader(request, reader)
            raise

    @app.get("/_hx/components-body", response_class=HTMLResponse)
    def hx_components_body(request: Request) -> HTMLResponse:
        reader = _reader_ctx(request)
        try:
            cat = reader.components()
            perf = reader.components_perf()
            perf_by_id: dict[str, list[Any]] = {}
            for row in perf:
                perf_by_id.setdefault(row.component_id, []).append(row)
            return _render(
                request,
                "_components_body.html",
                _reader=reader,
                catalog=cat,
                perf_by_id=perf_by_id,
            )
        except Exception:
            _close_reader(request, reader)
            raise

    @app.get("/_hx/tree-preview", response_class=HTMLResponse)
    def hx_tree_preview(request: Request, slug: str) -> HTMLResponse:
        # Recompute the TreeDiff for ``slug`` without applying it. Used
        # by the "Preview verdict" buttons on /tree and the experiment
        # detail page so an operator can see exactly what `tree apply`
        # is about to do before clicking through.
        reader = _reader_ctx(request)
        try:
            diff = reader.preview_diff(slug)
            return _render(
                request,
                "_tree_diff.html",
                _reader=reader,
                slug=slug,
                diff=diff,
            )
        except Exception:
            _close_reader(request, reader)
            raise

    @app.get("/_hx/pending-body", response_class=HTMLResponse)
    def hx_pending_body(request: Request) -> HTMLResponse:
        reader = _reader_ctx(request)
        try:
            return _render(
                request,
                "_pending_body.html",
                _reader=reader,
            )
        except Exception:
            _close_reader(request, reader)
            raise

    # ---- JSON endpoints (handy for ad-hoc tooling) ---------------------

    @app.get("/api/pending", response_class=JSONResponse)
    def api_pending() -> dict[str, object]:
        with labdata.LabReader() as r:
            p = r.pending_actions()
            return {
                "total": p.total,
                "suggested": [{"slug": s.slug, "hypothesis": s.hypothesis} for s in p.suggested],
                "auto_proposed": [
                    {"id": i.idea_id, "motivation": i.motivation} for i in p.auto_proposed
                ],
                "misconfig_recent": p.misconfig_recent,
                "failed_spawns_recent": p.failed_spawns_recent,
            }

    @app.get("/api/tree", response_class=JSONResponse)
    def api_tree() -> dict[str, object]:
        from dataclasses import asdict

        with labdata.LabReader() as r:
            t = r.tree()
            return {
                "trunk": {"id": t.trunk_id, "anchor": t.trunk_anchor},
                "branches": [asdict(b) for b in t.branches],
                "rejected": [asdict(b) for b in t.rejected],
                "proposed": [asdict(b) for b in t.proposed],
            }

    # ---- Command runner (Phase 3) --------------------------------------

    @app.post("/api/cmd", response_class=HTMLResponse)
    async def api_cmd(request: Request) -> HTMLResponse:
        form = await request.form()
        raw = {k: str(v) for k, v in form.items()}
        identity = labauth.identify(request)
        auth_err = labauth.check_write(identity)
        if auth_err is not None:
            # 401 when no identity at all (token missing, proxy header
            # missing); 403 when we know who you are but you're not
            # allowed (viewer, unknown email).
            status = 401 if identity.role == "anonymous" and identity.email is None else 403
            return templates.TemplateResponse(
                request,
                "_cmd_result.html",
                {
                    "error": auth_err,
                    "error_kind": "Forbidden" if status == 403 else "Authentication required",
                    "result": None,
                    "identity": identity,
                },
                status_code=status,
            )
        cmd_id = raw.pop("cmd_id", "")
        # Prefer the real authenticated email when we have one, so the
        # audit log records the human, not a generic ``human:webui``
        # placeholder. Form/header overrides still win for ad-hoc
        # impersonation by an admin (rare).
        actor = (
            raw.pop("_actor", "")
            or request.headers.get("X-Lab-Actor")
            or identity.email
            or os.environ.get("LAB_USER")
            or "human:webui"
        )
        if not cmd_id:
            return templates.TemplateResponse(
                request,
                "_cmd_result.html",
                {"error": "missing cmd_id", "result": None},
                status_code=400,
            )
        try:
            result = labcmd.run_command(cmd_id, raw, actor=actor)
        except labcmd.CommandError as e:
            return templates.TemplateResponse(
                request,
                "_cmd_result.html",
                {"error": str(e), "result": None},
                status_code=400,
            )
        response = templates.TemplateResponse(
            request,
            "_cmd_result.html",
            {"error": None, "result": result},
            status_code=200,
        )
        # Only auto-refresh listening containers when the CLI returned 0.
        # A non-zero exit usually means *no state changed* (e.g. unknown
        # slug); refreshing would just hide the error before the operator
        # could read it.
        if result.ok:
            events = labcmd.trigger_events(cmd_id)
            if events:
                # HX-Trigger fires events on the document immediately
                # after HTMX swaps the response. Each event maps to a
                # null payload — listeners only need the event name.
                response.headers["HX-Trigger"] = json.dumps({ev: None for ev in events})
        return response

    @app.get("/_hx/cmd-clear", response_class=HTMLResponse)
    def hx_cmd_clear() -> HTMLResponse:
        return HTMLResponse("")

    # ---- New IA routes (Phase 4 redesign) -------------------------------
    #
    # /runs and /log are the two new top-level surfaces. The /runs/...
    # detail and trial-detail routes proxy to the existing
    # experiment_detail templates so we don't duplicate rendering
    # logic; the route just sets ``nav_active="runs"`` for the
    # highlighted sidebar item.

    @app.get("/runs", response_class=HTMLResponse)
    def runs(request: Request) -> HTMLResponse:
        reader = _reader_ctx(request)
        try:
            return _render(
                request,
                "experiments_list.html",
                _reader=reader,
                nav_active="runs",
                experiments=reader.experiments(limit=200),
                journal=reader.journal(),
                pr_states=reader.pr_states(),
            )
        except Exception:
            _close_reader(request, reader)
            raise

    @app.get("/runs/{instance_id}", response_class=HTMLResponse)
    def runs_detail(request: Request, instance_id: str) -> HTMLResponse:
        # Same render path as /experiments/<id>, but with a nav_active
        # of "runs" so the sidebar reflects the new IA. We deliberately
        # do *not* redirect /experiments/<id> → /runs/<id> so external
        # bookmarks keep working.
        reader = _reader_ctx(request)
        try:
            exp = reader.experiment(instance_id)
            if exp is None:
                if labdata.run_dir_for(instance_id) is None:
                    _close_reader(request, reader)
                    raise HTTPException(404, f"unknown instance {instance_id}")
            legs = reader.legs(instance_id)
            tasks, leg_ids, cells = reader.task_pass_matrix(instance_id)
            verdict = next(
                (d for d in reader.tree_diffs() if d.instance_id == instance_id),
                None,
            )
            journal = reader.journal_entry_for_instance(instance_id)
            clusters = reader.task_clusters_for_instance(instance_id)
            comparisons = reader.comparisons_for_instance(instance_id)
            critic_md = labdata.critic_summary_md(instance_id)
            sum_md = labdata.summary_md(instance_id)
            cluster_deltas = reader.cluster_deltas(instance_id)
            cell_rows = reader.cells_for_instance(instance_id)
            pr_for_run = next(
                (pr for pr in reader.pr_states() if pr.instance_id == instance_id),
                None,
            )
            return _render(
                request,
                "experiment_detail.html",
                _reader=reader,
                nav_active="runs",
                instance_id=instance_id,
                experiment=exp,
                legs=legs,
                tasks=tasks,
                leg_ids=leg_ids,
                cells=cells,
                verdict=verdict,
                journal=journal,
                clusters=clusters,
                comparisons=comparisons,
                critic_md=critic_md,
                summary_md=sum_md,
                cluster_deltas=cluster_deltas,
                cell_rows=cell_rows,
                pr_for_run=pr_for_run,
            )
        except HTTPException:
            raise
        except Exception:
            _close_reader(request, reader)
            raise

    @app.get("/runs/{instance_id}/trials/{trial_id}", response_class=HTMLResponse)
    def runs_trial(request: Request, instance_id: str, trial_id: str) -> HTMLResponse:
        reader = _reader_ctx(request)
        try:
            trial = reader.trial(instance_id, trial_id)
            if trial is None:
                _close_reader(request, reader)
                raise HTTPException(404, f"unknown trial {trial_id} in {instance_id}")
            return _render(
                request,
                "trial_detail.html",
                _reader=reader,
                nav_active="runs",
                instance_id=instance_id,
                trial=trial,
            )
        except HTTPException:
            raise
        except Exception:
            _close_reader(request, reader)
            raise

    @app.get("/log", response_class=HTMLResponse)
    def log_page(
        request: Request,
        kind: str | None = None,
        actor: str | None = None,
        slug: str | None = None,
        limit: int = 200,
    ) -> HTMLResponse:
        # Unified activity log: web_commands.jsonl + tick history +
        # spawn finishes + verdicts + trunk swaps. Filters narrow by
        # row kind, actor, or slug; the fields the operator clicks on
        # are pre-computed into ``ActivityLogEntry.href``.
        reader = _reader_ctx(request)
        try:
            kinds: tuple[str, ...] | None
            kinds = (kind,) if kind else None
            rows = reader.activity_log(limit=max(50, min(limit, 1000)), kinds=kinds)
            if actor:
                rows = [r for r in rows if r.actor == actor]
            if slug:
                rows = [r for r in rows if r.slug == slug]
            return _render(
                request,
                "log.html",
                _reader=reader,
                nav_active="log",
                rows=rows,
                filter_kind=kind or "",
                filter_actor=actor or "",
                filter_slug=slug or "",
                limit=limit,
            )
        except Exception:
            _close_reader(request, reader)
            raise

    # ---- HTMX partials for the new home page ---------------------------

    @app.get("/_hx/idle-reason", response_class=HTMLResponse)
    def hx_idle_reason(request: Request) -> HTMLResponse:
        reader = _reader_ctx(request)
        try:
            return _render(
                request,
                "_idle_reason.html",
                _reader=reader,
                idle_reason=reader.idle_reason(),
            )
        except Exception:
            _close_reader(request, reader)
            raise

    @app.get("/_hx/you-owe", response_class=HTMLResponse)
    def hx_you_owe(request: Request) -> HTMLResponse:
        reader = _reader_ctx(request)
        try:
            return _render(
                request,
                "_you_owe.html",
                _reader=reader,
                pending=reader.pending_actions(),
            )
        except Exception:
            _close_reader(request, reader)
            raise

    @app.get("/audit", response_class=HTMLResponse)
    def audit_page(
        request: Request,
        cmd: str | None = None,
        actor: str | None = None,
        ok: str | None = None,
        limit: int = 200,
    ) -> HTMLResponse:
        reader = _reader_ctx(request)
        try:
            # Pull a wider window than we'll render so the summary tally
            # at the top reflects the recent history regardless of which
            # filters are applied.
            sample = labcmd.audit_tail(n=max(limit * 3, 500))
            # Filter for the rendered list.
            rows = sample
            if cmd:
                rows = [r for r in rows if r.get("cmd_id") == cmd]
            if actor:
                rows = [r for r in rows if r.get("actor") == actor]
            if ok in {"yes", "no"}:
                want_ok = ok == "yes"
                rows = [r for r in rows if (r.get("exit_code") == 0) == want_ok]
            rows = rows[:limit]
            # Summary across the unfiltered sample so operators see the
            # rate of failure even when narrowing the view.
            cmd_counts: dict[str, int] = {}
            actor_counts: dict[str, int] = {}
            ok_count = fail_count = 0
            for r in sample:
                cid = str(r.get("cmd_id", "?"))
                act = str(r.get("actor", "?"))
                cmd_counts[cid] = cmd_counts.get(cid, 0) + 1
                actor_counts[act] = actor_counts.get(act, 0) + 1
                if r.get("exit_code") == 0:
                    ok_count += 1
                else:
                    fail_count += 1
            return _render(
                request,
                "audit.html",
                _reader=reader,
                nav_active="audit",
                rows=rows,
                log_path=str(labcmd.audit_log_path()),
                total_in_sample=len(sample),
                ok_count=ok_count,
                fail_count=fail_count,
                cmd_counts=sorted(cmd_counts.items(), key=lambda kv: -kv[1]),
                actor_counts=sorted(actor_counts.items(), key=lambda kv: -kv[1]),
                filter_cmd=cmd or "",
                filter_actor=actor or "",
                filter_ok=ok or "",
                limit=limit,
            )
        except Exception:
            _close_reader(request, reader)
            raise

    return app


# ---------------------------------------------------------------------------
# Template helpers
# ---------------------------------------------------------------------------


def _fmt_dt(v: object) -> str:
    if v is None:
        return "—"
    if isinstance(v, datetime):
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        # Render in the local-ish UTC for now; client-side conversion is a
        # nice Phase 2.
        return v.strftime("%Y-%m-%d %H:%M:%SZ")
    return str(v)


def _fmt_delta(v: object) -> str:
    if v is None:
        return "—"
    return f"{float(v):+.1f}"  # type: ignore[arg-type]


def _fmt_elapsed(v: object) -> str:
    """Render "X seconds ago" / "Xm Ys ago" for a past datetime.

    Used by the active-tick panel to show how long the running tick
    has been alive. Robust to naive datetimes (assumed UTC) and to
    None (returns "—"), so a half-populated daemon_state doesn't
    500 the page render.
    """
    if v is None:
        return "—"
    if not isinstance(v, datetime):
        return str(v)
    if v.tzinfo is None:
        v = v.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - v
    secs = int(delta.total_seconds())
    if secs < 0:
        # Clock skew or a future timestamp (rare); avoid negative
        # signs in the UI.
        return "just now"
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m {secs % 60:02d}s"
    h, rem = divmod(secs, 3600)
    return f"{h}h {rem // 60:02d}m"


def _fmt_money(v: object) -> str:
    if v is None:
        return "—"
    try:
        return f"${float(v):,.2f}"  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "—"


def _fmt_int(v: object) -> str:
    if v is None:
        return "—"
    try:
        return f"{int(v):,}"  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "—"


def _pct_color(pct: float | None) -> str:
    if pct is None:
        return "bg-slate-200 text-slate-700"
    if pct >= 50:
        return "bg-emerald-100 text-emerald-800"
    if pct >= 25:
        return "bg-amber-100 text-amber-800"
    return "bg-rose-100 text-rose-800"


def _status_color(status: str | None) -> str:
    s = (status or "").lower()
    if s in ("validated",):
        return "bg-emerald-100 text-emerald-800"
    if s in ("branch",):
        return "bg-sky-100 text-sky-800"
    if s in ("experimental",):
        return "bg-amber-100 text-amber-800"
    if s in ("proposed",):
        return "bg-slate-100 text-slate-700"
    if s in ("rejected", "superseded"):
        return "bg-rose-100 text-rose-800"
    return "bg-slate-100 text-slate-700"


def _verdict_color(kind: str | None, applied: bool = True) -> str:
    k = (kind or "").lower()
    if k == "graduate":
        return (
            "border-amber-400 bg-amber-50 text-amber-900"
            if not applied
            else "border-amber-300 bg-amber-100 text-amber-900"
        )
    if k == "add_branch":
        return "border-emerald-300 bg-emerald-50 text-emerald-900"
    if k == "reject":
        return "border-rose-300 bg-rose-50 text-rose-900"
    if k == "no_op":
        return "border-slate-300 bg-slate-50 text-slate-700"
    return "border-slate-200 bg-slate-50 text-slate-700"
