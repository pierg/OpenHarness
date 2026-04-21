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
    /spawns              codex spawn audit log
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
    templates.env.globals["fmt_money"] = _fmt_money
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
        reader = _reader_ctx(request)
        try:
            up_next, suggested, _done = reader.roadmap()
            recent_exp = reader.experiments(limit=5)
            recent_spawns = reader.recent_spawns(limit=10)
            return _render(
                request, "home.html",
                _reader=reader,
                nav_active="home",
                daemon=reader.daemon_status(),
                up_next=up_next[:5],
                suggested=suggested[:5],
                recent_experiments=recent_exp,
                recent_spawns=recent_spawns,
                db_info=reader.db_info(),
            )
        except Exception:
            _close_reader(request, reader)
            raise

    @app.get("/pending", response_class=HTMLResponse)
    def pending(request: Request) -> HTMLResponse:
        reader = _reader_ctx(request)
        return _render(request, "pending.html", _reader=reader, nav_active="pending")

    @app.get("/tree", response_class=HTMLResponse)
    def tree(request: Request) -> HTMLResponse:
        reader = _reader_ctx(request)
        try:
            return _render(
                request, "tree.html",
                _reader=reader, nav_active="tree",
                snapshot=reader.tree(),
                trunk_history=reader.trunk_history(limit=20),
                staged_graduates=reader.tree_diffs(applied=False, kind="graduate"),
                pending_eval=reader.experiments_without_diff(limit=10),
                recent_diffs=reader.tree_diffs(limit=10),
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
                request, "components.html",
                _reader=reader, nav_active="components",
                catalog=cat, perf_by_id=perf_by_id,
            )
        except Exception:
            _close_reader(request, reader)
            raise

    @app.get("/experiments", response_class=HTMLResponse)
    def experiments(request: Request) -> HTMLResponse:
        reader = _reader_ctx(request)
        try:
            return _render(
                request, "experiments_list.html",
                _reader=reader, nav_active="experiments",
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
            return _render(
                request, "experiment_detail.html",
                _reader=reader, nav_active="experiments",
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
                request, "ideas.html",
                _reader=reader, nav_active="ideas",
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
                request, "roadmap.html",
                _reader=reader, nav_active="roadmap",
                up_next=up_next, suggested=suggested, done=done,
            )
        except Exception:
            _close_reader(request, reader)
            raise

    @app.get("/spawns", response_class=HTMLResponse)
    def spawns(request: Request, limit: int = 100) -> HTMLResponse:
        reader = _reader_ctx(request)
        try:
            return _render(
                request, "spawns.html",
                _reader=reader, nav_active="spawns",
                spawns=reader.recent_spawns(limit=limit),
                logs=list(labdata.list_log_files(limit=50)),
                limit=limit,
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
                request, "trial_detail.html",
                _reader=reader, nav_active="experiments",
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
                request, "tasks_list.html",
                _reader=reader, nav_active="tasks",
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
            task_name = features.task_name if features else (board[0].leg_id if board else checksum[:16])
            if board:
                # Pull the canonical task name from any trial row.
                trials_with_checksum = [t for t in reader.trials(board[0].instance_id)
                                        if t.task_checksum == checksum]
                if trials_with_checksum:
                    task_name = trials_with_checksum[0].task_name
            comparisons = reader.comparisons_for_task(task_name) if task_name else []
            return _render(
                request, "task_detail.html",
                _reader=reader, nav_active="tasks",
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
            cell_lookup: dict[tuple[str, str], Any] = {(r.component_id, r.task_cluster): r for r in rows}
            return _render(
                request, "components_perf.html",
                _reader=reader, nav_active="components",
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
                request, "component_detail.html",
                _reader=reader, nav_active="components",
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
                request, "daemon.html",
                _reader=reader, nav_active="daemon",
                status=status, tail=tail,
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
                request, "_drawer.html",
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
                request, "_log_tail.html",
                _reader=reader,
                status=status, tail=tail,
            )
        except Exception:
            _close_reader(request, reader)
            raise

    @app.get("/_hx/daemon-status", response_class=HTMLResponse)
    def hx_daemon_status(request: Request) -> HTMLResponse:
        reader = _reader_ctx(request)
        try:
            return _render(
                request, "_daemon_status.html",
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
                request, "_home_daemon.html",
                _reader=reader,
                daemon=reader.daemon_status(),
            )
        except Exception:
            _close_reader(request, reader)
            raise

    @app.get("/_hx/roadmap-body", response_class=HTMLResponse)
    def hx_roadmap_body(request: Request) -> HTMLResponse:
        reader = _reader_ctx(request)
        try:
            up_next, suggested, done = reader.roadmap()
            return _render(
                request, "_roadmap_body.html",
                _reader=reader,
                up_next=up_next, suggested=suggested, done=done,
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
                request, "_ideas_body.html",
                _reader=reader,
                grouped=grouped,
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
                request, "_tree_diff.html",
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
                request, "_pending_body.html",
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
                "staged_graduates": [
                    {"slug": d.slug, "instance_id": d.instance_id,
                     "target_id": d.target_id} for d in p.staged_graduates
                ],
                "suggested": [{"slug": s.slug, "hypothesis": s.hypothesis}
                              for s in p.suggested],
                "auto_proposed": [{"id": i.idea_id, "motivation": i.motivation}
                                  for i in p.auto_proposed],
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
                request, "_cmd_result.html",
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
                request, "_cmd_result.html",
                {"error": "missing cmd_id", "result": None},
                status_code=400,
            )
        try:
            result = labcmd.run_command(cmd_id, raw, actor=actor)
        except labcmd.CommandError as e:
            return templates.TemplateResponse(
                request, "_cmd_result.html",
                {"error": str(e), "result": None},
                status_code=400,
            )
        response = templates.TemplateResponse(
            request, "_cmd_result.html",
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
                response.headers["HX-Trigger"] = json.dumps(
                    {ev: None for ev in events}
                )
        return response

    @app.get("/_hx/cmd-clear", response_class=HTMLResponse)
    def hx_cmd_clear() -> HTMLResponse:
        return HTMLResponse("")

    @app.get("/audit", response_class=HTMLResponse)
    def audit_page(request: Request) -> HTMLResponse:
        reader = _reader_ctx(request)
        try:
            return _render(
                request, "audit.html",
                _reader=reader, nav_active="audit",
                rows=labcmd.audit_tail(n=200),
                log_path=str(labcmd.audit_log_path()),
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


def _fmt_money(v: object) -> str:
    if v is None:
        return "—"
    try:
        return f"${float(v):,.2f}"  # type: ignore[arg-type]
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
        return "border-amber-400 bg-amber-50 text-amber-900" if not applied \
            else "border-amber-300 bg-amber-100 text-amber-900"
    if k == "add_branch":
        return "border-emerald-300 bg-emerald-50 text-emerald-900"
    if k == "reject":
        return "border-rose-300 bg-rose-50 text-rose-900"
    if k == "no_op":
        return "border-slate-300 bg-slate-50 text-slate-700"
    return "border-slate-200 bg-slate-50 text-slate-700"
