"""Events yielded by the query engine."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from openharness.api.usage import UsageSnapshot
from openharness.engine.messages import ConversationMessage


@dataclass(frozen=True)
class ModelRequest:
    """Snapshot of the request that crosses to the LLM provider for one turn.

    Complements ``AssistantTurnComplete`` (the response side) and the
    ``ToolExecution*`` events (tool I/O). Captures inputs that are not
    otherwise on the audit trail — most importantly the system prompt
    and the tool surface — so a trial run can be reasoned about and
    replayed without re-running the agent. Aligned with the OpenTelemetry
    GenAI / Langfuse "generation" pattern: one request event per model
    invocation, recorded next to the response on a single event stream.

    The conversation history itself is intentionally not duplicated here
    on every turn; it is reconstructable from the user/assistant rows in
    ``messages.jsonl`` (cross-referenced via ``message_count`` /
    ``turn_index`` on this event).
    """

    model: str
    system_prompt: str
    tools: tuple[str, ...]
    max_tokens: int
    max_turns: int | None = None
    turn_index: int = 0
    message_count: int = 0
    agent: str | None = None


@dataclass(frozen=True)
class AssistantTextDelta:
    """Incremental assistant text."""

    text: str


@dataclass(frozen=True)
class AssistantTurnComplete:
    """Completed assistant turn."""

    message: ConversationMessage
    usage: UsageSnapshot


@dataclass(frozen=True)
class ToolExecutionStarted:
    """The engine is about to execute a tool."""

    tool_name: str
    tool_input: dict[str, Any]


@dataclass(frozen=True)
class ToolExecutionCompleted:
    """A tool has finished executing."""

    tool_name: str
    output: str
    is_error: bool = False


@dataclass(frozen=True)
class ErrorEvent:
    """An error that should be surfaced to the user."""

    message: str
    recoverable: bool = True


@dataclass(frozen=True)
class StatusEvent:
    """A transient system status message shown to the user."""

    message: str


@dataclass(frozen=True)
class CompactProgressEvent:
    """Structured progress event for conversation compaction."""

    phase: Literal[
        "hooks_start",
        "context_collapse_start",
        "context_collapse_end",
        "session_memory_start",
        "session_memory_end",
        "compact_start",
        "compact_retry",
        "compact_end",
        "compact_failed",
    ]
    trigger: Literal["auto", "manual", "reactive"]
    message: str | None = None
    attempt: int | None = None
    checkpoint: str | None = None
    metadata: dict[str, Any] | None = None


StreamEvent = (
    ModelRequest
    | AssistantTextDelta
    | AssistantTurnComplete
    | ToolExecutionStarted
    | ToolExecutionCompleted
    | ErrorEvent
    | StatusEvent
    | CompactProgressEvent
)
