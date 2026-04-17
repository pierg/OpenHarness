"""Local execution backend."""

from __future__ import annotations

import traceback
from datetime import datetime, timezone
from pathlib import Path

import yaml

from openharness.experiments.backends import Backend, LegContext, LegOutcome
from openharness.experiments.manifest import LegStatus, TrialRecord
from openharness.experiments.paths import make_rel
from openharness.experiments.plan import Leg
from openharness.runs import AgentSpec, InlineTaskSpec, LocalAgentRunSpec, run_local_agent


class LocalBackend(Backend):
    async def run_leg(self, leg: Leg, ctx: LegContext) -> LegOutcome:
        started_at = datetime.now(timezone.utc)

        task_instruction = ctx.spec.dataset

        config_yaml = yaml.safe_dump(
            leg.agent_config.model_dump(mode="json", exclude_none=True), sort_keys=False
        )
        resolved_agent_path = ctx.leg_dir / "agent.resolved.yaml"
        resolved_agent_path.parent.mkdir(parents=True, exist_ok=True)
        resolved_agent_path.write_text(config_yaml, encoding="utf-8")

        openharness_dir = Path(__file__).resolve().parents[4]

        spec = LocalAgentRunSpec(
            cwd=ctx.leg_dir,
            run_cwd=openharness_dir,
            task=InlineTaskSpec(instruction=task_instruction),
            agent=AgentSpec(
                name=leg.agent_config.name,
                model=leg.agent_config.model,
                max_turns=leg.agent_config.max_turns,
            ),
            run_id=leg.harbor_run_id,
            metadata={
                "experiment_id": ctx.spec.id,
                "leg_id": leg.leg_id,
                "agent_id": leg.agent_id,
            },
        )

        try:
            result = await run_local_agent(spec)
        except Exception as exc:
            return LegOutcome(
                status=LegStatus.FAILED,
                started_at=started_at,
                finished_at=datetime.now(timezone.utc),
                error=str(exc),
                traceback=traceback.format_exc(),
            )

        now = datetime.now(timezone.utc)
        trial_record = TrialRecord(
            trial_id=leg.harbor_run_id,
            task_name="local-task",
            trial_dir=make_rel(ctx.experiment_root, result.run_dir),
            score=None,
            passed=False,
            error=None,
            model=leg.agent_config.model,
            input_tokens=None,
            output_tokens=None,
            total_tokens=None,
            cost_usd=None,
            duration_sec=(now - started_at).total_seconds(),
            agent_duration_sec=None,
            env_setup_duration_sec=None,
            verifier_duration_sec=None,
            trace_id=result.trace_id,
            trace_url=result.trace_url,
        )

        return LegOutcome(
            status=LegStatus.SUCCEEDED,
            trials=(trial_record,),
            started_at=started_at,
            finished_at=now,
        )

    def is_leg_complete(self, leg: Leg, ctx: LegContext) -> bool:
        result_path = ctx.leg_dir / "results.json"
        return result_path.exists()
