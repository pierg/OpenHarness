"""Reproducibility metadata collection."""

from __future__ import annotations

import socket
import subprocess
import sys
from pathlib import Path

from openharness.experiments.manifest import Reproducibility
import importlib.metadata

try:
    openharness_version = importlib.metadata.version("openharness")
except importlib.metadata.PackageNotFoundError:
    openharness_version = "unknown"


def _run_git(args: list[str], cwd: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def collect_reproducibility() -> Reproducibility:
    """Collect reproducibility metadata."""
    cwd = Path.cwd()

    git_sha = _run_git(["rev-parse", "HEAD"], cwd)
    git_status = _run_git(["status", "--porcelain"], cwd)
    git_dirty = bool(git_status) if git_status is not None else False

    harbor_version = None
    try:
        import harbor

        harbor_version = getattr(harbor, "__version__", None)
    except ImportError:
        pass

    if not harbor_version:
        try:
            result = subprocess.run(
                ["harbor", "--version"],
                capture_output=True,
                text=True,
            )
            harbor_version = result.stdout.strip()
        except FileNotFoundError:
            pass

    return Reproducibility(
        git_sha=git_sha,
        git_dirty=git_dirty,
        harbor_version=harbor_version,
        openharness_version=openharness_version,
        python_version=sys.version.split()[0],
        hostname=socket.gethostname(),
    )
