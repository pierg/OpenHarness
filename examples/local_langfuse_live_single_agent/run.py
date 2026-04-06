"""Single-agent Langfuse example with live trace updates.

This example takes the smallest direct path through the runtime:

1. create an isolated local workspace
2. register one simple YAML-style agent config in an ``AgentFactory``
3. instantiate that agent
4. create a task definition
5. create a Langfuse trace observer
6. run the agent with ``AgentRuntime``
7. verify the fix and close the trace session

Because the script enables ``OPENHARNESS_LANGFUSE_FLUSH_MODE=live`` by default,
closed spans are flushed during the run instead of only when the session ends.
That makes model turns and tool calls appear in Langfuse while the agent is
still working.

Prerequisites:
    export LANGFUSE_PUBLIC_KEY=pk-lf-...
    export LANGFUSE_SECRET_KEY=sk-lf-...
    export LANGFUSE_BASE_URL=http://localhost:3000
    export GEMINI_API_KEY=...
    # or GOOGLE_API_KEY / ANTHROPIC_API_KEY / OPENAI_API_KEY

Usage:
    uv run python examples/local_langfuse_live_single_agent/run.py
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from openharness.agents.config import AgentConfig
from openharness.agents.contracts import AgentRunResult, TaskDefinition
from openharness.agents.factory import AgentFactory
from openharness.api.provider import detect_provider
from openharness.config import load_settings
from openharness.config.settings import Settings
from openharness.observability import NullTraceObserver, create_trace_observer
from openharness.permissions.modes import PermissionMode
from openharness.runtime.session import AgentRuntime
from openharness.workspace import LocalWorkspace

_INTERFACE = "example_langfuse_live_single_agent"

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


@dataclass(frozen=True)
class RunSummary:
    """Summary of one single-agent example run."""

    session_id: str
    trace_name: str
    trace_id: str | None
    workspace_dir: Path
    model: str
    provider: str
    passed: bool
    elapsed_seconds: float
    final_text: str
    input_tokens: int
    output_tokens: int


def _pick_model() -> str:
    """Choose a model that matches the API keys available in the shell."""
    if (
        os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    ) and importlib.util.find_spec("google.genai"):
        return "gemini-2.5-flash-lite"
    if os.environ.get("OPENAI_API_KEY") and importlib.util.find_spec("openai"):
        return "gpt-4.1-mini"
    if os.environ.get("ANTHROPIC_API_KEY") and importlib.util.find_spec("anthropic"):
        return "claude-sonnet-4-20250514"
    raise RuntimeError(
        "No supported model/API client combination found. Install the provider "
        "SDK you want to use and set one of GEMINI_API_KEY, GOOGLE_API_KEY, "
        "ANTHROPIC_API_KEY, or OPENAI_API_KEY."
    )


def _build_agent_config(model: str) -> AgentConfig:
    """Return a small single-agent config suitable for live Langfuse tracing."""
    return AgentConfig(
        name="langfuse_live_simple",
        architecture="simple",
        description="Single coding agent used by the live Langfuse example.",
        model=model,
        max_turns=10,
        max_tokens=8192,
        tools=(
            "bash",
            "read_file",
            "write_file",
            "edit_file",
            "glob",
            "grep",
        ),
        prompts={
            "system": (
                "{{ openharness_system_context }}\n\n"
                "You are a single coding agent used for a Langfuse live tracing demo.\n"
                "Inspect the local workspace, fix the bug, verify the result, and stop.\n"
                "Be concise and prefer small accurate edits."
            ),
            "user": "{{ instruction }}",
        },
    )


def _build_runtime_settings(model: str) -> Settings:
    """Return explicit settings for the selected example model."""
    if model.startswith("gpt-"):
        return load_settings().merge_cli_overrides(
            model=model,
            provider="openai",
            api_format="openai",
        )
    if model.startswith("claude-"):
        return load_settings().merge_cli_overrides(
            model=model,
            provider="anthropic",
            api_format="anthropic",
        )
    return load_settings().merge_cli_overrides(model=model)


def _check_output(script_path: Path) -> bool:
    """Return True when the script prints the expected result."""
    proc = subprocess.run(
        [sys.executable, str(script_path)],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    return proc.stdout.strip() == "12"


async def run_example() -> RunSummary:
    """Run the single-agent live-tracing demo."""
    os.environ.setdefault("OPENHARNESS_LANGFUSE_FLUSH_MODE", "live")

    model = _pick_model()
    settings = _build_runtime_settings(model)
    provider = detect_provider(settings).name

    factory = AgentFactory()
    config = _build_agent_config(model)
    factory.register(config)
    agent = factory.create(config.name)
    task = TaskDefinition(instruction=_INSTRUCTION)
    session_id = uuid4().hex[:12]
    trace_name = f"openharness.{_INTERFACE}"

    workspace_dir = Path(tempfile.mkdtemp(prefix="oh-langfuse-live-"))
    script_path = workspace_dir / "sum_evens.py"
    script_path.write_text(_BUGGY_CODE, encoding="utf-8")

    workspace = LocalWorkspace(cwd=workspace_dir)
    trace_observer = create_trace_observer(
        session_id=session_id,
        interface=_INTERFACE,
        cwd=str(workspace_dir),
        model=model,
        provider=provider,
    )
    if isinstance(trace_observer, NullTraceObserver) or not trace_observer.enabled:
        raise RuntimeError(
            "Langfuse tracing is not enabled. Set LANGFUSE_PUBLIC_KEY, "
            "LANGFUSE_SECRET_KEY, and LANGFUSE_BASE_URL or LANGFUSE_HOST."
        )

    print("1. Workspace seeded")
    print(f"   path:        {workspace_dir}")
    print("2. Agent registered")
    print(f"   name:        {config.name}")
    print(f"   architecture:{config.architecture}")
    print(f"   model:       {model}")
    print("3. Agent instantiated")
    print(f"   class:       {agent.__class__.__name__}")
    print("4. Langfuse session created")
    print(f"   trace name:  {trace_name}")
    print(f"   session id:  {session_id}")
    print("   flush mode:  live")

    trace_observer.start_session(
        metadata={
            "example": "local_langfuse_live_single_agent",
            "agent_name": config.name,
            "architecture": config.architecture,
        }
    )
    print(f"   trace id:    {trace_observer.trace_id}")
    print("5. Task ready")
    print(f"   instruction: {_INSTRUCTION.splitlines()[0]}")
    print("6. Agent running")
    print("   open Langfuse and watch the trace update while tools/model calls complete")

    runtime = AgentRuntime(
        workspace=workspace,
        settings=settings,
        permission_mode=PermissionMode.FULL_AUTO,
        trace_observer=trace_observer,
    )
    started_at = time.perf_counter()

    try:
        result: AgentRunResult[str] = await agent.run(task, runtime)
    except Exception as exc:
        trace_observer.end_session(
            output={"error": str(exc)},
            metadata={"status": "error"},
        )
        raise

    elapsed = time.perf_counter() - started_at
    passed = _check_output(script_path)
    trace_observer.end_session(
        output={
            "final_text": result.final_text,
            "passed": passed,
        },
        metadata={
            "status": "completed",
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "passed": passed,
        },
    )

    return RunSummary(
        session_id=session_id,
        trace_name=trace_name,
        trace_id=trace_observer.trace_id,
        workspace_dir=workspace_dir,
        model=model,
        provider=provider,
        passed=passed,
        elapsed_seconds=elapsed,
        final_text=result.final_text,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
    )


async def main() -> None:
    summary = await run_example()
    status = "PASS" if summary.passed else "FAIL"

    print("7. Run finished")
    print(f"   status:      {status}")
    print(f"   elapsed:     {summary.elapsed_seconds:.1f}s")
    print(f"   provider:    {summary.provider}")
    print(f"   tokens:      in={summary.input_tokens} out={summary.output_tokens}")
    print(f"   trace id:    {summary.trace_id}")
    print(f"   workspace:   {summary.workspace_dir}")
    print(f"   final text:  {summary.final_text.strip()[:200]}")
    print("8. What to look for in Langfuse")
    print(f"   project:     OpenHarness")
    print(f"   trace name:  {summary.trace_name}")
    print("   observations:")
    print("     - session")
    print("     - agent:langfuse_live_simple")
    print("     - model")
    print("     - tool:read_file, tool:edit_file, tool:bash")


if __name__ == "__main__":
    asyncio.run(main())
