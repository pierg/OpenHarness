"""Workspace-backed tool implementations for the AgentWorkspace protocol.

These tools adapt the abstract ``AgentWorkspace`` into the concrete
``BaseTool`` interface expected by the query engine, so agent logic remains
independent of the underlying execution substrate.
"""

from __future__ import annotations

import posixpath

from openharness.agents.contracts import AgentWorkspace
from openharness.tools.base import BaseTool, ToolExecutionContext, ToolRegistry, ToolResult
from openharness.tools.bash_tool import BashToolInput
from openharness.tools.file_edit_tool import FileEditToolInput
from openharness.tools.file_read_tool import FileReadToolInput
from openharness.tools.file_write_tool import FileWriteToolInput

DEFAULT_REMOTE_TOOL_NAMES: tuple[str, ...] = ("bash", "read_file", "write_file", "edit_file")


class RemoteToolRegistryFactory:
    """Build a tool registry bound to an abstract workspace."""

    def __init__(self, tool_names: tuple[str, ...] = DEFAULT_REMOTE_TOOL_NAMES) -> None:
        self._tool_names = tuple(tool_names)

    def build(self, workspace: AgentWorkspace) -> ToolRegistry:
        registry = ToolRegistry()
        for name in self._tool_names:
            registry.register(_build_tool(name, workspace))
        return registry


class RemoteBashTool(BaseTool):
    """Execute shell commands in an abstract workspace."""

    name = "bash"
    description = "Run a shell command in the configured workspace."
    input_model = BashToolInput

    def __init__(self, workspace: AgentWorkspace) -> None:
        self._workspace = workspace

    async def execute(self, arguments: BashToolInput, context: ToolExecutionContext) -> ToolResult:
        del context
        cwd = (
            resolve_workspace_path(self._workspace.cwd, arguments.cwd)
            if arguments.cwd
            else self._workspace.cwd
        )
        result = await self._workspace.run_shell(
            arguments.command, cwd=cwd, timeout_seconds=arguments.timeout_seconds
        )
        return ToolResult(
            output=format_command_output(result.stdout, result.stderr),
            is_error=result.return_code != 0,
            metadata={"returncode": result.return_code, "cwd": cwd},
        )


class RemoteFileReadTool(BaseTool):
    """Read a UTF-8 text file from an abstract workspace."""

    name = "read_file"
    description = "Read a UTF-8 text file from the configured workspace."
    input_model = FileReadToolInput

    def __init__(self, workspace: AgentWorkspace) -> None:
        self._workspace = workspace

    def is_read_only(self, arguments: FileReadToolInput) -> bool:
        del arguments
        return True

    async def execute(self, arguments: FileReadToolInput, context: ToolExecutionContext) -> ToolResult:
        del context
        path = resolve_workspace_path(self._workspace.cwd, arguments.path)
        if await self._workspace.dir_exists(path):
            return ToolResult(output=f"Cannot read directory: {path}", is_error=True)
        if not await self._workspace.file_exists(path):
            return ToolResult(output=f"File not found: {path}", is_error=True)
        raw = await self._workspace.read_file(path)
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


class RemoteFileWriteTool(BaseTool):
    """Write a text file to an abstract workspace."""

    name = "write_file"
    description = "Create or overwrite a text file in the configured workspace."
    input_model = FileWriteToolInput

    def __init__(self, workspace: AgentWorkspace) -> None:
        self._workspace = workspace

    async def execute(self, arguments: FileWriteToolInput, context: ToolExecutionContext) -> ToolResult:
        del context
        path = resolve_workspace_path(self._workspace.cwd, arguments.path)
        await self._workspace.write_file(
            path, arguments.content.encode("utf-8"),
            create_directories=arguments.create_directories,
        )
        return ToolResult(output=f"Wrote {path}")


class RemoteFileEditTool(BaseTool):
    """Edit a text file in an abstract workspace by replacing a string."""

    name = "edit_file"
    description = "Edit an existing file by replacing a string."
    input_model = FileEditToolInput

    def __init__(self, workspace: AgentWorkspace) -> None:
        self._workspace = workspace

    async def execute(self, arguments: FileEditToolInput, context: ToolExecutionContext) -> ToolResult:
        del context
        path = resolve_workspace_path(self._workspace.cwd, arguments.path)
        if not await self._workspace.file_exists(path):
            return ToolResult(output=f"File not found: {path}", is_error=True)
        original = (await self._workspace.read_file(path)).decode("utf-8")
        if arguments.old_str not in original:
            return ToolResult(output="old_str was not found in the file", is_error=True)
        updated = (
            original.replace(arguments.old_str, arguments.new_str)
            if arguments.replace_all
            else original.replace(arguments.old_str, arguments.new_str, 1)
        )
        await self._workspace.write_file(path, updated.encode("utf-8"), create_directories=False)
        return ToolResult(output=f"Updated {path}")


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def resolve_workspace_path(base: str, candidate: str) -> str:
    """Resolve *candidate* against *base*, keeping it within a POSIX root."""
    if candidate.startswith("/"):
        return normalize_workspace_path(candidate)
    return normalize_workspace_path(posixpath.join(base, candidate))


def normalize_workspace_path(path: str) -> str:
    """Normalise a POSIX path, ensuring it starts with '/'."""
    normalized = posixpath.normpath(path)
    return normalized if normalized.startswith("/") else f"/{normalized}"


def format_command_output(stdout: str | None, stderr: str | None) -> str:
    """Combine stdout and stderr into a single bounded tool response."""
    parts = [p.rstrip() for p in (stdout, stderr) if p]
    text = "\n".join(parts).strip()
    if not text:
        return "(no output)"
    return f"{text[:12000]}\n...[truncated]..." if len(text) > 12000 else text


def _build_tool(tool_name: str, workspace: AgentWorkspace) -> BaseTool:
    builders: dict[str, type[BaseTool]] = {
        "bash": RemoteBashTool,
        "read_file": RemoteFileReadTool,
        "write_file": RemoteFileWriteTool,
        "edit_file": RemoteFileEditTool,
    }
    if tool_name not in builders:
        raise ValueError(f"Unknown remote tool: {tool_name!r}")
    return builders[tool_name](workspace)
