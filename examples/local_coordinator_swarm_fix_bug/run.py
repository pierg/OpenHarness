"""Coordinator/swarm example: spawn a YAML agent through the unified runtime.

This example mirrors the same bug-fix task but takes the control-plane path:
1. Load a project-local YAML config into an AgentDefinition
2. Map it into TeammateSpawnConfig
3. Spawn a stateful teammate using the in-process swarm backend
4. Delegate the task over the swarm mailbox

Prerequisites:
    export ANTHROPIC_API_KEY=sk-ant-...
    # or GEMINI_API_KEY / OPENAI_API_KEY depending on the selected model

Usage:
    uv run python examples/local_coordinator_swarm_fix_bug/run.py
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

from openharness.coordinator.agent_definitions import get_agent_definition
from openharness.swarm.mailbox import TeammateMailbox
from openharness.swarm.registry import get_backend_registry
from openharness.swarm.types import TeammateMessage, TeammateSpawnConfig

log = logging.getLogger(__name__)

HERE = Path(__file__).resolve().parent
SOURCE_AGENT_CONFIG = HERE / ".openharness" / "agent_configs" / "coordinator_swarm.yaml"

_BUGGY_CODE = """\
def sum_evens(numbers):
    # BUG: should filter n % 2 == 0, not n % 2 == 1
    return sum(n for n in numbers if n % 2 == 1)

if __name__ == "__main__":
    result = sum_evens([1, 2, 3, 4, 5, 6])
    print(result)
"""

_INSTRUCTION = (
    "The file sum_evens.py in the current directory contains a function "
    "`sum_evens(numbers)` that should return the sum of all even numbers "
    "in a list, but it currently returns the sum of the odd numbers instead.\n\n"
    "Fix the bug so that `sum_evens([1, 2, 3, 4, 5, 6])` returns `12`.\n\n"
    "Verify the fix before you stop."
)


def _seed_workspace(workspace_dir: Path) -> None:
    """Create the buggy file and a project-local YAML agent config."""
    (workspace_dir / "sum_evens.py").write_text(_BUGGY_CODE, encoding="utf-8")
    project_agent_dir = workspace_dir / ".openharness" / "agent_configs"
    project_agent_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SOURCE_AGENT_CONFIG, project_agent_dir / SOURCE_AGENT_CONFIG.name)


async def main() -> None:
    from openharness.observability.logging import setup_logging
    setup_logging()

    team_name = f"demo-{uuid.uuid4().hex[:8]}"
    leader_mailbox = TeammateMailbox(team_name=team_name, agent_id="leader")
    await leader_mailbox.clear()

    with tempfile.TemporaryDirectory(prefix="oh-coordinator-swarm-") as tmpdir:
        workspace_dir = Path(tmpdir)
        _seed_workspace(workspace_dir)

        # 1. Resolve the project-local agent definition
        agent_def = get_agent_definition("coordinator-swarm-fixer", cwd=str(workspace_dir))
        if not agent_def:
            raise RuntimeError("Agent definition not found")

        # 2. Build spawn config and select backend
        spawn_config = TeammateSpawnConfig(
            name=agent_def.subagent_type,
            team=team_name,
            cwd=str(workspace_dir),
            runner=agent_def.runner,
            agent_config_name=agent_def.agent_config_name,
            model=agent_def.model,
            system_prompt=agent_def.system_prompt,
            parent_session_id="leader",
        )
        
        backend = get_backend_registry().get_executor("in_process")

        log.info(f"Workspace:      {workspace_dir}")
        log.info(f"Backend:        {backend.__class__.__name__}")
        log.info(f"Agent:          {agent_def.subagent_type} (runner: {agent_def.runner})")

        # 3. Spawn and delegate
        spawn_result = await backend.spawn(spawn_config)
        if not spawn_result.success:
            raise RuntimeError(spawn_result.error)

        await backend.send_message(
            spawn_result.agent_id,
            TeammateMessage(text=_INSTRUCTION, from_agent="leader")
        )

        # 4. Wait for the fix
        script_path = workspace_dir / "sum_evens.py"
        passed = False
        for _ in range(100):
            try:
                proc = subprocess.run([sys.executable, str(script_path)], capture_output=True, text=True, timeout=5)
                if proc.stdout.strip() == "12":
                    passed = True
                    break
            except Exception:
                pass
            await asyncio.sleep(0.5)

        # 5. Shutdown and read final summary
        await backend.shutdown(spawn_result.agent_id)
        
        idle_summary = ""
        messages = await leader_mailbox.read_all(unread_only=True)
        for msg in messages:
            if msg.type == "idle_notification":
                idle_summary = msg.payload.get("summary", "")

        log.info(f"Passed:         {passed}")
        if idle_summary:
            log.info(f"Summary:        {idle_summary}")

if __name__ == "__main__":
    asyncio.run(main())
