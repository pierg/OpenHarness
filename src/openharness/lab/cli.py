"""`uv run lab ...` — single Typer entry point for the lab pipeline.

Two audiences, one entry point:

- **Critic skills** write their outputs to files (single source of
  truth) via thin filesystem helpers:
  `uv run lab write-trial-critique <trial_dir> --json -`
  `uv run lab write-comparison <run_dir> <task_name> --json -`
  `uv run lab write-experiment-critique <run_dir> --json -`
  `uv run lab write-task-features <task_checksum> --json -`
  `uv run lab write-component-perf <id> <cluster> --json -`
  `uv run lab write-cross-experiment <spawn_id> --json -`
  `uv run lab append-followup-idea <slug> --motivation ... --sketch ... --source ...`

  The DB tables (`trial_critiques`, `comparisons`, `task_features`,
  …) are derived caches; refresh them with
  `uv run lab ingest-critiques`.

- **The five existing lab/* skills** call deterministic markdown helpers:
  `uv run lab idea move <id> trying`
  `uv run lab idea append <id> --theme runtime --motivation ... --sketch ...`
  `uv run lab experiments stub <slug> --hypothesis ... --variant ...`
  `uv run lab experiments fill <slug> --run-path ... --from-summary ...`
  `uv run lab roadmap add <slug> ...`
  `uv run lab roadmap done <slug> --ran ... --outcome ...`

The orchestrator (`runner.py`) calls this same CLI rather than
importing the package directly, so the contract is identical for
skills, humans, and the daemon.

Back-compat: the old `insert-critique <trial_id>` /
`insert-comparison <instance> <task>` / `insert-task-features` /
`upsert-component-perf` aliases still work — they look up the
on-disk path from the DB and forward to the new file-writing
helper.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
import time
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from openharness.lab import critic_io
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
tree_app = typer.Typer(no_args_is_help=True, help="Inspect / mutate the configuration tree (lab/configs.md).")
trunk_app = typer.Typer(no_args_is_help=True, help="Show / set the trunk pointer.")
graduate_app = typer.Typer(no_args_is_help=True, help="Confirm or reject staged trunk swaps.")
components_app = typer.Typer(no_args_is_help=True, help="Inspect / mutate the components catalog (lab/components.md).")
runs_app = typer.Typer(no_args_is_help=True, help="Manage runs/experiments/<id>/ directories on disk.")
preflight_app = typer.Typer(
    no_args_is_help=True,
    help="Git preflight + per-experiment worktree management (Phase 0 of the lab pipeline).",
)
phases_app = typer.Typer(
    no_args_is_help=True,
    help="Inspect / reset per-slug pipeline state in runs/lab/state/<slug>/phases.json.",
)
from openharness.lab.svc_cli import svc_app  # noqa: E402  (sub-app, imported after `app`)
app.add_typer(idea_app, name="idea")
app.add_typer(exp_app, name="experiments")
app.add_typer(roadmap_app, name="roadmap")
app.add_typer(daemon_app, name="daemon")
app.add_typer(tree_app, name="tree")
app.add_typer(trunk_app, name="trunk")
app.add_typer(graduate_app, name="graduate")
app.add_typer(components_app, name="components")
app.add_typer(runs_app, name="runs")
app.add_typer(preflight_app, name="preflight")
app.add_typer(phases_app, name="phases")
app.add_typer(svc_app, name="svc")


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


# ===== writers (used by critic skills; FILE-BASED — no DB writes) ==========
#
# Each command writes a JSON file under the canonical layout
# (`critic_io.py`). The DB tables are caches rebuilt on demand by
# `uv run lab ingest-critiques`. This eliminates DuckDB single-writer
# contention between concurrent critic spawns.


def _read_json_arg(value: str | None) -> dict | list:
    if value is None or value == "-":
        return json.load(sys.stdin)
    p = Path(value)
    if p.is_file():
        return json.loads(p.read_text())
    return json.loads(value)


def _expect_dict(payload: dict | list, what: str) -> dict:
    if not isinstance(payload, dict):
        err_console.print(f"[red]{what} payload must be a JSON object[/red]")
        raise typer.Exit(2)
    return payload


@app.command("write-trial-critique")
def write_trial_critique_cmd(
    trial_dir: Path = typer.Argument(
        ..., help="Absolute path to the trial directory under runs/experiments/."
    ),
    json_in: str = typer.Option(
        "-", "--json", help="JSON file path, '-' for stdin, or inline JSON string."
    ),
    critic_model: Optional[str] = typer.Option(
        None, "--critic-model",
        help="Defaults to $OPENHARNESS_CODEX_MODEL set by the codex adapter.",
    ),
) -> None:
    """Persist a per-trial critique to `<trial_dir>/critic/trial-critic.json`."""
    payload = _expect_dict(_read_json_arg(json_in), "critique")
    if not trial_dir.is_dir():
        err_console.print(f"[red]trial_dir does not exist: {trial_dir}[/red]")
        raise typer.Exit(2)
    path = critic_io.write_trial_critique(
        trial_dir, payload, critic_model=critic_model,
    )
    typer.echo(f"write-trial-critique ok: {path}")


@app.command("write-comparison")
def write_comparison_cmd(
    run_dir: Path = typer.Argument(
        ..., help="Absolute path to the experiment run directory."
    ),
    task_name: str = typer.Argument(...),
    json_in: str = typer.Option("-", "--json"),
    critic_model: Optional[str] = typer.Option(None, "--critic-model"),
) -> None:
    """Persist a per-task A/B comparison to `<run_dir>/critic/comparisons/<task>.json`."""
    payload = _expect_dict(_read_json_arg(json_in), "comparison")
    if not run_dir.is_dir():
        err_console.print(f"[red]run_dir does not exist: {run_dir}[/red]")
        raise typer.Exit(2)
    path = critic_io.write_comparison(
        run_dir, task_name, payload, critic_model=critic_model,
    )
    typer.echo(f"write-comparison ok: {path}")


@app.command("write-experiment-critique")
def write_experiment_critique_cmd(
    run_dir: Path = typer.Argument(...),
    json_in: str = typer.Option("-", "--json"),
    critic_model: Optional[str] = typer.Option(None, "--critic-model"),
) -> None:
    """Persist the experiment-level critique to `<run_dir>/critic/experiment-critic.json`."""
    payload = _expect_dict(_read_json_arg(json_in), "experiment-critic summary")
    if not run_dir.is_dir():
        err_console.print(f"[red]run_dir does not exist: {run_dir}[/red]")
        raise typer.Exit(2)
    path = critic_io.write_experiment_critique(
        run_dir, payload, critic_model=critic_model,
    )
    typer.echo(f"write-experiment-critique ok: {path}")


@app.command("write-task-features")
def write_task_features_cmd(
    task_checksum: str = typer.Argument(...),
    json_in: str = typer.Option("-", "--json"),
    extracted_by: Optional[str] = typer.Option(
        None, "--extracted-by",
        help="Defaults to $OPENHARNESS_CODEX_MODEL set by the codex adapter.",
    ),
) -> None:
    """Persist task features to `runs/lab/task_features/<checksum>.json`."""
    payload = _expect_dict(_read_json_arg(json_in), "task-features")
    path = critic_io.write_task_features(
        task_checksum, payload, extracted_by=extracted_by,
    )
    typer.echo(f"write-task-features ok: {path}")


@app.command("write-component-perf")
def write_component_perf_cmd(
    component_id: str = typer.Argument(...),
    task_cluster: str = typer.Argument(...),
    json_in: str = typer.Option("-", "--json"),
) -> None:
    """Persist a component × task-cluster perf row to `runs/lab/components_perf/`."""
    payload = _expect_dict(_read_json_arg(json_in), "component-perf")
    path = critic_io.write_component_perf(component_id, task_cluster, payload)
    typer.echo(f"write-component-perf ok: {path}")


@app.command("write-cross-experiment")
def write_cross_experiment_cmd(
    spawn_id: str = typer.Argument(
        ...,
        help="Spawn id (or any unique tag); used to disambiguate concurrent runs.",
    ),
    json_in: str = typer.Option("-", "--json"),
    critic_model: Optional[str] = typer.Option(None, "--critic-model"),
) -> None:
    """Persist a cross-experiment snapshot to `runs/lab/cross_experiment/`."""
    payload = _expect_dict(_read_json_arg(json_in), "cross-experiment")
    path = critic_io.write_cross_experiment(
        spawn_id, payload, critic_model=critic_model,
    )
    typer.echo(f"write-cross-experiment ok: {path}")


@app.command("write-auto-proposed")
def write_auto_proposed_cmd(
    idea_id: str = typer.Argument(...),
    json_in: str = typer.Option("-", "--json"),
) -> None:
    """Append-only sink for cross-experiment follow-up suggestions."""
    payload = _expect_dict(_read_json_arg(json_in), "auto-proposed-idea")
    path = critic_io.write_auto_proposed(idea_id, payload)
    typer.echo(f"write-auto-proposed ok: {path}")


# ----- back-compat aliases ------------------------------------------------
#
# Old skill prompts (and older docs) used `insert-critique <trial_id>` /
# `insert-comparison <instance_id> <task>` / `insert-task-features`
# / `upsert-component-perf`. Keep these working by looking up the
# on-disk path from the DB and forwarding to the file-writing
# commands above.


@app.command("insert-critique")
def insert_critique_alias(
    trial_id: str = typer.Argument(...),
    json_in: str = typer.Option("-", "--json"),
    critic_model: Optional[str] = typer.Option(None, "--critic-model"),
) -> None:
    """[deprecated alias] Forwards to write-trial-critique using DB lookup."""
    trial_dir = critic_io.trial_dir_from_id(trial_id)
    if trial_dir is None:
        err_console.print(
            f"[red]No trials row for trial_id={trial_id!r}; cannot resolve trial_dir."
            " Run `uv run lab ingest <run_dir>` first, or call write-trial-critique"
            " directly with an absolute path.[/red]"
        )
        raise typer.Exit(2)
    write_trial_critique_cmd(
        trial_dir=trial_dir, json_in=json_in, critic_model=critic_model,
    )


@app.command("insert-comparison")
def insert_comparison_alias(
    instance_id: str = typer.Argument(...),
    task_name: str = typer.Argument(...),
    json_in: str = typer.Option("-", "--json"),
    critic_model: Optional[str] = typer.Option(None, "--critic-model"),
) -> None:
    """[deprecated alias] Forwards to write-comparison using DB lookup."""
    run_dir = critic_io.run_dir_from_instance(instance_id)
    if run_dir is None:
        err_console.print(
            f"[red]No experiments row for instance_id={instance_id!r}.[/red]"
        )
        raise typer.Exit(2)
    write_comparison_cmd(
        run_dir=run_dir, task_name=task_name, json_in=json_in, critic_model=critic_model,
    )


@app.command("insert-task-features")
def insert_task_features_alias(
    task_checksum: str = typer.Argument(...),
    json_in: str = typer.Option("-", "--json"),
    extracted_by: Optional[str] = typer.Option(None, "--extracted-by"),
) -> None:
    """[deprecated alias] Forwards to write-task-features."""
    write_task_features_cmd(
        task_checksum=task_checksum, json_in=json_in, extracted_by=extracted_by,
    )


@app.command("upsert-component-perf")
def upsert_component_perf_alias(
    component_id: str = typer.Argument(...),
    task_cluster: str = typer.Argument(...),
    json_in: str = typer.Option("-", "--json"),
) -> None:
    """[deprecated alias] Forwards to write-component-perf."""
    write_component_perf_cmd(
        component_id=component_id, task_cluster=task_cluster, json_in=json_in,
    )


# ===== ingest-critiques + dump-critiques-to-files ==========================


@app.command("ingest-critiques")
def ingest_critiques_cmd(
    run_dirs: list[Path] = typer.Argument(
        None,
        help=(
            "One or more experiment run dirs to scan. Omit to scan ALL "
            "experiment dirs under runs/experiments/."
        ),
    ),
    include_lab_wide: bool = typer.Option(
        True, "--lab-wide/--no-lab-wide",
        help=(
            "Also load lab-wide artifacts: task_features, components_perf, "
            "spawns. Off if you only want to refresh per-experiment caches."
        ),
    ),
) -> None:
    """Rebuild the DB cache tables from on-disk critic artifacts."""
    summary = labingest.ingest_critiques(
        run_dirs or None, include_lab_wide=include_lab_wide,
    )
    typer.echo(
        f"ingest-critiques: trial_critiques={summary['trial_critiques']} "
        f"comparisons={summary['comparisons']} "
        f"experiment_critic_files={summary['experiment_critic_files']} "
        f"task_features={summary['task_features']} "
        f"components_perf={summary['components_perf']} "
        f"spawns={summary['spawns']}"
    )


@app.command("dump-critiques-to-files")
def dump_critiques_to_files_cmd(
    instance_id: Optional[str] = typer.Argument(
        None, help="Restrict the dump to one instance. Omit for ALL."
    ),
    overwrite: bool = typer.Option(
        False, "--overwrite",
        help="Overwrite existing critic files. Off by default — preserves any "
             "newer file already on disk.",
    ),
) -> None:
    """One-shot migration: dump existing DB rows to the file scheme.

    Use this once after the file-based refactor lands to materialize
    historical critiques (which were inserted into DuckDB by the old
    `insert-critique` path) onto the new on-disk layout. After this
    runs, every trial / task feature / comparison has a JSON file
    next to its evidence and the DB rows can be regenerated from
    them at any time.
    """
    summary = labingest.dump_db_to_files(
        instance_id=instance_id, overwrite=overwrite,
    )
    typer.echo(
        f"dumped: trial_critiques={summary['trial_critiques']} "
        f"comparisons={summary['comparisons']} "
        f"task_features={summary['task_features']} "
        f"components_perf={summary['components_perf']} "
        f"(overwrite={overwrite})"
    )


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
    """Legacy stub (Variant/Results/Notes/Decision shape).

    Kept for back-compat with old experiments. New entries should use
    `lab experiments append-entry` (tree+journal shape).
    """
    lab_docs.stub_experiment(slug=slug, hypothesis=hypothesis, variant=variant)
    typer.echo(f"stubbed experiment {slug!r}")


@exp_app.command("append-entry")
def cmd_exp_append_entry(
    slug: str,
    type_: str = typer.Option(
        "paired-ablation",
        "--type",
        help="paired-ablation | broad-sweep | smoke",
    ),
    trunk: str = typer.Option(
        "",
        "--trunk",
        help="Trunk agent id at run-time (e.g. 'basic'). "
             "Defaults to `uv run lab trunk show`.",
    ),
    mutation: Optional[str] = typer.Option(
        None,
        "--mutation",
        help="One-line description of what differs from trunk. "
             "Omit for --type broad-sweep.",
    ),
    hypothesis: str = typer.Option(..., "--hypothesis"),
    run_path: Optional[str] = typer.Option(
        None,
        "--run",
        help="repo-relative path, e.g. runs/experiments/<id>. Optional.",
    ),
    branch: Optional[str] = typer.Option(
        None,
        "--branch",
        help="Experiment branch name (e.g. lab/<slug>). If omitted, "
             "the entry's Branch bullet is rendered as a placeholder "
             "and `experiments set-branch` fills it in later.",
    ),
) -> None:
    """Append a new journal entry (tree+journal shape) at the top of
    `lab/experiments.md`.

    Header is filled in immediately; the five `### <section>` blocks
    are stubbed empty and populated later by `experiments synthesize`
    and `tree apply`.
    """
    if not trunk:
        snap = lab_docs.tree_snapshot()
        trunk = snap.trunk_id or "unknown"
    trunk_md = (
        f"[`{trunk}`](../src/openharness/agents/configs/{trunk}.yaml)"
        if not trunk.startswith("[")
        else trunk
    )
    lab_docs.append_journal_entry(
        slug=slug,
        type_=type_,
        trunk_at_runtime=trunk_md,
        mutation=mutation,
        hypothesis=hypothesis,
        run_path=run_path,
        branch=branch,
    )
    typer.echo(f"appended journal entry {slug!r} (type={type_})")


@exp_app.command("set-branch")
def cmd_exp_set_branch(
    slug: str,
    branch: str = typer.Option(..., "--branch", help="e.g. lab/<slug>"),
    pr_url: Optional[str] = typer.Option(
        None,
        "--pr-url",
        help="Open PR URL. Renders as `Branch: [<branch>](<pr-url>)`.",
    ),
    rejected_reason: Optional[str] = typer.Option(
        None,
        "--rejected-reason",
        help="One-line reason. Renders as `Branch: <branch> — not "
             "opened (<reason>)`. Mutually exclusive with --pr-url.",
    ),
) -> None:
    """Replace the **Branch:** bullet on the journal entry for `slug`.

    Called from the `lab-finalize-pr` skill after deciding whether to
    push the experiment branch and open a PR (verdict AddBranch /
    Graduate) or to discard the worktree (verdict Reject / NoOp).
    Idempotent — safe to re-run with the same arguments.
    """
    if pr_url and rejected_reason:
        typer.echo("[red]--pr-url and --rejected-reason are mutually exclusive[/red]")
        raise typer.Exit(2)
    lab_docs.set_journal_branch(
        slug=slug,
        branch=branch,
        pr_url=pr_url,
        rejected_reason=rejected_reason,
    )
    if pr_url:
        typer.echo(f"set Branch bullet for {slug!r} -> PR {pr_url}")
    elif rejected_reason:
        typer.echo(f"set Branch bullet for {slug!r} -> not opened ({rejected_reason})")
    else:
        typer.echo(f"set Branch bullet for {slug!r} -> {branch}")


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


@app.command()
def webui(
    port: int = typer.Option(8765, "--port", help="HTTP port to bind."),
    host: str = typer.Option("127.0.0.1", "--host", help="Interface to bind."),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload on code changes (dev)."),
    log_level: str = typer.Option("info", "--log-level"),
) -> None:
    """Launch the lab web UI (FastAPI + HTMX, operator console + live commands)."""
    from openharness.lab.web import auth as labauth
    from openharness.lab.web.server import run as run_webui

    err_console.print(f"[green]Lab web UI →[/green] http://{host}:{port}")
    mode = labauth.configured_mode()
    if mode == "proxy":
        kind = os.environ.get("LAB_TRUST_PROXY_AUTH", "?")
        admins = os.environ.get("LAB_ADMIN_EMAILS", "")
        viewers = os.environ.get("LAB_VIEWER_EMAILS", "")
        err_console.print(
            f"[green]Auth:[/green] proxy mode ({kind}) — identity from "
            "trusted reverse-proxy header."
        )
        if not admins:
            err_console.print(
                "[red]WARNING:[/red] LAB_ADMIN_EMAILS is empty in proxy mode. "
                "Nobody will be able to run /api/cmd. Set it to a comma-"
                "separated list of admin emails."
            )
        else:
            err_console.print(f"  admins:  {admins}")
            if viewers:
                err_console.print(f"  viewers: {viewers}")
    else:
        err_console.print(
            "[yellow]Auth:[/yellow] open mode — /api/cmd is unrestricted. "
            "Use loopback / SSH tunnel only, or set LAB_TRUST_PROXY_AUTH "
            "(SSO via reverse proxy) to share."
        )
    if host not in ("127.0.0.1", "localhost", "::1") and mode == "open":
        err_console.print(
            "[red]WARNING:[/red] binding to a non-loopback host with no auth. "
            "Anyone who can reach this port can mutate the lab."
        )

    # Preflight: catch the most common operational mistake — running
    # `lab webui` interactively while the systemd unit already has the
    # port. uvicorn's bare "address already in use" trace is too easy
    # to misread as a code bug; surface the real cause + the fix.
    _webui_preflight_check(host=host, port=port)

    run_webui(host=host, port=port, reload=reload, log_level=log_level)


def _webui_preflight_check(*, host: str, port: int) -> None:
    """Refuse to start if something is already bound to ``host:port``.

    Splits the diagnosis into two cases:

    1. The systemd ``openharness-lab.service`` unit is active. Show
       its pid / start time and recommend ``systemctl --user
       restart`` (to pick up code changes) or pointing the browser
       at the already-running instance.
    2. Some other process holds the port — show the pid via the
       socket inspection so the operator knows what to investigate.

    On any introspection failure (no systemctl, no /proc, etc.) we
    fall through silently; uvicorn will still produce its own error
    a moment later. This is purely a UX layer.
    """
    import socket

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # Match uvicorn's bind semantics: SO_REUSEADDR=1 so a TIME_WAIT
    # socket from a just-stopped webui doesn't make us think the port
    # is held by something live. Without this, `systemctl restart`
    # fails ~5 s after stopping a previous instance because Python's
    # default REUSEADDR is 0.
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind((host, port))
    except OSError:
        # Port is taken. Figure out by what.
        from openharness.lab.web import services as labsvc

        webui_unit = labsvc.status("openharness-lab")
        # Treat any "loaded + not-stopped" state as ours so we still
        # show the friendly hint during the brief `activating` window
        # right after `systemctl restart`. Stopped units fall through
        # to the generic message below.
        unit_is_ours = (
            webui_unit.load_state == "loaded"
            and webui_unit.active_state in {"active", "activating", "reloading"}
        )
        if unit_is_ours:
            pid_str = (
                f" (pid {webui_unit.main_pid}"
                + (f", since {webui_unit.started_at}" if webui_unit.started_at else "")
                + ")"
                if webui_unit.main_pid
                else f" ({webui_unit.active_state})"
            )
            err_console.print()
            err_console.print(
                f"[red]ERROR:[/red] [bold]openharness-lab.service[/bold] is "
                f"already running{pid_str}. Either:"
            )
            err_console.print(
                f"  • visit the running instance:  [cyan]http://{host}:{port}/[/cyan]"
            )
            err_console.print(
                "  • restart it to pick up code changes:  "
                "[cyan]uv run lab svc restart web[/cyan]"
            )
            err_console.print(
                "  • stop it and run interactively here:  "
                "[cyan]uv run lab svc stop web && uv run lab webui[/cyan]"
            )
        else:
            err_console.print()
            err_console.print(
                f"[red]ERROR:[/red] something else is already bound to "
                f"{host}:{port}. Find it with:"
            )
            err_console.print(
                f"  [cyan]ss -tlnp 'sport = :{port}'[/cyan]   "
                f"# (then kill / reconfigure / pick a different --port)"
            )
        raise typer.Exit(1)
    finally:
        s.close()


# ===== analyze (manual backfill) ============================================


@app.command()
def analyze(
    instance_id: str = typer.Argument(
        ...,
        help="The experiment instance_id to analyze (matches `experiments.instance_id`).",
    ),
    concurrency: int = typer.Option(
        4, "--concurrency", "-j", min=1, max=32,
        help="Max parallel codex spawns. Defaults to the codex adapter's pool size.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Print the spawn plan and exit. No codex calls.",
    ),
    limit_trials: Optional[int] = typer.Option(
        None, "--limit-trials",
        help="Cap how many trial-critic spawns to run. Useful for smoke tests.",
    ),
    limit_features: Optional[int] = typer.Option(
        None, "--limit-features",
        help="Cap how many task-features spawns to run.",
    ),
    skip_trial_critic: bool = typer.Option(
        False, "--skip-trial-critic", help="Skip Phase A trial-critic fan-out.",
    ),
    skip_task_features: bool = typer.Option(
        False, "--skip-task-features", help="Skip Phase A task-features fan-out.",
    ),
    skip_experiment_critic: bool = typer.Option(
        False, "--skip-experiment-critic", help="Skip Phase B experiment-critic.",
    ),
    force_experiment_critic: bool = typer.Option(
        False, "--force-experiment-critic",
        help="Run experiment-critic even if comparison rows already exist.",
    ),
    include_cross_experiment: bool = typer.Option(
        False, "--include-cross-experiment",
        help=(
            "Also run cross-experiment-critic after Phase B. Off by default "
            "because the apex spawn analyzes the WHOLE database, not just "
            "this instance."
        ),
    ),
) -> None:
    """Backfill critic data for an existing experiment.

    Mirrors the daemon's post-ingest pipeline (steps 4-7), but
    targeted at one instance and triggered manually. Three phases:

      A. parallel: trial-critic over uncritiqued trials  +
                   task-features over unseen task_checksums
      B. sequential: experiment-critic <instance_id>
                     (only if every trial now has a critique)
      C. sequential (opt-in): cross-experiment-critic

    Use --dry-run to preview the spawn plan, --limit-trials N to
    smoke a small batch first, --concurrency N to tune the pool.
    """
    from openharness.lab import codex as codex_adapter
    from openharness.lab.runner import (
        checksums_needing_features,
        comparison_exists,
        instance_exists,
        trials_needing_critique,
    )

    if not LAB_DB_PATH.exists():
        err_console.print("[red]No lab DB. Run `uv run lab init` and `uv run lab ingest <run_dir>` first.[/red]")
        raise typer.Exit(1)
    if not instance_exists(instance_id):
        err_console.print(f"[red]instance_id {instance_id!r} not found in `experiments` table[/red]")
        raise typer.Exit(1)

    # ---- gather work ------------------------------------------------------
    trial_jobs = [] if skip_trial_critic else trials_needing_critique(instance_id)
    feature_jobs = [] if skip_task_features else checksums_needing_features(instance_id)
    if limit_trials is not None:
        trial_jobs = trial_jobs[:limit_trials]
    if limit_features is not None:
        feature_jobs = feature_jobs[:limit_features]

    cmp_already = comparison_exists(instance_id)
    will_run_exp_critic = not skip_experiment_critic and (
        force_experiment_critic or not cmp_already
    )

    # ---- print plan -------------------------------------------------------
    plan = Table(title=f"analyze plan for {instance_id}", show_header=True)
    plan.add_column("phase")
    plan.add_column("skill")
    plan.add_column("count", justify="right")
    plan.add_column("notes")
    plan.add_row(
        "A", "trial-critic", str(len(trial_jobs)),
        "skipped" if skip_trial_critic else f"limit={limit_trials}" if limit_trials else "all uncritiqued",
    )
    plan.add_row(
        "A", "task-features", str(len(feature_jobs)),
        "skipped" if skip_task_features else f"limit={limit_features}" if limit_features else "all unseen checksums",
    )
    plan.add_row(
        "B", "experiment-critic",
        "1" if will_run_exp_critic else "0",
        "skipped" if skip_experiment_critic
        else "comparison rows already present (use --force-experiment-critic to re-run)" if cmp_already and not force_experiment_critic
        else "would run",
    )
    plan.add_row(
        "C", "cross-experiment-critic",
        "1" if include_cross_experiment else "0",
        "opt in with --include-cross-experiment" if not include_cross_experiment
        else "WHOLE DB scope; xhigh, singleton, ~12h cap",
    )
    console.print(plan)

    if dry_run:
        typer.echo("(dry-run; no spawns issued)")
        raise typer.Exit(0)

    if not (trial_jobs or feature_jobs or will_run_exp_critic or include_cross_experiment):
        typer.echo("nothing to do.")
        raise typer.Exit(0)

    # ---- build a codex config tuned for backfill --------------------------
    # NB: enforce_orchestrator_lock=False because `analyze` is a
    # human-triggered backfill, not the daemon — it must NOT contend
    # with the orchestrator lock. record_in_db=True so spawns still
    # land in the `spawns` table (so this run is auditable next to
    # daemon-driven runs).
    cx = codex_adapter.CodexConfig(
        max_concurrency=concurrency,
        enforce_orchestrator_lock=False,
        record_in_db=True,
    )

    started = datetime.now(timezone.utc)
    typer.echo(f"\n=== Phase A: per-trial + per-checksum (concurrency={concurrency}) ===")
    # Interleave so monitoring sees both row counts climbing in
    # parallel; the global semaphore decides actual concurrency.
    trial_invs = [("trial-critic", [trial_dir]) for _, trial_dir in trial_jobs]
    feat_invs = [("task-features", [c]) for c in feature_jobs]
    invocations: list[tuple[str, list[str]]] = []
    for i in range(max(len(trial_invs), len(feat_invs))):
        if i < len(trial_invs):
            invocations.append(trial_invs[i])
        if i < len(feat_invs):
            invocations.append(feat_invs[i])
    if invocations:
        results_a = codex_adapter.run_many(invocations, cfg=cx)
        n_ok = sum(1 for r in results_a if r.ok)
        n_fail = len(results_a) - n_ok
        typer.echo(f"Phase A done: {n_ok} ok, {n_fail} failed.")
        if n_fail:
            err_console.print("[yellow]failed spawn logs:[/yellow]")
            for r in results_a:
                if not r.ok:
                    err_console.print(f"  {r.skill} args={r.args} -> exit={r.exit_code} log={r.log_path}")
    else:
        typer.echo("Phase A: nothing to do.")

    # Re-check after Phase A: experiment-critic only makes sense once
    # every trial has a critique; warn loudly if any are missing.
    still_needing = trials_needing_critique(instance_id)
    if will_run_exp_critic and still_needing:
        err_console.print(
            f"[yellow]Skipping experiment-critic: {len(still_needing)} trial(s) "
            "still missing critiques after Phase A. Re-run analyze to retry.[/yellow]"
        )
        will_run_exp_critic = False

    if will_run_exp_critic:
        typer.echo("\n=== Phase B: experiment-critic ===")
        r = codex_adapter.run("experiment-critic", [instance_id], cfg=cx)
        typer.echo(
            f"experiment-critic exit={r.exit_code} log={r.log_path}"
            f" duration={r.duration_sec:.0f}s"
        )

    if include_cross_experiment:
        typer.echo("\n=== Phase C: cross-experiment-critic ===")
        r = codex_adapter.run("cross-experiment-critic", [], cfg=cx)
        typer.echo(
            f"cross-experiment-critic exit={r.exit_code} log={r.log_path}"
            f" duration={r.duration_sec:.0f}s"
        )

    # Refresh the DB cache from the on-disk critic artifacts the
    # spawns above produced. Critic outputs are files (single
    # source of truth); the DB tables are derived.
    typer.echo("\n=== refreshing DB cache from critic files ===")
    run_dir = critic_io.run_dir_from_instance(instance_id)
    cache = labingest.ingest_critiques(
        [run_dir] if run_dir else None,
        include_lab_wide=True,
    )
    typer.echo(
        "ingest-critiques: " + ", ".join(f"{k}={v}" for k, v in cache.items())
    )

    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    typer.echo(f"\nanalyze done in {elapsed:.0f}s ({elapsed/60:.1f}m).")


# ===== tree / trunk / graduate / experiments-synthesize ===================
#
# All of these commands are *deterministic* mutations of the lab.
# They never touch codex. Critic skills and the daemon call them via
# this CLI; humans do too. The runner's close-loop is just:
#
#   lab experiments synthesize <slug>      # write Aggregate / Mutation impact
#   lab tree apply <slug>                  # tree_ops.evaluate + tree.apply_diff
#   lab roadmap suggest <new_slug> ...     # if the verdict surfaces a follow-up
#   lab graduate confirm <slug>            # human-only step for trunk swaps


def _lookup_instance_for_slug(conn: object, slug: str) -> str | None:
    """Best-effort: map a journal slug to an `experiments.instance_id`.

    Resolution order (each rejects to the next on miss):
      1. exact: instance_id == slug
      2. cached: tree_diffs.slug == slug
      3. prefix: instance_id LIKE slug || '-%'
      4. experiment_id == slug
      5. slug starts with `<experiment_id>-` for some row
    """
    exact = conn.execute(  # type: ignore[attr-defined]
        "SELECT instance_id FROM experiments WHERE instance_id = ?",
        [slug],
    ).fetchone()
    if exact:
        return exact[0]
    cached = conn.execute(  # type: ignore[attr-defined]
        "SELECT instance_id FROM tree_diffs WHERE slug = ?", [slug],
    ).fetchone()
    if cached and cached[0]:
        return cached[0]
    prefix = conn.execute(  # type: ignore[attr-defined]
        "SELECT instance_id FROM experiments "
        "WHERE instance_id LIKE ? || '-%' "
        "ORDER BY created_at DESC LIMIT 1",
        [slug],
    ).fetchone()
    if prefix:
        return prefix[0]
    by_eid = conn.execute(  # type: ignore[attr-defined]
        "SELECT instance_id FROM experiments "
        "WHERE experiment_id = ? "
        "ORDER BY created_at DESC LIMIT 1",
        [slug],
    ).fetchone()
    if by_eid:
        return by_eid[0]
    rows = conn.execute(  # type: ignore[attr-defined]
        "SELECT instance_id, experiment_id FROM experiments "
        "ORDER BY created_at DESC"
    ).fetchall()
    for inst_id, eid in rows:
        if eid and slug.startswith(f"{eid}-"):
            return inst_id
    return None


def _resolve_diff_for_slug(slug: str):
    # -> tuple[str, tree_ops.TreeDiff]; lazy import to avoid cycles.
    """Look up the experiment instance for a slug and recompute its diff.

    Falls back to the cached row in `tree_diffs` if the slug isn't in
    `experiments`. We always recompute from `tree_ops.evaluate` when
    we have an instance_id so the verdict reflects the latest data.
    """
    from openharness.lab import tree_ops as _tree_ops

    instance_id: str | None = None
    with labdb.reader() as conn:
        instance_id = _lookup_instance_for_slug(conn, slug)

    if not instance_id:
        err_console.print(
            f"[red]Could not resolve slug {slug!r} to an instance_id.[/red]"
        )
        raise typer.Exit(2)

    diff = _tree_ops.evaluate(instance_id)
    return instance_id, diff


@tree_app.command("show")
def tree_show(json_out: bool = typer.Option(False, "--json")) -> None:
    """Print the current configuration tree (trunk + branches + rejected)."""
    snap = lab_docs.tree_snapshot()
    if json_out:
        typer.echo(json.dumps({
            "trunk_id": snap.trunk_id,
            "trunk_anchor": snap.trunk_anchor,
            "branches": [b.__dict__ for b in snap.branches],
            "rejected": [r.__dict__ for r in snap.rejected],
            "proposed": [p.__dict__ for p in snap.proposed],
        }, indent=2))
        return
    console.print(f"[bold]Trunk:[/bold] [cyan]{snap.trunk_id}[/cyan]")
    if snap.trunk_anchor:
        console.print(f"  [dim]{snap.trunk_anchor}[/dim]")
    if snap.branches:
        t = Table(title="Branches", show_header=True)
        t.add_column("ID")
        t.add_column("Mutation")
        t.add_column("Use-when")
        t.add_column("Last verified")
        for b in snap.branches:
            t.add_row(b.branch_id, b.mutation, b.use_when, b.last_verified or "")
        console.print(t)
    else:
        console.print("[dim]Branches: (none)[/dim]")
    if snap.rejected:
        t = Table(title="Rejected", show_header=True)
        t.add_column("ID")
        t.add_column("Reason")
        t.add_column("Evidence")
        for r in snap.rejected:
            t.add_row(r.branch_id, r.reason, r.evidence or "")
        console.print(t)


@tree_app.command("apply")
def tree_apply(
    slug: str = typer.Argument(...),
    instance: Optional[str] = typer.Option(
        None, "--instance",
        help="Override: use this instance_id (else resolved from slug).",
    ),
    applied_by: str = typer.Option("auto:cli", "--applied-by"),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Print the diff that would be applied; do not write.",
    ),
) -> None:
    """Recompute the TreeDiff for `slug` and apply it to the lab."""
    from openharness.lab import tree as _tree
    from openharness.lab import tree_ops as _tree_ops

    if instance:
        diff = _tree_ops.evaluate(instance)
    else:
        _, diff = _resolve_diff_for_slug(slug)

    console.print(f"[bold]TreeDiff for {slug}:[/bold]")
    console.print(json.dumps(diff.to_dict(), indent=2, default=str))

    if dry_run:
        typer.echo("(dry-run; no edits)")
        raise typer.Exit(0)

    result = _tree.apply_diff(slug=slug, diff=diff, applied_by=applied_by)
    typer.echo(
        f"\napplied={result.applied} (by={result.applied_by}); "
        f"journal_block_written={result.journal_block_written}"
    )
    for note in result.notes:
        typer.echo(f"  - {note}")


@trunk_app.command("show")
def trunk_show() -> None:
    """Print the current trunk id (last `trunk_changes` row, else trunk.yaml)."""
    from openharness.lab import tree_ops as _tree_ops

    tid = _tree_ops.current_trunk_id()
    typer.echo(tid)


@trunk_app.command("set")
def trunk_set(
    trunk_id: str = typer.Argument(...),
    reason: str = typer.Option(..., "--reason"),
    journal_link: Optional[str] = typer.Option(
        None, "--journal-link",
        help="e.g. '[`tb2-baseline-full-sweep`](experiments.md#...)'",
    ),
    audit: bool = typer.Option(
        True, "--audit/--no-audit",
        help="Also append a `trunk_changes` row.",
    ),
) -> None:
    """Manually set the `## Trunk` pointer in configs.md (and audit it).

    NOTE: this only edits the markdown + audit log. It does NOT copy
    `<trunk_id>.yaml → trunk.yaml`. Use `lab graduate confirm` for
    that — it's the path that swaps the actual agent file.
    """
    from openharness.lab.tree_ops import current_trunk_id, insert_trunk_change

    prev = current_trunk_id()
    lab_docs.set_trunk(trunk_id=trunk_id, reason=reason, journal_link=journal_link)
    msg = f"trunk set to {trunk_id!r}"
    if audit:
        with labdb.writer() as conn:
            insert_trunk_change(
                conn,
                at_ts=datetime.now(timezone.utc),
                from_id=prev if prev != trunk_id else None,
                to_id=trunk_id,
                reason=reason,
                applied_by="human:cli",
            )
        msg += " (audited in trunk_changes)"
    typer.echo(msg)


@graduate_app.command("confirm")
def graduate_confirm(
    slug: str = typer.Argument(...),
    applied_by: str = typer.Option(
        ..., "--applied-by",
        help="e.g. 'human:alice' — required so trunk swaps are attributable.",
    ),
    reason: Optional[str] = typer.Option(
        None, "--reason",
        help="Override the rationale (else the diff's own rationale is used).",
    ),
    instance: Optional[str] = typer.Option(
        None, "--instance",
        help="Override: use this instance_id (else resolved from slug).",
    ),
) -> None:
    """Confirm a STAGED Graduate diff: copy `<target>.yaml → trunk.yaml` + audit.

    This is the only path that swaps the actual trunk YAML. It
    requires `--applied-by` so we always know who made the call.
    """
    from openharness.lab import tree as _tree
    from openharness.lab import tree_ops as _tree_ops

    if instance:
        diff = _tree_ops.evaluate(instance)
    else:
        _, diff = _resolve_diff_for_slug(slug)

    if diff.kind != "graduate":
        err_console.print(
            f"[red]TreeDiff for {slug!r} is kind={diff.kind!r}, not 'graduate'."
            f" Nothing to confirm.[/red]"
        )
        raise typer.Exit(2)

    result = _tree.confirm_graduate(
        slug=slug, diff=diff, applied_by=applied_by, reason=reason,
    )
    typer.echo(f"graduated `{diff.target_id}` → trunk (by={applied_by}).")
    for note in result.notes:
        typer.echo(f"  - {note}")


@exp_app.command("synthesize")
def cmd_exp_synthesize(
    slug: str = typer.Argument(...),
    instance: Optional[str] = typer.Option(
        None, "--instance",
        help="Override: use this instance_id (else looked up from slug).",
    ),
    sections: list[str] = typer.Option(
        [], "--section",
        help=(
            "Repeat to limit which `### <section>`s are synthesised. "
            "Default: Aggregate, Mutation impact, Failure modes, "
            "Linked follow-ups (the four narrative sections; "
            "Tree effect comes from `lab tree apply`)."
        ),
    ),
) -> None:
    """Synthesize narrative journal sections for `slug` from critic JSONs.

    Reads `<run_dir>/critic/experiment-critic.json`, the per-task
    `comparisons/*.json`, and the trial critiques, then writes the
    `### Aggregate`, `### Mutation impact`, `### Failure modes`, and
    `### Linked follow-ups` blocks into the matching journal entry.
    Idempotent: re-running rewrites in place.
    """
    from openharness.lab import journal_synth

    instance_id = instance
    if not instance_id:
        with labdb.reader() as conn:
            instance_id = _lookup_instance_for_slug(conn, slug)
        if not instance_id:
            err_console.print(
                f"[red]No experiment matches slug {slug!r}.[/red]"
            )
            raise typer.Exit(2)

    written = journal_synth.synthesize(
        slug=slug, instance_id=instance_id,
        only_sections=sections or None,
    )
    typer.echo(f"synthesized {len(written)} section(s) into {slug!r}:")
    for section in written:
        typer.echo(f"  - ### {section}")


# ===== roadmap suggest / promote ==========================================


@roadmap_app.command("suggest")
def cmd_roadmap_suggest(
    slug: str,
    hypothesis: str = typer.Option(..., "--hypothesis"),
    source: str = typer.Option(
        ..., "--source",
        help="e.g. 'cross-experiment-critic@2026-04-18' or 'lab-reflect-and-plan'.",
    ),
    cost: Optional[str] = typer.Option(None, "--cost"),
) -> None:
    """Append a daemon-proposed entry to `## Up next > ### Suggested`.

    The Suggested subsection is the agent write-zone; humans promote
    one to the main queue with `lab roadmap promote <slug>`.
    """
    lab_docs.add_suggested_followup(
        slug=slug, hypothesis=hypothesis, source=source, cost=cost,
    )
    typer.echo(f"suggested {slug!r} (source={source})")


@roadmap_app.command("promote")
def cmd_roadmap_promote(slug: str) -> None:
    """Move `### Suggested > #### <slug>` into the main `## Up next` queue."""
    try:
        lab_docs.promote_suggested(slug=slug)
    except lab_docs.LabDocError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc
    typer.echo(f"promoted {slug!r} to ## Up next")


@roadmap_app.command("demote")
def cmd_roadmap_demote(slug: str) -> None:
    """Move `## Up next > ### <slug>` back into `### Suggested > #### <slug>`."""
    try:
        lab_docs.demote_to_suggested(slug=slug)
    except lab_docs.LabDocError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc
    typer.echo(f"demoted {slug!r} to ### Suggested")


@roadmap_app.command("remove")
def cmd_roadmap_remove(
    slug: str,
    section: Optional[str] = typer.Option(
        None, "--section",
        help="Restrict to one of: 'up-next' | 'suggested' | 'done'. "
             "Default scans all three.",
    ),
) -> None:
    """Delete a roadmap entry by slug from any section.

    Use ``--section`` to be explicit when the same slug might appear in
    more than one place (rare).
    """
    sections: tuple[str, ...]
    if section is None:
        sections = ("Up next", "Suggested", "Done")
    else:
        mapping = {"up-next": "Up next", "suggested": "Suggested", "done": "Done"}
        key = section.strip().lower()
        if key not in mapping:
            err_console.print(
                f"[red]--section must be one of {sorted(mapping)}, got {section!r}[/red]"
            )
            raise typer.Exit(2)
        sections = (mapping[key],)
    try:
        lab_docs.remove_roadmap_entry(slug=slug, sections=sections)
    except lab_docs.LabDocError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc
    typer.echo(f"removed roadmap entry {slug!r}")


# ===== idea auto-propose (cross-experiment-critic write-zone) ==============


@idea_app.command("auto-propose")
def cmd_idea_auto_propose(
    idea_id: str,
    motivation: str = typer.Option(..., "--motivation"),
    sketch: str = typer.Option(..., "--sketch"),
    source: str = typer.Option(
        ..., "--source",
        help="e.g. 'cross-experiment-critic@2026-04-18'.",
    ),
) -> None:
    """Append a follow-up to `## Auto-proposed` (alias for the legacy command)."""
    lab_docs.append_auto_proposed_idea(
        idea_id=idea_id, motivation=motivation, sketch=sketch, source=source,
    )
    typer.echo(f"auto-proposed {idea_id!r}")


# ===== components catalog ==================================================


@components_app.command("show")
def components_show(
    kind: Optional[str] = typer.Option(
        None, "--kind", help="Filter to one of Architecture/Runtime/Tools/Prompt/Model.",
    ),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Print the components catalog (atoms grouped by kind + status)."""
    from openharness.lab import components_doc as cdoc

    cat = cdoc.read_catalog()
    if json_out:
        out: dict[str, list[dict]] = {}
        for k, entries in cat.by_kind.items():
            if kind and k != kind:
                continue
            out[k] = [
                {
                    "id": e.component_id,
                    "status": e.status,
                    "description": e.description,
                    "used_by": e.used_by,
                    "evidence": e.evidence,
                }
                for e in entries
            ]
        typer.echo(json.dumps(out, indent=2))
        return

    for k in cdoc.CATALOG_KINDS:
        if kind and k != kind:
            continue
        entries = cat.by_kind.get(k, [])
        if not entries:
            console.print(f"[bold]{k}:[/bold] [dim](none)[/dim]")
            continue
        t = Table(title=k, show_header=True)
        t.add_column("ID", style="cyan")
        t.add_column("Status")
        t.add_column("Description")
        t.add_column("Used by")
        for e in entries:
            t.add_row(
                e.component_id,
                _status_badge(e.status),
                e.description,
                ", ".join(e.used_by) if e.used_by else "—",
            )
        console.print(t)


