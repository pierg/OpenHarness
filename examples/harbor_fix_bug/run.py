"""Run the shared bug-fix task inside Harbor.

Usage:
    uv run python examples/harbor_fix_bug/run.py
"""

import sys
import shutil
import asyncio
from pathlib import Path

EXAMPLES_ROOT = Path(__file__).resolve().parents[1]
if str(EXAMPLES_ROOT) not in sys.path:
    sys.path.insert(0, str(EXAMPLES_ROOT))

from openharness.experiments.spec import ExperimentSpec, AgentLegSpec, AgentOverrides  # noqa: E402
from openharness.experiments.runner import run_experiment  # noqa: E402
from openharness.observability.langfuse import langfuse_agent_env_for_docker  # noqa: E402

AGENT_CONFIG = EXAMPLES_ROOT / "_shared" / "agent_configs" / "bugfix_agent.yaml"
MODEL = "gemini-3.1-flash-lite-preview"
MAX_TURNS = 10


async def main() -> None:
    env = langfuse_agent_env_for_docker()

    task_source = EXAMPLES_ROOT / "_shared" / "bugfix_task"
    workspace_dir = EXAMPLES_ROOT.parent / "runs" / "temp_workspace"
    if workspace_dir.exists():
        shutil.rmtree(workspace_dir)
    shutil.copytree(task_source, workspace_dir)

    spec = ExperimentSpec(
        id="example-fix-bug",
        dataset=str(workspace_dir),
        agents=(AgentLegSpec(id=str(AGENT_CONFIG)),),
        defaults=AgentOverrides(
            model=MODEL,
            max_turns=MAX_TURNS,
            n_concurrent=1,
        ),
    )

    experiment_root = Path("runs/experiments/example-fix-bug")

    await run_experiment(
        spec,
        experiment_root=experiment_root,
        instance_id="example-fix-bug",
        env=env,
        emit_results=True,
    )

    print(f"\nExperiment complete. Results in {experiment_root}")


if __name__ == "__main__":
    asyncio.run(main())
