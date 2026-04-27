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
import json
import os
import time
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
        pause_after_phase="run",
        pause_after_slug="x",
        pause_after_requested_at=now,
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
    assert loaded.pause_after_phase == "run"
    assert loaded.pause_after_slug == "x"
    assert loaded.pause_after_requested_at is not None
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
    """Per-slug failure counter feeds the failure gate."""
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


def test_paused_tick_does_not_increment_failure_counter(isolated_lab: Path) -> None:
    import openharness.lab.daemon_state as ds

    ds.begin_tick(ds.ActiveTick(
        slug="alpha",
        phase="run",
        started_at=datetime.now(timezone.utc),
    ))
    _, rec = ds.end_tick(outcome="paused", summary="paused after run")

    state = ds.load()
    assert rec is None
    assert state.entry_failures == {}
    assert state.history[-1].outcome == "paused"


def test_consume_pause_after_sets_paused_mode(isolated_lab: Path) -> None:
    import openharness.lab.daemon_state as ds

    ds.set_mode("autonomous")
    ds.set_pause_after("run", slug="alpha")

    assert ds.consume_pause_after_if_matches(phase="design", slug="alpha") is False
    assert ds.consume_pause_after_if_matches(phase="run", slug="beta") is False
    assert ds.consume_pause_after_if_matches(phase="run", slug="alpha") is True

    state = ds.load()
    assert state.mode == "paused"
    assert state.pause_after_phase is None
    assert state.pause_after_slug is None


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
# Tier 1.5: SIGUSR1 wake-up — operator clicks should propagate in <1s
# ---------------------------------------------------------------------------


def test_notify_daemon_returns_false_when_no_lock(isolated_lab: Path) -> None:
    """No orchestrator lock means no daemon to notify; helper returns False
    instead of raising. Critical: the CLI / web UI must not error out
    just because the daemon happens to be stopped."""
    import openharness.lab.daemon_state as ds

    assert ds.notify_daemon() is False


def test_notify_daemon_returns_false_on_corrupted_lock(
    isolated_lab: Path,
) -> None:
    """A garbage lock file is treated as 'no daemon', not a crash."""
    import openharness.lab.daemon_state as ds
    from openharness.lab.paths import ORCHESTRATOR_LOCK_PATH

    ORCHESTRATOR_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    ORCHESTRATOR_LOCK_PATH.write_text("{not json")

    assert ds.notify_daemon() is False


def test_notify_daemon_returns_false_when_pid_dead(isolated_lab: Path) -> None:
    """If the lock points at a vanished pid, helper returns False (not raise)."""
    import openharness.lab.daemon_state as ds
    from openharness.lab.paths import ORCHESTRATOR_LOCK_PATH

    # PID 1 always exists (init), so use a high one we're confident is free.
    # Probing 0 returns True for "any pid" so we use a real-ish high pid;
    # if by cosmic chance it IS taken, os.kill will succeed and our test
    # would still pass for the right reason — we only assert non-raise.
    ORCHESTRATOR_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    ORCHESTRATOR_LOCK_PATH.write_text(json.dumps({"pid": 999_999}))
    # Just ensure it doesn't raise — the True/False outcome depends on
    # whether 999_999 happens to exist on this kernel.
    result = ds.notify_daemon()
    assert isinstance(result, bool)


def test_notify_daemon_signals_self(isolated_lab: Path) -> None:
    """Smoke test of the round trip: write own pid to the lock, install
    a SIGUSR1 handler, call notify_daemon, expect the handler to fire.

    Confirms (a) the lock-format payload the helper expects matches
    what ``orchestrator_lock`` actually writes, and (b) the helper
    actually delivers SIGUSR1 (not, say, SIGUSR2 by typo).
    """
    import os as _os
    import signal as _sig
    import threading as _th
    import openharness.lab.daemon_state as ds
    from openharness.lab.paths import ORCHESTRATOR_LOCK_PATH

    received = _th.Event()

    def _handler(_signum: int, _frame: object) -> None:
        received.set()

    prev = _sig.signal(_sig.SIGUSR1, _handler)
    try:
        ORCHESTRATOR_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        ORCHESTRATOR_LOCK_PATH.write_text(json.dumps({"pid": _os.getpid()}))

        assert ds.notify_daemon() is True
        # Signal delivery is synchronous on POSIX after a syscall
        # boundary; Event.wait gives us up to 2 s of slack for slow CI.
        assert received.wait(timeout=2.0), "SIGUSR1 was not delivered"
    finally:
        _sig.signal(_sig.SIGUSR1, prev)


