"""Tests for the engine loop guard."""

from __future__ import annotations

from pathlib import Path

import pytest

from openharness.api.client import ApiMessageCompleteEvent
from openharness.api.usage import UsageSnapshot
from openharness.engine.conversation import Conversation
from openharness.engine.loop_guard import (
    LoopGuardConfig,
    LoopGuardState,
    _tool_call_key,
    inspect_turn,
)
from openharness.engine.messages import ConversationMessage, TextBlock, ToolUseBlock
from openharness.engine.query import QueryContext, TurnResult
from openharness.permissions import PermissionChecker
from openharness.config.settings import PermissionSettings
from openharness.tools.base import ToolRegistry


def _make_turn(
    *,
    text: str = "",
    tool_calls: tuple[ToolUseBlock, ...] = (),
    is_final: bool = False,
) -> TurnResult:
    message = ConversationMessage(
        role="assistant",
        content=[TextBlock(text=text), *tool_calls],
    )
    return TurnResult(
        message=message,
        text=text,
        tool_calls=tool_calls,
        tool_results=(),
        usage=UsageSnapshot(),
        is_final=is_final,
    )


def _bash_call(command: str) -> ToolUseBlock:
    return ToolUseBlock(name="bash", input={"command": command})


def test_first_empty_turn_triggers_nudge_by_default():
    state = LoopGuardState()
    nudge = inspect_turn(state, _make_turn())
    assert nudge is not None
    assert nudge.role == "user"
    assert "no content" in nudge.content[0].text.lower()
    assert state.empty_turn_streak == 0
    assert state.recoveries_used == 1


def test_empty_turns_tolerated_when_configured():
    state = LoopGuardState(config=LoopGuardConfig(max_empty_turns=1))
    assert inspect_turn(state, _make_turn()) is None
    assert state.empty_turn_streak == 1
    nudge = inspect_turn(state, _make_turn())
    assert nudge is not None
    assert state.recoveries_used == 1


def test_empty_streak_resets_on_text_turn():
    state = LoopGuardState(config=LoopGuardConfig(max_empty_turns=1))
    inspect_turn(state, _make_turn())
    inspect_turn(state, _make_turn(text="progress"))
    assert state.empty_turn_streak == 0
    assert inspect_turn(state, _make_turn()) is None
    assert state.empty_turn_streak == 1


def test_identical_tool_call_triggers_nudge_after_threshold():
    state = LoopGuardState(config=LoopGuardConfig(max_identical_tool_calls=3))
    call = _bash_call("ls /nope")
    inspect_turn(state, _make_turn(tool_calls=(call,)))
    inspect_turn(state, _make_turn(tool_calls=(call,)))
    nudge = inspect_turn(state, _make_turn(tool_calls=(call,)))
    assert nudge is not None
    assert "bash" in nudge.content[0].text
    assert "3 times" in nudge.content[0].text
    assert state.recoveries_used == 1


def test_tool_call_streak_resets_on_different_args():
    state = LoopGuardState(config=LoopGuardConfig(max_identical_tool_calls=3))
    inspect_turn(state, _make_turn(tool_calls=(_bash_call("ls /a"),)))
    inspect_turn(state, _make_turn(tool_calls=(_bash_call("ls /a"),)))
    # Different args -> streak resets
    inspect_turn(state, _make_turn(tool_calls=(_bash_call("ls /b"),)))
    assert state.same_call_streak == 1
    # 3rd identical of the NEW call also doesn't trigger until threshold
    inspect_turn(state, _make_turn(tool_calls=(_bash_call("ls /b"),)))
    nudge = inspect_turn(state, _make_turn(tool_calls=(_bash_call("ls /b"),)))
    assert nudge is not None
    assert state.recoveries_used == 1


def test_parallel_tool_calls_do_not_count_as_repeat():
    state = LoopGuardState(config=LoopGuardConfig(max_identical_tool_calls=3))
    # Two-tool-call turn should reset last_call_key (can't identify a single repeating key)
    multi = (_bash_call("ls /a"), _bash_call("ls /b"))
    inspect_turn(state, _make_turn(tool_calls=multi))
    inspect_turn(state, _make_turn(tool_calls=multi))
    inspect_turn(state, _make_turn(tool_calls=multi))
    assert state.recoveries_used == 0


