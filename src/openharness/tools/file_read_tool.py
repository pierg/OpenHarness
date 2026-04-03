"""File reading tool."""

from __future__ import annotations

from pydantic import BaseModel, Field

from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult
from openharness.workspace import LocalWorkspace, Workspace


class FileReadToolInput(BaseModel):
    """Arguments for the file read tool."""

    path: str = Field(description="Path of the file to read")
    offset: int = Field(default=0, ge=0, description="Zero-based starting line")
    limit: int = Field(default=200, ge=1, le=2000, description="Number of lines to return")


class FileReadTool(BaseTool):
    """Read a UTF-8 text file with line numbers."""

    name = "read_file"
    description = "Read a text file."
    input_model = FileReadToolInput

    def __init__(self, workspace: Workspace | None = None) -> None:
        self._workspace = workspace

    def is_read_only(self, arguments: FileReadToolInput) -> bool:
        del arguments
        return True

    async def execute(self, arguments: FileReadToolInput, context: ToolExecutionContext) -> ToolResult:
        workspace = self._workspace or LocalWorkspace(context.cwd)
        path = _resolve(workspace.cwd, arguments.path)

        if await workspace.dir_exists(path):
            return ToolResult(output=f"Cannot read directory: {path}", is_error=True)
        if not await workspace.file_exists(path):
            return ToolResult(output=f"File not found: {path}", is_error=True)

        raw = await workspace.read_file(path)
        if b"\x00" in raw:
            return ToolResult(output=f"Binary file cannot be read as text: {path}", is_error=True)

        text = raw.decode("utf-8", errors="replace")
        lines = text.splitlines()
        selected = lines[arguments.offset : arguments.offset + arguments.limit]
        if not selected:
            return ToolResult(output=f"(no content in selected range for {path})")
        numbered = [
            f"{arguments.offset + i + 1:>6}\t{line}" for i, line in enumerate(selected)
        ]
        return ToolResult(output="\n".join(numbered))


def _resolve(base: str, candidate: str) -> str:
    from pathlib import Path

    p = Path(candidate).expanduser()
    if p.is_absolute():
        return str(p)
    return str((Path(base) / candidate).resolve())
