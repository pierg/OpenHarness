"""Tests for the experiment `rerun` CLI command and its leg-selection logic."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from typer.testing import CliRunner

from openharness.experiments.cli import (
    DEFAULT_RERUN_STATUSES,
    app as experiments_app,
    select_legs_to_rerun,
)
from openharness.experiments.manifest import (
    ExperimentManifest,
    LegRecord,
    LegResultStatus,
    LegStatus,
    Reproducibility,
)


def _leg(
    leg_id: str,
    *,
    status: LegStatus,
    result_status: LegResultStatus | None,
) -> LegRecord:
    return LegRecord(
        leg_id=leg_id,
        agent_id=leg_id,
        status=status,
        result_status=result_status,
        started_at=None,
        finished_at=None,
        duration_sec=None,
        harbor_dir=None,
        harbor_result_path=None,
        agent_config_path=None,
    )


def _manifest(legs: Iterable[LegRecord]) -> ExperimentManifest:
    now = datetime.now(timezone.utc)
    return ExperimentManifest(
        experiment_id="exp",
        instance_id="exp-instance",
        dataset="ds",
        spec_path=Path("config.source.yaml"),
        resolved_spec_path=Path("config.resolved.yaml"),
        created_at=now,
        updated_at=now,
        reproducibility=Reproducibility(
            git_sha=None,
            git_dirty=False,
            harbor_version=None,
            openharness_version="0.0.0",
            python_version="3.12",
            hostname="h",
        ),
        legs=tuple(legs),
    )


def test_select_skips_all_passed_legs():
    manifest = _manifest(
        [
            _leg("good", status=LegStatus.SUCCEEDED, result_status=LegResultStatus.ALL_PASSED),
            _leg("bad", status=LegStatus.SUCCEEDED, result_status=LegResultStatus.ALL_FAILED),
        ]
    )

    selected = [leg.leg_id for leg in select_legs_to_rerun(manifest)]
    assert selected == ["bad"]


def test_select_includes_failed_partial_errored_and_no_trials_by_default():
    manifest = _manifest(
        [
            _leg("a", status=LegStatus.SUCCEEDED, result_status=LegResultStatus.ALL_PASSED),
            _leg("b", status=LegStatus.SUCCEEDED, result_status=LegResultStatus.PARTIAL),
            _leg("c", status=LegStatus.SUCCEEDED, result_status=LegResultStatus.ALL_FAILED),
            _leg("d", status=LegStatus.SUCCEEDED, result_status=LegResultStatus.ALL_ERRORED),
            _leg("e", status=LegStatus.SUCCEEDED, result_status=LegResultStatus.NO_TRIALS),
        ]
    )

    selected = sorted(leg.leg_id for leg in select_legs_to_rerun(manifest))
    assert selected == ["b", "c", "d", "e"]


def test_select_includes_failed_or_interrupted_legs_regardless_of_result_status():
    manifest = _manifest(
        [
            _leg("crashed", status=LegStatus.FAILED, result_status=None),
            _leg("interrupted", status=LegStatus.INTERRUPTED, result_status=None),
            _leg("pending", status=LegStatus.PENDING, result_status=None),
            _leg("running", status=LegStatus.RUNNING, result_status=None),
        ]
    )

    selected = sorted(leg.leg_id for leg in select_legs_to_rerun(manifest))
    assert selected == ["crashed", "interrupted", "pending", "running"]


def test_select_only_legs_overrides_status_filter():
    manifest = _manifest(
        [
            _leg("a", status=LegStatus.SUCCEEDED, result_status=LegResultStatus.ALL_PASSED),
            _leg("b", status=LegStatus.SUCCEEDED, result_status=LegResultStatus.ALL_FAILED),
            _leg("c", status=LegStatus.SUCCEEDED, result_status=LegResultStatus.ALL_PASSED),
        ]
    )

    selected = [leg.leg_id for leg in select_legs_to_rerun(manifest, only_legs=["a", "c"])]
    assert selected == ["a", "c"]


def test_select_status_filter_narrows_default_set():
    manifest = _manifest(
        [
            _leg("a", status=LegStatus.SUCCEEDED, result_status=LegResultStatus.PARTIAL),
            _leg("b", status=LegStatus.SUCCEEDED, result_status=LegResultStatus.ALL_FAILED),
        ]
    )

    only_partial = [leg.leg_id for leg in select_legs_to_rerun(manifest, statuses=["partial"])]
    assert only_partial == ["a"]


def test_default_rerun_statuses_excludes_all_passed():
    assert "all_passed" not in DEFAULT_RERUN_STATUSES


def test_rerun_cli_dry_run_lists_failed_legs_and_does_not_touch_disk(tmp_path: Path):
    """End-to-end CLI smoke: --dry-run must report selected legs and
    leave the run directory untouched. We avoid invoking the actual
    runner by short-circuiting before run_experiment is called."""
    root = tmp_path / "exp-root"
    root.mkdir()
    (root / "legs" / "good").mkdir(parents=True)
    (root / "legs" / "good" / "leg.json").write_text("{}", encoding="utf-8")
    (root / "legs" / "bad").mkdir(parents=True)
    (root / "legs" / "bad" / "leg.json").write_text("{}", encoding="utf-8")

    manifest = _manifest(
        [
            _leg("good", status=LegStatus.SUCCEEDED, result_status=LegResultStatus.ALL_PASSED),
            _leg("bad", status=LegStatus.SUCCEEDED, result_status=LegResultStatus.ALL_FAILED),
        ]
    )
    (root / "experiment.json").write_text(manifest.model_dump_json(), encoding="utf-8")
    # Minimal but valid resolved spec so the CLI passes its existence check
    # and doesn't fall over before --dry-run short-circuits.
    (root / "config.resolved.yaml").write_text(
        "id: exp\ndataset: terminal-bench@2.0\nagents:\n  - good\n  - bad\n",
        encoding="utf-8",
    )

    runner = CliRunner()
    result = runner.invoke(experiments_app, ["rerun", str(root), "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "bad" in result.output
    # The passed-clean leg must not appear as a selected target.
    selected_lines = [line for line in result.output.splitlines() if line.strip().startswith("- ")]
    assert any("bad" in line for line in selected_lines)
    assert not any(line.strip().startswith("- good") for line in selected_lines)

    # Disk is untouched.
    assert (root / "legs" / "good").exists()
    assert (root / "legs" / "bad").exists()


def test_rerun_cli_unknown_leg_errors_out(tmp_path: Path):
    root = tmp_path / "exp-root"
    root.mkdir()
    manifest = _manifest(
        [_leg("only", status=LegStatus.SUCCEEDED, result_status=LegResultStatus.ALL_FAILED)]
    )
    (root / "experiment.json").write_text(manifest.model_dump_json(), encoding="utf-8")
    (root / "config.resolved.yaml").write_text(
        "id: exp\ndataset: ds\nagents:\n  - only\n", encoding="utf-8"
    )

    runner = CliRunner()
    result = runner.invoke(experiments_app, ["rerun", str(root), "--leg", "nonexistent"])
    assert result.exit_code != 0
    assert "Unknown leg" in result.output


def test_rerun_cli_no_match_returns_zero(tmp_path: Path):
    root = tmp_path / "exp-root"
    root.mkdir()
    manifest = _manifest(
        [_leg("only", status=LegStatus.SUCCEEDED, result_status=LegResultStatus.ALL_PASSED)]
    )
    (root / "experiment.json").write_text(manifest.model_dump_json(), encoding="utf-8")
    (root / "config.resolved.yaml").write_text(
        "id: exp\ndataset: ds\nagents:\n  - only\n", encoding="utf-8"
    )

    runner = CliRunner()
    result = runner.invoke(experiments_app, ["rerun", str(root)])
    assert result.exit_code == 0
    assert "nothing to do" in result.output.lower()
