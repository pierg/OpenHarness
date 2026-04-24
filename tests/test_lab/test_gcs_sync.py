"""Tests for portable GCS mirroring of lab run artifacts."""

from __future__ import annotations

import importlib
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def isolated_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create an isolated repo root and reload lab modules against it."""
    repo = tmp_path / "repo"
    (repo / "lab").mkdir(parents=True)
    (repo / "runs" / "lab").mkdir(parents=True)
    (repo / "pyproject.toml").write_text("# placeholder for repo-detection\n")

    monkeypatch.setenv("OPENHARNESS_REPO_ROOT", str(repo))

    import openharness.lab.paths as paths
    importlib.reload(paths)
    import openharness.lab.ingest as ingest
    importlib.reload(ingest)
    import openharness.lab.gcs_sync as gcs_sync
    importlib.reload(gcs_sync)

    return repo


def test_portable_targets_only_include_safe_subtrees(isolated_repo: Path) -> None:
    """The mirror excludes live DuckDB/lock/state paths by construction."""
    import openharness.lab.gcs_sync as gcs_sync

    targets = gcs_sync.portable_targets(include_lab_wide=True)
    rel_paths = {
        target.local_path.relative_to(isolated_repo).as_posix()
        for target in targets
    }

    assert rel_paths == {
        "runs/experiments",
        "runs/lab/task_features",
        "runs/lab/cross_experiment",
        "runs/lab/components_perf",
        "runs/lab/auto_proposed",
        "runs/lab/spawns",
    }
    assert "runs/lab/trials.duckdb" not in rel_paths
    assert "runs/lab/state" not in rel_paths
    assert "runs/lab/logs" not in rel_paths


def test_sync_portable_runs_push_invokes_gcloud_for_existing_targets(
    isolated_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Push only shells out for subtrees that actually exist on disk."""
    import openharness.lab.gcs_sync as gcs_sync

    run_dir = isolated_repo / "runs" / "experiments" / "exp-123"
    run_dir.mkdir(parents=True)
    (run_dir / "experiment.json").write_text("{}\n")

    task_features = isolated_repo / "runs" / "lab" / "task_features"
    task_features.mkdir(parents=True)
    (task_features / "abc.json").write_text("{}\n")

    calls: list[list[str]] = []

    def fake_run(cmd: list[str], check: bool) -> subprocess.CompletedProcess[str]:
        assert check is True
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(gcs_sync.subprocess, "run", fake_run)

    summary = gcs_sync.sync_portable_runs(
        direction="push",
        uri="gs://bucket/root",
        instance_id="exp-123",
        include_lab_wide=True,
    )

    assert summary.synced == (
        "experiment run exp-123",
        "lab task_features",
    )
    assert "lab cross_experiment snapshots" in summary.skipped
    assert len(calls) == 2
    assert calls[0] == [
        "gcloud",
        "storage",
        "rsync",
        str(run_dir),
        "gs://bucket/root/experiments/exp-123",
        "--recursive",
        "--skip-if-dest-has-newer-mtime",
    ]
    assert calls[1] == [
        "gcloud",
        "storage",
        "rsync",
        str(task_features),
        "gs://bucket/root/lab/task_features",
        "--recursive",
        "--skip-if-dest-has-newer-mtime",
    ]


def test_refresh_local_cache_uses_only_experiment_dirs_with_manifests(
    isolated_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cache refresh scans portable experiment dirs and lab-wide critic files."""
    import openharness.lab.gcs_sync as gcs_sync

    ready = isolated_repo / "runs" / "experiments" / "ready-run"
    ready.mkdir(parents=True)
    (ready / "experiment.json").write_text("{}\n")

    ignored = isolated_repo / "runs" / "experiments" / "missing-manifest"
    ignored.mkdir(parents=True)

    captured: dict[str, object] = {}

    def fake_ingest_runs(run_dirs: list[Path]) -> list[SimpleNamespace]:
        captured["run_dirs"] = list(run_dirs)
        return [
            SimpleNamespace(
                legs_inserted=2,
                trials_inserted=7,
                trials_skipped=1,
                misconfigurations=3,
            )
        ]

    def fake_ingest_critiques(
        run_dirs: list[Path] | None,
        *,
        include_lab_wide: bool,
    ) -> dict[str, int]:
        captured["critic_run_dirs"] = None if run_dirs is None else list(run_dirs)
        captured["include_lab_wide"] = include_lab_wide
        return {
            "trial_critiques": 11,
            "comparisons": 5,
            "experiment_critic_files": 1,
            "task_features": 9,
            "components_perf": 4,
            "spawns": 3,
        }

    monkeypatch.setattr(gcs_sync.labingest, "ingest_runs", fake_ingest_runs)
    monkeypatch.setattr(gcs_sync.labingest, "ingest_critiques", fake_ingest_critiques)

    summary = gcs_sync.refresh_local_cache()

    assert captured["run_dirs"] == [ready.resolve()]
    assert captured["critic_run_dirs"] == [ready.resolve()]
    assert captured["include_lab_wide"] is True
    assert summary.runs_ingested == 1
    assert summary.legs_inserted == 2
    assert summary.trials_inserted == 7
    assert summary.trials_skipped == 1
    assert summary.misconfigurations == 3
    assert summary.trial_critiques == 11
    assert summary.comparisons == 5
    assert summary.experiment_critic_files == 1
    assert summary.task_features == 9
    assert summary.components_perf == 4
    assert summary.spawns == 3


def test_runs_pull_gcs_cli_reports_refresh_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CLI wires download + cache refresh through the shared module."""
    from typer.testing import CliRunner

    import openharness.lab.cli as cli

    def fake_sync_portable_runs(
        *,
        direction: str,
        uri: str | None,
        instance_id: str | None,
        include_lab_wide: bool,
        dry_run: bool,
        delete_unmatched: bool,
    ) -> object:
        assert direction == "pull"
        assert uri == "gs://bucket/root"
        assert instance_id is None
        assert include_lab_wide is True
        assert dry_run is False
        assert delete_unmatched is False
        return cli.gcs_sync.SyncSummary(
            direction="pull",
            uri="gs://bucket/root",
            synced=("all experiment runs",),
            skipped=("lab spawns",),
        )

    def fake_refresh_local_cache() -> object:
        return cli.gcs_sync.CacheRefreshSummary(
            runs_ingested=3,
            legs_inserted=6,
            trials_inserted=21,
            trials_skipped=0,
            misconfigurations=0,
            trial_critiques=12,
            comparisons=4,
            experiment_critic_files=2,
            task_features=8,
            components_perf=5,
            spawns=9,
        )

    monkeypatch.setattr(cli.gcs_sync, "sync_portable_runs", fake_sync_portable_runs)
    monkeypatch.setattr(cli.gcs_sync, "refresh_local_cache", fake_refresh_local_cache)

    runner = CliRunner()
    result = runner.invoke(cli.app, ["runs", "pull-gcs", "--uri", "gs://bucket/root"])

    assert result.exit_code == 0
    assert "pull-gcs: uri=gs://bucket/root" in result.output
    assert "synced : all experiment runs" in result.output
    assert "skipped: lab spawns" in result.output
    assert "cache   : runs=3 legs=6 trials=21" in result.output
