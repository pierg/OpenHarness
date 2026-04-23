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


def test_ideas_page_no_longer_renders_raw_input_form() -> None:
    # IA redesign: raw user input forms removed. Ideas land via CLI/skill
    # path (`uv run lab idea-append`); the UI shows a read-only catalog
    # plus a "where to add ideas" notice.
    r = _client().get("/ideas")
    assert r.status_code == 200
    assert 'cmd_id" value="idea-append"' not in r.text
    # Per-card actions (move) survive — they're one-click confirmations,
    # not free-form input.
    assert 'cmd_id" value="idea-move"' in r.text
    # The replacement notice is present.
    assert "lab-propose-idea" in r.text or "idea-append" in r.text


def test_roadmap_page_no_longer_renders_raw_input_forms() -> None:
    # IA redesign: roadmap-add / roadmap-suggest forms removed. Operators
    # use the CLI / `lab-plan-next` skill, or one-click promote/discard
    # on the home-page "You owe" zone.
    r = _client().get("/roadmap")
    assert r.status_code == 200
    assert 'cmd_id" value="roadmap-add"' not in r.text
    assert 'cmd_id" value="roadmap-suggest"' not in r.text
    assert "lab-plan-next" in r.text or "roadmap-add" in r.text


def test_components_page_no_longer_renders_upsert_or_inline_status() -> None:
    # IA redesign: component-upsert and the inline per-row
    # component-set-status select were removed. The catalog is read-only.
    r = _client().get("/components")
    assert r.status_code == 200
    assert 'cmd_id" value="component-upsert"' not in r.text
    assert 'cmd_id" value="component-set-status"' not in r.text
    assert "lab-implement-variant" in r.text or "components" in r.text


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


# ---------------------------------------------------------------------------
# IA redesign smoke tests
# ---------------------------------------------------------------------------
#
# After the 6-page IA redesign, the following surfaces should all
# render with HTTP 200 and contain the expected anchor text. These are
# pure smoke tests — they do not assert on layout — but they catch
# template-undefined errors (the most common regression after a route
# refactor) and protect the navigation contract.


def test_home_page_renders_three_zones_and_pr_aware_idle_reason() -> None:
    r = _client().get("/")
    assert r.status_code == 200
    body = r.text
    # The three zones the operator sees first.
    assert "Now" in body
    # Zone anchors used by the "You owe" pill in the header.
    assert 'id="you-owe"' in body or "You owe" in body
    # HTMX partials mounted on first render.
    assert "/_hx/idle-reason" in body
    assert "/_hx/you-owe" in body


def test_idle_reason_partial_renders_a_palette() -> None:
    r = _client().get("/_hx/idle-reason")
    assert r.status_code == 200
    # One of the operational states should be reflected somewhere.
    body = r.text.lower()
    assert any(token in body for token in (
        "idle", "running", "paused", "stopped", "blocked", "queue",
    ))


def test_you_owe_partial_renders_or_is_empty_state() -> None:
    r = _client().get("/_hx/you-owe")
    assert r.status_code == 200
    # Either the operator owes nothing (empty state) or there are
    # actionable rows. In both cases the partial must render — not
    # throw an UndefinedError.
    body = r.text
    assert any(token in body for token in (
        "You owe", "Nothing", "Graduate", "Discard", "Promote", "queue",
        # In empty state the partial still has to render *something*
        # benign — any non-empty body is acceptable as long as no
        # template error leaked through.
    ))


def test_log_page_renders_filter_form_and_kind_pills() -> None:
    r = _client().get("/log")
    assert r.status_code == 200
    body = r.text
    # Filter form controls.
    assert 'name="kind"' in body
    assert 'name="actor"' in body
    # The five activity kinds the unified log merges.
    for kind in ("cmd", "tick", "spawn", "verdict", "trunk-swap"):
        assert kind in body, f"kind '{kind}' missing from /log"


def test_log_page_filter_query_round_trips() -> None:
    # /log accepts `kind`, `actor`, `slug`, `limit` GET params and
    # echoes them back into the form. Pick something obviously absent
    # so the table renders as empty.
    r = _client().get("/log?kind=cmd&actor=zzz-not-a-real-actor&limit=50")
    assert r.status_code == 200
    body = r.text
    assert "zzz-not-a-real-actor" in body