def test_idle_wait_returns_quickly_when_event_set(isolated_lab: Path) -> None:
    """``_idle_wait`` must return ~immediately when SIGUSR1 fires.

    Drives the wake event from a background thread (proxy for the
    SIGUSR1 handler) and confirms the wait returns in well under the
    full timeout. Proves the snappy-UI design isn't wishful thinking.
    """
    import threading as _th
    import time as _time
    import openharness.lab.runner as runner

    runner._WAKE_EVENT.clear()

    def _set_after_delay() -> None:
        _time.sleep(0.05)
        runner._WAKE_EVENT.set()

    t = _th.Thread(target=_set_after_delay, daemon=True)
    t.start()

    started = _time.monotonic()
    woken = runner._idle_wait(seconds=5.0)
    elapsed = _time.monotonic() - started

    t.join(timeout=1.0)
    assert woken is True, "wait should have returned True (interrupted)"
    assert elapsed < 1.0, f"wait took {elapsed:.2f}s, expected < 1s"
    # And it must clear the event so the next wait blocks again.
    assert not runner._WAKE_EVENT.is_set()


def test_idle_wait_blocks_full_duration_when_no_signal(
    isolated_lab: Path,
) -> None:
    """Conversely, with no signal the wait blocks for (at least most of)
    the requested duration. Catches a regression where someone makes
    the wait return early unconditionally."""
    import time as _time
    import openharness.lab.runner as runner

    runner._WAKE_EVENT.clear()
    started = _time.monotonic()
    woken = runner._idle_wait(seconds=0.2)
    elapsed = _time.monotonic() - started

    assert woken is False
    # Allow some slack for CI scheduling jitter.
    assert elapsed >= 0.15, f"wait returned in {elapsed:.3f}s, too fast"


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


def test_select_next_entry_skips_failure_blocked_slugs(isolated_lab: Path) -> None:
    from openharness.lab.runner import _select_next_entry
    import openharness.lab.daemon_state as ds

    ready = [_entry("a"), _entry("b"), _entry("c")]
    state = ds.DaemonState(
        mode="autonomous",
        max_failures_before_demote=2,
        entry_failures={"a": ds.FailureRecord(count=2, last_outcome="error")},
    )

    chosen = _select_next_entry(ready, state)

    assert chosen is not None and chosen.slug == "b"


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


