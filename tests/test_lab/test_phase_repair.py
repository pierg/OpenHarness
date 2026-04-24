"""Tests for the per-phase auto-repair loop.

Two layers of coverage:

1.  **State module** (``phase_state``): ``mark_failed`` accumulates a
    failure history, ``mark_running`` preserves it, ``mark_ok`` clears
    it. The ``failure_count`` and ``prior_failures`` fields survive a
    save/load roundtrip (back-compat with old files where they're
    absent: defaults to 0 / []).

2.  **Runner glue** (``runner``): the helper that materialises the
    repair-context markdown file and emits the right ``--repair-*``
    CLI flags only after at least one prior failure, formatted in
    newest-first order.

The runner-side end-to-end test lives in this same file as a single
``_process_entry`` smoke test that monkeypatches ``codex_adapter.run``
to fail-then-succeed and asserts the second invocation got
``--repair-context=`` injected.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Isolated lab tree (mirror of test_daemon_state.isolated_lab so this
# file is fully self-contained — it does NOT import that fixture).
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_lab(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Spin up a self-contained `lab/` + `runs/lab/` under tmp_path."""
    repo = tmp_path / "repo"
    (repo / "lab").mkdir(parents=True)
    (repo / "runs" / "lab").mkdir(parents=True)
    (repo / "pyproject.toml").write_text("# placeholder for repo-detection")

    monkeypatch.setenv("OPENHARNESS_REPO_ROOT", str(repo))

    import openharness.lab.paths as paths
    importlib.reload(paths)
    import openharness.lab.phase_state as ps
    importlib.reload(ps)

    return repo


# ---------------------------------------------------------------------------
# State module
# ---------------------------------------------------------------------------


def test_mark_failed_appends_to_prior_failures(isolated_lab: Path) -> None:
    """Each ``mark_failed`` grows the history and the counter."""
    import openharness.lab.phase_state as ps

    ps.mark_running("alpha", "implement")
    ps.mark_failed("alpha", "implement", error="first boom")
    ps.mark_failed("alpha", "implement", error="second boom")

    state = ps.load("alpha")
    assert state is not None
    rec = state.get("implement")
    assert rec.status == "failed"
    assert rec.failure_count == 2
    assert rec.prior_failures == ["first boom", "second boom"]
    assert rec.error == "second boom"


def test_prior_failures_are_capped(isolated_lab: Path) -> None:
    """Only the most recent N=cap failures survive — bounds JSON size."""
    import openharness.lab.phase_state as ps

    cap = ps._PRIOR_FAILURE_CAP
    for i in range(cap + 2):
        ps.mark_failed("alpha", "implement", error=f"boom-{i}")
    rec = ps.load("alpha").get("implement")
    assert len(rec.prior_failures) == cap
    assert rec.prior_failures[-1] == f"boom-{cap + 1}"
    assert rec.prior_failures[0] == f"boom-{2}"  # earliest two dropped
    assert rec.failure_count == cap + 2  # counter keeps the true total


def test_mark_running_preserves_repair_history(isolated_lab: Path) -> None:
    """``mark_running`` (called by every retry) must NOT clear the history.

    The repair-context spawn arg is built from ``prior_failures``;
    if ``mark_running`` wiped them the second attempt would have no
    context to act on.
    """
    import openharness.lab.phase_state as ps

    ps.mark_failed("alpha", "implement", error="boom")
    ps.mark_running("alpha", "implement")

    rec = ps.load("alpha").get("implement")
    assert rec.status == "running"
    assert rec.failure_count == 1
    assert rec.prior_failures == ["boom"]


def test_mark_ok_clears_repair_history(isolated_lab: Path) -> None:
    """Success ends the failure history; a future failure starts fresh."""
    import openharness.lab.phase_state as ps

    ps.mark_failed("alpha", "implement", error="boom")
    ps.mark_ok("alpha", "implement", payload={"commits": ["abc"]})

    rec = ps.load("alpha").get("implement")
    assert rec.status == "ok"
    assert rec.failure_count == 0
    assert rec.prior_failures == []
    assert rec.payload == {"commits": ["abc"]}


def test_phase_state_back_compat_loads_old_records(isolated_lab: Path) -> None:
    """Older ``phases.json`` files (no failure_count / prior_failures) load fine."""
    import json as _json

    import openharness.lab.phase_state as ps

    legacy = {
        "slug": "alpha",
        "schema_version": 1,
        "started_at": "2026-01-01T00:00:00+00:00",
        "last_updated_at": "2026-01-01T00:00:00+00:00",
        "needs_variant": True,
        "phases": {
            "implement": {
                "status": "failed",
                "started_at": "2026-01-01T00:00:00+00:00",
                "finished_at": "2026-01-01T00:00:01+00:00",
                "error": "old boom",
                "payload": {},
            },
        },
    }
    path = ps.state_path("alpha")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_json.dumps(legacy))

    loaded = ps.load("alpha")
    assert loaded is not None
    rec = loaded.get("implement")
    assert rec.status == "failed"
    assert rec.failure_count == 0  # default for legacy rows
    assert rec.prior_failures == []


# ---------------------------------------------------------------------------
# Runner glue: repair-context file + CLI flags
# ---------------------------------------------------------------------------


def test_repair_args_empty_on_first_attempt(isolated_lab: Path) -> None:
    """No prior failures → no ``--repair-*`` flags injected."""
    import openharness.lab.phase_state as ps
    import openharness.lab.runner as runner
    importlib.reload(runner)

    ps.mark_running("alpha", "implement")
    state = ps.load("alpha")
    assert runner._repair_args("alpha", "implement", state) == []


