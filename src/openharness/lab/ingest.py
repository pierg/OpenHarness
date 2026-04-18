"""Ingest a `runs/experiments/<id>/` directory into the lab DB.

Reads the canonical run artifacts:

- `experiment.json`              — instance metadata + per-leg trial list
- `results/rows.json`            — flat per-trial summary (already aggregated)
- `legs/<leg>/agent.resolved.yaml` — exact agent config used per leg
- `legs/<leg>/harbor/<inst>-<leg>/<trial>/result.json` — per-trial verifier
                                    result, agent_result, timing
- `legs/<leg>/harbor/.../<trial>/events.jsonl` (optional)
                                  — turn count + tool-call count

Idempotent on `trial_id`: re-running upserts every row.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

import duckdb
import yaml

from openharness.lab import db as labdb

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class IngestSummary:
    instance_id: str
    run_dir: Path
    legs_inserted: int
    trials_inserted: int
    trials_skipped: int
    misconfigurations: int = 0


def _scan_misconfigurations(
    *,
    components_active: list[str],
    architecture: str | None,
    agent_name: str | None,
) -> list[tuple[str, str, dict[str, Any]]]:
    """Return ``[(component_id, kind, detail_dict), ...]`` for one leg.

    Imports the components registry lazily so ingest still works when
    the registry layer fails to load (we only emit a generic
    ``registry_unavailable`` finding in that case).
    """
    if not components_active:
        return []
    try:
        from openharness.agents import components as comps
    except Exception as exc:  # pragma: no cover - defensive
        return [(
            cid, "registry_unavailable",
            {"reason": str(exc)},
        ) for cid in components_active]

    try:
        registry = comps.load_registry()
    except comps.ComponentError as exc:
        return [(
            cid, "registry_invalid",
            {"reason": str(exc)},
        ) for cid in components_active]

    issues: list[tuple[str, str, dict[str, Any]]] = []
    chosen_ids = set(components_active)
    for cid in components_active:
        spec = registry.get(cid)
        if spec is None:
            issues.append((cid, "unknown_id",
                           {"known": sorted(registry)}))
            continue
        if spec.applies_to_architectures and architecture and \
                architecture not in spec.applies_to_architectures:
            issues.append((
                cid, "architecture_mismatch",
                {"agent_architecture": architecture,
                 "supported": list(spec.applies_to_architectures)},
            ))
        if spec.applies_to_agents and agent_name and \
                agent_name not in spec.applies_to_agents:
            issues.append((
                cid, "agent_mismatch",
                {"agent_name": agent_name,
                 "supported": list(spec.applies_to_agents)},
            ))
        clashing = chosen_ids & set(spec.conflicts_with)
        if clashing:
            issues.append((
                cid, "conflicts_with",
                {"clashing": sorted(clashing)},
            ))
    return issues


def _record_misconfigurations(
    conn: duckdb.DuckDBPyConnection,
    *,
    trial_ids: Iterable[str],
    findings: list[tuple[str, str, dict[str, Any]]],
) -> int:
    """Upsert leg-wide findings against every trial in the leg."""
    if not findings:
        return 0
    now = datetime.now(timezone.utc)
    n = 0
    for tid in trial_ids:
        for cid, kind, detail in findings:
            conn.execute(
                """
                INSERT INTO misconfigurations (
                    trial_id, component_id, kind, detail, created_at
                ) VALUES (?,?,?,?,?)
                ON CONFLICT (trial_id, component_id, kind) DO UPDATE SET
                    detail = EXCLUDED.detail,
                    created_at = EXCLUDED.created_at
                """,
                [tid, cid, kind, json.dumps(detail), now],
            )
            n += 1
    return n


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text())


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _events_stats(events_path: Path) -> tuple[int, int]:
    """Return (n_assistant_turns, n_tool_calls) for an events.jsonl file."""
    if not events_path.is_file():
        return (0, 0)
    n_turns = 0
    n_tools = 0
    try:
        with events_path.open() as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                t = ev.get("type")
                if t == "assistant_complete":
                    n_turns += 1
                elif t == "tool_started":
                    n_tools += 1
    except OSError:
        return (n_turns, n_tools)
    return (n_turns, n_tools)


def _load_agent_resolved(leg_dir: Path) -> tuple[str | None, dict[str, Any]]:
    path = leg_dir / "agent.resolved.yaml"
    if not path.is_file():
        return (None, {})
    raw = path.read_text()
    try:
        data = yaml.safe_load(raw) or {}
    except yaml.YAMLError:
        data = {}
    return (raw, data)


def _agent_config_hash(raw_yaml: str | None) -> str | None:
    if raw_yaml is None:
        return None
    return hashlib.sha256(raw_yaml.encode("utf-8")).hexdigest()[:16]


def _read_result_json(trial_dir: Path) -> dict[str, Any]:
    path = trial_dir / "result.json"
    if not path.is_file():
        return {}
    try:
        return _read_json(path)
    except (OSError, json.JSONDecodeError):
        return {}


def _final_text(result: dict[str, Any]) -> str | None:
    summary = (result.get("agent_result") or {}).get("metadata", {}).get("summary") or {}
    text = summary.get("final_text")
    if isinstance(text, str):
        return text[:8000]
    return None


def _classify_status(row: dict[str, Any]) -> str:
    status = row.get("status")
    if status:
        return str(status)
    if row.get("score", 0.0) and row["score"] > 0:
        return "passed"
    return "failed"


def ingest_run(
    run_dir: Path,
    *,
    conn: duckdb.DuckDBPyConnection | None = None,
) -> IngestSummary:
    """Ingest a single experiment run directory into the DB.

    Pass an existing writer connection to batch multiple runs in one
    transaction; otherwise we open and close our own.
    """

    run_dir = run_dir.resolve()
    if not run_dir.is_dir():
        raise FileNotFoundError(f"Run directory does not exist: {run_dir}")

    experiment_path = run_dir / "experiment.json"
    if not experiment_path.is_file():
        raise FileNotFoundError(
            f"{run_dir} is not an experiment run directory (no experiment.json)."
        )

    if conn is None:
        with labdb.writer() as c:
            return _ingest_run_inner(run_dir, experiment_path, c)
    return _ingest_run_inner(run_dir, experiment_path, conn)


@contextlib.contextmanager
def _noop_cm(conn: duckdb.DuckDBPyConnection) -> Iterator[duckdb.DuckDBPyConnection]:
    yield conn


def _ingest_run_inner(
    run_dir: Path,
    experiment_path: Path,
    conn: duckdb.DuckDBPyConnection,
) -> IngestSummary:
    exp = _read_json(experiment_path)
    instance_id = exp["instance_id"]
    repro = exp.get("reproducibility") or {}

    summary_path = run_dir / "results" / "summary.md"
    rows_path = run_dir / "results" / "rows.json"
    rows_by_trial: dict[str, dict[str, Any]] = {}
    if rows_path.is_file():
        for row in _read_json(rows_path):
            rows_by_trial[row["trial_id"]] = row

    conn.execute(
        """
        INSERT INTO experiments (
            instance_id, experiment_id, dataset, spec_path, resolved_spec,
            git_sha, git_dirty, hostname, openharness_ver, harbor_ver,
            python_ver, created_at, updated_at, summary_path, run_dir,
            ingested_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT (instance_id) DO UPDATE SET
            experiment_id = EXCLUDED.experiment_id,
            dataset = EXCLUDED.dataset,
            spec_path = EXCLUDED.spec_path,
            resolved_spec = EXCLUDED.resolved_spec,
            git_sha = EXCLUDED.git_sha,
            git_dirty = EXCLUDED.git_dirty,
            hostname = EXCLUDED.hostname,
            openharness_ver = EXCLUDED.openharness_ver,
            harbor_ver = EXCLUDED.harbor_ver,
            python_ver = EXCLUDED.python_ver,
            updated_at = EXCLUDED.updated_at,
            summary_path = EXCLUDED.summary_path,
            run_dir = EXCLUDED.run_dir,
            ingested_at = EXCLUDED.ingested_at
        """,
        [
            instance_id,
            exp.get("experiment_id"),
            exp.get("dataset"),
            exp.get("spec_path"),
            exp.get("resolved_spec_path"),
            repro.get("git_sha"),
            repro.get("git_dirty"),
            repro.get("hostname"),
            repro.get("openharness_version"),
            repro.get("harbor_version"),
            repro.get("python_version"),
            _parse_ts(exp.get("created_at")),
            _parse_ts(exp.get("updated_at")),
            str(summary_path.relative_to(run_dir)) if summary_path.is_file() else None,
            str(run_dir),
            datetime.now(timezone.utc),
        ],
    )

    legs_inserted = 0
    trials_inserted = 0
    trials_skipped = 0
    misconfig_count = 0

    for leg in exp.get("legs", []):
        leg_id = leg["leg_id"]
        agent_id = leg.get("agent_id", leg_id)
        leg_dir = run_dir / "legs" / leg_id
        raw_yaml, parsed_yaml = _load_agent_resolved(leg_dir)
        components_list = list(parsed_yaml.get("components") or [])
        components_active = json.dumps(components_list)
        config_hash = _agent_config_hash(raw_yaml)
        leg_findings = _scan_misconfigurations(
            components_active=components_list,
            architecture=parsed_yaml.get("architecture"),
            agent_name=parsed_yaml.get("name"),
        )
        leg_trial_ids: list[str] = []

        conn.execute(
            """
            INSERT INTO legs (
                instance_id, leg_id, agent_id, agent_architecture, model,
                max_turns, max_tokens, components_active, agent_resolved_yaml,
                agent_config_hash, status, result_status, started_at,
                finished_at, duration_sec
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT (instance_id, leg_id) DO UPDATE SET
                agent_id = EXCLUDED.agent_id,
                agent_architecture = EXCLUDED.agent_architecture,
                model = EXCLUDED.model,
                max_turns = EXCLUDED.max_turns,
                max_tokens = EXCLUDED.max_tokens,
                components_active = EXCLUDED.components_active,
                agent_resolved_yaml = EXCLUDED.agent_resolved_yaml,
                agent_config_hash = EXCLUDED.agent_config_hash,
                status = EXCLUDED.status,
                result_status = EXCLUDED.result_status,
                started_at = EXCLUDED.started_at,
                finished_at = EXCLUDED.finished_at,
                duration_sec = EXCLUDED.duration_sec
            """,
            [
                instance_id,
                leg_id,
                agent_id,
                parsed_yaml.get("architecture"),
                parsed_yaml.get("model"),
                parsed_yaml.get("max_turns"),
                parsed_yaml.get("max_tokens"),
                components_active,
                raw_yaml,
                config_hash,
                leg.get("status"),
                leg.get("result_status"),
                _parse_ts(leg.get("started_at")),
                _parse_ts(leg.get("finished_at")),
                leg.get("duration_sec"),
            ],
        )
        legs_inserted += 1

        for trial in leg.get("trials", []):
            trial_id = trial["trial_id"]
            trial_dir_rel = trial.get("trial_dir") or ""
            trial_dir_abs = run_dir / trial_dir_rel
            result = _read_result_json(trial_dir_abs)
            row = rows_by_trial.get(trial_id, {})

            task_id = result.get("task_id") or {}
            n_turns, n_tools = _events_stats(trial_dir_abs / "events.jsonl")
            agent_result = result.get("agent_result") or {}

            error_type = row.get("error_type")
            error_phase = row.get("error_phase")
            error_message = row.get("error_message")
            if not error_type and trial.get("error"):
                err = trial.get("error") or {}
                if isinstance(err, dict):
                    error_type = err.get("type")
                    error_phase = err.get("phase")
                    error_message = err.get("message")

            params = [
                trial_id,
                instance_id,
                leg_id,
                trial.get("task_name"),
                result.get("task_checksum"),
                task_id.get("git_url"),
                task_id.get("git_commit_id"),
                task_id.get("path"),
                trial.get("score"),
                bool(trial.get("passed", False)),
                _classify_status(row | trial),
                error_type,
                error_phase,
                error_message,
                trial.get("model"),
                trial.get("input_tokens") or agent_result.get("n_input_tokens"),
                trial.get("output_tokens") or agent_result.get("n_output_tokens"),
                agent_result.get("n_cache_tokens"),
                trial.get("total_tokens"),
                trial.get("cost_usd") or agent_result.get("cost_usd"),
                trial.get("duration_sec"),
                trial.get("agent_duration_sec"),
                trial.get("env_setup_duration_sec"),
                trial.get("verifier_duration_sec"),
                n_turns or None,
                n_tools or None,
                components_active,
                trial.get("trace_id"),
                trial.get("trace_url"),
                str(trial_dir_abs),
                _parse_ts(result.get("started_at")),
                _parse_ts(result.get("finished_at")),
                _final_text(result),
            ]
            try:
                conn.execute(
                    """
                    INSERT INTO trials (
                        trial_id, instance_id, leg_id, task_name, task_checksum,
                        task_git_url, task_git_commit, task_path,
                        score, passed, status, error_type, error_phase, error_message,
                        model, input_tokens, output_tokens, cache_tokens, total_tokens,
                        cost_usd, duration_sec, agent_duration_sec,
                        env_setup_duration_sec, verifier_duration_sec,
                        n_turns, n_tool_calls, components_active,
                        trace_id, trace_url, trial_dir,
                        started_at, finished_at, final_text
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT (trial_id) DO UPDATE SET
                        instance_id = EXCLUDED.instance_id,
                        leg_id = EXCLUDED.leg_id,
                        task_name = EXCLUDED.task_name,
                        task_checksum = EXCLUDED.task_checksum,
                        score = EXCLUDED.score,
                        passed = EXCLUDED.passed,
                        status = EXCLUDED.status,
                        error_type = EXCLUDED.error_type,
                        error_phase = EXCLUDED.error_phase,
                        error_message = EXCLUDED.error_message,
                        model = EXCLUDED.model,
                        input_tokens = EXCLUDED.input_tokens,
                        output_tokens = EXCLUDED.output_tokens,
                        cache_tokens = EXCLUDED.cache_tokens,
                        total_tokens = EXCLUDED.total_tokens,
                        cost_usd = EXCLUDED.cost_usd,
                        duration_sec = EXCLUDED.duration_sec,
                        agent_duration_sec = EXCLUDED.agent_duration_sec,
                        env_setup_duration_sec = EXCLUDED.env_setup_duration_sec,
                        verifier_duration_sec = EXCLUDED.verifier_duration_sec,
                        n_turns = EXCLUDED.n_turns,
                        n_tool_calls = EXCLUDED.n_tool_calls,
                        components_active = EXCLUDED.components_active,
                        trace_id = EXCLUDED.trace_id,
                        trace_url = EXCLUDED.trace_url,
                        trial_dir = EXCLUDED.trial_dir,
                        started_at = EXCLUDED.started_at,
                        finished_at = EXCLUDED.finished_at,
                        final_text = EXCLUDED.final_text
                    """,
                    params,
                )
                trials_inserted += 1
                leg_trial_ids.append(trial_id)
            except duckdb.Error as exc:  # pragma: no cover - defensive
                logger.warning("Skipping trial %s: %s", trial_id, exc)
                trials_skipped += 1

        misconfig_count += _record_misconfigurations(
            conn, trial_ids=leg_trial_ids, findings=leg_findings,
        )

    return IngestSummary(
        instance_id=instance_id,
        run_dir=run_dir,
        legs_inserted=legs_inserted,
        trials_inserted=trials_inserted,
        trials_skipped=trials_skipped,
        misconfigurations=misconfig_count,
    )


def ingest_runs(
    run_dirs: Iterable[Path],
    *,
    conn: duckdb.DuckDBPyConnection | None = None,
) -> list[IngestSummary]:
    summaries: list[IngestSummary] = []
    if conn is None:
        with labdb.writer() as c:
            for d in run_dirs:
                summaries.append(ingest_run(d, conn=c))
    else:
        for d in run_dirs:
            summaries.append(ingest_run(d, conn=conn))
    return summaries
