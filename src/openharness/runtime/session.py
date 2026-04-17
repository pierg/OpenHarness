"""AgentRuntime — wires settings, API client, tools, tracing, and logging."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from openharness.agents.config import AgentConfig
from openharness.agents.contracts import AgentRunResult, TaskDefinition
from openharness.api.client import SupportsStreamingMessages
from openharness.api.factory import create_api_client
from openharness.api.provider import detect_provider
from openharness.config import load_settings
from openharness.config.settings import Settings
from openharness.engine.cost_tracker import CostTracker
from openharness.engine.messages import ConversationMessage
from openharness.engine.query import QueryContext
from openharness.engine.stream_events import (
    AssistantTextDelta,
    AssistantTurnComplete,
    StreamEvent,
    ToolExecutionCompleted,
    ToolExecutionStarted,
)
from openharness.observability import NullTraceObserver, TraceObserver
from openharness.permissions import PermissionChecker
from openharness.permissions.modes import PermissionMode
from openharness.prompts.context import build_runtime_system_prompt
from openharness.tools import WorkspaceToolRegistryFactory
from openharness.tools.base import ToolRegistry
from openharness.workspace import Workspace

log = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass(frozen=True)
class AgentLogPaths:
    """Paths where a run should emit JSONL logs."""

    messages_path: str
    events_path: str


class AgentRuntime:
    """Execution substrate that any agent uses to interact with the system.

    Responsibilities:
    - Hold the active workspace.
    - Provide the API client and tool registry.
    - Provide the current settings and permissions.
    - Track token usage.
    - Write JSONL event / message logs.
    - Provide ``run_agent_config`` / ``create_conversation`` helpers so that
      agent architectures only contain orchestration logic.
    """

    def __init__(
        self,
        workspace: Workspace,
        tool_registry: ToolRegistry | None = None,
        *,
        settings: Settings | None = None,
        permission_mode: PermissionMode | None = None,
        api_client: SupportsStreamingMessages | None = None,
        log_paths: AgentLogPaths | None = None,
        trace_observer: TraceObserver | None = None,
        tool_registry_factory: Any | None = None,
    ) -> None:
        self.workspace = workspace
        self.tool_registry = tool_registry
        self._tool_registry_factory_override = tool_registry_factory
        self.settings = settings or load_settings()
        self._explicit_api_client = api_client
        self._client_cache: dict[str, SupportsStreamingMessages] = {}

        self._log_paths = log_paths
        self._trace_observer = trace_observer or NullTraceObserver()
        self._tracker = CostTracker()

        perm_settings = self.settings.permission
        if permission_mode is not None:
            perm_settings = perm_settings.model_copy(update={"mode": permission_mode})
        self._permission_checker = PermissionChecker(perm_settings)

    @property
    def api_client(self) -> SupportsStreamingMessages:
        """The primary API client (defaults to settings.model if none explicit)."""
        if self._explicit_api_client:
            return self._explicit_api_client
        return self._get_client(self.settings.model)

    def _get_client(self, model: str) -> SupportsStreamingMessages:
        """Return (and cache) a client for the given model string."""
        if self._explicit_api_client:
            return self._explicit_api_client
        if model not in self._client_cache:
            temp_settings = self.settings.model_copy(update={"model": model})
            self._client_cache[model] = create_api_client(temp_settings)
        return self._client_cache[model]

    def get_provider_name(self, model: str | None = None) -> str:
        """Return the resolved LLM provider name for the given model.

        Defaults to the current primary model in settings if none provided.
        """
        m = model or self.settings.model
        temp_settings = self.settings.model_copy(update={"model": m})
        return detect_provider(temp_settings).name

    @property
    def provider_name(self) -> str:
        """Return the resolved LLM provider name for the primary model."""
        return self.get_provider_name()

    @property
    def trace_observer(self) -> TraceObserver:
        """Return the active trace observer."""
        return self._trace_observer

    # ------------------------------------------------------------------
    # High-level agent execution helpers
    # ------------------------------------------------------------------

    def create_conversation(
        self,
        config: AgentConfig,
        task: TaskDefinition,
        extra_template_vars: dict[str, Any] | None = None,
    ):
        """Create a ``Conversation`` for step-level control over the loop."""
        from openharness.engine.conversation import Conversation

        query_ctx, messages = self._prepare_query(config, task, extra_template_vars)
        return Conversation(
            query_ctx,
            messages,
            _track_usage=self.track_usage,
            _log_event=self.log_event,
            _log_messages=self.log_messages,
        )

    async def run_agent_config(
        self,
        config: AgentConfig,
        task: TaskDefinition,
        extra_template_vars: dict[str, Any] | None = None,
        output_type: type[T] | None = None,
    ) -> str | T:
        """Run a single agent config end-to-end.

        When *output_type* is ``None`` (default), returns the final text as
        ``str``.  When a Pydantic ``BaseModel`` subclass is provided, the
        LLM is instructed to return JSON matching that schema and the
        validated model instance is returned.
        """
        extra = dict(extra_template_vars or {})
        with self._trace_observer.span(
            name=f"agent:{config.name}",
            input={
                "instruction": task.instruction,
                "payload": task.payload,
            },
            metadata={
                "architecture": config.architecture,
                "model": config.model,
                "structured_output_type": (
                    output_type.__name__
                    if output_type is not None and issubclass(output_type, BaseModel)
                    else None
                ),
            },
        ) as span:
            if output_type is not None and issubclass(output_type, BaseModel):
                schema_json = json.dumps(output_type.model_json_schema(), indent=2)
                extra["_output_schema_instruction"] = (
                    "\n\nIMPORTANT: You MUST respond ONLY with a valid JSON object "
                    f"matching this schema:\n```json\n{schema_json}\n```\n"
                    "Do not include any text before or after the JSON object."
                )

            conv = self.create_conversation(config, task, extra or None)
            text = await conv.run_to_completion()

            if output_type is not None:
                parsed = _parse_structured_output(text, output_type)
                span.update(
                    output=parsed,
                    metadata={"message_count": len(conv.messages)},
                )
                return parsed

            span.update(
                output=text,
                metadata={"message_count": len(conv.messages)},
            )
            return text

    # ------------------------------------------------------------------
    # Lower-level building blocks (still public for backward compat)
    # ------------------------------------------------------------------

    def build_query_context(
        self,
        *,
        model: str,
        system_prompt: str,
        max_tokens: int,
        max_turns: int,
    ) -> QueryContext:
        """Assemble a ``QueryContext`` ready for ``run_query``."""
        if self.tool_registry is None:
            raise RuntimeError(
                "build_query_context requires an explicit tool_registry; "
                "prefer using run_agent_config() instead."
            )
        return QueryContext(
            api_client=self._get_client(model),
            tool_registry=self.tool_registry,
            permission_checker=self._permission_checker,
            cwd=Path(self.workspace.cwd),
            model=model,
            system_prompt=system_prompt,
            max_tokens=max_tokens,
            max_turns=max_turns,
            trace_observer=self._trace_observer,
        )

    # ------------------------------------------------------------------
    # Usage tracking
    # ------------------------------------------------------------------

    def track_usage(self, usage: Any) -> None:
        """Accumulate a usage snapshot (``None`` is silently ignored)."""
        if usage is not None:
            self._tracker.add(usage)

    def build_result(self, output: Any) -> AgentRunResult:
        """Package tracked usage and *output* into an ``AgentRunResult``."""
        if isinstance(output, str):
            output = output.strip()
        return AgentRunResult(
            output=output,
            input_tokens=self._tracker.total.input_tokens,
            output_tokens=self._tracker.total.output_tokens,
        )

    # ------------------------------------------------------------------
    # JSONL logging
    # ------------------------------------------------------------------

    def log_event(self, event: StreamEvent) -> None:
        """Append a serialised stream event to the events JSONL file."""
        if self._log_paths is not None:
            _append_jsonl(
                Path(self._log_paths.events_path),
                _serialize_event(event),
            )

    def log_messages(self, messages: list[ConversationMessage]) -> None:
        """Flush all conversation messages to the messages JSONL file."""
        if self._log_paths is not None:
            path = Path(self._log_paths.messages_path)
            for msg in messages:
                _append_jsonl(path, msg.model_dump(mode="json"))

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_tool_registry(self, tool_names: tuple[str, ...]) -> ToolRegistry:
        """Build a tool registry scoped to the given tool names.

        If a custom ``tool_registry_factory`` was provided at construction
        time it takes precedence (the factory controls which tools are built).
        """
        if self._tool_registry_factory_override is not None:
            return self._tool_registry_factory_override.build(self.workspace)
        return WorkspaceToolRegistryFactory(tool_names=tool_names).build(self.workspace)

    def _prepare_query(
        self,
        config: AgentConfig,
        task: TaskDefinition,
        extra_template_vars: dict[str, Any] | None = None,
    ) -> tuple[QueryContext, list[ConversationMessage]]:
        """Render prompts, build a QueryContext, and return initial messages."""
        extra = extra_template_vars or {}

        openharness_context = build_runtime_system_prompt(
            self.settings,
            cwd=self.workspace.cwd,
            latest_user_prompt=task.instruction,
        )

        schema_instruction = extra.pop("_output_schema_instruction", "")

        system_prompt = config.render_prompt(
            "system",
            openharness_system_context=openharness_context,
            cwd=self.workspace.cwd,
            provider=self.get_provider_name(config.model),
            **task.payload,
            **extra,
        )
        system_prompt += schema_instruction

        user_payload = {"instruction": task.instruction, "payload": task.payload, **task.payload, **extra}
        user_message = config.render_prompt("user", **user_payload)

        tool_registry = self._build_tool_registry(config.tools)

        query_ctx = QueryContext(
            api_client=self._get_client(config.model),
            tool_registry=tool_registry,
            permission_checker=self._permission_checker,
            cwd=Path(self.workspace.cwd),
            model=config.model,
            system_prompt=system_prompt,
            max_tokens=config.max_tokens,
            max_turns=config.max_turns,
            trace_observer=self._trace_observer,
        )

        messages = [ConversationMessage.from_user_text(user_message)]
        return query_ctx, messages


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _parse_structured_output(text: str, output_type: type[T]) -> T:
    """Extract and validate JSON from an LLM response."""
    text = text.strip()

    # Find the first occurrence of a JSON block or object
    if "```json" in text:
        text = text.split("```json", 1)[1].split("```", 1)[0]
    elif "```" in text:
        text = text.split("```", 1)[1].split("```", 1)[0]
    else:
        # Try to find the first '{' and last '}'
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1:
            text = text[start : end + 1]

    return output_type.model_validate_json(text.strip())


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _serialize_event(event: StreamEvent) -> dict[str, Any]:
    if isinstance(event, AssistantTextDelta):
        return {"type": "assistant_delta", "text": event.text}
    if isinstance(event, AssistantTurnComplete):
        return {
            "type": "assistant_complete",
            "message": event.message.model_dump(mode="json"),
            "usage": event.usage.model_dump(mode="json"),
        }
    if isinstance(event, ToolExecutionStarted):
        return {
            "type": "tool_started",
            "tool_name": event.tool_name,
            "tool_input": event.tool_input,
        }
    if isinstance(event, ToolExecutionCompleted):
        return {
            "type": "tool_completed",
            "tool_name": event.tool_name,
            "output": event.output,
            "is_error": event.is_error,
        }
    return {"type": "unknown"}
