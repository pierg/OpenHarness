"""Tests for the Phase 3 creation/mutation web slices.

Covers four contracts:

1. The five new whitelist entries (``idea-append``, ``roadmap-add``,
   ``roadmap-suggest``, ``component-set-status``, ``component-upsert``)
   exist with the expected argv shape, vocabulary regexes, and event
   list — these are the form targets the templates rely on.
2. The optional-flag-group argv builder drops a group iff any
   placeholder inside it is missing — and includes the entire group
   when all are present.
3. The free-text validators reject leading hyphens (which Click could
   otherwise misinterpret as flags) and ASCII control characters,
   while still allowing newlines / tabs in long-text fields.
4. The /audit page surfaces the summary tally + filter form so the
   operator can narrow by command, actor, or status.

Doesn't actually run the underlying CLI — the existing
test_web_tree_apply suite covers a real subprocess round-trip; these
new entries are exercised via parameter validation only so the suite
stays fast.
"""

from __future__ import annotations

from openharness.lab.web import commands as labcmd


# ---------------------------------------------------------------------------
# Whitelist coverage
# ---------------------------------------------------------------------------


_NEW_CMDS = [
    "idea-append",
    "roadmap-add",
    "roadmap-suggest",
    "component-set-status",
    "component-upsert",
]


def test_new_commands_in_whitelist() -> None:
    for cid in _NEW_CMDS:
        assert cid in labcmd.COMMANDS, f"{cid} missing from whitelist"


def test_new_commands_emit_refresh_events() -> None:
    expected = {
        "idea-append":          {"lab-ideas-changed", "lab-pending-changed"},
        "roadmap-add":          {"lab-roadmap-changed", "lab-pending-changed"},
        "roadmap-suggest":      {"lab-roadmap-changed", "lab-pending-changed"},
        "component-set-status": {"lab-components-changed"},
        "component-upsert":     {"lab-components-changed"},
    }
    for cid, want in expected.items():
        events = set(labcmd.trigger_events(cid))
        assert want.issubset(events), f"{cid} missing events {want - events}"
        # Cross-cutting tag is always emitted.
        assert "lab-cmd-success" in events


# ---------------------------------------------------------------------------
# Optional flag group argv builder
# ---------------------------------------------------------------------------


def test_optional_group_dropped_when_param_missing() -> None:
    spec = labcmd.COMMANDS["roadmap-add"]
    params = labcmd._validate_params(spec, {
        "slug": "tb2-foo",
        "hypothesis": "h", "plan": "p",
        # idea / depends_on / cost intentionally absent
    })
    argv = labcmd._build_argv(spec, params)
    assert "--idea" not in argv
    assert "--depends-on" not in argv
    assert "--cost" not in argv
    # Required tokens still present.
    assert argv == ["roadmap", "add", "tb2-foo",
                    "--hypothesis", "h", "--plan", "p"]


def test_optional_group_included_when_param_present() -> None:
    spec = labcmd.COMMANDS["roadmap-add"]
    params = labcmd._validate_params(spec, {
        "slug": "tb2-foo", "hypothesis": "h", "plan": "p",
        "idea": "loop-guard", "depends_on": "a, b", "cost": "$5",
    })
    argv = labcmd._build_argv(spec, params)
    assert argv == ["roadmap", "add", "tb2-foo",
                    "--hypothesis", "h", "--plan", "p",
                    "--idea", "loop-guard",
                    "--depends-on", "a, b",
                    "--cost", "$5"]


def test_optional_group_partial_includes_only_resolvable() -> None:
    # Setting only `cost` (not idea/depends_on) must still emit --cost
    # but skip the other two — each group is independent.
    spec = labcmd.COMMANDS["roadmap-add"]
    params = labcmd._validate_params(spec, {
        "slug": "tb2-foo", "hypothesis": "h", "plan": "p",
        "cost": "$5",
    })
    argv = labcmd._build_argv(spec, params)
    assert "--cost" in argv and "$5" in argv
    assert "--idea" not in argv
    assert "--depends-on" not in argv


# ---------------------------------------------------------------------------
# Free text validators
# ---------------------------------------------------------------------------


def test_safe_text_rejects_leading_hyphen() -> None:
    assert labcmd._SAFE_TEXT.fullmatch("ok value") is not None
    assert labcmd._SAFE_TEXT.fullmatch("-flag-like") is None
    # Mid-string hyphens are fine.
    assert labcmd._SAFE_TEXT.fullmatch("a-b-c") is not None


def test_safe_text_rejects_control_characters() -> None:
    assert labcmd._SAFE_TEXT.fullmatch("hello\x00world") is None
    assert labcmd._SAFE_TEXT.fullmatch("bell\x07") is None
    # Tab and CR/LF are explicitly allowed because forms with textareas
    # routinely produce them.
    assert labcmd._SAFE_TEXT.fullmatch("hello\tworld") is not None
    assert labcmd._SAFE_LONG_TEXT.fullmatch("multi\nline\nok") is not None


def test_idea_theme_vocabulary_locked() -> None:
    for ok in ["Architecture", "Runtime", "Tools", "Memory"]:
        assert labcmd._IDEA_THEME.fullmatch(ok)
    for bad in ["architecture", "Other", "tools ", "", "Memory\n"]:
        assert not labcmd._IDEA_THEME.fullmatch(bad)


