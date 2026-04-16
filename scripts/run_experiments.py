"""Run the configured OpenHarness experiment.

Launch with:

    uv run --extra harbor python scripts/run_experiments.py
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

from dotenv import load_dotenv

from openharness.experiments.runner import run_experiment
from openharness.experiments.specs import load_experiment_config
from openharness.observability.langfuse import langfuse_agent_env_for_docker

# ---------------------------------------------------------------------------
# Experiment configuration
# ---------------------------------------------------------------------------

CONFIG_PATH = Path("experiments/tb2-baseline.yaml")

# Same instance id means resume. Change this when agent prompts/config/code
# changes should produce a fresh benchmark namespace.
EXPERIMENT_INSTANCE_ID = "tb2-baseline"
MANIFEST_PATH = Path("runs/experiments") / EXPERIMENT_INSTANCE_ID / "experiment.json"

RESUME = True
DRY_RUN = False


def main() -> None:
    load_dotenv()
    
    config = load_experiment_config(CONFIG_PATH)
    manifest = run_experiment(
        config,
        cwd=Path.cwd(),
        manifest_path=MANIFEST_PATH,
        env=langfuse_agent_env_for_docker(),
        experiment_instance_id=EXPERIMENT_INSTANCE_ID,
        dry_run=DRY_RUN,
        resume=RESUME,
    )

    print(f"Experiment: {manifest['experiment_id']}")
    print(f"Instance:   {manifest['experiment_instance_id']}")
    print(f"Jobs:       {len(manifest['jobs'])}")
    print(f"Manifest:   {MANIFEST_PATH}")
    for job in manifest["jobs"]:
        print(f"- {job['job_id']} [{job['status']}]")


def _ensure_no_cli_args(args: Sequence[str]) -> None:
    if args:
        raise SystemExit(
            "scripts/run_experiments.py is configured by editing top-level constants; "
            "remove CLI arguments and launch it again."
        )


if __name__ == "__main__":
    _ensure_no_cli_args(sys.argv[1:])
    main()
