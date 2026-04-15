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
    """Add or update an item in a TODO markdown file."""

    name = "todo_write"
    description = (
        "Add a new TODO item or mark an existing one as done in a markdown checklist file."
    )
    input_model = TodoWriteToolInput

    def __init__(self, workspace: Workspace | None = None) -> None:
        self._workspace = workspace

    async def execute(
        self, arguments: TodoWriteToolInput, context: ToolExecutionContext
    ) -> ToolResult:
        workspace = self._workspace or LocalWorkspace(context.cwd)
        path = _resolve(workspace.cwd, arguments.path)

        if await workspace.file_exists(path):
            existing = (await workspace.read_file(path)).decode("utf-8")
        else:
            existing = "# TODO\n"

        unchecked_line = f"- [ ] {arguments.item}"
        checked_line = f"- [x] {arguments.item}"
        target_line = checked_line if arguments.checked else unchecked_line

        if unchecked_line in existing and arguments.checked:
            updated = existing.replace(unchecked_line, checked_line, 1)
        elif target_line in existing:
            return ToolResult(output=f"No change needed in {path}")
        else:
            updated = existing.rstrip() + f"\n{target_line}\n"

        await workspace.write_file(path, updated.encode("utf-8"))
        return ToolResult(output=f"Updated {path}")


def _resolve(base: str, candidate: str) -> str:
    p = Path(candidate).expanduser()
    if p.is_absolute():
        return str(p)
    return str((Path(base) / candidate).resolve())
