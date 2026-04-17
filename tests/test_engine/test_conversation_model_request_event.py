"""Tests for the ``ModelRequest`` audit event emitted before each turn.

The event captures the full request payload that crosses to the LLM
provider for one turn (system prompt, tool surface, request params),
complementing the response-side ``AssistantTurnComplete`` event so that
runs are inspectable / replayable from ``events.jsonl`` alone.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openharness.api.client import ApiMessageCompleteEvent
from openharness.api.usage import UsageSnapshot
from openharness.config.settings import PermissionSettings
from openharness.engine.conversation import Conversation
from openharness.engine.messages import ConversationMessage, TextBlock, ToolUseBlock
from openharness.engine.query import QueryContext
from openharness.engine.stream_events import (
    AssistantTurnComplete,
    ModelRequest,
    StreamEvent,
)
from openharness.permissions import PermissionChecker
from openharness.tools.base import ToolRegistry


class _OneShotClient:
    async def stream_message(self, request):  # noqa: ANN001
        del request
        yield ApiMessageCompleteEvent(
            message=ConversationMessage(
                role="assistant",
                content=[TextBlock(text="ok")],
            ),
            usage=UsageSnapshot(input_tokens=1, output_tokens=1),
            stop_reason=None,
        )


class _TwoTurnClient:
    """First turn returns a tool call, second turn returns a final text."""

    def __init__(self) -> None:
        self.calls = 0

    async def stream_message(self, request):  # noqa: ANN001
        del request
        self.calls += 1
        if self.calls == 1:
            yield ApiMessageCompleteEvent(
                message=ConversationMessage(
                    role="assistant",
                    content=[
                        TextBlock(text="thinking"),
                        ToolUseBlock(name="bash", input={"command": "echo hi"}),
                    ],
                ),
                usage=UsageSnapshot(input_tokens=1, output_tokens=1),
                stop_reason=None,
            )
        else:
            yield ApiMessageCompleteEvent(
                message=ConversationMessage(
                    role="assistant",
                    content=[TextBlock(text="done")],
                ),
                usage=UsageSnapshot(input_tokens=2, output_tokens=2),
                stop_reason=None,
            )


def _make_context(client, *, system_prompt: str = "you are a tester") -> QueryContext:
    return QueryContext(
        api_client=client,
        tool_registry=ToolRegistry(),
        permission_checker=PermissionChecker(PermissionSettings()),
        cwd=Path("/tmp"),
        model="fake-model",
        system_prompt=system_prompt,
        max_tokens=1024,
        max_turns=4,
    )


@pytest.mark.asyncio
async def test_model_request_event_emitted_before_turn() -> None:
    """A ``ModelRequest`` is logged before each LLM call."""
    events: list[StreamEvent] = []
    ctx = _make_context(_OneShotClient(), system_prompt="hello system")
    conv = Conversation(
        ctx,
        [ConversationMessage.from_user_text("go")],
        agent_name="planner",
        _log_event=events.append,
    )

    await conv.run_to_completion()

    request_events = [e for e in events if isinstance(e, ModelRequest)]
    assert len(request_events) == 1
    req = request_events[0]
    assert req.model == "fake-model"
    assert req.system_prompt == "hello system"
    assert req.tools == ()  # empty registry
    assert req.max_tokens == 1024
    assert req.max_turns == 4
    assert req.turn_index == 1
    assert req.message_count == 1
    assert req.agent == "planner"

    # The request event must precede the response event for the same turn.
    types = [type(e) for e in events]
    assert types.index(ModelRequest) < types.index(AssistantTurnComplete)


@pytest.mark.asyncio
async def test_model_request_event_per_turn() -> None:
    """One ``ModelRequest`` is emitted per LLM call (so two for a two-turn loop)."""
    events: list[StreamEvent] = []
    ctx = _make_context(_TwoTurnClient())
    conv = Conversation(
        ctx,
        [ConversationMessage.from_user_text("go")],
        agent_name="executor",
        _log_event=events.append,
    )

    await conv.run_to_completion()

    request_events = [e for e in events if isinstance(e, ModelRequest)]
    assert len(request_events) == 2
    assert [e.turn_index for e in request_events] == [1, 2]
    # Second turn's request was made after the assistant's tool-call turn
    # and the synthetic tool-result user message were appended.
    assert request_events[1].message_count > request_events[0].message_count


@pytest.mark.asyncio
async def test_model_request_event_serializes_to_jsonl(tmp_path: Path) -> None:
    """The event is serialized to ``events.jsonl`` with the expected shape."""
    import json

    from openharness.runtime.session import _serialize_event

    event = ModelRequest(
        model="fake-model",
        system_prompt="sys",
        tools=("bash", "read_file"),
        max_tokens=2048,
        max_turns=20,
        turn_index=3,
        message_count=7,
        agent="planner",
    )
    payload = _serialize_event(event)
    # Round-trips cleanly through JSON.
    assert json.loads(json.dumps(payload)) == {
        "type": "model_request",
        "model": "fake-model",
        "system_prompt": "sys",
        "tools": ["bash", "read_file"],
        "max_tokens": 2048,
        "max_turns": 20,
        "turn_index": 3,
        "message_count": 7,
        "agent": "planner",
    }
