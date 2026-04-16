"""Harbor execution backend."""

from __future__ import annotations

import asyncio
import json
import traceback
from datetime import datetime
from pathlib import Path

import yaml

from openharness.agents.config import AgentConfig
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
from openharness.experiments.backends import Backend, LegContext, LegOutcome
from openharness.experiments.manifest import LegStatus, TrialRecord
from openharness.experiments.paths import make_rel
from openharness.experiments.plan import Leg


class HarborBackend(Backend):
    async def run_leg(self, leg: Leg, ctx: LegContext) -> LegOutcome:
        started_at = datetime.now()
        run_spec = self._build_harbor_run_spec(leg, ctx)

        try:
            result = await asyncio.to_thread(run_harbor_agent, run_spec)
        except Exception as exc:
            return LegOutcome(
                status=LegStatus.FAILED,
                started_at=started_at,
                finished_at=datetime.now(),
                error=str(exc),
                traceback=traceback.format_exc(),
            )

        return LegOutcome(
            status=LegStatus.SUCCEEDED,
            trials=tuple(
                self._trial_record_from_harbor_result(t, ctx.experiment_root) for t in result.trials
            ),
            started_at=started_at,
            finished_at=datetime.now(),
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

        job_spec = HarborJobSpec(
            tool=HarborToolSpec(editable_openharness_dir=openharness_dir),
            agent=OpenHarnessHarborAgentSpec(
                agent_name=leg.agent_config.name,
                model=leg.agent_config.model,
                max_turns=leg.agent_config.max_turns,
                max_tokens=leg.agent_config.max_tokens,
                agent_config_yaml=config_yaml,
                env=ctx.env,
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
            quiet=ctx.spec.leg_concurrency > 1,
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
        return TrialRecord(
            trial_id=t.trial_id,
            task_name=t.task_name,
            trial_dir=make_rel(experiment_root, t.trial_dir),
            score=t.score,
            passed=t.passed,
            error=t.error,
            traceback=None,
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