def test_runs_index_aliases_experiments_list() -> None:
    # /runs is the new IA name; /experiments is kept for backward
    # compatibility. Both should return 200 and render the same table.
    c = _client()
    r1 = c.get("/runs")
    r2 = c.get("/experiments")
    assert r1.status_code == 200
    assert r2.status_code == 200
    # Both surfaces use the same template, so a stable identifier
    # ("Experiments" or "Runs" header) should appear in both bodies.
    for body in (r1.text, r2.text):
        assert "Runs" in body or "Experiments" in body


def test_tree_page_renders_with_pr_badge_template_available() -> None:
    # The tree page now embeds the _pr_badge.html partial. With no
    # PRs in the local DB the include is a no-op, but the page must
    # still render (no UndefinedError on `pr_by_slug` / `pr_by_instance`).
    r = _client().get("/tree")
    assert r.status_code == 200
    body = r.text
    assert "Configuration tree" in body
    # Verdict workflow surface is part of the redesigned tree page.
    assert "Verdicts" in body


def test_sidebar_reflects_new_six_page_ia() -> None:
    # The sidebar shipped with the redesign exposes exactly the six
    # IA endpoints in the primary nav. The audit power-user surface
    # remains as a primary entry.
    r = _client().get("/")
    assert r.status_code == 200
    body = r.text
    for href in ('href="/"', 'href="/tree"', 'href="/runs"',
                 'href="/tasks"', 'href="/log"', 'href="/audit"'):
        assert href in body, f"sidebar link {href} missing"


def test_sidebar_more_views_reaches_every_secondary_page() -> None:
    # Regression for "/roadmap is not visible from the side bar" — the
    # 'More views' disclosure underneath the primary IA must include
    # every secondary surface so each is reachable in one click.
    r = _client().get("/")
    assert r.status_code == 200
    body = r.text
    for href in (
        'href="/components"',
        'href="/components-perf"',
        'href="/ideas"',
        'href="/roadmap"',
        'href="/daemon"',
        'href="/pending"',
        'href="/spawns"',
        'href="/experiments"',
    ):
        assert href in body, f"'More views' link {href} missing from sidebar"


def test_more_views_is_open_when_active_page_is_secondary() -> None:
    # The disclosure should auto-open when the operator lands on a
    # secondary page so they immediately see where they are. We test
    # this by hitting /roadmap and looking for the ``open`` attribute
    # on the <details> element.
    r = _client().get("/roadmap")
    assert r.status_code == 200
    body = r.text
    # Pick a window around the disclosure summary text.
    needle = "More views"
    idx = body.find(needle)
    assert idx > 0, "'More views' summary missing"
    # The <details ... open> tag opens before the summary text. Look
    # for it in the 200-char window before the needle.
    window = body[max(0, idx - 200):idx]
    assert "<details" in window
    assert "open" in window, "details element should be open on secondary page"


def test_every_get_route_returns_200_on_a_fresh_db() -> None:
    # End-to-end reachability sweep. Walk every non-parameterised GET
    # route registered on the app and assert it renders cleanly.
    # Parameterised routes (``/runs/{id}``, ``/components/{id}``,
    # ``/tasks/{checksum}``, etc.) and the JSON ``/api/*`` /  ``/_hx/*``
    # endpoints are skipped — they're exercised by their own targeted
    # tests.
    from openharness.lab.web.app import create_app
    app = create_app()
    skip_prefixes = ("/api", "/_hx", "/static")
    skip_exact = {"/openapi.json", "/docs", "/redoc",
                  "/docs/oauth2-redirect"}
    paths: list[str] = []
    for r in app.routes:
        path = getattr(r, "path", None)
        if not path:
            continue
        if "{" in path:
            continue  # parameterised — handled by other tests
        if path.startswith(skip_prefixes) or path in skip_exact:
            continue
        if "GET" not in (getattr(r, "methods", None) or set()):
            continue
        paths.append(path)
    assert paths, "expected at least one renderable GET route"
    c = _client()
    for path in paths:
        resp = c.get(path)
        assert resp.status_code == 200, (
            f"{path} returned {resp.status_code}; "
            f"body[:200]={resp.text[:200]!r}"
        )


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
