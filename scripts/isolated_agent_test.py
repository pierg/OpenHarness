"""Isolated agent test harness.

Runs a given agent on a trivial file-manipulation task (no containers, no
Harbor), and reports which tools were actually called by the model.

Used to verify tool wiring and to reproduce per-architecture behaviour.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
from pathlib import Path

from openharness.runs.local import run_local_agent
from openharness.runs.specs import AgentSpec, InlineTaskSpec, LocalAgentRunSpec


TASK_INSTRUCTION = (
    "You have access to a working directory at the current cwd. "
    "Create a file called `hello.txt` whose contents is exactly `hello world`, "
    "then read it back to confirm it exists, and finally report success."
)


async def _run_one(agent_name: str, *, model: str, cwd: Path, run_root: Path) -> dict:
    spec = LocalAgentRunSpec(
        cwd=cwd,
        task=InlineTaskSpec(instruction=TASK_INSTRUCTION),
        agent=AgentSpec(name=agent_name, model=model, max_turns=6, max_tokens=1024),
        run_id=f"iso-{agent_name}",
        run_cwd=run_root,
    )
    result = await run_local_agent(spec)
    messages_path = result.run_dir / "messages.jsonl"
    events_path = result.run_dir / "events.jsonl"

    tool_calls: list[str] = []
    tool_results: list[dict] = []
    non_empty_assistant_turns = 0
    if messages_path.exists():
        for line in messages_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            msg = json.loads(line)
            content = msg.get("content", [])
            if isinstance(content, list):
                for block in content:
                    kind = block.get("type")
                    if kind == "tool_use":
                        tool_calls.append(block.get("name", "?"))
                    elif kind == "tool_result":
                        tool_results.append(block)
                if msg.get("role") == "assistant" and any(
                    (b.get("type") == "text" and (b.get("text") or "").strip())
                    or b.get("type") == "tool_use"
                    for b in content
                ):
                    non_empty_assistant_turns += 1

    artifact_exists = (cwd / "hello.txt").exists()
    return {
        "agent": agent_name,
        "run_id": result.run_id,
        "run_dir": str(result.run_dir),
        "tool_calls": tool_calls,
        "non_empty_assistant_turns": non_empty_assistant_turns,
        "hello_txt_exists": artifact_exists,
        "hello_txt_contents": (
            (cwd / "hello.txt").read_text(encoding="utf-8").strip() if artifact_exists else None
        ),
        "message_count": sum(1 for _ in messages_path.open("r")) if messages_path.exists() else 0,
        "event_count": sum(1 for _ in events_path.open("r")) if events_path.exists() else 0,
    }


async def _main() -> int:
    agents = sys.argv[1:] or ["default", "planner_executor"]
    model = "gemini-2.0-flash"

    tmp_root = Path("/tmp/oh-iso-test")
    if tmp_root.exists():
        shutil.rmtree(tmp_root)
    tmp_root.mkdir(parents=True)

    all_reports: list[dict] = []
    for agent_name in agents:
        agent_cwd = tmp_root / agent_name / "workspace"
        agent_cwd.mkdir(parents=True, exist_ok=True)
        run_root = tmp_root / agent_name / "runs"
        run_root.mkdir(parents=True, exist_ok=True)
        print(f"\n=== running {agent_name} on {agent_cwd} ===")
        try:
            report = await _run_one(
                agent_name, model=model, cwd=agent_cwd, run_root=run_root
            )
        except Exception as exc:
            import traceback as _tb
            report = {
                "agent": agent_name,
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": _tb.format_exc(),
            }
        all_reports.append(report)
        print(json.dumps(report, indent=2, default=str))

    print("\n=== SUMMARY ===")
    for r in all_reports:
        name = r["agent"]
        if r.get("error"):
            print(f"  {name:20s}  FAILED: {r['error']}")
            continue
        ok = "OK " if r["hello_txt_exists"] else "BAD"
        print(
            f"  {name:20s}  [{ok}]  tool_calls={r['tool_calls']}  "
            f"non_empty_turns={r['non_empty_assistant_turns']}  "
            f"hello.txt={r.get('hello_txt_contents')!r}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
