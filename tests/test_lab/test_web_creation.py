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
4. The /activity page surfaces the unified timeline + usage summary so
   the operator can narrow by kind, actor, or slug.

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
        "idea-append": {"lab-ideas-changed", "lab-pending-changed"},
        "roadmap-add": {"lab-roadmap-changed", "lab-pending-changed"},
        "roadmap-suggest": {"lab-roadmap-changed", "lab-pending-changed"},
        "component-set-status": {"lab-components-changed"},
        "component-upsert": {"lab-components-changed"},
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
    params = labcmd._validate_params(
        spec,
        {
            "slug": "tb2-foo",
            "hypothesis": "h",
            "plan": "p",
            # idea / depends_on / cost intentionally absent
        },
    )
    argv = labcmd._build_argv(spec, params)
    assert "--idea" not in argv
    assert "--depends-on" not in argv
    assert "--cost" not in argv
    # Required tokens still present.
    assert argv == ["roadmap", "add", "tb2-foo", "--hypothesis", "h", "--plan", "p"]


def test_optional_group_included_when_param_present() -> None:
    spec = labcmd.COMMANDS["roadmap-add"]
    params = labcmd._validate_params(
        spec,
        {
            "slug": "tb2-foo",
            "hypothesis": "h",
            "plan": "p",
            "idea": "loop-guard",
            "depends_on": "a, b",
            "cost": "$5",
        },
    )
    argv = labcmd._build_argv(spec, params)
    assert argv == [
        "roadmap",
        "add",
        "tb2-foo",
        "--hypothesis",
        "h",
        "--plan",
        "p",
        "--idea",
        "loop-guard",
        "--depends-on",
        "a, b",
        "--cost",
        "$5",
    ]


def test_optional_group_partial_includes_only_resolvable() -> None:
    # Setting only `cost` (not idea/depends_on) must still emit --cost
    # but skip the other two — each group is independent.
    spec = labcmd.COMMANDS["roadmap-add"]
    params = labcmd._validate_params(
        spec,
        {
            "slug": "tb2-foo",
            "hypothesis": "h",
            "plan": "p",
            "cost": "$5",
        },
    )
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
    for ok in [
        "Prompting",
        "Architecture",
        "Memory",
        "Tools",
        "Runtime",
        "Exploration",
        "Test-Time Inference",
        "Model Policy",
        "Evaluation",
    ]:
        assert labcmd._IDEA_THEME.fullmatch(ok)
    for bad in ["architecture", "Other", "tools ", "", "Memory\n"]:
        assert not labcmd._IDEA_THEME.fullmatch(bad)


def test_component_status_vocabulary_locked() -> None:
    for ok in ["proposed", "experimental", "validated", "rejected", "superseded"]:
        assert labcmd._COMPONENT_STATUS.fullmatch(ok)
    for bad in ["Proposed", "done", "promoted", ""]:
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


def test_backlog_ideas_no_longer_renders_raw_input_form() -> None:
    # IA redesign: raw user input forms removed. Ideas land via CLI/skill
    # path (`uv run lab idea-append`); the UI shows a read-only backlog
    # section with one-click lifecycle actions.
    r = _client().get("/backlog?section=ideas")
    assert r.status_code == 200
    assert 'cmd_id" value="idea-append"' not in r.text
    # Per-card actions (move) survive — they're one-click confirmations,
    # not free-form input.
    assert 'cmd_id" value="idea-move"' in r.text


def test_backlog_queue_no_longer_renders_raw_input_forms() -> None:
    # IA redesign: roadmap-add / roadmap-suggest forms removed. Operators
    # use the CLI / `lab-plan-next` skill, or one-click backlog actions.
    r = _client().get("/backlog?section=queue")
    assert r.status_code == 200
    assert 'cmd_id" value="roadmap-add"' not in r.text
    assert 'cmd_id" value="roadmap-suggest"' not in r.text
    assert "Up next" in r.text


def test_catalog_components_no_longer_renders_upsert_or_inline_status() -> None:
    # IA redesign: component-upsert and the inline per-row
    # component-set-status select were removed. The catalog is read-only.
    r = _client().get("/catalog?tab=components")
    assert r.status_code == 200
    assert 'cmd_id" value="component-upsert"' not in r.text
    assert 'cmd_id" value="component-set-status"' not in r.text
    assert "Components" in r.text


def test_components_body_partial_used_for_autorefresh() -> None:
    r = _client().get("/_hx/components-body")
    assert r.status_code == 200
    # The partial returns table fragments only, no <html>/<body>.
    assert "<html" not in r.text
    assert "Used by" in r.text


# ---------------------------------------------------------------------------
# Activity page polish
# ---------------------------------------------------------------------------


