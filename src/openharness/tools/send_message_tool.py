"""Tool for writing messages to running agent tasks."""

from __future__ import annotations

import logging
import os
from uuid import uuid4

from pydantic import BaseModel, Field

from openharness.swarm.registry import get_backend_registry
from openharness.swarm.types import TeammateMessage
from openharness.tasks.manager import get_task_manager
from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult

logger = logging.getLogger(__name__)


def _preview_text(text: str, *, limit: int = 120) -> str:
    rendered = " ".join(str(text).split())
    if len(rendered) <= limit:
        return rendered
    return f"{rendered[: max(0, limit - 3)].rstrip()}..."


def _resolve_sender_agent_id() -> str | None:
    """Prefer the current worker identity when available.
    
    If no explicit agent ID is set via environment variable (CLAUDE_CODE_AGENT_ID),
    and no teammate context exists (e.g. running inline), return None to allow
    callers to define the sender identity themselves.
    """
    agent_id = os.environ.get("CLAUDE_CODE_AGENT_ID")
    if agent_id:
        return agent_id

    from openharness.swarm.in_process import get_teammate_context
    
    context = get_teammate_context()
    if context is None:
        return None
    return context.agent_id


def _resolve_active_thread() -> tuple[str | None, str | None]:
    """Return ``(reply_to, correlation_id)`` for the current worker turn."""
    from openharness.swarm.in_process import get_teammate_context
    
    context = get_teammate_context()
    if context is None:
        return None, None
    return context.current_message_id, context.current_correlation_id


def _mark_explicit_reply_sent() -> None:
    """Mark the current in-process teammate turn as explicitly replied."""
    from openharness.swarm.in_process import get_teammate_context
    
    context = get_teammate_context()
    if context is None:
        return
    context.explicit_reply_sent = True


class SendMessageToolInput(BaseModel):
    """Arguments for sending a follow-up message to a task."""

    task_id: str = Field(description="Target local agent task id or swarm agent_id (name@team)")
    message: str = Field(description="Message to write to the task stdin")


class SendMessageTool(BaseTool):
    """Send a message to a running local agent task."""

    name = "send_message"
    description = "Send a follow-up message to a running local agent task."
    input_model = SendMessageToolInput

    async def execute(self, arguments: SendMessageToolInput, context: ToolExecutionContext) -> ToolResult:
        del context
        sender_agent_id = _resolve_sender_agent_id()
        if not sender_agent_id:
            raise RuntimeError(
                "Cannot resolve sender agent ID. CLAUDE_CODE_AGENT_ID environment "
                "variable is missing and no teammate context is active."
            )

        reply_to, inherited_correlation_id = _resolve_active_thread()
        message_id = uuid4().hex
        correlation_id = inherited_correlation_id or message_id
        message_preview = _preview_text(arguments.message)
        # Swarm agents use agent_id format (name@team); legacy tasks use plain task IDs
        if "@" in arguments.task_id:
            return await self._send_swarm_message(
                arguments.task_id,
                arguments.message,
                sender_agent_id=sender_agent_id,
                message_id=message_id,
                correlation_id=correlation_id,
                reply_to=reply_to,
                summary=message_preview,
            )
        try:
            await get_task_manager().write_to_task(arguments.task_id, arguments.message)
        except ValueError as exc:
            return ToolResult(output=str(exc), is_error=True)
        return ToolResult(
            output=f"Sent message to task {arguments.task_id}",
            metadata={
                "delivery_target": arguments.task_id,
                "sender_agent_id": sender_agent_id,
                "message_preview": message_preview,
            },
        )

    async def _send_swarm_message(
        self,
        agent_id: str,
        message: str,
        *,
        sender_agent_id: str,
        message_id: str,
        correlation_id: str,
        reply_to: str | None,
        summary: str,
    ) -> ToolResult:
        """Route a message to a swarm agent via the backend."""
        registry = get_backend_registry()
        # Prefer in_process backend for mailbox-based delivery
        try:
            executor = registry.get_executor("in_process")
        except KeyError:
            executor = registry.get_executor("subprocess")

        teammate_msg = TeammateMessage(
            text=message,
            from_agent=sender_agent_id,
            message_id=message_id,
            correlation_id=correlation_id,
            reply_to=reply_to,
            summary=summary,
        )
        try:
            await executor.send_message(agent_id, teammate_msg)
        except ValueError as exc:
            return ToolResult(output=str(exc), is_error=True)
        except Exception as exc:
            logger.error("Failed to send message to %s: %s", agent_id, exc)
            return ToolResult(output=str(exc), is_error=True)
        _mark_explicit_reply_sent()
        return ToolResult(
            output=f"Sent message to agent {agent_id}",
            metadata={
                "delivery_channel": "swarm",
                "delivery_target": agent_id,
                "sender_agent_id": sender_agent_id,
                "message_id": message_id,
                "correlation_id": correlation_id,
                "reply_to": reply_to,
                "message_preview": summary,
            },
        )
