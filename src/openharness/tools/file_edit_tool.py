"""String-based file editing tool."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult
from openharness.workspace import LocalWorkspace, Workspace


class FileEditToolInput(BaseModel):
    """Arguments for the file edit tool."""

    path: str = Field(description="Path of the file to edit")
    old_str: str = Field(description="Existing text to replace")
    new_str: str = Field(description="Replacement text")
    replace_all: bool = Field(default=False)


class FileEditTool(BaseTool):
    """Replace text in an existing file."""

    name = "edit_file"
    description = "Edit an existing file by replacing a string."
    input_model = FileEditToolInput

    def __init__(self, workspace: Workspace | None = None) -> None:
        self._workspace = workspace

    async def execute(
        self,
        arguments: FileEditToolInput,
        context: ToolExecutionContext,
    ) -> ToolResult:
        workspace = self._workspace or LocalWorkspace(context.cwd)
        path = _resolve(workspace.cwd, arguments.path)

        if isinstance(workspace, LocalWorkspace):
            blocked = _validate_local_sandbox_path(path, workspace.cwd)
            if blocked is not None:
                return blocked

        if not await workspace.file_exists(path):
            return ToolResult(output=f"File not found: {path}", is_error=True)

        original = (await workspace.read_file(path)).decode("utf-8")
        if arguments.old_str not in original:
            return ToolResult(output="old_str was not found in the file", is_error=True)

        updated = (
            original.replace(arguments.old_str, arguments.new_str)
            if arguments.replace_all
            else original.replace(arguments.old_str, arguments.new_str, 1)
        )
        await workspace.write_file(path, updated.encode("utf-8"), create_directories=False)
        return ToolResult(output=f"Updated {path}")


def _validate_local_sandbox_path(path: str, cwd: str) -> ToolResult | None:
    from openharness.sandbox.session import is_docker_sandbox_active

    if not is_docker_sandbox_active():
        return None

    from openharness.sandbox.path_validator import validate_sandbox_path

    allowed, reason = validate_sandbox_path(Path(path), Path(cwd))
    if allowed:
        return None
    return ToolResult(output=f"Sandbox: {reason}", is_error=True)


def _resolve(base: str, candidate: str) -> str:
    p = Path(candidate).expanduser()
    if p.is_absolute():
        return str(p)
    return str((Path(base) / candidate).resolve())
