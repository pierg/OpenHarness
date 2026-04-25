"""Gemini CLI adapter for high-volume lab critique work.

The daemon uses Codex for judgment-heavy phase decisions. Per-trial
critique is different: it is high-volume and should run from a
deterministic evidence digest through Gemini CLI, with no Codex
fallback. If Gemini is missing, over quota, or returns invalid JSON,
the trial remains uncritiqued and the phase fails visibly.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from openharness.lab import codex as skill_runtime
from openharness.lab import critic_io, trial_evidence
from openharness.lab.env import apply_gemini_cli_key, apply_repo_dotenv
from openharness.lab.paths import LAB_LOGS_DIR, REPO_ROOT, ensure_lab_runs_dir
from openharness.lab.usage import augment_spawn_record

DEFAULT_TRIAL_MODEL = "gemini-3.1-pro-preview"
FLASH_TRIAL_MODEL = "gemini-3-flash-preview"
DEFAULT_TIMEOUT_SEC = 60 * 60 * 2
DEFAULT_MAX_CONCURRENCY = 4

_TRIAL_REQUIRED_FIELDS = {
    "schema_version",
    "task_summary",
    "agent_strategy",
    "key_actions",
    "outcome",
    "components_active",
    "task_features",
    "confidence",
}


class GeminiAdapterError(RuntimeError):
    """Adapter-level failure (missing binary, invalid JSON, quota, etc.)."""


@dataclass(slots=True)
class GeminiResult:
    spawn_id: str
    skill: str
    args: list[str]
    model: str
    exit_code: int
    log_path: Path
    last_message: str | None
    started_at: datetime
    finished_at: datetime
    duration_sec: float
    output_path: Path | None = None
    payload: dict[str, Any] | None = None
    parent_run_dir: Path | None = None
    notes: str | None = None

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


@dataclass
class GeminiConfig:
    binary: str = "gemini"
    cwd: Path = REPO_ROOT
    trial_model: str = field(
        default_factory=lambda: os.environ.get(
            "OPENHARNESS_GEMINI_TRIAL_MODEL",
            DEFAULT_TRIAL_MODEL,
        )
    )
    timeout_sec: int = DEFAULT_TIMEOUT_SEC
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY
    output_format: str = "json"
    approval_mode: str = "yolo"

    def __post_init__(self) -> None:
        self._semaphore = threading.BoundedSemaphore(self.max_concurrency)

    @property
    def semaphore(self) -> threading.BoundedSemaphore:
        return self._semaphore


def run_trial_critic(
    trial_dir: Path,
    *,
    cfg: GeminiConfig | None = None,
    model: str | None = None,
    parent_run_dir: Path | None = None,
    persist: bool = True,
) -> GeminiResult:
    """Run Gemini trial critique for one trial directory."""
    trial_dir = critic_io.localize_trial_dir(Path(trial_dir))
    cfg = cfg or GeminiConfig()
    selected_model = model or cfg.trial_model
    _check_binary(cfg)
    skill_runtime._ensure_skill_path("trial-critic", checkout_root=cfg.cwd)
    skill_path = skill_runtime.skill_path("trial-critic", checkout_root=cfg.cwd)
    ensure_lab_runs_dir()

    spawn_id = uuid.uuid4().hex[:12]
    log_path = _log_path_for("trial-critic", spawn_id)
    started = datetime.now(timezone.utc)
    t0 = time.monotonic()
    evidence_path = trial_evidence.write_trial_evidence(trial_dir)
    evidence = critic_io.read_trial_evidence(trial_dir) or {}
    prompt = _render_trial_prompt(
        trial_dir=trial_dir,
        evidence_path=evidence_path,
        evidence=evidence,
        skill_body=skill_path.read_text(encoding="utf-8"),
        persist=persist,
    )
    argv = _build_argv(
        cfg,
        model=selected_model,
        include_dirs=[Path(trial_dir).resolve()],
    )
    env = _build_env()
    env["OPENHARNESS_LAB_SKILL"] = "trial-critic"
    env["OPENHARNESS_LAB_SPAWN_ID"] = spawn_id
    env["OPENHARNESS_GEMINI_MODEL"] = selected_model

    with cfg.semaphore:
        with log_path.open("w", encoding="utf-8") as logfh:
            logfh.write(f"# spawn_id: {spawn_id}\n")
            logfh.write("# skill: trial-critic\n")
            logfh.write(f"# model: {selected_model}\n")
            logfh.write(f"# cwd: {cfg.cwd}\n")
            logfh.write(f"# command: {' '.join(argv[:5])} ...\n\n")
            logfh.write("# --- prompt --- #\n")
            logfh.write(prompt)
            logfh.write("\n\n# --- gemini stdout --- #\n")
            try:
                proc = subprocess.run(
                    argv,
                    cwd=str(cfg.cwd),
                    env=env,
                    input=prompt,
                    text=True,
                    capture_output=True,
                    timeout=cfg.timeout_sec,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                finished = datetime.now(timezone.utc)
                logfh.write(exc.stdout or "")
                logfh.write("\n# --- gemini stderr --- #\n")
                logfh.write(exc.stderr or "")
                result = GeminiResult(
                    spawn_id=spawn_id,
                    skill="trial-critic",
                    args=[str(trial_dir)],
                    model=selected_model,
                    exit_code=124,
                    log_path=log_path,
                    last_message=f"timeout after {cfg.timeout_sec}s",
                    started_at=started,
                    finished_at=finished,
                    duration_sec=time.monotonic() - t0,
                    parent_run_dir=parent_run_dir,
                )
                _record_spawn(result)
                return result
            logfh.write(proc.stdout or "")
            logfh.write("\n# --- gemini stderr --- #\n")
            logfh.write(proc.stderr or "")

    finished = datetime.now(timezone.utc)
    duration = time.monotonic() - t0
    last_message: str | None = None
    output_path: Path | None = None
    payload: dict[str, Any] | None = None
    exit_code = proc.returncode
    notes: str | None = None

    if proc.returncode == 0:
        try:
            payload = _parse_trial_payload(proc.stdout)
            payload.setdefault("evidence_source", "pre_digest")
            payload.setdefault("expanded_artifacts", [])
            payload.setdefault("expansion_reason", None)
            payload.setdefault("critic_provider", "gemini-cli")
            payload.setdefault("critic_model", selected_model)
            _validate_trial_payload(payload)
            last_message = (
                f"OK; outcome={payload.get('outcome')} confidence={payload.get('confidence')}"
            )
            if persist:
                output_path = critic_io.write_trial_critique(
                    Path(trial_dir),
                    payload,
                    critic_model=selected_model,
                )
        except GeminiAdapterError as exc:
            exit_code = 2
            last_message = f"invalid Gemini trial critique: {exc}"
            notes = "invalid_schema"
    else:
        last_message = _tail(proc.stderr or proc.stdout)
        notes = "gemini_cli_failed"

    result = GeminiResult(
        spawn_id=spawn_id,
        skill="trial-critic",
        args=[str(trial_dir)],
        model=selected_model,
        exit_code=exit_code,
        log_path=log_path,
        last_message=last_message,
        started_at=started,
        finished_at=finished,
        duration_sec=duration,
        output_path=output_path,
        payload=payload,
        parent_run_dir=parent_run_dir,
        notes=notes,
    )
    _record_spawn(result)
    return result


def run_many_trial_critics(
    trial_dirs: Sequence[Path | str],
    *,
    cfg: GeminiConfig | None = None,
    model: str | None = None,
    parent_run_dir: Path | None = None,
    persist: bool = True,
) -> list[GeminiResult]:
    """Run Gemini trial critics concurrently."""
    cfg = cfg or GeminiConfig()
    paths = [Path(p) for p in trial_dirs]
    if not paths:
        return []
    results: list[GeminiResult] = []
    with ThreadPoolExecutor(max_workers=cfg.max_concurrency) as pool:
        futures = [
            pool.submit(
                run_trial_critic,
                p,
                cfg=cfg,
                model=model,
                parent_run_dir=parent_run_dir,
                persist=persist,
            )
            for p in paths
        ]
        for fut in as_completed(futures):
            results.append(fut.result())
    return results


def _check_binary(cfg: GeminiConfig) -> None:
    binary_path = Path(cfg.binary)
    if binary_path.is_absolute() and binary_path.is_file():
        return
    if shutil.which(cfg.binary) is None:
        raise GeminiAdapterError(
            f"`{cfg.binary}` not found on PATH. Install Gemini CLI before running trial-critic."
        )


def _build_argv(
    cfg: GeminiConfig,
    *,
    model: str,
    include_dirs: Sequence[Path],
) -> list[str]:
    argv = [
        cfg.binary,
        "--model",
        model,
        "--prompt",
        "",
        "--output-format",
        cfg.output_format,
        "--approval-mode",
        cfg.approval_mode,
    ]
    for path in include_dirs:
        argv += ["--include-directories", str(path)]
    return argv


def _build_env() -> dict[str, str]:
    env = os.environ.copy()
    env = apply_repo_dotenv(env, REPO_ROOT / ".env")
    return apply_gemini_cli_key(env, REPO_ROOT / ".env")


def _render_trial_prompt(
    *,
    trial_dir: Path,
    evidence_path: Path,
    evidence: dict[str, Any],
    skill_body: str,
    persist: bool,
) -> str:
    write_instruction = (
        "Return the JSON payload only. The parent process will persist it."
        if persist
        else "Return the JSON payload only. This is a shadow comparison; do not write files."
    )
    return f"""\
