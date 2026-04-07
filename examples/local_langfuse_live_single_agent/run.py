"""Single-agent Langfuse example with live trace updates.

This example showcases a clean execution path using the main APIs:
1. Load a built-in agent configuration
2. Setup an isolated workspace
3. Enable Langfuse tracing
4. Run the task using Workflow orchestration

Prerequisites:
    uv pip install --python .venv/bin/python 'langfuse>=2.0'
    export LANGFUSE_PUBLIC_KEY=pk-lf-...
    export LANGFUSE_SECRET_KEY=sk-lf-...
    export LANGFUSE_BASE_URL=http://localhost:3000
    export OPENAI_API_KEY=... # or ANTHROPIC_API_KEY, GEMINI_API_KEY

Usage:
    uv run python examples/local_langfuse_live_single_agent/run.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

from openharness.agents.contracts import TaskDefinition
from openharness.agents.factory import AgentFactory
from openharness.api.factory import create_api_client
from openharness.config import load_settings
from openharness.observability import create_trace_observer
from openharness.runtime.workflow import Workflow
from openharness.workspace import LocalWorkspace

log = logging.getLogger(__name__)

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
    "`sum_evens(numbers)` that should return the sum of all even numbers in a list, "
    "but it currently returns the sum of the odd numbers instead.\n\n"
    "Fix the bug so that `sum_evens([1, 2, 3, 4, 5, 6])` returns `12`.\n\n"
    "Verify the fix before you finish."
)


async def main() -> None:
    # 1. Enable live flushing so spans appear while the agent is running
    os.environ.setdefault("OPENHARNESS_LANGFUSE_FLUSH_MODE", "live")
    
    from openharness.observability.logging import setup_logging
    setup_logging()

    # 2. Define the workspace and task
    workspace_dir = Path(tempfile.mkdtemp(prefix="oh-langfuse-live-"))
    script_path = workspace_dir / "sum_evens.py"
    script_path.write_text(_BUGGY_CODE, encoding="utf-8")
    workspace = LocalWorkspace(cwd=workspace_dir)
    
    task = TaskDefinition(instruction=_INSTRUCTION)

    # 3. Initialize the factory with built-in configurations
    factory = AgentFactory.with_default_configs()
    agent_name = "default"
    config = factory.get_config(agent_name)

    # 4. Load settings
    settings = load_settings()
    config.model = settings.model
    model = settings.model
    
    trace_observer = create_trace_observer(
        session_id=uuid4().hex[:12],
        interface="example_langfuse_live_single_agent",
        cwd=str(workspace_dir),
        model=model,
    )
    trace_observer.start_session(
        metadata={
            "example": "local_langfuse_live_single_agent",
            "agent_name": agent_name,
            "architecture": config.architecture,
        }
    )

    log.info(f"Workspace: {workspace_dir}")
    log.info(f"Trace ID:  {trace_observer.trace_id}")
    log.info(f"Agent:     {agent_name} ({config.architecture})")
    log.info("Running... (Check Langfuse for live updates)")

    # 5. Run the workflow
    workflow = Workflow(workspace, factory)
    try:
        result = await workflow.run(
            task,
            agent_name=agent_name,
            api_client=create_api_client(settings),
            trace_observer=trace_observer,
        )
        
        # Verify the result
        proc = subprocess.run([sys.executable, str(script_path)], capture_output=True, text=True)
        passed = proc.stdout.strip() == "12"
        
        trace_observer.end_session(
            output={"final_text": result.agent_result.final_text, "passed": passed},
            metadata={
                "status": "completed", 
                "passed": passed,
                "input_tokens": result.agent_result.input_tokens,
                "output_tokens": result.agent_result.output_tokens
            }
        )
        
        log.info(f"Done! Passed: {passed}")
        
    except Exception as exc:
        trace_observer.end_session(
            output={"error": str(exc)},
            metadata={"status": "error"}
        )
        log.error(f"Task failed: {exc}")
        raise

if __name__ == "__main__":
    asyncio.run(main())
