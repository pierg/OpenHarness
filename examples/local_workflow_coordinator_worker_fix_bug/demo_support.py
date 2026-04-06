"""Shared setup helpers for the coordinator/worker workflow demos."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
SOURCE_OH_DIR = HERE / ".openharness"
WORKFLOW_NAME = "coordinator_worker_bugfix"
TOPOLOGY_NAME = "coordinator_worker"

BUGGY_CODE = """\
def sum_evens(numbers):
    # BUG: should filter n % 2 == 0, not n % 2 == 1
    return sum(n for n in numbers if n % 2 == 1)

if __name__ == "__main__":
    result = sum_evens([1, 2, 3, 4, 5, 6])
    print(result)
"""

INSTRUCTION = (
    "The file sum_evens.py in the current directory contains a function "
    "`sum_evens(numbers)` that should return the sum of all even numbers in a list, "
    "but it currently returns the sum of the odd numbers instead.\n\n"
    "Coordinate your workers to fix the bug and verify that the script prints `12`."
)


def seed_workspace(
    workspace_dir: Path,
    *,
    model: str,
    include_workflow_yaml: bool,
) -> None:
    """Create the demo workspace and copy the example configs into it."""
    (workspace_dir / "sum_evens.py").write_text(BUGGY_CODE, encoding="utf-8")

    target_oh_dir = workspace_dir / ".openharness"
    target_oh_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(SOURCE_OH_DIR / "agent_configs", target_oh_dir / "agent_configs")
    if include_workflow_yaml:
        shutil.copytree(
            SOURCE_OH_DIR / "workflow_configs",
            target_oh_dir / "workflow_configs",
        )

    for path in target_oh_dir.rglob("*.yaml"):
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace("__MODEL__", model), encoding="utf-8")


def script_prints_twelve(workspace_dir: Path) -> bool:
    """Return True when the demo script now produces the expected output."""
    proc = subprocess.run(
        [sys.executable, str(workspace_dir / "sum_evens.py")],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    return proc.stdout.strip() == "12"


def format_result_lines(
    result: Any,
    *,
    passed: bool,
) -> tuple[str, ...]:
    """Return concise log lines for one workflow run."""
    def _message_text(message: Any) -> str:
        text = getattr(message, "text", None)
        if text is not None:
            return str(text)

        payload = getattr(message, "payload", {}) or {}
        if "content" in payload:
            return str(payload["content"])
        if "summary" in payload:
            return str(payload["summary"])
        return str(payload)

    lines = [
        f"Workflow: {result.workflow_name} ({result.topology})",
        f"Team:     {result.team_name}",
        f"Passed:   {passed}",
        f"Final:    {result.final_text}",
    ]
    if result.mailbox_messages:
        lines.append("Mailbox:")
        lines.extend(
            f"  - {message.sender} -> {message.recipient}: {_message_text(message)}"
            for message in result.mailbox_messages
        )
    return tuple(lines)