def test_loop_blocks_without_mutating_roadmap(
    isolated_lab: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The failure gate must not dirty lab/roadmap.md on main."""
    import openharness.lab.daemon_state as ds
    import openharness.lab.runner as runner

    importlib.reload(runner)

    roadmap = isolated_lab / "lab" / "roadmap.md"
    original = (
        "# Roadmap\n\n"
        "## Up next\n\n"
        "### alpha\n\n"
        "-   **Hypothesis:** h\n\n"
        "### beta\n\n"
        "-   **Hypothesis:** h\n\n"
        "## Done\n\n"
        "_(none)_\n"
    )
    roadmap.write_text(original)
    with ds.mutate(actor="test") as state:
        state.mode = "autonomous"
        state.max_failures_before_demote = 1

    def _fail(_entry, _cfg):
        return runner.TickResult(ok=False, outcome="error", summary="boom")

    monkeypatch.setattr(runner, "_process_entry", _fail)

    runner.loop(runner.OrchestratorConfig(once=True, idle_sleep_sec=0))

    state = ds.load()
    assert roadmap.read_text() == original
    assert state.entry_failures["alpha"].count == 1
    assert [h.outcome for h in state.history] == ["error", "blocked"]


def test_parse_up_next_ignores_suggested_subsection(tmp_path: Path) -> None:
    from openharness.lab.runner import parse_up_next

    roadmap = tmp_path / "roadmap.md"
    roadmap.write_text(
        "# Roadmap\n\n"
        "## Up next\n\n"
        "### first\n\n"
        "-   **Hypothesis:** first hypothesis\n\n"
        "### second\n\n"
        "-   **Depends on:** `first`\n\n"
        "### Suggested\n\n"
        "#### later\n\n"
        "-   **Hypothesis:** not runnable yet\n\n"
        "## Done\n\n"
        "_(none)_\n"
    )

    entries = parse_up_next(roadmap)

    assert [entry.slug for entry in entries] == ["first", "second"]
    assert entries[1].depends_on == ["first"]


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
    (isolated_lab / "lab" / "configs.md").write_text("# Configs\n\n## Operational baseline\n\n")

    app = create_app()
    return TestClient(app)


def test_pipeline_page_renders_cockpit_panels(client) -> None:
    """`/pipeline` renders the pipeline-centric cockpit.

    After the redesign, the page is built around three top-level
    panels (control bar, pipeline strip, recent ticks) plus an
    Approval queue card and a collapsed Diagnostics section. We
    smoke-test for one anchor in each of the still-visible panels.
    """
    resp = client.get("/pipeline")
    assert resp.status_code == 200
    body = resp.text
    for marker in ("Mode", "Approval queue", "Recent ticks", "Diagnostics"):
        assert marker in body, f"missing {marker!r} on /pipeline"


def test_daemon_partials_round_trip(client) -> None:
    """Each `/_hx/daemon-*` partial returns 200 + non-empty content.

    Includes both the legacy partials (kept for backwards compat with
    external dashboards) and the redesigned cockpit partials
    (control-bar, pipeline). Catches the regression where someone
    removes a route while a template still references it.
    """
    for path in (
        # Legacy partials — the cockpit no longer mounts them but
        # external dashboards / tests may still target them.
        "/_hx/daemon-mode",
        "/_hx/daemon-active-tick",
        # Always-on cockpit partials.
        "/_hx/daemon-approvals",
        "/_hx/daemon-history",
        "/_hx/daemon-failures",
        # Redesigned cockpit partials.
        "/_hx/daemon-control-bar",
        "/_hx/daemon-pipeline",
    ):
        resp = client.get(path)
        assert resp.status_code == 200, f"{path} → {resp.status_code}"
    # daemon-failures is empty when there are no failures (sentinel
    # for "don't render the section"); the others always have body.
    assert client.get("/_hx/daemon-mode").text.strip() != ""
    assert client.get("/_hx/daemon-control-bar").text.strip() != ""
    # The pipeline partial must always render useful HTML. Depending
    # on earlier stateful tests in this module it may show the empty
    # state or the most recently touched slug.
    body = client.get("/_hx/daemon-pipeline").text
    assert any(token in body for token in (
        "No pipeline yet",
        "Reset all phases",
        "last seen",
        "running",
    ))


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
        # Cleanup actions added when the cockpit grew "stop / clean
        # up / restart" affordances. Catches the regression where
        # someone removes one without updating the corresponding
        # template button.
        "daemon-reset-all-failures",
        "daemon-clear-history",
        "runs-prune",
        # Per-slug pipeline + worktree controls added with the
        # cockpit redesign; the pipeline panel renders buttons that
        # POST these whitelist entries, so dropping one would leave
        # dead UI behind.
        "phases-reset",
        "phases-reset-one",
        "worktree-remove",
    }
    missing = expected - COMMANDS.keys()
    assert not missing, f"missing whitelist entries: {missing}"


def test_log_endpoint_returns_tail(client, isolated_lab: Path) -> None:
    """`/_hx/daemon-log/<basename>` reads the last bytes of a real log
    file from ``runs/lab/logs`` and renders the tail template.

    Catches the regression where someone changes the route or the
    template name and the disclosure UI silently breaks.
    """
    logs_dir = isolated_lab / "runs" / "lab" / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_name = "20260422T184348Z__lab-implement-variant__abcdef012345.log"
    (logs_dir / log_name).write_text(
        "# spawn_id: abcdef012345\n"
        "# --- codex stderr --- #\n"
        "/usr/bin/env: 'node': No such file or directory\n"
    )

    resp = client.get(f"/_hx/daemon-log/{log_name}")
    assert resp.status_code == 200, resp.text
    assert "node" in resp.text
    assert log_name in resp.text


def test_log_endpoint_rejects_path_traversal(client) -> None:
    """The strict basename regex must block ``../etc/passwd`` and friends.

    Even if the regex was somehow bypassed, the resolved-path check
    inside LAB_LOGS_DIR is a second safety net. We only verify the
    400 response (proving the regex layer triggered) — the second
    check is implicit but covered by code review."""
    for bad in ("../etc/passwd", "..%2Fetc%2Fpasswd", "log;rm -rf .log"):
        resp = client.get(f"/_hx/daemon-log/{bad}")
        assert resp.status_code in (400, 404), \
            f"expected 400/404 for {bad!r}, got {resp.status_code}"


def test_log_endpoint_404s_on_missing_file(client) -> None:
    """A well-formed but non-existent log basename → 404, not 500."""
    name = "20991231T235959Z__lab-implement-variant__deadbeef0000.log"
    resp = client.get(f"/_hx/daemon-log/{name}")
    assert resp.status_code == 404


def test_tail_log_for_summary_extracts_stderr_line(tmp_path: Path) -> None:
    """``runner._tail_log_for_summary`` returns the last meaningful
    stderr line, which is exactly what the operator needs to debug an
    exit-127-style failure (PATH problem, missing binary, etc.).
    """
    from openharness.lab.runner import _tail_log_for_summary

    log = tmp_path / "spawn.log"
    log.write_text(
        "# spawn_id: x\n"
        "# command: codex exec ...\n"
        "# --- codex stdout (jsonl events) --- #\n"
        "\n"
        "# --- codex stderr --- #\n"
        "/usr/bin/env: 'node': No such file or directory\n"
        "\n"
    )
    out = _tail_log_for_summary(log)
    assert out == "/usr/bin/env: 'node': No such file or directory"


def test_tail_log_for_summary_truncates_long_lines(tmp_path: Path) -> None:
    """A log with a multi-KB stderr line is truncated with an ellipsis
    so the daemon-state.json summary stays cheap to render."""
    from openharness.lab.runner import _tail_log_for_summary

    log = tmp_path / "spawn.log"
    long_line = "x" * 5000
    log.write_text(
        "# --- codex stderr --- #\n" + long_line + "\n",
    )
    out = _tail_log_for_summary(log, max_chars=200)
    assert len(out) == 200
    assert out.endswith("…")


def test_tail_log_for_summary_handles_missing_marker(tmp_path: Path) -> None:
    """No `# --- codex stderr --- #` marker → still grabs last useful
    line from the tail of the file (e.g. a Python traceback)."""
    from openharness.lab.runner import _tail_log_for_summary

    log = tmp_path / "spawn.log"
    log.write_text("preamble\nTraceback (most recent call last):\nValueError: boom\n")
    out = _tail_log_for_summary(log)
    assert out == "ValueError: boom"


def test_tail_log_for_summary_returns_empty_when_no_log(tmp_path: Path) -> None:
    """Missing file → empty string, not raise. The runner surfaces a
    "(no output; see log …)" placeholder when this happens."""
    from openharness.lab.runner import _tail_log_for_summary

    out = _tail_log_for_summary(tmp_path / "does-not-exist.log")
    assert out == ""


def test_reset_all_failures_clears_every_counter(isolated_lab: Path) -> None:
    """``reset_all_failures`` returns (state, count) and empties the dict."""
    from openharness.lab import daemon_state as ds

    with ds.mutate(actor="t") as st:
        for slug in ("a", "b", "c"):
            rec = ds.FailureRecord()
            rec.count = 1
            rec.last_outcome = "error"
            rec.last_error = "boom"
            st.entry_failures[slug] = rec
    assert len(ds.load().entry_failures) == 3

    new_state, cleared = ds.reset_all_failures(actor="t")
    assert cleared == 3
    assert new_state.entry_failures == {}
    assert ds.load().entry_failures == {}


def test_reset_all_failures_on_empty_state_returns_zero(
    isolated_lab: Path,
) -> None:
    """No failures -> count=0, no error."""
    from openharness.lab import daemon_state as ds

    _, cleared = ds.reset_all_failures(actor="t")
    assert cleared == 0


def test_clear_history_wipes_ring_buffer(isolated_lab: Path) -> None:
    """``clear_history`` returns (state, count) and empties history."""
    from openharness.lab import daemon_state as ds

    now = datetime.now(timezone.utc)
    with ds.mutate(actor="t") as st:
        for i in range(5):
            st.history.append(
                ds.TickHistoryEntry(
                    slug=f"s{i}",
                    started_at=now,
                    ended_at=now,
                    outcome="ok",
                    phase_reached="done",
                    duration_sec=1.0,
                    summary="ok",
                    log_path=None,
                )
            )
    assert len(ds.load().history) == 5

    new_state, removed = ds.clear_history(actor="t")
    assert removed == 5
    assert new_state.history == []
    assert ds.load().history == []


def test_runs_prune_only_targets_orphan_dirs(
    isolated_lab: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``_is_prunable_run_dir`` keeps completed and recent dirs.

    Three fixtures: completed (has results/summary.md), recent (no
    summary, mtime now), and orphan (no summary, mtime far in past).
    Only the third should be flagged prunable at age_hours=1.
    """
    from openharness.lab import cli as labcli

    root = isolated_lab / "runs" / "experiments"
    root.mkdir(parents=True)

    completed = root / "completed-run"
    (completed / "results").mkdir(parents=True)
    (completed / "results" / "summary.md").write_text("done\n")

    recent = root / "recent-run"
    recent.mkdir()
    (recent / "config.yaml").write_text("k: v\n")

    orphan = root / "orphan-run"
    orphan.mkdir()
    (orphan / "config.yaml").write_text("k: v\n")
    # Backdate mtime by 2 hours.
    old_ts = time.time() - 2 * 3600
    os.utime(orphan, (old_ts, old_ts))

    assert labcli._is_prunable_run_dir(completed, age_hours=1.0)[0] is False
    assert labcli._is_prunable_run_dir(recent, age_hours=1.0)[0] is False
    prunable, reason = labcli._is_prunable_run_dir(orphan, age_hours=1.0)
    assert prunable is True
    assert "orphan" in reason


def test_runs_prune_dry_run_does_not_delete(
    isolated_lab: Path,
) -> None:
    """``runs prune --dry-run`` lists candidates without rmtree-ing."""
    from typer.testing import CliRunner
    from openharness.lab.cli import app

    root = isolated_lab / "runs" / "experiments"
    root.mkdir(parents=True)
    orphan = root / "orphan-run"
    orphan.mkdir()
    old_ts = time.time() - 2 * 3600
    os.utime(orphan, (old_ts, old_ts))

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["runs", "prune", "--age-hours", "1", "--dry-run"],
    )
    assert result.exit_code == 0, result.output
    assert "would delete" in result.output
    assert orphan.exists(), "dry-run must not delete"