def _status_badge(status: str) -> str:
    return {
        "proposed":     "[dim]proposed[/dim]",
        "experimental": "[yellow]experimental[/yellow]",
        "branch":       "[cyan]branch[/cyan]",
        "validated":    "[green]validated[/green]",
        "rejected":     "[red]rejected[/red]",
        "superseded":   "[magenta]superseded[/magenta]",
    }.get(status, status)


@components_app.command("upsert")
def components_upsert(
    component_id: str = typer.Argument(...),
    kind: str = typer.Option(..., "--kind", help="Architecture/Runtime/Tools/Prompt/Model."),
    description: Optional[str] = typer.Option(None, "--description"),
    status: Optional[str] = typer.Option(
        None, "--status",
        help="proposed/experimental/branch/validated/rejected/superseded "
             "(forward-only via this command).",
    ),
    used_by: Optional[str] = typer.Option(
        None, "--used-by",
        help="Comma-separated agent ids that include this component.",
    ),
    evidence: Optional[str] = typer.Option(
        None, "--evidence",
        help="Markdown link or short string to append to the Evidence column.",
    ),
) -> None:
    """Insert or update a component entry. Status bumps are forward-only."""
    from openharness.lab import components_doc as cdoc

    used_list = [u.strip() for u in used_by.split(",") if u.strip()] if used_by else None
    ev_list = [evidence.strip()] if evidence else None
    entry = cdoc.upsert(
        component_id=component_id,
        kind=kind,
        description=description,
        status=status,
        used_by=used_list,
        evidence=ev_list,
    )
    typer.echo(f"upserted {entry.component_id!r} [{entry.status}] under {entry.kind}")


