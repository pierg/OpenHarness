"""Evaluate an OpenHarness agent across a pre-defined Harbor dataset concurrently.

Usage:
    uv run python examples/harbor_dataset/run.py
"""

import sys
from pathlib import Path

EXAMPLES_ROOT = Path(__file__).resolve().parents[1]
if str(EXAMPLES_ROOT) not in sys.path:
    sys.path.insert(0, str(EXAMPLES_ROOT))

from openharness.experiments import HarborExperiment  # noqa: E402
from openharness.experiments.observability import setup_local_langfuse  # noqa: E402

AGENT_CONFIG = EXAMPLES_ROOT / "_shared" / "agent_configs" / "harbor_registry_agent.yaml"

# 1. Define the dataset (e.g. from registry.harborframework.com)
DATASET = "terminal-bench@2.0"
MODEL = "gemini-2.5-flash"
MAX_TURNS = 10


def main() -> None:
    env = setup_local_langfuse(docker_compatible=True)

    experiment = HarborExperiment(
        agent_config=AGENT_CONFIG,
        # We specify dataset instead of a single task
        dataset=DATASET,
        # We define a reduced dataset by filtering the full dataset.
        # This will ONLY run the tasks that match the glob patterns provided below
        include_tasks=[
            "build-*",
            "git-*",
        ],
        # Limit the total number of tasks to run (useful for a fast trial)
        n_tasks=2,
        # How many Docker sandbox environments to spin up and run in parallel
        n_concurrent=2,
        model=MODEL,
        max_turns=MAX_TURNS,
        env=env,
    )

    # This automatically provisions 2 concurrent environments, runs the 4 matching
    # tasks, aggregates the scores, and exports the traces to Langfuse.
    result = experiment.run()

    # Will print aggregated statistics and location of the combined results.json
    experiment.log_summary(result)


if __name__ == "__main__":
    main()
