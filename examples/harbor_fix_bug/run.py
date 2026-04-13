"""Run the shared bug-fix task inside Harbor.

Feature slice:
- same YAML agent config used by the local example
- Harbor task/environment wrapper
- OpenHarness Harbor agent adapter
- Harbor task source instead of a manually defined inline task
- runtime-generated run ID propagated into the Harbor job
- canonical local run artifacts plus Harbor's external `result.json`

Prerequisites:
    Docker must be running.
    uv can install the pinned Harbor CLI tool.

Usage:
    uv run python examples/harbor_fix_bug/run.py
    uv run python examples/harbor_fix_bug/run.py --agent bugfix_agent
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import subprocess
import sys
from pathlib import Path

EXAMPLES_ROOT = Path(__file__).resolve().parents[1]
if str(EXAMPLES_ROOT) not in sys.path:
    sys.path.insert(0, str(EXAMPLES_ROOT))

from _shared.bugfix_task import (  # noqa: E402
    BUGFIX_AGENT_CONFIG,
    BUGFIX_AGENT_NAME,
    EXAMPLE_MODEL,
    add_common_arguments,
    install_project_agent_configs,
    local_langfuse_agent_env_for_harbor,
    log_run_summary,
    read_agent_config,
)
from openharness.config import load_settings  # noqa: E402
from openharness.harbor import (  # noqa: E402
    DEFAULT_HARBOR_VERSION,
    HarborEnvironmentSpec,
    HarborExistingJobPolicy,
    HarborJobSpec,
    HarborTaskSpec,
    HarborToolSpec,
    OpenHarnessHarborAgentSpec,
)
from openharness.runs import HarborAgentRunSpec, run_harbor_agent  # noqa: E402
from openharness.services.runs import generate_run_id  # noqa: E402

log = logging.getLogger(__name__)

HERE = Path(__file__).resolve().parent
TASK_DIR = HERE / "harbor_task"
OPENHARNESS_DIR = HERE.parent.parent


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_common_arguments(parser, include_workspace=False)
    parser.add_argument("--agent", default=BUGFIX_AGENT_NAME, help="Agent config name to run.")
    parser.add_argument(
        "--harbor-version",
        default=DEFAULT_HARBOR_VERSION,
        help="Pinned Harbor CLI version.",
    )
    return parser.parse_args()


def _harbor_score(path: Path | None) -> float | None:
    if path is None or not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    evals = data.get("stats", {}).get("evals", {})
    for value in evals.values():
        metrics = value.get("metrics", [])
        if metrics:
            return metrics[0].get("mean")
    return None


def _docker_daemon_available() -> bool:
    docker = shutil.which("docker")
    if docker is None:
        return False
    result = subprocess.run(
        [docker, "info"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    return result.returncode == 0


def _prepare_harbor_run_workspace() -> tuple[str, Path, Path, Path]:
    while True:
        run_id = generate_run_id()
        run_dir = OPENHARNESS_DIR / "runs" / run_id
        if not run_dir.exists():
            break
    workspace_dir = run_dir / "workspace"
    shutil.copytree(TASK_DIR, workspace_dir)
    config_dir = install_project_agent_configs(
        workspace_dir,
        (BUGFIX_AGENT_CONFIG,),
    )
    return run_id, run_dir, workspace_dir, config_dir / BUGFIX_AGENT_CONFIG


def main() -> None:
    from openharness.observability.logging import setup_logging

    setup_logging()
    args = _parse_args()
    settings = load_settings().merge_cli_overrides(model=EXAMPLE_MODEL)

    if not _docker_daemon_available():
        log.info("Docker daemon is not running. Start Docker to run the Harbor example.")
        return

    langfuse_env = local_langfuse_agent_env_for_harbor()
    run_id, run_dir, workspace_dir, config_path = _prepare_harbor_run_workspace()
    result = run_harbor_agent(
        HarborAgentRunSpec(
            cwd=OPENHARNESS_DIR,
            run_id=run_id,
            metadata={"example": "harbor_fix_bug"},
            job=HarborJobSpec(
                jobs_dir=run_dir / "harbor_jobs",
                existing_job_policy=HarborExistingJobPolicy.ERROR,
                tool=HarborToolSpec(
                    version=args.harbor_version,
                    editable_openharness_dir=OPENHARNESS_DIR,
                ),
                agent=OpenHarnessHarborAgentSpec(
                    agent_name=args.agent,
                    model=settings.model,
                    remote_cwd="/app",
                    max_turns=args.max_turns or 10,
                    max_tokens=8192,
                    agent_config_yaml=read_agent_config(
                        BUGFIX_AGENT_CONFIG,
                    ),
                    env=langfuse_env,
                ),
                task=HarborTaskSpec(path=workspace_dir),
                environment=HarborEnvironmentSpec(type="docker"),
            ),
        )
    )
    score = _harbor_score(result.external_result_path)
    passed = score is not None and score > 0

    log_run_summary(
        log,
        run_id=result.run_id,
        workspace=workspace_dir,
        run_dir=result.run_dir,
        passed=passed,
        extra={
            "Agent": args.agent,
            "Model": settings.model,
            "Trace URL": result.trace_url,
            "Config": config_path,
            "Score": score,
            "Harbor": result.external_result_path,
        },
    )


if __name__ == "__main__":
    main()
