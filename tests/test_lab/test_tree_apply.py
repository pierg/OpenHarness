"""Tests for `tree.apply_diff` and `tree.render_tree_effect_block`.

We don't touch the DB cache table here (it requires the lab DB to
be initialised); we cover the markdown side and the rendering.
DB-cache assertions live in `test_ingest.py` integration tests.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

from openharness.lab import lab_docs
from openharness.lab import tree as tree_mod
from openharness.lab.tree_ops import TreeDiff


@pytest.fixture
def lab_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "experiments.md").write_text("# Experiments\n\nPreamble.\n")
    (tmp_path / "configs.md").write_text("# Configs\n\nPreamble.\n")
    (tmp_path / "components.md").write_text("# Components\n\nPreamble.\n")
    (tmp_path / "roadmap.md").write_text(
        "# Roadmap\n\n## Up next\n\n_(none)_\n\n## Done\n\n_(none)_\n"
    )
    monkeypatch.setattr(lab_docs, "LAB_ROOT", tmp_path)
    return tmp_path


def _make_diff(kind: str, **overrides) -> TreeDiff:
    base = dict(
        kind=kind,
        target_id="planner_executor",
        rationale="positive on multi_file cluster",
        evidence_paths=[Path("/tmp/x.json")],
        use_when={"any_of": [{"task_features.category": "multi_file"}]},
        confidence=0.8,
        instance_id="exp-1",
        trunk_leg="basic",
        mutation_leg="planner_executor",
        pass_rate_delta_pp=4.5,
        cost_per_pass_delta_pct=12.0,
        cluster_evidence=[{
            "cluster": "multi_file", "trunk_n": 10, "trunk_pass": 2,
            "mut_n": 10, "mut_pass": 6, "delta_pp": 40.0,
            "trunk_pass_rate": 0.2, "mut_pass_rate": 0.6,
        }],
    )
    base.update(overrides)
    return TreeDiff(**base)


def test_render_tree_effect_block_includes_verdict_badge() -> None:
    diff = _make_diff("add_branch")
    out = tree_mod.render_tree_effect_block(diff, slug="x", applied=True)
    assert "Add branch" in out
    assert "auto-applied" in out
    assert "`planner_executor`" in out
    assert "Δ pass-rate" in out
    assert "+4.50 pp" in out
    assert "multi_file" in out


def test_render_graduate_staged_vs_applied_differs() -> None:
    diff = _make_diff("graduate")
    staged = tree_mod.render_tree_effect_block(diff, slug="x", applied=False)
    applied = tree_mod.render_tree_effect_block(diff, slug="x", applied=True)
    assert "STAGED" in staged
    assert "STAGED" not in applied
    assert "APPLIED" in applied


def test_apply_add_branch_writes_configs_and_journal(
    lab_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lab_docs.append_journal_entry(
        slug="add-planner",
        type_="paired ablation",
        trunk_at_runtime="basic",
        mutation="planner_executor",
        hypothesis="planner helps",
        run_path=None,
        on_date=date(2026, 4, 18),
        lab_root=lab_root,
    )

    diff = _make_diff("add_branch")
    with patch.object(tree_mod, "labdb") as mock_db:
        mock_db.writer.side_effect = RuntimeError("no DB in test")
        result = tree_mod.apply_diff(
            slug="add-planner", diff=diff, lab_root=lab_root,
        )

    assert result.applied is True
    assert result.applied_by == "auto:daemon"
    assert result.journal_block_written is True

    snap = lab_docs.tree_snapshot(lab_root=lab_root)
    assert any(b.branch_id == "planner_executor" for b in snap.branches)

    journal = (lab_root / "experiments.md").read_text()
    assert "### Tree effect" in journal
    assert "Add branch" in journal


def test_apply_add_branch_bumps_unique_components(
    lab_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When `planner_executor` becomes a branch over trunk `basic`, the
    `planner-executor` architecture component should be bumped to `branch`,
    while `single-loop` (in trunk) and `gemini-...` (in both) should NOT
    be touched (forward-only, no demotions).
    """
    (lab_root / "components.md").write_text(
        "# Components\n\n"
        "## Architecture\n\n"
        "| ID | Status | Description | Used by | Evidence |\n"
        "|----|--------|-------------|---------|----------|\n"
        "| `single-loop` | validated | one model | `basic` | [tb2](experiments.md#tb2) |\n"
        "| `planner-executor` | proposed | planner subagent | — | — |\n\n"
        "## Runtime\n\n_(none)_\n\n"
        "## Tools\n\n_(none)_\n\n"
        "## Prompt\n\n_(none)_\n\n"
        "## Model\n\n"
        "| ID | Status | Description | Used by | Evidence |\n"
        "|----|--------|-------------|---------|----------|\n"
        "| `gemini-3.1-flash-lite-preview` | validated | default | `basic` | [tb2](experiments.md#tb2) |\n"
    )
    lab_docs.append_journal_entry(
        slug="add-planner-bump", type_="paired", trunk_at_runtime="basic",
        mutation="planner_executor", hypothesis="x", run_path=None,
        on_date=date(2026, 4, 18), lab_root=lab_root,
    )

    diff = _make_diff("add_branch")
    with patch.object(tree_mod, "labdb") as mock_db:
        mock_db.writer.side_effect = RuntimeError("no DB in test")
        tree_mod.apply_diff(
            slug="add-planner-bump", diff=diff, lab_root=lab_root,
        )

    from openharness.lab import components_doc as cdoc
    cat = cdoc.read_catalog(lab_root=lab_root)
    pe = cat.find("planner-executor")
    sl = cat.find("single-loop")
    gm = cat.find("gemini-3.1-flash-lite-preview")
    assert pe is not None and pe.status == "branch"
    assert sl is not None and sl.status == "validated"  # not demoted
    assert gm is not None and gm.status == "validated"  # not demoted


