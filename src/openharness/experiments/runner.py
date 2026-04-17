"""Experiment runner and orchestrator."""

from __future__ import annotations

import asyncio
import logging
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

import yaml

from openharness.experiments.backends import Backend, LegContext
from openharness.experiments.backends.harbor import HarborBackend
from openharness.experiments.manifest import (
    ExperimentManifest,
    LegAggregate,
    LegRecord,
    LegResultStatus,
    LegStatus,
    TrialErrorPhase,
    TrialRecord,
)
from openharness.experiments.paths import make_rel
from openharness.experiments.plan import ExperimentPlan, Leg, plan_experiment
from openharness.experiments.reproducibility import collect_reproducibility
from openharness.experiments.spec import ExperimentSpec, LoadedExperimentSpec

log = logging.getLogger(__name__)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _get_default_backend(spec: ExperimentSpec) -> Backend:
    return HarborBackend()


def _initialize_experiment_tree(
    experiment_root: Path,
    spec: ExperimentSpec,
    plan: ExperimentPlan,
    *,
    loaded_spec: LoadedExperimentSpec | None,
) -> None:
    experiment_root.mkdir(parents=True, exist_ok=True)

    spec_path = experiment_root / "config.source.yaml"
    if not spec_path.exists():
        if loaded_spec is not None:
            spec_path.write_text(loaded_spec.source_text, encoding="utf-8")
        else:
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

    now = _now_utc()

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
                result_status=None,
                started_at=None,
                finished_at=None,
                duration_sec=None,
                harbor_dir=None,
                harbor_result_path=None,
                agent_config_path=None,
                trials=(),
                aggregate=None,
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


def _compute_aggregate(trials: tuple[TrialRecord, ...]) -> LegAggregate | None:
    if not trials:
        return None

    n_passed = sum(1 for t in trials if t.passed)
    n_errored = sum(1 for t in trials if t.error is not None)
    n_failed = sum(1 for t in trials if not t.passed and t.error is None)

    by_phase: dict[str, int] = {}
    for t in trials:
        if t.error is None:
            continue
        phase = t.error.phase.value if hasattr(t.error.phase, "value") else str(t.error.phase)
        by_phase[phase] = by_phase.get(phase, 0) + 1

    scores = [t.score for t in trials if t.score is not None]
    mean_score = sum(scores) / len(scores) if scores else None

    total_input = sum(t.input_tokens or 0 for t in trials)
    total_output = sum(t.output_tokens or 0 for t in trials)
    total_tokens = sum(t.total_tokens or 0 for t in trials)
    total_cost = round(sum(t.cost_usd or 0.0 for t in trials), 10)

    return LegAggregate(
        n_trials=len(trials),
        n_passed=n_passed,
        n_failed=n_failed,
        n_errored=n_errored,
        n_errored_by_phase=by_phase,
        mean_score=mean_score,
        total_input_tokens=total_input,
        total_output_tokens=total_output,
        total_tokens=total_tokens,
        total_cost_usd=total_cost,
    )


def _compute_result_status(
    trials: tuple[TrialRecord, ...], aggregate: LegAggregate | None
) -> LegResultStatus | None:
    if aggregate is None or aggregate.n_trials == 0:
        return LegResultStatus.NO_TRIALS
    if aggregate.n_passed == aggregate.n_trials:
        return LegResultStatus.ALL_PASSED
    if aggregate.n_errored == aggregate.n_trials:
        return LegResultStatus.ALL_ERRORED
    if aggregate.n_passed == 0:
        return LegResultStatus.ALL_FAILED
    return LegResultStatus.PARTIAL


def _finalize_leg_record(record: LegRecord) -> LegRecord:
    aggregate = _compute_aggregate(record.trials)
    result_status = _compute_result_status(record.trials, aggregate)
    return record.model_copy(update={"aggregate": aggregate, "result_status": result_status})


def _upsert_leg(
    manifest: ExperimentManifest, record: LegRecord, experiment_root: Path
) -> ExperimentManifest:
    record = _finalize_leg_record(record)
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
            "updated_at": _now_utc(),
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
    loaded_spec: LoadedExperimentSpec | None = None,
) -> ExperimentManifest:
    plan = plan_experiment(spec, instance_id=instance_id)
    backend = backend or _get_default_backend(spec)
    env = env or {}

    _initialize_experiment_tree(experiment_root, spec, plan, loaded_spec=loaded_spec)
    manifest = _load_or_seed_manifest(experiment_root, plan)
    _persist_manifest(manifest, experiment_root)

    semaphore = asyncio.Semaphore(spec.leg_concurrency)

    async def run_one(leg: Leg) -> LegRecord:
        async with semaphore:
            ctx = LegContext(
                experiment_root=experiment_root,
                leg_dir=experiment_root / "legs" / leg.leg_id,
                env=dict(env),
                dry_run=dry_run,
                resume=resume,
                spec=spec,
                instance_id=instance_id,
            )

            if dry_run:
                return LegRecord(
                    leg_id=leg.leg_id,
                    agent_id=leg.agent_id,
                    status=LegStatus.DRY_RUN,
                    result_status=None,
                    started_at=None,
                    finished_at=None,
                    duration_sec=None,
                    harbor_dir=None,
                    harbor_result_path=None,
                    agent_config_path=None,
                )

            if resume and backend.is_leg_complete(leg, ctx):
                return LegRecord(
                    leg_id=leg.leg_id,
                    agent_id=leg.agent_id,
                    status=LegStatus.SKIPPED,
                    result_status=None,
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
                result_status=None,
                started_at=_now_utc(),
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

            try:
                outcome = await backend.run_leg(leg, ctx)

                if spec.fail_fast and outcome.status == LegStatus.FAILED:
                    raise RuntimeError(f"Leg {leg.leg_id} failed: {outcome.error}")

            except asyncio.CancelledError:
                now = _now_utc()
                return LegRecord(
                    leg_id=leg.leg_id,
                    agent_id=leg.agent_id,
                    status=LegStatus.INTERRUPTED,
                    result_status=None,
                    started_at=running_record.started_at,
                    finished_at=now,
                    duration_sec=(now - running_record.started_at).total_seconds()
                    if running_record.started_at is not None
                    else None,
                    harbor_dir=running_record.harbor_dir,
                    harbor_result_path=running_record.harbor_result_path,
                    agent_config_path=running_record.agent_config_path,
                )
            except Exception as exc:
                now = _now_utc()
                return LegRecord(
                    leg_id=leg.leg_id,
                    agent_id=leg.agent_id,
                    status=LegStatus.FAILED,
                    result_status=None,
                    started_at=running_record.started_at,
                    finished_at=now,
                    duration_sec=(now - running_record.started_at).total_seconds()
                    if running_record.started_at is not None
                    else None,
                    harbor_dir=running_record.harbor_dir,
                    harbor_result_path=running_record.harbor_result_path,
                    agent_config_path=running_record.agent_config_path,
                    error=str(exc),
                    traceback=traceback.format_exc(),
                )

            return LegRecord(
                leg_id=leg.leg_id,
                agent_id=leg.agent_id,
                status=outcome.status,
                result_status=None,
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

    tasks: list[asyncio.Task[LegRecord]] = []
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
            # Should not happen because run_one catches everything.
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


__all__ = [
    "run_experiment",
    "TrialErrorPhase",
]
