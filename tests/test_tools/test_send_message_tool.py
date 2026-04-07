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
    marks: list[str] = []
    monkeypatch.setattr(
        "openharness.tools.send_message_tool.get_backend_registry",
        lambda: _FakeRegistry(executor),
    )
    monkeypatch.setattr(
        "openharness.tools.send_message_tool._resolve_sender_agent_id",
        lambda: "implementer@team-demo",
    )
    monkeypatch.setattr(
        "openharness.tools.send_message_tool._resolve_active_thread",
        lambda: ("msg-parent", "corr-parent"),
    )
    monkeypatch.setattr(
        "openharness.tools.send_message_tool._mark_explicit_reply_sent",
        lambda: marks.append("marked"),
    )

    tool = SendMessageTool()
    result = await tool.execute(
        tool.input_model(task_id="leader@team-demo", message="done"),
        ToolExecutionContext(cwd=tmp_path),
    )

    assert result.is_error is False
    assert executor.calls[0][0] == "leader@team-demo"
    assert executor.calls[0][1].from_agent == "implementer@team-demo"
    assert executor.calls[0][1].reply_to == "msg-parent"
    assert executor.calls[0][1].correlation_id == "corr-parent"
    assert result.metadata["delivery_channel"] == "swarm"
    assert result.metadata["reply_to"] == "msg-parent"
    assert result.metadata["correlation_id"] == "corr-parent"
    assert marks == ["marked"]


def test_resolve_sender_defaults_to_none() -> None:
    assert _resolve_sender_agent_id() is None
