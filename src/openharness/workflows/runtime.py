"""Workflow runtime built on top of the existing agent and swarm layers."""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any

from openharness.agents.factory import AgentFactory
from openharness.agents.contracts import TaskDefinition
from openharness.coordinator.agent_definitions import get_agent_definition
from openharness.observability import NullTraceObserver, TraceObserver
from openharness.runtime.workflow import Workflow as AgentWorkflow
from openharness.swarm.mailbox import MailboxMessage, TeammateMailbox
from openharness.swarm.registry import get_backend_registry
from openharness.swarm.types import TeammateMessage, TeammateSpawnConfig
from openharness.swarm.worktree import WorktreeManager
from openharness.workspace import LocalWorkspace
from openharness.workflows.contracts import (
    RoleRunResult,
    WorkerHandle,
    WorkflowMessage,
)
import logging

log = logging.getLogger(__name__)


def _slugify(value: str) -> str:
    collapsed = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return collapsed or "workflow"


def _flatten_slug(value: str) -> str:
    return value.replace("/", "+")


class WorkflowRuntime:
    """Execution and coordination facade for workflow topologies."""

    def __init__(
        self,
        *,
        workspace_root: str | Path,
        spec,
        trace_observer: TraceObserver | None = None,
        api_client: Any | None = None,
    ) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self.spec = spec
        self.trace_observer = trace_observer or NullTraceObserver()
        self.api_client = api_client
        self.team_name = f"{_slugify(spec.name)}-{uuid.uuid4().hex[:8]}"
        self.leader_agent_id = f"leader@{self.team_name}"
        self._backend_registry = get_backend_registry()
        self._worktree_manager = WorktreeManager()
        self._role_cwds: dict[str, Path] = {}
        self._created_worktree_slugs: set[str] = set()
        self.worker_handles: dict[str, WorkerHandle] = {}

    async def start(self) -> None:
        """Initialize role-neutral workflow state."""
        log.info(f"Starting workflow runtime for team '{self.team_name}'")
        leader_mailbox = TeammateMailbox(team_name=self.team_name, agent_id="leader")
        await leader_mailbox.clear()

    async def close(self) -> None:
        """Shut down workers and clean up workflow-owned worktrees."""
        log.info(f"Closing workflow runtime for team '{self.team_name}'")
        for worker in self.worker_handles.values():
            log.info(f"Shutting down worker '{worker.role}' ({worker.agent_id})...")
            executor = self._backend_registry.get_executor(worker.backend_type)
            await executor.shutdown(worker.agent_id)

        if self.spec.coordination.lifecycle.cleanup_worktrees:
            for slug in sorted(self._created_worktree_slugs, reverse=True):
                log.info(f"Removing worktree '{slug}'...")
                await self._worktree_manager.remove_worktree(slug)

    async def run_inline(self, role: str, task: TaskDefinition) -> RoleRunResult:
        """Run one inline role to completion."""
        log.info(f"Running inline role '{role}'...")
        role_spec = self._get_role(role)
        if role_spec.mode != "inline":
            raise ValueError(f"Role {role!r} is not configured for inline execution")

        role_cwd = await self._resolve_role_cwd(role)
        workspace = LocalWorkspace(role_cwd)
        factory = AgentFactory.with_catalog_configs(role_cwd)
        agent_name = self._resolve_agent_config_name(role_spec.agent)
        workflow = AgentWorkflow(workspace, agent_factory=factory)
        role_task = self._build_role_task(role, task)

        if self.spec.coordination.observability.emit_role_spans:
            with self.trace_observer.span(
                name=f"workflow_role:{role}",
                input={"instruction": role_task.instruction, "payload": role_task.payload},
                metadata={
                    "workflow": self.spec.name,
                    "mode": role_spec.mode,
                    "run_id": getattr(self.trace_observer, "run_id", None),
                },
            ):
                result = await workflow.run(
                    role_task,
                    agent_name=agent_name,
                    api_client=self.api_client,
                    trace_observer=self.trace_observer,
                )
        else:
            result = await workflow.run(
                role_task,
                agent_name=agent_name,
                api_client=self.api_client,
                trace_observer=self.trace_observer,
            )

        return RoleRunResult(
            role=role,
            final_text=result.agent_result.final_text,
            input_tokens=result.agent_result.input_tokens,
            output_tokens=result.agent_result.output_tokens,
            workspace_cwd=str(role_cwd),
        )

    async def spawn(self, role: str, task: TaskDefinition | None = None) -> WorkerHandle:
        """Spawn one persistent worker role."""
        log.info(f"Spawning worker role '{role}'...")
        role_spec = self._get_role(role)
        if role_spec.mode != "spawned":
            raise ValueError(f"Role {role!r} is not configured for spawned execution")

        role_cwd = await self._resolve_role_cwd(role)
        agent_def = self._resolve_agent_definition(role_spec.agent)
        executor = self._resolve_executor(role_spec.backend)

        base_task = task or TaskDefinition(instruction=role_spec.bootstrap_task or "")
        role_task = self._build_role_task(role, base_task)
        mailbox_name = role_spec.spawn_as or role
        spawn_config = TeammateSpawnConfig(
            name=mailbox_name,
            team=self.team_name,
            prompt=role_task.instruction,
            cwd=str(self.workspace_root),
            worktree_path=str(role_cwd) if role_spec.isolation == "worktree" else None,
            parent_session_id="leader",
            description=role_spec.description or role_task.instruction[:80],
            model=role_spec.model or agent_def.model,
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
            allowed_tools=(
                list(role_spec.allowed_tools)
                if role_spec.allowed_tools is not None
                else (agent_def.tools if agent_def.tools != ["*"] else None)
            ),
            disallowed_tools=(
                list(role_spec.disallowed_tools)
                if role_spec.disallowed_tools is not None
                else agent_def.disallowed_tools
            ),
            initial_prompt=agent_def.initial_prompt,
            max_turns=role_spec.max_turns or agent_def.max_turns,
            run_id=getattr(self.trace_observer, "run_id", None),
            task_payload=role_task.payload,
        )
        spawn_result = await executor.spawn(spawn_config)
        if not spawn_result.success:
            raise RuntimeError(spawn_result.error or f"Failed to spawn role {role!r}")

        handle = WorkerHandle(
            role=role,
            agent_id=spawn_result.agent_id,
            task_id=spawn_result.task_id,
            backend_type=spawn_result.backend_type,
            team_name=self.team_name,
            mailbox_name=mailbox_name,
            worktree_path=(str(role_cwd) if role_spec.isolation == "worktree" else None),
        )
        self.worker_handles[role] = handle
        return handle

    async def send(self, worker: WorkerHandle | str, message: str) -> None:
        """Send a follow-up message to a spawned worker."""
        handle = self._resolve_worker_handle(worker)
        log.info(f"Sending message to worker '{handle.role}' ({handle.agent_id})...")
        executor = self._backend_registry.get_executor(handle.backend_type)
        await executor.send_message(
            handle.agent_id,
            TeammateMessage(text=message, from_agent="leader"),
        )

    async def read_mailbox(
        self,
        target: str = "leader",
        *,
        unread_only: bool = True,
        mark_read: bool = True,
        limit: int = 50,
    ) -> list[WorkflowMessage]:
        """Read messages from one workflow mailbox."""
        team_name = self.team_name
        mailbox_name = target
        if "@" in target:
            mailbox_name, team_name = target.split("@", 1)
        elif target in self.worker_handles:
            mailbox_name = self.worker_handles[target].mailbox_name

        mailbox = TeammateMailbox(team_name=team_name, agent_id=mailbox_name)
        raw_messages = await mailbox.read_all(unread_only=unread_only)
        selected = raw_messages[:limit]
        if mark_read:
            for msg in selected:
                await mailbox.mark_read(msg.id)
        return [self._normalize_message(message) for message in selected]

    def workflow_context(self, role: str) -> dict[str, Any]:
        """Return common workflow context injected into each role task."""
        role_spec = self._get_role(role)
        return {
            "workflow_name": self.spec.name,
            "workflow_topology": self.spec.topology,
            "team_name": self.team_name,
            "leader_agent_id": self.leader_agent_id,
            "role_name": role,
            "role_mode": role_spec.mode,
            "worker_handles": {
                name: handle.agent_id for name, handle in self.worker_handles.items()
            },
            "run_id": getattr(self.trace_observer, "run_id", None),
            "trace_id": self.trace_observer.trace_id,
            "routing": {
                name: policy.model_dump(mode="json") for name, policy in self.spec.routing.items()
            },
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_role(self, role: str):
        try:
            return self.spec.roles[role]
        except KeyError as exc:
            raise KeyError(f"Unknown workflow role {role!r}") from exc

    def _resolve_agent_definition(self, agent_ref: str):
        agent_def = get_agent_definition(agent_ref, cwd=str(self.workspace_root))
        if agent_def is None:
            raise KeyError(f"Unknown agent reference {agent_ref!r}")
        return agent_def

    def _resolve_agent_config_name(self, agent_ref: str) -> str:
        agent_def = self._resolve_agent_definition(agent_ref)
        return agent_def.agent_config_name or agent_def.name

    def _resolve_executor(self, backend: str):
        if backend == "in_process":
            return self._backend_registry.get_executor("in_process")
        if backend == "subprocess":
            return self._backend_registry.get_executor("subprocess")
        try:
            return self._backend_registry.get_executor("in_process")
        except KeyError:
            return self._backend_registry.get_executor()

    async def _resolve_role_cwd(self, role: str) -> Path:
        cached = self._role_cwds.get(role)
        if cached is not None:
            return cached

        role_spec = self._get_role(role)
        if role_spec.isolation == "shared":
            self._role_cwds[role] = self.workspace_root
            return self.workspace_root

        slug = f"{_slugify(self.spec.name)}/{_slugify(role)}"
        flat_slug = _flatten_slug(slug)
        if not (self._worktree_manager.base_dir / flat_slug).exists():
            self._created_worktree_slugs.add(slug)
        info = await self._worktree_manager.create_worktree(
            repo_path=self.workspace_root,
            slug=slug,
            agent_id=role,
        )
        self._role_cwds[role] = info.path
        return info.path

    def _build_role_task(self, role: str, task: TaskDefinition) -> TaskDefinition:
        role_spec = self._get_role(role)
        instruction = self._combine_prompt(role_spec, task.instruction)
        payload = {
            **task.payload,
            **role_spec.initial_payload,
            "workflow_context": self.workflow_context(role),
        }
        return TaskDefinition(instruction=instruction, payload=payload)

    def _combine_prompt(self, role_spec, instruction: str) -> str:
        if not role_spec.prompt_prefix:
            return instruction
        return f"{role_spec.prompt_prefix.strip()}\n\n{instruction.strip()}".strip()

    def _resolve_worker_handle(self, worker: WorkerHandle | str) -> WorkerHandle:
        if isinstance(worker, WorkerHandle):
            return worker
        try:
            return self.worker_handles[worker]
        except KeyError as exc:
            raise KeyError(f"Unknown worker role {worker!r}") from exc

    def _normalize_message(self, message: MailboxMessage) -> WorkflowMessage:
        text = self._message_text(message)
        return WorkflowMessage(
            id=message.id,
            type=message.type,
            sender=message.sender,
            recipient=message.recipient,
            text=text,
            timestamp=message.timestamp,
            payload=message.payload,
        )

    def _message_text(self, message: MailboxMessage) -> str:
        payload = message.payload
        if "content" in payload:
            return str(payload.get("content", ""))
        if "summary" in payload and payload.get("summary"):
            return str(payload["summary"])
        raw_text = payload.get("text")
        if not raw_text:
            return json.dumps(payload, sort_keys=True)
        try:
            parsed = json.loads(raw_text)
        except (json.JSONDecodeError, TypeError):
            return str(raw_text)
        if isinstance(parsed, dict):
            if "content" in parsed:
                return str(parsed["content"])
            if "summary" in parsed:
                return str(parsed["summary"])
        return json.dumps(parsed, sort_keys=True)