@components_app.command("set-status")
def components_set_status(
    component_id: str = typer.Argument(...),
    status: str = typer.Argument(..., help="One of proposed/experimental/branch/validated/rejected/superseded."),
    evidence: Optional[str] = typer.Option(
        None, "--evidence",
        help="Markdown link or short string to append to the Evidence column.",
    ),
) -> None:
    """Unconditional status set (humans only — bypasses the bump lattice)."""
    from openharness.lab import components_doc as cdoc

    entry = cdoc.set_status(component_id=component_id, status=status, evidence=evidence)
    typer.echo(f"set {entry.component_id!r} → {entry.status}")


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


# ===== daemon control surface (mode / approve / cancel / state) =============
#
# These mutate `runs/lab/daemon-state.json` rather than the markdown
# files. They're the CLI side of the same surface the web UI exposes
# via `/api/cmd` whitelisted entries; both routes go through the
# same `daemon_state` module so behaviour is identical.


_VALID_MODES = ("paused", "manual", "autonomous")


@daemon_app.command("mode")
def daemon_mode(
    new_mode: str = typer.Argument(
        ...,
        help="paused | manual | autonomous",
    ),
    actor: str = typer.Option(
        "human:cli", "--actor",
        help="Recorded as last_updated_by in daemon-state.json.",
    ),
) -> None:
    """Set the daemon's operating mode.

    - **paused**: daemon process keeps running but processes nothing.
    - **manual**: only runs roadmap entries you explicitly approve
      via `lab daemon approve <slug>`. Approvals are one-shot.
    - **autonomous**: walks the queue automatically (legacy
      behaviour). The exit gate (auto-demote after N consecutive
      failures) is always on regardless of mode.
    """
    from openharness.lab import daemon_state as _ds

    if new_mode not in _VALID_MODES:
        err_console.print(
            f"[red]invalid mode '{new_mode}' (use: {' | '.join(_VALID_MODES)})[/red]"
        )
        raise typer.Exit(2)
    state = _ds.set_mode(new_mode, actor=actor)  # type: ignore[arg-type]
    woke = _ds.notify_daemon()
    typer.echo(f"mode → {state.mode}" + (" (daemon notified)" if woke else ""))


