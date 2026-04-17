"""Evaluate a YAML-defined OpenHarness agent on an existing Harbor registry task.

Usage:
    uv run python examples/harbor_registry_task/run.py
"""

import sys
import asyncio
from pathlib import Path

EXAMPLES_ROOT = Path(__file__).resolve().parents[1]
if str(EXAMPLES_ROOT) not in sys.path:
    sys.path.insert(0, str(EXAMPLES_ROOT))

from openharness.experiments.spec import ExperimentSpec, AgentLegSpec, AgentOverrides  # noqa: E402
from openharness.experiments.runner import run_experiment  # noqa: E402
from openharness.observability.langfuse import langfuse_agent_env_for_docker  # noqa: E402

AGENT_CONFIG = EXAMPLES_ROOT / "_shared" / "agent_configs" / "harbor_registry_agent.yaml"
TASK = "cookbook/hello-world"
MODEL = "gemini-3.1-flash-lite-preview"
MAX_TURNS = 10


async def main() -> None:
    env = langfuse_agent_env_for_docker()

    spec = ExperimentSpec(
        id="example-registry",
        dataset=TASK,
        agents=(AgentLegSpec(id=str(AGENT_CONFIG)),),
        defaults=AgentOverrides(
            model=MODEL,
            max_turns=MAX_TURNS,
            n_concurrent=1,
        ),
    )

    experiment_root = Path("runs/experiments/example-registry")

    await run_experiment(
        spec,
        experiment_root=experiment_root,
        instance_id="example-registry",
        env=env,
        emit_results=True,
    )

    print(f"\nExperiment complete. Results in {experiment_root}")


if __name__ == "__main__":
    asyncio.run(main())
