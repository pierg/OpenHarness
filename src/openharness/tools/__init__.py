"""Built-in tool registration."""

import importlib
import re
from collections.abc import Callable, Collection
from typing import Any

from openharness.tools.ask_user_question_tool import AskUserQuestionTool
from openharness.tools.agent_tool import AgentTool
from openharness.tools.bash_tool import BashTool
from openharness.tools.base import (
    BaseTool,
    ToolExecutionContext,
    ToolRegistry,
    ToolRegistryFactory,
    ToolResult,
)
from openharness.tools.brief_tool import BriefTool
from openharness.tools.config_tool import ConfigTool
from openharness.tools.cron_create_tool import CronCreateTool
from openharness.tools.cron_delete_tool import CronDeleteTool
from openharness.tools.cron_list_tool import CronListTool
from openharness.tools.cron_toggle_tool import CronToggleTool
from openharness.tools.enter_plan_mode_tool import EnterPlanModeTool
from openharness.tools.enter_worktree_tool import EnterWorktreeTool
from openharness.tools.exit_plan_mode_tool import ExitPlanModeTool
from openharness.tools.exit_worktree_tool import ExitWorktreeTool
from openharness.tools.file_edit_tool import FileEditTool
from openharness.tools.file_read_tool import FileReadTool
from openharness.tools.file_write_tool import FileWriteTool
from openharness.tools.glob_tool import GlobTool
from openharness.tools.grep_tool import GrepTool
from openharness.tools.list_mcp_resources_tool import ListMcpResourcesTool
from openharness.tools.lsp_tool import LspTool
from openharness.tools.mcp_auth_tool import McpAuthTool
from openharness.tools.mcp_tool import McpToolAdapter
from openharness.tools.notebook_edit_tool import NotebookEditTool
from openharness.tools.read_mcp_resource_tool import ReadMcpResourceTool
from openharness.tools.remote_trigger_tool import RemoteTriggerTool
from openharness.tools.send_message_tool import SendMessageTool
from openharness.tools.skill_tool import SkillTool
from openharness.tools.sleep_tool import SleepTool
from openharness.tools.task_create_tool import TaskCreateTool
from openharness.tools.task_get_tool import TaskGetTool
from openharness.tools.task_list_tool import TaskListTool
from openharness.tools.task_output_tool import TaskOutputTool
from openharness.tools.task_stop_tool import TaskStopTool
from openharness.tools.task_update_tool import TaskUpdateTool
from openharness.tools.team_create_tool import TeamCreateTool
from openharness.tools.team_delete_tool import TeamDeleteTool
from openharness.tools.think_tool import ThinkTool
from openharness.tools.todo_write_tool import TodoWriteTool
from openharness.tools.tool_search_tool import ToolSearchTool
from openharness.tools.web_fetch_tool import WebFetchTool
from openharness.tools.web_search_tool import WebSearchTool
from openharness.workspace import Workspace

WORKSPACE_TOOLS: dict[str, type[BaseTool]] = {
    "bash": BashTool,
    "read_file": FileReadTool,
    "write_file": FileWriteTool,
    "edit_file": FileEditTool,
    "glob": GlobTool,
    "grep": GrepTool,
    "notebook_edit": NotebookEditTool,
    "think": ThinkTool,
    "todo_write": TodoWriteTool,
    "enter_worktree": EnterWorktreeTool,
    "exit_worktree": ExitWorktreeTool,
    "remote_trigger": RemoteTriggerTool,
}


class _LazyTool(BaseTool):
    """Proxy a tool whose import has heavyweight side effects until first use."""

    def __init__(
        self,
        *,
        name: str,
        description: str,
        module_name: str,
        class_name: str,
    ) -> None:
        self.name = name
        self.description = description
        self._module_name = module_name
        self._class_name = class_name
        self._tool: BaseTool | None = None

    @property
    def input_model(self) -> Any:
        return self._load().input_model

    async def execute(self, arguments, context: ToolExecutionContext) -> ToolResult:
        return await self._load().execute(arguments, context)

    def is_read_only(self, arguments) -> bool:
        return self._load().is_read_only(arguments)

    def to_api_schema(self) -> dict[str, Any]:
        return self._load().to_api_schema()

    def _load(self) -> BaseTool:
        if self._tool is None:
            module = importlib.import_module(self._module_name)
            tool_type = getattr(module, self._class_name)
            self._tool = tool_type()
        return self._tool