def test_recovery_budget_exhaustion():
    state = LoopGuardState(config=LoopGuardConfig(max_recoveries=2))
    for _ in range(5):
        inspect_turn(state, _make_turn())
    assert state.recoveries_used >= 2
    assert state.exhausted is True
    # After exhaustion no more nudges
    assert inspect_turn(state, _make_turn()) is None


def test_disabled_guard_never_triggers():
    state = LoopGuardState(config=LoopGuardConfig(enabled=False))
    for _ in range(10):
        assert inspect_turn(state, _make_turn()) is None
    assert state.recoveries_used == 0


def test_tool_call_key_is_stable_under_arg_reordering():
    a = ToolUseBlock(name="bash", input={"command": "echo hi", "cwd": "/"})
    b = ToolUseBlock(name="bash", input={"cwd": "/", "command": "echo hi"})
    assert _tool_call_key(a) == _tool_call_key(b)


def test_tool_call_key_differentiates_arg_values():
    a = ToolUseBlock(name="bash", input={"command": "echo 1"})
    b = ToolUseBlock(name="bash", input={"command": "echo 2"})
    assert _tool_call_key(a) != _tool_call_key(b)


class _ScriptedApiClient:
    """Streaming client that returns a scripted sequence of assistant messages."""

    def __init__(self, messages: list[ConversationMessage]) -> None:
        self._messages = list(messages)

    async def stream_message(self, request):
        del request
        if not self._messages:
            raise RuntimeError("no more scripted responses")
        msg = self._messages.pop(0)
        yield ApiMessageCompleteEvent(
            message=msg,
            usage=UsageSnapshot(input_tokens=1, output_tokens=1),
            stop_reason=None,
        )


def _make_context(client) -> QueryContext:
    return QueryContext(
        api_client=client,
        tool_registry=ToolRegistry(),
        permission_checker=PermissionChecker(PermissionSettings()),
        cwd=Path("/tmp"),
        model="fake",
        system_prompt="",
        max_tokens=1024,
        max_turns=20,
    )


@pytest.mark.asyncio
async def test_conversation_loop_guard_recovers_from_empty_turns():
    scripted = [
        ConversationMessage(role="assistant", content=[]),
        ConversationMessage(role="assistant", content=[]),
        ConversationMessage(role="assistant", content=[TextBlock(text="recovered")]),
    ]
    client = _ScriptedApiClient(scripted)
    ctx = _make_context(client)
    messages = [ConversationMessage.from_user_text("go")]
    conv = Conversation(ctx, messages)

    guard = LoopGuardState()
    result = await conv.run_to_completion(loop_guard=guard)

    assert result == "recovered"
    # Two empty turns before recovery -> two nudges injected.
    assert guard.recoveries_used == 2
    user_roles = [m.role for m in conv.messages if m.role == "user"]
    # Original user message + 2 injected nudges.
    assert len(user_roles) >= 3


@pytest.mark.asyncio
async def test_conversation_without_loop_guard_terminates_on_empty_turn():
    scripted = [ConversationMessage(role="assistant", content=[])]
    client = _ScriptedApiClient(scripted)
    ctx = _make_context(client)
    messages = [ConversationMessage.from_user_text("go")]
    conv = Conversation(ctx, messages)
    text = await conv.run_to_completion()
    assert text == ""
    assert conv.is_complete is True


@pytest.mark.asyncio
async def test_conversation_loop_guard_gives_up_after_budget():
    # Provide 10 empty turns; with max_recoveries=2 the guard should
    # eventually exhaust and the conversation should end.
    scripted = [ConversationMessage(role="assistant", content=[]) for _ in range(10)]
    client = _ScriptedApiClient(scripted)
    ctx = _make_context(client)
    messages = [ConversationMessage.from_user_text("go")]
    conv = Conversation(ctx, messages)

    guard = LoopGuardState(config=LoopGuardConfig(max_recoveries=2))
    text = await conv.run_to_completion(loop_guard=guard)
    assert text == ""
    assert guard.exhausted is True
    assert guard.recoveries_used == 3  # 2 allowed + 1 that triggered exhaustion