@daemon_app.command("approve")
def daemon_approve(
    slug: str = typer.Argument(..., help="Roadmap slug to approve for one tick."),
    actor: str = typer.Option("human:cli", "--actor"),
) -> None:
    """Approve a roadmap slug for the next tick (manual mode).

    The approval is *consumed* on the next pickup — the daemon will
    process this slug exactly once and then return to waiting.
    """
    from openharness.lab import daemon_state as _ds

    state = _ds.approve(slug, actor=actor)
    woke = _ds.notify_daemon()
    typer.echo(
        f"approved {slug!r}; queue: {state.approved_slugs}"
        + (" (daemon notified)" if woke else "")
    )


@daemon_app.command("revoke")
def daemon_revoke(
    slug: str = typer.Argument(..., help="Slug to remove from approvals."),
    actor: str = typer.Option("human:cli", "--actor"),
) -> None:
    """Remove a slug from the approval list."""
    from openharness.lab import daemon_state as _ds

    state = _ds.revoke(slug, actor=actor)
    woke = _ds.notify_daemon()
    typer.echo(
        f"revoked {slug!r}; queue: {state.approved_slugs}"
        + (" (daemon notified)" if woke else "")
    )


@daemon_app.command("approvals")
def daemon_approvals() -> None:
    """List currently-approved slugs."""
    from openharness.lab import daemon_state as _ds

    state = _ds.load()
    if not state.approved_slugs:
        typer.echo("(no approvals)")
        return
    for s in state.approved_slugs:
        typer.echo(s)


