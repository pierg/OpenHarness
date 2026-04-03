"""Content search tool."""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, Field

from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult
from openharness.workspace import LocalWorkspace, Workspace


class GrepToolInput(BaseModel):
    """Arguments for the grep tool."""

    pattern: str = Field(description="Regular expression to search for")
    root: str | None = Field(default=None, description="Search root directory")
    file_glob: str = Field(default="**/*")
    case_sensitive: bool = Field(default=True)
    limit: int = Field(default=200, ge=1, le=2000)


class GrepTool(BaseTool):
    """Search text files for a regex pattern.

    Uses a pure-Python scanner for local workspaces and falls back to ``grep``
    via ``workspace.run_shell`` for remote substrates.
    """

    name = "grep"
    description = "Search file contents with a regular expression."
    input_model = GrepToolInput

    def __init__(self, workspace: Workspace | None = None) -> None:
        self._workspace = workspace

    def is_read_only(self, arguments: GrepToolInput) -> bool:
        del arguments
        return True

    async def execute(self, arguments: GrepToolInput, context: ToolExecutionContext) -> ToolResult:
        workspace = self._workspace or LocalWorkspace(context.cwd)

        if isinstance(workspace, LocalWorkspace):
            return self._execute_local(arguments, workspace)
        return await self._execute_via_shell(arguments, workspace)

    def _execute_local(self, arguments: GrepToolInput, workspace: LocalWorkspace) -> ToolResult:
        root = _resolve_path(Path(workspace.cwd), arguments.root)
        flags = 0 if arguments.case_sensitive else re.IGNORECASE
        pattern = re.compile(arguments.pattern, flags)
        matches: list[str] = []

        for path in sorted(root.glob(arguments.file_glob)):
            if len(matches) >= arguments.limit:
                break
            if not path.is_file():
                continue
            try:
                raw = path.read_bytes()
            except OSError:
                continue
            if b"\x00" in raw:
                continue
            text = raw.decode("utf-8", errors="replace")
            for line_no, line in enumerate(text.splitlines(), start=1):
                if pattern.search(line):
                    matches.append(f"{path.relative_to(root)}:{line_no}:{line}")
                    if len(matches) >= arguments.limit:
                        break

        if not matches:
            return ToolResult(output="(no matches)")
        return ToolResult(output="\n".join(matches))

    async def _execute_via_shell(self, arguments: GrepToolInput, workspace: Workspace) -> ToolResult:
        root = arguments.root or workspace.cwd
        flags = "" if arguments.case_sensitive else " -i"
        include = ""
        if arguments.file_glob != "**/*":
            include = f" --include={_sq(arguments.file_glob)}"
        cmd = (
            f"grep -rn{flags}{include} -m {arguments.limit} "
            f"-- {_sq(arguments.pattern)} {_sq(root)} "
            f"2>/dev/null | head -n {arguments.limit}"
        )
        result = await workspace.run_shell(cmd, cwd=root)
        lines = [ln for ln in result.stdout.strip().splitlines() if ln]
        if not lines:
            return ToolResult(output="(no matches)")
        prefix = root.rstrip("/") + "/"
        relative = [ln[len(prefix):] if ln.startswith(prefix) else ln for ln in lines]
        return ToolResult(output="\n".join(relative))


def _resolve_path(base: Path, candidate: str | None) -> Path:
    path = Path(candidate or ".").expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def _sq(s: str) -> str:
    """Shell-quote a string."""
    return "'" + s.replace("'", "'\\''") + "'"
