"""Tool for reading swarm workflow mailbox messages."""

from __future__ import annotations

import json
import os

from pydantic import BaseModel, Field

from openharness.swarm.mailbox import TeammateMailbox
from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult


class MailboxReadToolInput(BaseModel):
    """Arguments for reading a mailbox."""

    agent_id: str = Field(
        description="Mailbox owner in 'agent@team' form, or just the agent name when team is set",
    )
    team: str | None = Field(
        default=None,
        description="Optional team name override when agent_id does not include '@team'",
    )
    unread_only: bool = Field(default=True)
    mark_read: bool = Field(default=True)
    limit: int = Field(default=20, ge=1, le=200)


class MailboxReadTool(BaseTool):
    """Read messages from a swarm mailbox."""

    name = "mailbox_read"
    description = "Read messages from a swarm mailbox."
    input_model = MailboxReadToolInput

    async def execute(
        self,
        arguments: MailboxReadToolInput,
        context: ToolExecutionContext,
    ) -> ToolResult:
        del context
        try:
            mailbox_name, team_name = self._resolve_mailbox(arguments.agent_id, arguments.team)
        except ValueError as exc:
            return ToolResult(output=str(exc), is_error=True)

        mailbox = TeammateMailbox(team_name=team_name, agent_id=mailbox_name)
        messages = await mailbox.read_all(unread_only=arguments.unread_only)
        selected = messages[: arguments.limit]
        if arguments.mark_read:
            for message in selected:
                await mailbox.mark_read(message.id)

        payload = [
            {
                "id": message.id,
                "type": message.type,
                "sender": message.sender,
                "recipient": message.recipient,
                "timestamp": message.timestamp,
                "read": message.read,
                "payload": message.payload,
            }
            for message in selected
        ]
        return ToolResult(output=json.dumps(payload, indent=2, sort_keys=True))

    def _resolve_mailbox(self, agent_id: str, team: str | None) -> tuple[str, str]:
        if "@" in agent_id:
            mailbox_name, team_name = agent_id.split("@", 1)
            return mailbox_name, team_name

        team_name = team or os.environ.get("CLAUDE_CODE_TEAM_NAME")
        if not team_name:
            raise ValueError(
                "Mailbox team is required when agent_id is not in 'agent@team' form"
            )
        return agent_id, team_name
