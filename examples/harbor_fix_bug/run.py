"""Architecture comparison via Harbor: all agent configs solve the same bug in Docker.

Launches each registered agent configuration as a separate Harbor job inside
its own container.  After all runs complete, prints a comparison table with
pass/fail, scores, and timing.

Prerequisites
-------------
- Docker daemon running
- Harbor CLI:  uv tool install harbor==0.3.0
- API key exported, e.g.:
    export ANTHROPIC_API_KEY=sk-ant-...
  or for Gemini:
    export GOOGLE_API_KEY=...

Usage
-----
  cd examples/harbor_fix_bug
  python run.py                                       # all architectures
  python run.py default planner_executor_example      # specific ones
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from openharness.agents.factory import AgentFactory
from openharness.harbor import (
    HarborEnvironmentSpec,
    HarborJobSpec,
    HarborRunResult,
    HarborTaskSpec,
    HarborToolSpec,
    OpenHarnessHarborAgentSpec,
    run_harbor_job,
)

HERE = Path(__file__).parent
TASK_DIR = HERE / "harbor_task"
JOBS_DIR = HERE / "harbor_jobs"
OPENHARNESS_DIR = HERE.parent.parent


@dataclass
class RunResult:
    agent_name: str
    architecture: str
    score: float | None
    status: str | None
    elapsed_seconds: float
    job_name: str
    error: str | None = None


def run_agent(agent_name: str, architecture: str) -> RunResult:
    """Run a single agent configuration as a Harbor job."""
    t0 = time.perf_counter()
    try:
        result: HarborRunResult = run_harbor_job(
            HarborJobSpec(
                jobs_dir=JOBS_DIR,
                tool=HarborToolSpec(
                    version="0.3.0",
                    editable_openharness_dir=OPENHARNESS_DIR,
                ),
                agent=OpenHarnessHarborAgentSpec(
                    agent_name=agent_name,
                    remote_cwd="/app",
                    max_turns=10,
                    max_tokens=8192,
                ),
                task=HarborTaskSpec(path=TASK_DIR),
                environment=HarborEnvironmentSpec(type="docker"),
            )
        )
        elapsed = time.perf_counter() - t0

        score = None
        status = None
        if result.result_path.exists():
            data = json.loads(result.result_path.read_text())
            # Harbor 0.3.0 result structure:
            # stats -> evals -> <agent>__<dataset> -> metrics -> [{"mean": 1.0}]
            evals = data.get("stats", {}).get("evals", {})
            for key, val in evals.items():
                metrics = val.get("metrics", [])
                if metrics:
                    score = metrics[0].get("mean")
                    status = "PASS" if score and score > 0 else "FAIL"
                    break

        return RunResult(
            agent_name=agent_name,
            architecture=architecture,
            score=score,
            status=status,
            elapsed_seconds=elapsed,
            job_name=result.job_name,
        )
    except Exception as exc:
        elapsed = time.perf_counter() - t0
        return RunResult(
            agent_name=agent_name,
            architecture=architecture,
            score=None,
            status="error",
            elapsed_seconds=elapsed,
            job_name="",
            error=str(exc)[:120],
        )


def print_table(results: list[RunResult]) -> None:
    """Pretty-print a comparison table."""
    hdr = f"{'Agent':<30} {'Architecture':<20} {'Score':>6} {'Status':<10} {'Time':>7}  Error"
    print(hdr)
    print("-" * len(hdr) + "----------")
    for r in results:
        score_str = f"{r.score}" if r.score is not None else "N/A"
        err = r.error or ""
        print(
            f"{r.agent_name:<30} {r.architecture:<20} {score_str:>6} "
            f"{(r.status or 'N/A'):<10} {r.elapsed_seconds:>6.1f}s  {err}"
        )


def main() -> None:
    factory = AgentFactory.with_default_configs()
    available = factory.list_agents()

    requested = sys.argv[1:] or available
    agents_to_run = [a for a in requested if a in available]

    if not agents_to_run:
        print(f"No matching agents. Available: {available}")
        return

    print(f"Task:   {TASK_DIR}")
    print(f"Jobs:   {JOBS_DIR}")
    print(f"Agents: {agents_to_run}\n")

    results: list[RunResult] = []
    for agent_name in agents_to_run:
        config = factory.get_config(agent_name)
        print(f"--- Running: {agent_name} (architecture: {config.architecture}) ---")
        result = run_agent(agent_name, config.architecture)
        status_icon = "PASS" if result.score and result.score > 0 else "FAIL"
        print(f"    {status_icon}  score={result.score}  ({result.elapsed_seconds:.1f}s)")
        if result.error:
            print(f"    Error: {result.error}")
        print()
        results.append(result)

    print("\n" + "=" * 80)
    print("COMPARISON TABLE")
    print("=" * 80)
    print_table(results)

    passed = sum(1 for r in results if r.score and r.score > 0)
    print(f"\n{passed}/{len(results)} agents solved the task.")


if __name__ == "__main__":
    main()
