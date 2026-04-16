"""Core tool-aware query loop.

Two entry points:

- ``run_single_turn`` executes one LLM call plus tool execution and returns a
  ``TurnResult``. Used by ``Conversation.step()``.
- ``run_query`` is the streaming multi-turn loop used by the interactive
  runtime and kept for compatibility.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
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
    CompactProgressEvent,
    ErrorEvent,
    StatusEvent,
    StreamEvent,
    ToolExecutionCompleted,
    ToolExecutionStarted,
)
from openharness.hooks import HookEvent, HookExecutor
from openharness.observability import NullTraceObserver, TraceObserver
from openharness.permissions.checker import PermissionChecker
from openharness.tools.base import ToolExecutionContext, ToolResult
from openharness.tools.base import ToolRegistry

AUTO_COMPACT_STATUS_MESSAGE = "Auto-compacting conversation memory to keep things fast and focused."
REACTIVE_COMPACT_STATUS_MESSAGE = "Prompt too long; compacting conversation memory and retrying."

log = logging.getLogger(__name__)


PermissionPrompt = Callable[[str, str], Awaitable[bool]]
AskUserPrompt = Callable[[str], Awaitable[str]]

MAX_TRACKED_READ_FILES = 6
MAX_TRACKED_SKILLS = 8
MAX_TRACKED_ASYNC_AGENT_EVENTS = 8
MAX_TRACKED_WORK_LOG = 10
MAX_TRACKED_USER_GOALS = 5
MAX_TRACKED_ACTIVE_ARTIFACTS = 8
MAX_TRACKED_VERIFIED_WORK = 10
_TRACE_TEXT_LIMIT = 4000


def _is_prompt_too_long_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(
        needle in text
        for needle in (
            "prompt too long",
            "context length",
            "maximum context",
            "context window",
            "too many tokens",
            "too large for the model",
            "maximum context length",
        )
    )


class MaxTurnsExceeded(RuntimeError):
    """Raised when the agent exceeds the configured max_turns for one user prompt."""

    def __init__(self, max_turns: int) -> None:
        super().__init__(f"Exceeded maximum turn limit ({max_turns})")
        self.max_turns = max_turns


@dataclass(frozen=True)
class TurnResult:
    """What happened in a single LLM turn."""

    message: ConversationMessage
    text: str
    tool_calls: tuple[ToolUseBlock, ...]
    tool_results: tuple[ToolResultBlock, ...]
    usage: UsageSnapshot
    is_final: bool


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
    context_window_tokens: int | None = None
    auto_compact_threshold_tokens: int | None = None
    permission_prompt: PermissionPrompt | None = None
    ask_user_prompt: AskUserPrompt | None = None
    max_turns: int | None = 200
    hook_executor: HookExecutor | None = None
    tool_metadata: dict[str, object] | None = None
    trace_observer: TraceObserver | None = None


def _append_capped_unique(bucket: list[Any], value: Any, *, limit: int) -> None:
    if value in bucket:
        bucket.remove(value)
    bucket.append(value)
    if len(bucket) > limit:
        del bucket[:-limit]


def _task_focus_state(tool_metadata: dict[str, object] | None) -> dict[str, object]:
    if tool_metadata is None:
        return {}
    value = tool_metadata.setdefault(
        "task_focus_state",
        {
            "goal": "",
            "recent_goals": [],
            "active_artifacts": [],
            "verified_state": [],
            "next_step": "",
        },
    )
    if isinstance(value, dict):
        value.setdefault("goal", "")
        value.setdefault("recent_goals", [])
        value.setdefault("active_artifacts", [])
        value.setdefault("verified_state", [])
        value.setdefault("next_step", "")
        return value
    replacement = {
        "goal": "",
        "recent_goals": [],
        "active_artifacts": [],
        "verified_state": [],
        "next_step": "",
    }
    tool_metadata["task_focus_state"] = replacement
    return replacement


def _summarize_focus_text(text: str) -> str:
    normalized = " ".join(text.split())
    if not normalized:
        return ""
    return normalized[:240]


def remember_user_goal(
    tool_metadata: dict[str, object] | None,
    prompt: str,
) -> None:
    state = _task_focus_state(tool_metadata)
    summary = _summarize_focus_text(prompt)
    if not summary:
        return
    recent_goals = state.setdefault("recent_goals", [])
    if isinstance(recent_goals, list):
        _append_capped_unique(recent_goals, summary, limit=MAX_TRACKED_USER_GOALS)
    state["goal"] = summary


def _remember_active_artifact(
    tool_metadata: dict[str, object] | None,
    artifact: str,
) -> None:
    normalized = artifact.strip()
    if not normalized:
        return
    state = _task_focus_state(tool_metadata)
    artifacts = state.setdefault("active_artifacts", [])
    if isinstance(artifacts, list):
        _append_capped_unique(artifacts, normalized[:240], limit=MAX_TRACKED_ACTIVE_ARTIFACTS)


def _remember_verified_work(
    tool_metadata: dict[str, object] | None,
    entry: str,
) -> None:
    normalized = entry.strip()
    if not normalized:
        return
    bucket = _tool_metadata_bucket(tool_metadata, "recent_verified_work")
    _append_capped_unique(bucket, normalized[:320], limit=MAX_TRACKED_VERIFIED_WORK)
    state = _task_focus_state(tool_metadata)
    verified_state = state.setdefault("verified_state", [])
    if isinstance(verified_state, list):
        _append_capped_unique(verified_state, normalized[:320], limit=MAX_TRACKED_VERIFIED_WORK)


def _tool_metadata_bucket(
    tool_metadata: dict[str, object] | None,
    key: str,
) -> list[Any]:
    if tool_metadata is None:
        return []
    value = tool_metadata.setdefault(key, [])
    if isinstance(value, list):
        return value
    replacement: list[Any] = []
    tool_metadata[key] = replacement
    return replacement


def _remember_read_file(
    tool_metadata: dict[str, object] | None,
    *,
    path: str,
    offset: int,
    limit: int,
    output: str,
) -> None:
    bucket = _tool_metadata_bucket(tool_metadata, "read_file_state")
    preview_lines = [line.strip() for line in output.splitlines()[:6] if line.strip()]
    entry = {
        "path": path,
        "span": f"lines {offset + 1}-{offset + limit}",
        "preview": " | ".join(preview_lines)[:320],
        "timestamp": time.time(),
    }
    if isinstance(bucket, list):
        bucket[:] = [
            existing
            for existing in bucket
            if not isinstance(existing, dict) or str(existing.get("path") or "") != path
        ]
        bucket.append(entry)
        if len(bucket) > MAX_TRACKED_READ_FILES:
            del bucket[:-MAX_TRACKED_READ_FILES]


def _remember_skill_invocation(
    tool_metadata: dict[str, object] | None,
    *,
    skill_name: str,
) -> None:
    bucket = _tool_metadata_bucket(tool_metadata, "invoked_skills")
    normalized = skill_name.strip()
    if not normalized:
        return
    if normalized in bucket:
        bucket.remove(normalized)
    bucket.append(normalized)
    if len(bucket) > MAX_TRACKED_SKILLS:
        del bucket[:-MAX_TRACKED_SKILLS]


def _remember_async_agent_activity(
    tool_metadata: dict[str, object] | None,
    *,
    tool_name: str,
    tool_input: dict[str, object],
    output: str,
) -> None:
    bucket = _tool_metadata_bucket(tool_metadata, "async_agent_state")
    if tool_name == "agent":
        description = str(tool_input.get("description") or tool_input.get("prompt") or "").strip()
        summary = f"Spawned async agent. {description}".strip()
        if output.strip():
            summary = f"{summary} [{output.strip()[:180]}]".strip()
    elif tool_name == "send_message":
        target = str(tool_input.get("task_id") or "").strip()
        summary = f"Sent follow-up message to async agent {target}".strip()
    else:
        summary = output.strip()[:220] or f"Async agent activity via {tool_name}"
    bucket.append(summary)
    if len(bucket) > MAX_TRACKED_ASYNC_AGENT_EVENTS:
        del bucket[:-MAX_TRACKED_ASYNC_AGENT_EVENTS]


def _remember_work_log(
    tool_metadata: dict[str, object] | None,
    *,
    entry: str,
) -> None:
    bucket = _tool_metadata_bucket(tool_metadata, "recent_work_log")
    normalized = entry.strip()
    if not normalized:
        return
    bucket.append(normalized[:320])
    if len(bucket) > MAX_TRACKED_WORK_LOG:
        del bucket[:-MAX_TRACKED_WORK_LOG]


def _update_plan_mode(tool_metadata: dict[str, object] | None, mode: str) -> None:
    if tool_metadata is None:
        return
    tool_metadata["permission_mode"] = mode


def _record_tool_carryover(
    context: QueryContext,
    *,
    tool_name: str,
    tool_input: dict[str, object],
    tool_output: str,
    is_error: bool,
    resolved_file_path: str | None,
) -> None:
    if is_error:
        return
    if resolved_file_path is not None:
        _remember_active_artifact(context.tool_metadata, resolved_file_path)
    if tool_name == "read_file" and resolved_file_path is not None:
        offset = int(tool_input.get("offset") or 0)
        limit = int(tool_input.get("limit") or 200)
        _remember_read_file(
            context.tool_metadata,
            path=resolved_file_path,
            offset=offset,
            limit=limit,
            output=tool_output,
        )
        _remember_verified_work(
            context.tool_metadata,
            f"Inspected file {resolved_file_path} (lines {offset + 1}-{offset + limit})",
        )
    elif tool_name == "skill":
        _remember_skill_invocation(
            context.tool_metadata,
            skill_name=str(tool_input.get("name") or ""),
        )
        skill_name = str(tool_input.get("name") or "").strip()
        if skill_name:
            _remember_active_artifact(context.tool_metadata, f"skill:{skill_name}")
            _remember_verified_work(context.tool_metadata, f"Loaded skill {skill_name}")
    elif tool_name in {"agent", "send_message"}:
        _remember_async_agent_activity(
            context.tool_metadata,
            tool_name=tool_name,
            tool_input=tool_input,
            output=tool_output,
        )
        description = str(
            tool_input.get("description") or tool_input.get("prompt") or tool_name
        ).strip()
        _remember_verified_work(
            context.tool_metadata,
            f"Confirmed async-agent activity via {tool_name}: {description[:180]}",
        )
    elif tool_name == "enter_plan_mode":
        _update_plan_mode(context.tool_metadata, "plan")
    elif tool_name == "exit_plan_mode":
        _update_plan_mode(context.tool_metadata, "default")
    elif tool_name == "web_fetch":
        url = str(tool_input.get("url") or "").strip()
        if url:
            _remember_active_artifact(context.tool_metadata, url)
            _remember_verified_work(context.tool_metadata, f"Fetched remote content from {url}")
    elif tool_name == "web_search":
        query = str(tool_input.get("query") or "").strip()
        if query:
            _remember_verified_work(context.tool_metadata, f"Ran web search for {query[:180]}")
    elif tool_name == "glob":
        pattern = str(tool_input.get("pattern") or "").strip()
        if pattern:
            _remember_verified_work(context.tool_metadata, f"Expanded glob pattern {pattern[:180]}")
    elif tool_name == "grep":
        pattern = str(tool_input.get("pattern") or "").strip()
        if pattern:
            _remember_verified_work(
                context.tool_metadata,
                f"Checked repository matches for grep pattern {pattern[:180]}",
            )
    elif tool_name == "bash":
        command = str(tool_input.get("command") or "").strip()
        summary = tool_output.splitlines()[0].strip() if tool_output.strip() else "no output"
        _remember_verified_work(
            context.tool_metadata,
            f"Ran bash command {command[:160]} [{summary[:120]}]",
        )
    if tool_name == "read_file" and resolved_file_path is not None:
        _remember_work_log(
            context.tool_metadata,
            entry=f"Read file {resolved_file_path}",
        )
    elif tool_name == "bash":
        command = str(tool_input.get("command") or "").strip()
        summary = tool_output.splitlines()[0].strip() if tool_output.strip() else "no output"
        _remember_work_log(
            context.tool_metadata,
            entry=f"Ran bash: {command[:160]} [{summary[:120]}]",
        )
    elif tool_name == "grep":
        pattern = str(tool_input.get("pattern") or "").strip()
        _remember_work_log(
            context.tool_metadata,
            entry=f"Searched with grep pattern={pattern[:160]}",
        )
    elif tool_name == "skill":
        _remember_work_log(
            context.tool_metadata,
            entry=f"Loaded skill {str(tool_input.get('name') or '').strip()}",
        )
    elif tool_name in {"agent", "send_message"}:
        _remember_work_log(
            context.tool_metadata,
            entry=f"Async agent action via {tool_name}",
        )
    elif tool_name == "enter_plan_mode":
        _remember_work_log(context.tool_metadata, entry="Entered plan mode")
    elif tool_name == "exit_plan_mode":
        _remember_work_log(context.tool_metadata, entry="Exited plan mode")


async def run_single_turn(
    context: QueryContext,
    messages: list[ConversationMessage],
) -> TurnResult:
    """Execute one LLM call and any requested tools, mutating *messages* in place."""
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
    reactive_compact_attempted = False
    last_compaction_result: tuple[list[ConversationMessage], bool] = (messages, False)

    async def _stream_compaction(
        *,
        trigger: str,
        force: bool = False,
    ) -> AsyncIterator[tuple[StreamEvent, UsageSnapshot | None]]:
        nonlocal last_compaction_result
        progress_queue: asyncio.Queue[CompactProgressEvent] = asyncio.Queue()

        async def _progress(event: CompactProgressEvent) -> None:
            await progress_queue.put(event)

        task = asyncio.create_task(
            auto_compact_if_needed(
                messages,
                api_client=context.api_client,
                model=context.model,
                system_prompt=context.system_prompt,
                state=compact_state,
                progress_callback=_progress,
                force=force,
                trigger=trigger,
                hook_executor=context.hook_executor,
                carryover_metadata=context.tool_metadata,
                context_window_tokens=context.context_window_tokens,
                auto_compact_threshold_tokens=context.auto_compact_threshold_tokens,
            )
        )
        while True:
            try:
                event = await asyncio.wait_for(progress_queue.get(), timeout=0.05)
                yield event, None
            except asyncio.TimeoutError:
                if task.done():
                    break
                continue
        while not progress_queue.empty():
            yield progress_queue.get_nowait(), None
        last_compaction_result = await task
        return

    turn_count = 0
    while context.max_turns is None or turn_count < context.max_turns:
        turn_count += 1
        # --- auto-compact check before calling the model ---------------
        async for event, usage in _stream_compaction(trigger="auto"):
            yield event, usage
        messages, was_compacted = last_compaction_result
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
                        yield (
                            StatusEvent(
                                message=(
                                    f"Request failed; retrying in {event.delay_seconds:.1f}s "
                                    f"(attempt {event.attempt + 1} of {event.max_attempts}): {event.message}"
                                )
                            ),
                            None,
                        )
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
        except Exception as exc:
            error_msg = str(exc)
            if not reactive_compact_attempted and _is_prompt_too_long_error(exc):
                reactive_compact_attempted = True
                yield StatusEvent(message=REACTIVE_COMPACT_STATUS_MESSAGE), None
                async for event, usage in _stream_compaction(trigger="reactive", force=True):
                    yield event, usage
                messages, was_compacted = last_compaction_result
                if was_compacted:
                    continue
            if (
                "connect" in error_msg.lower()
                or "timeout" in error_msg.lower()
                or "network" in error_msg.lower()
            ):
                yield (
                    ErrorEvent(
                        message=f"Network error: {error_msg}. Check your internet connection and try again."
                    ),
                    None,
                )
            else:
                yield ErrorEvent(message=f"API error: {error_msg}"), None
            return

        coordinator_context_message: ConversationMessage | None = None
        if context.system_prompt.startswith("You are a **coordinator**."):
            if (
                messages
                and messages[-1].role == "user"
                and messages[-1].text.startswith("# Coordinator User Context")
            ):
                coordinator_context_message = messages.pop()

        if final_message.role == "assistant" and final_message.is_effectively_empty():
            log.warning("dropping empty assistant message from provider response")
            yield (
                ErrorEvent(
                    message=(
                        "Model returned an empty assistant message. "
                        "The turn was ignored to keep the session healthy."
                    )
                ),
                usage,
            )
            return

        messages.append(final_message)
        yield AssistantTurnComplete(message=final_message, usage=usage), usage

        if coordinator_context_message is not None:
            messages.append(coordinator_context_message)

        if not final_message.tool_uses:
            return

        tool_calls = final_message.tool_uses

        if len(tool_calls) == 1:
            # Single tool: sequential (stream events immediately)
            tc = tool_calls[0]
            yield ToolExecutionStarted(tool_name=tc.name, tool_input=tc.input), None
            result = await _execute_tool_call(context, tc.name, tc.id, tc.input)
            yield (
                ToolExecutionCompleted(
                    tool_name=tc.name,
                    output=result.content,
                    is_error=result.is_error,
                ),
                None,
            )
            tool_results = [result]
        else:
            # Multiple tools: execute concurrently, emit events after
            for tc in tool_calls:
                yield ToolExecutionStarted(tool_name=tc.name, tool_input=tc.input), None

            async def _run(tc):
                return await _execute_tool_call(context, tc.name, tc.id, tc.input)

            # Use return_exceptions=True so a single failing tool does not abandon
            # its siblings as cancelled coroutines and leave the conversation with
            # un-replied tool_use blocks (Anthropic's API rejects the next request
            # on the session if any tool_use is missing a matching tool_result).
            raw_results = await asyncio.gather(
                *[_run(tc) for tc in tool_calls], return_exceptions=True
            )
            tool_results = []
            for tc, result in zip(tool_calls, raw_results):
                if isinstance(result, BaseException):
                    log.exception(
                        "tool execution raised: name=%s id=%s",
                        tc.name,
                        tc.id,
                        exc_info=result,
                    )
                    result = ToolResultBlock(
                        tool_use_id=tc.id,
                        content=f"Tool {tc.name} failed: {type(result).__name__}: {result}",
                        is_error=True,
                    )
                tool_results.append(result)

            for tc, result in zip(tool_calls, tool_results):
                yield (
                    ToolExecutionCompleted(
                        tool_name=tc.name,
                        output=result.content,
                        is_error=result.is_error,
                    ),
                    None,
                )

        messages.append(ConversationMessage(role="user", content=tool_results))

    if context.max_turns is not None:
        raise MaxTurnsExceeded(context.max_turns)
    raise RuntimeError("Query loop exited without a max_turns limit or final response")


async def _execute_tools(
    context: QueryContext,
    tool_calls: list[ToolUseBlock],
) -> tuple[ToolResultBlock, ...]:
    """Execute tool calls concurrently when more than one tool is requested."""
    if len(tool_calls) == 1:
        tc = tool_calls[0]
        return (await _execute_tool_call(context, tc.name, tc.id, tc.input),)

    results = await asyncio.gather(
        *[_execute_tool_call(context, tc.name, tc.id, tc.input) for tc in tool_calls]
    )
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
                {
                    "tool_name": tool_name,
                    "tool_input": tool_input,
                    "event": HookEvent.PRE_TOOL_USE.value,
                },
            )
            if pre_hooks.blocked:
                reason = pre_hooks.reason or f"pre_tool_use hook blocked {tool_name}"
                tool_handle.update(output=reason, metadata={"blocked_by_hook": True})
                return ToolResultBlock(
                    tool_use_id=tool_use_id,
                    content=reason,
                    is_error=True,
                )

        log.debug("tool_call start: %s id=%s", tool_name, tool_use_id)

        tool = context.tool_registry.get(tool_name)
        if tool is None:
            log.warning("unknown tool: %s", tool_name)
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
            log.warning("invalid input for %s: %s", tool_name, exc)
            tool_handle.update(
                output=f"Invalid input for {tool_name}: {exc}",
                metadata={"is_error": True},
            )
            return ToolResultBlock(
                tool_use_id=tool_use_id,
                content=f"Invalid input for {tool_name}: {exc}",
                is_error=True,
            )

        # Normalize common tool inputs before permission checks so path rules apply
        # consistently across built-in tools that use `file_path`, `path`, or
        # directory-scoped roots such as `glob`/`grep`.
        _file_path = _resolve_permission_file_path(context.cwd, tool_input, parsed_input)
        _command = _extract_permission_command(tool_input, parsed_input)
        log.debug(
            "permission check: %s read_only=%s path=%s cmd=%s",
            tool_name,
            tool.is_read_only(parsed_input),
            _file_path,
            _command and _command[:80],
        )
        decision = context.permission_checker.evaluate(
            tool_name,
            is_read_only=tool.is_read_only(parsed_input),
            file_path=_file_path,
            command=_command,
        )
        if not decision.allowed:
            if decision.requires_confirmation and context.permission_prompt is not None:
                log.debug("permission prompt for %s: %s", tool_name, decision.reason)
                confirmed = await context.permission_prompt(tool_name, decision.reason)
                if not confirmed:
                    log.debug("permission denied by user for %s", tool_name)
                    output = decision.reason or f"Permission denied for {tool_name}"
                    tool_handle.update(
                        output=output,
                        metadata={"is_error": True, "permission_reason": decision.reason},
                    )
                    return ToolResultBlock(
                        tool_use_id=tool_use_id,
                        content=output,
                        is_error=True,
                    )
            else:
                log.debug("permission blocked for %s: %s", tool_name, decision.reason)
                output = decision.reason or f"Permission denied for {tool_name}"
                tool_handle.update(
                    output=output,
                    metadata={"is_error": True, "permission_reason": decision.reason},
                )
                return ToolResultBlock(
                    tool_use_id=tool_use_id,
                    content=output,
                    is_error=True,
                )

        log.debug("executing %s ...", tool_name)
        t0 = time.monotonic()
        try:
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
        except Exception as exc:
            log.exception(
                "tool execution raised: name=%s id=%s",
                tool_name,
                tool_use_id,
                exc_info=exc,
            )
            result = ToolResult(
                output=f"Tool {tool_name} failed: {type(exc).__name__}: {exc}",
                is_error=True,
            )
        elapsed = time.monotonic() - t0
        log.debug(
            "executed %s in %.2fs err=%s output_len=%d",
            tool_name,
            elapsed,
            result.is_error,
            len(result.output or ""),
        )
        tool_result = ToolResultBlock(
            tool_use_id=tool_use_id,
            content=result.output,
            is_error=result.is_error,
        )
        _record_tool_carryover(
            context,
            tool_name=tool_name,
            tool_input=tool_input,
            tool_output=tool_result.content,
            is_error=tool_result.is_error,
            resolved_file_path=_file_path,
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


def _trace_model_input(
    system_prompt: str, messages: list[ConversationMessage]
) -> list[dict[str, Any]]:
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
                    "name": _tool_name_for_result(messages, result.tool_use_id)
                    or result.tool_use_id,
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
    for key in ("file_path", "path", "root"):
        value = raw_input.get(key)
        if isinstance(value, str) and value.strip():
            path = Path(value).expanduser()
            if not path.is_absolute():
                path = cwd / path
            return str(path.resolve())

    for attr in ("file_path", "path", "root"):
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
