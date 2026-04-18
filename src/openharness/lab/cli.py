"""`uv run lab ...` — single Typer entry point for the lab pipeline.

Two audiences, one entry point:

- **Critic skills** call read/write commands against the DB:
  `uv run lab insert-critique <trial_id> --json -`
  `uv run lab query-trials --task <name>`
  `uv run lab insert-comparison <instance_id> <task> --json -`
  `uv run lab insert-task-features <checksum> --json -`
  `uv run lab append-followup-idea <slug> --motivation ... --sketch ... --source ...`

- **The five existing lab/* skills** call deterministic markdown helpers:
  `uv run lab idea move <id> trying`
  `uv run lab idea append <id> --theme runtime --motivation ... --sketch ...`
  `uv run lab experiments stub <slug> --hypothesis ... --variant ...`
  `uv run lab experiments fill <slug> --run-path ... --from-summary ...`
  `uv run lab roadmap add <slug> ...`
  `uv run lab roadmap done <slug> --ran ... --outcome ...`

The orchestrator (Phase 2) calls this same CLI rather than importing
the package directly, so the contract is identical for skills, humans,
and the daemon.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from openharness.lab import db as labdb
from openharness.lab import ingest as labingest
from openharness.lab import lab_docs
from openharness.lab.paths import (
    LAB_DB_PATH,
    LAB_LOGS_DIR,
    LAB_RUNS_ROOT,
    ensure_lab_runs_dir,
)

console = Console()
err_console = Console(stderr=True)

app = typer.Typer(
    add_completion=False,
    help="Lab pipeline CLI — DB I/O + deterministic markdown helpers.",
    no_args_is_help=True,
)
idea_app = typer.Typer(no_args_is_help=True, help="Edit lab/ideas.md.")
exp_app = typer.Typer(no_args_is_help=True, help="Edit lab/experiments.md.")
roadmap_app = typer.Typer(no_args_is_help=True, help="Edit lab/roadmap.md.")
daemon_app = typer.Typer(no_args_is_help=True, help="Orchestrator daemon (Phase 2).")
app.add_typer(idea_app, name="idea")
app.add_typer(exp_app, name="experiments")
app.add_typer(roadmap_app, name="roadmap")
app.add_typer(daemon_app, name="daemon")


# ===== infrastructure =======================================================


@app.command()
def init() -> None:
    """Create the lab DB and apply migrations (no-op if up to date)."""
    ensure_lab_runs_dir()
    with labdb.writer() as conn:
        n_trials = conn.execute("SELECT count(*) FROM trials").fetchone()[0]
    typer.echo(f"DB ready at {LAB_DB_PATH} (trials: {n_trials}).")


@app.command()
def info() -> None:
    """Show lab paths + DB stats."""
    table = Table(title="Lab paths", show_header=False)
    table.add_row("DB", str(LAB_DB_PATH))
    table.add_row("Logs", str(LAB_LOGS_DIR))
    table.add_row("Runs root", str(LAB_RUNS_ROOT))
    console.print(table)
    if not LAB_DB_PATH.exists():
        err_console.print("[yellow]No lab DB yet — run `uv run lab init`.[/yellow]")
        raise typer.Exit(0)
    with labdb.reader() as conn:
        for tbl in ("experiments", "legs", "trials", "trial_critiques",
                    "comparisons", "task_features", "components_perf",
                    "misconfigurations", "spawns"):
            (n,) = conn.execute(f"SELECT count(*) FROM {tbl}").fetchone()
            console.print(f"  [cyan]{tbl}[/cyan]: {n}")


# ===== ingest ===============================================================


@app.command()
def ingest(
    run_dirs: list[Path] = typer.Argument(
        ..., help="Experiment run directories under runs/experiments/."
    ),
) -> None:
    """Backfill or refresh trial rows from one or more run directories."""
    ensure_lab_runs_dir()
    summaries = labingest.ingest_runs(run_dirs)
    for s in summaries:
        typer.echo(
            f"{s.instance_id}: legs={s.legs_inserted} trials={s.trials_inserted} "
            f"skipped={s.trials_skipped} misconfig={s.misconfigurations} "
            f"(from {s.run_dir})"
        )


# ===== queries (read-only) =================================================


@app.command("query-trials")
def query_trials(
    task: Optional[str] = typer.Option(None, "--task", help="Filter by task_name."),
    instance: Optional[str] = typer.Option(None, "--instance", help="Filter by instance_id."),
    leg: Optional[str] = typer.Option(None, "--leg", help="Filter by leg_id."),
    passed: Optional[bool] = typer.Option(None, "--passed/--failed", help="Filter by pass/fail."),
    needs_critique: bool = typer.Option(
        False, "--needs-critique", help="Only trials lacking a row in trial_critiques."
    ),
    limit: int = typer.Option(100, "--limit"),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON (one obj per row)."),
) -> None:
    where: list[str] = []
    params: list[object] = []
    if task:
        where.append("t.task_name = ?")
        params.append(task)
    if instance:
        where.append("t.instance_id = ?")
        params.append(instance)
    if leg:
        where.append("t.leg_id = ?")
        params.append(leg)
    if passed is not None:
        where.append("t.passed = ?")
        params.append(passed)
    if needs_critique:
        where.append("c.trial_id IS NULL")
    sql = (
        "SELECT t.trial_id, t.instance_id, t.leg_id, t.task_name, t.passed, "
        "  t.score, t.cost_usd, t.duration_sec, t.trial_dir "
        "FROM trials t LEFT JOIN trial_critiques c USING (trial_id) "
        + ("WHERE " + " AND ".join(where) if where else "")
        + " ORDER BY t.task_name, t.leg_id LIMIT ?"
    )
    params.append(limit)
    with labdb.reader() as conn:
        rows = conn.execute(sql, params).fetchall()
        cols = [d[0] for d in conn.description]
    if json_out:
        for row in rows:
            typer.echo(json.dumps(dict(zip(cols, row, strict=True)), default=str))
        return
    table = Table(*cols)
    for row in rows:
        table.add_row(*[str(c) for c in row])
    console.print(table)
    console.print(f"[dim]{len(rows)} row(s)[/dim]")


@app.command("query")
def query(sql: str = typer.Argument(..., help="A read-only SQL query.")) -> None:
    """Run an ad-hoc SQL query (read-only)."""
    with labdb.reader() as conn:
        rows = conn.execute(sql).fetchall()
        cols = [d[0] for d in conn.description]
    table = Table(*cols)
    for row in rows:
        table.add_row(*[str(c) for c in row])
    console.print(table)
    console.print(f"[dim]{len(rows)} row(s)[/dim]")


# ===== writers (used by critic skills) =====================================


def _read_json_arg(value: str | None) -> dict | list:
    if value is None or value == "-":
        return json.load(sys.stdin)
    p = Path(value)
    if p.is_file():
        return json.loads(p.read_text())
    return json.loads(value)


@app.command("insert-critique")
def insert_critique(
    trial_id: str = typer.Argument(..., help="Trial id this critique is about."),
    json_in: str = typer.Option(
        "-", "--json", help="JSON file path, '-' for stdin, or inline JSON string."
    ),
    critic_model: Optional[str] = typer.Option(None, "--critic-model"),
) -> None:
    """Insert / replace a row in trial_critiques."""
    payload = _read_json_arg(json_in)
    if not isinstance(payload, dict):
        err_console.print("[red]critique payload must be a JSON object[/red]")
        raise typer.Exit(2)
    schema_version = int(payload.get("schema_version", 1))
    with labdb.writer() as conn:
        conn.execute(
            """
            INSERT INTO trial_critiques (
                trial_id, schema_version, task_summary, agent_strategy, key_actions,
                outcome, root_cause, success_factor, anti_patterns, components_active,
                task_features, surprising_observations, confidence, critic_model,
                extra, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT (trial_id) DO UPDATE SET
                schema_version = EXCLUDED.schema_version,
                task_summary = EXCLUDED.task_summary,
                agent_strategy = EXCLUDED.agent_strategy,
                key_actions = EXCLUDED.key_actions,
                outcome = EXCLUDED.outcome,
                root_cause = EXCLUDED.root_cause,
                success_factor = EXCLUDED.success_factor,
                anti_patterns = EXCLUDED.anti_patterns,
                components_active = EXCLUDED.components_active,
                task_features = EXCLUDED.task_features,
                surprising_observations = EXCLUDED.surprising_observations,
                confidence = EXCLUDED.confidence,
                critic_model = EXCLUDED.critic_model,
                extra = EXCLUDED.extra,
                created_at = EXCLUDED.created_at
            """,
            [
                trial_id,
                schema_version,
                payload.get("task_summary"),
                payload.get("agent_strategy"),
                json.dumps(payload.get("key_actions") or []),
                payload.get("outcome"),
                payload.get("root_cause"),
                payload.get("success_factor"),
                json.dumps(payload.get("anti_patterns") or []),
                json.dumps(payload.get("components_active") or []),
                json.dumps(payload.get("task_features") or []),
                json.dumps(payload.get("surprising_observations") or []),
                payload.get("confidence"),
                critic_model or payload.get("critic_model"),
                json.dumps(payload.get("extra") or {}),
                datetime.now(timezone.utc),
            ],
        )
    typer.echo(f"insert-critique ok: {trial_id}")


@app.command("insert-comparison")
def insert_comparison(
    instance_id: str = typer.Argument(...),
    task_name: str = typer.Argument(...),
    json_in: str = typer.Option("-", "--json"),
    critic_model: Optional[str] = typer.Option(None, "--critic-model"),
) -> None:
    payload = _read_json_arg(json_in)
    if not isinstance(payload, dict):
        err_console.print("[red]comparison payload must be a JSON object[/red]")
        raise typer.Exit(2)
    with labdb.writer() as conn:
        conn.execute(
            """
            INSERT INTO comparisons (
                instance_id, task_name, winning_leg, runner_up_leg, delta_score,
                why, evidence, legs_compared, critic_model, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT (instance_id, task_name) DO UPDATE SET
                winning_leg = EXCLUDED.winning_leg,
                runner_up_leg = EXCLUDED.runner_up_leg,
                delta_score = EXCLUDED.delta_score,
                why = EXCLUDED.why,
                evidence = EXCLUDED.evidence,
                legs_compared = EXCLUDED.legs_compared,
                critic_model = EXCLUDED.critic_model,
                created_at = EXCLUDED.created_at
            """,
            [
                instance_id,
                task_name,
                payload.get("winning_leg"),
                payload.get("runner_up_leg"),
                payload.get("delta_score"),
                payload.get("why"),
                json.dumps(payload.get("evidence") or {}),
                json.dumps(payload.get("legs_compared") or []),
                critic_model or payload.get("critic_model"),
                datetime.now(timezone.utc),
            ],
        )
    typer.echo(f"insert-comparison ok: {instance_id}/{task_name}")


@app.command("insert-task-features")
def insert_task_features(
    task_checksum: str = typer.Argument(...),
    json_in: str = typer.Option("-", "--json"),
    extracted_by: Optional[str] = typer.Option(None, "--extracted-by"),
) -> None:
    payload = _read_json_arg(json_in)
    if not isinstance(payload, dict):
        err_console.print("[red]task-features payload must be a JSON object[/red]")
        raise typer.Exit(2)
    with labdb.writer() as conn:
        conn.execute(
            """
            INSERT INTO task_features (
                task_checksum, task_name, category, required_tools, env_complexity,
                output_shape, keywords, extra, extracted_by, extracted_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT (task_checksum) DO UPDATE SET
                task_name = EXCLUDED.task_name,
                category = EXCLUDED.category,
                required_tools = EXCLUDED.required_tools,
                env_complexity = EXCLUDED.env_complexity,
                output_shape = EXCLUDED.output_shape,
                keywords = EXCLUDED.keywords,
                extra = EXCLUDED.extra,
                extracted_by = EXCLUDED.extracted_by,
                extracted_at = EXCLUDED.extracted_at
            """,
            [
                task_checksum,
                payload.get("task_name"),
                payload.get("category"),
                json.dumps(payload.get("required_tools") or []),
                payload.get("env_complexity"),
                payload.get("output_shape"),
                json.dumps(payload.get("keywords") or []),
                json.dumps(payload.get("extra") or {}),
                extracted_by or payload.get("extracted_by"),
                datetime.now(timezone.utc),
            ],
        )
    typer.echo(f"insert-task-features ok: {task_checksum[:12]}…")


@app.command("upsert-component-perf")
def upsert_component_perf(
    component_id: str = typer.Argument(...),
    task_cluster: str = typer.Argument(...),
    json_in: str = typer.Option("-", "--json"),
) -> None:
    payload = _read_json_arg(json_in)
    if not isinstance(payload, dict):
        err_console.print("[red]component-perf payload must be a JSON object[/red]")
        raise typer.Exit(2)
    with labdb.writer() as conn:
        conn.execute(
            """
            INSERT INTO components_perf (
                component_id, task_cluster, n_trials, win_rate, cost_delta_pct,
                supporting_experiments, notes, updated_at
            ) VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT (component_id, task_cluster) DO UPDATE SET
                n_trials = EXCLUDED.n_trials,
                win_rate = EXCLUDED.win_rate,
                cost_delta_pct = EXCLUDED.cost_delta_pct,
                supporting_experiments = EXCLUDED.supporting_experiments,
                notes = EXCLUDED.notes,
                updated_at = EXCLUDED.updated_at
            """,
            [
                component_id,
                task_cluster,
                int(payload.get("n_trials", 0)),
                payload.get("win_rate"),
                payload.get("cost_delta_pct"),
                json.dumps(payload.get("supporting_experiments") or []),
                payload.get("notes"),
                datetime.now(timezone.utc),
            ],
        )
    typer.echo(f"upsert-component-perf ok: {component_id}/{task_cluster}")


# ===== ideas / experiments / roadmap markdown helpers ======================


@idea_app.command("move")
def cmd_idea_move(
    idea_id: str,
    target: str = typer.Argument(..., help="Section: proposed | trying | graduated | rejected"),
    cross_ref: Optional[str] = typer.Option(
        None, "--cross-ref", help="Bullet to append to the moved entry."
    ),
    target_theme: Optional[str] = typer.Option(
        None, "--theme",
        help="Only used when target == proposed (rare).",
    ),
) -> None:
    section = target.strip().capitalize()
    if section == "Up next":
        section = "Up next"
    lab_docs.move_idea(
        idea_id=idea_id,
        target_section=section,
        cross_ref_bullet=cross_ref,
        target_theme=target_theme,
    )
    typer.echo(f"moved idea {idea_id!r} → ## {section}")


@idea_app.command("append")
def cmd_idea_append(
    idea_id: str,
    theme: str = typer.Option(..., "--theme",
                              help="Architecture | Runtime | Tools | Memory"),
    motivation: str = typer.Option(..., "--motivation"),
    sketch: str = typer.Option(..., "--sketch"),
) -> None:
    lab_docs.append_idea(
        idea_id=idea_id, theme=theme, motivation=motivation, sketch=sketch
    )
    typer.echo(f"appended idea {idea_id!r} under ## Proposed > {theme}")


@app.command("append-followup-idea")
def cmd_append_followup_idea(
    idea_id: str,
    motivation: str = typer.Option(..., "--motivation"),
    sketch: str = typer.Option(..., "--sketch"),
    source: str = typer.Option(..., "--source",
                               help="e.g. 'cross-experiment-critic@2026-04-18'"),
) -> None:
    """Append a follow-up to `## Auto-proposed` (cross-experiment-critic)."""
    lab_docs.append_auto_proposed_idea(
        idea_id=idea_id, motivation=motivation, sketch=sketch, source=source
    )
    typer.echo(f"appended auto-proposed {idea_id!r}")


@exp_app.command("stub")
def cmd_exp_stub(
    slug: str,
    hypothesis: str = typer.Option(..., "--hypothesis"),
    variant: str = typer.Option(..., "--variant"),
) -> None:
    lab_docs.stub_experiment(slug=slug, hypothesis=hypothesis, variant=variant)
    typer.echo(f"stubbed experiment {slug!r}")


@exp_app.command("fill")
def cmd_exp_fill(
    slug: str,
    run_path: str = typer.Option(..., "--run-path"),
    from_summary: Path = typer.Option(..., "--from-summary",
                                      help="Path to runs/.../results/summary.md"),
    note: list[str] = typer.Option([], "--note", help="Repeat for each note bullet."),
    decision: str = typer.Option(..., "--decision"),
) -> None:
    table_md = lab_docs.render_results_table_from_summary(from_summary.read_text())
    lab_docs.fill_experiment_results(
        slug=slug,
        run_path=run_path,
        results_table=table_md,
        notes=note,
        decision=decision,
    )
    typer.echo(f"filled experiment {slug!r}")


@roadmap_app.command("add")
def cmd_roadmap_add(
    slug: str,
    idea_id: Optional[str] = typer.Option(None, "--idea"),
    hypothesis: str = typer.Option(..., "--hypothesis"),
    plan: str = typer.Option(..., "--plan"),
    depends_on: Optional[str] = typer.Option(None, "--depends-on"),
    cost: Optional[str] = typer.Option(None, "--cost"),
) -> None:
    lab_docs.add_roadmap_entry(
        slug=slug,
        idea_id=idea_id,
        hypothesis=hypothesis,
        plan=plan,
        depends_on=depends_on,
        cost=cost,
    )
    typer.echo(f"queued roadmap entry {slug!r}")


@roadmap_app.command("done")
def cmd_roadmap_done(
    slug: str,
    ran: str = typer.Option(..., "--ran",
                            help="Markdown link to experiments.md entry."),
    outcome: str = typer.Option(..., "--outcome"),
) -> None:
    lab_docs.move_roadmap_entry_to_done(slug=slug, ran_link=ran, outcome=outcome)
    typer.echo(f"moved roadmap entry {slug!r} → ## Done")


# ===== dashboard launcher ===================================================


@app.command()
def dashboard(
    port: int = typer.Option(8501, "--port"),
    host: str = typer.Option("127.0.0.1", "--host"),
) -> None:
    """Launch the Streamlit dashboard (read-only)."""
    import subprocess

    app_path = Path(__file__).resolve().parents[3] / "lab" / "dashboard" / "app.py"
    if not app_path.is_file():
        err_console.print(f"[red]Dashboard app not found at {app_path}[/red]")
        raise typer.Exit(1)
    cmd = [
        "streamlit", "run", str(app_path),
        "--server.port", str(port),
        "--server.address", host,
        "--browser.gatherUsageStats", "false",
    ]
    raise typer.Exit(subprocess.call(cmd))


# ===== daemon ==============================================================


@daemon_app.command("status")
def daemon_status() -> None:
    """Show whether the orchestrator daemon is running."""
    from openharness.lab.runner import status as _status

    info = _status()
    if not info.get("running"):
        if info.get("lock_corrupted"):
            typer.echo("orchestrator lock present but unreadable")
            raise typer.Exit(1)
        typer.echo("orchestrator: not running")
        return
    typer.echo(f"orchestrator: running (pid={info.get('pid')})")
    typer.echo(json.dumps(info.get("lock", {}), indent=2, default=str))


@daemon_app.command("start")
def daemon_start(
    foreground: bool = typer.Option(
        True, "--foreground/--background",
        help="--background spawns a detached process via tmux if available, "
             "otherwise nohup. Default --foreground attaches to the terminal.",
    ),
    once: bool = typer.Option(
        False, "--once",
        help="Run a single roadmap entry and exit. Useful for smoke tests.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Walk the roadmap but skip codex spawns.",
    ),
) -> None:
    """Start the orchestrator daemon."""
    from openharness.lab.runner import start as _start
    from openharness.lab.runner import status as _status

    info = _status()
    if info.get("running"):
        err_console.print(
            f"[red]orchestrator already running (pid={info.get('pid')})."
            " stop it first or run --once.[/red]"
        )
        raise typer.Exit(1)

    if foreground:
        _start(foreground=True, once=once, dry_run=dry_run)
        return

    import shutil as _shutil
    import subprocess as _sub

    args = ["uv", "run", "lab", "daemon", "start", "--foreground"]
    if once:
        args.append("--once")
    if dry_run:
        args.append("--dry-run")

    if _shutil.which("tmux"):
        session = "openharness-lab"
        _sub.run(["tmux", "kill-session", "-t", session],
                 check=False, stdout=_sub.DEVNULL, stderr=_sub.DEVNULL)
        _sub.run(["tmux", "new-session", "-d", "-s", session,
                  " ".join(args)], check=True)
        typer.echo(f"orchestrator started in tmux session {session!r}")
        typer.echo(f"attach: `tmux attach -t {session}` or `uv run lab daemon attach`")
        return

    log_path = LAB_LOGS_DIR / "orchestrator.out"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "ab") as fh:
        _sub.Popen(
            args, stdout=fh, stderr=fh, stdin=_sub.DEVNULL,
            start_new_session=True,
        )
    typer.echo(f"orchestrator detached; logs: {log_path}")


@daemon_app.command("stop")
def daemon_stop() -> None:
    """SIGTERM the recorded orchestrator pid."""
    from openharness.lab.runner import stop as _stop

    _stop()


@daemon_app.command("attach")
def daemon_attach() -> None:
    """Attach to the tmux session if `daemon start --background` used tmux."""
    import shutil as _shutil
    import subprocess as _sub

    if not _shutil.which("tmux"):
        err_console.print("[red]tmux not installed[/red]")
        raise typer.Exit(1)
    raise typer.Exit(_sub.call(["tmux", "attach", "-t", "openharness-lab"]))


def main() -> None:  # pragma: no cover - thin wrapper
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