@daemon_app.command("cancel")
def daemon_cancel(
    actor: str = typer.Option("human:cli", "--actor"),
) -> None:
    """SIGTERM the active codex spawn (if any) and clear the active tick.

    The signal goes to the recorded ``active_tick.spawn_pid``. If no
    tick is in flight, this is a no-op (exit 0). Safety: the pid is
    only killed if it's still alive AND the daemon recorded it
    itself; we don't trust an arbitrary pid from the state file.
    """
    import os as _os
    import signal as _sig
    from openharness.lab import daemon_state as _ds

    state = _ds.load()
    if state.active_tick is None:
        typer.echo("(no active tick)")
        return
    at = state.active_tick
    if at.spawn_pid:
        try:
            _os.kill(at.spawn_pid, _sig.SIGTERM)
            typer.echo(f"sent SIGTERM to spawn pid {at.spawn_pid} ({at.slug})")
        except ProcessLookupError:
            typer.echo(f"spawn pid {at.spawn_pid} already gone")
    else:
        typer.echo(f"active tick has no spawn_pid yet (phase={at.phase})")
    # Mark cancelled in history so the UI shows what happened. Don't
    # increment the failure counter — operator override shouldn't
    # count toward the auto-demote gate.
    with _ds.mutate(actor=actor) as st:
        if st.active_tick is not None:
            from datetime import datetime as _dt, timezone as _tz
            now = _dt.now(_tz.utc)
            st.history.append(
                _ds.TickHistoryEntry(
                    slug=at.slug,
                    started_at=at.started_at,
                    ended_at=now,
                    outcome="cancelled",
                    phase_reached=at.phase,
                    duration_sec=(now - at.started_at).total_seconds(),
                    summary=f"cancelled by {actor}",
                    log_path=at.log_path,
                )
            )
            st.active_tick = None
    # Wake the daemon's idle wait too, in case the spawn died fast and
    # the runner already returned to the top of the loop. This is a
    # no-op while the daemon is mid-tick (signal queues, applies at
    # next _idle_wait). Cheap and safe either way.
    _ds.notify_daemon()


