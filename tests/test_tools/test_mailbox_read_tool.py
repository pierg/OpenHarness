"""Tests for the mailbox_read tool."""

from __future__ import annotations

from pathlib import Path

from openharness.swarm.mailbox import TeammateMailbox, create_user_message
from openharness.tools.base import ToolExecutionContext
from openharness.tools.mailbox_read_tool import MailboxReadTool, MailboxReadToolInput


async def test_mailbox_read_reads_and_marks_messages(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    mailbox = TeammateMailbox(team_name="team-x", agent_id="leader")
    await mailbox.write(create_user_message("worker", "leader", "done"))

    tool = MailboxReadTool()
    result = await tool.execute(
        MailboxReadToolInput(agent_id="leader@team-x", unread_only=True, mark_read=True),
        ToolExecutionContext(cwd=tmp_path),
    )

    assert '"content": "done"' in result.output
    unread = await mailbox.read_all(unread_only=True)
    assert unread == []


async def test_mailbox_read_requires_team_for_bare_agent_id(tmp_path) -> None:
    tool = MailboxReadTool()
    result = await tool.execute(
        MailboxReadToolInput(agent_id="leader"),
        ToolExecutionContext(cwd=tmp_path),
    )
    assert result.is_error is True
    assert "team is required" in result.output