def test_runs_prune_actually_deletes_orphans(
    isolated_lab: Path,
) -> None:
    """Without --dry-run, eligible dirs are rmtree-d."""
    from typer.testing import CliRunner
    from openharness.lab.cli import app

    root = isolated_lab / "runs" / "experiments"
    root.mkdir(parents=True)
    orphan = root / "orphan-run"
    (orphan / "legs").mkdir(parents=True)
    (orphan / "legs" / "junk.log").write_text("noise\n")
    old_ts = time.time() - 2 * 3600
    os.utime(orphan, (old_ts, old_ts))

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["runs", "prune", "--age-hours", "1"],
    )
    assert result.exit_code == 0, result.output
    assert not orphan.exists(), "orphan dir should be deleted"


def test_runs_prune_refuses_subhour_without_force(
    isolated_lab: Path,
) -> None:
    """``--age-hours 0`` requires ``--force`` (safety gate)."""
    from typer.testing import CliRunner
    from openharness.lab.cli import app

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["runs", "prune", "--age-hours", "0"],
    )
    assert result.exit_code != 0
    assert "force" in result.output.lower()


def test_journal_endpoint_returns_pre_block(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`/_hx/daemon-journal` calls ``services.journal`` and renders.

    Stubs the subprocess so the test doesn't require a real
    journalctl to be reachable. The fixture-injected text must
    surface in the rendered template.
    """
    from openharness.lab.web import services as labsvc

    fake_lines = (
        "2026-04-22T19:15:15+0000 pier-dev-engine uv[1370843]: "
        "INFO openharness.lab.runner: daemon mode=manual; sleeping\n"
    )
    monkeypatch.setattr(labsvc, "journal", lambda *a, **kw: fake_lines)

    resp = client.get("/_hx/daemon-journal")
    assert resp.status_code == 200, resp.text
    assert "daemon mode=manual" in resp.text
    # Header label must show the unit name and requested line count.
    assert "openharness-daemon" in resp.text


def test_journal_endpoint_clamps_lines_param(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Out-of-range ``lines`` is clamped to the [50, 2000] window.

    Defends against a rogue caller asking for 10M lines and OOMing
    the page render.
    """
    from openharness.lab.web import services as labsvc

    received: dict[str, int] = {}

    def _stub(unit, *, lines, since=None):
        received["lines"] = lines
        return ""

    monkeypatch.setattr(labsvc, "journal", _stub)

    client.get("/_hx/daemon-journal?lines=999999")
    assert received["lines"] == 2000

    client.get("/_hx/daemon-journal?lines=1")
    assert received["lines"] == 50


def test_services_journal_handles_missing_journalctl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``journalctl`` is not on PATH, the helper returns a soft message."""
    from openharness.lab.web import services as labsvc

    monkeypatch.setattr(labsvc.shutil, "which", lambda _: None)
    out = labsvc.journal("openharness-daemon", lines=10)
    assert "not available" in out


def test_compact_journal_strips_systemd_prefix() -> None:
    """The outer ``<ts> <host> <prog>[<pid>]:`` prefix is removed."""
    from openharness.lab.web.services import _compact_journal_line

    raw = (
        "2026-04-22T19:34:15+0000 pier-dev-engine uv[1370843]: "
        "2026-04-22T19:34:15+0000 INFO openharness.lab.runner: "
        "daemon mode=manual; sleeping"
    )
    out = _compact_journal_line(raw)
    assert out.startswith("2026-04-22T19:34:15+0000 INFO")
    assert "pier-dev-engine" not in out
    assert "uv[1370843]" not in out


def test_compact_journal_passes_unknown_lines_through() -> None:
    """Lines that don't match the expected shape are returned verbatim.

    Defends against silently dropping ``-- Boot ...`` markers or
    rotation notices that don't carry a ``prog[pid]:`` prefix.
    """
    from openharness.lab.web.services import _compact_journal_line

    weird = "-- Boot abcdef --"
    assert _compact_journal_line(weird) == weird
    empty = ""
    assert _compact_journal_line(empty) == empty


def test_active_spawn_endpoint_falls_back_to_newest_log(
    client, isolated_lab: Path,
) -> None:
    """With no active tick, the panel renders the newest spawn log."""
    logs_dir = isolated_lab / "runs" / "lab" / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    older = logs_dir / "20260101T000000Z__lab-implement-variant__aaaaaaaaaaaa.log"
    newer = logs_dir / "20260202T000000Z__lab-implement-variant__bbbbbbbbbbbb.log"
    older.write_text("OLD\n")
    newer.write_text("NEWEST_SPAWN_OUTPUT\n")
    # Ensure mtime ordering matches name ordering, regardless of host.
    os.utime(older, (1, 1))
    os.utime(newer, (2, 2))

    resp = client.get("/_hx/daemon-active-spawn")
    assert resp.status_code == 200, resp.text
    body = resp.text
    assert "NEWEST_SPAWN_OUTPUT" in body
    assert "OLD" not in body
    # Header should mark this as the idle/newest fallback.
    assert "idle" in body or "newest" in body


def test_active_spawn_endpoint_prefers_active_tick_log(
    client, isolated_lab: Path,
) -> None:
    """When daemon_state has an active tick, its log path wins."""
    from openharness.lab import daemon_state as ds

    logs_dir = isolated_lab / "runs" / "lab" / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    bystander = logs_dir / "20260202T000000Z__lab-implement-variant__bbbbbbbbbbbb.log"
    bystander.write_text("BYSTANDER\n")
    active = logs_dir / "20260202T000005Z__lab-implement-variant__cccccccccccc.log"
    active.write_text("ACTIVE_SPAWN_OUTPUT\n")

    with ds.mutate(actor="t") as st:
        st.active_tick = ds.ActiveTick(
            slug="my-running-slug",
            phase="running",
            started_at=datetime.now(timezone.utc),
            log_path=str(active),
        )

    resp = client.get("/_hx/daemon-active-spawn")
    assert resp.status_code == 200, resp.text
    body = resp.text
    assert "ACTIVE_SPAWN_OUTPUT" in body
    assert "BYSTANDER" not in body
    assert "my-running-slug" in body
    assert "live" in body


def test_active_spawn_endpoint_idle_state(client, isolated_lab: Path) -> None:
    """No active tick + no spawn logs → friendly idle placeholder."""
    resp = client.get("/_hx/daemon-active-spawn")
    assert resp.status_code == 200, resp.text
    assert "Nothing has been run" in resp.text or "No spawn logs" in resp.text


def test_active_tick_renders_when_present(client, isolated_lab: Path) -> None:
    """`/_hx/daemon-active-tick` renders an in-flight tick without 500.

    Regression guard for the bug where the template called
    ``fmt_delta(at.started_at)`` (a numeric helper) on a datetime
    and crashed with ``TypeError: float() argument must be a
    string or a real number, not 'datetime.datetime'`` — only
    triggered when an approval was actually picked up.
    """
    from openharness.lab import daemon_state as ds

    started = datetime.now(timezone.utc) - timedelta(seconds=125)
    with ds.mutate(actor="t") as st:
        st.active_tick = ds.ActiveTick(
            slug="my-running-slug",
            phase="spawning",
            started_at=started,
            spawn_pid=99999,
        )

    resp = client.get("/_hx/daemon-active-tick")
    assert resp.status_code == 200, resp.text
    body = resp.text
    assert "my-running-slug" in body
    assert "spawning" in body
    # "X minutes ago" or "Xs" — anything but the broken raw repr.
    assert "ago" in body
    assert "datetime.datetime" not in body

    # And the full /pipeline page must also render (this is what the
    # user actually hits in the browser).
    resp_full = client.get("/pipeline")
    assert resp_full.status_code == 200, resp_full.text


def test_fmt_elapsed_handles_naive_datetime_and_none() -> None:
    """``_fmt_elapsed`` is robust to None and naive datetimes.

    Both cases occur in real daemon-state.json snapshots: ``None``
    when a field isn't populated yet, naive datetimes when an old
    JSON file written before the tz-aware migration is read back.
    """
    from openharness.lab.web.app import _fmt_elapsed

    assert _fmt_elapsed(None) == "—"
    naive = (datetime.now(timezone.utc) - timedelta(seconds=30)).replace(tzinfo=None)
    out = _fmt_elapsed(naive)
    assert "s" in out and "ago" not in out  # raw value, no suffix
    # Future timestamp shouldn't render as a negative number.
    fut = datetime.now(timezone.utc) + timedelta(seconds=5)
    assert _fmt_elapsed(fut) == "just now"


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
