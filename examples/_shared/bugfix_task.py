"""Shared bug-fix task setup for the OpenHarness examples."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openharness.services.runs import generate_run_id

EXAMPLES_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = EXAMPLES_ROOT.parent
AGENT_CONFIG_TEMPLATES = Path(__file__).resolve().parent / "agent_configs"
EXAMPLE_MODEL = "gemini-2.5-flash"
BUGFIX_AGENT_NAME = "bugfix_agent"
BUGFIX_AGENT_CONFIG = f"{BUGFIX_AGENT_NAME}.yaml"
LOCAL_LANGFUSE_HOST = "http://localhost:3000"
LOCAL_LANGFUSE_DOCKER_HOST = "http://host.docker.internal:3000"
LANGFUSE_ENV_KEYS = (
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
    "LANGFUSE_HOST",
    "LANGFUSE_BASE_URL",
    "LANGFUSE_ENVIRONMENT",
    "LANGFUSE_RELEASE",
    "LANGFUSE_SAMPLE_RATE",
    "OPENHARNESS_LANGFUSE_FLUSH_MODE",
    "OPENHARNESS_LANGFUSE_REQUIRED",
    "OPENHARNESS_LANGFUSE_VERIFY",
)

BUGGY_CODE = """\
def sum_evens(numbers):
    # BUG: should filter n % 2 == 0, not n % 2 == 1
    return sum(n for n in numbers if n % 2 == 1)


if __name__ == "__main__":
    result = sum_evens([1, 2, 3, 4, 5, 6])
    print(result)
"""

INSTRUCTION = (
    "The file sum_evens.py in the current directory contains a function "
    "`sum_evens(numbers)` that should return the sum of all even numbers in a list, "
    "but it currently returns the sum of odd numbers instead.\n\n"
    "Fix the bug so that `sum_evens([1, 2, 3, 4, 5, 6])` returns `12`.\n\n"
    "Verify the fix before you finish."
)


@dataclass(frozen=True)
class BugfixWorkspace:
    """Paths for one seeded bug-fix workspace."""

    root: Path
    script_path: Path


@dataclass(frozen=True)
class ExampleRunWorkspace:
    """Filesystem layout for an example run."""

    run_id: str
    run_dir: Path
    workspace: BugfixWorkspace


def add_common_arguments(
    parser: argparse.ArgumentParser,
    *,
    include_workspace: bool = False,
) -> None:
    """Add arguments shared by example launchers."""
    if include_workspace:
        parser.add_argument(
            "--workspace",
            type=Path,
            default=None,
            help=(
                "Workspace directory to create or reuse. Defaults to "
                "runs/<generated-run-id>/workspace."
            ),
        )
    parser.add_argument(
        "--max-turns",
        type=int,
        default=None,
        help="Optional max-turns override.",
    )


def prepare_workspace(
    example_name: str,
    *,
    workspace: Path | None = None,
) -> BugfixWorkspace:
    """Create a clean workspace containing the shared bug-fix task."""
    if workspace is None:
        raise ValueError("Examples must pass an explicit workspace under runs/<run-id>/workspace")
    root = workspace.expanduser().resolve()
    root = root.expanduser().resolve()
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    script_path = root / "sum_evens.py"
    script_path.write_text(BUGGY_CODE, encoding="utf-8")
    return BugfixWorkspace(root=root, script_path=script_path)


def prepare_run_workspace(example_name: str) -> ExampleRunWorkspace:
    """Create ``runs/<run-id>/workspace`` and seed the shared bug-fix task."""
    while True:
        run_id = generate_run_id()
        run_dir = REPO_ROOT / "runs" / run_id
        if not run_dir.exists():
            break
    workspace = prepare_workspace(example_name, workspace=run_dir / "workspace")
    return ExampleRunWorkspace(run_id=run_id, run_dir=run_dir, workspace=workspace)


def configure_local_langfuse() -> str:
    """Require a live local Langfuse instance for examples."""
    if not os.environ.get("LANGFUSE_HOST") and not os.environ.get("LANGFUSE_BASE_URL"):
        os.environ["LANGFUSE_HOST"] = LOCAL_LANGFUSE_HOST
    os.environ.setdefault("OPENHARNESS_LANGFUSE_FLUSH_MODE", "live")
    os.environ.setdefault("OPENHARNESS_LANGFUSE_REQUIRED", "1")
    missing = [
        key for key in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY") if not os.environ.get(key)
    ]
    if missing:
        host = os.environ.get("LANGFUSE_HOST") or os.environ.get("LANGFUSE_BASE_URL")
        raise RuntimeError(
            "Local Langfuse is required for examples. Start Langfuse, create a "
            f"project at {host}, and export: {', '.join(missing)}."
        )
    return (
        os.environ.get("LANGFUSE_HOST")
        or os.environ.get("LANGFUSE_BASE_URL")
        or LOCAL_LANGFUSE_HOST
    )


def local_langfuse_agent_env_for_harbor() -> dict[str, str]:
    """Return Langfuse env vars adjusted for a Docker-hosted Harbor agent."""
    configure_local_langfuse()
    env = {key: os.environ[key] for key in LANGFUSE_ENV_KEYS if key in os.environ}
    if env.get("LANGFUSE_HOST") in {LOCAL_LANGFUSE_HOST, "http://127.0.0.1:3000"}:
        env["LANGFUSE_HOST"] = LOCAL_LANGFUSE_DOCKER_HOST
    if env.get("LANGFUSE_BASE_URL") in {LOCAL_LANGFUSE_HOST, "http://127.0.0.1:3000"}:
        env["LANGFUSE_BASE_URL"] = LOCAL_LANGFUSE_DOCKER_HOST
    return env


def install_project_agent_configs(
    workspace: Path,
    config_names: tuple[str, ...],
) -> Path:
    """Copy tracked example YAML templates into a workspace's project catalog."""
    target_dir = workspace / ".openharness" / "agent_configs"
    target_dir.mkdir(parents=True, exist_ok=True)
    for name in config_names:
        (target_dir / name).write_text(read_agent_config(name), encoding="utf-8")
    return target_dir


