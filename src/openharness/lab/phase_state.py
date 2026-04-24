"""Per-slug phase state for the autonomous lab pipeline.

Each roadmap entry the orchestrator picks up advances through a fixed
sequence of phases (preflight → design → implement → run → critique →
replan → finalize). This module owns the durable record of "where in that
sequence are we, and what artifact did each phase produce", stored as
one JSON file per slug under ``runs/lab/state/<slug>/phases.json``.

The file is the source of truth for **resumability**:

-   On every tick the runner reads ``phases.json`` for the slug and
    *resumes from the first non-``ok`` phase*. This means a crashed
    Implement spawn (e.g. context-window blowout) doesn't re-run the
    Design spawn that already produced ``design.md``.
-   Each phase records the timestamp it succeeded at plus a small
    payload (commit list, run id, PR url, …). Operators can read these
    files directly to debug "what did the daemon think happened here".
-   Failed phases are *not* sticky — the runner clears them at the
    start of each retry attempt, then re-marks them on the new outcome.
    "Stuck on the same phase forever" is detected via the existing
    consecutive-failure counter in :mod:`daemon_state`, not here.

Layout:

    runs/lab/state/
        <slug>/
            phases.json                 # canonical state (this module)
            design.md                   # Phase 1 output (lab-design-variant)
            implement.json              # Phase 2 output (lab-implement-variant)

The ``runs/lab/`` prefix inherits the ``runs/`` gitignore. Nothing
under ``state/`` is ever committed.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Literal

from openharness.lab.paths import LAB_RUNS_ROOT

logger = logging.getLogger(__name__)


PHASE_STATE_ROOT: Path = LAB_RUNS_ROOT / "state"
"""Per-slug subdirs live here. Created lazily on first write."""


PhaseName = Literal[
    "preflight",
    "design",
    "implement",
    "run",
    "critique",
    "replan",
    "finalize",
]
PHASE_ORDER: tuple[PhaseName, ...] = (
    "preflight", "design", "implement", "run", "critique", "replan", "finalize",
)


PhaseStatus = Literal["pending", "running", "ok", "failed", "skipped"]


# Per-phase auto-repair budget. After a phase fails, the orchestrator
# is allowed to spawn the same skill ``MAX_REPAIRS_PER_PHASE`` more
# times with **repair context** (the prior failure messages) injected
# as a CLI argument. After that budget is exhausted, the failure
# becomes sticky and the existing daemon-state consecutive-failure
# counter takes over (and may auto-demote the entry).
#
# Rationale: most "design too rigid → implement REFUSE" failures are
# *contract ambiguities* that one self-correction round can resolve
# (the skill reads its own prior failure and either fixes the
# contract or escalates with a precise blocker). Without this
# budget the operator has to babysit every contract mismatch.
MAX_REPAIRS_PER_PHASE: int = 1


_PRIOR_FAILURE_CAP: int = 3
"""Keep at most the last N failure messages on each phase. Bounds
``phases.json`` size and avoids forwarding stale, unresolved errors
into a fresh repair attempt's prompt context."""


# ---------------------------------------------------------------------------
# Per-phase record
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class PhaseRecord:
    """One row inside ``phases.json``. Carries enough to resume.

    ``payload`` is intentionally a free-form dict: each phase decides
    its own keys (preflight stores ``worktree``/``branch``/``base_sha``;
    implement stores ``commits`` and ``validations``; run stores the
    ``instance_id``; finalize stores ``pr_url``). The runner reads
    these via the typed accessors below — no magic strings.

    ``failure_count`` and ``prior_failures`` drive the auto-repair
    loop: every ``mark_failed`` call appends the new error and
    increments the counter, ``mark_running`` deliberately does NOT
    clear them so the next attempt can read its own history, and
    ``mark_ok`` (success) zeroes them. The counter is what the
    runner consults to decide "repair attempt or give up".
    """

    status: PhaseStatus = "pending"
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    failure_count: int = 0
    prior_failures: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Whole-slug state
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class SlugPhases:
    """The full ``phases.json`` document for one slug."""

    slug: str
    schema_version: int = 1
    started_at: str = ""
    last_updated_at: str = ""
    needs_variant: bool = True
    """If ``False`` (baseline / infrastructure entries), the design and
    implement phases are skipped without spawning anything."""
    phases: dict[PhaseName, PhaseRecord] = field(default_factory=dict)

    def get(self, name: PhaseName) -> PhaseRecord:
        """Return the record for ``name``, materializing a pending one."""
        rec = self.phases.get(name)
        if rec is None:
            rec = PhaseRecord()
            self.phases[name] = rec
        return rec

    def first_unfinished(self) -> PhaseName | None:
        """The leftmost phase whose status is not ``ok`` / ``skipped``.

        ``None`` means every phase is done — the slug is closed.
        """
        for phase in PHASE_ORDER:
            rec = self.phases.get(phase)
            if rec is None or rec.status not in ("ok", "skipped"):
                return phase
        return None


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------