You are running OpenHarness `trial-critic` through Gemini CLI.

Use the deterministic evidence digest first. You may inspect raw files
under the trial directory only when the digest is ambiguous or your
confidence would otherwise be below 0.75. If you inspect raw files,
populate `evidence_source` as "pre_digest_plus_expansion",
`expanded_artifacts` with relative paths, and `expansion_reason` with
the reason. Otherwise use `evidence_source="pre_digest"`,
`expanded_artifacts=[]`, and `expansion_reason=null`.

{write_instruction}

Trial directory:
{trial_dir}

Evidence digest path:
{evidence_path}

Evidence digest JSON:
{json.dumps(evidence, indent=2, sort_keys=False, default=str)}

Required output JSON shape:
{{
  "schema_version": 1,
  "task_summary": "...",
  "agent_strategy": "...",
  "key_actions": ["turn 1: ..."],
  "outcome": "passed|failed|errored",
  "root_cause": "... or null",
  "success_factor": "... or null",
  "anti_patterns": ["kebab-case"],
  "components_active": ["component-id"],
  "task_features": ["kebab-case"],
  "surprising_observations": ["..."],
  "confidence": 0.0,
  "evidence_source": "pre_digest|pre_digest_plus_expansion",
  "expanded_artifacts": ["relative/path"],
  "expansion_reason": "... or null"
}}

