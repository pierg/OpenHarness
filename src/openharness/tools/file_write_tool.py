"""File writing tool."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult
from openharness.workspace import LocalWorkspace, Workspace


class FileWriteToolInput(BaseModel):
    """Arguments for the file write tool."""

    path: str = Field(description="Path of the file to write")
    content: str = Field(description="Full file contents")
    create_directories: bool = Field(default=True)


class FileWriteTool(BaseTool):
    """Write complete file contents."""

    name = "write_file"
    description = "Create or overwrite a text file."
    input_model = FileWriteToolInput

    def __init__(self, workspace: Workspace | None = None) -> None:
        self._workspace = workspace

    async def execute(
        self,
        arguments: FileWriteToolInput,
        context: ToolExecutionContext,
    ) -> ToolResult:
        workspace = self._workspace or LocalWorkspace(context.cwd)
        path = _resolve(workspace.cwd, arguments.path)

        if isinstance(workspace, LocalWorkspace):
            blocked = _validate_local_sandbox_path(path, workspace.cwd)
            if blocked is not None:
                return blocked

        await workspace.write_file(
            path,
            arguments.content.encode("utf-8"),
            create_directories=arguments.create_directories,
        )
        return ToolResult(output=f"Wrote {path}")


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