def test_component_status_vocabulary_locked() -> None:
    for ok in ["proposed", "experimental", "branch",
               "validated", "rejected", "superseded"]:
        assert labcmd._COMPONENT_STATUS.fullmatch(ok)
    for bad in ["Proposed", "graduated", "promoted", ""]:
        assert not labcmd._COMPONENT_STATUS.fullmatch(bad)


def test_used_by_csv_pattern() -> None:
    assert labcmd._USED_BY.fullmatch("agent-1") is not None
    assert labcmd._USED_BY.fullmatch("agent-1,agent-2,agent-3") is not None
    # No trailing comma, no space after comma in this strict variant.
    assert labcmd._USED_BY.fullmatch("agent-1,") is None
    assert labcmd._USED_BY.fullmatch("agent-1, agent-2") is None
    assert labcmd._USED_BY.fullmatch("") is None


# ---------------------------------------------------------------------------
# End-to-end form rendering (open mode → admin)
# ---------------------------------------------------------------------------


def _client():  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient

    from openharness.lab.web.app import create_app

    return TestClient(create_app())


def test_ideas_page_renders_create_form_in_open_mode() -> None:
    r = _client().get("/ideas")
    assert r.status_code == 200
    assert 'cmd_id" value="idea-append"' in r.text
    # And the per-card move buttons.
    assert 'cmd_id" value="idea-move"' in r.text


def test_roadmap_page_renders_both_create_forms() -> None:
    r = _client().get("/roadmap")
    assert r.status_code == 200
    assert 'cmd_id" value="roadmap-add"' in r.text
    assert 'cmd_id" value="roadmap-suggest"' in r.text


def test_components_page_renders_upsert_and_inline_status() -> None:
    r = _client().get("/components")
    assert r.status_code == 200
    assert 'cmd_id" value="component-upsert"' in r.text
    # The inline per-row select for force-setting status.
    assert 'cmd_id" value="component-set-status"' in r.text


def test_components_body_partial_used_for_autorefresh() -> None:
    r = _client().get("/_hx/components-body")
    assert r.status_code == 200
    # The partial returns table fragments only, no <html>/<body>.
    assert "<html" not in r.text
    assert "Used by" in r.text


# ---------------------------------------------------------------------------
# Audit page polish
# ---------------------------------------------------------------------------


def test_audit_page_summary_and_filters() -> None:
    c = _client()
    r = c.get("/audit")
    assert r.status_code == 200
    # Summary tally surface.
    assert "commands in recent window" in r.text
    # Filter form.
    for snippet in ['<select name="cmd"', '<select name="actor"',
                    '<select name="ok"']:
        assert snippet in r.text
    # Filter values are echoed back into selectors.
    r2 = c.get("/audit?ok=no")
    assert r2.status_code == 200
    # Empty matches still render a usable page (rather than 500).
    assert ("No matching rows" in r2.text) or ("non-zero exit" in r2.text)


def test_audit_filter_with_unknown_cmd_yields_empty_state() -> None:
    r = _client().get("/audit?cmd=this-cmd-does-not-exist")
    assert r.status_code == 200
    # The empty-state copy includes a reset link.
    assert "No matching rows" in r.text or "Reset filters" in r.text


# ---------------------------------------------------------------------------
# Defence-in-depth: form validation + 400 surface for the cmd endpoint
# ---------------------------------------------------------------------------


def test_api_cmd_rejects_unknown_cmd_id() -> None:
    r = _client().post("/api/cmd", data={"cmd_id": "not-a-real-cmd"})
    assert r.status_code == 400
    assert "unknown cmd_id" in r.text


def test_api_cmd_rejects_invalid_slug_for_idea_append() -> None:
    r = _client().post("/api/cmd", data={
        "cmd_id": "idea-append",
        "idea_id": "Has Spaces",
        "theme": "Tools",
        "motivation": "m",
        "sketch": "s",
    })
    assert r.status_code == 400
    assert "does not match required pattern" in r.text


def test_api_cmd_rejects_unknown_extras() -> None:
    r = _client().post("/api/cmd", data={
        "cmd_id": "idea-append",
        "idea_id": "ok",
        "theme": "Tools",
        "motivation": "m",
        "sketch": "s",
        "evil_param": "rm -rf /",
    })
    assert r.status_code == 400
    assert "unexpected param" in r.text


# Sanity: the open-mode auth still allows writes from loopback. We
# exercise this implicitly above (the smoke endpoints would 401/403
# otherwise) but make it explicit so a regression is unambiguous.
def test_open_mode_allows_admin_writes() -> None:
    from openharness.lab.web import auth as labauth

    # In open mode the configured_mode is "open" and the synthesized
    # identity claims admin-equivalent privileges on loopback.
    assert labauth.configured_mode() == "open"
    # Build a minimal request mock just enough for identify().
    class _Req:
        headers: dict[str, str] = {}
        client = type("C", (), {"host": "127.0.0.1"})()
    ident = labauth.identify(_Req())  # type: ignore[arg-type]
    assert ident.can_write is True
    assert labauth.check_write(ident) is None