def test_activity_page_summary_and_filters() -> None:
    c = _client()
    r = c.get("/activity")
    assert r.status_code == 200
    # Usage summary surface.
    assert "Pipeline usage" in r.text
    assert "Trial usage" in r.text
    # Filter form.
    for snippet in ['<select name="kind"', 'name="actor"', 'name="slug"']:
        assert snippet in r.text
    # Filter values are echoed back into selectors.
    r2 = c.get("/activity?kind=cmd&actor=zzz-not-a-real-actor&limit=50")
    assert r2.status_code == 200
    assert "zzz-not-a-real-actor" in r2.text


def test_activity_filter_with_unknown_actor_yields_empty_state() -> None:
    r = _client().get("/activity?kind=cmd&actor=this-actor-does-not-exist")
    assert r.status_code == 200
    assert "No activity matches these filters." in r.text


# ---------------------------------------------------------------------------
# Defence-in-depth: form validation + 400 surface for the cmd endpoint
# ---------------------------------------------------------------------------


def test_api_cmd_rejects_unknown_cmd_id() -> None:
    r = _client().post("/api/cmd", data={"cmd_id": "not-a-real-cmd"})
    assert r.status_code == 400
    assert "unknown cmd_id" in r.text


def test_api_cmd_rejects_invalid_slug_for_idea_append() -> None:
    r = _client().post(
        "/api/cmd",
        data={
            "cmd_id": "idea-append",
            "idea_id": "Has Spaces",
            "theme": "Tools",
            "motivation": "m",
            "sketch": "s",
        },
    )
    assert r.status_code == 400
    assert "does not match required pattern" in r.text


def test_api_cmd_rejects_unknown_extras() -> None:
    r = _client().post(
        "/api/cmd",
        data={
            "cmd_id": "idea-append",
            "idea_id": "ok",
            "theme": "Tools",
            "motivation": "m",
            "sketch": "s",
            "evil_param": "rm -rf /",
        },
    )
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


def test_home_page_renders_leaderboard_and_work_zones() -> None:
    r = _client().get("/")
    assert r.status_code == 200
    body = r.text
    # Leaderboard-first home.
    assert "Leaderboard" in body
    assert "full-suite only" in body
    assert "Experiment history" in body
    assert "Top ranked" not in body
    assert "Ranked full-suite trajectory" not in body
    assert "Experiment evaluation deltas" not in body
    # Operator work zones remain on the first page.
    assert "Now" in body
    assert "Queue" in body
    assert 'id="roadmap-queue"' in body
    assert 'id="you-owe"' in body or "Inbox" in body
    # HTMX partials mounted on first render.
    assert "/_hx/leaderboard" in body
    assert "/_hx/experiment-history" in body
    assert "/_hx/leaderboard-hero" not in body
    assert "/_hx/leaderboard-trajectory" not in body
    assert "/_hx/leaderboard-delta" not in body
    assert "/_hx/status-roadmap-queue" in body
    assert "/_hx/you-owe" in body


def test_leaderboard_partials_render() -> None:
    c = _client()
    expected = {
        "/_hx/leaderboard": ("No full-suite", "<table"),
        "/_hx/experiment-history": ("No experiments", "<table"),
    }
    for path, markers in expected.items():
        r = c.get(path)
        assert r.status_code == 200, f"{path} -> {r.status_code}"
        choices = markers if isinstance(markers, tuple) else (markers,)
        assert any(marker in r.text for marker in choices), path
    for old_path in (
        "/_hx/leaderboard-hero",
        "/_hx/leaderboard-trajectory",
        "/_hx/leaderboard-ladder",
        "/_hx/leaderboard-delta",
    ):
        assert c.get(old_path).status_code == 404, old_path


def test_you_owe_partial_renders_or_is_empty_state() -> None:
    r = _client().get("/_hx/you-owe")
    assert r.status_code == 200
    # Either the operator owes nothing (empty state) or there are
    # actionable rows. In both cases the partial must render — not
    # throw an UndefinedError.
    body = r.text
    assert any(
        token in body
        for token in (
            "Nothing waiting",
            "up to date",
            "Discard",
            "Promote",
            "Auto-proposed",
            "Daemon suggestions",
            # In empty state the partial still has to render *something*
            # benign — any non-empty body is acceptable as long as no
            # template error leaked through.
        )
    )


def test_status_roadmap_queue_partial_renders_queue_or_empty_state() -> None:
    r = _client().get("/_hx/status-roadmap-queue")
    assert r.status_code == 200
    body = r.text
    assert "Processing" in body
    assert "Done" in body
    assert "Daemon queue" in body
    assert any(
        token in body
        for token in (
            "No roadmap entries",
            "ready",
            "approved",
            "running",
            "blocked",
        )
    )