Do not include markdown fences or prose outside the JSON object.

--- BEGIN OPERATOR-LOCAL SKILL CONTEXT: trial-critic ---
{skill_body}
--- END OPERATOR-LOCAL SKILL CONTEXT: trial-critic ---
"""


def _parse_trial_payload(stdout: str) -> dict[str, Any]:
    raw = (stdout or "").strip()
    if not raw:
        raise GeminiAdapterError("empty stdout")
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        decoded = None
    if isinstance(decoded, dict):
        if _looks_like_trial_payload(decoded):
            return decoded
        text = _text_from_gemini_json(decoded)
        if text:
            return _parse_json_object(text)
    if isinstance(decoded, list):
        text = "\n".join(filter(None, (_text_from_gemini_json(x) for x in decoded)))
        if text:
            return _parse_json_object(text)
    return _parse_json_object(raw)


def _text_from_gemini_json(value: Any) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return ""
    for key in ("response", "text", "content", "message", "output"):
        item = value.get(key)
        if isinstance(item, str):
            return item
    if isinstance(value.get("candidates"), list):
        return _text_from_gemini_json(value["candidates"][0]) if value["candidates"] else ""
    if isinstance(value.get("parts"), list):
        return "\n".join(_text_from_gemini_json(p) for p in value["parts"])
    return ""


def _parse_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise GeminiAdapterError("no JSON object found")
        try:
            payload = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise GeminiAdapterError(f"invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise GeminiAdapterError("payload is not a JSON object")
    return payload


def _looks_like_trial_payload(payload: dict[str, Any]) -> bool:
    return bool(_TRIAL_REQUIRED_FIELDS.intersection(payload.keys()))


def _validate_trial_payload(payload: dict[str, Any]) -> None:
    missing = sorted(k for k in _TRIAL_REQUIRED_FIELDS if k not in payload)
    if missing:
        raise GeminiAdapterError(f"missing required fields: {missing}")
    if payload.get("outcome") not in {"passed", "failed", "errored"}:
        raise GeminiAdapterError(f"invalid outcome: {payload.get('outcome')!r}")
    confidence = payload.get("confidence")
    if not isinstance(confidence, (int, float)) or not 0 <= float(confidence) <= 1:
        raise GeminiAdapterError("confidence must be a number in [0, 1]")
    if payload.get("outcome") == "passed" and not payload.get("success_factor"):
        raise GeminiAdapterError("passed outcome requires success_factor")
    if payload.get("outcome") in {"failed", "errored"} and not payload.get("root_cause"):
        raise GeminiAdapterError("failed/errored outcome requires root_cause")


def _log_path_for(skill_id: str, spawn_id: str) -> Path:
    LAB_LOGS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return LAB_LOGS_DIR / f"{ts}__gemini-{skill_id}__{spawn_id}.log"


def _record_spawn(result: GeminiResult) -> None:
    record = {
        "spawn_id": result.spawn_id,
        "skill": result.skill,
        "provider": "gemini-cli",
        "args": list(result.args),
        "cwd": str(REPO_ROOT),
        "log_path": str(result.log_path),
        "started_at": result.started_at.isoformat(),
        "finished_at": result.finished_at.isoformat(),
        "exit_code": result.exit_code,
        "parent_run_dir": str(result.parent_run_dir) if result.parent_run_dir else None,
        "notes": result.notes,
        "effective_settings": {
            "model": result.model,
            "provider": "gemini-cli",
        },
        "duration_sec": result.duration_sec,
        "last_message": result.last_message,
    }
    with contextlib.suppress(Exception):
        critic_io.write_spawn_record(augment_spawn_record(record))


def _tail(text: str, *, n: int = 800) -> str:
    text = (text or "").strip()
    return text[-n:] if len(text) > n else text
