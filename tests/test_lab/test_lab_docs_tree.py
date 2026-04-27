"""Tests for config-state helpers in `lab_docs`.

Covers:
- `set_section` / `get_section` round-trip on a stub journal entry.
- `append_journal_entry` produces the canonical 5-section shell.
- `tree_snapshot` parses Operational baseline + Rejected.
- `add_rejected` / `set_operational_baseline` are idempotent.
- `add_suggested_followup` and `promote_suggested` round-trip.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest

from openharness.lab import lab_docs


@pytest.fixture
def lab_root(tmp_path: Path) -> Path:
    """Build a minimal lab/ directory the helpers can mutate."""
    (tmp_path / "experiments.md").write_text(
        "# Experiments\n\nPreamble.\n"
    )
    (tmp_path / "configs.md").write_text(
        "# Configs\n\nPreamble.\n"
    )
    (tmp_path / "components.md").write_text(
        "# Components\n\nPreamble.\n"
    )
    (tmp_path / "roadmap.md").write_text(
        "# Roadmap\n\n## Up next\n\n_(none)_\n\n## Done\n\n_(none)_\n"
    )
    return tmp_path


# ---- experiments.md journal entries --------------------------------------


def test_append_journal_entry_creates_canonical_shell(lab_root: Path) -> None:
    lab_docs.append_journal_entry(
        slug="foo-bar",
        type_="paired ablation",
        baseline_at_runtime="basic",
        mutation="planner_executor",
        hypothesis="planner helps on multi-file tasks.",
        run_path="runs/experiments/foo-bar-2026",
        on_date=date(2026, 4, 18),
        lab_root=lab_root,
    )
    text = (lab_root / "experiments.md").read_text()
    assert "## 2026-04-18 — foo-bar" in text
    for s in lab_docs.JOURNAL_SECTIONS:
        assert f"### {s}" in text


def test_append_journal_entry_rejects_duplicate(lab_root: Path) -> None:
    lab_docs.append_journal_entry(
        slug="foo-bar",
        type_="paired",
        baseline_at_runtime="basic",
        mutation=None,
        hypothesis="x",
        run_path=None,
        on_date=date(2026, 4, 18),
        lab_root=lab_root,
    )
    with pytest.raises(lab_docs.LabDocError):
        lab_docs.append_journal_entry(
            slug="foo-bar",
            type_="paired",
            baseline_at_runtime="basic",
            mutation=None,
            hypothesis="x",
            run_path=None,
            on_date=date(2026, 4, 18),
            lab_root=lab_root,
        )


def test_set_section_inserts_in_canonical_order(lab_root: Path) -> None:
    lab_docs.append_journal_entry(
        slug="x", type_="paired", baseline_at_runtime="basic",
        mutation=None, hypothesis="h", run_path=None,
        on_date=date(2026, 4, 18), lab_root=lab_root,
    )
    lab_docs.set_section(
        slug="x", section="Experiment evaluation",
        body="-   **Verdict:** accept",
        lab_root=lab_root,
    )
    body = lab_docs.get_section(
        slug="x", section="Experiment evaluation", lab_root=lab_root
    )
    assert body is not None
    assert "**Verdict:** accept" in body


def test_set_section_replaces_existing(lab_root: Path) -> None:
    lab_docs.append_journal_entry(
        slug="x", type_="paired", baseline_at_runtime="basic",
        mutation=None, hypothesis="h", run_path=None,
        on_date=date(2026, 4, 18), lab_root=lab_root,
    )
    lab_docs.set_section(
        slug="x", section="Aggregate", body="first", lab_root=lab_root,
    )
    lab_docs.set_section(
        slug="x", section="Aggregate", body="second", lab_root=lab_root,
    )
    body = lab_docs.get_section(
        slug="x", section="Aggregate", lab_root=lab_root
    )
    assert body == "second"
    assert (lab_root / "experiments.md").read_text().count("### Aggregate") == 1


def test_set_section_missing_entry_raises(lab_root: Path) -> None:
    with pytest.raises(lab_docs.LabDocError):
        lab_docs.set_section(
            slug="nope", section="Aggregate", body="x", lab_root=lab_root,
        )


# ---- configs.md tree CRUD ------------------------------------------------


def test_tree_snapshot_bootstraps_empty_skeleton(lab_root: Path) -> None:
    snap = lab_docs.tree_snapshot(lab_root=lab_root)
    assert snap.operational_baseline_id == "basic"
    assert snap.proposed == []
    assert snap.rejected == []


def test_set_operational_baseline_then_snapshot_roundtrips(lab_root: Path) -> None:
    lab_docs.set_operational_baseline(
        agent_id="planner_executor",
        reason="best on multi-file tasks",
        journal_link="[`x`](experiments.md#x)",
        lab_root=lab_root,
    )
    snap = lab_docs.tree_snapshot(lab_root=lab_root)
    assert snap.operational_baseline_id == "planner_executor"
    assert snap.operational_baseline_anchor and "best on multi-file tasks" in snap.operational_baseline_anchor


def test_add_rejected_appears_in_snapshot(lab_root: Path) -> None:
    lab_docs.add_rejected(
        branch_id="reflection",
        reason="context blowup; >500k tokens/trial",
        evidence="experiments.md#reflection-context",
        lab_root=lab_root,
    )
    snap = lab_docs.tree_snapshot(lab_root=lab_root)
    assert any(r.branch_id == "reflection" for r in snap.rejected)


# ---- roadmap suggested / promote -----------------------------------------


def test_add_suggested_then_promote(lab_root: Path) -> None:
    lab_docs.add_suggested_followup(
        slug="loop-guard-v2",
        hypothesis="add a stronger no-progress guard",
        source="cross-experiment-critic@2026-04-18",
        cost="smoke ~$0.50",
        lab_root=lab_root,
    )
    text = (lab_root / "roadmap.md").read_text()
    assert "### Suggested" in text
    assert "#### loop-guard-v2" in text
    assert "cross-experiment-critic" in text

    lab_docs.promote_suggested(slug="loop-guard-v2", lab_root=lab_root)
    text2 = (lab_root / "roadmap.md").read_text()
    assert "### loop-guard-v2" in text2
    assert "#### loop-guard-v2" not in text2


def test_promote_unknown_raises(lab_root: Path) -> None:
    with pytest.raises(lab_docs.LabDocError):
        lab_docs.promote_suggested(slug="nope", lab_root=lab_root)


def test_append_idea_rejects_unknown_theme(lab_root: Path) -> None:
    (lab_root / "ideas.md").write_text("# Ideas\n\n## Proposed\n\n_(none)_\n")

    with pytest.raises(lab_docs.LabDocError, match="Unknown idea theme"):
        lab_docs.append_idea(
            idea_id="bad-theme",
            theme="Framework",
            motivation="x",
            sketch="y",
            lab_root=lab_root,
        )


def test_repository_ideas_use_known_proposed_themes() -> None:
    text = (lab_docs.LAB_ROOT / "ideas.md").read_text()
    proposed = text.split("## Proposed", 1)[1].split("## Trying", 1)[0]
    themes = set(re.findall(r"^### (.+)$", proposed, flags=re.MULTILINE))
    assert themes.issubset(set(lab_docs.VALID_THEMES))


def test_add_suggested_replaces_existing_slug(lab_root: Path) -> None:
    lab_docs.add_suggested_followup(
        slug="x", hypothesis="first", source="a", lab_root=lab_root,
    )
    lab_docs.add_suggested_followup(
        slug="x", hypothesis="second", source="b", lab_root=lab_root,
    )
    text = (lab_root / "roadmap.md").read_text()
    assert text.count("#### x") == 1
    assert "second" in text
    assert "first" not in text


def test_demote_suggested_heading_is_not_allowed(lab_root: Path) -> None:
    (lab_root / "roadmap.md").write_text(
        "# Roadmap\n\n"
        "## Up next\n\n"
        "### runnable\n\n"
        "-   **Hypothesis:** run me\n\n"
        "### Suggested\n\n"
        "#### later\n\n"
        "-   **Hypothesis:** later\n\n"
        "## Done\n\n"
        "_(none)_\n"
    )

    with pytest.raises(lab_docs.LabDocError):
        lab_docs.demote_to_suggested(slug="Suggested", lab_root=lab_root)

    text = (lab_root / "roadmap.md").read_text()
    assert text.count("### Suggested") == 1
    assert "#### Suggested" not in text
