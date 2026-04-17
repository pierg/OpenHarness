"""Shorthand entry points for the CLI to be registered in pyproject.toml."""

import typer
from pathlib import Path
import sys

from openharness.experiments.cli import (
    plan as cli_plan,
    rerun as cli_rerun,
    results_command as cli_results,
    run as cli_run,
    status as cli_status,
)


def resolve_spec_path(name_or_path: str) -> Path:
    p = Path(name_or_path)
    if p.is_file():
        return p
    exp_dir = Path("experiments")
    for ext in (".yaml", ".yml"):
        candidate = exp_dir / f"{name_or_path}{ext}"
        if candidate.is_file():
            return candidate
    print(f"Error: Could not find experiment spec for '{name_or_path}'", file=sys.stderr)
    raise typer.Exit(1)


def resolve_run_root(name_or_path: str) -> Path:
    p = Path(name_or_path)
    if p.is_dir() and (p / "experiment.json").exists():
        return p
    runs_dir = Path("runs/experiments")

    # Exact match
    candidate = runs_dir / name_or_path
    if candidate.is_dir() and (candidate / "experiment.json").exists():
        return candidate

    # Latest match by prefix
    if runs_dir.exists():
        candidates = []
        for d in runs_dir.iterdir():
            # If the user asks for "tb2-baseline", we match "tb2-baseline-20260416-..."
            # We also match if the user asks for "tb2-baseline-demo"
            if (
                d.is_dir()
                and d.name.startswith(f"{name_or_path}-")
                and (d / "experiment.json").exists()
            ):
                candidates.append(d)
        if candidates:
            # Sort lexically (timestamp makes it chronological)
            candidates.sort(key=lambda x: x.name, reverse=True)
            return candidates[0]

    print(f"Error: Could not find experiment run root for '{name_or_path}'", file=sys.stderr)
    raise typer.Exit(1)


def exec_app():
    typer.run(exec_cmd)


def exec_cmd(
    name: str = typer.Argument(..., help="Experiment ID (e.g. tb2-baseline) or path to YAML"),
    profile: str | None = typer.Option(None, "--profile", help="Profile to apply from the YAML"),
    instance_id: str | None = typer.Option(
        None,
        "--instance-id",
        help="Experiment instance ID (defaults to timestamped name). Use 'latest' to resume the most recent run.",
    ),
    root: Path | None = typer.Option(
        None,
        "--root",
        help="Root directory for the run (defaults to runs/experiments/<instance-id>)",
    ),
    resume: bool = typer.Option(
        True, "--resume/--no-resume", help="Resume completed trials from previous run"
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Dry run without launching Harbor"),
    langfuse: bool = typer.Option(
        True, "--langfuse/--no-langfuse", help="Pass Langfuse credentials to agents"
    ),
    fail_fast: bool = typer.Option(False, "--fail-fast", help="Stop on first leg failure"),
    emit_results: bool = typer.Option(
        True, "--results/--no-results", help="Emit summary results after run"
    ),
):
    spec_path = resolve_spec_path(name)
    prof_suffix = f"-{profile}" if profile else ""
    base_name = name if not name.endswith((".yaml", ".yml")) else spec_path.stem

    if instance_id == "latest":
        search_prefix = f"{base_name}{prof_suffix}"
        runs_dir = Path("runs/experiments")
        if runs_dir.exists():
            candidates = []
            for d in runs_dir.iterdir():
                if (
                    d.is_dir()
                    and d.name.startswith(f"{search_prefix}-")
                    and (d / "experiment.json").exists()
                ):
                    candidates.append(d)
            if candidates:
                candidates.sort(key=lambda x: x.name, reverse=True)
                instance_id = candidates[0].name
            else:
                print(
                    f"Error: Could not find latest run for '{search_prefix}' to resume",
                    file=sys.stderr,
                )
                raise typer.Exit(1)
        else:
            print(
                f"Error: Could not find latest run for '{search_prefix}' to resume", file=sys.stderr
            )
            raise typer.Exit(1)
    elif instance_id is None:
        from datetime import datetime

        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        instance_id = f"{base_name}{prof_suffix}-{timestamp}"

    cli_run(
        spec=spec_path,
        profile=profile,
        instance_id=instance_id,
        root=root,
        resume=resume,
        dry_run=dry_run,
        langfuse=langfuse,
        fail_fast=fail_fast,
        emit_results=emit_results,
    )


def status_app():
    typer.run(status_cmd)


def status_cmd(
    name: str = typer.Argument(
        ..., help="Experiment ID (e.g. tb2-baseline) or path to run directory"
    ),
):
    root = resolve_run_root(name)
    cli_status(root=root)


def results_app():
    typer.run(results_cmd)


def results_cmd(
    name: str = typer.Argument(
        ..., help="Experiment ID (e.g. tb2-baseline) or path to run directory"
    ),
    fmt: str = typer.Option("md", "--fmt", help="Output format (json, csv, md)"),
):
    root = resolve_run_root(name)
    cli_results(root=root, fmt=fmt)  # type: ignore


def plan_app():
    typer.run(plan_cmd)


def plan_cmd(
    name: str = typer.Argument(..., help="Experiment ID (e.g. tb2-baseline) or path to YAML"),
    profile: str | None = typer.Option(None, "--profile", help="Profile to apply from the YAML"),
):
    spec_path = resolve_spec_path(name)
    cli_plan(spec=spec_path, profile=profile)


def rerun_app():
    typer.run(rerun_cmd)


def rerun_cmd(
    name: str = typer.Argument(
        ...,
        help=(
            "Experiment instance id (e.g. tb2-baseline-smoke-20260416-151537), "
            "experiment id prefix (e.g. tb2-baseline — picks the latest run), "
            "or path to a run root."
        ),
    ),
    leg: list[str] = typer.Option(
        None,
        "--leg",
        "-l",
        help="Leg id to re-run (repeatable). When set, --status is ignored.",
    ),
    status_filter: list[str] = typer.Option(
        None,
        "--status",
        "-s",
        help="Trial-level result statuses to re-run (repeatable).",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Show which legs would be wiped and re-run without doing anything.",
    ),
    langfuse: bool = typer.Option(
        True, "--langfuse/--no-langfuse", help="Pass Langfuse credentials to agents"
    ),
    fail_fast: bool = typer.Option(False, "--fail-fast", help="Stop on first leg failure"),
    emit_results: bool = typer.Option(
        True, "--results/--no-results", help="Emit summary results after run"
    ),
):
    root = resolve_run_root(name)
    cli_rerun(
        root=root,
        leg=leg or [],
        status_filter=status_filter or [],
        dry_run=dry_run,
        langfuse=langfuse,
        fail_fast=fail_fast,
        emit_results=emit_results,
    )