def test_activity_page_renders_filter_form_and_kind_pills() -> None:
    r = _client().get("/activity")
    assert r.status_code == 200
    body = r.text
    # Filter form controls.
    assert 'name="kind"' in body
    assert 'name="actor"' in body
    # The activity kinds the unified log merges.
    for kind in ("cmd", "tick", "spawn", "verdict"):
        assert kind in body, f"kind '{kind}' missing from /activity"


def test_activity_page_filter_query_round_trips() -> None:
    # /activity accepts `kind`, `actor`, `slug`, `limit` GET params and
    # echoes them back into the form. Pick something obviously absent
    # so the table renders as empty.
    r = _client().get("/activity?kind=cmd&actor=zzz-not-a-real-actor&limit=50")
    assert r.status_code == 200
    body = r.text
    assert "zzz-not-a-real-actor" in body


def test_runs_index_is_canonical_and_experiments_route_is_removed() -> None:
    # /runs is canonical. The old /experiments alias is intentionally
    # gone so stale routes do not survive the IA collapse.
    c = _client()
    r1 = c.get("/runs")
    r2 = c.get("/experiments")
    assert r1.status_code == 200
    assert r2.status_code == 404
    assert "Runs" in r1.text


def test_existing_run_detail_pages_render_when_db_has_runs() -> None:
    from openharness.lab.web import data as labdata

    with labdata.LabReader() as reader:
        if not reader.db_available:
            return
        experiments = reader.experiments(limit=3)
    if not experiments:
        return

    c = _client()
    for exp in experiments:
        r = c.get(f"/runs/{exp.instance_id}")
        assert r.status_code == 200, f"{exp.instance_id} -> {r.status_code}"
        assert "Paired" not in r.text or "Undefined" not in r.text


def test_catalog_configs_renders_with_pr_badge_template_available() -> None:
    # The configs tab embeds the _pr_badge.html partial. With no
    # PRs in the local DB the include is a no-op, but the page must
    # still render (no UndefinedError on `pr_by_slug` / `pr_by_instance`).
    r = _client().get("/catalog?tab=configs")
    assert r.status_code == 200
    body = r.text
    assert "Operational baseline" in body
    # Evaluation workflow surface is part of the configs tab.
    assert "Experiment Evaluations" in body


def test_phase_reset_command_accepts_replan() -> None:
    spec = labcmd.COMMANDS["phases-reset-one"]
    params = labcmd._validate_params(spec, {"slug": "tb2-foo", "phase": "replan"})
    argv = labcmd._build_argv(spec, params)
    assert argv == ["phases", "reset", "tb2-foo", "--phase", "replan"]


def test_sidebar_reflects_new_six_page_ia() -> None:
    # The sidebar shipped with the redesign exposes exactly the six
    # IA endpoints in the primary nav.
    r = _client().get("/")
    assert r.status_code == 200
    body = r.text
    for href in (
        'href="/"',
        'href="/pipeline"',
        'href="/runs"',
        'href="/catalog"',
        'href="/backlog"',
        'href="/activity"',
    ):
        assert href in body, f"sidebar link {href} missing"


def test_sidebar_omits_legacy_secondary_pages() -> None:
    # The collapsed IA has no "More views" compatibility menu. Old
    # public page paths should not appear as sidebar links.
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
        'href="/usage"',
        'href="/experiments"',
        'href="/tree"',
        'href="/tasks"',
        'href="/log"',
        'href="/audit"',
    ):
        assert href not in body, f"legacy sidebar link {href} should be gone"


def test_activity_page_includes_usage_summary() -> None:
    r = _client().get("/activity")
    assert r.status_code == 200
    assert "Pipeline usage" in r.text
    assert "Trial usage" in r.text


def test_backlog_sections_are_reachable_as_tabs() -> None:
    r = _client().get("/backlog?section=suggested")
    assert r.status_code == 200
    body = r.text
    for href in (
        'href="/backlog?section=queue"',
        'href="/backlog?section=suggested"',
        'href="/backlog?section=ideas"',
        'href="/backlog?section=done"',
        'href="/backlog?section=inbox"',
    ):
        assert href in body


def test_every_get_route_returns_200_on_a_fresh_db() -> None:
    # End-to-end reachability sweep. Walk every non-parameterised GET
    # route registered on the app and assert it renders cleanly.
    # Parameterised routes (``/runs/{id}``, ``/catalog/components/{id}``,
    # ``/catalog/tasks/{checksum}``, etc.) and the JSON ``/api/*`` /
    # ``/_hx/*`` endpoints are skipped — they're exercised by their
    # own targeted tests.
    from openharness.lab.web.app import create_app

    app = create_app()
    skip_prefixes = ("/api", "/_hx", "/static")
    skip_exact = {"/openapi.json", "/docs", "/redoc", "/docs/oauth2-redirect"}
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
            f"{path} returned {resp.status_code}; body[:200]={resp.text[:200]!r}"
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
