"""Tests for applying simplified experiment evaluations."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

from openharness.lab import lab_docs
from openharness.lab import tree as tree_mod
from openharness.lab.evaluation import ExperimentEvaluation


@pytest.fixture
def lab_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "experiments.md").write_text("# Experiments\n\nPreamble.\n")
    (tmp_path / "configs.md").write_text(
        "# Configs\n\n"
        "## Operational baseline\n\n"
        "-   **Agent:** [`basic`](../src/openharness/agents/configs/basic.yaml)\n"
        "-   **Why:** baseline\n\n"
        "## Rejected\n\n_(none)_\n\n"
        "## Proposed\n\n_(none)_\n"
    )
    (tmp_path / "components.md").write_text("# Components\n\nPreamble.\n")
    (tmp_path / "roadmap.md").write_text(
        "# Roadmap\n\n## Up next\n\n_(none)_\n\n## Done\n\n_(none)_\n"
    )
    monkeypatch.setattr(lab_docs, "LAB_ROOT", tmp_path)
    return tmp_path


def _make_evaluation(verdict: str, **overrides: object) -> ExperimentEvaluation:
    base = dict(
        verdict=verdict,
        target_id="planner_executor",
        rationale="positive on multi_file tasks",
        evidence_paths=[Path("/tmp/x.json")],
        confidence=0.8,
        instance_id="exp-1",
        baseline_leg="basic",
        candidate_leg="planner_executor",
        promotability_notes="uses runtime-visible signals only",
        cluster_evidence=[{"cluster": "multi_file", "summary": "candidate won"}],
    )
    base.update(overrides)
    return ExperimentEvaluation(**base)


def test_render_evaluation_block_includes_verdict_badge() -> None:
    evaluation = _make_evaluation("accept")
    out = tree_mod.render_evaluation_block(evaluation, slug="x")
    assert "Accept" in out
    assert "dynamic leaderboard" in out
    assert "`planner_executor`" in out
    assert "multi_file" in out


def test_apply_accept_records_evaluation_without_baseline_mutation(
    lab_root: Path,
) -> None:
    lab_docs.append_journal_entry(
        slug="accept-planner",
        type_="paired ablation",
        baseline_at_runtime="basic",
        mutation="planner_executor",
        hypothesis="planner helps",
        run_path=None,
        on_date=date(2026, 4, 18),
        lab_root=lab_root,
    )

    evaluation = _make_evaluation("accept")
    with patch.object(tree_mod, "labdb") as mock_db:
        mock_db.writer.side_effect = RuntimeError("no DB in test")
        result = tree_mod.apply_evaluation(
            slug="accept-planner",
            evaluation=evaluation,
            lab_root=lab_root,
        )

    assert result.applied is True
    assert result.journal_block_written is True

    snap = lab_docs.tree_snapshot(lab_root=lab_root)
    assert snap.operational_baseline_id == "basic"

    journal = (lab_root / "experiments.md").read_text()
    assert "### Experiment evaluation" in journal
    assert "Accept" in journal


def test_apply_reject_appends_to_rejected(lab_root: Path) -> None:
    lab_docs.append_journal_entry(
        slug="rej-x",
        type_="paired",
        baseline_at_runtime="basic",
        mutation="bad_thing",
        hypothesis="x",
        run_path=None,
        on_date=date(2026, 4, 18),
        lab_root=lab_root,
    )

    evaluation = _make_evaluation("reject", target_id="bad_thing", confidence=1.0)
    with patch.object(tree_mod, "labdb") as mock_db:
        mock_db.writer.side_effect = RuntimeError("no DB in test")
        result = tree_mod.apply_evaluation(
            slug="rej-x",
            evaluation=evaluation,
            lab_root=lab_root,
        )

    assert result.applied is True
    snap = lab_docs.tree_snapshot(lab_root=lab_root)
    assert any(r.branch_id == "bad_thing" for r in snap.rejected)


def test_apply_no_op_writes_journal_only(lab_root: Path) -> None:
    lab_docs.append_journal_entry(
        slug="noop-x",
        type_="paired",
        baseline_at_runtime="basic",
        mutation="planner_executor",
        hypothesis="no signal",
        run_path=None,
        on_date=date(2026, 4, 18),
        lab_root=lab_root,
    )

    evaluation = _make_evaluation("no_op", confidence=0.1)
    with patch.object(tree_mod, "labdb") as mock_db:
        mock_db.writer.side_effect = RuntimeError("no DB in test")
        result = tree_mod.apply_evaluation(
            slug="noop-x",
            evaluation=evaluation,
            lab_root=lab_root,
        )

    assert result.applied is True
    snap = lab_docs.tree_snapshot(lab_root=lab_root)
    assert snap.rejected == []
    journal = (lab_root / "experiments.md").read_text()
    assert "No-op" in journal
