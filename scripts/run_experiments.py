"""Run the configured OpenHarness experiment.

Launch with:

    uv run --extra harbor python scripts/run_experiments.py
"""

from __future__ import annotations

import sys
from openharness.experiments.cli import app


def main() -> None:
    args = sys.argv[1:]
    if not args:
        args = [
            "run",
            "experiments/tb2-baseline.yaml",
            "--instance-id",
            "tb2-baseline",
            "--resume",
        ]
    app(args)


if __name__ == "__main__":
    main()
