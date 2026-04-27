"""Tests for `components_doc` — the catalog of building-block atoms."""

from __future__ import annotations

from pathlib import Path

import pytest

from openharness.lab import components_doc as cdoc


SEED = """# Components

## Architecture

| ID | Status | Description | Used by | Evidence |
|----|--------|-------------|---------|----------|
| `single-loop` | validated | one model, no subagent | `basic` | [tb2](experiments.md#tb2) |
| `react-loop` | experimental | thought / action / observation | `react` | [tb2](experiments.md#tb2) |

## Runtime

| ID | Status | Description | Used by | Evidence |
|----|--------|-------------|---------|----------|
| `loop-guard` | proposed | detects no-progress turns | — | [idea](ideas.md#loop-guard) |

## Tools

_(none)_

## Prompt

_(none)_

## Model

| ID | Status | Description | Used by | Evidence |
|----|--------|-------------|---------|----------|
| `gemini-flash` | validated | default SKU | `basic`, `react` | [tb2](experiments.md#tb2) |
"""


@pytest.fixture
def lab_root(tmp_path: Path) -> Path:
    (tmp_path / "components.md").write_text(SEED)
    return tmp_path


def test_read_catalog_parses_seed(lab_root: Path) -> None:
    cat = cdoc.read_catalog(lab_root=lab_root)
    assert {k for k, v in cat.by_kind.items() if v} == {"Architecture", "Runtime", "Model"}
    arch = {e.component_id: e for e in cat.by_kind["Architecture"]}
    assert arch["single-loop"].status == "validated"
    assert arch["single-loop"].used_by == ["basic"]
    assert "tb2" in arch["single-loop"].evidence[0]
    assert arch["react-loop"].status == "experimental"
    runtime = {e.component_id: e for e in cat.by_kind["Runtime"]}
    assert runtime["loop-guard"].status == "proposed"
    assert runtime["loop-guard"].used_by == []
    model = {e.component_id: e for e in cat.by_kind["Model"]}
    assert set(model["gemini-flash"].used_by) == {"basic", "react"}


def test_round_trip_is_byte_stable(lab_root: Path) -> None:
    cat = cdoc.read_catalog(lab_root=lab_root)
    rendered = cdoc.render_catalog(cat)
    cdoc.write_catalog(cat, lab_root=lab_root)
    again = cdoc.read_catalog(lab_root=lab_root)
    assert cdoc.render_catalog(again) == rendered


def test_upsert_creates_new_entry(lab_root: Path) -> None:
    entry = cdoc.upsert(
        component_id="planner-executor",
        kind="Architecture",
        description="planner subagent + executor",
        status="experimental",
        lab_root=lab_root,
    )
    assert entry.status == "experimental"
    cat = cdoc.read_catalog(lab_root=lab_root)
    assert cat.find("planner-executor") is not None


def test_upsert_bumps_status_forward_only(lab_root: Path) -> None:
    cdoc.upsert(
        component_id="planner-executor",
        kind="Architecture",
        description="planner",
        status="experimental",
        lab_root=lab_root,
    )
    cdoc.upsert(
        component_id="planner-executor",
        kind="Architecture",
        status="validated",
        lab_root=lab_root,
    )
    cdoc.upsert(
        component_id="planner-executor",
        kind="Architecture",
        status="proposed",
        lab_root=lab_root,
    )
    e = cdoc.read_catalog(lab_root=lab_root).find("planner-executor")
    assert e is not None and e.status == "validated"


def test_upsert_refuses_kind_change(lab_root: Path) -> None:
    with pytest.raises(cdoc.LabDocError):
        cdoc.upsert(
            component_id="single-loop",
            kind="Runtime",
            lab_root=lab_root,
        )


def test_bump_status_terminal_is_sticky(lab_root: Path) -> None:
    cdoc.upsert(
        component_id="reflection-loop",
        kind="Architecture",
        description="critic loop",
        status="rejected",
        lab_root=lab_root,
    )
    cdoc.bump_status(
        component_id="reflection-loop",
        target="validated",
        lab_root=lab_root,
    )
    e = cdoc.read_catalog(lab_root=lab_root).find("reflection-loop")
    assert e is not None and e.status == "rejected"


def test_set_status_overrides(lab_root: Path) -> None:
    cdoc.set_status(
        component_id="single-loop",
        status="superseded",
        evidence="[explicit](ideas.md#x)",
        lab_root=lab_root,
    )
    e = cdoc.read_catalog(lab_root=lab_root).find("single-loop")
    assert e is not None and e.status == "superseded"
    assert "[explicit](ideas.md#x)" in e.evidence


def test_add_used_by_dedupes(lab_root: Path) -> None:
    cdoc.add_used_by(
        component_id="single-loop",
        agent_ids=["basic", "control"],
        lab_root=lab_root,
    )
    cdoc.add_used_by(
        component_id="single-loop",
        agent_ids=["basic", "control-prime"],
        lab_root=lab_root,
    )
    e = cdoc.read_catalog(lab_root=lab_root).find("single-loop")
    assert e is not None
    assert e.used_by == ["basic", "control", "control-prime"]


def test_unknown_status_rejected(lab_root: Path) -> None:
    with pytest.raises(cdoc.LabDocError):
        cdoc.upsert(
            component_id="single-loop",
            kind="Architecture",
            status="not-a-real-status",
            lab_root=lab_root,
        )
