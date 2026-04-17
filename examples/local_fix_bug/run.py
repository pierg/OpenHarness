"""Start a local OpenHarness run and inspect its artifacts.

Usage:
    uv run python examples/local_fix_bug/run.py
"""

import sys
from pathlib import Path

EXAMPLES_ROOT = Path(__file__).resolve().parents[1]
if str(EXAMPLES_ROOT) not in sys.path:
    sys.path.insert(0, str(EXAMPLES_ROOT))

from _shared.helpers import prepare_bugfix_workspace, get_bugfix_instruction, script_prints_twelve  # noqa: E402
from openharness.experiments import LocalExperiment  # noqa: E402
from openharness.experiments.observability import setup_local_langfuse  # noqa: E402

AGENT_CONFIG = EXAMPLES_ROOT / "_shared" / "agent_configs" / "bugfix_agent.yaml"
MODEL = "gemini-2.5-flash"
MAX_TURNS = 10


async def main() -> None:
    env = setup_local_langfuse(docker_compatible=False)
    workspace_dir = prepare_bugfix_workspace()

    experiment = LocalExperiment(
        agent_config=AGENT_CONFIG,
        task=get_bugfix_instruction(local=True),
        workspace=workspace_dir,
        model=MODEL,
        max_turns=MAX_TURNS,
        env=env,
    )

    result = await experiment.run()

    passed = script_prints_twelve(workspace_dir)
    experiment.log_summary(result, passed=passed)


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
