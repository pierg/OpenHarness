"""Command line interface for OpenHarness experiments."""

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Literal

import typer

from openharness.experiments.manifest import LegStatus
from openharness.experiments.plan import plan_experiment
from openharness.experiments.results import collect_results, summarize_results, write_results
from openharness.experiments.runner import run_experiment
from openharness.experiments.spec import load_experiment_spec
from openharness.experiments.logging import setup_experiment_logging
from openharness.observability.langfuse import langfuse_agent_env_for_docker

app = typer.Typer(help="Run and manage OpenHarness experiments.")


@app.command()
def run(
    spec: Path = typer.Argument(..., exists=True, dir_okay=False, help="Path to experiment YAML"),
    profile: str | None = typer.Option(None, "--profile", help="Profile to apply from the YAML"),
    instance_id: str | None = typer.Option(
        None, "--instance-id", help="Experiment instance ID (defaults to timestamp)"
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
    """Run an experiment from a declarative YAML spec."""
    experiment_spec = load_experiment_spec(spec, profile=profile)

    if instance_id is None:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        prof_suffix = f"-{profile}" if profile else ""
        instance_id = f"{experiment_spec.id}{prof_suffix}-{timestamp}"

    experiment_root = root or Path(f"runs/experiments/{instance_id}")
    experiment_root.mkdir(parents=True, exist_ok=True)

    setup_experiment_logging(experiment_root / "logs" / "runner.log")

    env = langfuse_agent_env_for_docker() if langfuse else {}

    if fail_fast and not experiment_spec.fail_fast:
        experiment_spec = experiment_spec.model_copy(update={"fail_fast": True})

    typer.echo(f"Starting experiment {experiment_spec.id} (Instance: {instance_id})")
    typer.echo(f"Root: {experiment_root}")
    typer.echo(f"Agents: {', '.join(a.alias or a.id for a in experiment_spec.agents)}")
    typer.echo(f"Leg concurrency: {experiment_spec.leg_concurrency}")
    typer.echo(f"Trials per agent: {experiment_spec.task_filter.n_tasks if experiment_spec.task_filter.n_tasks is not None else 'all'} (concurrent: {experiment_spec.defaults.n_concurrent or 1})")
    typer.echo("-" * 40)

    manifest = asyncio.run(
        run_experiment(
            experiment_spec,
            experiment_root=experiment_root,
            instance_id=instance_id,
            env=env,
            dry_run=dry_run,
            resume=resume,
            emit_results=emit_results,
        )
    )

    if any(leg.status == LegStatus.FAILED for leg in manifest.legs):
        raise typer.Exit(code=1)


@app.command()
def status(
    root: Path = typer.Argument(..., exists=True, file_okay=False, help="Path to experiment root"),
):
    """Print the current status of an experiment."""
    from openharness.experiments.manifest import ExperimentManifest

    manifest_path = root / "experiment.json"
    if not manifest_path.exists():
        typer.echo(f"No experiment.json found in {root}", err=True)
        raise typer.Exit(1)

    manifest = ExperimentManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))

    typer.echo(f"Experiment: {manifest.experiment_id} (Instance: {manifest.instance_id})")
    typer.echo(f"Created: {manifest.created_at.isoformat()}")
    typer.echo(f"Updated: {manifest.updated_at.isoformat()}")
    typer.echo("\nLegs:")
    for leg in manifest.legs:
        typer.echo(f"  - {leg.leg_id}: {leg.status.value.upper()}")
        if leg.error:
            typer.echo(f"      Error: {leg.error}")


@app.command("results")
def results_command(
    root: Path = typer.Argument(..., exists=True, file_okay=False, help="Path to experiment root"),
    fmt: Literal["json", "csv", "md"] = typer.Option("md", "--fmt", help="Output format"),
):
    """Generate and print results from an experiment."""
    from openharness.experiments.manifest import ExperimentManifest

    manifest_path = root / "experiment.json"
    if not manifest_path.exists():
        typer.echo(f"No experiment.json found in {root}", err=True)
        raise typer.Exit(1)

    manifest = ExperimentManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))

    rows = collect_results(manifest, experiment_root=root)
    summary = summarize_results(rows)

    write_results(rows, summary, experiment_root=root)

    if fmt == "md":
        typer.echo((root / "results" / "summary.md").read_text(encoding="utf-8"))
    elif fmt == "json":
        typer.echo((root / "results" / "rows.json").read_text(encoding="utf-8"))
    elif fmt == "csv":
        typer.echo((root / "results" / "rows.csv").read_text(encoding="utf-8"))


@app.command()
def plan(
    spec: Path = typer.Argument(..., exists=True, dir_okay=False, help="Path to experiment YAML"),
    profile: str | None = typer.Option(None, "--profile", help="Profile to apply from the YAML"),
):
    """Print the resolved experiment plan without running."""
    experiment_spec = load_experiment_spec(spec, profile=profile)
    plan_obj = plan_experiment(experiment_spec, instance_id="plan-dry-run")

    typer.echo(plan_obj.model_dump_json(indent=2))


if __name__ == "__main__":
    app()
