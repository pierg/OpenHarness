"""Evaluate a YAML-defined OpenHarness agent on an existing Harbor registry task.

Usage:
    uv run python examples/harbor_registry_task/run.py
"""

import sys
from pathlib import Path

EXAMPLES_ROOT = Path(__file__).resolve().parents[1]
if str(EXAMPLES_ROOT) not in sys.path:
    sys.path.insert(0, str(EXAMPLES_ROOT))

from openharness.experiments import HarborExperiment  # noqa: E402
from openharness.experiments.observability import setup_local_langfuse  # noqa: E402

AGENT_CONFIG = EXAMPLES_ROOT / "_shared" / "agent_configs" / "harbor_registry_agent.yaml"
TASK = "cookbook/hello-world"
MODEL = "gemini-2.5-flash"
MAX_TURNS = 10


def main() -> None:
    env = setup_local_langfuse(docker_compatible=True)

    experiment = HarborExperiment(
        agent_config=AGENT_CONFIG,
        task=TASK,
        model=MODEL,
        max_turns=MAX_TURNS,
        env=env,
    )

    result = experiment.run()
    experiment.log_summary(result)


if __name__ == "__main__":
    main()
