"""Tests for ``daemon_state`` + the runner / web-UI integration.

Three tiers of coverage in this file:

1. **State module** (atomic read / write, mutators, history ring).
   Pure data-layer tests — no FastAPI, no subprocess.

2. **Runner scheduling** (``_select_next_entry``).
   Verifies the manual / autonomous / paused branches without
   actually spawning codex skills.

3. **Web cockpit** (the new ``/_hx/daemon-*`` partials and the
   ``/api/cmd`` whitelist entries).
   Hits the FastAPI app via ``TestClient`` so the templates are
   exercised; mutation tests stub the subprocess shell-out so they
   stay hermetic.

The whole file uses an isolated lab tree per test (``OPENHARNESS_REPO_ROOT``
override + ``LAB_RUNS_ROOT`` redirection) so the on-disk
``daemon-state.json`` of the dev machine is never touched.
"""

from __future__ import annotations

import importlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Fixtures: isolated lab tree
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_lab(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Spin up a self-contained `lab/` + `runs/lab/` under tmp_path.

    Reimports `paths` and `daemon_state` so module-level constants
    (e.g. `LAB_RUNS_ROOT`) pick up the new repo root.
    """
    repo = tmp_path / "repo"
    (repo / "lab").mkdir(parents=True)
    (repo / "runs" / "lab").mkdir(parents=True)
    (repo / "pyproject.toml").write_text("# placeholder for repo-detection")

    monkeypatch.setenv("OPENHARNESS_REPO_ROOT", str(repo))

    # Force reimport so module-level paths bind to the override.
    import openharness.lab.paths as paths
    importlib.reload(paths)
    import openharness.lab.daemon_state as ds
    importlib.reload(ds)

    return repo


# ---------------------------------------------------------------------------
# Tier 1: state module
# ---------------------------------------------------------------------------


def test_default_state_has_manual_mode_and_no_approvals(isolated_lab: Path) -> None:
    """A fresh lab boots in `manual` with zero approvals.

    This is the contract the operator chose: nothing the daemon picks
    up unless the human says so. If this test breaks the regression
    risk is "the daemon silently went back to autopilot".
    """
    import openharness.lab.daemon_state as ds

    state = ds.load()
    assert state.mode == "manual"
    assert state.approved_slugs == []
    assert state.active_tick is None
    assert state.history == []
    assert state.entry_failures == {}


def test_save_then_load_roundtrips_all_fields(isolated_lab: Path) -> None:
    """Every field on `DaemonState` survives a save → load cycle.

    Uses one of every variant (active_tick set, two failures, two
    history rows, custom max_failures) to guard against a serializer
    field that gets dropped silently on the next round.
    """
    import openharness.lab.daemon_state as ds

    now = datetime.now(timezone.utc)
    state = ds.DaemonState(
        mode="autonomous",
        approved_slugs=["a", "b"],
        max_failures_before_demote=5,
        active_tick=ds.ActiveTick(
            slug="x", phase="running", started_at=now,
            spawn_pid=1234, log_path="/tmp/x.log",
            worktree_path="/tmp/wt", note="hello",
        ),
        entry_failures={
            "y": ds.FailureRecord(count=2, last_error="boom",
                                  last_outcome="refuse",
                                  last_failed_at=now - timedelta(minutes=5)),
        },
        history=[
            ds.TickHistoryEntry(
                slug="z", started_at=now - timedelta(minutes=10),
                ended_at=now - timedelta(minutes=9),
                outcome="ok", phase_reached="done",
                duration_sec=60.0, summary="ok run",
                log_path="/tmp/z.log",
            ),
        ],
    )
    ds.save(state, actor="unit-test")

    loaded = ds.load()
    assert loaded.mode == "autonomous"
    assert loaded.approved_slugs == ["a", "b"]
    assert loaded.max_failures_before_demote == 5
    assert loaded.active_tick is not None
    assert loaded.active_tick.slug == "x"
    assert loaded.active_tick.spawn_pid == 1234
    assert loaded.entry_failures["y"].count == 2
    assert len(loaded.history) == 1
    assert loaded.history[0].outcome == "ok"


def test_corrupted_file_returns_default_state(isolated_lab: Path) -> None:
    """A garbage state file isn't fatal — load returns a fresh default.

    Recovery story: the operator can click "set mode" in the UI and
    the next save overwrites the corrupted file. Without this
    safeguard a single bad write would brick the daemon and the UI.
    """
    import openharness.lab.daemon_state as ds

    ds.DAEMON_STATE_PATH.write_text("{not json")

    state = ds.load()
    assert state.mode == "manual"
    assert state.approved_slugs == []


def test_consume_approval_pops_first_match(isolated_lab: Path) -> None:
    """Approvals are consumed exactly once (operator chose `consumed`)."""
    import openharness.lab.daemon_state as ds

    ds.approve("alpha")
    ds.approve("beta")

    assert ds.consume_approval("alpha") is True
    assert ds.load().approved_slugs == ["beta"]
    assert ds.consume_approval("alpha") is False, "second consume should no-op"


def test_history_ring_caps_at_50(isolated_lab: Path) -> None:
    """The history ring buffer trims to `HISTORY_LIMIT` entries on `end_tick`."""
    import openharness.lab.daemon_state as ds

    for i in range(ds.HISTORY_LIMIT + 5):
        ds.begin_tick(ds.ActiveTick(
            slug=f"slug-{i:02d}", phase="spawning",
            started_at=datetime.now(timezone.utc),
        ))
        ds.end_tick(outcome="ok")

    history = ds.load().history
    assert len(history) == ds.HISTORY_LIMIT
    # Newest is last; ring should have kept the tail.
    assert history[-1].slug == f"slug-{ds.HISTORY_LIMIT + 4:02d}"


def test_failure_counter_increments_then_resets_on_success(isolated_lab: Path) -> None:
    """Per-slug failure counter feeds the auto-demote gate."""
    import openharness.lab.daemon_state as ds

    for _ in range(2):
        ds.begin_tick(ds.ActiveTick(slug="bad", phase="spawning",
                                    started_at=datetime.now(timezone.utc)))
        _, rec = ds.end_tick(outcome="refuse", summary="no creds")
        assert rec is not None

    assert ds.load().entry_failures["bad"].count == 2

    # Success resets.
    ds.begin_tick(ds.ActiveTick(slug="bad", phase="spawning",
                                started_at=datetime.now(timezone.utc)))
    _, rec = ds.end_tick(outcome="ok")
    assert rec is None
    assert "bad" not in ds.load().entry_failures


def test_clear_active_tick_does_not_record_history(isolated_lab: Path) -> None:
    """Signal-handler shutdown should drop in-flight tick without faking a history row."""
    import openharness.lab.daemon_state as ds

    ds.begin_tick(ds.ActiveTick(slug="abandoned", phase="running",
                                started_at=datetime.now(timezone.utc)))
    ds.clear_active_tick()
    s = ds.load()
    assert s.active_tick is None
    assert s.history == []


# ---------------------------------------------------------------------------
# Tier 2: runner scheduling
# ---------------------------------------------------------------------------


def _entry(slug: str):
    from openharness.lab.runner import RoadmapEntry
    return RoadmapEntry(
        slug=slug, body="", idea_id=None, hypothesis="", depends_on=[],
    )


def test_select_next_entry_paused_picks_nothing(isolated_lab: Path) -> None:
    from openharness.lab.runner import _select_next_entry
    import openharness.lab.daemon_state as ds

    ready = [_entry("a"), _entry("b")]
    state = ds.DaemonState(mode="paused", approved_slugs=["a"])
    assert _select_next_entry(ready, state) is None


def test_select_next_entry_autonomous_picks_first(isolated_lab: Path) -> None:
    from openharness.lab.runner import _select_next_entry
    import openharness.lab.daemon_state as ds

    ready = [_entry("a"), _entry("b")]
    state = ds.DaemonState(mode="autonomous", approved_slugs=["b"])
    chosen = _select_next_entry(ready, state)
    assert chosen is not None and chosen.slug == "a", \
        "autonomous must ignore approval list and pick top of queue"


def test_select_next_entry_manual_picks_highest_approved(isolated_lab: Path) -> None:
    """Roadmap order beats approval order — that lets the operator
    approve out of order without changing the queue."""
    from openharness.lab.runner import _select_next_entry
    import openharness.lab.daemon_state as ds

    ready = [_entry("a"), _entry("b"), _entry("c")]
    state = ds.DaemonState(mode="manual", approved_slugs=["c", "b"])
    chosen = _select_next_entry(ready, state)
    assert chosen is not None and chosen.slug == "b"


def test_select_next_entry_manual_no_approvals_picks_nothing(
    isolated_lab: Path,
) -> None:
    """Pure-manual + zero approvals = idle. The single most important
    behaviour change in this whole feature; without it the daemon would
    keep eating tokens on whatever sits at the top of the queue."""
    from openharness.lab.runner import _select_next_entry
    import openharness.lab.daemon_state as ds

    ready = [_entry("a"), _entry("b")]
    state = ds.DaemonState(mode="manual", approved_slugs=[])
    assert _select_next_entry(ready, state) is None


# ---------------------------------------------------------------------------
# Tier 3: web cockpit
# ---------------------------------------------------------------------------


@pytest.fixture
def client(isolated_lab: Path):
    """FastAPI TestClient wired to the isolated lab tree."""
    from fastapi.testclient import TestClient
    from openharness.lab.web.app import create_app

    # Touch the markdown files create_app's reader expects.
    (isolated_lab / "lab" / "ideas.md").write_text("# Ideas\n\n## Proposed\n")
    (isolated_lab / "lab" / "roadmap.md").write_text(
        "# Roadmap\n\n## Up next\n_(none)_\n\n## Suggested\n\n## Done\n"
    )
    (isolated_lab / "lab" / "experiments.md").write_text("# Experiments\n")
    (isolated_lab / "lab" / "components.md").write_text("# Components\n")
    (isolated_lab / "lab" / "trunk.yaml").write_text("trunk: []\n")
    (isolated_lab / "lab" / "configs.md").write_text("# Configs\n\n## Trunk\n\n## Branches\n")

    app = create_app()
    return TestClient(app)


def test_daemon_page_renders_cockpit_panels(client) -> None:
    """`/daemon` includes all five cockpit sections."""
    resp = client.get("/daemon")
    assert resp.status_code == 200
    body = resp.text
    for marker in ("Mode:", "Current tick", "Approval queue", "Recent ticks"):
        assert marker in body, f"missing {marker!r} on /daemon"


def test_daemon_partials_round_trip(client) -> None:
    """Each `/_hx/daemon-*` partial returns 200 + non-empty content."""
    for path in (
        "/_hx/daemon-mode",
        "/_hx/daemon-active-tick",
        "/_hx/daemon-approvals",
        "/_hx/daemon-history",
        "/_hx/daemon-failures",
    ):
        resp = client.get(path)
        assert resp.status_code == 200, f"{path} → {resp.status_code}"
    # daemon-failures is empty when there are no failures (sentinel
    # for "don't render the section"); the others always have body.
    assert client.get("/_hx/daemon-mode").text.strip() != ""


def test_whitelist_includes_all_daemon_commands(client) -> None:
    """The five new daemon-control commands are registered.

    Catches the kind of regression where a new spec is added to
    daemon_state but never wired into the web whitelist.
    """
    from openharness.lab.web.commands import COMMANDS
    expected = {
        "daemon-mode",
        "daemon-approve",
        "daemon-revoke",
        "daemon-cancel",
        "daemon-reset-failures",
    }
    missing = expected - COMMANDS.keys()
    assert not missing, f"missing whitelist entries: {missing}"


def test_api_cmd_validates_mode_param(client) -> None:
    """Invalid mode value is rejected with a 400; valid value succeeds.

    Stubs subprocess.run so a real `uv run lab daemon mode …` doesn't
    have to spin up. We assert the command was assembled correctly,
    not that the CLI itself worked (that's covered by Tier 1/2).
    """
    from unittest import mock

    # Bad mode → 400 from the param validator.
    resp = client.post(
        "/api/cmd",
        data={"cmd_id": "daemon-mode", "mode": "bogus"},
    )
    assert resp.status_code == 400, resp.text

    # Good mode → subprocess invoked with the right argv.
    with mock.patch(
        "openharness.lab.web.commands.subprocess.run"
    ) as m:
        m.return_value = mock.Mock(returncode=0, stdout="mode → manual\n", stderr="")
        resp = client.post(
            "/api/cmd",
            data={"cmd_id": "daemon-mode", "mode": "manual"},
        )
        assert resp.status_code == 200, resp.text
        argv = m.call_args.args[0]
        # Last 4 tokens are: ['daemon', 'mode', 'manual', '--actor', 'human:webui']
        assert argv[-5:] == ["daemon", "mode", "manual", "--actor", "human:webui"]
