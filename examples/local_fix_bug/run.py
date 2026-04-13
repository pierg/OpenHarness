"""Start a local OpenHarness run and inspect its artifacts.

Feature slice:
- YAML agent config installed into the workspace project catalog
- high-level `openharness.runs.run_local_agent` API
- manually defined inline task
- runtime-generated run ID
- canonical `runs/<generated-run-id>/` artifacts
- local workspace edit and verification

Usage:
    uv run python examples/local_fix_bug/run.py
    uv run python examples/local_fix_bug/run.py --agent bugfix_agent
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
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

log = logging.getLogger(__name__)


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


async def main() -> None:
    from openharness.observability.logging import setup_logging

    setup_logging()
    args = _parse_args()
    configure_local_langfuse()
    settings = load_settings().merge_cli_overrides(model=EXAMPLE_MODEL)
    run_workspace = prepare_run_workspace("local_fix_bug")
    workspace = run_workspace.workspace
    config_dir = install_project_agent_configs(
        workspace.root,
        (BUGFIX_AGENT_CONFIG,),
    )

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
            metadata={"example": "local_fix_bug"},
        )
    )

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
            "Trace URL": result.trace_url,
            "Config": config_dir / BUGFIX_AGENT_CONFIG,
            "Result": result.result_path,
            "Metrics": result.metrics_path,
        },
    )


if __name__ == "__main__":
    asyncio.run(main())
