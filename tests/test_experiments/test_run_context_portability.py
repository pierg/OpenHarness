"""RunContext.as_manifest emits portable paths relative to the run dir."""

from __future__ import annotations

from pathlib import Path

from openharness.runs.context import RunContext


def test_as_manifest_paths_are_relative_to_run_dir(tmp_path: Path) -> None:
    run_dir = tmp_path / "legs" / "default" / "harbor" / "job" / "trial-1"
    run_dir.mkdir(parents=True)
    (run_dir / "agent").mkdir()

    ctx = RunContext.from_run_root(
        run_root=run_dir,
        interface="harbor",
        cwd="/app",
        run_id="trial-1",
        metadata={
            "harbor_logs_dir": str(run_dir / "agent"),
            "harbor_job_dir": str(run_dir.parent),
            "outside_path": "/somewhere/else",
        },
    )

    manifest = ctx.as_manifest()

    assert manifest["schema_version"] == 1
    paths = manifest["paths"]
    assert paths["anchor"] == "run_dir"
    assert paths["run_dir"] == "."
    assert paths["messages"] == "messages.jsonl"
    assert paths["events"] == "events.jsonl"
    assert paths["results"] == "results.json"
    assert paths["metrics"] == "metrics.json"
    assert paths["manifest"] == "run.json"

    metadata = manifest["metadata"]
    assert metadata["harbor_logs_dir"] == "agent"
    assert metadata["harbor_job_dir"] == ".."
    assert metadata["outside_path"] == "/somewhere/else"

    for key, value in paths.items():
        if key == "anchor" or value is None:
            continue
        assert not str(value).startswith("/"), f"{key}={value} is absolute"
