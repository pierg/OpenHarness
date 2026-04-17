"""Evaluate an OpenHarness agent across a pre-defined Harbor dataset concurrently.

Usage:
    uv run python examples/harbor_dataset/run.py
"""

import sys
import asyncio
from pathlib import Path

EXAMPLES_ROOT = Path(__file__).resolve().parents[1]
if str(EXAMPLES_ROOT) not in sys.path:
    sys.path.insert(0, str(EXAMPLES_ROOT))

from openharness.observability.langfuse import langfuse_agent_env_for_docker  # noqa: E402
from openharness.experiments.spec import ExperimentSpec, AgentLegSpec, AgentOverrides, TaskFilter  # noqa: E402
from openharness.experiments.runner import run_experiment  # noqa: E402

AGENT_CONFIG = EXAMPLES_ROOT / "_shared" / "agent_configs" / "harbor_registry_agent.yaml"
DATASET = "terminal-bench@2.0"
MODEL = "gemini-3.1-flash-lite-preview"
MAX_TURNS = 10


async def main() -> None:
    env = langfuse_agent_env_for_docker()

    spec = ExperimentSpec(
        id="example-dataset",
        dataset=DATASET,
        agents=(AgentLegSpec(id=str(AGENT_CONFIG)),),
        defaults=AgentOverrides(
            model=MODEL,
            max_turns=MAX_TURNS,
            n_concurrent=2,
        ),
        task_filter=TaskFilter(
            include_tasks=("build-*", "git-*"),
            n_tasks=2,
        ),
    )

    # We use the new async run_experiment
    experiment_root = Path("runs/experiments/example-dataset")

    await run_experiment(
        spec,
        experiment_root=experiment_root,
        instance_id="example-dataset",
        env=env,
        emit_results=True,
    )

    print(f"\nExperiment complete. Manifest written to: {experiment_root / 'experiment.json'}")
    print(f"Summary written to: {experiment_root / 'results' / 'summary.md'}")


if __name__ == "__main__":
    asyncio.run(main())