def _mailbox_read_tool() -> BaseTool:
    return _LazyTool(
        name="mailbox_read",
        description="Read messages from a swarm mailbox.",
        module_name="openharness.tools.mailbox_read_tool",
        class_name="MailboxReadTool",
    )


WORKSPACE_COMPAT_TOOLS: dict[str, Callable[[], BaseTool]] = {
    "agent": AgentTool,
    "send_message": SendMessageTool,
    "mailbox_read": _mailbox_read_tool,
    "task_stop": TaskStopTool,
    "skill": SkillTool,
    "task_create": TaskCreateTool,
    "task_get": TaskGetTool,
    "task_list": TaskListTool,
    "task_output": TaskOutputTool,
    "task_update": TaskUpdateTool,
    "team_create": TeamCreateTool,
    "team_delete": TeamDeleteTool,
    "web_fetch": WebFetchTool,
    "web_search": WebSearchTool,
}

DEFAULT_TOOL_NAMES: tuple[str, ...] = (
    "bash",
    "read_file",
    "write_file",
    "edit_file",
    "glob",
    "grep",
)

_TOOL_ALIAS_OVERRIDES = {
    "*": "*",
    "bash": "bash",
    "read": "read_file",
    "readfile": "read_file",
    "file_read": "read_file",
    "read_file": "read_file",
    "write": "write_file",
    "writefile": "write_file",
    "file_write": "write_file",
    "write_file": "write_file",
    "edit": "edit_file",
    "editfile": "edit_file",
    "file_edit": "edit_file",
    "edit_file": "edit_file",
    "glob": "glob",
    "grep": "grep",
    "notebookedit": "notebook_edit",
    "notebook_edit": "notebook_edit",
    "think": "think",
    "todowrite": "todo_write",
    "todo_write": "todo_write",
    "enterworktree": "enter_worktree",
    "enter_worktree": "enter_worktree",
    "exitworktree": "exit_worktree",
    "exit_worktree": "exit_worktree",
    "remotetrigger": "remote_trigger",
    "remote_trigger": "remote_trigger",
    "askuserquestion": "ask_user_question",
    "ask_user_question": "ask_user_question",
    "config": "config",
    "brief": "brief",
    "sleep": "sleep",
    "enterplanmode": "enter_plan_mode",
    "enter_plan_mode": "enter_plan_mode",
    "exitplanmode": "exit_plan_mode",
    "exit_plan_mode": "exit_plan_mode",
    "croncreate": "cron_create",
    "cron_create": "cron_create",
    "cronlist": "cron_list",
    "cron_list": "cron_list",
    "crondelete": "cron_delete",
    "cron_delete": "cron_delete",
    "crontoggle": "cron_toggle",
    "cron_toggle": "cron_toggle",
    "taskcreate": "task_create",
    "task_create": "task_create",
    "taskget": "task_get",
    "task_get": "task_get",
    "tasklist": "task_list",
    "task_list": "task_list",
    "taskstop": "task_stop",
    "task_stop": "task_stop",
    "taskoutput": "task_output",
    "task_output": "task_output",
    "taskupdate": "task_update",
    "task_update": "task_update",
    "agent": "agent",
    "sendmessage": "send_message",
    "send_message": "send_message",
    "mailboxread": "mailbox_read",
    "mailbox_read": "mailbox_read",
    "teamcreate": "team_create",
    "team_create": "team_create",
    "teamdelete": "team_delete",
    "team_delete": "team_delete",
    "skill": "skill",
    "webfetch": "web_fetch",
    "web_fetch": "web_fetch",
    "websearch": "web_search",
    "web_search": "web_search",
}


def normalize_tool_name(name: str) -> str:
    """Normalize upstream aliases such as ``Read`` into runtime tool names."""
    if name.startswith("mcp__"):
        return name
    collapsed = re.sub(r"[^a-z0-9_]+", "", name.strip().lower().replace("-", "_"))
    return _TOOL_ALIAS_OVERRIDES.get(collapsed, name.strip())


def _normalize_tool_name_set(tool_names: Collection[str] | None) -> set[str] | None:
    if tool_names is None:
        return None
    return {normalize_tool_name(name) for name in tool_names}


