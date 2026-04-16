"""Experiment runner and orchestrator."""

from __future__ import annotations

import asyncio
import logging
import traceback
from datetime import datetime
from pathlib import Path
from typing import Mapping

import yaml
from rich.console import Console

from openharness.experiments.backends import Backend, LegContext
from openharness.experiments.backends.harbor import HarborBackend
from openharness.experiments.manifest import (
    ExperimentManifest,
    LegRecord,
    LegStatus,
)
from openharness.experiments.paths import make_rel
from openharness.experiments.plan import ExperimentPlan, Leg, plan_experiment
from openharness.experiments.reproducibility import collect_reproducibility
from openharness.experiments.spec import ExperimentSpec

log = logging.getLogger(__name__)


def _get_default_backend(spec: ExperimentSpec) -> Backend:
    return HarborBackend()


def _initialize_experiment_tree(
    experiment_root: Path, spec: ExperimentSpec, plan: ExperimentPlan
) -> None:
    experiment_root.mkdir(parents=True, exist_ok=True)

    spec_path = experiment_root / "config.source.yaml"
    if not spec_path.exists():
        spec_path.write_text(
            yaml.safe_dump(spec.model_dump(mode="json", exclude_none=True), sort_keys=False),
            encoding="utf-8",
        )

    for leg in plan.legs:
        (experiment_root / "legs" / leg.leg_id).mkdir(parents=True, exist_ok=True)


def _load_or_seed_manifest(experiment_root: Path, plan: ExperimentPlan) -> ExperimentManifest:
    manifest_path = experiment_root / "experiment.json"
    if manifest_path.exists():
        try:
            return ExperimentManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
        except Exception as e:
            log.warning(f"Failed to load existing manifest: {e}. Starting fresh.")

    now = datetime.now()

    resolved_spec_path = experiment_root / "config.resolved.yaml"
    resolved_spec_path.write_text(
        yaml.safe_dump(plan.spec.model_dump(mode="json", exclude_none=True), sort_keys=False),
        encoding="utf-8",
    )

    legs = []
    for leg in plan.legs:
        legs.append(
            LegRecord(
                leg_id=leg.leg_id,
                agent_id=leg.agent_id,
                status=LegStatus.PENDING,
                started_at=None,
                finished_at=None,
                duration_sec=None,
                harbor_dir=None,
                harbor_result_path=None,
                agent_config_path=None,
                trials=(),
            )
        )

    return ExperimentManifest(
        experiment_id=plan.spec.id,
        instance_id=plan.instance_id,
        dataset=plan.spec.dataset,
        spec_path=make_rel(experiment_root, experiment_root / "config.source.yaml"),
        resolved_spec_path=make_rel(experiment_root, resolved_spec_path),
        created_at=now,
        updated_at=now,
        reproducibility=collect_reproducibility(),
        legs=tuple(legs),
    )


def _persist_manifest(manifest: ExperimentManifest, experiment_root: Path) -> None:
    manifest_path = experiment_root / "experiment.json"
    manifest_path.write_text(manifest.model_dump_json(indent=2) + "\n", encoding="utf-8")


def _upsert_leg(
    manifest: ExperimentManifest, record: LegRecord, experiment_root: Path
) -> ExperimentManifest:
    updated_legs = []
    for existing in manifest.legs:
        if existing.leg_id == record.leg_id:
            updated_legs.append(record)
        else:
            updated_legs.append(existing)

    leg_json = experiment_root / "legs" / record.leg_id / "leg.json"
    leg_json.write_text(record.model_dump_json(indent=2) + "\n", encoding="utf-8")

    return manifest.model_copy(
        update={
            "legs": tuple(updated_legs),
            "updated_at": datetime.now(),
        }
    )


