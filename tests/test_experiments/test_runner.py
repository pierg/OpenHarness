"""Runner-level tests using a stub backend."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from openharness.experiments.backends import Backend, LegOutcome
from openharness.experiments.manifest import (
    ExperimentManifest,
    LegResultStatus,
    LegStatus,
    TrialError,
    TrialErrorPhase,
    TrialRecord,
)
from openharness.experiments.paths import make_rel
from openharness.experiments.runner import run_experiment
from openharness.experiments.spec import (
    load_experiment_spec_full,
)


def _mini_spec_text() -> str:
    return """
id: stub-experiment
dataset: stub-dataset
model: gpt-stub
agents:
  - basic
"""


class _StubBackend(Backend):
    def __init__(self, outcomes: dict[str, LegOutcome]):
        self._outcomes = outcomes

    async def run_leg(self, leg, ctx):
        return self._outcomes[leg.leg_id]

    def is_leg_complete(self, leg, ctx):
        return False


def _outcome_with_trials(experiment_root: Path, trials: tuple[TrialRecord, ...]) -> LegOutcome:
    now = datetime.now(timezone.utc)
    return LegOutcome(
        status=LegStatus.SUCCEEDED,
        trials=trials,
        started_at=now,
        finished_at=now,
    )


@pytest.mark.asyncio
async def test_run_experiment_persists_portable_manifest(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.yaml"
    spec_path.write_text(_mini_spec_text(), encoding="utf-8")
    loaded = load_experiment_spec_full(spec_path)

    experiment_root = tmp_path / "runs"
    experiment_root.mkdir()

    trial_dir = experiment_root / "legs" / "basic" / "harbor" / "job" / "task__trial-1"
    trial_dir.mkdir(parents=True)

    trials = (
        TrialRecord(
            trial_id="trial-1",
            task_name="task",
            trial_dir=make_rel(experiment_root, trial_dir),
            score=1.0,
            passed=True,
        ),
        TrialRecord(
            trial_id="trial-2",
            task_name="task",
            trial_dir=Path("legs/basic/harbor/job/task__trial-2"),
            score=0.0,
            passed=False,
            error=TrialError(
                exception_type="RuntimeError",
                message="boom",
                phase=TrialErrorPhase.ENV_SETUP,
            ),
        ),
        TrialRecord(
            trial_id="trial-3",
            task_name="task",
            trial_dir=Path("legs/basic/harbor/job/task__trial-3"),
            score=0.0,
            passed=False,
        ),
    )
    backend = _StubBackend({"basic": _outcome_with_trials(experiment_root, trials)})

    manifest = await run_experiment(
        loaded.spec,
        experiment_root=experiment_root,
        instance_id="inst-1",
        backend=backend,
        loaded_spec=loaded,
    )

    assert manifest.schema_version == 2
    assert manifest.created_at.tzinfo is not None
    leg = manifest.legs[0]
    assert leg.status == LegStatus.SUCCEEDED
    assert leg.result_status == LegResultStatus.PARTIAL
    agg = leg.aggregate
    assert agg is not None
    assert agg.n_trials == 3
    assert agg.n_passed == 1
    assert agg.n_failed == 1
    assert agg.n_errored == 1
    assert agg.n_errored_by_phase == {"env_setup": 1}

    persisted = json.loads((experiment_root / "experiment.json").read_text(encoding="utf-8"))
    persisted_trial_dirs = [t["trial_dir"] for t in persisted["legs"][0]["trials"]]
    for path in persisted_trial_dirs:
        assert not path.startswith("/"), f"{path} should be relative"
        assert path.startswith("legs/")

    source_yaml = (experiment_root / "config.source.yaml").read_text(encoding="utf-8")
    assert source_yaml == loaded.source_text

    resolved_yaml = yaml.safe_load(
        (experiment_root / "config.resolved.yaml").read_text(encoding="utf-8")
    )
    assert resolved_yaml["defaults"]["model"] == "gpt-stub"

    summary = (experiment_root / "results" / "summary.md").read_text(encoding="utf-8")
    assert "basic" in summary
    rows = json.loads((experiment_root / "results" / "rows.json").read_text(encoding="utf-8"))
    assert {row["status"] for row in rows} == {"passed", "failed", "errored"}


@pytest.mark.asyncio
async def test_dry_run_does_not_invoke_backend(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.yaml"
    spec_path.write_text(_mini_spec_text(), encoding="utf-8")
    loaded = load_experiment_spec_full(spec_path)

    class _FailIfCalled(Backend):
        async def run_leg(self, leg, ctx):
            raise AssertionError("backend should not run during dry-run")

        def is_leg_complete(self, leg, ctx):
            return False

    experiment_root = tmp_path / "runs"
    manifest = await run_experiment(
        loaded.spec,
        experiment_root=experiment_root,
        instance_id="inst-1",
        backend=_FailIfCalled(),
        dry_run=True,
        loaded_spec=loaded,
    )

    assert [leg.status for leg in manifest.legs] == [LegStatus.DRY_RUN]
    assert not any(experiment_root.rglob("harbor/**/*.json"))


@pytest.mark.asyncio
async def test_resume_skips_complete_leg(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.yaml"
    spec_path.write_text(_mini_spec_text(), encoding="utf-8")
    loaded = load_experiment_spec_full(spec_path)

    class _CompletedBackend(Backend):
        async def run_leg(self, leg, ctx):
            raise AssertionError("should not be called for completed legs")

        def is_leg_complete(self, leg, ctx):
            return True

    experiment_root = tmp_path / "runs"
    manifest = await run_experiment(
        loaded.spec,
        experiment_root=experiment_root,
        instance_id="inst-1",
        backend=_CompletedBackend(),
        resume=True,
        loaded_spec=loaded,
    )
    assert manifest.legs[0].status == LegStatus.SKIPPED


@pytest.mark.asyncio
async def test_no_empty_runs_or_openharness_dirs(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.yaml"
    spec_path.write_text(_mini_spec_text(), encoding="utf-8")
    loaded = load_experiment_spec_full(spec_path)

    experiment_root = tmp_path / "runs"
    trial_dir = experiment_root / "legs" / "basic" / "t"
    trial_dir.mkdir(parents=True)
    outcome = LegOutcome(
        status=LegStatus.SUCCEEDED,
        trials=(),
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
    )
    backend = _StubBackend({"basic": outcome})

    await run_experiment(
        loaded.spec,
        experiment_root=experiment_root,
        instance_id="inst-1",
        backend=backend,
        loaded_spec=loaded,
    )

    assert not (experiment_root / "runs").exists(), "runs/ must not be created inside experiment"
    assert not (experiment_root / ".openharness").exists(), ".openharness/ must not be created"


def test_experiment_manifest_rejects_absolute_paths(tmp_path: Path) -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ExperimentManifest.model_validate(
            {
                "schema_version": 2,
                "experiment_id": "x",
                "instance_id": "i",
                "dataset": "d",
                "spec_path": "/etc/passwd",
                "resolved_spec_path": "config.resolved.yaml",
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
                "reproducibility": {
                    "git_sha": None,
                    "git_dirty": False,
                    "harbor_version": None,
                    "openharness_version": "0.0.0",
                    "python_version": "3.12.0",
                    "hostname": "h",
                },
                "legs": [],
            }
        )
