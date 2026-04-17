"""Unit tests for HarborBackend's translation and portability helpers."""

from __future__ import annotations

import json
from pathlib import Path

from openharness.experiments.backends.harbor import (
    HarborBackend,
    _classify_phase,
    _parse_harbor_exception,
)
from openharness.experiments.manifest import TrialErrorPhase
from openharness.runs.specs import TrialResult


def test_parse_harbor_exception_from_dict() -> None:
    raw = str(
        {
            "exception_type": "RuntimeError",
            "exception_message": "Docker compose command failed",
            "exception_traceback": "Traceback...",
            "occurred_at": "2026-04-16T11:42:50+00:00",
        }
    )
    err = _parse_harbor_exception(raw)
    assert err.exception_type == "RuntimeError"
    assert "Docker compose" in err.message
    assert err.phase == TrialErrorPhase.ENV_SETUP
    assert err.occurred_at is not None


def test_parse_harbor_exception_from_plain_string() -> None:
    err = _parse_harbor_exception("ValueError: something went wrong in the verifier")
    assert err.exception_type == "ValueError"
    assert err.phase == TrialErrorPhase.VERIFIER


def test_classify_phase_env_setup() -> None:
    assert (
        _classify_phase("environment failed", "File _setup_environment")
        == TrialErrorPhase.ENV_SETUP
    )


def test_harbor_backend_writes_portable_result(tmp_path: Path) -> None:
    experiment_root = tmp_path / "experiment"
    trial_dir = experiment_root / "legs" / "basic" / "harbor" / "job-1" / "task__trial-1"
    trial_dir.mkdir(parents=True)

    harbor_result = {
        "trial_uri": f"file://{trial_dir}",
        "config": {"trial_dir": str(trial_dir)},
        "agent_result": {"n_input_tokens": 5, "n_output_tokens": 7},
        "metadata": {"absolute": str(trial_dir / "nested"), "other": 123},
    }
    (trial_dir / "result.json").write_text(json.dumps(harbor_result), encoding="utf-8")

    trial_result = TrialResult(
        trial_id="trial-1",
        task_name="task",
        trial_dir=trial_dir,
    )

    HarborBackend()._write_portable_harbor_results(experiment_root, [trial_result])

    portable_path = trial_dir / "result.portable.json"
    assert portable_path.exists()
    portable = json.loads(portable_path.read_text(encoding="utf-8"))
    assert portable["anchor"] == "experiment_root"
    assert portable["trial_dir"] == "legs/basic/harbor/job-1/task__trial-1"

    res = portable["result"]
    assert res["config"]["trial_dir"] == "legs/basic/harbor/job-1/task__trial-1"
    assert res["metadata"]["absolute"] == "legs/basic/harbor/job-1/task__trial-1/nested"
    assert res["metadata"]["other"] == 123


def test_parse_harbor_exception_handles_empty_string() -> None:
    err = _parse_harbor_exception("")
    assert err.message == ""
    assert err.phase == TrialErrorPhase.UNKNOWN
