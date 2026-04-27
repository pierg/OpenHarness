"""Tests for the evaluation-apply web slice.

Covers three contracts in one place:

1. ``LabReader.resolve_slug`` — mirrors the CLI's resolver; not running
   the resolver from the web UI was the original evaluation-apply UX
   blocker.
2. ``LabReader.preview_evaluation`` — returns ``None`` for unknown slugs
   (the only behaviour we depend on without a populated DB), otherwise
   a dict with the canonical evaluation fields.
3. ``commands.COMMANDS["evaluation-apply"]`` — argv template, param specs,
   and the events list (used by ``HX-Trigger`` to auto-refresh the
   evaluation panels).

Doesn't exercise an actual ``evaluation apply`` invocation because that
requires a populated experiments table; the integration check lives
in the project's smoke tests.
"""

from __future__ import annotations

import re

from openharness.lab.web import commands as labcmd
from openharness.lab.web import data as labdata


# ---------------------------------------------------------------------------
# commands.COMMANDS registry
# ---------------------------------------------------------------------------


def test_evaluation_apply_in_whitelist() -> None:
    spec = labcmd.COMMANDS.get("evaluation-apply")
    assert spec is not None, "evaluation-apply must be in the web command whitelist"
    assert spec.cmd_id == "evaluation-apply"
    # Must shell out to ``uv run lab evaluation apply <slug> --applied-by ...``.
    assert spec.argv_template[:2] == ["evaluation", "apply"]
    assert "{slug}" in spec.argv_template
    assert "--applied-by" in spec.argv_template
    assert "{applied_by}" in spec.argv_template


def test_evaluation_apply_param_specs() -> None:
    spec = labcmd.COMMANDS["evaluation-apply"]
    by_name = {p.name: p for p in spec.params}
    assert "slug" in by_name and "applied_by" in by_name
    # Slug regex must reject shell metacharacters.
    assert by_name["slug"].pattern.fullmatch("tb2-baseline-20260417-234913")
    assert not by_name["slug"].pattern.fullmatch("foo;rm -rf /")
    assert not by_name["slug"].pattern.fullmatch("foo bar")
    # ``applied_by`` defaults to ``human:webui`` so the form doesn't
    # need to send it explicitly.
    assert by_name["applied_by"].default == "human:webui"


def test_evaluation_apply_emits_refresh_events() -> None:
    events = labcmd.trigger_events("evaluation-apply")
    # Config verdict panel + backlog suggested-list refresh.
    assert "lab-configs-changed" in events
    assert "lab-pending-changed" in events
    assert "lab-roadmap-changed" in events
    # And the cross-cutting tag is always appended last.
    assert events[-1] == "lab-cmd-success"


# ---------------------------------------------------------------------------
# data.LabReader.resolve_slug + preview_evaluation
# ---------------------------------------------------------------------------


def test_resolve_slug_returns_none_when_db_missing(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    # Force the reader into the "no DB available" branch by pointing
    # the lab DB path at a non-existent location.
    nonexistent = tmp_path / "missing.duckdb"
    monkeypatch.setattr(labdata, "LAB_DB_PATH", nonexistent)
    with labdata.LabReader() as r:
        assert r.db_available is False
        assert r.resolve_slug("anything") is None
        assert r.preview_evaluation("anything") is None


def test_preview_evaluation_unknown_slug_against_real_db() -> None:
    # The repo's lab DB exists in dev; if not, this test no-ops cleanly
    # so contributors without a populated lab can still run the suite.
    with labdata.LabReader() as r:
        if not r.db_available:
            return
        out = r.preview_evaluation("definitely-not-a-real-slug-zzz")
        assert out is None, (
            "preview_evaluation must return None for unresolvable slugs so the "
            "template can render a 'no experiment found' message instead "
            "of a confusing empty diff"
        )


def test_preview_evaluation_known_slug_shape() -> None:
    # If the DB has at least one experiment with a critic evaluation,
    # preview_evaluation must echo the canonical dict plus slug + instance id.
    with labdata.LabReader() as r:
        if not r.db_available:
            return
        exps = r.experiments(limit=1)
        if not exps:
            return
        instance_id = exps[0].instance_id
        out = r.preview_evaluation(instance_id)
        if out is None:
            return
        # Canonical evaluation fields.
        for field in (
            "verdict",
            "target_id",
            "rationale",
            "confidence",
            "evidence_paths",
            "cluster_evidence",
        ):
            assert field in out, f"preview_evaluation missing evaluation field {field!r}"
        # Web-only echo fields.
        assert out["slug"] == instance_id
        assert out["resolved_instance_id"] == instance_id
        # Verdict must be one of the documented evaluation labels.
        assert out["verdict"] in {"accept", "reject", "no_op"}


# ---------------------------------------------------------------------------
# Render path: /_hx/evaluation-preview must return well-formed HTML
# ---------------------------------------------------------------------------


def test_evaluation_preview_partial_renders_unknown_slug() -> None:
    from fastapi.testclient import TestClient

    from openharness.lab.web.app import create_app

    client = TestClient(create_app())
    r = client.get("/_hx/evaluation-preview", params={"slug": "totally-bogus-slug-xx"})
    assert r.status_code == 200
    body = r.text
    assert "No experiment found" in body
    # Anchor that the rewriter ran through render() — bogus slug must
    # appear inside <code>.
    assert re.search(r"<code[^>]*>totally-bogus-slug-xx", body)


def test_catalog_configs_page_includes_evaluation_panel() -> None:
    from fastapi.testclient import TestClient

    from openharness.lab.web.app import create_app

    client = TestClient(create_app())
    r = client.get("/catalog?tab=configs")
    assert r.status_code == 200
    # The whole evaluation surface lives inside this id so HTMX can target
    # it for in-place refresh on lab-configs-changed.
    assert 'id="config-evaluation-panel"' in r.text
    assert 'id="evaluation-preview"' in r.text
