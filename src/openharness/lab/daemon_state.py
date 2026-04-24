"""Daemon runtime state — the single contract between the orchestrator,
the CLI, and the web UI.

The orchestrator (``runner.py``) used to make every scheduling
decision implicitly: it picked the top of ``lab/roadmap.md`` and ran
it, no matter what. That was fine while the daemon was idle most of
the time, but it produced a 5-hour quota-burn loop the first time a
roadmap entry refused to run cleanly (no provider creds → REFUSE
every tick → no exit gate → infinite retry).

This module makes the daemon's state **explicit and inspectable**.
Three orthogonal pieces live here:

1. ``mode`` — ``paused`` / ``manual`` / ``autonomous``.
   The daemon defaults to ``manual``: it only picks roadmap entries
   the operator has explicitly approved. ``autonomous`` falls back
   to the legacy "top of the queue" behaviour, but with the failure
   gate (#3 below) always on. ``paused`` is a hard stop — the
   daemon process keeps running but does no work.

2. ``approved_slugs`` — set of roadmap slugs the operator has
   explicitly green-lit. In ``manual`` mode, the daemon will only
   ever process a slug that's in this set, and the approval is
   **consumed** (removed) on success or terminal failure. In
   ``autonomous`` mode, the set is ignored.

3. ``entry_failures`` — per-slug failure counter. After
   ``max_failures_before_demote`` consecutive failures, the slug is
   blocked in daemon state so it stops eating quota. The counter
   resets on success or by operator command.

Plus two pieces of "what's happening right now" data the web UI
relies on:

- ``active_tick`` — populated for the duration of one
  ``_process_entry`` call. Tracks slug, phase, started_at, the
  current codex spawn pid (so the UI's "Cancel" button can
  SIGTERM precisely the right process).

- ``history`` — ring buffer of the last 50 tick outcomes. Used by
  the web UI's "Recent ticks" panel and `lab daemon state` CLI.

Storage is a single JSON file at
``runs/lab/daemon-state.json``. Writes are atomic (write to
``.tmp`` + ``os.replace``) under a per-process file lock so the
runner and the CLI can mutate it concurrently without losing
updates.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Literal

from openharness.lab.paths import LAB_RUNS_ROOT, ensure_lab_runs_dir

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

DaemonMode = Literal["paused", "manual", "autonomous"]
"""Three operating modes; see module docstring."""

DEFAULT_MODE: DaemonMode = "manual"
"""New deployments boot in ``manual``. The user explicitly chose this:
the autonomous mode burned quota in a loop on the first failed entry,
and the lab is too expensive to leave unsupervised at the current
maturity. Switch to ``autonomous`` when you trust the gates."""

DEFAULT_MAX_FAILURES_BEFORE_DEMOTE = 2
"""After this many consecutive REFUSE / no-run-dir / spawn-error
outcomes for the same slug, the daemon skips it until the operator
resets the failure counter. 2 is conservative — one bad config → at
most ~6 minutes of looping at the typical ~3-min tick cadence — but
high enough to tolerate a flaky network.
"""

HISTORY_LIMIT = 50
"""Ring buffer length for ``history``. The web UI shows the latest
20-25 anyway; the extra headroom is for ``lab daemon state`` /
``lab query`` style introspection."""

TickPhase = Literal[
    # New phased pipeline (one per `_process_entry_phased` step):
    "preflight",           # git clean-check + worktree create (deterministic)
    "design",              # `lab-design-variant` codex spawn (read-only)
    "implement",           # `lab-implement-variant` codex spawn (worktree-write)
    "run",                 # harbor exec + poll for results/summary.md
    "critique",            # ingest + per-trial critic fan-out + tree apply
    "replan",              # `lab-replan-roadmap` codex spawn + roadmap rewrite
    "finalize",            # `lab-finalize-pr` codex spawn + cleanup
    # Post-pipeline / generic:
    "done",                # successful close-out, awaiting next tick
    # Legacy values kept so historical `daemon-state.json` files still
    # rehydrate cleanly. The new pipeline never writes these.
    "verdict-pending",
    "spawning",
    "running",
    "post-processing",
]
"""Coarse-grained phases the runner advances through inside one tick.
Surfaced verbatim by the web UI's `Current tick` panel so the
operator can tell "still designing the variant" from "polling the
harbor run" from "rewriting the roadmap after critique"."""

