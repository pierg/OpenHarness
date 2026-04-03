"""Framework-agnostic OpenHarness agent.

``OpenHarnessSimpleAgent`` wraps the query engine and operates exclusively
through the ``AgentWorkspace`` protocol, making it portable across local
and remote execution substrates without any code changes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openharness.agents.contracts import (
    AgentLogPaths,
    AgentRunContext,
    AgentRunResult,
    AgentWorkspace,
    ToolRegistryFactory,
)
from openharness.agents.remote_tools import DEFAULT_REMOTE_TOOL_NAMES, RemoteToolRegistryFactory
from openharness.api.client import SupportsStreamingMessages
from openharness.api.factory import create_api_client
from openharness.api.provider import detect_provider
from openharness.config import load_settings
from openharness.config.settings import PermissionSettings
from openharness.engine.cost_tracker import CostTracker
from openharness.engine.messages import ConversationMessage
from openharness.engine.query import QueryContext, run_query
from openharness.engine.stream_events import (
    AssistantTextDelta,
    AssistantTurnComplete,
    StreamEvent,
    ToolExecutionCompleted,
    ToolExecutionStarted,
)
from openharness.observability import NullTraceObserver
from openharness.permissions import PermissionChecker
from openharness.permissions.modes import PermissionMode


@dataclass(frozen=True)
class OpenHarnessSimpleAgentConfig:
    """Configuration for ``OpenHarnessSimpleAgent``."""

    model: str
    tool_names: tuple[str, ...] = DEFAULT_REMOTE_TOOL_NAMES
    max_turns: int = 8
    max_tokens: int = 4096
    system_prompt: str | None = None


class OpenHarnessSimpleAgent:
    """A portable agent that operates through the ``AgentWorkspace`` protocol.

    Pass an explicit ``api_client`` to override the default client resolved
    from settings (useful in tests and integrations that supply their own client).
    """

    def __init__(
        self,
        config: OpenHarnessSimpleAgentConfig,
        *,
        api_client: SupportsStreamingMessages | None = None,
        tool_registry_factory: ToolRegistryFactory | None = None,
    ) -> None:
        self._config = config
        self._api_client_override = api_client
        self._tool_registry_factory = tool_registry_factory or RemoteToolRegistryFactory(
            tool_names=config.tool_names
        )

    async def run(
        self,
        instruction: str,
        workspace: AgentWorkspace,
        *,
        log_paths: AgentLogPaths | None = None,
        run_context: AgentRunContext | None = None,
    ) -> AgentRunResult:
        """Run the agent on *instruction* against *workspace*."""
        settings = load_settings().merge_cli_overrides(model=self._config.model)
        api_client = self._api_client_override or create_api_client(settings)
        trace_observer = (
            run_context.trace_observer
            if run_context is not None and run_context.trace_observer is not None
            else NullTraceObserver()
        )
        tracker = CostTracker()
        messages = [ConversationMessage.from_user_text(instruction)]
        final_text = ""
        query_context = QueryContext(
            api_client=api_client,
            tool_registry=self._tool_registry_factory.build(workspace),
            permission_checker=PermissionChecker(PermissionSettings(mode=PermissionMode.FULL_AUTO)),
            cwd=Path(workspace.cwd),
            model=self._config.model,
            system_prompt=self._config.system_prompt or _build_system_prompt(workspace.cwd, settings),
            max_tokens=self._config.max_tokens,
            max_turns=self._config.max_turns,
            trace_observer=trace_observer,
        )
        try:
            async for event, usage in run_query(query_context, messages):
                if usage is not None:
                    tracker.add(usage)
                _maybe_log_event(log_paths, event)
                if isinstance(event, AssistantTextDelta):
                    final_text += event.text
                elif isinstance(event, AssistantTurnComplete) and event.message.text.strip():
                    final_text = event.message.text.strip()
        finally:
            _maybe_log_messages(log_paths, messages)

        return AgentRunResult(
            final_text=final_text.strip(),
            input_tokens=tracker.total.input_tokens,
            output_tokens=tracker.total.output_tokens,
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_system_prompt(cwd: str, settings: Any) -> str:
    base = settings.system_prompt or (
        "You are a simple OpenHarness coding agent. "
        "Solve the task directly using the available tools."
    )
    provider_name = detect_provider(settings).name
    return (
        f"{base}\n\n"
        "# Session\n"
        f"- Working directory: {cwd}\n"
        f"- Provider: {provider_name}\n"
        "- The tools operate against the configured workspace.\n"
        "- Prefer the smallest correct change and finish once the task is solved.\n"
    )


def _maybe_log_event(log_paths: AgentLogPaths | None, event: StreamEvent) -> None:
    if log_paths is not None:
        _append_jsonl(Path(log_paths.events_path), _serialize_event(event))


def _maybe_log_messages(log_paths: AgentLogPaths | None, messages: list[ConversationMessage]) -> None:
    if log_paths is not None:
        path = Path(log_paths.messages_path)
        for msg in messages:
            _append_jsonl(path, msg.model_dump(mode="json"))


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=True) + "\n")


def _serialize_event(event: StreamEvent) -> dict[str, Any]:
    if isinstance(event, AssistantTextDelta):
        return {"type": "assistant_delta", "text": event.text}
    if isinstance(event, AssistantTurnComplete):
        return {"type": "assistant_complete",
                "message": event.message.model_dump(mode="json"),
                "usage": event.usage.model_dump(mode="json")}
    if isinstance(event, ToolExecutionStarted):
        return {"type": "tool_started", "tool_name": event.tool_name, "tool_input": event.tool_input}
    if isinstance(event, ToolExecutionCompleted):
        return {"type": "tool_completed", "tool_name": event.tool_name,
                "output": event.output, "is_error": event.is_error}
    return {"type": "unknown"}
