"""Subprocess-based TeammateExecutor implementation."""

from __future__ import annotations

import dataclasses
import json
import logging
import shlex
import sys
import uuid
from pathlib import Path

from openharness.swarm.spawn_utils import build_inherited_env_vars
from openharness.swarm.types import (
    BackendType,
    SpawnResult,
    TeammateMessage,
    TeammateSpawnConfig,
)
from openharness.tasks.manager import get_task_manager

logger = logging.getLogger(__name__)


class SubprocessBackend:
    """TeammateExecutor that runs each teammate as a separate subprocess."""

    type: BackendType = "subprocess"

    _agent_tasks: dict[str, str]

    def __init__(self) -> None:
        self._agent_tasks = {}
        self._agent_config_paths: dict[str, Path] = {}

    def is_available(self) -> bool:
        """Subprocess backend is always available."""
        return True

    async def spawn(self, config: TeammateSpawnConfig) -> SpawnResult:
        """Spawn a new teammate as an OpenHarness worker subprocess."""
        agent_id = f"{config.name}@{config.team}"
        extra_env = build_inherited_env_vars()
        extra_env.update(
            {
                "CLAUDE_CODE_TEAM_NAME": config.team,
                "CLAUDE_CODE_AGENT_ID": agent_id,
                "CLAUDE_CODE_AGENT_NAME": config.name,
            }
        )
        if config.color:
            extra_env["CLAUDE_CODE_AGENT_COLOR"] = config.color

        env_prefix = " ".join(f"{k}={v!r}" for k, v in extra_env.items())

        from openharness.config.paths import get_tasks_dir  # noqa: PLC0415

        config_path = get_tasks_dir() / f"teammate_{uuid.uuid4().hex}.json"
        config_path.write_text(
            json.dumps(dataclasses.asdict(config), indent=2),
            encoding="utf-8",
        )

        cmd_parts = [
            shlex.quote(sys.executable),
            "-m",
            "openharness.swarm.worker",
            "--config",
            shlex.quote(str(config_path)),
        ]
        command = f"{env_prefix} {' '.join(cmd_parts)}" if env_prefix else " ".join(cmd_parts)

        manager = get_task_manager()
        try:
            record = await manager.create_agent_task(
                prompt=config.prompt,
                description=f"Teammate: {agent_id}",
                cwd=config.cwd,
                task_type="in_process_teammate",
                model=config.model,
                command=command,
            )
        except Exception as exc:
            logger.error("Failed to spawn teammate %s: %s", agent_id, exc)
            return SpawnResult(
                task_id="",
                agent_id=agent_id,
                backend_type=self.type,
                success=False,
                error=str(exc),
            )

        self._agent_tasks[agent_id] = record.id
        self._agent_config_paths[agent_id] = config_path
        logger.debug("Spawned teammate %s as task %s", agent_id, record.id)
        return SpawnResult(
            task_id=record.id,
            agent_id=agent_id,
            backend_type=self.type,
        )

    async def send_message(self, agent_id: str, message: TeammateMessage) -> None:
        """Send a message to a running teammate via its stdin pipe."""
        task_id = self._agent_tasks.get(agent_id)
        if task_id is None:
            raise ValueError(f"No active subprocess for agent {agent_id!r}")

        payload = {
            "text": message.text,
            "from": message.from_agent,
            "message_id": message.message_id,
            "correlation_id": message.correlation_id,
            "reply_to": message.reply_to,
            "timestamp": message.timestamp,
        }
        if message.color:
            payload["color"] = message.color
        if message.summary:
            payload["summary"] = message.summary

        manager = get_task_manager()
        await manager.write_to_task(task_id, json.dumps(payload))
        logger.debug("Sent message to %s (task %s)", agent_id, task_id)

    async def shutdown(self, agent_id: str, *, force: bool = False) -> bool:
        """Terminate a subprocess teammate."""
        del force
        task_id = self._agent_tasks.get(agent_id)
        if task_id is None:
            logger.warning("shutdown() called for unknown agent %s", agent_id)
            return False

        manager = get_task_manager()
        try:
            await manager.stop_task(task_id)
        except ValueError as exc:
            logger.debug("stop_task for %s: %s", task_id, exc)
        finally:
            self._agent_tasks.pop(agent_id, None)
            config_path = self._agent_config_paths.pop(agent_id, None)
            if config_path is not None:
                try:
                    config_path.unlink(missing_ok=True)
                except OSError:
                    logger.debug(
                        "Unable to remove teammate config %s",
                        config_path,
                        exc_info=True,
                    )

        logger.debug("Shut down teammate %s (task %s)", agent_id, task_id)
        return True

    def get_task_id(self, agent_id: str) -> str | None:
        """Return the task manager task ID for a given agent, if known."""
        return self._agent_tasks.get(agent_id)
