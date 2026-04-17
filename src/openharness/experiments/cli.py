"""Command line interface for OpenHarness experiments."""

import asyncio
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Literal

import typer
from dotenv import load_dotenv

load_dotenv()

from openharness.experiments.manifest import (  # noqa: E402
    ExperimentManifest,
    LegRecord,
    LegResultStatus,
    LegStatus,
)
from openharness.experiments.plan import plan_experiment  # noqa: E402
from openharness.experiments.results import (  # noqa: E402
    ResultsSummary,
    collect_results,
    summarize_results,
    write_results,
)
from openharness.experiments.runner import run_experiment  # noqa: E402
from openharness.experiments.spec import (  # noqa: E402
    load_experiment_spec,
    load_experiment_spec_full,
)
from openharness.experiments.logging import setup_experiment_logging  # noqa: E402
from openharness.observability.langfuse import (  # noqa: E402
    create_trace_observer,
    langfuse_agent_env_for_docker,
)

app = typer.Typer(help="Run and manage OpenHarness experiments.")

log = logging.getLogger(__name__)


def preflight_check(langfuse: bool = True, docker: bool = True):
    """Run pre-flight checks before launching an experiment."""
    if langfuse:
        try:
            # We use a short timeout for the auth check to avoid hanging
            import os

            if os.environ.get("OPENHARNESS_LANGFUSE_ENABLED") == "0":
                log.info("Langfuse is explicitly disabled via env var.")
            elif not os.environ.get("LANGFUSE_PUBLIC_KEY") or not os.environ.get(
                "LANGFUSE_SECRET_KEY"
            ):
                log.warning("Langfuse keys are missing. Tracing will be disabled.")
            else:
                log.info("Checking Langfuse connection...")
                # create_trace_observer does an auth_check if required=True
                create_trace_observer(
                    session_id="preflight",
                    interface="cli",
                    cwd=str(Path.cwd()),
                    model="preflight",
                    required=True,
                )
                log.info("Langfuse connection OK.")
        except Exception as exc:
            typer.echo(f"Error: Langfuse pre-flight check failed: {exc}", err=True)
            typer.echo("Check your LANGFUSE_BASE_URL and API keys.", err=True)
            raise typer.Exit(1)

    if docker:
        import subprocess

        try:
            log.info("Checking Docker daemon...")
            subprocess.run(
                ["docker", "info"], capture_output=True, check=True, timeout=10
            )
            log.info("Docker daemon OK.")
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
            typer.echo(
                "Error: Docker is not running or not accessible. Harbor requires Docker.",
                err=True,
            )
            raise typer.Exit(1)


@app.command()
def preflight(
    langfuse: bool = typer.Option(
        True, "--langfuse/--no-langfuse", help="Check Langfuse connection"
    ),
    docker: bool = typer.Option(True, "--docker/--no-docker", help="Check Docker daemon"),
):
    """Run pre-flight checks for connections and environment."""
    preflight_check(langfuse=langfuse, docker=docker)
    typer.echo("All pre-flight checks passed.")


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
    loaded = load_experiment_spec_full(spec, profile=profile)
    experiment_spec = loaded.spec

    if instance_id is None:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        prof_suffix = f"-{profile}" if profile else ""
        instance_id = f"{experiment_spec.id}{prof_suffix}-{timestamp}"

    experiment_root = root or Path(f"runs/experiments/{instance_id}")
    experiment_root.mkdir(parents=True, exist_ok=True)

    setup_experiment_logging(experiment_root / "logs" / "runner.log")

    if not dry_run:
        preflight_check(langfuse=langfuse, docker=True)

    env = langfuse_agent_env_for_docker() if langfuse else {}

    if fail_fast and not experiment_spec.fail_fast:
        experiment_spec = experiment_spec.model_copy(update={"fail_fast": True})

    agents_list = ", ".join(a.alias or a.id for a in experiment_spec.agents)
    n_tasks_str = (
        experiment_spec.task_filter.n_tasks
        if experiment_spec.task_filter.n_tasks is not None
        else "all"
    )
    log.info(
        "Starting experiment %s instance=%s agents=[%s] leg_concurrency=%s trials=%s",
        experiment_spec.id,
        instance_id,
        agents_list,
        experiment_spec.leg_concurrency,
        n_tasks_str,
    )

    manifest = asyncio.run(
        run_experiment(
            experiment_spec,
            experiment_root=experiment_root,
            instance_id=instance_id,
            env=env,
            dry_run=dry_run,
            resume=resume,
            emit_results=emit_results,
            loaded_spec=loaded,
        )
    )

    if emit_results:
        rows = collect_results(manifest, experiment_root=experiment_root)
        summary = summarize_results(rows)
        _print_summary(experiment_spec.id, instance_id, experiment_root, summary, manifest)

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
        status_str = leg.status.value.upper()
        result_str = f" [{leg.result_status.value}]" if leg.result_status is not None else ""
        typer.echo(f"  - {leg.leg_id}: {status_str}{result_str}")
        if leg.aggregate is not None:
            agg = leg.aggregate
            typer.echo(
                f"      Trials: {agg.n_trials} | Passed: {agg.n_passed} | "
                f"Failed: {agg.n_failed} | Errored: {agg.n_errored}"
            )
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