def read_agent_config(config_name: str) -> str:
    """Read a tracked example YAML config."""
    source = AGENT_CONFIG_TEMPLATES / config_name
    return source.read_text(encoding="utf-8")


def run_script(workspace: Path) -> subprocess.CompletedProcess[str]:
    """Execute the demo script and return the completed process."""
    return subprocess.run(
        [sys.executable, str(workspace / "sum_evens.py")],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )


def script_prints_twelve(workspace: Path) -> bool:
    """Return True when the bug-fix script prints the expected value."""
    return run_script(workspace).stdout.strip() == "12"


def latest_artifact_paths(run_dir: Path) -> dict[str, Path]:
    """Return the canonical artifact paths for a run directory."""
    return {
        "manifest": run_dir / "run.json",
        "messages": run_dir / "messages.jsonl",
        "events": run_dir / "events.jsonl",
        "results": run_dir / "results.json",
        "metrics": run_dir / "metrics.json",
    }


def log_run_summary(
    log: Any,
    *,
    run_id: str,
    workspace: Path,
    run_dir: Path,
    passed: bool,
    extra: dict[str, Any] | None = None,
) -> None:
    """Print a consistent summary for example runs."""
    log.info("Run ID:    %s", run_id)
    log.info("Workspace: %s", workspace)
    log.info("Run dir:   %s", run_dir)
    log.info("Passed:    %s", passed)
    for label, value in (extra or {}).items():
        log.info("%s: %s", f"{label} ".ljust(10), value)
    for label, path in latest_artifact_paths(run_dir).items():
        marker = "yes" if path.exists() else "no"
        log.info("Artifact:  %-8s %s (%s)", label, path, marker)