def test_apply_reject_appends_to_rejected(
    lab_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lab_docs.append_journal_entry(
        slug="rej-x", type_="paired", trunk_at_runtime="basic",
        mutation="bad_thing", hypothesis="x", run_path=None,
        on_date=date(2026, 4, 18), lab_root=lab_root,
    )

    diff = _make_diff("reject", target_id="bad_thing", confidence=1.0,
                     pass_rate_delta_pp=-10.0, cost_per_pass_delta_pct=80.0)
    with patch.object(tree_mod, "labdb") as mock_db:
        mock_db.writer.side_effect = RuntimeError("no DB in test")
        result = tree_mod.apply_diff(slug="rej-x", diff=diff, lab_root=lab_root)

    assert result.applied is True
    snap = lab_docs.tree_snapshot(lab_root=lab_root)
    assert any(r.branch_id == "bad_thing" for r in snap.rejected)


def test_apply_graduate_stages_only(
    lab_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lab_docs.append_journal_entry(
        slug="grad-x", type_="paired", trunk_at_runtime="basic",
        mutation="planner_executor", hypothesis="big win",
        run_path=None, on_date=date(2026, 4, 18), lab_root=lab_root,
    )

    diff = _make_diff("graduate", pass_rate_delta_pp=10.0)
    with patch.object(tree_mod, "labdb") as mock_db:
        mock_db.writer.side_effect = RuntimeError("no DB in test")
        result = tree_mod.apply_diff(slug="grad-x", diff=diff, lab_root=lab_root)

    assert result.applied is False
    assert result.applied_by == "proposed"

    snap = lab_docs.tree_snapshot(lab_root=lab_root)
    # Trunk should NOT have changed; we only staged the verdict.
    assert snap.trunk_id != "planner_executor" or snap.trunk_id == "basic"

    journal = (lab_root / "experiments.md").read_text()
    assert "STAGED" in journal


def test_apply_no_op_writes_journal_only(
    lab_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lab_docs.append_journal_entry(
        slug="noop-x", type_="paired", trunk_at_runtime="basic",
        mutation="planner_executor", hypothesis="no signal",
        run_path=None, on_date=date(2026, 4, 18), lab_root=lab_root,
    )

    diff = _make_diff("no_op", confidence=0.1, pass_rate_delta_pp=0.5)
    with patch.object(tree_mod, "labdb") as mock_db:
        mock_db.writer.side_effect = RuntimeError("no DB in test")
        result = tree_mod.apply_diff(slug="noop-x", diff=diff, lab_root=lab_root)

    assert result.applied is True
    snap = lab_docs.tree_snapshot(lab_root=lab_root)
    assert snap.branches == []
    assert snap.rejected == []
    journal = (lab_root / "experiments.md").read_text()
    assert "No-op" in journal
