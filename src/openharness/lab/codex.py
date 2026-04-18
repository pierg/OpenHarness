"""Uniform `codex exec` adapter for the lab orchestrator.

Every multi-step "thinking" task in the lab loop (variant
implementation, the four critic skills, task-features extraction)
is invoked through this adapter so we have one failure mode for
agent execution and one shape of audit trail.

Key design points:

- **Skill discovery.** `codex exec` does not (today) take a
  `--skill <id>` flag, but it does pick up skills from the project's
  `.agents/skills/` directory automatically. The adapter therefore
  reads `.agents/skills/<skill_id>/SKILL.md`, validates it exists,
  and inlines its content into the prompt so the model has the
  authoring conventions in context regardless of how codex's
  skill-loader behaves on a given version.

- **Logs.** Per-spawn log at
  `runs/lab/logs/<utc>__<skill>__<short_spawn_id>.log` containing
  the full prompt, the raw `codex exec --json` event stream, the
  agent's last message, and the exit code. A row is also written
  to `spawns` in the DB.

- **Concurrency.** A process-local `threading.BoundedSemaphore`
  caps in-flight spawns. The orchestrator passes its own semaphore
  in for cross-task coordination.

- **Preconditions.** Verifies the codex binary is on `$PATH`,
  authentication is configured (`~/.codex/auth.json` or
  `OPENAI_API_KEY` env), and that no other orchestrator holds the
  lock file (only checked when `enforce_orchestrator_lock=True`).
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator, Sequence

from openharness.lab import db as labdb
from openharness.lab.paths import (
    LAB_LOGS_DIR,
    ORCHESTRATOR_LOCK_PATH,
    REPO_ROOT,
    ensure_lab_runs_dir,
)

logger = logging.getLogger(__name__)

SKILLS_DIR = REPO_ROOT / ".agents" / "skills"
DEFAULT_TIMEOUT_SEC = 60 * 30  # 30 min upper bound on a single critic run.
DEFAULT_MAX_CONCURRENCY = 4


class CodexAdapterError(RuntimeError):
    """Adapter-level failure (skill not found, codex missing, etc.)."""


@dataclass(slots=True)
class SpawnResult:
    spawn_id: str
    skill: str
    args: list[str]
    exit_code: int
    log_path: Path
    last_message: str | None
    started_at: datetime
    finished_at: datetime
    duration_sec: float
    cost_usd_estimate: float | None = None
    parent_run_dir: Path | None = None
    notes: str | None = None

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


@dataclass
class CodexConfig:
    """Per-process configuration shared across spawns.

    Not slotted: we lazily attach a `_semaphore` after construction.
    """

    binary: str = "codex"
    cwd: Path = REPO_ROOT
    sandbox: str = "workspace-write"
    full_auto: bool = True
    extra_codex_args: list[str] = field(default_factory=list)
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY
    timeout_sec: int = DEFAULT_TIMEOUT_SEC
    enforce_orchestrator_lock: bool = False
    record_in_db: bool = True

    def __post_init__(self) -> None:
        self._semaphore = threading.BoundedSemaphore(self.max_concurrency)

    @property
    def semaphore(self) -> threading.BoundedSemaphore:
        return self._semaphore


# ----- skill discovery ------------------------------------------------------


def skill_path(skill_id: str) -> Path:
    """Return the SKILL.md path for `skill_id`, or raise."""
    if not SKILLS_DIR.is_dir():
        raise CodexAdapterError(
            f"Shared skills dir not found: {SKILLS_DIR}. Codex / Cursor expect "
            "skills to live under .agents/skills/<id>/SKILL.md."
        )
    candidate = SKILLS_DIR / skill_id / "SKILL.md"
    if not candidate.is_file():
        raise CodexAdapterError(f"Skill not found: {candidate}")
    return candidate


def list_skills() -> list[str]:
    if not SKILLS_DIR.is_dir():
        return []
    return sorted(
        d.name for d in SKILLS_DIR.iterdir()
        if d.is_dir() and (d / "SKILL.md").is_file()
    )


def _ensure_skill_path() -> None:
    """Verify `.agents/skills/` is the canonical path. Hook for future
    tweaks (env var / symlink / codex config file) if codex needs help
    discovering it."""
    if not SKILLS_DIR.is_dir():
        raise CodexAdapterError(
            f"Skills directory missing: {SKILLS_DIR}. The lab pipeline "
            "expects skills at this path (shared with Cursor)."
        )
    # Codex >= current versions auto-discover .agents/skills/ when
    # operating with --cd <repo>. Nothing else required today; if a
    # future codex release needs an explicit pointer, set it here
    # (e.g. write a config.toml fragment under runs/lab/ and pass
    # --config or symlink as needed).


# ----- precondition checks --------------------------------------------------


def _check_binary(cfg: CodexConfig) -> None:
    if shutil.which(cfg.binary) is None:
        raise CodexAdapterError(
            f"`{cfg.binary}` not found on PATH. Install Codex CLI before "
            "running the orchestrator."
        )


def _check_auth() -> None:
    auth_file = Path.home() / ".codex" / "auth.json"
    if auth_file.is_file():
        return
    if os.environ.get("OPENAI_API_KEY"):
        return
    raise CodexAdapterError(
        "Codex auth missing: neither ~/.codex/auth.json nor $OPENAI_API_KEY "
        "is set. Run `codex login` (or export OPENAI_API_KEY) first."
    )


def _check_orchestrator_lock(cfg: CodexConfig, *, expected_owner_pid: int | None) -> None:
    """If lock enforcement is on, ensure either no lock or our pid owns it."""
    if not cfg.enforce_orchestrator_lock:
        return
    if not ORCHESTRATOR_LOCK_PATH.is_file():
        return
    try:
        owner = json.loads(ORCHESTRATOR_LOCK_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return
    owner_pid = owner.get("pid")
    if expected_owner_pid is not None and owner_pid == expected_owner_pid:
        return
    raise CodexAdapterError(
        f"Another orchestrator holds the lock at {ORCHESTRATOR_LOCK_PATH}: "
        f"{owner!r}. Stop it first (`uv run lab daemon stop`) or remove the "
        "stale lock if you know it's safe."
    )


# ----- prompt construction --------------------------------------------------


_PROMPT_TEMPLATE = """\
You are running the `{skill_id}` skill non-interactively from the
OpenHarness lab orchestrator. Read the skill instructions below and
execute them against the arguments at the top.

