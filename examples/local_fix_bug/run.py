"""Architecture comparison: all agent configs solve the same bug.

Sets up an identical buggy workspace for each registered agent
configuration, runs them in sequence, then prints a comparison table.

Prerequisites:
    export ANTHROPIC_API_KEY=sk-ant-...
    # or GEMINI_API_KEY / OPENAI_API_KEY depending on the model in the configs

Usage:
    uv run python examples/local_fix_bug/run.py
    uv run python examples/local_fix_bug/run.py default planner_executor_example
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from openharness.agents.contracts import TaskDefinition
from openharness.agents.factory import AgentFactory
from openharness.runtime.workflow import Workflow
from openharness.workspace import LocalWorkspace

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
    "`sum_evens(numbers)` that should return the sum of all **even** numbers "
    "in a list, but it currently returns the sum of the odd numbers instead.\n\n"
    "Fix the bug so that `sum_evens([1, 2, 3, 4, 5, 6])` returns `12`.\n\n"
    "Do not change the function signature or the filename."
)


@dataclass
class RunResult:
    agent_name: str
    architecture: str
    passed: bool
    input_tokens: int
    output_tokens: int
    elapsed_seconds: float
    error: str | None = None


async def run_agent(
    agent_name: str,
    factory: AgentFactory,
) -> RunResult:
    """Run a single agent config in its own isolated workspace."""
    config = factory.get_config(agent_name)

    with tempfile.TemporaryDirectory(prefix=f"oh-{agent_name}-") as tmpdir:
        workspace_dir = Path(tmpdir)
        (workspace_dir / "sum_evens.py").write_text(_BUGGY_CODE, encoding="utf-8")

        workspace = LocalWorkspace(cwd=workspace_dir)
        workflow = Workflow(workspace, agent_factory=factory)
        task = TaskDefinition(instruction=_INSTRUCTION)

        t0 = time.perf_counter()
        try:
            result = await workflow.run(task, agent_name=agent_name)
            elapsed = time.perf_counter() - t0

            passed = _check_output(workspace_dir / "sum_evens.py")
            return RunResult(
                agent_name=agent_name,
                architecture=config.architecture,
                passed=passed,
                input_tokens=result.agent_result.input_tokens,
                output_tokens=result.agent_result.output_tokens,
                elapsed_seconds=elapsed,
            )
        except Exception as exc:
            elapsed = time.perf_counter() - t0
            return RunResult(
                agent_name=agent_name,
                architecture=config.architecture,
                passed=False,
                input_tokens=0,
                output_tokens=0,
                elapsed_seconds=elapsed,
                error=str(exc)[:120],
            )


def _check_output(script_path: Path) -> bool:
    """Return True if the script outputs '12'."""
    try:
        proc = subprocess.run(
            [sys.executable, str(script_path)],
            capture_output=True, text=True, timeout=10,
        )
        return proc.stdout.strip() == "12"
    except Exception:
        return False


def _print_table(results: list[RunResult]) -> None:
    """Pretty-print a comparison table."""
    hdr = f"{'Agent':<30} {'Architecture':<20} {'Pass':>6} {'In Tok':>8} {'Out Tok':>8} {'Time':>7}  Error"
    print(hdr)
    print("-" * len(hdr) + "----------")
    for r in results:
        status = "✅" if r.passed else "❌"
        err = r.error or ""
        print(
            f"{r.agent_name:<30} {r.architecture:<20} {status:>6} "
            f"{r.input_tokens:>8} {r.output_tokens:>8} {r.elapsed_seconds:>6.1f}s  {err}"
        )


async def main() -> None:
    factory = AgentFactory.with_default_configs()
    available = factory.list_agents()

    # Allow filtering via CLI args
    requested = sys.argv[1:] or available
    agents_to_run = [a for a in requested if a in available]

    if not agents_to_run:
        print(f"No matching agents. Available: {available}")
        return

    print(f"Task: Fix sum_evens.py so it returns 12 instead of 9")
    print(f"Agents to run: {agents_to_run}\n")

    results: list[RunResult] = []
    for agent_name in agents_to_run:
        config = factory.get_config(agent_name)
        print(f"--- Running: {agent_name} (architecture: {config.architecture}) ---")
        result = await run_agent(agent_name, factory)
        status = "✅ PASS" if result.passed else "❌ FAIL"
        print(f"    {status}  ({result.elapsed_seconds:.1f}s, {result.input_tokens + result.output_tokens} tokens)")
        if result.error:
            print(f"    Error: {result.error}")
        print()
        results.append(result)

    print("\n" + "=" * 80)
    print("COMPARISON TABLE")
    print("=" * 80)
    _print_table(results)

    passed = sum(1 for r in results if r.passed)
    print(f"\n{passed}/{len(results)} agents solved the task.")


if __name__ == "__main__":
    asyncio.run(main())
