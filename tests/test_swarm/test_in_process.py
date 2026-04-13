"""Tests for InProcessBackend: spawn, shutdown, send_message, and contextvars."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from openharness.observability import NullTraceObserver
from openharness.swarm.in_process import (
    InProcessBackend,
    TeammateContext,
    get_teammate_context,
    set_teammate_context,
)
from openharness.swarm.mailbox import TeammateMailbox
from openharness.swarm.runner import TeammateTurnResult
from openharness.swarm.types import TeammateMessage, TeammateSpawnConfig


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def spawn_config():
    return TeammateSpawnConfig(
        name="worker",
        team="test-team",
        prompt="hello",
        cwd="/tmp",
        parent_session_id="sess-001",
    )


@pytest.fixture
def backend(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    async def _fake_create_runner(config):
        del config
        return _FakeRunner("")

    monkeypatch.setattr(
        "openharness.swarm.in_process.create_teammate_runner",
        _fake_create_runner,
    )
    return InProcessBackend()


# ---------------------------------------------------------------------------
# TeammateContext
# ---------------------------------------------------------------------------


def test_teammate_context_defaults():
    ctx = TeammateContext(
        agent_id="w@t",
        agent_name="w",
        team_name="t",
    )
    assert ctx.color is None
    assert ctx.plan_mode_required is False
    assert not ctx.cancel_event.is_set()


# ---------------------------------------------------------------------------
# ContextVar get / set
# ---------------------------------------------------------------------------


def test_get_teammate_context_returns_none_outside_task():
    # Outside any async task, the contextvar should be None
    result = get_teammate_context()
    assert result is None


async def test_set_and_get_teammate_context():
    ctx = TeammateContext(agent_id="x@y", agent_name="x", team_name="y")
    set_teammate_context(ctx)
    assert get_teammate_context() is ctx


# ---------------------------------------------------------------------------
# InProcessBackend.spawn
# ---------------------------------------------------------------------------


async def test_spawn_returns_success_result(backend, spawn_config):
    result = await backend.spawn(spawn_config)
    assert result.success is True
    assert result.agent_id == "worker@test-team"
    assert result.backend_type == "in_process"
    assert result.task_id.startswith("in_process_")


async def test_spawn_duplicate_returns_failure(backend, spawn_config):
    await backend.spawn(spawn_config)
    # Spawn again while first is still running
    result = await backend.spawn(spawn_config)
    assert result.success is False
    assert result.error is not None


async def test_spawn_creates_active_agent(backend, spawn_config):
    await backend.spawn(spawn_config)
    assert backend.is_active("worker@test-team")


# ---------------------------------------------------------------------------
# InProcessBackend.shutdown
# ---------------------------------------------------------------------------


async def test_shutdown_unknown_agent_returns_false(backend):
    result = await backend.shutdown("nonexistent@team")
    assert result is False


async def test_graceful_shutdown(backend, spawn_config):
    await backend.spawn(spawn_config)
    assert backend.is_active("worker@test-team")

    result = await backend.shutdown("worker@test-team", timeout=2.0)
    assert result is True
    assert not backend.is_active("worker@test-team")


async def test_force_shutdown(backend, spawn_config):
    await backend.spawn(spawn_config)
    result = await backend.shutdown("worker@test-team", force=True, timeout=2.0)
    assert result is True


# ---------------------------------------------------------------------------
# InProcessBackend.send_message
# ---------------------------------------------------------------------------


async def test_send_message_writes_to_mailbox(backend, tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    config = TeammateSpawnConfig(
        name="rcvr",
        team="myteam",
        prompt="wait",
        cwd="/tmp",
        parent_session_id="s",
    )
    await backend.spawn(config)

    msg = TeammateMessage(
        text="work on it",
        from_agent="leader",
        message_id="msg-123",
        correlation_id="corr-123",
        reply_to="msg-parent",
    )
    # Should not raise
    await backend.send_message("rcvr@myteam", msg)

    # Verify the message was written to mailbox
    from openharness.swarm.mailbox import TeammateMailbox
    mailbox = TeammateMailbox(team_name="myteam", agent_id="rcvr")
    messages = await mailbox.read_all(unread_only=False)
    assert any(m.payload.get("content") == "work on it" for m in messages)
    assert any(m.id == "msg-123" for m in messages)
    assert any(m.correlation_id == "corr-123" for m in messages)
    assert any(m.reply_to == "msg-parent" for m in messages)

    await backend.shutdown("rcvr@myteam", force=True)


async def test_send_message_invalid_agent_id_raises(backend):
    with pytest.raises(ValueError, match="agentName@teamName"):
        await backend.send_message("no-at-sign", TeammateMessage(text="hi", from_agent="l"))


class _FakeRunner:
    def __init__(self, reply_text: str) -> None:
        self.reply_text = reply_text
        self.trace_observer = NullTraceObserver()

    async def run_turn(self, message: str) -> TeammateTurnResult:
        del message
        return TeammateTurnResult(text=self.reply_text)

    async def close(self) -> None:
        return None


async def test_worker_auto_forwards_final_text_when_no_explicit_reply(
    backend,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    async def _fake_create_runner(config):
        del config
        return _FakeRunner("Repair complete.")

    monkeypatch.setattr(
        "openharness.swarm.in_process.create_teammate_runner",
        _fake_create_runner,
    )

    config = TeammateSpawnConfig(
        name="worker",
        team="myteam",
        prompt="",
        cwd="/tmp",
        parent_session_id="leader",
    )
    await backend.spawn(config)

    await backend.send_message(
        "worker@myteam",
        TeammateMessage(
            text="Please repair the bug.",
            from_agent="leader",
            message_id="msg-in",
            correlation_id="corr-in",
        ),
    )

    leader_mailbox = TeammateMailbox(team_name="myteam", agent_id="leader")
    forwarded = None
    for _ in range(20):
        messages = await leader_mailbox.read_all(unread_only=False)
        forwarded = next(
            (message for message in messages if message.reply_to == "msg-in"),
            None,
        )
        if forwarded is not None:
            break
        await asyncio.sleep(0.05)

    assert forwarded is not None
    assert forwarded.sender == "worker@myteam"
    assert forwarded.payload["content"] == "Repair complete."
    assert forwarded.payload["auto_forwarded"] is True
    assert forwarded.correlation_id == "corr-in"
    assert forwarded.reply_to == "msg-in"

    await backend.shutdown("worker@myteam", force=True)


# ---------------------------------------------------------------------------
# active_agents / shutdown_all
# ---------------------------------------------------------------------------


async def test_active_agents_lists_running(backend, spawn_config):
    await backend.spawn(spawn_config)
    active = backend.active_agents()
    assert "worker@test-team" in active


async def test_shutdown_all(backend, tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    for name in ("a", "b"):
        cfg = TeammateSpawnConfig(
            name=name,
            team="t",
            prompt="run",
            cwd="/tmp",
            parent_session_id="s",
        )
        await backend.spawn(cfg)

    await backend.shutdown_all(force=True, timeout=2.0)
    assert backend.active_agents() == []
