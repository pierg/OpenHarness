"""Run the shared bug-fix task with the OpenHarness Docker sandbox.

Usage:
    uv run python examples/local_docker_sandbox_fix_bug/run.py
"""

import sys
import os
import logging
from contextlib import contextmanager
from pathlib import Path

EXAMPLES_ROOT = Path(__file__).resolve().parents[1]
if str(EXAMPLES_ROOT) not in sys.path:
    sys.path.insert(0, str(EXAMPLES_ROOT))

from _shared.helpers import prepare_bugfix_workspace, get_bugfix_instruction, script_prints_twelve  # noqa: E402
from openharness.experiments import LocalExperiment  # noqa: E402
from openharness.experiments.observability import setup_local_langfuse  # noqa: E402
from openharness.config import load_settings  # noqa: E402
from openharness.sandbox.adapter import SandboxUnavailableError  # noqa: E402
from openharness.sandbox.docker_backend import get_docker_availability  # noqa: E402
from openharness.sandbox.session import (  # noqa: E402
    get_docker_sandbox,
    start_docker_sandbox,
    stop_docker_sandbox,
)

AGENT_CONFIG = EXAMPLES_ROOT / "_shared" / "agent_configs" / "bugfix_agent.yaml"
MODEL = "gemini-3.1-flash-lite-preview"
MAX_TURNS = 10

log = logging.getLogger(__name__)

SANDBOX_ENV = {
    "OPENHARNESS_SANDBOX_ENABLED": "1",
    "OPENHARNESS_SANDBOX_BACKEND": "docker",
    "OPENHARNESS_SANDBOX_FAIL_IF_UNAVAILABLE": "1",
}


@contextmanager
def _docker_sandbox_env():
    previous = {key: os.environ.get(key) for key in SANDBOX_ENV}
    os.environ.update(SANDBOX_ENV)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


async def main() -> None:
    env = setup_local_langfuse(docker_compatible=False)
    workspace_dir = prepare_bugfix_workspace()

    experiment = LocalExperiment(
        agent_config=AGENT_CONFIG,
        task=get_bugfix_instruction(local=True),
        workspace=workspace_dir,
        model=MODEL,
        max_turns=MAX_TURNS,
        env=env,
    )

    with _docker_sandbox_env():
        settings = load_settings().merge_cli_overrides(model=MODEL)
        availability = get_docker_availability(settings)
        if not availability.available:
            log.info("Docker sandbox is unavailable: %s", availability.reason)
            return

        container_name = None
        result = None
        try:
            run_id = workspace_dir.parent.name

            await start_docker_sandbox(
                settings,
                session_id=run_id,
                cwd=workspace_dir,
            )
            session = get_docker_sandbox()
            container_name = session.container_name if session is not None else None

            result = await experiment.run(
                run_id=run_id,
                metadata={
                    "example": "local_docker_sandbox_fix_bug",
                    "sandbox_backend": "docker",
                    "sandbox_container": container_name,
                },
            )
        except SandboxUnavailableError as exc:
            log.info("Docker sandbox could not start: %s", exc)
            return
        finally:
            await stop_docker_sandbox()

    passed = script_prints_twelve(workspace_dir)
    experiment.log_summary(result, passed=passed, extra={"Sandbox": container_name})


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
