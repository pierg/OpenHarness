"""Clean Python primitives for orchestrating agent teams.

Provides a `TeamOrchestrator` context manager to spawn workers, execute
coordinator agents, and cleanly manage mailboxes and backend lifecycles.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from openharness.agents.contracts import TaskDefinition
from openharness.agents.factory import AgentFactory
from openharness.coordinator.agent_definitions import AgentDefinition
from openharness.observability import NullTraceObserver, TraceObserver
from openharness.runtime.workflow import Workflow as AgentWorkflow
from openharness.swarm.mailbox import MailboxMessage, TeammateMailbox
from openharness.swarm.registry import get_backend_registry
from openharness.swarm.types import TeammateMessage, TeammateSpawnConfig
from openharness.workspace import LocalWorkspace


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _preview_text(text: str, *, limit: int = 120) -> str:
    rendered = " ".join(str(text).split())
    if len(rendered) <= limit:
        return rendered
    return f"{rendered[: max(0, limit - 3)].rstrip()}..."


class TeamOrchestrator:
    """Manages the lifecycle, mailboxes, and execution of a team of agents."""

    def __init__(
        self,
        team_name: str,
        workspace_dir: Path,
        backend_name: str = "in_process",
        *,
        trace_observer: TraceObserver | None = None,
    ) -> None:
        self.team_name = team_name
        self.workspace_dir = workspace_dir
        self.backend = get_backend_registry().get_executor(backend_name)
        self.leader_mailbox = TeammateMailbox(team_name=team_name, agent_id="leader")
        self.trace_observer = trace_observer or NullTraceObserver()
        self.workers: dict[str, str] = {}  # Maps role_name to agent_id

    async def __aenter__(self) -> "TeamOrchestrator":
        """Initialize the team state."""
        await self.leader_mailbox.clear()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Clean up all worker resources."""
        await self.shutdown_all()

    async def spawn_worker(
        self,
        role_name: str,
        agent_def: AgentDefinition,
        bootstrap_task: str,
        payload: dict[str, Any] | None = None,
    ) -> str:
        """Spawn a persistent worker and add it to the team."""
        config = TeammateSpawnConfig(
            name=role_name,
            team=self.team_name,
            prompt=bootstrap_task,
            cwd=str(self.workspace_dir),
            parent_session_id="leader",
            description=bootstrap_task.strip()[:80],
            model=agent_def.model,
            system_prompt=agent_def.system_prompt,
            system_prompt_mode=agent_def.system_prompt_mode,
            color=agent_def.color,
            permissions=agent_def.permissions,
            plan_mode_required=agent_def.plan_mode_required,
            allow_permission_prompts=agent_def.allow_permission_prompts,
            runner=agent_def.runner,
            agent_config_name=agent_def.agent_config_name,
            agent_architecture=agent_def.agent_architecture,
            permission_mode=agent_def.permission_mode,
            allowed_tools=agent_def.tools if agent_def.tools not in (None, ["*"]) else None,
            disallowed_tools=agent_def.disallowed_tools,
            initial_prompt=agent_def.initial_prompt,
            max_turns=agent_def.max_turns,
            run_id=getattr(self.trace_observer, "run_id", None),
            task_payload=payload or {},
        )
        result = await self.backend.spawn(config)
        if not result.success:
            raise RuntimeError(result.error or f"Failed to spawn {role_name!r}")

        self.workers[role_name] = result.agent_id
        return result.agent_id

    async def send(self, role_name: str, message: str) -> None:
        """Send a message to a spawned worker."""
        agent_id = self.workers.get(role_name)
        if not agent_id:
            raise ValueError(f"Worker role {role_name!r} not found in this team.")
        correlation_id = f"leader-{time.time_ns()}"
        await self.backend.send_message(
            agent_id,
            TeammateMessage(
                text=message,
                from_agent="leader",
                correlation_id=correlation_id,
                summary=_preview_text(message),
            ),
        )

    async def wait_for_updates(
        self,
        target_roles: list[str],
        timeout: float,
        poll_interval: float = 0.1,
        *,
        mark_read: bool = True,
    ) -> list[MailboxMessage]:
        """Block until each target role has sent at least one update to the leader."""
        pending = set(target_roles)
        observed: list[MailboxMessage] = []
        deadline = time.monotonic() + timeout
        seen_message_ids: set[str] = set()
        while pending:
            batch = await self.leader_mailbox.read_all(unread_only=True)
            new_messages = [msg for msg in batch if msg.id not in seen_message_ids]
            if new_messages:
                if mark_read:
                    for msg in new_messages:
                        await self.leader_mailbox.mark_read(msg.id)

                observed.extend(new_messages)
                seen_message_ids.update(msg.id for msg in new_messages)
                for message in new_messages:
                    if message.sender == "leader":
                        role_name = "leader"
                    else:
                        role_name = self._role_for_sender(message.sender)
                        
                    if role_name and role_name in pending:
                        pending.remove(role_name)
                    # Support legacy agent_id match as fallback
                    elif message.sender in pending:
                        pending.remove(message.sender)
                if not pending:
                    break
            
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"Timed out waiting for worker updates from {', '.join(sorted(pending))}"
                )
            await asyncio.sleep(poll_interval)

        return observed

    async def read_mailbox(self, unread_only: bool = False) -> list[MailboxMessage]:
        """Read all messages from the leader mailbox."""
        return await self.leader_mailbox.read_all(unread_only=unread_only)

    async def read_all_mailboxes(self) -> list[MailboxMessage]:
        """Read all messages from all mailboxes in the team, sorted by timestamp."""
        all_messages: list[MailboxMessage] = []
        
        # Read the leader's mailbox
        all_messages.extend(await self.leader_mailbox.read_all(unread_only=False))
        
        # Read all workers' mailboxes
        for agent_id in self.workers.values():
            from openharness.swarm.mailbox import TeammateMailbox
            worker_mailbox = TeammateMailbox(self.team_name, agent_id)
            all_messages.extend(await worker_mailbox.read_all(unread_only=False))
            
        all_messages.sort(key=lambda m: m.timestamp)
        return all_messages

    async def run_inline(
        self,
        agent_def: AgentDefinition,
        instruction: str,
        identity: str,
        payload: dict[str, Any] | None = None,
        api_client: Any | None = None,
    ) -> Any:
        """Execute a coordinator or single-run agent inline within the team environment.
        
        Args:
            agent_def: The definition of the agent to execute inline.
            instruction: The initial query/prompt to start the agent with.
            identity: The identity string this inline agent will use when sending 
                      mailbox messages.
            payload: Optional variables rendered into the prompt payload.
            api_client: Optional API client override.
        """
        from openharness.swarm.in_process import TeammateContext, get_teammate_context, set_teammate_context
        
        previous_context = get_teammate_context()
        ctx = TeammateContext(
            agent_id=identity,
            agent_name=identity.split("@")[0] if "@" in identity else identity,
            team_name=self.team_name,
        )
        set_teammate_context(ctx)
        
        try:
            workspace = LocalWorkspace(self.workspace_dir)
            factory = AgentFactory.with_catalog_configs(self.workspace_dir)
            workflow = AgentWorkflow(workspace, agent_factory=factory)
            return await workflow.run(
                TaskDefinition(
                    instruction=instruction,
                    payload=payload or {},
                ),
                agent_name=agent_def.agent_config_name or agent_def.name,
                api_client=api_client,
                trace_observer=self.trace_observer,
            )
        finally:
            if previous_context is not None:
                set_teammate_context(previous_context)
            else:
                from openharness.swarm.in_process import _teammate_context_var
                _teammate_context_var.set(None)

    async def shutdown_all(self) -> None:
        """Explicitly shut down all spawned workers in the team."""
        for agent_id in self.workers.values():
            await self.backend.shutdown(agent_id)
        self.workers.clear()

    def _role_for_sender(self, sender_id: str) -> str | None:
        for role, agent_id in self.workers.items():
            if agent_id == sender_id:
                return role
        return None