def _register_if_allowed(
    registry: ToolRegistry,
    tool: BaseTool,
    *,
    allowed: set[str] | None,
    disallowed: set[str] | None,
) -> None:
    if allowed is not None and "*" not in allowed and tool.name not in allowed:
        return
    if disallowed is not None and tool.name in disallowed:
        return
    registry.register(tool)


class WorkspaceToolRegistryFactory:
    """Build a tool registry with standard tools bound to a workspace."""

    def __init__(self, tool_names: tuple[str, ...] = DEFAULT_TOOL_NAMES) -> None:
        self._tool_names = tuple(tool_names)

    def build(self, workspace: Workspace) -> ToolRegistry:
        registry = ToolRegistry()
        for name in self._tool_names:
            normalized = normalize_tool_name(name)
            if normalized == "*":
                for workspace_name, tool_type in WORKSPACE_TOOLS.items():
                    if registry.get(workspace_name) is None:
                        registry.register(tool_type(workspace=workspace))
                for compat_name, tool_factory in WORKSPACE_COMPAT_TOOLS.items():
                    if registry.get(compat_name) is None:
                        registry.register(tool_factory())
                continue
            if normalized in WORKSPACE_TOOLS:
                registry.register(WORKSPACE_TOOLS[normalized](workspace=workspace))
                continue
            if normalized in WORKSPACE_COMPAT_TOOLS:
                registry.register(WORKSPACE_COMPAT_TOOLS[normalized]())
                continue
            raise ValueError(f"Unknown tool: {name!r}")
        return registry


def create_default_tool_registry(
    mcp_manager=None,
    *,
    allowed_tools: Collection[str] | None = None,
    disallowed_tools: Collection[str] | None = None,
) -> ToolRegistry:
    """Return the default built-in tool registry."""
    allowed = _normalize_tool_name_set(allowed_tools)
    disallowed = _normalize_tool_name_set(disallowed_tools)

    registry = ToolRegistry()
    for tool in (
        BashTool(),
        AskUserQuestionTool(),
        FileReadTool(),
        FileWriteTool(),
        FileEditTool(),
        NotebookEditTool(),
        LspTool(),
        McpAuthTool(),
        GlobTool(),
        GrepTool(),
        SkillTool(),
        ThinkTool(),
        ToolSearchTool(),
        WebFetchTool(),
        WebSearchTool(),
        ConfigTool(),
        BriefTool(),
        SleepTool(),
        EnterWorktreeTool(),
        ExitWorktreeTool(),
        TodoWriteTool(),
        EnterPlanModeTool(),
        ExitPlanModeTool(),
        CronCreateTool(),
        CronListTool(),
        CronDeleteTool(),
        CronToggleTool(),
        RemoteTriggerTool(),
        TaskCreateTool(),
        TaskGetTool(),
        TaskListTool(),
        TaskStopTool(),
        TaskOutputTool(),
        TaskUpdateTool(),
        AgentTool(),
        SendMessageTool(),
        _mailbox_read_tool(),
        TeamCreateTool(),
        TeamDeleteTool(),
    ):
        _register_if_allowed(
            registry,
            tool,
            allowed=allowed,
            disallowed=disallowed,
        )
    if mcp_manager is not None:
        _register_if_allowed(
            registry,
            ListMcpResourcesTool(mcp_manager),
            allowed=allowed,
            disallowed=disallowed,
        )
        _register_if_allowed(
            registry,
            ReadMcpResourceTool(mcp_manager),
            allowed=allowed,
            disallowed=disallowed,
        )
        for tool_info in mcp_manager.list_tools():
            adapted = McpToolAdapter(mcp_manager, tool_info)
            _register_if_allowed(
                registry,
                adapted,
                allowed=allowed,
                disallowed=disallowed,
            )
    return registry


__all__ = [
    "BaseTool",
    "DEFAULT_TOOL_NAMES",
    "ToolExecutionContext",
    "ToolRegistry",
    "ToolRegistryFactory",
    "ToolResult",
    "WORKSPACE_TOOLS",
    "WORKSPACE_COMPAT_TOOLS",
    "WorkspaceToolRegistryFactory",
    "create_default_tool_registry",
    "normalize_tool_name",
]