def slug_dir(slug: str) -> Path:
    """Per-slug directory holding ``phases.json`` and any phase artifacts."""
    return PHASE_STATE_ROOT / slug


def state_path(slug: str) -> Path:
    return slug_dir(slug) / "phases.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write(path: Path, text: str) -> None:
    """Write-then-rename to avoid half-written files on crash mid-write.

    The lab daemon and the operator CLI may both read this file at the
    same time; an atomic swap guarantees readers always see either the
    old or the new full document, never a torn one.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp_path, path)
    except Exception:
        # Best-effort cleanup; swallow any errors so we surface the
        # original write failure to the caller.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _to_dict(state: SlugPhases) -> dict[str, Any]:
    return {
        "slug": state.slug,
        "schema_version": state.schema_version,
        "started_at": state.started_at,
        "last_updated_at": state.last_updated_at,
        "needs_variant": state.needs_variant,
        "phases": {name: asdict(rec) for name, rec in state.phases.items()},
    }


def _from_dict(data: dict[str, Any]) -> SlugPhases:
    raw_phases = data.get("phases") or {}
    phases: dict[PhaseName, PhaseRecord] = {}
    for name, rec in raw_phases.items():
        if name in PHASE_ORDER:
            phases[name] = PhaseRecord(
                status=rec.get("status", "pending"),
                started_at=rec.get("started_at"),
                finished_at=rec.get("finished_at"),
                error=rec.get("error"),
                payload=dict(rec.get("payload") or {}),
                failure_count=int(rec.get("failure_count") or 0),
                prior_failures=list(rec.get("prior_failures") or []),
            )
    return SlugPhases(
        slug=data["slug"],
        schema_version=int(data.get("schema_version", 1)),
        started_at=data.get("started_at", ""),
        last_updated_at=data.get("last_updated_at", ""),
        needs_variant=bool(data.get("needs_variant", True)),
        phases=phases,
    )


def load(slug: str) -> SlugPhases | None:
    """Read ``phases.json`` for ``slug``, or ``None`` if it doesn't exist."""
    path = state_path(slug)
    if not path.is_file():
        return None
    try:
        return _from_dict(json.loads(path.read_text()))
    except (OSError, json.JSONDecodeError) as exc:
        logger.exception("phases.json for %s is unreadable: %s", slug, exc)
        return None


def save(state: SlugPhases) -> None:
    """Persist ``state`` atomically. Bumps ``last_updated_at`` automatically."""
    state.last_updated_at = _now_iso()
    if not state.started_at:
        state.started_at = state.last_updated_at
    _atomic_write(state_path(state.slug), json.dumps(_to_dict(state), indent=2))


def load_or_init(slug: str, *, needs_variant: bool = True) -> SlugPhases:
    """Read existing state or create a fresh one and persist it."""
    existing = load(slug)
    if existing is not None:
        return existing
    state = SlugPhases(
        slug=slug,
        started_at=_now_iso(),
        last_updated_at=_now_iso(),
        needs_variant=needs_variant,
    )
    save(state)
    return state


# ---------------------------------------------------------------------------
# Convenience mutators (used by runner.py one phase at a time)
# ---------------------------------------------------------------------------