# Default leg-result statuses considered "failed-ish" and worth re-running.
DEFAULT_RERUN_STATUSES: frozenset[str] = frozenset(
    {
        LegResultStatus.ALL_FAILED.value,
        LegResultStatus.ALL_ERRORED.value,
        LegResultStatus.PARTIAL.value,
        LegResultStatus.NO_TRIALS.value,
    }
)


def select_legs_to_rerun(
    manifest: ExperimentManifest,
    *,
    only_legs: Iterable[str] | None = None,
    statuses: Iterable[str] | None = None,
) -> list[LegRecord]:
    """Return the subset of ``manifest.legs`` to re-run.

    Selection rules:
    - ``only_legs`` (if provided) takes precedence and selects exactly
      those leg ids — no status filter is applied.
    - Otherwise, a leg is selected when it failed at the leg level
      (``status in {FAILED, INTERRUPTED, PENDING, RUNNING}``) **or** when
      its trial-level ``result_status`` is in ``statuses`` (defaulting
      to ``DEFAULT_RERUN_STATUSES``).
    - Legs that succeeded with ``ALL_PASSED`` are never re-run.
    """
    only_set = set(only_legs) if only_legs else None
    if only_set is not None:
        return [leg for leg in manifest.legs if leg.leg_id in only_set]

    status_set = set(statuses) if statuses is not None else set(DEFAULT_RERUN_STATUSES)
    failed_leg_statuses = {
        LegStatus.FAILED,
        LegStatus.INTERRUPTED,
        LegStatus.PENDING,
        LegStatus.RUNNING,
    }

    selected: list[LegRecord] = []
    for leg in manifest.legs:
        if leg.status in failed_leg_statuses:
            selected.append(leg)
            continue
        if leg.result_status is not None and leg.result_status.value in status_set:
            selected.append(leg)
    return selected


