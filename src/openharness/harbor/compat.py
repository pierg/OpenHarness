"""Harbor type compatibility for optional integration code.

When the ``harbor`` package is installed, real types are imported.  Otherwise,
lightweight stubs are defined so that ``OpenHarnessHarborAgent`` can be type-
checked and tested without pulling in the full Harbor dependency tree.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

try:
    from harbor.agents.base import BaseAgent
    from harbor.environments.base import BaseEnvironment, ExecResult
    from harbor.models.agent.context import AgentContext
    from harbor.models.task.config import MCPServerConfig
except ImportError:

    @dataclass
    class ExecResult:
        stdout: str | None = None
        stderr: str | None = None
        return_code: int = 0


    class AgentContext(BaseModel):
        n_input_tokens: int | None = None
        n_cache_tokens: int | None = None
        n_output_tokens: int | None = None
        cost_usd: float | None = None
        rollout_details: list[object] | None = None
        metadata: dict[str, Any] | None = None


    class MCPServerConfig(BaseModel):
        name: str
        transport: str = "sse"
        url: str | None = None
        command: str | None = None
        args: list[str] = Field(default_factory=list)


    class BaseEnvironment:
        default_user: str | int | None = None

        async def exec(
            self,
            command: str,
            cwd: str | None = None,
            env: dict[str, str] | None = None,
            timeout_sec: int | None = None,
            user: str | int | None = None,
        ) -> ExecResult:
            raise NotImplementedError

        async def upload_file(self, source_path: Path | str, target_path: str) -> None:
            raise NotImplementedError

        async def download_file(self, source_path: str, target_path: Path | str) -> None:
            raise NotImplementedError

        async def is_dir(self, path: str, user: str | int | None = None) -> bool:
            raise NotImplementedError

        async def is_file(self, path: str, user: str | int | None = None) -> bool:
            raise NotImplementedError


    class BaseAgent:
        SUPPORTS_ATIF = False

        def __init__(
            self,
            logs_dir: Path,
            model_name: str | None = None,
            logger: logging.Logger | None = None,
            mcp_servers: list[MCPServerConfig] | None = None,
            skills_dir: str | None = None,
            memory_dir: str | None = None,
            *args: object,
            **kwargs: object,
        ) -> None:
            del args, kwargs
            self.logs_dir = logs_dir
            self.model_name = model_name
            self.logger = logger or logging.getLogger(__name__)
            self.mcp_servers = mcp_servers or []
            self.skills_dir = skills_dir
            self.memory_dir = memory_dir

        @staticmethod
        def name() -> str:
            raise NotImplementedError

        def version(self) -> str | None:
            raise NotImplementedError

        async def setup(self, environment: BaseEnvironment) -> None:
            raise NotImplementedError

        async def run(
            self,
            instruction: str,
            environment: BaseEnvironment,
            context: AgentContext,
        ) -> None:
            raise NotImplementedError


__all__ = [
    "AgentContext",
    "BaseAgent",
    "BaseEnvironment",
    "ExecResult",
    "MCPServerConfig",
]
