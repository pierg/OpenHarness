"""High-level Harbor run orchestration."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from openharness.config.paths import get_project_runs_dir
from openharness.harbor import run_harbor_job
from openharness.runs.specs import HarborAgentRunSpec, RunLaunchResult
from openharness.services.runs import generate_run_id


def run_harbor_agent(spec: HarborAgentRunSpec) -> RunLaunchResult:
    """Launch a Harbor job while reserving canonical OpenHarness run artifacts."""
    resolved_run_id = spec.run_id or generate_run_id()
    job_spec = replace(
        spec.job,
        job_name=resolved_run_id,
        run_cwd=Path(spec.cwd).expanduser().resolve(),
        metadata={
            **spec.job.metadata,
            **spec.metadata,
        },
    )
    harbor_result = run_harbor_job(job_spec)
    run_dir = get_project_runs_dir(spec.cwd) / resolved_run_id
    trace_metadata = _read_harbor_trace_metadata(harbor_result.result_path)
    return RunLaunchResult(
        run_id=resolved_run_id,
        run_dir=run_dir,
        manifest_path=run_dir / "run.json",
        trace_id=getattr(harbor_result, "trace_id", None) or trace_metadata.get("trace_id"),
        trace_url=getattr(harbor_result, "trace_url", None) or trace_metadata.get("trace_url"),
        result_path=run_dir / "results.json",
        metrics_path=run_dir / "metrics.json",
        external_result_path=harbor_result.result_path,
    )


def _read_harbor_trace_metadata(result_path: Path) -> dict[str, str]:
    paths = [result_path]
    if result_path.parent.exists():
        paths.extend(sorted(result_path.parent.glob("*/result.json")))
    for path in paths:
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        metadata = _find_trace_metadata(payload)
        if metadata:
            return metadata
    return {}


def _find_trace_metadata(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        metadata = {}
        if isinstance(value.get("trace_id"), str) and value["trace_id"]:
            metadata["trace_id"] = value["trace_id"]
        if isinstance(value.get("trace_url"), str) and value["trace_url"]:
            metadata["trace_url"] = value["trace_url"]
        if metadata:
            return metadata
        for nested in value.values():
            found = _find_trace_metadata(nested)
            if found:
                return found
    if isinstance(value, list):
        for nested in value:
            found = _find_trace_metadata(nested)
            if found:
                return found
    return {}