TickOutcome = Literal[
    "ok",                  # full pipeline succeeded
    "refuse",              # codex skill returned REFUSE (e.g. missing creds)
    "no-run-dir",          # skill said OK but no new run dir appeared
    "timeout",             # results/summary.md never landed
    "error",               # unhandled exception in the pipeline
    "cancelled",           # operator pressed Cancel
    "auto-demoted",        # legacy: exit gate pushed entry to Suggested
    "blocked",             # exit gate fired — skipped until failures reset
]
"""How a tick ended. Drives both the failure counter and the history
panel rendering (color, icon, retry suggestion)."""


@dataclass(eq=False, slots=True)
class ActiveTick:
    """The tick currently in flight, or absent.

    Cleared on clean shutdown so the next start doesn't show a stale
    'still running' state. The ``spawn_pid`` is the codex process
    the operator's Cancel button targets — see
    :func:`openharness.lab.web.commands._precheck_kill_process` for
    the safety check that ensures we only ever SIGTERM descendants of
    the orchestrator.
    """

    slug: str
    phase: TickPhase
    started_at: datetime
    spawn_pid: int | None = None
    log_path: str | None = None
    worktree_path: str | None = None
    # Free-form most-recent status line surfaced to the UI. Optional.
    note: str | None = None


@dataclass(eq=False, slots=True)
class FailureRecord:
    """Per-slug failure counter feeding the block gate."""

    count: int = 0
    last_error: str | None = None
    last_outcome: TickOutcome | None = None
    last_failed_at: datetime | None = None


@dataclass(eq=False, slots=True)
class TickHistoryEntry:
    """One row in the ring buffer."""

    slug: str
    started_at: datetime
    ended_at: datetime
    outcome: TickOutcome
    phase_reached: TickPhase
    duration_sec: float
    summary: str | None = None
    log_path: str | None = None


@dataclass(eq=False, slots=True)
class DaemonState:
    """The whole runtime state. Persisted as ``daemon-state.json``."""

    mode: DaemonMode = DEFAULT_MODE
    approved_slugs: list[str] = field(default_factory=list)
    active_tick: ActiveTick | None = None
    entry_failures: dict[str, FailureRecord] = field(default_factory=dict)
    max_failures_before_demote: int = DEFAULT_MAX_FAILURES_BEFORE_DEMOTE
    history: list[TickHistoryEntry] = field(default_factory=list)
    # Bumped every write; useful for optimistic concurrency / debugging.
    schema_version: int = 1
    last_updated_at: datetime | None = None
    last_updated_by: str | None = None


# ---------------------------------------------------------------------------
# Disk layout
# ---------------------------------------------------------------------------

DAEMON_STATE_PATH: Path = LAB_RUNS_ROOT / "daemon-state.json"
"""Lives under ``runs/lab/`` so it inherits the existing gitignore."""

_LOCK_PATH: Path = LAB_RUNS_ROOT / "daemon-state.lock"
"""Separate file used purely for ``flock``. We deliberately don't
flock the state file itself because some readers (the web UI) only
need a snapshot and shouldn't have to wait on a writer."""


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def _ts(value: datetime | None) -> str | None:
    """Serialize a datetime as RFC 3339 string, ``None`` if absent."""
    return value.isoformat() if value is not None else None


def _parse_ts(value: str | None) -> datetime | None:
    if value is None:
        return None
    # ``fromisoformat`` accepts both "+00:00" and the trailing "Z" since
    # Python 3.11. Older versions need a fallback, but the project
    # already requires 3.11+ for other reasons.
    return datetime.fromisoformat(value)


def _state_to_dict(state: DaemonState) -> dict[str, Any]:
    """Convert :class:`DaemonState` to a JSON-friendly dict.

    We don't use ``dataclasses.asdict`` directly because nested
    datetimes need ISO-string conversion; doing it field-by-field is
    explicit and unambiguous.
    """
    out: dict[str, Any] = {
        "schema_version": state.schema_version,
        "mode": state.mode,
        "approved_slugs": list(state.approved_slugs),
        "max_failures_before_demote": state.max_failures_before_demote,
        "last_updated_at": _ts(state.last_updated_at),
        "last_updated_by": state.last_updated_by,
    }
    if state.active_tick is not None:
        at = state.active_tick
        out["active_tick"] = {
            "slug": at.slug,
            "phase": at.phase,
            "started_at": _ts(at.started_at),
            "spawn_pid": at.spawn_pid,
            "log_path": at.log_path,
            "worktree_path": at.worktree_path,
            "note": at.note,
        }
    else:
        out["active_tick"] = None
    out["entry_failures"] = {
        slug: {
            "count": rec.count,
            "last_error": rec.last_error,
            "last_outcome": rec.last_outcome,
            "last_failed_at": _ts(rec.last_failed_at),
        }
        for slug, rec in state.entry_failures.items()
    }
    out["history"] = [
        {
            "slug": h.slug,
            "started_at": _ts(h.started_at),
            "ended_at": _ts(h.ended_at),
            "outcome": h.outcome,
            "phase_reached": h.phase_reached,
            "duration_sec": h.duration_sec,
            "summary": h.summary,
            "log_path": h.log_path,
        }
        for h in state.history
    ]
    return out