Arguments (positional, in order):
{args_block}

When you are done, your FINAL message must start with one of:
  OK; <one-line summary>
  REFUSE; <reason>

Do not append any trailing text after that line. The orchestrator
parses your final message verbatim.

--- BEGIN SKILL: {skill_id} ---
{skill_body}
--- END SKILL: {skill_id} ---
"""


def _render_prompt(skill_id: str, args: Sequence[str]) -> str:
    body = skill_path(skill_id).read_text()
    if args:
        args_block = "\n".join(f"  {i+1}. {a}" for i, a in enumerate(args))
    else:
        args_block = "  (no arguments)"
    return _PROMPT_TEMPLATE.format(
        skill_id=skill_id, args_block=args_block, skill_body=body
    )


# ----- subprocess driver ----------------------------------------------------


def _new_spawn_id() -> str:
    return uuid.uuid4().hex[:12]


def _log_path_for(skill_id: str, spawn_id: str) -> Path:
    LAB_LOGS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe_skill = skill_id.replace("/", "_")
    return LAB_LOGS_DIR / f"{ts}__{safe_skill}__{spawn_id}.log"


def _record_spawn(result: SpawnResult, *, parent_run_dir: Path | None) -> None:
    try:
        with labdb.writer() as conn:
            conn.execute(
                """
                INSERT INTO spawns (
                    spawn_id, skill, args, cwd, log_path, started_at,
                    finished_at, exit_code, cost_usd_estimate,
                    parent_run_dir, notes
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                [
                    result.spawn_id,
                    result.skill,
                    json.dumps(result.args),
                    str(REPO_ROOT),
                    str(result.log_path),
                    result.started_at,
                    result.finished_at,
                    result.exit_code,
                    result.cost_usd_estimate,
                    str(parent_run_dir) if parent_run_dir else None,
                    result.notes,
                ],
            )
    except Exception as exc:  # pragma: no cover - telemetry, shouldn't break runs
        logger.warning("failed to record spawn %s: %s", result.spawn_id, exc)


def _parse_last_message(text: str) -> str | None:
    text = (text or "").strip()
    return text or None


