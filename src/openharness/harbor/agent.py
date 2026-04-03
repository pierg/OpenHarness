"""Harbor wrapper around the framework-agnostic OpenHarness agent system."""

from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from openharness.agents.contracts import TaskDefinition
from openharness.agents.factory import AgentFactory
from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from harbor.models.task.config import MCPServerConfig
from openharness.api.client import SupportsStreamingMessages
from openharness.api.provider import detect_provider
from openharness.config import load_settings
from openharness.observability import create_trace_observer
from openharness.runtime.session import AgentLogPaths, AgentRuntime
from openharness.services.runs import save_run_manifest
from openharness.tools.base import ToolRegistryFactory
from openharness.workspace.harbor import HarborWorkspace


OPENHARNESS_HARBOR_VERSION = "0.1.0"


@dataclass(frozen=True)
class HarborRunSummary:
    final_text: str
    input_tokens: int
    output_tokens: int


class OpenHarnessHarborAgent(BaseAgent):
    """Thin Harbor wrapper that delegates to any OpenHarness agent architecture.

    The ``agent_name`` selects which YAML config the factory loads.
    """

    SUPPORTS_ATIF = False

    def __init__(
        self,
        logs_dir: Path,
        model_name: str | None = None,
        logger: Any | None = None,
        mcp_servers: list[MCPServerConfig] | None = None,
        skills_dir: str | None = None,
        memory_dir: str | None = None,
        *,
        agent_name: str = "default",
        api_client: SupportsStreamingMessages | None = None,
        extra_env: dict[str, str] | None = None,
        remote_cwd: str = "/app",
        tool_registry_factory: ToolRegistryFactory | None = None,
        **kwargs: object,
    ) -> None:
        del kwargs
        self._remote_cwd = remote_cwd
        self._extra_env = dict(extra_env or {})
        resolved_model_name = (
            model_name
            or self._extra_env.get("OPENHARNESS_MODEL")
            or os.environ.get("OPENHARNESS_MODEL")
        )

        self._agent_name = agent_name
        factory = AgentFactory.with_default_configs()
        self._agent = factory.create(agent_name)

        self._api_client = api_client
        self._tool_registry_factory = tool_registry_factory

        super().__init__(
            logs_dir=logs_dir,
            model_name=resolved_model_name,
            logger=logger,
            mcp_servers=mcp_servers,
            skills_dir=skills_dir,
            memory_dir=memory_dir,
        )

    @staticmethod
    def name() -> str:
        return "openharness"

    def version(self) -> str | None:
        return OPENHARNESS_HARBOR_VERSION

    async def setup(self, environment: BaseEnvironment) -> None:
        del environment

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        with _temporary_environ(self._extra_env):
            self.logs_dir.mkdir(parents=True, exist_ok=True)
            messages_path = self.logs_dir / "messages.jsonl"
            events_path = self.logs_dir / "events.jsonl"

            settings = load_settings()
            resolved_settings = settings.merge_cli_overrides(model=self.model_name)
            resolved_model = self.model_name or resolved_settings.model
            trace_observer = create_trace_observer(
                session_id=uuid4().hex[:12],
                interface="harbor",
                cwd=self._remote_cwd,
                model=resolved_model,
                provider=detect_provider(resolved_settings).name,
            )
            trace_observer.start_session(
                metadata={
                    "remote_cwd": self._remote_cwd,
                    "mcp_server_count": len(self.mcp_servers),
                    "logs_dir": str(self.logs_dir),
                }
            )

            error_message: str | None = None
            summary = HarborRunSummary(final_text="", input_tokens=0, output_tokens=0)
            workspace = HarborWorkspace(environment, cwd=self._remote_cwd)
            log_paths = AgentLogPaths(
                messages_path=str(messages_path),
                events_path=str(events_path),
            )
            
            runtime = AgentRuntime(
                workspace=workspace,
                settings=resolved_settings,
                api_client=self._api_client,
                log_paths=log_paths,
                trace_observer=trace_observer,
                tool_registry_factory=self._tool_registry_factory,
            )

            try:
                result = await self._agent.run(
                    task=TaskDefinition(instruction=instruction),
                    runtime=runtime,
                )
                summary = HarborRunSummary(
                    final_text=result.final_text,
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                )
            except Exception as exc:
                error_message = str(exc)
                raise
            finally:
                context.n_input_tokens = summary.input_tokens or None
                context.n_output_tokens = summary.output_tokens or None
                context.n_cache_tokens = None
                context.cost_usd = None
                run_id = os.environ.get("OPENHARNESS_RUN_ID")
                run_root = os.environ.get("OPENHARNESS_RUN_ROOT")
                context.metadata = {
                    "agent_name": self.name(),
                    "agent_version": self.version(),
                    "model": resolved_model,
                    "trace_id": trace_observer.trace_id,
                    "run_id": run_id,
                    "run_root": run_root,
                    "remote_cwd": self._remote_cwd,
                    "messages_path": str(messages_path),
                    "events_path": str(events_path),
                    "mcp_server_count": len(self.mcp_servers),
                    "summary": asdict(summary),
                    "error": error_message,
                }
                if run_id is not None and run_root is not None:
                    save_run_manifest(
                        Path(run_root),
                        {
                            "run_id": run_id,
                            "run_root": run_root,
                            "agent_name": self.name(),
                            "agent_version": self.version(),
                            "model": resolved_model,
                            "trace_id": trace_observer.trace_id,
                            "summary": asdict(summary),
                            "error": error_message,
                        },
                    )
                trace_observer.end_session(
                    output={
                        "final_text": summary.final_text,
                        "usage": {
                            "input_tokens": summary.input_tokens,
                            "output_tokens": summary.output_tokens,
                            "total_tokens": summary.input_tokens + summary.output_tokens,
                        },
                    },
                    metadata={
                        "error": error_message,
                        "messages_path": str(messages_path),
                        "events_path": str(events_path),
                    },
                )


@contextmanager
def _temporary_environ(overrides: dict[str, str]):
    previous = {key: os.environ.get(key) for key in overrides}
    os.environ.update(overrides)
    try:
        yield
    finally:
        for key, old_value in previous.items():
            if old_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old_value
