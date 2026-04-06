"""Tests for the send_message tool."""

from __future__ import annotations

from openharness.tools.base import ToolExecutionContext
from openharness.tools.send_message_tool import (
    SendMessageTool,
    _resolve_sender_agent_id,
)


class _FakeExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    async def send_message(self, agent_id: str, message) -> None:
        self.calls.append((agent_id, message))


class _FakeRegistry:
    def __init__(self, executor: _FakeExecutor) -> None:
        self._executor = executor

    def get_executor(self, _backend_type: str | None = None) -> _FakeExecutor:
        return self._executor


async def test_send_message_uses_current_worker_identity_from_context(monkeypatch, tmp_path) -> None:
    executor = _FakeExecutor()
    monkeypatch.setattr(
        "openharness.tools.send_message_tool.get_backend_registry",
        lambda: _FakeRegistry(executor),
    )
    monkeypatch.setattr(
        "openharness.tools.send_message_tool._resolve_sender_agent_id",
        lambda: "implementer@team-demo",
    )

    tool = SendMessageTool()
    result = await tool.execute(
        tool.input_model(task_id="leader@team-demo", message="done"),
        ToolExecutionContext(cwd=tmp_path),
    )

    assert result.is_error is False
    assert executor.calls[0][0] == "leader@team-demo"
    assert executor.calls[0][1].from_agent == "implementer@team-demo"


def test_resolve_sender_defaults_to_coordinator(monkeypatch) -> None:
    monkeypatch.delenv("CLAUDE_CODE_AGENT_ID", raising=False)
    assert _resolve_sender_agent_id() == "coordinator"