@app.command()
def rerun(
    root: Path = typer.Argument(
        ...,
        exists=True,
        file_okay=False,
        help="Path to experiment root (use the `rerun` shorthand to pass a bare instance id).",
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
        help=(
            "Trial-level result statuses to re-run. Repeatable. "
            f"Defaults to {sorted(DEFAULT_RERUN_STATUSES)}."
        ),
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
    """Re-run failed legs from a previous experiment in-place.

    Reads the resolved spec from ``<root>/config.resolved.yaml`` and the
    manifest from ``<root>/experiment.json``, picks the legs to re-run
    (failed / errored / partial / interrupted by default — overridable
    via --leg or --status), wipes their leg directories, and resumes
    against the same instance id so passed legs stay cached.
    """
    manifest_path = root / "experiment.json"
    if not manifest_path.exists():
        typer.echo(f"No experiment.json found in {root}", err=True)
        raise typer.Exit(1)

    resolved_spec_path = root / "config.resolved.yaml"
    if not resolved_spec_path.exists():
        typer.echo(f"No config.resolved.yaml found in {root}", err=True)
        raise typer.Exit(1)

    manifest = ExperimentManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))

    if leg:
        unknown = sorted(set(leg) - {leg_record.leg_id for leg_record in manifest.legs})
        if unknown:
            typer.echo(
                f"Unknown leg id(s): {', '.join(unknown)}. "
                f"Available: {', '.join(sorted({lr.leg_id for lr in manifest.legs}))}",
                err=True,
            )
            raise typer.Exit(1)

    to_rerun = select_legs_to_rerun(
        manifest,
        only_legs=leg or None,
        statuses=status_filter or None,
    )

    if not to_rerun:
        typer.echo("No legs match the rerun criteria — nothing to do.")
        raise typer.Exit(0)

    typer.echo(f"Experiment: {manifest.experiment_id} (instance: {manifest.instance_id})")
    typer.echo(f"Root: {root}")
    typer.echo(f"Legs to re-run ({len(to_rerun)}):")
    for leg_record in to_rerun:
        result_str = (
            leg_record.result_status.value if leg_record.result_status is not None else "n/a"
        )
        typer.echo(
            f"  - {leg_record.leg_id}  [status={leg_record.status.value} result={result_str}]"
        )

    if dry_run:
        typer.echo("\n--dry-run: not wiping or re-running anything.")
        return

    for leg_record in to_rerun:
        leg_dir = root / "legs" / leg_record.leg_id
        if leg_dir.exists():
            shutil.rmtree(leg_dir)

    loaded = load_experiment_spec_full(resolved_spec_path)
    experiment_spec = loaded.spec
    if fail_fast and not experiment_spec.fail_fast:
        experiment_spec = experiment_spec.model_copy(update={"fail_fast": True})

    setup_experiment_logging(root / "logs" / "runner.log")

    if not dry_run:
        preflight_check(langfuse=langfuse, docker=True)

    env = langfuse_agent_env_for_docker() if langfuse else {}

    log.info(
        "Re-running experiment %s instance=%s legs=[%s]",
        experiment_spec.id,
        manifest.instance_id,
        ", ".join(lr.leg_id for lr in to_rerun),
    )

    new_manifest = asyncio.run(
        run_experiment(
            experiment_spec,
            experiment_root=root,
            instance_id=manifest.instance_id,
            env=env,
            dry_run=False,
            resume=True,
            emit_results=emit_results,
            loaded_spec=loaded,
        )
    )

    if emit_results:
        rows = collect_results(new_manifest, experiment_root=root)
        summary = summarize_results(rows)
        _print_summary(experiment_spec.id, manifest.instance_id, root, summary, new_manifest)

    if any(leg_record.status == LegStatus.FAILED for leg_record in new_manifest.legs):
        raise typer.Exit(code=1)


def _print_summary(
    experiment_id: str,
    instance_id: str,
    experiment_root: Path,
    summary: ResultsSummary,
    manifest,
) -> None:
    typer.echo("")
    typer.echo("=" * 72)
    typer.echo(f"Experiment {experiment_id} (instance: {instance_id}) complete.")
    typer.echo(f"Root: {experiment_root}")
    typer.echo("=" * 72)

    if not summary.by_leg:
        typer.echo("No trials recorded.")
        return

    for leg_id, stats in summary.by_leg.items():
        leg_record = next((leg for leg in manifest.legs if leg.leg_id == leg_id), None)
        result_status = (
            leg_record.result_status.value if leg_record and leg_record.result_status else "unknown"
        )
        pass_rate = f"{stats.pass_rate * 100:.1f}%" if stats.pass_rate is not None else "n/a"
        mean_score = f"{stats.mean_score:.3f}" if stats.mean_score is not None else "n/a"
        phase_str = (
            ", ".join(f"{k}={v}" for k, v in sorted(stats.n_errored_by_phase.items()))
            if stats.n_errored_by_phase
            else "-"
        )
        typer.echo(
            f"  {leg_id:32s} result={result_status:11s} "
            f"trials={stats.n_trials:3d} pass={stats.n_passed:3d} "
            f"fail={stats.n_failed:3d} err={stats.n_errored:3d} "
            f"(phases: {phase_str})"
        )
        typer.echo(
            f"    pass_rate={pass_rate} mean_score={mean_score} "
            f"tokens={stats.total_tokens} cost_usd={stats.total_cost_usd:.4f}"
        )

    totals = _totals(summary)
    typer.echo("-" * 72)
    typer.echo(
        f"  TOTAL{'':27s} trials={totals[0]:3d} pass={totals[1]:3d} "
        f"fail={totals[2]:3d} err={totals[3]:3d}"
    )
    typer.echo(f"\nSummary written to {experiment_root / 'results' / 'summary.md'}")


def _totals(summary: ResultsSummary) -> tuple[int, int, int, int]:
    n_trials = sum(s.n_trials for s in summary.by_leg.values())
    n_passed = sum(s.n_passed for s in summary.by_leg.values())
    n_failed = sum(s.n_failed for s in summary.by_leg.values())
    n_errored = sum(s.n_errored for s in summary.by_leg.values())
    return n_trials, n_passed, n_failed, n_errored


if __name__ == "__main__":
    app()
