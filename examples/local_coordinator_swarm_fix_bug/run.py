"""Coordinator/swarm example: spawn a YAML agent through the unified runtime.

This example mirrors the same bug-fix task used by ``examples/local_fix_bug``,
but it takes the control-plane path that upstream added:

1. a project-local YAML config is projected into an ``AgentDefinition``
2. the coordinator-visible definition is mapped into ``TeammateSpawnConfig``
3. the in-process swarm backend starts the teammate
4. the leader sends work over the swarm mailbox
5. the YAML workflow runner executes the planner/executor architecture

Prerequisites:
    export ANTHROPIC_API_KEY=sk-ant-...
    # or GEMINI_API_KEY / OPENAI_API_KEY depending on the selected model

Usage:
    uv run python examples/local_coordinator_swarm_fix_bug/run.py
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from openharness.coordinator.agent_definitions import AgentDefinition, get_agent_definition
from openharness.swarm.mailbox import TeammateMailbox
from openharness.swarm.registry import get_backend_registry
from openharness.swarm.types import TeammateMessage, TeammateSpawnConfig

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


@dataclass(frozen=True)
class RunResult:
    """Summary of the coordinator/swarm example run."""

    agent_id: str
    subagent_type: str
    runner: str
    architecture: str | None
    backend: str
    passed: bool
    elapsed_seconds: float
    idle_summary: str


def _seed_workspace(workspace_dir: Path) -> None:
    """Create the buggy file and a project-local YAML agent config."""
    (workspace_dir / "sum_evens.py").write_text(_BUGGY_CODE, encoding="utf-8")

    project_agent_dir = workspace_dir / ".openharness" / "agent_configs"
    project_agent_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SOURCE_AGENT_CONFIG, project_agent_dir / SOURCE_AGENT_CONFIG.name)


def _build_spawn_config(
    agent_def: AgentDefinition,
    *,
    workspace_dir: Path,
    team_name: str,
) -> TeammateSpawnConfig:
    """Map a coordinator-visible definition into the swarm spawn contract."""
    allowed_tools = agent_def.tools
    if allowed_tools is not None and "*" in allowed_tools:
        allowed_tools = None

    return TeammateSpawnConfig(
        name=agent_def.subagent_type,
        team=team_name,
        prompt="",
        description=agent_def.description,
        cwd=str(workspace_dir),
        parent_session_id="leader",
        model=agent_def.model,
        system_prompt=agent_def.system_prompt,
        system_prompt_mode=agent_def.system_prompt_mode,
        color=agent_def.color,
        permissions=list(agent_def.permissions),
        plan_mode_required=agent_def.plan_mode_required,
        allow_permission_prompts=agent_def.allow_permission_prompts,
        runner=agent_def.runner,
        agent_config_name=agent_def.agent_config_name,
        agent_architecture=agent_def.agent_architecture,
        permission_mode=agent_def.permission_mode,
        allowed_tools=allowed_tools,
        disallowed_tools=agent_def.disallowed_tools,
        initial_prompt=agent_def.initial_prompt,
        max_turns=agent_def.max_turns,
    )


def _check_output(script_path: Path) -> bool:
    """Return True if the script outputs ``12``."""
    try:
        proc = subprocess.run(
            [sys.executable, str(script_path)],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception:
        return False
    return proc.stdout.strip() == "12"


async def _wait_for_fix(script_path: Path, *, timeout_seconds: float = 90.0) -> bool:
    """Poll until the workspace produces the expected output or the timeout elapses."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if _check_output(script_path):
            return True
        await asyncio.sleep(0.5)
    return False


async def _wait_for_idle_summary(
    mailbox: TeammateMailbox,
    *,
    timeout_seconds: float = 15.0,
) -> str:
    """Wait for the teammate shutdown notification sent back to the leader."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        for message in await mailbox.read_all(unread_only=True):
            await mailbox.mark_read(message.id)
            if message.type == "idle_notification":
                summary = message.payload.get("summary", "")
                return str(summary)
        await asyncio.sleep(0.1)
    return ""


async def run_example() -> RunResult:
    """Run the coordinator/swarm bug-fix example in an isolated workspace."""
    team_name = f"demo-{uuid.uuid4().hex[:8]}"
    leader_mailbox = TeammateMailbox(team_name=team_name, agent_id="leader")
    await leader_mailbox.clear()

    with tempfile.TemporaryDirectory(prefix="oh-coordinator-swarm-") as tmpdir:
        workspace_dir = Path(tmpdir)
        _seed_workspace(workspace_dir)

        agent_def = get_agent_definition("coordinator-swarm-fixer", cwd=str(workspace_dir))
        if agent_def is None:
            raise RuntimeError("Project-local YAML agent was not projected into AgentDefinition")

        print(f"Workspace:      {workspace_dir}")
        print(f"Catalog source: {agent_def.source}")
        print(f"Subagent type:  {agent_def.subagent_type}")
        print(f"Runner:         {agent_def.runner}")
        print(f"Architecture:   {agent_def.agent_architecture}")

        registry = get_backend_registry()
        backend = registry.get_executor("in_process")
        spawn_config = _build_spawn_config(
            agent_def,
            workspace_dir=workspace_dir,
            team_name=team_name,
        )

        started_at = time.perf_counter()
        spawn_result = await backend.spawn(spawn_config)
        if not spawn_result.success:
            raise RuntimeError(spawn_result.error or "Failed to spawn teammate")

        print(f"Backend:        {spawn_result.backend_type}")
        print(f"Agent ID:       {spawn_result.agent_id}")
        print("Leader action:  send task over swarm mailbox")

        await backend.send_message(
            spawn_result.agent_id,
            TeammateMessage(
                text=_INSTRUCTION,
                from_agent="leader",
            ),
        )

        passed = await _wait_for_fix(workspace_dir / "sum_evens.py")
        await backend.shutdown(spawn_result.agent_id)
        idle_summary = await _wait_for_idle_summary(leader_mailbox)
        elapsed = time.perf_counter() - started_at

        return RunResult(
            agent_id=spawn_result.agent_id,
            subagent_type=agent_def.subagent_type,
            runner=agent_def.runner,
            architecture=agent_def.agent_architecture,
            backend=spawn_result.backend_type,
            passed=passed,
            elapsed_seconds=elapsed,
            idle_summary=idle_summary,
        )


async def main() -> None:
    result = await run_example()
    print()
    print("=" * 80)
    print("RESULT")
    print("=" * 80)
    print(f"Subagent type: {result.subagent_type}")
    print(f"Runner:        {result.runner}")
    print(f"Architecture:  {result.architecture}")
    print(f"Backend:       {result.backend}")
    print(f"Agent ID:      {result.agent_id}")
    print(f"Passed:        {result.passed}")
    print(f"Elapsed:       {result.elapsed_seconds:.1f}s")
    if result.idle_summary:
        print(f"Idle summary:  {result.idle_summary}")


if __name__ == "__main__":
    asyncio.run(main())
