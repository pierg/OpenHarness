"""Tool for maintaining a project TODO file."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult
from openharness.workspace import LocalWorkspace, Workspace


class TodoWriteToolInput(BaseModel):
    """Arguments for TODO writes."""

    item: str = Field(description="TODO item text")
    checked: bool = Field(default=False)
    path: str = Field(default="TODO.md")


class TodoWriteTool(BaseTool):
    """Append an item to a TODO markdown file."""

    name = "todo_write"
    description = "Append a TODO item to a markdown checklist file."
    input_model = TodoWriteToolInput

    def __init__(self, workspace: Workspace | None = None) -> None:
        self._workspace = workspace

    async def execute(self, arguments: TodoWriteToolInput, context: ToolExecutionContext) -> ToolResult:
        workspace = self._workspace or LocalWorkspace(context.cwd)
        path = _resolve(workspace.cwd, arguments.path)
        prefix = "- [x]" if arguments.checked else "- [ ]"

        if await workspace.file_exists(path):
            existing = (await workspace.read_file(path)).decode("utf-8")
        else:
            existing = "# TODO\n"

        updated = existing.rstrip() + f"\n{prefix} {arguments.item}\n"
        await workspace.write_file(path, updated.encode("utf-8"))
        return ToolResult(output=f"Updated {path}")


def _resolve(base: str, candidate: str) -> str:
    p = Path(candidate).expanduser()
    if p.is_absolute():
        return str(p)
    return str((Path(base) / candidate).resolve())
