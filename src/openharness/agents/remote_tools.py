"""Tool registry factory for workspace-backed agent runs.

Constructs standard tools bound to the provided ``Workspace``.  Every tool
that does I/O accepts a workspace — the same class works locally and remotely.
"""

from __future__ import annotations

from openharness.tools.base import BaseTool, ToolRegistry
from openharness.tools.bash_tool import BashTool
from openharness.tools.enter_worktree_tool import EnterWorktreeTool
from openharness.tools.exit_worktree_tool import ExitWorktreeTool
from openharness.tools.file_edit_tool import FileEditTool
from openharness.tools.file_read_tool import FileReadTool
from openharness.tools.file_write_tool import FileWriteTool
from openharness.tools.glob_tool import GlobTool
from openharness.tools.grep_tool import GrepTool
from openharness.tools.notebook_edit_tool import NotebookEditTool
from openharness.tools.remote_trigger_tool import RemoteTriggerTool
from openharness.tools.todo_write_tool import TodoWriteTool
from openharness.workspace import Workspace

DEFAULT_TOOL_NAMES: tuple[str, ...] = (
    "bash", "read_file", "write_file", "edit_file", "glob", "grep",
)

WORKSPACE_TOOLS: dict[str, type[BaseTool]] = {
    "bash": BashTool,
    "read_file": FileReadTool,
    "write_file": FileWriteTool,
    "edit_file": FileEditTool,
    "glob": GlobTool,
    "grep": GrepTool,
    "notebook_edit": NotebookEditTool,
    "todo_write": TodoWriteTool,
    "enter_worktree": EnterWorktreeTool,
    "exit_worktree": ExitWorktreeTool,
    "remote_trigger": RemoteTriggerTool,
}


class WorkspaceToolRegistryFactory:
    """Build a tool registry with standard tools bound to a workspace."""

    def __init__(self, tool_names: tuple[str, ...] = DEFAULT_TOOL_NAMES) -> None:
        self._tool_names = tuple(tool_names)

    def build(self, workspace: Workspace) -> ToolRegistry:
        registry = ToolRegistry()
        for name in self._tool_names:
            if name not in WORKSPACE_TOOLS:
                raise ValueError(f"Unknown tool: {name!r}")
            registry.register(WORKSPACE_TOOLS[name](workspace=workspace))
        return registry


# Backward-compatible aliases
RemoteToolRegistryFactory = WorkspaceToolRegistryFactory
DEFAULT_REMOTE_TOOL_NAMES = DEFAULT_TOOL_NAMES