async def run_experiment(
    spec: ExperimentSpec,
    *,
    experiment_root: Path,
    instance_id: str,
    env: Mapping[str, str] | None = None,
    dry_run: bool = False,
    resume: bool = False,
    backend: Backend | None = None,
    emit_results: bool = True,
) -> ExperimentManifest:
    plan = plan_experiment(spec, instance_id=instance_id, cwd=experiment_root)
    backend = backend or _get_default_backend(spec)
    env = env or {}

    _initialize_experiment_tree(experiment_root, spec, plan)
    manifest = _load_or_seed_manifest(experiment_root, plan)
    _persist_manifest(manifest, experiment_root)

    semaphore = asyncio.Semaphore(spec.leg_concurrency)
    console = Console()

    async def run_one(leg: Leg) -> LegRecord:
        async with semaphore:
            ctx = LegContext(
                experiment_root=experiment_root,
                leg_dir=experiment_root / "legs" / leg.leg_id,
                env=dict(env),
                dry_run=dry_run,
                resume=resume,
                spec=spec,
            )

            if dry_run:
                return LegRecord(
                    leg_id=leg.leg_id,
                    agent_id=leg.agent_id,
                    status=LegStatus.DRY_RUN,
                    started_at=None,
                    finished_at=None,
                    duration_sec=None,
                    harbor_dir=None,
                    harbor_result_path=None,
                    agent_config_path=None,
                )

            if resume and backend.is_leg_complete(leg, ctx):
                # When skipping, we could also collect existing trials.
                # For now, mark skipped. The results collector will read from disk.
                now_str = datetime.now().strftime("%H:%M:%S")
                console.print(f"[[dim]{now_str}[/dim]] ⏭️  Leg [bold cyan]{leg.leg_id}[/bold cyan] "
                              f"already complete, [yellow]skipping[/yellow].")
                return LegRecord(
                    leg_id=leg.leg_id,
                    agent_id=leg.agent_id,
                    status=LegStatus.SKIPPED,
                    started_at=None,
                    finished_at=None,
                    duration_sec=None,
                    harbor_dir=make_rel(experiment_root, ctx.leg_dir / "harbor"),
                    harbor_result_path=make_rel(
                        experiment_root, ctx.leg_dir / "harbor" / leg.harbor_run_id / "result.json"
                    ),
                    agent_config_path=make_rel(
                        experiment_root, ctx.leg_dir / "agent.resolved.yaml"
                    ),
                )

            running_record = LegRecord(
                leg_id=leg.leg_id,
                agent_id=leg.agent_id,
                status=LegStatus.RUNNING,
                started_at=datetime.now(),
                finished_at=None,
                duration_sec=None,
                harbor_dir=make_rel(experiment_root, ctx.leg_dir / "harbor"),
                harbor_result_path=make_rel(
                    experiment_root, ctx.leg_dir / "harbor" / leg.harbor_run_id / "result.json"
                ),
                agent_config_path=make_rel(experiment_root, ctx.leg_dir / "agent.resolved.yaml"),
            )
            nonlocal manifest
            manifest = _upsert_leg(manifest, running_record, experiment_root)
            _persist_manifest(manifest, experiment_root)

            now_str = datetime.now().strftime("%H:%M:%S")
            console.print(f"[[dim]{now_str}[/dim]] 🚀 Starting leg [bold cyan]{leg.leg_id}[/bold cyan] "
                          f"([dim]agent:[/dim] {leg.agent_id}, [dim]trials:[/dim] {leg.n_attempts * leg.n_concurrent})")

            try:
                outcome = await backend.run_leg(leg, ctx)

                if spec.fail_fast and outcome.status == LegStatus.FAILED:
                    raise RuntimeError(f"Leg {leg.leg_id} failed: {outcome.error}")

            except asyncio.CancelledError:
                dur = (datetime.now() - running_record.started_at).total_seconds()
                now_str = datetime.now().strftime("%H:%M:%S")
                console.print(f"[[dim]{now_str}[/dim]] 🛑 Leg [bold cyan]{leg.leg_id}[/bold cyan] "
                              f"was [yellow]interrupted[/yellow] after {dur:.1f}s")
                return LegRecord(
                    leg_id=leg.leg_id,
                    agent_id=leg.agent_id,
                    status=LegStatus.INTERRUPTED,
                    started_at=running_record.started_at,
                    finished_at=datetime.now(),
                    duration_sec=dur,
                    harbor_dir=running_record.harbor_dir,
                    harbor_result_path=running_record.harbor_result_path,
                    agent_config_path=running_record.agent_config_path,
                )
            except Exception as exc:
                dur = (datetime.now() - running_record.started_at).total_seconds()
                now_str = datetime.now().strftime("%H:%M:%S")
                console.print(f"[[dim]{now_str}[/dim]] 💥 Leg [bold cyan]{leg.leg_id}[/bold cyan] "
                              f"[red]failed[/red] after {dur:.1f}s: {exc}")
                return LegRecord(
                    leg_id=leg.leg_id,
                    agent_id=leg.agent_id,
                    status=LegStatus.FAILED,
                    started_at=running_record.started_at,
                    finished_at=datetime.now(),
                    duration_sec=dur,
                    harbor_dir=running_record.harbor_dir,
                    harbor_result_path=running_record.harbor_result_path,
                    agent_config_path=running_record.agent_config_path,
                    error=str(exc),
                    traceback=traceback.format_exc(),
                )

            dur = (outcome.finished_at - outcome.started_at).total_seconds()
            now_str = datetime.now().strftime("%H:%M:%S")
            passed = sum(1 for t in outcome.trials if t.passed)
            total = len(outcome.trials)
            
            if outcome.status == LegStatus.SUCCEEDED:
                console.print(f"[[dim]{now_str}[/dim]] ✅ Leg [bold cyan]{leg.leg_id}[/bold cyan] "
                              f"completed in {dur:.1f}s ([green]{passed}[/green]/{total} passed)")
            else:
                console.print(f"[[dim]{now_str}[/dim]] ⚠️ Leg [bold cyan]{leg.leg_id}[/bold cyan] "
                              f"finished with status [yellow]{outcome.status.value}[/yellow] in {dur:.1f}s")

            return LegRecord(
                leg_id=leg.leg_id,
                agent_id=leg.agent_id,
                status=outcome.status,
                started_at=outcome.started_at,
                finished_at=outcome.finished_at,
                duration_sec=(outcome.finished_at - outcome.started_at).total_seconds(),
                harbor_dir=running_record.harbor_dir,
                harbor_result_path=running_record.harbor_result_path,
                agent_config_path=running_record.agent_config_path,
                trials=outcome.trials,
                error=outcome.error,
                traceback=outcome.traceback,
            )

    try:
        async with asyncio.TaskGroup() as tg:
            tasks = [tg.create_task(run_one(leg)) for leg in plan.legs]
    except Exception:
        # Exceptions are recorded inside the task and returned,
        # but if fail_fast raises we catch it here and let it finish recording.
        pass

    for task in tasks:
        try:
            record = task.result()
            manifest = _upsert_leg(manifest, record, experiment_root)
        except Exception:
            # Should not happen as run_one catches everything
            pass

    _persist_manifest(manifest, experiment_root)

    if emit_results:
        from openharness.experiments.results import (
            collect_results,
            summarize_results,
            write_results,
        )

        rows = collect_results(manifest, experiment_root=experiment_root)
        summary = summarize_results(rows)
        write_results(rows, summary, experiment_root=experiment_root)

    return manifest