@daemon_app.command("reset-failures")
def daemon_reset_failures(
    slug: str = typer.Argument(..., help="Slug whose failure counter to reset."),
    actor: str = typer.Option("human:cli", "--actor"),
) -> None:
    """Manually clear a slug's failure counter (operator override)."""
    from openharness.lab import daemon_state as _ds

    _ds.reset_failures(slug, actor=actor)
    woke = _ds.notify_daemon()
    typer.echo(
        f"failure counter cleared for {slug!r}"
        + (" (daemon notified)" if woke else "")
    )


@daemon_app.command("reset-all-failures")
def daemon_reset_all_failures(
    actor: str = typer.Option("human:cli", "--actor"),
) -> None:
    """Clear every recorded failure counter at once.

    Use after fixing a host-level cause (e.g. PATH, credentials)
    that broke a batch of slugs simultaneously, so you don't have
    to issue one ``reset-failures`` call per slug.
    """
    from openharness.lab import daemon_state as _ds

    _, cleared = _ds.reset_all_failures(actor=actor)
    woke = _ds.notify_daemon()
    typer.echo(
        f"cleared {cleared} failure counter(s)"
        + (" (daemon notified)" if woke else "")
    )


@daemon_app.command("clear-history")
def daemon_clear_history(
    actor: str = typer.Option("human:cli", "--actor"),
) -> None:
    """Wipe the tick-history ring buffer.

    Purely cosmetic — the daemon never reads history back into its
    decision loop, but the cockpit's "Recent ticks" panel renders
    from it. Useful for starting fresh after noisy debugging.
    """
    from openharness.lab import daemon_state as _ds

    _, removed = _ds.clear_history(actor=actor)
    woke = _ds.notify_daemon()
    typer.echo(
        f"cleared {removed} tick-history entry(ies)"
        + (" (daemon notified)" if woke else "")
    )


