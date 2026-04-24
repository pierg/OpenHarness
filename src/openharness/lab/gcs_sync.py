"""Mirror portable run artifacts between the repo and a GCS bucket.

The lab writes two very different classes of data under ``runs/``:

- ``runs/experiments/<id>/`` and the file-based critic outputs are
  portable, immutable-enough artifacts that should survive machine
  moves.
- ``runs/lab/trials.duckdb``, ``daemon-state.json``, file locks, and
  ``runs/lab/state/<slug>/`` are live machine-local control-plane
  state. Mirroring those between machines would corrupt resumability
  and can fight DuckDB's single-writer contract.

This module deliberately syncs only the portable subtrees. Pulling
them onto another machine can then rebuild the local DuckDB cache from
the on-disk source-of-truth files.
"""

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from openharness.lab import ingest as labingest
from openharness.lab.paths import EXPERIMENTS_RUNS_ROOT, LAB_RUNS_ROOT, RUNS_ROOT

log = logging.getLogger(__name__)

type SyncDirection = Literal["push", "pull"]

GCS_URI_ENV = "OPENHARNESS_RUNS_GCS_URI"
AUTO_PUSH_ENV = "OPENHARNESS_RUNS_GCS_AUTO_PUSH"


class GCSRunsSyncError(RuntimeError):
    """Raised when the configured GCS mirror cannot be used."""


@dataclass(frozen=True, slots=True)
class SyncTarget:
    """One portable subtree mirrored to or from GCS."""

    local_path: Path
    remote_suffix: str
    description: str
    required_for_push: bool = False


@dataclass(frozen=True, slots=True)
class SyncSummary:
    """What one push/pull operation attempted."""

    direction: SyncDirection
    uri: str
    synced: tuple[str, ...]
    skipped: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CacheRefreshSummary:
    """Local DuckDB/cache refresh result after a pull."""

    runs_ingested: int
    legs_inserted: int
    trials_inserted: int
    trials_skipped: int
    misconfigurations: int
    trial_critiques: int
    comparisons: int
    experiment_critic_files: int
    task_features: int
    components_perf: int
    spawns: int


_PORTABLE_LAB_WIDE_TARGETS: tuple[SyncTarget, ...] = (
    SyncTarget(
        local_path=LAB_RUNS_ROOT / "task_features",
        remote_suffix="lab/task_features",
        description="lab task_features",
    ),
    SyncTarget(
        local_path=LAB_RUNS_ROOT / "cross_experiment",
        remote_suffix="lab/cross_experiment",
        description="lab cross_experiment snapshots",
    ),
    SyncTarget(
        local_path=LAB_RUNS_ROOT / "components_perf",
        remote_suffix="lab/components_perf",
        description="lab components_perf rows",
    ),
    SyncTarget(
        local_path=LAB_RUNS_ROOT / "auto_proposed",
        remote_suffix="lab/auto_proposed",
        description="lab auto_proposed ideas",
    ),
    SyncTarget(
        local_path=LAB_RUNS_ROOT / "spawns",
        remote_suffix="lab/spawns",
        description="lab spawn records",
    ),
)


def resolve_gcs_uri(uri: str | None = None) -> str:
    """Return the configured ``gs://`` root used for portable artifacts."""
    candidate = (uri or os.environ.get(GCS_URI_ENV, "")).strip()
    if not candidate:
        raise GCSRunsSyncError(
            f"Missing GCS mirror URI. Pass --uri or set {GCS_URI_ENV}, "
            "for example gs://my-bucket/openharness/runs."
        )
    if not candidate.startswith("gs://"):
        raise GCSRunsSyncError(
            f"Invalid GCS mirror URI {candidate!r}; expected a gs://... path."
        )
    return candidate.rstrip("/")


def portable_targets(
    *,
    instance_id: str | None = None,
    include_lab_wide: bool = True,
) -> tuple[SyncTarget, ...]:
    """Return the portable subtrees safe to mirror across machines."""
    targets: list[SyncTarget] = []
    if instance_id is None:
        targets.append(
            SyncTarget(
                local_path=EXPERIMENTS_RUNS_ROOT,
                remote_suffix="experiments",
                description="all experiment runs",
            )
        )
    else:
        targets.append(
            SyncTarget(
                local_path=EXPERIMENTS_RUNS_ROOT / instance_id,
                remote_suffix=f"experiments/{instance_id}",
                description=f"experiment run {instance_id}",
                required_for_push=True,
            )
        )
    if include_lab_wide:
        targets.extend(_PORTABLE_LAB_WIDE_TARGETS)
    return tuple(targets)


