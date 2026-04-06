"""Single-role workflow topology."""

from __future__ import annotations

from openharness.agents.contracts import TaskDefinition
from openharness.workflows.contracts import WorkflowRunResult
import logging

log = logging.getLogger(__name__)


class SingleTopology:
    """Run a single inline role."""

    name = "single"

    async def run(self, spec, task: TaskDefinition, runtime) -> WorkflowRunResult:
        if spec.entry_role is not None:
            role = spec.entry_role
        elif len(spec.roles) == 1:
            role = next(iter(spec.roles))
        else:
            raise ValueError("single topology requires entry_role when multiple roles exist")

        log.info(f"Running single role '{role}'...")
        result = await runtime.run_inline(role, task)
        return WorkflowRunResult(
            workflow_name=spec.name,
            topology=spec.topology,
            entry_role=role,
            team_name=runtime.team_name,
            final_text=result.final_text,
            role_results={role: result},
        )

