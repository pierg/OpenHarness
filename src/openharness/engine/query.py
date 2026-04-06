"""Core tool-aware query loop.

Two entry points:

- ``run_single_turn`` — execute one LLM call + tool execution, return a
  ``TurnResult``.  Used by ``Conversation.step()``.
- ``run_query`` — streaming generator that runs the full multi-turn loop.
  Kept for backward compatibility.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable

from openharness.api.client import (
    ApiMessageCompleteEvent,
    ApiMessageRequest,
    ApiRetryEvent,
    ApiTextDeltaEvent,
    SupportsStreamingMessages,
)
from openharness.api.usage import UsageSnapshot
from openharness.engine.messages import ConversationMessage, ToolResultBlock, ToolUseBlock
from openharness.engine.stream_events import (
    AssistantTextDelta,
    AssistantTurnComplete,
    ErrorEvent,
    StatusEvent,
    StreamEvent,
    ToolExecutionCompleted,
    ToolExecutionStarted,
)
from openharness.hooks import HookEvent, HookExecutor
from openharness.observability import NullTraceObserver, TraceObserver
from openharness.permissions.checker import PermissionChecker
from openharness.tools.base import ToolExecutionContext
from openharness.tools.base import ToolRegistry


PermissionPrompt = Callable[[str, str], Awaitable[bool]]
AskUserPrompt = Callable[[str], Awaitable[str]]

_TRACE_TEXT_LIMIT = 4000


@dataclass(frozen=True)
class TurnResult:
    """What happened in a single LLM turn (one API call + tool execution)."""

    message: ConversationMessage
    text: str
    tool_calls: tuple[ToolUseBlock, ...]
    tool_results: tuple[ToolResultBlock, ...]
    usage: UsageSnapshot
    is_final: bool


class MaxTurnsExceeded(RuntimeError):
    """Raised when the agent exceeds the configured max_turns for one user prompt."""

    def __init__(self, max_turns: int) -> None:
        super().__init__(f"Exceeded maximum turn limit ({max_turns})")
        self.max_turns = max_turns


@dataclass
class QueryContext:
    """Context shared across a query run."""

    api_client: SupportsStreamingMessages
    tool_registry: ToolRegistry
    permission_checker: PermissionChecker
    cwd: Path
    model: str
    system_prompt: str
    max_tokens: int
    permission_prompt: PermissionPrompt | None = None
    ask_user_prompt: AskUserPrompt | None = None
    max_turns: int | None = 200
    hook_executor: HookExecutor | None = None
    tool_metadata: dict[str, object] | None = None
    trace_observer: TraceObserver | None = None


# ---------------------------------------------------------------------------
# Step-based entry point (used by Conversation.step)
# ---------------------------------------------------------------------------


async def run_single_turn(
    context: QueryContext,
    messages: list[ConversationMessage],
) -> TurnResult:
    """Execute one LLM call + tool execution.  Mutates *messages* in place."""
    final_message: ConversationMessage | None = None
    usage = UsageSnapshot()
    observer = context.trace_observer or NullTraceObserver()

    with observer.model_call(
        model=context.model,
        input=_trace_model_input(context.system_prompt, messages),
        metadata=_trace_model_metadata(messages),
        model_parameters={
            "max_tokens": context.max_tokens,
            "max_turns": context.max_turns,
        },
    ) as model_handle:
        async for event in context.api_client.stream_message(
            ApiMessageRequest(
                model=context.model,
                messages=messages,
                system_prompt=context.system_prompt,
                max_tokens=context.max_tokens,
                tools=context.tool_registry.to_api_schema(),
            )
        ):
            if isinstance(event, ApiMessageCompleteEvent):
                final_message = event.message
                usage = event.usage

        if final_message is None:
            raise RuntimeError("Model stream finished without a final message")

        model_handle.update(
            output=_trace_model_output(final_message),
            metadata={
                "usage": usage.model_dump(mode="json"),
                "tool_calls": _trace_tool_calls(final_message.tool_uses) or None,
            },
        )

    messages.append(final_message)
    tool_calls = final_message.tool_uses

    if not tool_calls:
        return TurnResult(
            message=final_message,
            text=final_message.text.strip(),
            tool_calls=(),
            tool_results=(),
            usage=usage,
            is_final=True,
        )

    tool_results = await _execute_tools(context, tool_calls)
    messages.append(ConversationMessage(role="user", content=list(tool_results)))

    return TurnResult(
        message=final_message,
        text=final_message.text.strip(),
        tool_calls=tuple(tool_calls),
        tool_results=tuple(tool_results),
        usage=usage,
        is_final=False,
    )


# ---------------------------------------------------------------------------
# Streaming entry point (backward compat)
# ---------------------------------------------------------------------------


async def run_query(
    context: QueryContext,
    messages: list[ConversationMessage],
) -> AsyncIterator[tuple[StreamEvent, UsageSnapshot | None]]:
    """Run the conversation loop until the model stops requesting tools.

    Auto-compaction is checked at the start of each turn.  When the
    estimated token count exceeds the model's auto-compact threshold,
    the engine first tries a cheap microcompact (clearing old tool result
    content) and, if that is not enough, performs a full LLM-based
    summarization of older messages.
    """
    from openharness.services.compact import (
        AutoCompactState,
        auto_compact_if_needed,
    )

    compact_state = AutoCompactState()

    turn_count = 0
    while context.max_turns is None or turn_count < context.max_turns:
        turn_count += 1
        # --- auto-compact check before calling the model ---------------
        messages, was_compacted = await auto_compact_if_needed(
            messages,
            api_client=context.api_client,
            model=context.model,
            system_prompt=context.system_prompt,
            state=compact_state,
        )
        # ---------------------------------------------------------------

        final_message: ConversationMessage | None = None
        usage = UsageSnapshot()
        observer = context.trace_observer or NullTraceObserver()

        try:
            with observer.model_call(
                model=context.model,
                input=_trace_model_input(context.system_prompt, messages),
                metadata={
                    **_trace_model_metadata(messages),
                    "turn_index": turn_count,
                    "was_compacted": was_compacted,
                },
                model_parameters={
                    "max_tokens": context.max_tokens,
                    "max_turns": context.max_turns,
                },
            ) as model_handle:
                async for event in context.api_client.stream_message(
                    ApiMessageRequest(
                        model=context.model,
                        messages=messages,
                        system_prompt=context.system_prompt,
                        max_tokens=context.max_tokens,
                        tools=context.tool_registry.to_api_schema(),
                    )
                ):
                    if isinstance(event, ApiTextDeltaEvent):
                        yield AssistantTextDelta(text=event.text), None
                        continue
                    if isinstance(event, ApiRetryEvent):
                        yield StatusEvent(
                            message=(
                                f"Request failed; retrying in {event.delay_seconds:.1f}s "
                                f"(attempt {event.attempt + 1} of {event.max_attempts}): {event.message}"
                            )
                        ), None
                        continue

                    if isinstance(event, ApiMessageCompleteEvent):
                        final_message = event.message
                        usage = event.usage

                if final_message is None:
                    raise RuntimeError("Model stream finished without a final message")

                model_handle.update(
                    output=_trace_model_output(final_message),
                    metadata={
                        "usage": usage.model_dump(mode="json"),
                        "tool_calls": _trace_tool_calls(final_message.tool_uses) or None,
                    },
                )

            messages.append(final_message)
            yield AssistantTurnComplete(message=final_message, usage=usage), usage

            if not final_message.tool_uses:
                return

            tool_calls = final_message.tool_uses

            for tc in tool_calls:
                yield ToolExecutionStarted(tool_name=tc.name, tool_input=tc.input), None

            tool_results = await _execute_tools(context, tool_calls)

            for tc, result in zip(tool_calls, tool_results):
                yield ToolExecutionCompleted(
                    tool_name=tc.name,
                    output=result.content,
                    is_error=result.is_error,
                ), None

            messages.append(ConversationMessage(role="user", content=list(tool_results)))
        except Exception as exc:
            error_msg = str(exc)
            if "connect" in error_msg.lower() or "timeout" in error_msg.lower() or "network" in error_msg.lower():
                yield ErrorEvent(message=f"Network error: {error_msg}. Check your internet connection and try again."), None
            else:
                yield ErrorEvent(message=f"API error: {error_msg}"), None
            return

    if context.max_turns is not None:
        raise MaxTurnsExceeded(context.max_turns)
    raise RuntimeError("Query loop exited without a max_turns limit or final response")


async def _execute_tools(
    context: QueryContext,
    tool_calls: list[ToolUseBlock],
) -> tuple[ToolResultBlock, ...]:
    """Execute tool calls (concurrently if multiple). Shared by both entry points."""
    if len(tool_calls) == 1:
        tc = tool_calls[0]
        result = await _execute_tool_call(context, tc.name, tc.id, tc.input)
        return (result,)

    results = await asyncio.gather(*[
        _execute_tool_call(context, tc.name, tc.id, tc.input)
        for tc in tool_calls
    ])
    return tuple(results)


async def _execute_tool_call(
    context: QueryContext,
    tool_name: str,
    tool_use_id: str,
    tool_input: dict[str, object],
) -> ToolResultBlock:
    observer = context.trace_observer or NullTraceObserver()
    with observer.tool_call(
        tool_name=tool_name,
        tool_input=tool_input,
        metadata={
            "cwd": str(context.cwd),
            "tool_use_id": tool_use_id,
        },
    ) as tool_handle:
        if context.hook_executor is not None:
            pre_hooks = await context.hook_executor.execute(
                HookEvent.PRE_TOOL_USE,
                {"tool_name": tool_name, "tool_input": tool_input, "event": HookEvent.PRE_TOOL_USE.value},
            )
            if pre_hooks.blocked:
                tool_handle.update(
                    output=pre_hooks.reason,
                    metadata={"blocked_by_hook": True},
                )
                return ToolResultBlock(
                    tool_use_id=tool_use_id,
                    content=pre_hooks.reason or f"pre_tool_use hook blocked {tool_name}",
                    is_error=True,
                )

        tool = context.tool_registry.get(tool_name)
        if tool is None:
            tool_handle.update(
                output=f"Unknown tool: {tool_name}",
                metadata={"is_error": True},
            )
            return ToolResultBlock(
                tool_use_id=tool_use_id,
                content=f"Unknown tool: {tool_name}",
                is_error=True,
            )

        try:
            parsed_input = tool.input_model.model_validate(tool_input)
        except Exception as exc:
            tool_handle.update(
                output=f"Invalid input for {tool_name}: {exc}",
                metadata={"is_error": True},
            )
            return ToolResultBlock(
                tool_use_id=tool_use_id,
                content=f"Invalid input for {tool_name}: {exc}",
                is_error=True,
            )

        _file_path = str(tool_input.get("path") or tool_input.get("file_path") or "") or None
        _command = str(tool_input.get("command", "")) or None
        # Normalize common tool inputs before permission checks so path rules apply
        # consistently across built-in tools that use either `file_path` or `path`.
        _file_path = _resolve_permission_file_path(context.cwd, tool_input, parsed_input) or _file_path
        _command = _extract_permission_command(tool_input, parsed_input) or _command
        decision = context.permission_checker.evaluate(
            tool_name,
            is_read_only=tool.is_read_only(parsed_input),
            file_path=_file_path,
            command=_command,
        )
        if not decision.allowed:
            if decision.requires_confirmation and context.permission_prompt is not None:
                confirmed = await context.permission_prompt(tool_name, decision.reason)
                if not confirmed:
                    tool_handle.update(
                        output=f"Permission denied for {tool_name}",
                        metadata={"is_error": True, "permission_reason": decision.reason},
                    )
                    return ToolResultBlock(
                        tool_use_id=tool_use_id,
                        content=f"Permission denied for {tool_name}",
                        is_error=True,
                    )
            else:
                tool_handle.update(
                    output=decision.reason or f"Permission denied for {tool_name}",
                    metadata={"is_error": True, "permission_reason": decision.reason},
                )
                return ToolResultBlock(
                    tool_use_id=tool_use_id,
                    content=decision.reason or f"Permission denied for {tool_name}",
                    is_error=True,
                )

        result = await tool.execute(
            parsed_input,
            ToolExecutionContext(
                cwd=context.cwd,
                metadata={
                    "tool_registry": context.tool_registry,
                    "ask_user_prompt": context.ask_user_prompt,
                    "trace_observer": context.trace_observer,
                    **(context.tool_metadata or {}),
                },
            ),
        )
        tool_result = ToolResultBlock(
            tool_use_id=tool_use_id,
            content=result.output,
            is_error=result.is_error,
        )
        if context.hook_executor is not None:
            await context.hook_executor.execute(
                HookEvent.POST_TOOL_USE,
                {
                    "tool_name": tool_name,
                    "tool_input": tool_input,
                    "tool_output": tool_result.content,
                    "tool_is_error": tool_result.is_error,
                    "event": HookEvent.POST_TOOL_USE.value,
                },
            )
        tool_handle.update(
            output=_truncate_trace_text(result.output),
            metadata={
                "is_error": result.is_error,
                **(result.metadata or {}),
            },
        )
        return tool_result


def _latest_user_prompt(messages: list[ConversationMessage]) -> str:
    for message in reversed(messages):
        if message.role == "user" and message.text.strip():
            return message.text.strip()
    return ""


def _trace_model_input(system_prompt: str, messages: list[ConversationMessage]) -> list[dict[str, Any]]:
    history: list[dict[str, Any]] = []
    if system_prompt:
        history.append({"role": "system", "content": _truncate_trace_text(system_prompt)})
    history.extend(_trace_history_entries(messages))
    return history


def _trace_model_metadata(messages: list[ConversationMessage]) -> dict[str, Any]:
    history = _trace_history_entries(messages)
    latest = history[-1] if history else None
    metadata: dict[str, Any] = {
        "message_count": len(messages),
        "history_entry_count": len(history),
    }
    if latest is not None:
        metadata["latest_role"] = latest.get("role")
        if latest.get("role") == "tool":
            metadata["latest_input_kind"] = "tool_result"
            metadata["latest_tool_name"] = latest.get("name")
        else:
            metadata["latest_input_kind"] = "message"
    return metadata


def _trace_history_entries(messages: list[ConversationMessage]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for message in messages:
        text = _truncate_trace_text(message.text)
        tool_results = [block for block in message.content if isinstance(block, ToolResultBlock)]
        tool_uses = [block for block in message.content if isinstance(block, ToolUseBlock)]

        if message.role == "user" and text:
            entries.append({"role": "user", "content": text})

        if message.role == "assistant":
            entry: dict[str, Any] = {"role": "assistant", "content": text}
            rendered_tool_calls = _trace_tool_calls(tool_uses)
            if rendered_tool_calls:
                entry["tool_calls"] = rendered_tool_calls
            if text or rendered_tool_calls:
                entries.append(entry)

        for result in tool_results:
            entries.append(
                {
                    "role": "tool",
                    "name": _tool_name_for_result(messages, result.tool_use_id) or result.tool_use_id,
                    "content": _truncate_trace_text(result.content),
                    "is_error": result.is_error,
                }
            )

        if not text and message.role == "user" and not tool_results:
            entries.append(
                {
                    "role": "user",
                    "content": f"[non-text blocks: {', '.join(block.type for block in message.content)}]",
                }
            )

    return entries


def _trace_model_output(message: ConversationMessage) -> dict[str, Any]:
    return {
        "content": _truncate_trace_text(message.text.strip()),
        "tool_calls": _trace_tool_calls(message.tool_uses) or None,
    }


def _trace_tool_calls(tool_uses: list[ToolUseBlock]) -> list[dict[str, Any]]:
    return [
        {
            "name": tool_use.name,
            "arguments": _coerce_trace_tool_arguments(tool_use.input),
        }
        for tool_use in tool_uses
    ]


def _coerce_trace_tool_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        str(key): _truncate_trace_text(json.dumps(value) if not isinstance(value, str) else value)
        for key, value in arguments.items()
    }


def _truncate_trace_text(text: str, *, limit: int = _TRACE_TEXT_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return f"{text[: limit - 16]}...[truncated]..."


def _tool_name_for_result(messages: list[ConversationMessage], tool_use_id: str) -> str | None:
    for message in reversed(messages):
        for block in message.content:
            if isinstance(block, ToolUseBlock) and block.id == tool_use_id:
                return block.name
    return None


def _resolve_permission_file_path(
    cwd: Path,
    raw_input: dict[str, object],
    parsed_input: object,
) -> str | None:
    for key in ("file_path", "path"):
        value = raw_input.get(key)
        if isinstance(value, str) and value.strip():
            path = Path(value).expanduser()
            if not path.is_absolute():
                path = cwd / path
            return str(path.resolve())

    for attr in ("file_path", "path"):
        value = getattr(parsed_input, attr, None)
        if isinstance(value, str) and value.strip():
            path = Path(value).expanduser()
            if not path.is_absolute():
                path = cwd / path
            return str(path.resolve())

    return None


def _extract_permission_command(
    raw_input: dict[str, object],
    parsed_input: object,
) -> str | None:
    value = raw_input.get("command")
    if isinstance(value, str) and value.strip():
        return value

    value = getattr(parsed_input, "command", None)
    if isinstance(value, str) and value.strip():
        return value

    return None