@daemon_app.command("state")
def daemon_state_show(
    json_out: bool = typer.Option(False, "--json", help="Raw JSON instead of pretty print."),
) -> None:
    """Print the current daemon-state.json snapshot."""
    from openharness.lab import daemon_state as _ds

    state = _ds.load()
    if json_out:
        from dataclasses import asdict as _asdict
        # Reuse the canonical serializer.
        typer.echo(json.dumps(_ds._state_to_dict(state), indent=2, default=str))
        return
    typer.echo(f"mode:       {state.mode}")
    typer.echo(f"approvals:  {state.approved_slugs or '(none)'}")
    typer.echo(f"active:     {state.active_tick.slug + ' / ' + state.active_tick.phase if state.active_tick else '(idle)'}")
    if state.entry_failures:
        typer.echo("failures:")
        for slug, rec in state.entry_failures.items():
            typer.echo(f"  {slug}: count={rec.count} last={rec.last_outcome}")
    if state.history:
        typer.echo(f"history:    {len(state.history)} entries (newest: {state.history[-1].slug} / {state.history[-1].outcome})")


# ---------------------------------------------------------------------------
# `lab runs ...` — disk-side cleanup of runs/experiments/<id>/ directories.
# ---------------------------------------------------------------------------
#
# Failed/cancelled experiments leave half-written run dirs behind. They
# don't break anything, but they accumulate, balloon disk usage, and
# make `ls runs/experiments/` noisy. These commands give the operator
# (and the web UI) a one-call cleanup.
#
# A run dir is considered **prunable** iff:
#   1. There is no ``results/summary.md`` (the canonical "experiment
#      finished cleanly" sentinel — written by the agg/finalize step),
#   2. AND the directory's mtime is older than ``--age-hours`` (default
#      1 h) so we never race a run that's currently being written.
#
# We never look at the running daemon's process tree to decide what to
# prune — pidfile coupling is fragile. The mtime gate is the single
# defence; the operator can always pass ``--age-hours 0 --force`` if
# they really mean "delete EVERYTHING unfinished right now" (e.g.
# after stopping the daemon).


def _experiments_root() -> Path:
    # Re-import on every call so tests that reload ``paths`` (with
    # the ``isolated_lab`` fixture) see the override. The cost is
    # negligible: a module attribute lookup, not an actual re-import.
    from openharness.lab import paths as _paths
    return _paths.REPO_ROOT / "runs" / "experiments"