def test_repair_args_emits_flags_after_failure(isolated_lab: Path) -> None:
    """One prior failure → exactly two flags + a markdown file on disk."""
    import openharness.lab.phase_state as ps
    import openharness.lab.runner as runner
    importlib.reload(runner)

    ps.mark_failed("alpha", "implement", error="REFUSE; cannot find slice")
    state = ps.load("alpha")
    args = runner._repair_args("alpha", "implement", state)

    assert len(args) == 2
    repair_arg = next(a for a in args if a.startswith("--repair-context="))
    attempt_arg = next(a for a in args if a.startswith("--repair-attempt="))
    assert attempt_arg == "--repair-attempt=2", "1-indexed retry # past first failure"

    repair_path = Path(repair_arg.split("=", 1)[1])
    assert repair_path.is_file()
    body = repair_path.read_text()
    assert "Repair context" in body
    assert "alpha" in body and "implement" in body
    assert "REFUSE; cannot find slice" in body
    assert "design_amendment" in body, "must mention amendment channel"


def test_repair_context_orders_failures_newest_first(isolated_lab: Path) -> None:
    """Most recent failure goes at the top of the prompt context."""
    import openharness.lab.phase_state as ps
    import openharness.lab.runner as runner
    importlib.reload(runner)

    ps.mark_failed("alpha", "implement", error="oldest boom")
    ps.mark_failed("alpha", "implement", error="newest boom")
    state = ps.load("alpha")
    args = runner._repair_args("alpha", "implement", state)

    repair_path = Path(args[0].split("=", 1)[1])
    body = repair_path.read_text()
    # "newest boom" must come before "oldest boom" in document order.
    assert body.index("newest boom") < body.index("oldest boom")


def test_repair_args_attempt_number_grows_with_failures(isolated_lab: Path) -> None:
    """1-indexed attempt number = failure_count + 1."""
    import openharness.lab.phase_state as ps
    import openharness.lab.runner as runner
    importlib.reload(runner)

    ps.mark_failed("alpha", "design", error="boom-1")
    args = runner._repair_args("alpha", "design", ps.load("alpha"))
    assert "--repair-attempt=2" in args

    ps.mark_failed("alpha", "design", error="boom-2")
    args = runner._repair_args("alpha", "design", ps.load("alpha"))
    assert "--repair-attempt=3" in args


# ---------------------------------------------------------------------------
# Runner glue: budget-exhaustion short-circuit in _process_entry
# ---------------------------------------------------------------------------


def test_process_entry_short_circuits_when_repair_budget_exhausted(
    isolated_lab: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Once failure_count > MAX_REPAIRS_PER_PHASE, we return error without spawning.

    Without this gate the runner would loop forever on a phase the
    skill keeps refusing for the same reason. The ds.end_tick failure
    counter (cross-tick) then takes over and may block the entry.
    """
    import openharness.lab.phase_state as ps
    import openharness.lab.runner as runner
    importlib.reload(runner)

    # Force budget = 0 so a single prior failure already exhausts repairs.
    monkeypatch.setattr(ps, "MAX_REPAIRS_PER_PHASE", 0)

    # Mark preflight ok so first_unfinished() lands on `design`.
    ps.mark_ok("alpha", "preflight", payload={
        "worktree": "/tmp/wt", "branch": "lab/alpha",
        "base_sha": "abc", "base_branch": "main",
    })
    ps.mark_failed("alpha", "design", error="REFUSE; bad")

    spawned: list[str] = []

    def _fake_run(*_args, **_kwargs):  # pragma: no cover - shouldn't fire
        spawned.append("design")
        raise AssertionError("phase handler must not spawn when budget exhausted")

    monkeypatch.setattr(runner.codex_adapter, "run", _fake_run)

    entry = runner.RoadmapEntry(
        slug="alpha", body="", idea_id="some-idea", hypothesis="h",
    )
    cfg = runner.OrchestratorConfig(once=True)
    result = runner._process_entry(entry, cfg)

    assert result.ok is False
    assert result.outcome == "error"
    assert "repair budget exhausted" in (result.summary or "")
    assert spawned == [], "no spawn should have been attempted"

    # The phase remains failed so the cross-tick failure gate sees it.
    rec = ps.load("alpha").get("design")
    assert rec.status == "failed"
    assert rec.failure_count == 1


def test_process_entry_retries_failed_preflight_after_host_cleanup(
    isolated_lab: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preflight failures are host-state dependent, so stale failures retry."""
    import openharness.lab.phase_state as ps
    import openharness.lab.runner as runner
    importlib.reload(runner)

    monkeypatch.setattr(ps, "MAX_REPAIRS_PER_PHASE", 0)
    ps.mark_failed("alpha", "preflight", error="parent repo dirty")
    ps.mark_failed("alpha", "preflight", error="parent repo dirty again")

    calls: list[str] = []

    def _preflight(entry, _state, _cfg):
        calls.append(entry.slug)
        ps.mark_ok("alpha", "preflight", payload={
            "worktree": "/tmp/wt",
            "branch": "lab/alpha",
            "base_sha": "abc",
            "base_branch": "main",
        })
        return None

    monkeypatch.setattr(runner, "_PHASE_DISPATCH", (("preflight", _preflight),))

    result = runner._process_entry(
        runner.RoadmapEntry(
            slug="alpha", body="", idea_id="some-idea", hypothesis="h",
        ),
        runner.OrchestratorConfig(once=True),
    )

    assert calls == ["alpha"]
    assert result.ok is True
    assert ps.load("alpha").get("preflight").status == "ok"
