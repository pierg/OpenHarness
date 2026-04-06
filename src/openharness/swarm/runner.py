"""Shared teammate runner implementations for swarm backends."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from openharness.agents.contracts import TaskDefinition
from openharness.agents.factory import AgentFactory
from openharness.api.provider import detect_provider
from openharness.config import load_settings
from openharness.engine.stream_events import AssistantTextDelta, AssistantTurnComplete
from openharness.observability import TraceObserver, create_trace_observer
from openharness.permissions.modes import PermissionMode
from openharness.runtime.workflow import Workflow
from openharness.swarm.types import TeammateSpawnConfig
from openharness.ui.runtime import RuntimeBundle, build_runtime, close_runtime, start_runtime
from openharness.workspace import LocalWorkspace


@dataclass(frozen=True)
class TeammateTurnResult:
    """Normalized result for one teammate turn."""

    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


class SupportsTeammateTurns(Protocol):
    """A stateful teammate runner."""

    async def run_turn(self, message: str) -> TeammateTurnResult:
        """Execute one inbound message and return the result."""

    async def close(self) -> None:
        """Release any resources held by the runner."""


def _effective_cwd(config: TeammateSpawnConfig) -> str:
    return str(Path(config.worktree_path or config.cwd).resolve())


def _combine_initial_prompt(config: TeammateSpawnConfig, message: str, *, consume: bool) -> str:
    if not consume or not config.initial_prompt:
        return message
    return f"{config.initial_prompt.strip()}\n\n{message.strip()}".strip()


def _usage_total(snapshot: Any) -> int:
    return int(getattr(snapshot, "input_tokens", 0)) + int(getattr(snapshot, "output_tokens", 0))


def _resolve_worker_permission_mode(config: TeammateSpawnConfig) -> str:
    if config.plan_mode_required or config.permission_mode == "plan":
        return PermissionMode.PLAN.value
    if config.permission_mode == "default" and config.allow_permission_prompts:
        return PermissionMode.DEFAULT.value
    return PermissionMode.FULL_AUTO.value


def _resolve_system_prompt(bundle: RuntimeBundle, config: TeammateSpawnConfig, message: str) -> str:
    from openharness.prompts.context import build_runtime_system_prompt  # noqa: PLC0415

    settings = bundle.current_settings()
    base_prompt = build_runtime_system_prompt(
        settings,
        cwd=bundle.cwd,
        latest_user_prompt=message,
    )
    if not config.system_prompt:
        return base_prompt
    if config.system_prompt_mode == "replace":
        return config.system_prompt
    return f"{base_prompt}\n\n{config.system_prompt}".strip()


def _create_swarm_trace_observer(
    config: TeammateSpawnConfig,
    *,
    interface: str,
    model: str,
) -> TraceObserver:
    settings = load_settings().merge_cli_overrides(model=model)
    return create_trace_observer(
        session_id=config.session_id or uuid4().hex[:12],
        interface=interface,
        cwd=_effective_cwd(config),
        model=model,
        provider=detect_provider(settings).name,
    )


class PromptNativeTeammateRunner:
    """Stateful teammate runner backed by the interactive query engine."""

    def __init__(self, bundle: RuntimeBundle, config: TeammateSpawnConfig, trace_observer: TraceObserver) -> None:
        self._bundle = bundle
        self._config = config
        self._trace_observer = trace_observer
        self._initial_prompt_pending = True

    @classmethod
    async def create(cls, config: TeammateSpawnConfig) -> "PromptNativeTeammateRunner":
        allowed_tools = config.allowed_tools
        if allowed_tools is not None and "*" in allowed_tools:
            allowed_tools = None
        trace_observer = _create_swarm_trace_observer(
            config,
            interface="swarm_prompt_native",
            model=config.model or load_settings().model,
        )
        trace_observer.start_session(
            metadata={
                "runner": "prompt_native",
                "team": config.team,
                "teammate_name": config.name,
                "parent_session_id": config.parent_session_id,
            }
        )

        bundle = await build_runtime(
            cwd=_effective_cwd(config),
            model=config.model,
            max_turns=config.max_turns,
            permission_mode=_resolve_worker_permission_mode(config),
            allowed_tools=allowed_tools,
            disallowed_tools=config.disallowed_tools,
            enforce_max_turns=True,
            trace_observer=trace_observer,
        )
        await start_runtime(bundle)
        return cls(bundle, config, trace_observer)

    async def run_turn(self, message: str) -> TeammateTurnResult:
        turn_message = _combine_initial_prompt(
            self._config,
            message,
            consume=self._initial_prompt_pending,
        )
        self._initial_prompt_pending = False
        with self._trace_observer.span(
            name="turn",
            input={"message": turn_message},
            metadata={"team": self._config.team, "teammate_name": self._config.name},
        ) as turn_span:
            before = self._bundle.engine.total_usage
            before_total = _usage_total(before)
            self._bundle.engine.set_system_prompt(
                _resolve_system_prompt(self._bundle, self._config, turn_message)
            )

            collected = ""
            final_text = ""
            async for event in self._bundle.engine.submit_message(turn_message):
                if isinstance(event, AssistantTextDelta):
                    collected += event.text
                elif isinstance(event, AssistantTurnComplete):
                    final_text = event.message.text.strip()

            after = self._bundle.engine.total_usage
            delta_total = max(0, _usage_total(after) - before_total)
            delta_input = max(0, int(getattr(after, "input_tokens", 0)) - int(getattr(before, "input_tokens", 0)))
            delta_output = max(
                0,
                int(getattr(after, "output_tokens", 0)) - int(getattr(before, "output_tokens", 0)),
            )
            result = TeammateTurnResult(
                text=final_text or collected.strip(),
                input_tokens=delta_input,
                output_tokens=delta_output or max(0, delta_total - delta_input),
            )
            turn_span.update(
                output={"text": result.text},
                metadata={
                    "input_tokens": result.input_tokens,
                    "output_tokens": result.output_tokens,
                },
            )
            return result

    async def close(self) -> None:
        try:
            await close_runtime(self._bundle)
        finally:
            self._trace_observer.end_session(
                metadata={
                    "runner": "prompt_native",
                    "team": self._config.team,
                    "teammate_name": self._config.name,
                }
            )


class YamlWorkflowTeammateRunner:
    """Stateful teammate runner backed by the YAML workflow system."""

    def __init__(self, config: TeammateSpawnConfig) -> None:
        self._config = config
        self._workspace = LocalWorkspace(_effective_cwd(config))
        self._factory = AgentFactory.with_catalog_configs(self._workspace.cwd)
        self._workflow = Workflow(self._workspace, agent_factory=self._factory)
        self._agent_name = self._config.agent_config_name or self._config.name
        workflow_config = self._factory.get_config(self._agent_name)
        self._trace_observer = _create_swarm_trace_observer(
            config,
            interface="swarm_yaml_workflow",
            model=workflow_config.model,
        )
        self._trace_observer.start_session(
            metadata={
                "runner": "yaml_workflow",
                "team": config.team,
                "teammate_name": config.name,
                "agent_config_name": self._agent_name,
                "architecture": workflow_config.architecture,
            }
        )
        self._initial_prompt_pending = True
        self._history: list[dict[str, str]] = []

    async def run_turn(self, message: str) -> TeammateTurnResult:
        turn_message = _combine_initial_prompt(
            self._config,
            message,
            consume=self._initial_prompt_pending,
        )
        self._initial_prompt_pending = False
        with self._trace_observer.span(
            name="turn",
            input={"message": turn_message},
            metadata={
                "team": self._config.team,
                "teammate_name": self._config.name,
                "agent_config_name": self._agent_name,
            },
        ) as turn_span:
            task = TaskDefinition(
                instruction=turn_message,
                payload={
                    **self._config.task_payload,
                    "history": list(self._history),
                    "teammate_name": self._config.name,
                    "team_name": self._config.team,
                },
            )
            result = await self._workflow.run(
                task,
                agent_name=self._agent_name,
                trace_observer=self._trace_observer,
            )
            final_text = result.agent_result.final_text
            self._history.append({"input": turn_message, "output": final_text})
            teammate_result = TeammateTurnResult(
                text=final_text,
                input_tokens=result.agent_result.input_tokens,
                output_tokens=result.agent_result.output_tokens,
            )
            turn_span.update(
                output={"text": teammate_result.text},
                metadata={
                    "input_tokens": teammate_result.input_tokens,
                    "output_tokens": teammate_result.output_tokens,
                },
            )
            return teammate_result

    async def close(self) -> None:
        self._trace_observer.end_session(
            metadata={
                "runner": "yaml_workflow",
                "team": self._config.team,
                "teammate_name": self._config.name,
                "agent_config_name": self._agent_name,
            }
        )


async def create_teammate_runner(config: TeammateSpawnConfig) -> SupportsTeammateTurns:
    """Create the appropriate stateful runner for *config*."""
    if config.runner == "prompt_native":
        return await PromptNativeTeammateRunner.create(config)
    if config.runner == "yaml_workflow":
        return YamlWorkflowTeammateRunner(config)
    raise NotImplementedError("Harbor-backed swarm runners are not implemented yet")
