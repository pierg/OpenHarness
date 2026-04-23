"""Harbor execution backend."""

from __future__ import annotations

import ast
import asyncio
import json
import re
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from openharness.agents.config import AgentConfig
from openharness.experiments.backends import Backend, LegContext, LegOutcome
from openharness.experiments.manifest import (
    LegStatus,
    TrialError,
    TrialErrorPhase,
    TrialRecord,
)
from openharness.experiments.paths import make_rel, try_make_rel
from openharness.experiments.plan import Leg
from openharness.harbor import (
    HarborEnvironmentSpec,
    HarborExistingJobPolicy,
    HarborJobSpec,
    HarborTaskSpec,
    HarborToolSpec,
    OpenHarnessHarborAgentSpec,
)
from openharness.runs import HarborAgentRunSpec, run_harbor_agent
from openharness.runs.specs import TrialResult as HarborTrialResult


class HarborBackend(Backend):
    async def run_leg(self, leg: Leg, ctx: LegContext) -> LegOutcome:
        started_at = datetime.now(timezone.utc)
        run_spec = self._build_harbor_run_spec(leg, ctx)

        try:
            result = await asyncio.to_thread(run_harbor_agent, run_spec)
        except Exception as exc:
            return LegOutcome(
                status=LegStatus.FAILED,
                started_at=started_at,
                finished_at=datetime.now(timezone.utc),
                error=str(exc),
                traceback=traceback.format_exc(),
            )

        trials = tuple(
            self._trial_record_from_harbor_result(t, ctx.experiment_root) for t in result.trials
        )

        self._write_portable_harbor_results(ctx.experiment_root, result.trials)

        return LegOutcome(
            status=LegStatus.SUCCEEDED,
            trials=trials,
            started_at=started_at,
            finished_at=datetime.now(timezone.utc),
        )

    def is_leg_complete(self, leg: Leg, ctx: LegContext) -> bool:
        result_path = ctx.leg_dir / "harbor" / leg.harbor_run_id / "result.json"
        if not result_path.exists():
            return False

        try:
            data = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False

        expected_trials = data.get("n_total_trials")
        if not isinstance(expected_trials, int):
            return False

        harbor_dir = result_path.parent
        completed_trials = 0
        for trial_dir in harbor_dir.iterdir():
            if not trial_dir.is_dir():
                continue
            trial_result = trial_dir / "result.json"
            if not trial_result.exists():
                continue
            completed_trials += 1

        return completed_trials >= expected_trials

    def _build_harbor_run_spec(self, leg: Leg, ctx: LegContext) -> HarborAgentRunSpec:
        root = ctx.experiment_root
        config_yaml = self._dump_agent_config_yaml(leg.agent_config)

        jobs_dir = ctx.leg_dir / "harbor"

        resolved_agent_path = ctx.leg_dir / "agent.resolved.yaml"
        resolved_agent_path.parent.mkdir(parents=True, exist_ok=True)
        resolved_agent_path.write_text(config_yaml, encoding="utf-8")

        openharness_dir = Path(__file__).resolve().parents[4]

        merged_env: dict[str, str] = dict(ctx.env)
        # Forward experiment context to the agent so it can enrich Langfuse
        # traces with experiment_id / leg_id / agent_id / dataset and attach
        # every trace of an experiment to a single Langfuse session for
        # rollups in Langfuse / the lab web UI.
        merged_env.setdefault("OPENHARNESS_EXPERIMENT_ID", ctx.spec.id)
        merged_env.setdefault("OPENHARNESS_INSTANCE_ID", ctx.instance_id)
        merged_env.setdefault("OPENHARNESS_LEG_ID", leg.leg_id)
        merged_env.setdefault("OPENHARNESS_AGENT_ID", leg.agent_id)
        merged_env.setdefault("OPENHARNESS_DATASET", ctx.spec.dataset)
        if leg.agent_config.architecture:
            merged_env.setdefault(
                "OPENHARNESS_AGENT_ARCHITECTURE", str(leg.agent_config.architecture)
            )
        # Group every trial in an experiment under one Langfuse session so the
        # UI can show aggregate cost/tokens/score across legs.
        merged_env.setdefault("OPENHARNESS_LANGFUSE_SESSION_ID", ctx.instance_id)
        overrides_env = getattr(leg, "overrides_env", None)
        if overrides_env:
            merged_env.update(overrides_env)

        job_spec = HarborJobSpec(
            tool=HarborToolSpec(editable_openharness_dir=openharness_dir),
            agent=OpenHarnessHarborAgentSpec(
                agent_name=leg.agent_config.name,
                model=leg.agent_config.model,
                max_turns=leg.agent_config.max_turns,
                max_tokens=leg.agent_config.max_tokens,
                agent_config_yaml=config_yaml,
                env=merged_env,
            ),
            task=HarborTaskSpec(
                dataset=ctx.spec.dataset,
                include_task_names=ctx.spec.task_filter.include_tasks,
                exclude_task_names=ctx.spec.task_filter.exclude_tasks,
                n_tasks=ctx.spec.task_filter.n_tasks,
            ),
            environment=HarborEnvironmentSpec(
                type=ctx.spec.environment.type,
                kwargs=ctx.spec.environment.kwargs,
            ),
            jobs_dir=jobs_dir,
            n_attempts=leg.n_attempts,
            n_concurrent_trials=leg.n_concurrent,
            existing_job_policy=(
                HarborExistingJobPolicy.RESUME if ctx.resume else HarborExistingJobPolicy.ERROR
            ),
            metadata={
                "experiment_id": ctx.spec.id,
                "leg_id": leg.leg_id,
                "agent_id": leg.agent_id,
                "dataset": ctx.spec.dataset,
            },
        )
        return HarborAgentRunSpec(cwd=root, job=job_spec, run_id=leg.harbor_run_id)

    def _dump_agent_config_yaml(self, config: AgentConfig) -> str:
        raw = config.model_dump(mode="json", exclude_none=True)
        return yaml.safe_dump(raw, sort_keys=False)

    def _trial_record_from_harbor_result(
        self, t: HarborTrialResult, experiment_root: Path
    ) -> TrialRecord:
        trial_error: TrialError | None = None
        if t.error:
            trial_error = _parse_harbor_exception(t.error)

        return TrialRecord(
            trial_id=t.trial_id,
            task_name=t.task_name,
            trial_dir=make_rel(experiment_root, t.trial_dir),
            score=t.score,
            passed=t.passed,
            error=trial_error,
            model=t.model,
            input_tokens=t.input_tokens,
            output_tokens=t.output_tokens,
            total_tokens=t.total_tokens,
            cost_usd=t.cost_usd,
            duration_sec=t.duration_sec,
            agent_duration_sec=t.agent_duration_sec,
            env_setup_duration_sec=t.env_setup_duration_sec,
            verifier_duration_sec=t.verifier_duration_sec,
            trace_id=t.trace_id,
            trace_url=t.trace_url,
        )

    def _write_portable_harbor_results(
        self, experiment_root: Path, trials: list[HarborTrialResult]
    ) -> None:
        """Emit ``result.portable.json`` siblings for each Harbor result.json.

        Paths that fall under *experiment_root* are rewritten to
        experiment-root-relative POSIX strings so that the twin can safely
        travel across machines. The original Harbor artifacts are never
        modified.
        """
        for trial in trials:
            trial_dir = Path(trial.trial_dir)
            source = trial_dir / "result.json"
            if not source.exists():
                continue
            try:
                data = json.loads(source.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue

            portable = {
                "schema_version": 1,
                "anchor": "experiment_root",
                "trial_dir": try_make_rel(experiment_root, trial_dir).as_posix()
                if try_make_rel(experiment_root, trial_dir) is not None
                else str(trial_dir),
                "result": _portable_map(data, experiment_root),
            }
            destination = trial_dir / "result.portable.json"
            destination.write_text(
                json.dumps(portable, indent=2, ensure_ascii=True) + "\n", encoding="utf-8"
            )


def _portable_map(value: Any, experiment_root: Path) -> Any:
    if isinstance(value, dict):
        return {key: _portable_map(item, experiment_root) for key, item in value.items()}
    if isinstance(value, list):
        return [_portable_map(item, experiment_root) for item in value]
    if isinstance(value, str):
        return _maybe_relativize(value, experiment_root)
    return value


def _maybe_relativize(value: str, experiment_root: Path) -> str:
    if not value:
        return value
    candidate = value
    if value.startswith("file://"):
        candidate = value[len("file://") :]
    if not candidate.startswith("/"):
        return value
    try:
        path = Path(candidate).resolve()
    except (OSError, RuntimeError):
        return value
    rel = try_make_rel(experiment_root, path)
    if rel is None:
        return value
    return rel.as_posix()


_EXCEPTION_SIGNATURE_RE = re.compile(
    r"^(?P<type>[A-Za-z_][A-Za-z0-9_.]*?(?:Error|Exception|Interrupt)):\s*(?P<message>.*)",
    re.MULTILINE,
)


def _parse_harbor_exception(raw: str) -> TrialError:
    """Best-effort parse of Harbor's ``exception_info`` payload into ``TrialError``."""
    data: dict[str, Any] | None = None
    try:
        data = ast.literal_eval(raw) if isinstance(raw, str) else None
    except (ValueError, SyntaxError):
        data = None

    if isinstance(data, dict):
        message = str(data.get("exception_message") or raw)
        exc_type = data.get("exception_type")
        trace = data.get("exception_traceback")
        occurred_at_raw = data.get("occurred_at")
        occurred_at: datetime | None = None
        if isinstance(occurred_at_raw, str):
            try:
                occurred_at = datetime.fromisoformat(occurred_at_raw.replace("Z", "+00:00"))
            except ValueError:
                occurred_at = None
        phase = _classify_phase(message, str(trace) if trace else "")
        return TrialError(
            exception_type=exc_type,
            message=message,
            phase=phase,
            occurred_at=occurred_at,
            traceback=str(trace) if trace else None,
        )

    message = str(raw).strip()
    exc_type = None
    m = _EXCEPTION_SIGNATURE_RE.search(message)
    if m:
        exc_type = m.group("type")
    return TrialError(
        exception_type=exc_type,
        message=message,
        phase=_classify_phase(message, ""),
    )


def _classify_phase(message: str, traceback_text: str) -> TrialErrorPhase:
    haystack = f"{message}\n{traceback_text}".lower()
    if (
        "_setup_environment" in haystack
        or "docker compose" in haystack
        or "environment" in haystack
    ):
        return TrialErrorPhase.ENV_SETUP
    if "verifier" in haystack:
        return TrialErrorPhase.VERIFIER
    if "agent" in haystack or "run_harbor" in haystack:
        return TrialErrorPhase.AGENT
    return TrialErrorPhase.UNKNOWN
