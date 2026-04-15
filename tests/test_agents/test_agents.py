"""Tests for openharness.agents."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from openharness.agents import AgentConfig, SimpleAgent, TaskDefinition
from openharness.observability import ObservationScope
from openharness.permissions.modes import PermissionMode
from openharness.runtime.session import AgentLogPaths, AgentRuntime
from openharness.workspace import CommandResult, LocalWorkspace, Workspace
from openharness.tools import WorkspaceToolRegistryFactory, normalize_tool_name
from openharness.api.client import ApiMessageCompleteEvent, ApiMessageRequest
from openharness.api.usage import UsageSnapshot
from openharness.engine.messages import (
    ConversationMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from openharness.engine.query import _trace_model_input, _trace_model_output
from openharness.tools.bash_tool import format_command_output


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeWorkspace:
    def __init__(self, cwd: str = "/workspace") -> None:
        self._cwd = cwd
        self.files: dict[str, bytes] = {}
        self.shell_calls: list[str] = []

    @property
    def cwd(self) -> str:
        return self._cwd

    async def run_shell(
        self, command: str, *, cwd: str | None = None, timeout_seconds: int | None = None
    ) -> CommandResult:
        self.shell_calls.append(command)
        return CommandResult(stdout="ok\n")

    async def read_file(self, path: str) -> bytes:
        return self.files[path]

    async def write_file(
        self, path: str, content: bytes, *, create_directories: bool = True
    ) -> None:
        self.files[path] = content

    async def file_exists(self, path: str) -> bool:
        return path in self.files

    async def dir_exists(self, path: str) -> bool:
        prefix = path.rstrip("/") + "/"
        return any(k.startswith(prefix) for k in self.files)


@dataclass
class _FakeResponse:
    message: ConversationMessage
    usage: UsageSnapshot = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.usage is None:
            self.usage = UsageSnapshot(input_tokens=5, output_tokens=3)


class FakeApiClient:
    def __init__(self, responses: list[_FakeResponse]) -> None:
        self._responses = list(responses)
        self.requests: list[ApiMessageRequest] = []

    async def stream_message(self, request: ApiMessageRequest):
        self.requests.append(request)
        response = self._responses.pop(0)
        yield ApiMessageCompleteEvent(
            message=response.message, usage=response.usage, stop_reason=None
        )


@dataclass
class _TraceCall:
    kind: str
    name: str


class _RecordingHandle:
    def __init__(self, observer: "_RecordingTraceObserver", kind: str, name: str) -> None:
        self.trace_id = "trace-test"
        self._observer = observer
        self._kind = kind
        self._name = name

    def update(self, **kwargs):
        self._observer.updates.append((self._kind, self._name, kwargs))

    def close(self) -> None:
        self._observer.closed.append((self._kind, self._name))


class _RecordingTraceObserver:
    enabled = True
    trace_id = "trace-test"

    def __init__(self) -> None:
        self.calls: list[_TraceCall] = []
        self.updates: list[tuple[str, str, dict]] = []
        self.closed: list[tuple[str, str]] = []

    def start_session(self, *, metadata=None) -> None:
        del metadata

    def end_session(self, *, output=None, metadata=None) -> None:
        del output, metadata

    def start_model_call(self, *, model: str, input, metadata=None, model_parameters=None):
        del model, input, metadata, model_parameters
        self.calls.append(_TraceCall(kind="model", name="model"))
        return _RecordingHandle(self, "model", "model")

    def model_call(self, *, model: str, input, metadata=None, model_parameters=None):
        return ObservationScope(
            self.start_model_call(
                model=model,
                input=input,
                metadata=metadata,
                model_parameters=model_parameters,
            )
        )

    def start_tool_call(self, *, tool_name: str, tool_input, metadata=None):
        del tool_input, metadata
        name = f"tool:{tool_name}"
        self.calls.append(_TraceCall(kind="tool", name=name))
        return _RecordingHandle(self, "tool", name)

    def tool_call(self, *, tool_name: str, tool_input, metadata=None):
        return ObservationScope(
            self.start_tool_call(tool_name=tool_name, tool_input=tool_input, metadata=metadata)
        )

    def start_span(self, *, name: str, input=None, metadata=None):
        del input, metadata
        self.calls.append(_TraceCall(kind="span", name=name))
        return _RecordingHandle(self, "span", name)

    def span(self, *, name: str, input=None, metadata=None):
        return ObservationScope(self.start_span(name=name, input=input, metadata=metadata))

    def flush(self) -> None:
        return None


# ---------------------------------------------------------------------------
# AgentWorkspace protocol
# ---------------------------------------------------------------------------


def test_fake_workspace_satisfies_protocol():
    assert isinstance(FakeWorkspace(), Workspace)


def test_local_workspace_satisfies_protocol(tmp_path: Path):
    assert isinstance(LocalWorkspace(tmp_path), Workspace)


# ---------------------------------------------------------------------------
# WorkspaceToolRegistryFactory
# ---------------------------------------------------------------------------


def test_registry_factory_builds_requested_tools(tmp_path: Path):
    workspace = FakeWorkspace(cwd=str(tmp_path))
    registry = WorkspaceToolRegistryFactory(tool_names=("bash", "read_file")).build(workspace)
    names = {t.name for t in registry.list_tools()}
    assert names == {"bash", "read_file"}


def test_registry_factory_raises_for_unknown_tool(tmp_path: Path):
    workspace = FakeWorkspace(cwd=str(tmp_path))
    with pytest.raises(ValueError, match="Unknown tool"):
        WorkspaceToolRegistryFactory(tool_names=("nonexistent",)).build(workspace)


def test_registry_factory_supports_swarm_tools(tmp_path: Path):
    workspace = FakeWorkspace(cwd=str(tmp_path))
    registry = WorkspaceToolRegistryFactory(
        tool_names=("agent", "send_message", "task_stop")
    ).build(workspace)
    names = {t.name for t in registry.list_tools()}
    assert names == {"agent", "send_message", "task_stop"}


def test_normalize_tool_name_supports_upstream_aliases():
    assert normalize_tool_name("Read") == "read_file"
    assert normalize_tool_name("Edit") == "edit_file"
    assert normalize_tool_name("WebFetch") == "web_fetch"


# ---------------------------------------------------------------------------
# format_command_output (shared by BashTool and callers)
# ---------------------------------------------------------------------------


def test_format_command_output_both():
    assert format_command_output("out", "err") == "out\nerr"


def test_format_command_output_empty():
    assert format_command_output("", "") == "(no output)"


def test_format_command_output_truncates():
    assert format_command_output("x" * 20000, None).endswith("...[truncated]...")


# ---------------------------------------------------------------------------
# SimpleAgent end-to-end
# ---------------------------------------------------------------------------


async def test_simple_agent_writes_file_and_returns_result(tmp_path: Path):
    api_client = FakeApiClient(
        [
            _FakeResponse(
                message=ConversationMessage(
                    role="assistant",
                    content=[
                        ToolUseBlock(
                            id="t1",
                            name="write_file",
                            input={"path": f"{tmp_path}/hello.txt", "content": "Hello!\n"},
                        )
                    ],
                ),
                usage=UsageSnapshot(input_tokens=11, output_tokens=7),
            ),
            _FakeResponse(
                message=ConversationMessage(role="assistant", content=[TextBlock(text="Done.")]),
                usage=UsageSnapshot(input_tokens=5, output_tokens=3),
            ),
        ]
    )
    config = AgentConfig(
        model="claude-test",
        tools=("write_file",),
        max_turns=4,
        prompts={"system": "sys", "user": "usr"},
    )
    agent = SimpleAgent(config)
    workspace = FakeWorkspace(cwd=str(tmp_path))

    runtime = AgentRuntime(
        workspace=workspace,
        permission_mode=PermissionMode.FULL_AUTO,
        api_client=api_client,
        log_paths=AgentLogPaths(
            messages_path=str(tmp_path / "messages.jsonl"),
            events_path=str(tmp_path / "events.jsonl"),
        ),
    )

    result = await agent.run(
        task=TaskDefinition(instruction="Write hello.txt"),
        runtime=runtime,
    )

    assert result.final_text == "Done."
    assert result.input_tokens == 16
    assert result.output_tokens == 10
    assert workspace.files[f"{tmp_path}/hello.txt"] == b"Hello!\n"

    events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert any(e["type"] == "tool_started" for e in events)
    assert any(e["type"] == "tool_completed" for e in events)


async def test_simple_agent_only_registers_requested_tools(tmp_path: Path):
    api_client = FakeApiClient(
        [
            _FakeResponse(
                message=ConversationMessage(role="assistant", content=[TextBlock(text="ok")])
            )
        ]
    )
    config = AgentConfig(
        model="claude-test", tools=("bash",), max_turns=2, prompts={"system": "sys", "user": "usr"}
    )
    agent = SimpleAgent(config)
    workspace = FakeWorkspace(cwd=str(tmp_path))

    runtime = AgentRuntime(
        workspace=workspace,
        api_client=api_client,
    )

    await agent.run(task=TaskDefinition(instruction="Run bash"), runtime=runtime)
    tool_names = [t["name"] for t in api_client.requests[0].tools]
    assert tool_names == ["bash"]


async def test_simple_agent_emits_trace_stages(tmp_path: Path):
    api_client = FakeApiClient(
        [
            _FakeResponse(
                message=ConversationMessage(
                    role="assistant",
                    content=[
                        ToolUseBlock(
                            id="t1",
                            name="write_file",
                            input={"path": f"{tmp_path}/hello.txt", "content": "Hello!\n"},
                        )
                    ],
                ),
                usage=UsageSnapshot(input_tokens=3, output_tokens=2),
            ),
            _FakeResponse(
                message=ConversationMessage(role="assistant", content=[TextBlock(text="Done.")]),
                usage=UsageSnapshot(input_tokens=2, output_tokens=1),
            ),
        ]
    )
    trace_observer = _RecordingTraceObserver()
    config = AgentConfig(
        name="trace-demo",
        model="claude-test",
        tools=("write_file",),
        max_turns=4,
        prompts={"system": "sys", "user": "usr"},
    )
    agent = SimpleAgent(config)
    workspace = FakeWorkspace(cwd=str(tmp_path))

    runtime = AgentRuntime(
        workspace=workspace,
        permission_mode=PermissionMode.FULL_AUTO,
        api_client=api_client,
        trace_observer=trace_observer,
    )

    result = await agent.run(
        task=TaskDefinition(instruction="Write hello.txt"),
        runtime=runtime,
    )

    assert result.final_text == "Done."
    assert ("span", "agent:trace-demo") in trace_observer.closed
    assert ("model", "model") in trace_observer.closed
    assert ("tool", "tool:write_file") in trace_observer.closed


def test_trace_model_input_renders_structured_history():
    messages = [
        ConversationMessage.from_user_text("Original task"),
        ConversationMessage(role="assistant", content=[TextBlock(text="I will inspect the file.")]),
        ConversationMessage.from_user_text("Mailbox follow-up"),
    ]

    rendered = _trace_model_input("System prompt", messages)

    assert rendered == [
        {"role": "system", "content": "System prompt"},
        {"role": "user", "content": "Original task"},
        {"role": "assistant", "content": "I will inspect the file."},
        {"role": "user", "content": "Mailbox follow-up"},
    ]


def test_trace_model_input_renders_tool_results_as_tool_messages():
    messages = [
        ConversationMessage.from_user_text("Fix bug"),
        ConversationMessage(
            role="assistant",
            content=[ToolUseBlock(id="tool-1", name="read_file", input={"path": "main.py"})],
        ),
        ConversationMessage(
            role="user",
            content=[ToolResultBlock(tool_use_id="tool-1", content="def main(): pass")],
        ),
    ]

    rendered = _trace_model_input("", messages)

    assert rendered == [
        {"role": "user", "content": "Fix bug"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"name": "read_file", "arguments": {"path": "main.py"}}],
        },
        {
            "role": "tool",
            "name": "read_file",
            "content": "def main(): pass",
            "is_error": False,
        },
    ]


def test_trace_model_output_renders_structured_tool_calls():
    message = ConversationMessage(
        role="assistant",
        content=[ToolUseBlock(id="tool-1", name="bash", input={"command": "pytest -q"})],
    )

    assert _trace_model_output(message) == {
        "content": "",
        "tool_calls": [{"name": "bash", "arguments": {"command": "pytest -q"}}],
    }
