import logging
from pathlib import Path
from dataclasses import dataclass, field

from openharness.runs import (
    AgentSpec,
    InlineTaskSpec,
    LocalAgentRunSpec,
    run_local_agent,
    RunLaunchResult,
)
from openharness.observability.logging import setup_logging

log = logging.getLogger(__name__)


@dataclass
class LocalExperiment:
    agent_config: str | Path
    task: str
    workspace: Path
    model: str = "gemini-2.5-flash"
    max_turns: int = 10
    env: dict[str, str] = field(default_factory=dict)

    async def run(self, **kwargs) -> RunLaunchResult | None:
        setup_logging()

        config_path = Path(self.agent_config)
        agent_name = config_path.stem if config_path.exists() else "local_agent"

        # Install config to workspace
        target_dir = self.workspace / ".openharness" / "agent_configs"
        target_dir.mkdir(parents=True, exist_ok=True)
        if config_path.exists():
            (target_dir / config_path.name).write_text(
                config_path.read_text(encoding="utf-8"), encoding="utf-8"
            )

        spec = LocalAgentRunSpec(
            cwd=self.workspace,
            run_cwd=Path(__file__).resolve().parents[3],
            task=InlineTaskSpec(instruction=self.task),
            agent=AgentSpec(
                name=agent_name,
                model=self.model,
                max_turns=self.max_turns,
            ),
            **kwargs,
        )
        return await run_local_agent(spec)

    def log_summary(
        self,
        result: RunLaunchResult | None,
        passed: bool | None = None,
        extra: dict[str, str] | None = None,
    ) -> None:
        if result is None:
            return

        log.info("Run ID:    %s", result.run_id)
        log.info("Workspace: %s", self.workspace.resolve())
        log.info("Run dir:   %s", result.run_dir)
        if passed is not None:
            log.info("Passed:    %s", passed)

        base_extra = {
            "Agent": str(self.agent_config),
            "Model": self.model,
            "Trace URL": result.trace_url,
        }
        if extra:
            base_extra.update(extra)

        for label, value in base_extra.items():
            if value is not None:
                log.info("%s: %s", f"{label} ".ljust(10), value)

        for label, path in result.artifact_paths.items():
            marker = "yes" if path.exists() else "no"
            log.info("Artifact:  %-8s %s (%s)", label, path, marker)