def _is_prunable_run_dir(d: Path, *, age_hours: float) -> tuple[bool, str]:
    """Return (prunable?, reason) for a candidate run directory.

    Reason strings are short captions suitable for ``--dry-run`` output
    and for the web UI confirmation dialog. The function never deletes
    anything itself — only inspects.
    """
    if not d.is_dir():
        return False, "not a directory"
    summary = d / "results" / "summary.md"
    if summary.is_file():
        return False, "completed (results/summary.md present)"
    try:
        age_s = time.time() - d.stat().st_mtime
    except OSError as e:
        return False, f"stat failed: {e}"
    if age_s < age_hours * 3600:
        return False, f"too recent ({age_s/3600:.2f}h < {age_hours}h)"
    return True, f"orphan, age={age_s/3600:.1f}h"


@runs_app.command("list")
def runs_list(
    age_hours: float = typer.Option(
        1.0, "--age-hours",
        help="Minimum age before a dir is eligible. Default 1h.",
    ),
) -> None:
    """List runs/experiments/<id>/ dirs and their prune-eligibility."""
    root = _experiments_root()
    if not root.is_dir():
        typer.echo(f"(no experiments dir at {root})")
        return
    rows: list[tuple[str, bool, str]] = []
    for child in sorted(root.iterdir()):
        prunable, reason = _is_prunable_run_dir(child, age_hours=age_hours)
        rows.append((child.name, prunable, reason))
    if not rows:
        typer.echo("(no run directories)")
        return
    width = max(len(n) for n, _, _ in rows)
    for name, prunable, reason in rows:
        marker = "PRUNE" if prunable else "keep "
        typer.echo(f"  {marker}  {name:<{width}}  {reason}")


@runs_app.command("prune")
def runs_prune(
    age_hours: float = typer.Option(
        1.0, "--age-hours",
        help=(
            "Minimum age (hours since last write) before a dir is "
            "eligible. Default 1h prevents racing a live run; pass 0 "
            "with --force to wipe everything unfinished."
        ),
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="List what would be deleted without touching the disk.",
    ),
    force: bool = typer.Option(
        False, "--force",
        help="Required when --age-hours is below 1.",
    ),
    actor: str = typer.Option("human:cli", "--actor"),
) -> None:
    """Delete orphan / unfinished runs/experiments/<id>/ directories.

    Deletion is recursive (``rmtree``) — these directories can hold
    GB of legs/logs/, so this is the only realistic way to free the
    space. The audit row is appended to ``runs/lab/web_commands.jsonl``
    so the action remains traceable.
    """
    import shutil as _shutil

    if age_hours < 1 and not force:
        raise typer.BadParameter(
            "refusing to prune dirs younger than 1h without --force; "
            "stop the daemon first to be safe"
        )
    root = _experiments_root()
    if not root.is_dir():
        typer.echo(f"(no experiments dir at {root})")
        return
    deleted: list[str] = []
    skipped: list[tuple[str, str]] = []
    for child in sorted(root.iterdir()):
        prunable, reason = _is_prunable_run_dir(child, age_hours=age_hours)
        if not prunable:
            skipped.append((child.name, reason))
            continue
        if dry_run:
            typer.echo(f"  would delete  {child.name}  ({reason})")
            deleted.append(child.name)
            continue
        try:
            _shutil.rmtree(child)
            deleted.append(child.name)
            typer.echo(f"  deleted       {child.name}  ({reason})")
        except OSError as e:
            typer.echo(f"  FAILED        {child.name}  ({e})", err=True)
    typer.echo("")
    typer.echo(
        f"summary: {len(deleted)} {'would-be ' if dry_run else ''}"
        f"deleted, {len(skipped)} skipped (actor={actor})"
    )


# ===== preflight (Phase 0 of the lab pipeline) ==============================


@preflight_app.command("run")
def cmd_preflight_run(
    slug: str,
    base_branch: Optional[str] = typer.Option(
        None, "--base-branch",
        help="Branch to base the worktree on. Defaults to the parent "
             "repo's current HEAD branch.",
    ),
    auto_push: bool = typer.Option(
        False, "--auto-push",
        help="Push any unpushed commits on the base branch first.",
    ),
) -> None:
    """Create the worktree for `slug` (idempotent).

    Equivalent to what the orchestrator does in phase 0, callable
    by hand for debugging or to pre-warm a slug before promoting it
    to Up next.
    """
    from openharness.lab import preflight as preflight_mod
    try:
        result = preflight_mod.run_preflight(
            slug, base_branch=base_branch, auto_push=auto_push,
            allow_lab_markdown_dirty=False,
        )
    except preflight_mod.PreflightError as exc:
        typer.echo(f"[red]preflight failed: {exc}[/red]")
        raise typer.Exit(1)
    typer.echo(f"worktree   : {result.info.path}")
    typer.echo(f"branch     : {result.info.branch}")
    typer.echo(f"base       : {result.base_branch} @ {result.base_sha[:8]}")


@preflight_app.command("remove")
def cmd_preflight_remove(
    slug: str,
    keep_branch: bool = typer.Option(
        False, "--keep-branch",
        help="Don't delete the lab/<slug> branch after removing the worktree.",
    ),
) -> None:
    """Tear down the worktree (and branch) for `slug` (idempotent)."""
    from openharness.lab import preflight as preflight_mod
    removed = preflight_mod.remove_worktree(
        slug, delete_branch=not keep_branch, force=True,
    )
    typer.echo("removed" if removed else "(nothing to remove)")


@preflight_app.command("list")
def cmd_preflight_list() -> None:
    """List every git worktree the parent repo currently knows about."""
    from openharness.lab import preflight as preflight_mod
    paths = preflight_mod.list_worktrees()
    for p in paths:
        typer.echo(str(p))


# ===== phases (per-slug pipeline state) =====================================


@phases_app.command("show")
def cmd_phases_show(
    slug: Optional[str] = typer.Argument(
        None,
        help="Slug to inspect. Omit to list every slug with state on disk.",
    ),
) -> None:
    """Print the phase status for `slug` (or list all known slugs)."""
    from openharness.lab import phase_state
    if slug is None:
        for s in phase_state.all_slugs():
            state = phase_state.load(s)
            if state is None:
                continue
            done = sum(1 for p in phase_state.PHASE_ORDER
                       if p in state.phases and state.phases[p].status in ("ok", "skipped"))
            current = state.first_unfinished() or "done"
            typer.echo(f"  {s:50s} {done}/{len(phase_state.PHASE_ORDER)}  next={current}")
        return
    state = phase_state.load(slug)
    if state is None:
        typer.echo(f"(no state for {slug!r})")
        raise typer.Exit(1)
    typer.echo(f"slug          : {state.slug}")
    typer.echo(f"started_at    : {state.started_at}")
    typer.echo(f"last_updated  : {state.last_updated_at}")
    typer.echo(f"needs_variant : {state.needs_variant}")
    typer.echo("")
    for phase_name in phase_state.PHASE_ORDER:
        rec = state.phases.get(phase_name)
        if rec is None:
            typer.echo(f"  {phase_name:12s}  pending")
            continue
        line = f"  {phase_name:12s}  {rec.status:9s}"
        if rec.finished_at:
            line += f"  finished={rec.finished_at}"
        if rec.error:
            line += f"  err={rec.error[:80]!r}"
        typer.echo(line)


@phases_app.command("reset")
def cmd_phases_reset(
    slug: str,
    phase: Optional[str] = typer.Option(
        None, "--phase",
        help="Reset only this phase (preflight | design | implement | "
             "run | critique | finalize). Omit to delete the entire "
             "phases.json.",
    ),
) -> None:
    """Reset (drop) one phase's record, or the whole document."""
    from openharness.lab import phase_state
    if phase is None:
        phase_state.reset_all(slug)
        typer.echo(f"reset all phases for {slug!r}")
        return
    if phase not in phase_state.PHASE_ORDER:
        typer.echo(
            f"[red]unknown phase {phase!r}; valid: "
            f"{', '.join(phase_state.PHASE_ORDER)}[/red]"
        )
        raise typer.Exit(2)
    phase_state.reset_phase(slug, phase)  # type: ignore[arg-type]
    typer.echo(f"reset phase {phase!r} for {slug!r}")


def main() -> None:  # pragma: no cover - thin wrapper
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
