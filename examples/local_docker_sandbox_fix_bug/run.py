"""Run the shared bug-fix task with the OpenHarness Docker sandbox.

Feature slice:
- same YAML agent config used by the local and Harbor examples
- OpenHarness Docker sandbox backend for bash tool execution
- sandbox session started before the agent run
- runtime-generated run ID
- canonical `runs/<generated-run-id>/` artifacts

This is different from the Harbor example: Harbor runs the evaluation task in a
Docker environment, while this example runs a normal local OpenHarness agent
with OpenHarness tools routed through the Docker sandbox backend.

Prerequisites:
    Docker must be running.

Usage:
    uv run python examples/local_docker_sandbox_fix_bug/run.py
    uv run python examples/local_docker_sandbox_fix_bug/run.py --agent bugfix_agent
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from contextlib import contextmanager
from pathlib import Path

EXAMPLES_ROOT = Path(__file__).resolve().parents[1]
if str(EXAMPLES_ROOT) not in sys.path:
    sys.path.insert(0, str(EXAMPLES_ROOT))

from _shared.bugfix_task import (  # noqa: E402
    BUGFIX_AGENT_CONFIG,
    BUGFIX_AGENT_NAME,
    EXAMPLE_MODEL,
    INSTRUCTION,
    add_common_arguments,
    configure_local_langfuse,
    install_project_agent_configs,
    log_run_summary,
    prepare_run_workspace,
    script_prints_twelve,
)
from openharness.config import load_settings  # noqa: E402
from openharness.runs import AgentSpec, InlineTaskSpec, LocalAgentRunSpec, run_local_agent  # noqa: E402
from openharness.sandbox.adapter import SandboxUnavailableError  # noqa: E402
from openharness.sandbox.docker_backend import get_docker_availability  # noqa: E402
from openharness.sandbox.session import (  # noqa: E402
    get_docker_sandbox,
    start_docker_sandbox,
    stop_docker_sandbox,
)
from openharness.tools.base import ToolExecutionContext  # noqa: E402
from openharness.tools.bash_tool import BashTool, BashToolInput  # noqa: E402

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


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_common_arguments(parser)
    parser.add_argument(
        "--agent",
        default=BUGFIX_AGENT_NAME,
        help="Agent config name from the built-in/user/project catalog.",
    )
    return parser.parse_args()


async def _probe_sandbox(workspace: Path) -> str:
    """Run one command through BashTool so the example proves sandbox routing."""
    result = await BashTool().execute(
        BashToolInput(command="printf 'sandbox-python=' && python --version", timeout_seconds=30),
        ToolExecutionContext(cwd=workspace),
    )
    if result.is_error:
        raise RuntimeError(f"Docker sandbox probe failed: {result.output}")
    return result.output.strip()


async def main() -> None:
    from openharness.observability.logging import setup_logging

    setup_logging()
    args = _parse_args()
    configure_local_langfuse()

    with _docker_sandbox_env():
        settings = load_settings().merge_cli_overrides(model=EXAMPLE_MODEL)
        availability = get_docker_availability(settings)
        if not availability.available:
            log.info("Docker sandbox is unavailable: %s", availability.reason)
            return

        run_workspace = prepare_run_workspace("local_docker_sandbox_fix_bug")
        workspace = run_workspace.workspace
        config_dir = install_project_agent_configs(
            workspace.root,
            (BUGFIX_AGENT_CONFIG,),
        )

        container_name = None
        sandbox_probe = None
        try:
            await start_docker_sandbox(
                settings,
                session_id=run_workspace.run_id,
                cwd=workspace.root,
            )
            session = get_docker_sandbox()
            container_name = session.container_name if session is not None else None
            sandbox_probe = await _probe_sandbox(workspace.root)

            result = await run_local_agent(
                LocalAgentRunSpec(
                    cwd=workspace.root,
                    run_cwd=EXAMPLES_ROOT.parent,
                    run_id=run_workspace.run_id,
                    task=InlineTaskSpec(instruction=INSTRUCTION),
                    agent=AgentSpec(
                        name=args.agent,
                        model=settings.model,
                        max_turns=args.max_turns,
                    ),
                    metadata={
                        "example": "local_docker_sandbox_fix_bug",
                        "sandbox_backend": "docker",
                        "sandbox_container": container_name,
                        "sandbox_probe": sandbox_probe,
                    },
                )
            )
        except SandboxUnavailableError as exc:
            log.info("Docker sandbox could not start: %s", exc)
            return
        finally:
            await stop_docker_sandbox()

    passed = script_prints_twelve(workspace.root)
    log_run_summary(
        log,
        run_id=result.run_id,
        workspace=workspace.root,
        run_dir=result.run_dir,
        passed=passed,
        extra={
            "Agent": args.agent,
            "Model": settings.model,
            "Sandbox": container_name,
            "Probe": sandbox_probe,
            "Trace URL": result.trace_url,
            "Config": config_dir / BUGFIX_AGENT_CONFIG,
            "Result": result.result_path,
            "Metrics": result.metrics_path,
        },
    )


if __name__ == "__main__":
    asyncio.run(main())
