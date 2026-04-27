"""Critic outputs as files — single source of truth.

Each critic skill writes a JSON file co-located with the artifact
it analyzes. **Files, not DuckDB rows, are the canonical output**:

- Eliminates DuckDB single-writer contention by construction
  (each spawn writes an independent file; nothing touches the DB).
- Co-locates analysis with evidence so a human navigating to a
  trial dir sees its critique alongside `agent/`, `verifier/`, etc.
- Critiques travel with the run dir as part of the git-trackable
  artifacts (subject to the existing `runs/` gitignore policy).
- Decouples capture from analysis: we freeze the evidence (JSON
  file) once and forever; the DuckDB tables become a derived cache
  we can rebuild with a richer schema as the analysis matures.

Re-population of the DB is `uv run lab ingest-critiques [<run_dir>...]`
which walks the file tree and upserts the cache tables.

File layout:

    <trial_dir>/critic/trial-critic.json
    <trial_dir>/critic/trial-evidence.json
    <run_dir>/critic/experiment-critic.json
    <run_dir>/critic/comparisons/<task_name>.json
    runs/lab/task_features/<task_checksum>.json
    runs/lab/cross_experiment/<utc_ts>__<spawn_id>.json
    runs/lab/components_perf/<component_id>__<task_cluster>.json
    runs/lab/auto_proposed/<idea_id>.json
    runs/lab/spawns/<spawn_id>.json

Every write is atomic (tmp file + rename) so a crashed spawn never
leaves a half-written JSON. Every payload carries a `provenance`
block (`skill`, `critic_model`, `critic_effort`, `spawn_id`,
`created_at`) so we never lose attribution again.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from openharness.lab.paths import (
    EXPERIMENTS_RUNS_ROOT,
    LAB_RUNS_ROOT,
)

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

CRITIC_DIRNAME = "critic"
"""Subdir name inside a trial dir / run dir holding critic outputs."""

TASK_FEATURES_DIR: Path = LAB_RUNS_ROOT / "task_features"
CROSS_EXPERIMENT_DIR: Path = LAB_RUNS_ROOT / "cross_experiment"
COMPONENTS_PERF_DIR: Path = LAB_RUNS_ROOT / "components_perf"
AUTO_PROPOSED_DIR: Path = LAB_RUNS_ROOT / "auto_proposed"
SPAWNS_DIR: Path = LAB_RUNS_ROOT / "spawns"


def trial_critique_path(trial_dir: Path) -> Path:
    """`<trial_dir>/critic/trial-critic.json`."""
    return Path(trial_dir) / CRITIC_DIRNAME / "trial-critic.json"


def localize_trial_dir(trial_dir: Path) -> Path:
    """Map stale absolute trial paths onto this checkout's runs root.

    Synced DuckDB files may contain absolute paths from another host
    (for example `/home/.../OpenHarness/runs/experiments/...`). The
    portable part starts at `runs/experiments/`; if the original path
    is absent, rebuild it under this machine's
    :data:`EXPERIMENTS_RUNS_ROOT`.
    """
    trial_dir = Path(trial_dir)
    if trial_dir.exists():
        return trial_dir
    parts = trial_dir.parts
    for i in range(len(parts) - 1):
        if parts[i] == "runs" and parts[i + 1] == "experiments":
            rel = Path(*parts[i + 2 :])
            candidate = EXPERIMENTS_RUNS_ROOT / rel
            if candidate.exists():
                return candidate
    return trial_dir


def trial_evidence_path(trial_dir: Path) -> Path:
    """`<trial_dir>/critic/trial-evidence.json`."""
    return Path(trial_dir) / CRITIC_DIRNAME / "trial-evidence.json"


def experiment_critic_path(run_dir: Path) -> Path:
    """`<run_dir>/critic/experiment-critic.json`."""
    return Path(run_dir) / CRITIC_DIRNAME / "experiment-critic.json"


def comparison_path(run_dir: Path, task_name: str) -> Path:
    """`<run_dir>/critic/comparisons/<safe_task_name>.json`."""
    return Path(run_dir) / CRITIC_DIRNAME / "comparisons" / f"{_safe(task_name)}.json"


def task_features_path(task_checksum: str) -> Path:
    """`runs/lab/task_features/<checksum>.json`."""
    return TASK_FEATURES_DIR / f"{task_checksum}.json"


def cross_experiment_path(spawn_id: str, *, ts: datetime | None = None) -> Path:
    """`runs/lab/cross_experiment/<utc_ts>__<spawn_id>.json`.

    The cross-experiment-critic operates over the WHOLE DB, so its
    output is keyed by spawn id (one file per invocation), not by
    instance id. Multiple invocations co-exist; analysis can pick
    the latest by mtime or by parsing the leading timestamp.
    """
    ts = ts or datetime.now(timezone.utc)
    stamp = ts.strftime("%Y%m%dT%H%M%SZ")
    return CROSS_EXPERIMENT_DIR / f"{stamp}__{spawn_id}.json"


def component_perf_path(component_id: str, task_cluster: str) -> Path:
    """`runs/lab/components_perf/<component>__<cluster>.json`."""
    return COMPONENTS_PERF_DIR / f"{_safe(component_id)}__{_safe(task_cluster)}.json"


def auto_proposed_path(idea_id: str) -> Path:
    """`runs/lab/auto_proposed/<idea_id>.json`."""
    return AUTO_PROPOSED_DIR / f"{_safe(idea_id)}.json"


def spawn_record_path(spawn_id: str) -> Path:
    """`runs/lab/spawns/<spawn_id>.json`."""
    return SPAWNS_DIR / f"{spawn_id}.json"


# ---------------------------------------------------------------------------
# Atomic write + provenance
# ---------------------------------------------------------------------------


def _provenance(
    *,
    skill: str | None = None,
    critic_model: str | None = None,
) -> dict[str, Any]:
    """Stable provenance block embedded in every critic file.

    Pulls model / effort / spawn id / skill from the env vars the
    codex adapter sets on every spawn (`OPENHARNESS_CODEX_*`,
    `OPENHARNESS_LAB_*`). Explicit args win over env.
    """
    env = os.environ
    return {
        "skill": skill or env.get("OPENHARNESS_LAB_SKILL"),
        "spawn_id": env.get("OPENHARNESS_LAB_SPAWN_ID"),
        "critic_model": critic_model or env.get("OPENHARNESS_CODEX_MODEL"),
        "critic_effort": env.get("OPENHARNESS_CODEX_EFFORT"),
        "critic_summary": env.get("OPENHARNESS_CODEX_SUMMARY"),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> Path:
    """Write `payload` to `path` atomically (tmp + rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=False, default=str))
    os.replace(tmp, path)
    return path


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _safe(s: str) -> str:
    """Filename-safe slug. Keeps `[A-Za-z0-9._-]`, replaces the rest."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s).strip("_") or "_"


# ---------------------------------------------------------------------------
# trial-critic
# ---------------------------------------------------------------------------


def write_trial_critique(
    trial_dir: Path,
    payload: dict[str, Any],
    *,
    critic_model: str | None = None,
) -> Path:
    """Persist a per-trial critique. Returns the file path."""
    path = trial_critique_path(trial_dir)
    body = _wrap(
        payload,
        provenance=_provenance(skill="trial-critic", critic_model=critic_model),
        kind="trial_critique",
    )
    return _atomic_write_json(path, body)


def read_trial_critique(trial_dir: Path) -> dict[str, Any] | None:
    return _read_json(trial_critique_path(trial_dir))


def write_trial_evidence(trial_dir: Path, payload: dict[str, Any]) -> Path:
    """Persist deterministic per-trial evidence. Returns the file path."""
    path = trial_evidence_path(trial_dir)
    body = _wrap(
        payload,
        provenance=_provenance(skill="trial-evidence"),
        kind="trial_evidence",
    )
    return _atomic_write_json(path, body)


def read_trial_evidence(trial_dir: Path) -> dict[str, Any] | None:
    return _read_json(trial_evidence_path(trial_dir))


# ---------------------------------------------------------------------------
# experiment-critic
# ---------------------------------------------------------------------------


def write_experiment_critique(
    run_dir: Path,
    payload: dict[str, Any],
    *,
    critic_model: str | None = None,
) -> Path:
    path = experiment_critic_path(run_dir)
    body = _wrap(
        payload,
        provenance=_provenance(
            skill="experiment-critic", critic_model=critic_model
        ),
        kind="experiment_critic_summary",
    )
    return _atomic_write_json(path, body)


def write_comparison(
    run_dir: Path,
    task_name: str,
    payload: dict[str, Any],
    *,
    critic_model: str | None = None,
) -> Path:
    path = comparison_path(run_dir, task_name)
    body = _wrap(
        payload,
        provenance=_provenance(
            skill="experiment-critic", critic_model=critic_model
        ),
        kind="comparison",
        task_name=task_name,
    )
    return _atomic_write_json(path, body)


def iter_comparisons(run_dir: Path) -> Iterator[tuple[str, dict[str, Any]]]:
    cmp_dir = Path(run_dir) / CRITIC_DIRNAME / "comparisons"
    if not cmp_dir.is_dir():
        return
    for p in sorted(cmp_dir.glob("*.json")):
        data = _read_json(p)
        if data is None:
            continue
        task_name = data.get("task_name") or p.stem
        yield task_name, data


# ---------------------------------------------------------------------------
# task-features
# ---------------------------------------------------------------------------


def write_task_features(
    task_checksum: str,
    payload: dict[str, Any],
    *,
    extracted_by: str | None = None,
) -> Path:
    path = task_features_path(task_checksum)
    body = _wrap(
        payload,
        provenance=_provenance(
            skill="task-features", critic_model=extracted_by
        ),
        kind="task_features",
        task_checksum=task_checksum,
    )
    # task-features exposes `extracted_by` at top level because the DB
    # cache uses that name.
    body["extracted_by"] = body["provenance"]["critic_model"]
    return _atomic_write_json(path, body)


def read_task_features(task_checksum: str) -> dict[str, Any] | None:
    return _read_json(task_features_path(task_checksum))


def iter_task_features() -> Iterator[tuple[str, dict[str, Any]]]:
    if not TASK_FEATURES_DIR.is_dir():
        return
    for p in sorted(TASK_FEATURES_DIR.glob("*.json")):
        data = _read_json(p)
        if data is None:
            continue
        yield p.stem, data


# ---------------------------------------------------------------------------
# cross-experiment-critic
# ---------------------------------------------------------------------------


def write_cross_experiment(
    spawn_id: str,
    payload: dict[str, Any],
    *,
    critic_model: str | None = None,
) -> Path:
    path = cross_experiment_path(spawn_id)
    body = _wrap(
        payload,
        provenance=_provenance(
            skill="cross-experiment-critic", critic_model=critic_model
        ),
        kind="cross_experiment_summary",
    )
    return _atomic_write_json(path, body)


def write_component_perf(
    component_id: str,
    task_cluster: str,
    payload: dict[str, Any],
) -> Path:
    path = component_perf_path(component_id, task_cluster)
    body = _wrap(
        payload,
        provenance=_provenance(skill="cross-experiment-critic"),
        kind="component_perf",
        component_id=component_id,
        task_cluster=task_cluster,
    )
    return _atomic_write_json(path, body)


def write_auto_proposed(idea_id: str, payload: dict[str, Any]) -> Path:
    """Append-only sink for cross-experiment follow-up suggestions."""
    path = auto_proposed_path(idea_id)
    body = _wrap(
        payload,
        provenance=_provenance(skill="cross-experiment-critic"),
        kind="auto_proposed_idea",
        idea_id=idea_id,
    )
    return _atomic_write_json(path, body)


def iter_components_perf() -> Iterator[tuple[str, str, dict[str, Any]]]:
    if not COMPONENTS_PERF_DIR.is_dir():
        return
    for p in sorted(COMPONENTS_PERF_DIR.glob("*.json")):
        data = _read_json(p)
        if data is None:
            continue
        cid = data.get("component_id")
        cluster = data.get("task_cluster")
        if cid and cluster:
            yield cid, cluster, data


# ---------------------------------------------------------------------------
# spawns telemetry
# ---------------------------------------------------------------------------


def write_spawn_record(record: dict[str, Any]) -> Path:
    """Per-spawn telemetry file. Parent process writes; never the child.

    The child agents already write their own outputs (critiques etc.)
    to files; this is the parent's view: skill, args, log path,
    timing, exit code. Replaces the previous DB write that raced
    with the children's writes.
    """
    spawn_id = record["spawn_id"]
    path = spawn_record_path(spawn_id)
    return _atomic_write_json(path, record)


def iter_spawn_records() -> Iterator[dict[str, Any]]:
    if not SPAWNS_DIR.is_dir():
        return
    for p in sorted(SPAWNS_DIR.glob("*.json")):
        data = _read_json(p)
        if data is not None:
            yield data


# ---------------------------------------------------------------------------
# Discovery helpers
# ---------------------------------------------------------------------------


def iter_trial_critiques(run_dir: Path) -> Iterator[tuple[Path, dict[str, Any]]]:
    """Walk a single experiment run dir for `critic/trial-critic.json`."""
    run_dir = Path(run_dir)
    for p in run_dir.rglob(f"{CRITIC_DIRNAME}/trial-critic.json"):
        data = _read_json(p)
        if data is None:
            continue
        # The trial dir is the *parent* of the critic dir.
        trial_dir = p.parent.parent
        yield trial_dir, data


def iter_all_trial_critiques() -> Iterator[tuple[Path, dict[str, Any]]]:
    """Walk every experiment run dir for trial critiques."""
    if not EXPERIMENTS_RUNS_ROOT.is_dir():
        return
    for run_dir in sorted(EXPERIMENTS_RUNS_ROOT.iterdir()):
        if run_dir.is_dir():
            yield from iter_trial_critiques(run_dir)


def ensure_dirs() -> None:
    """Create the lab-wide critic dirs (no per-run ones)."""
    for d in (
        TASK_FEATURES_DIR, CROSS_EXPERIMENT_DIR, COMPONENTS_PERF_DIR,
        AUTO_PROPOSED_DIR, SPAWNS_DIR,
    ):
        d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Internal: payload wrapper
# ---------------------------------------------------------------------------


def _wrap(
    payload: dict[str, Any],
    *,
    provenance: dict[str, Any],
    kind: str,
    **extra: Any,
) -> dict[str, Any]:
    """Normalize the on-disk envelope.

    - `kind` and `schema_version` are top-level so a generic loader
      can route a JSON blob without parsing the body.
    - `provenance` carries skill / model / spawn / timestamps.
    - `extra` carries identifying keys (task_name, task_checksum, …)
      that are easier to pull from the file path but cheap to repeat.
    - The original payload is merged in last so callers control the
      remaining shape; their `schema_version` (if any) wins.
    """
    body: dict[str, Any] = {
        "kind": kind,
        "schema_version": int(payload.get("schema_version", 1)),
        "provenance": provenance,
        **extra,
        **payload,
    }
    # Don't double-store schema_version inside the original payload.
    body["schema_version"] = int(body["schema_version"])
    return body


def run_dir_from_instance(
    instance_id: str, *, db_conn: Any | None = None
) -> Path | None:
    """Look up a run directory from the experiments table."""
    from openharness.lab import db as labdb

    if db_conn is not None:
        row = db_conn.execute(
            "SELECT run_dir FROM experiments WHERE instance_id = ?",
            [instance_id],
        ).fetchone()
    else:
        with labdb.reader() as conn:
            row = conn.execute(
                "SELECT run_dir FROM experiments WHERE instance_id = ?",
                [instance_id],
            ).fetchone()
    if not row:
        return None
    return Path(row[0])