def _state_from_dict(data: dict[str, Any]) -> DaemonState:
    """Rehydrate :class:`DaemonState` from a JSON dict.

    Tolerant of missing fields so we don't need a migration step
    every time we add one. Unknown fields are silently dropped.
    """
    active = data.get("active_tick")
    active_tick: ActiveTick | None = None
    if active:
        active_tick = ActiveTick(
            slug=active["slug"],
            phase=active["phase"],
            started_at=_parse_ts(active.get("started_at")) or datetime.now(timezone.utc),
            spawn_pid=active.get("spawn_pid"),
            log_path=active.get("log_path"),
            worktree_path=active.get("worktree_path"),
            note=active.get("note"),
        )
    failures = {
        slug: FailureRecord(
            count=rec.get("count", 0),
            last_error=rec.get("last_error"),
            last_outcome=rec.get("last_outcome"),
            last_failed_at=_parse_ts(rec.get("last_failed_at")),
        )
        for slug, rec in (data.get("entry_failures") or {}).items()
    }
    history = [
        TickHistoryEntry(
            slug=h["slug"],
            started_at=_parse_ts(h["started_at"]) or datetime.now(timezone.utc),
            ended_at=_parse_ts(h["ended_at"]) or datetime.now(timezone.utc),
            outcome=h["outcome"],
            phase_reached=h["phase_reached"],
            duration_sec=float(h.get("duration_sec", 0.0)),
            summary=h.get("summary"),
            log_path=h.get("log_path"),
        )
        for h in (data.get("history") or [])
    ]
    return DaemonState(
        mode=data.get("mode", DEFAULT_MODE),
        approved_slugs=list(data.get("approved_slugs") or []),
        active_tick=active_tick,
        entry_failures=failures,
        max_failures_before_demote=int(
            data.get("max_failures_before_demote", DEFAULT_MAX_FAILURES_BEFORE_DEMOTE)
        ),
        history=history,
        schema_version=int(data.get("schema_version", 1)),
        last_updated_at=_parse_ts(data.get("last_updated_at")),
        last_updated_by=data.get("last_updated_by"),
    )


# ---------------------------------------------------------------------------
# Atomic read/write
# ---------------------------------------------------------------------------