def mark_running(slug: str, phase: PhaseName) -> SlugPhases:
    """Flip a phase to ``running`` and stamp ``started_at``.

    Deliberately preserves ``failure_count`` and ``prior_failures`` —
    a repair attempt needs that history visible so the runner can
    decide budget and the skill can read it via repair-context.
    """
    state = load_or_init(slug)
    rec = state.get(phase)
    rec.status = "running"
    rec.started_at = _now_iso()
    rec.finished_at = None
    rec.error = None
    save(state)
    return state


def mark_ok(
    slug: str,
    phase: PhaseName,
    *,
    payload: dict[str, Any] | None = None,
) -> SlugPhases:
    """Flip a phase to ``ok``, stamp ``finished_at``, merge ``payload``.

    Success terminates the failure history: ``failure_count`` and
    ``prior_failures`` are zeroed so a future failure of the same
    phase (after the operator manually re-resets it) starts a fresh
    repair budget.
    """
    state = load_or_init(slug)
    rec = state.get(phase)
    rec.status = "ok"
    rec.finished_at = _now_iso()
    rec.error = None
    rec.failure_count = 0
    rec.prior_failures = []
    if payload:
        rec.payload.update(payload)
    save(state)
    return state


def mark_skipped(
    slug: str,
    phase: PhaseName,
    *,
    reason: str,
) -> SlugPhases:
    """Mark a phase as ``skipped`` (e.g. design+implement on a baseline)."""
    state = load_or_init(slug)
    rec = state.get(phase)
    rec.status = "skipped"
    rec.finished_at = _now_iso()
    rec.error = None
    rec.payload["skip_reason"] = reason
    save(state)
    return state


def mark_failed(
    slug: str,
    phase: PhaseName,
    *,
    error: str,
    payload: dict[str, Any] | None = None,
) -> SlugPhases:
    """Flip a phase to ``failed`` with an operator-readable error string.

    Side-effects on the auto-repair fields:

    -   Increments ``failure_count``.
    -   Appends the truncated ``error`` to ``prior_failures``,
        capped at :data:`_PRIOR_FAILURE_CAP` (oldest dropped). This
        is what gets fed into the next attempt's repair-context
        prompt block.

    Both fields survive across ``mark_running`` and only reset on
    ``mark_ok`` (success) or ``reset_phase`` (operator escape hatch).
    """
    state = load_or_init(slug)
    rec = state.get(phase)
    rec.status = "failed"
    rec.finished_at = _now_iso()
    truncated = error[:1000]
    rec.error = truncated
    rec.failure_count += 1
    rec.prior_failures.append(truncated)
    if len(rec.prior_failures) > _PRIOR_FAILURE_CAP:
        rec.prior_failures = rec.prior_failures[-_PRIOR_FAILURE_CAP:]
    if payload:
        rec.payload.update(payload)
    save(state)
    return state


def reset_phase(slug: str, phase: PhaseName) -> SlugPhases:
    """Drop the record for ``phase`` so it re-runs on the next tick.

    The runner calls this at the start of each retry attempt so failed
    payloads don't bleed into the next run's logs. Earlier phases'
    ``ok`` records survive untouched.
    """
    state = load_or_init(slug)
    state.phases.pop(phase, None)
    save(state)
    return state


def reset_all(slug: str) -> None:
    """Delete the entire ``phases.json`` for ``slug``.

    Operator escape hatch — used when a slug is so badly stuck the
    cleanest option is to start the pipeline from scratch.
    """
    path = state_path(slug)
    if path.is_file():
        path.unlink()


# ---------------------------------------------------------------------------
# Iteration / introspection helpers (CLI + web UI)
# ---------------------------------------------------------------------------


def all_slugs() -> Iterator[str]:
    """Yield every slug that has ever had state recorded.

    Order: filesystem mtime, newest first. Useful for the operator
    asking "what was the last thing the daemon was doing".
    """
    if not PHASE_STATE_ROOT.is_dir():
        return
    entries = [p for p in PHASE_STATE_ROOT.iterdir() if p.is_dir()]
    entries.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    for p in entries:
        if (p / "phases.json").is_file():
            yield p.name
