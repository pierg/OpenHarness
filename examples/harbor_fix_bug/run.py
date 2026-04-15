"""Run the shared bug-fix task inside Harbor.

Usage:
    uv run python examples/harbor_fix_bug/run.py
"""

import sys
import shutil
from pathlib import Path

EXAMPLES_ROOT = Path(__file__).resolve().parents[1]
if str(EXAMPLES_ROOT) not in sys.path:
    sys.path.insert(0, str(EXAMPLES_ROOT))

from openharness.experiments import HarborExperiment  # noqa: E402
from openharness.experiments.observability import setup_local_langfuse  # noqa: E402

AGENT_CONFIG = EXAMPLES_ROOT / "_shared" / "agent_configs" / "bugfix_agent.yaml"
MODEL = "gemini-2.5-flash"
MAX_TURNS = 10


def main() -> None:
    env = setup_local_langfuse(docker_compatible=True)

    # Harbor needs a proper task folder (task.toml, instruction.md, etc.)
    task_source = Path(__file__).resolve().parents[1] / "_shared" / "bugfix_task"
    workspace_dir = EXAMPLES_ROOT.parent / "runs" / "temp_workspace"
    if workspace_dir.exists():
        shutil.rmtree(workspace_dir)
    shutil.copytree(task_source, workspace_dir)

    experiment = HarborExperiment(
        agent_config=AGENT_CONFIG,
        task=workspace_dir,
        model=MODEL,
        max_turns=MAX_TURNS,
        env=env,
    )

    result = experiment.run()
    experiment.log_summary(result)


if __name__ == "__main__":
    main()