def load() -> DaemonState:
    """Return current state, creating a fresh default if absent.

    The default is *not* persisted on read — only :func:`save` writes.
    This keeps the file's mtime meaningful (= "last actual mutation").
    """
    if not DAEMON_STATE_PATH.is_file():
        return DaemonState()
    try:
        data = json.loads(DAEMON_STATE_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        # Corrupted file. Returning a fresh default rather than
        # raising lets the operator recover by clicking buttons in
        # the UI; the corrupted file is overwritten on the next save.
        return DaemonState()
    return _state_from_dict(data)


def save(state: DaemonState, *, actor: str | None = None) -> None:
    """Persist state atomically. Bumps ``last_updated_at`` / ``_by``.

    Atomic = write to a sibling tempfile, then ``os.replace``. This
    guarantees readers always see either the previous version or the
    new one, never a partial write — important because the web UI
    polls this file every few seconds.
    """
    ensure_lab_runs_dir()
    state.last_updated_at = datetime.now(timezone.utc)
    if actor:
        state.last_updated_by = actor
    payload = json.dumps(_state_to_dict(state), indent=2, sort_keys=False)
    tmp = DAEMON_STATE_PATH.with_suffix(".tmp")
    tmp.write_text(payload)
    os.replace(tmp, DAEMON_STATE_PATH)


@contextlib.contextmanager
def mutate(*, actor: str | None = None) -> Iterator[DaemonState]:
    """Read-modify-write under an exclusive file lock.

    Use this from any code path that wants to change state — the
    runner mid-tick, the CLI commands, the web UI's POST handlers.
    The lock is released even on exception, so a crash in the middle
    of mutation can't deadlock the next caller.

    Concurrency model: there is at most one daemon process (the
    orchestrator lock guarantees that), but the CLI and web UI can
    each call ``mutate`` in parallel with the daemon. ``flock`` on
    a sibling file means whoever calls first wins; the other waits
    a few ms.
    """
    ensure_lab_runs_dir()
    _LOCK_PATH.touch(exist_ok=True)
    fd = os.open(_LOCK_PATH, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        state = load()
        yield state
        save(state, actor=actor)
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


# ---------------------------------------------------------------------------
# Convenience mutators (used by both the runner and the CLI)
# ---------------------------------------------------------------------------


def set_mode(mode: DaemonMode, *, actor: str | None = None) -> DaemonState:
    """Change the daemon's operating mode."""
    if mode not in ("paused", "manual", "autonomous"):
        raise ValueError(f"invalid mode: {mode!r}")
    with mutate(actor=actor) as st:
        st.mode = mode
        return st


def approve(slug: str, *, actor: str | None = None) -> DaemonState:
    """Add a slug to the approval list (no-op if already there)."""
    with mutate(actor=actor) as st:
        if slug not in st.approved_slugs:
            st.approved_slugs.append(slug)
        return st


def revoke(slug: str, *, actor: str | None = None) -> DaemonState:
    """Remove a slug from the approval list (no-op if absent)."""
    with mutate(actor=actor) as st:
        st.approved_slugs = [s for s in st.approved_slugs if s != slug]
        return st


def consume_approval(slug: str, *, actor: str | None = None) -> bool:
    """Pop a slug from the approval list. Returns whether it was there.

    Called by the runner after it has decided to process ``slug``,
    so a single approval grants exactly one tick. (Per the operator's
    "approval is consumed" choice.)
    """
    with mutate(actor=actor) as st:
        if slug in st.approved_slugs:
            st.approved_slugs = [s for s in st.approved_slugs if s != slug]
            return True
        return False


def begin_tick(active: ActiveTick, *, actor: str | None = None) -> DaemonState:
    """Record that a tick has begun. Called by the runner."""
    with mutate(actor=actor) as st:
        st.active_tick = active
        return st


def update_tick(
    *,
    phase: TickPhase | None = None,
    spawn_pid: int | None = None,
    log_path: str | None = None,
    worktree_path: str | None = None,
    note: str | None = None,
    actor: str | None = None,
) -> DaemonState:
    """Patch the active tick's mutable fields."""
    with mutate(actor=actor) as st:
        if st.active_tick is None:
            return st
        at = st.active_tick
        if phase is not None:
            at.phase = phase
        if spawn_pid is not None:
            at.spawn_pid = spawn_pid
        if log_path is not None:
            at.log_path = log_path
        if worktree_path is not None:
            at.worktree_path = worktree_path
        if note is not None:
            at.note = note
        return st


def end_tick(
    *,
    outcome: TickOutcome,
    summary: str | None = None,
    actor: str | None = None,
) -> tuple[DaemonState, FailureRecord | None]:
    """Clear the active tick and record one history entry.

    On a non-success outcome, increment the slug's failure counter
    and return it so the runner can decide whether to block the slug.
    On success, reset the counter.

    Return shape ``(state, failure_record_or_none)``: the second
    value is the post-update FailureRecord for the tick's slug, or
    ``None`` if the tick succeeded (counter reset). The runner uses
    it to compare against ``max_failures_before_demote`` without
    having to re-load state.
    """
    with mutate(actor=actor) as st:
        if st.active_tick is None:
            return st, None
        at = st.active_tick
        ended_at = datetime.now(timezone.utc)
        st.history.append(
            TickHistoryEntry(
                slug=at.slug,
                started_at=at.started_at,
                ended_at=ended_at,
                outcome=outcome,
                phase_reached=at.phase,
                duration_sec=(ended_at - at.started_at).total_seconds(),
                summary=summary,
                log_path=at.log_path,
            )
        )
        if len(st.history) > HISTORY_LIMIT:
            st.history = st.history[-HISTORY_LIMIT:]

        slug = at.slug
        rec: FailureRecord | None = None
        if outcome == "ok":
            st.entry_failures.pop(slug, None)
        else:
            rec = st.entry_failures.get(slug) or FailureRecord()
            rec.count += 1
            rec.last_outcome = outcome
            rec.last_error = summary
            rec.last_failed_at = ended_at
            st.entry_failures[slug] = rec

        st.active_tick = None
        return st, rec


def clear_active_tick(*, actor: str | None = None) -> DaemonState:
    """Drop ``active_tick`` without recording history.

    Used by the signal handler on shutdown — the tick wasn't
    properly closed out, but neither did it fail in a way we want
    to count toward the failure gate. The next start sees a clean
    slate.
    """
    with mutate(actor=actor) as st:
        st.active_tick = None
        return st


def reset_failures(slug: str, *, actor: str | None = None) -> DaemonState:
    """Manually clear a slug's failure counter (operator override)."""
    with mutate(actor=actor) as st:
        st.entry_failures.pop(slug, None)
        return st


def reset_all_failures(*, actor: str | None = None) -> tuple[DaemonState, int]:
    """Clear *every* failure counter at once.

    Useful after the operator has fixed a host-level cause (e.g. a
    PATH or credential bug that broke a batch of slugs simultaneously)
    and wants a clean slate without per-slug ``reset-failures`` calls.

    Returns the new state plus the number of slugs whose counter was
    cleared, so the caller can echo a meaningful message to the user.
    The two are returned together to avoid a second mutate-cycle just
    to read the count.
    """
    with mutate(actor=actor) as st:
        cleared = len(st.entry_failures)
        st.entry_failures = {}
        return st, cleared


def clear_history(*, actor: str | None = None) -> tuple[DaemonState, int]:
    """Wipe the tick-history ring buffer.

    The history is a presentation surface (the cockpit's "Recent
    ticks" panel) that the daemon never reads back into its decision
    loop, so dropping it is purely cosmetic — useful when the
    operator wants to start fresh after a noisy debugging session.

    Returns the new state plus the number of entries removed.
    """
    with mutate(actor=actor) as st:
        removed = len(st.history)
        st.history = []
        return st, removed


def notify_daemon() -> bool:
    """Wake the running orchestrator so it re-reads daemon-state.json now.

    Sends ``SIGUSR1`` to the pid recorded in
    ``runs/lab/orchestrator.lock``. The runner's signal handler sets
    a ``threading.Event`` that short-circuits the idle ``Event.wait``
    in ``loop()``, so the next state read happens within milliseconds
    instead of waiting up to ``idle_sleep_sec``.

    Idempotent + safe to call from anywhere: CLI, web UI, tests. If
    the daemon isn't running (no lock, stale lock, malformed JSON,
    pid gone) this returns ``False`` without raising — the caller can
    log "daemon not running" but shouldn't fail because of it.

    The signal is delivered immediately, but only acted on at the
    next ``Event.wait`` boundary. That means: if the daemon is
    mid-tick (i.e. inside ``_process_entry``), the signal is
    effectively queued — the post-tick ``_idle_wait`` returns
    immediately and the next state-driven decision happens right
    away. We never need to interrupt a running tick to apply a state
    change; state is consulted at the *top* of every loop iteration.
    """
    import signal as _signal

    from openharness.lab.paths import ORCHESTRATOR_LOCK_PATH

    if not ORCHESTRATOR_LOCK_PATH.is_file():
        return False
    try:
        payload = json.loads(ORCHESTRATOR_LOCK_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    pid = int(payload.get("pid") or 0)
    if not pid:
        return False
    try:
        os.kill(pid, _signal.SIGUSR1)
        return True
    except (ProcessLookupError, PermissionError):
        return False


__all__ = [
    "DAEMON_STATE_PATH",
    "DEFAULT_MAX_FAILURES_BEFORE_DEMOTE",
    "DEFAULT_MODE",
    "HISTORY_LIMIT",
    "ActiveTick",
    "DaemonMode",
    "DaemonState",
    "FailureRecord",
    "TickHistoryEntry",
    "TickOutcome",
    "TickPhase",
    "approve",
    "begin_tick",
    "clear_active_tick",
    "consume_approval",
    "end_tick",
    "load",
    "mutate",
    "notify_daemon",
    "reset_failures",
    "revoke",
    "save",
    "set_mode",
    "update_tick",
]