def sync_portable_runs(
    *,
    direction: SyncDirection,
    uri: str | None = None,
    instance_id: str | None = None,
    include_lab_wide: bool = True,
    dry_run: bool = False,
    delete_unmatched: bool = False,
) -> SyncSummary:
    """Push or pull the portable run subtrees to or from GCS."""
    resolved_uri = resolve_gcs_uri(uri)
    RUNS_ROOT.mkdir(parents=True, exist_ok=True)
    synced: list[str] = []
    skipped: list[str] = []

    for target in portable_targets(
        instance_id=instance_id, include_lab_wide=include_lab_wide,
    ):
        if direction == "push":
            if not target.local_path.exists():
                if target.required_for_push:
                    raise GCSRunsSyncError(
                        f"Cannot push {target.description}: {target.local_path} does not exist."
                    )
                skipped.append(target.description)
                continue
            src = str(target.local_path)
            dst = f"{resolved_uri}/{target.remote_suffix}"
        else:
            target.local_path.mkdir(parents=True, exist_ok=True)
            src = f"{resolved_uri}/{target.remote_suffix}"
            dst = str(target.local_path)

        _run_rsync(
            src=src,
            dst=dst,
            description=target.description,
            dry_run=dry_run,
            delete_unmatched=delete_unmatched,
        )
        synced.append(target.description)

    return SyncSummary(
        direction=direction,
        uri=resolved_uri,
        synced=tuple(synced),
        skipped=tuple(skipped),
    )


def refresh_local_cache() -> CacheRefreshSummary:
    """Rebuild the local lab DB/cache from the synced on-disk artifacts."""
    run_dirs = _experiment_run_dirs()
    ingest_summaries = labingest.ingest_runs(run_dirs) if run_dirs else []
    critic_counts = labingest.ingest_critiques(
        run_dirs or None, include_lab_wide=True,
    )
    return CacheRefreshSummary(
        runs_ingested=len(ingest_summaries),
        legs_inserted=sum(item.legs_inserted for item in ingest_summaries),
        trials_inserted=sum(item.trials_inserted for item in ingest_summaries),
        trials_skipped=sum(item.trials_skipped for item in ingest_summaries),
        misconfigurations=sum(item.misconfigurations for item in ingest_summaries),
        trial_critiques=critic_counts["trial_critiques"],
        comparisons=critic_counts["comparisons"],
        experiment_critic_files=critic_counts["experiment_critic_files"],
        task_features=critic_counts["task_features"],
        components_perf=critic_counts["components_perf"],
        spawns=critic_counts["spawns"],
    )


def auto_push_enabled() -> bool:
    """Whether daemon hooks should upload portable artifacts automatically."""
    raw = os.environ.get(AUTO_PUSH_ENV, "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def maybe_auto_push(
    *,
    instance_id: str,
    include_lab_wide: bool,
) -> None:
    """Best-effort daemon hook; never raises to the experiment pipeline."""
    if not auto_push_enabled():
        return
    try:
        summary = sync_portable_runs(
            direction="push",
            instance_id=instance_id,
            include_lab_wide=include_lab_wide,
        )
    except GCSRunsSyncError:
        log.exception(
            "portable artifact auto-push failed for %s (include_lab_wide=%s)",
            instance_id, include_lab_wide,
        )
        return
    log.info(
        "portable artifact auto-push complete for %s: synced=%s skipped=%s",
        instance_id,
        ", ".join(summary.synced) or "(none)",
        ", ".join(summary.skipped) or "(none)",
    )


def _experiment_run_dirs() -> list[Path]:
    if not EXPERIMENTS_RUNS_ROOT.is_dir():
        return []
    return sorted(
        d.resolve()
        for d in EXPERIMENTS_RUNS_ROOT.iterdir()
        if d.is_dir() and (d / "experiment.json").is_file()
    )


def _run_rsync(
    *,
    src: str,
    dst: str,
    description: str,
    dry_run: bool,
    delete_unmatched: bool,
) -> None:
    cmd = [
        "gcloud",
        "storage",
        "rsync",
        src,
        dst,
        "--recursive",
        "--skip-if-dest-has-newer-mtime",
    ]
    if delete_unmatched:
        cmd.append("--delete-unmatched-destination-objects")
    if dry_run:
        cmd.append("--dry-run")
    try:
        subprocess.run(cmd, check=True)
    except FileNotFoundError as exc:
        raise GCSRunsSyncError(
            "gcloud is not installed or not on PATH; install Google Cloud SDK first."
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise GCSRunsSyncError(
            f"gcloud storage rsync failed for {description} (exit {exc.returncode})."
        ) from exc
