"""Run declarative OpenHarness experiments.

Examples:
    uv run --extra harbor python scripts/run_experiments.py experiments/tb2-baseline.yaml --dry-run
    uv run --extra harbor python scripts/run_experiments.py experiments/tb2-baseline.yaml --run smoke
"""

from __future__ import annotations

import argparse
import os
from datetime import datetime
from pathlib import Path

from openharness.experiments.runner import run_experiment
from openharness.experiments.specs import (
    ExperimentRuntimeOverrides,
    load_experiment_spec,
    runtime_overrides_from_mapping,
)


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
    "OPENHARNESS_LANGFUSE_ENABLED",
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", type=Path, help="Experiment YAML file.")
    parser.add_argument("--run", action="append", dest="run_ids", help="Run id to execute.")
    parser.add_argument("--dry-run", action="store_true", help="Expand jobs without launching Harbor.")
    parser.add_argument("--manifest", type=Path, help="Path for experiment manifest JSON.")
    parser.add_argument("--model", help="Override model for all selected jobs.")
    parser.add_argument("--max-turns", type=int, help="Override max turns for all selected jobs.")
    parser.add_argument("--max-tokens", type=int, help="Override max tokens for all selected jobs.")
    parser.add_argument("--n-concurrent", type=int, help="Override Harbor trial concurrency.")
    parser.add_argument("--n-attempts", type=int, help="Override Harbor attempts per trial.")
    parser.add_argument("--n-tasks", type=int, help="Override task limit after filters.")
    args = parser.parse_args()

    spec = load_experiment_spec(args.spec)
    manifest_path = args.manifest or _default_manifest_path(spec.id)
    overrides = _build_cli_overrides(args)
    manifest = run_experiment(
        spec,
        cwd=Path.cwd(),
        manifest_path=manifest_path,
        env=_docker_agent_env(),
        cli_overrides=overrides,
        only_run_ids=set(args.run_ids) if args.run_ids else None,
        dry_run=args.dry_run,
    )

    print(f"Experiment: {manifest['experiment_id']}")
    print(f"Jobs:       {len(manifest['jobs'])}")
    print(f"Manifest:   {manifest_path}")
    for job in manifest["jobs"]:
        print(f"- {job['job_id']} [{job['status']}]")


def _build_cli_overrides(args: argparse.Namespace) -> ExperimentRuntimeOverrides | None:
    overrides = runtime_overrides_from_mapping(
        {
            "model": args.model,
            "max_turns": args.max_turns,
            "max_tokens": args.max_tokens,
            "n_concurrent": args.n_concurrent,
            "n_attempts": args.n_attempts,
            "n_tasks": args.n_tasks,
        }
    )
    if not any(getattr(overrides, field) is not None for field in type(overrides).model_fields):
        return None
    return overrides


def _default_manifest_path(experiment_id: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return Path("runs") / "experiments" / f"{experiment_id}-{stamp}" / "experiment.json"


def _docker_agent_env() -> dict[str, str]:
    env = {key: os.environ[key] for key in LANGFUSE_ENV_KEYS if key in os.environ}
    for key in ("LANGFUSE_HOST", "LANGFUSE_BASE_URL"):
        if env.get(key) in {"http://localhost:3000", "http://127.0.0.1:3000"}:
            env[key] = "http://host.docker.internal:3000"
    return env


if __name__ == "__main__":
    main()
