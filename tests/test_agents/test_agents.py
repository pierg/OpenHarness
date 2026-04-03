"""Tests for openharness.agents."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from openharness.agents import AgentLogPaths, OpenHarnessSimpleAgent
from openharness.workspace import CommandResult, LocalWorkspace, Workspace
from openharness.tools import WorkspaceToolRegistryFactory
from openharness.agents.simple import OpenHarnessSimpleAgentConfig
from openharness.api.client import ApiMessageCompleteEvent, ApiMessageRequest
from openharness.api.usage import UsageSnapshot
from openharness.engine.messages import ConversationMessage, TextBlock, ToolUseBlock
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

    async def run_shell(self, command: str, *, cwd: str | None = None, timeout_seconds: int | None = None) -> CommandResult:
        self.shell_calls.append(command)
        return CommandResult(stdout="ok\n")

    async def read_file(self, path: str) -> bytes:
        return self.files[path]

    async def write_file(self, path: str, content: bytes, *, create_directories: bool = True) -> None:
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
        yield ApiMessageCompleteEvent(message=response.message, usage=response.usage, stop_reason=None)


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


def test_registry_factory_builds_requested_tools():
    workspace = FakeWorkspace()
    registry = WorkspaceToolRegistryFactory(tool_names=("bash", "read_file")).build(workspace)
    names = {t.name for t in registry.list_tools()}
    assert names == {"bash", "read_file"}


def test_registry_factory_raises_for_unknown_tool():
    workspace = FakeWorkspace()
    with pytest.raises(ValueError, match="Unknown tool"):
        WorkspaceToolRegistryFactory(tool_names=("nonexistent",)).build(workspace)


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
# OpenHarnessSimpleAgent end-to-end
# ---------------------------------------------------------------------------


async def test_simple_agent_writes_file_and_returns_result(tmp_path: Path):
    api_client = FakeApiClient([
        _FakeResponse(
            message=ConversationMessage(role="assistant", content=[
                ToolUseBlock(id="t1", name="write_file", input={"path": "hello.txt", "content": "Hello!\n"})
            ]),
            usage=UsageSnapshot(input_tokens=11, output_tokens=7),
        ),
        _FakeResponse(
            message=ConversationMessage(role="assistant", content=[TextBlock(text="Done.")]),
            usage=UsageSnapshot(input_tokens=5, output_tokens=3),
        ),
    ])
    agent = OpenHarnessSimpleAgent(
        OpenHarnessSimpleAgentConfig(model="claude-test", tool_names=("write_file",), max_turns=4),
        api_client=api_client,
    )
    workspace = FakeWorkspace()
    result = await agent.run(
        "Write hello.txt",
        workspace,
        log_paths=AgentLogPaths(
            messages_path=str(tmp_path / "messages.jsonl"),
            events_path=str(tmp_path / "events.jsonl"),
        ),
    )

    assert result.final_text == "Done."
    assert result.input_tokens == 16
    assert result.output_tokens == 10
    assert workspace.files["/workspace/hello.txt"] == b"Hello!\n"

    events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert any(e["type"] == "tool_started" for e in events)
    assert any(e["type"] == "tool_completed" for e in events)


async def test_simple_agent_only_registers_requested_tools(tmp_path: Path):
    api_client = FakeApiClient([
        _FakeResponse(message=ConversationMessage(role="assistant", content=[TextBlock(text="ok")]))
    ])
    agent = OpenHarnessSimpleAgent(
        OpenHarnessSimpleAgentConfig(model="claude-test", tool_names=("bash",), max_turns=2),
        api_client=api_client,
    )
    await agent.run("Run bash", FakeWorkspace())
    tool_names = [t["name"] for t in api_client.requests[0].tools]
    assert tool_names == ["bash"]