def run(
    skill_id: str,
    args: Sequence[str] = (),
    *,
    cfg: CodexConfig | None = None,
    parent_run_dir: Path | None = None,
    extra_env: dict[str, str] | None = None,
    expected_orchestrator_pid: int | None = None,
) -> SpawnResult:
    """Run one skill via `codex exec`. Blocks until completion."""
    cfg = cfg or CodexConfig()
    _ensure_skill_path()
    _check_binary(cfg)
    _check_auth()
    _check_orchestrator_lock(cfg, expected_owner_pid=expected_orchestrator_pid)
    skill_path(skill_id)  # raises if missing
    ensure_lab_runs_dir()

    spawn_id = _new_spawn_id()
    log_path = _log_path_for(skill_id, spawn_id)
    last_msg_path = log_path.with_suffix(".last.txt")
    prompt = _render_prompt(skill_id, args)

    base_args = [
        cfg.binary, "exec",
        "--json",
        "--cd", str(cfg.cwd),
        "--skip-git-repo-check",
        "-o", str(last_msg_path),
    ]
    if cfg.full_auto:
        base_args.append("--full-auto")
    base_args += ["--sandbox", cfg.sandbox]
    base_args += list(cfg.extra_codex_args)
    # Pass the prompt on stdin via "-".
    base_args.append("-")

    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)

    started = datetime.now(timezone.utc)
    t0 = time.monotonic()

    with cfg.semaphore:
        with log_path.open("w") as logfh:
            logfh.write(f"# spawn_id: {spawn_id}\n")
            logfh.write(f"# skill: {skill_id}\n")
            logfh.write(f"# args: {args!r}\n")
            logfh.write(f"# started_at: {started.isoformat()}\n")
            logfh.write("# command: " + " ".join(base_args) + "\n")
            logfh.write("# --- prompt --- #\n")
            logfh.write(prompt)
            logfh.write("\n# --- codex stdout (jsonl events) --- #\n")
            logfh.flush()

            try:
                proc = subprocess.run(
                    base_args,
                    input=prompt,
                    text=True,
                    capture_output=True,
                    cwd=str(cfg.cwd),
                    env=env,
                    timeout=cfg.timeout_sec,
                )
                logfh.write(proc.stdout or "")
                logfh.write("\n# --- codex stderr --- #\n")
                logfh.write(proc.stderr or "")
                exit_code = proc.returncode
            except subprocess.TimeoutExpired as exc:
                logfh.write(f"\n# TIMEOUT after {cfg.timeout_sec}s: {exc}\n")
                exit_code = 124  # convention: timeout

    finished = datetime.now(timezone.utc)
    duration = time.monotonic() - t0
    last_msg = None
    if last_msg_path.is_file():
        with contextlib.suppress(OSError):
            last_msg = _parse_last_message(last_msg_path.read_text())

    result = SpawnResult(
        spawn_id=spawn_id,
        skill=skill_id,
        args=list(args),
        exit_code=exit_code,
        log_path=log_path,
        last_message=last_msg,
        started_at=started,
        finished_at=finished,
        duration_sec=duration,
        parent_run_dir=parent_run_dir,
    )
    if cfg.record_in_db:
        _record_spawn(result, parent_run_dir=parent_run_dir)
    return result


# ----- helpers used by the orchestrator ------------------------------------


def run_many(
    invocations: Iterable[tuple[str, Sequence[str]]],
    *,
    cfg: CodexConfig | None = None,
    parent_run_dir: Path | None = None,
) -> list[SpawnResult]:
    """Run a batch of (skill_id, args) tuples respecting the semaphore."""
    cfg = cfg or CodexConfig()
    results: list[SpawnResult] = []
    threads: list[threading.Thread] = []
    out_lock = threading.Lock()

    def _worker(skill_id: str, args: Sequence[str]) -> None:
        try:
            r = run(skill_id, args, cfg=cfg, parent_run_dir=parent_run_dir)
        except CodexAdapterError as exc:
            logger.error("adapter error invoking %s %r: %s", skill_id, args, exc)
            return
        with out_lock:
            results.append(r)

    for skill_id, args in invocations:
        t = threading.Thread(
            target=_worker, args=(skill_id, args), name=f"codex-{skill_id}", daemon=False
        )
        t.start()
        threads.append(t)

    for t in threads:
        t.join()
    return results


# ----- orchestrator lock helpers (used by runner.py) -----------------------


@contextlib.contextmanager
def orchestrator_lock(*, owner: str | None = None) -> Iterator[Path]:
    """Acquire `runs/lab/orchestrator.lock` for the duration of the block."""
    ensure_lab_runs_dir()
    if ORCHESTRATOR_LOCK_PATH.is_file():
        try:
            cur = json.loads(ORCHESTRATOR_LOCK_PATH.read_text())
        except (OSError, json.JSONDecodeError):
            cur = {}
        raise CodexAdapterError(
            f"Orchestrator lock already held: {cur!r} (at "
            f"{ORCHESTRATOR_LOCK_PATH}). Refusing to start a second daemon."
        )
    payload = {
        "pid": os.getpid(),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "owner": owner or sys.argv[0],
    }
    ORCHESTRATOR_LOCK_PATH.write_text(json.dumps(payload, indent=2))
    try:
        yield ORCHESTRATOR_LOCK_PATH
    finally:
        with contextlib.suppress(OSError):
            ORCHESTRATOR_LOCK_PATH.unlink()


def force_release_lock() -> bool:
    """Remove a stale lock file. Caller is responsible for verifying staleness."""
    if ORCHESTRATOR_LOCK_PATH.is_file():
        ORCHESTRATOR_LOCK_PATH.unlink()
        return True
    return False
