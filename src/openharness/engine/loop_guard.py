"""LoopGuard — detect and recover from common agent failure modes.

Two failure modes this module addresses:

1. **Empty assistant turns**: the model returns neither text nor tool calls.
   Without intervention the conversation silently ends with zero progress
   (observed with some Gemini variants when the context confuses the model).

2. **Identical tool-call loops**: the model repeatedly issues the same tool
   call with the same arguments, which cannot produce a new observation.

When either pattern is detected the guard emits a short steering user
message (a "nudge") that the caller injects into the conversation. The
guard gives up after a small budget of recovery attempts so that a truly
stuck conversation still terminates.

Pure module: no I/O, no Conversation dependency — callers pass a
``LoopGuardState`` and per-turn inspect it with :func:`inspect_turn`.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field

from openharness.engine.messages import ConversationMessage, TextBlock, ToolUseBlock
from openharness.engine.query import TurnResult

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class LoopGuardConfig:
    """Tunables for the loop guard.

    The empty-turn threshold defaults to 0 (nudge on the first empty turn)
    because an empty turn causes ``run_single_turn`` to return
    ``is_final=True`` — so if we don't intervene immediately the
    conversation ends silently. Two identical tool calls in a row are
    allowed (retrying a flaky network call is sometimes sensible).
    """

    enabled: bool = True
    max_empty_turns: int = 0
    """Allow N consecutive empty turns before nudging (N=0 means the first empty turn triggers)."""
    max_identical_tool_calls: int = 3
    """Trigger a nudge when the same tool call (name + args) is issued this many times in a row."""
    max_recoveries: int = 3
    """Cap on the total number of nudges injected; after this, the guard goes silent."""


@dataclass
class LoopGuardState:
    """Mutable state for a single conversation."""

    config: LoopGuardConfig = field(default_factory=LoopGuardConfig)
    empty_turn_streak: int = 0
    last_call_key: str = ""
    same_call_streak: int = 0
    recoveries_used: int = 0
    exhausted: bool = False


def _tool_call_key(block: ToolUseBlock) -> str:
    """Return a stable string identifying a tool call (name + canonical args)."""
    try:
        args_blob = json.dumps(block.input, sort_keys=True, default=str)
    except (TypeError, ValueError):
        args_blob = repr(block.input)
    return f"{block.name}:{hashlib.sha1(args_blob.encode('utf-8')).hexdigest()[:12]}"


def _nudge_empty_turn() -> ConversationMessage:
    return ConversationMessage(
        role="user",
        content=[
            TextBlock(
                text=(
                    "You returned no content and no tool call. "
                    "If the task is complete, say so in one short message and stop. "
                    "If not, state your next concrete step and either call a tool "
                    "or clearly explain why you cannot proceed."
                )
            )
        ],
    )


def _nudge_identical_tool_call(name: str, count: int) -> ConversationMessage:
    return ConversationMessage(
        role="user",
        content=[
            TextBlock(
                text=(
                    f"You have just called `{name}` with identical arguments "
                    f"{count} times in a row. That will not produce a new result. "
                    "Form a new hypothesis, try a different approach (different "
                    "command, arguments, or tool), or stop and describe the blocker."
                )
            )
        ],
    )


def inspect_turn(state: LoopGuardState, result: TurnResult) -> ConversationMessage | None:
    """Inspect a completed turn; mutate state; return a nudge message or ``None``.

    The caller is responsible for injecting the returned message into the
    conversation (typically via ``Conversation.inject``).
    """
    cfg = state.config
    if not cfg.enabled or state.exhausted:
        return None

    has_text = bool(result.text)
    has_tools = bool(result.tool_calls)

    if not has_text and not has_tools:
        state.empty_turn_streak += 1
        if state.empty_turn_streak > cfg.max_empty_turns:
            state.empty_turn_streak = 0
            return _record_recovery(state, _nudge_empty_turn(), reason="empty_turn")
        return None

    state.empty_turn_streak = 0

    if has_tools:
        keys = [_tool_call_key(tc) for tc in result.tool_calls]
        if len(keys) == 1 and keys[0] == state.last_call_key:
            state.same_call_streak += 1
        else:
            state.same_call_streak = 1
            state.last_call_key = keys[0] if len(keys) == 1 else ""
        if state.same_call_streak >= cfg.max_identical_tool_calls:
            name = result.tool_calls[0].name
            count = state.same_call_streak
            state.same_call_streak = 0
            state.last_call_key = ""
            return _record_recovery(
                state,
                _nudge_identical_tool_call(name, count),
                reason=f"identical_tool_call:{name}",
            )
    else:
        state.same_call_streak = 0
        state.last_call_key = ""

    return None


def _record_recovery(
    state: LoopGuardState,
    message: ConversationMessage,
    *,
    reason: str,
) -> ConversationMessage | None:
    state.recoveries_used += 1
    if state.recoveries_used > state.config.max_recoveries:
        state.exhausted = True
        log.warning(
            "Loop guard exhausted (used=%d, max=%d); not injecting further nudges",
            state.recoveries_used,
            state.config.max_recoveries,
        )
        return None
    log.info(
        "Loop guard triggered (%s), injecting nudge (%d/%d)",
        reason,
        state.recoveries_used,
        state.config.max_recoveries,
    )
    return message
