"""Tests for the max_turns budget enforcement in `Conversation`."""

from __future__ import annotations

from pathlib import Path

import pytest

from openharness.api.client import ApiMessageCompleteEvent
from openharness.api.usage import UsageSnapshot
from openharness.config.settings import PermissionSettings
from openharness.engine.conversation import Conversation
from openharness.engine.messages import ConversationMessage, TextBlock, ToolUseBlock
from openharness.engine.query import QueryContext
from openharness.permissions import PermissionChecker
from openharness.tools.base import ToolRegistry


class _NeverEndingClient:
    """A scripted client that always returns a tool-call turn so the
    conversation never finishes on its own."""

    def __init__(self) -> None:
        self.calls = 0

    async def stream_message(self, request):  # noqa: ANN001
        del request
        self.calls += 1
        msg = ConversationMessage(
            role="assistant",
            content=[
                TextBlock(text=f"thinking turn {self.calls}"),
                ToolUseBlock(name="bash", input={"command": f"echo {self.calls}"}),
            ],
        )
        yield ApiMessageCompleteEvent(
            message=msg,
            usage=UsageSnapshot(input_tokens=1, output_tokens=1),
            stop_reason=None,
        )


def _make_context(client, *, max_turns: int) -> QueryContext:
    return QueryContext(
        api_client=client,
        tool_registry=ToolRegistry(),
        permission_checker=PermissionChecker(PermissionSettings()),
        cwd=Path("/tmp"),
        model="fake",
        system_prompt="",
        max_tokens=1024,
        max_turns=max_turns,
    )


@pytest.mark.asyncio
async def test_run_to_completion_stops_at_max_turns() -> None:
    """Conversation must stop after `max_turns` even if the model keeps
    requesting tools — the previous behaviour was an unbounded loop only
    capped by the outer harbor wall-clock timeout."""
    client = _NeverEndingClient()
    ctx = _make_context(client, max_turns=3)
    conv = Conversation(ctx, [ConversationMessage.from_user_text("go")])

    final_text = await conv.run_to_completion()

    assert client.calls == 3
    # Engine surfaces best-effort final_text from the last completed turn
    # rather than raising, so callers (planner, executor, default) keep
    # working on partial output.
    assert "thinking turn 3" in final_text
    # And the tool's stub output (no real registry → empty content) is
    # used as the final fallback when the assistant produced no plain
    # text (here it did, so final_text is the assistant text).


@pytest.mark.asyncio
async def test_run_to_completion_no_max_turns_remains_unbounded() -> None:
    """With max_turns=None the loop must still terminate normally when
    the model returns a final (no-tool-calls) message."""
    final_msg = ConversationMessage(
        role="assistant",
        content=[TextBlock(text="done")],
    )

    class _OneShotClient:
        async def stream_message(self, request):  # noqa: ANN001
            del request
            yield ApiMessageCompleteEvent(
                message=final_msg,
                usage=UsageSnapshot(input_tokens=1, output_tokens=1),
                stop_reason=None,
            )

    ctx = QueryContext(
        api_client=_OneShotClient(),
        tool_registry=ToolRegistry(),
        permission_checker=PermissionChecker(PermissionSettings()),
        cwd=Path("/tmp"),
        model="fake",
        system_prompt="",
        max_tokens=1024,
        max_turns=None,
    )
    conv = Conversation(ctx, [ConversationMessage.from_user_text("go")])

    final_text = await conv.run_to_completion()
    assert final_text == "done"
