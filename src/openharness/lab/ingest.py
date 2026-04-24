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

from openharness.lab import critic_io
from openharness.lab import db as labdb
from openharness.lab.paths import EXPERIMENTS_RUNS_ROOT, REPO_ROOT
from openharness.lab.usage import augment_spawn_record

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
        return [
            (
                cid,
                "registry_unavailable",
                {"reason": str(exc)},
            )
            for cid in components_active
        ]

    try:
        registry = comps.load_registry()
    except comps.ComponentError as exc:
        return [
            (
                cid,
                "registry_invalid",
                {"reason": str(exc)},
            )
            for cid in components_active
        ]

    issues: list[tuple[str, str, dict[str, Any]]] = []
    chosen_ids = set(components_active)
    for cid in components_active:
        spec = registry.get(cid)
        if spec is None:
            issues.append((cid, "unknown_id", {"known": sorted(registry)}))
            continue
        if (
            spec.applies_to_architectures
            and architecture
            and architecture not in spec.applies_to_architectures
        ):
            issues.append(
                (
                    cid,
                    "architecture_mismatch",
                    {
                        "agent_architecture": architecture,
                        "supported": list(spec.applies_to_architectures),
                    },
                )
            )
        if spec.applies_to_agents and agent_name and agent_name not in spec.applies_to_agents:
            issues.append(
                (
                    cid,
                    "agent_mismatch",
                    {"agent_name": agent_name, "supported": list(spec.applies_to_agents)},
                )
            )
        clashing = chosen_ids & set(spec.conflicts_with)
        if clashing:
            issues.append(
                (
                    cid,
                    "conflicts_with",
                    {"clashing": sorted(clashing)},
                )
            )
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


def _parse_ts(value: object) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
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
            conn,
            trial_ids=leg_trial_ids,
            findings=leg_findings,
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


# ---------------------------------------------------------------------------
# Critic artifact ingest (file -> DB cache)
# ---------------------------------------------------------------------------
#
# After the file-based critic refactor, critic skills NEVER write to
# DuckDB themselves; they write JSON files via `critic_io`. This pair
# of functions rebuilds the cache tables from those files on demand.

_TRIAL_DIR_TO_ID_CACHE: dict[str, str] = {}


def _trial_id_for_dir(conn: duckdb.DuckDBPyConnection, trial_dir: Path) -> str | None:
    key = str(trial_dir.resolve())
    cached = _TRIAL_DIR_TO_ID_CACHE.get(key)
    if cached is not None:
        return cached
    row = conn.execute(
        "SELECT trial_id FROM trials WHERE trial_dir = ?",
        [key],
    ).fetchone()
    if row:
        _TRIAL_DIR_TO_ID_CACHE[key] = row[0]
        return row[0]
    return None


def _instance_id_for_run_dir(conn: duckdb.DuckDBPyConnection, run_dir: Path) -> str | None:
    row = conn.execute(
        "SELECT instance_id FROM experiments WHERE run_dir = ?",
        [str(run_dir.resolve())],
    ).fetchone()
    return row[0] if row else None


def _provenance_model(payload: dict[str, Any]) -> str | None:
    prov = payload.get("provenance") or {}
    return prov.get("critic_model") or payload.get("critic_model")


def _provenance_created_at(payload: dict[str, Any]) -> datetime:
    prov = payload.get("provenance") or {}
    raw = prov.get("created_at") or payload.get("created_at")
    if isinstance(raw, str):
        ts = _parse_ts(raw)
        if ts is not None:
            return ts
    return datetime.now(timezone.utc)


def _upsert_trial_critique(
    conn: duckdb.DuckDBPyConnection,
    trial_id: str,
    payload: dict[str, Any],
) -> None:
    conn.execute(
        """
        INSERT INTO trial_critiques (
            trial_id, schema_version, task_summary, agent_strategy, key_actions,
            outcome, root_cause, success_factor, anti_patterns, components_active,
            task_features, surprising_observations, confidence, critic_model,
            extra, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT (trial_id) DO UPDATE SET
            schema_version = EXCLUDED.schema_version,
            task_summary = EXCLUDED.task_summary,
            agent_strategy = EXCLUDED.agent_strategy,
            key_actions = EXCLUDED.key_actions,
            outcome = EXCLUDED.outcome,
            root_cause = EXCLUDED.root_cause,
            success_factor = EXCLUDED.success_factor,
            anti_patterns = EXCLUDED.anti_patterns,
            components_active = EXCLUDED.components_active,
            task_features = EXCLUDED.task_features,
            surprising_observations = EXCLUDED.surprising_observations,
            confidence = EXCLUDED.confidence,
            critic_model = EXCLUDED.critic_model,
            extra = EXCLUDED.extra,
            created_at = EXCLUDED.created_at
        """,
        [
            trial_id,
            int(payload.get("schema_version", 1)),
            payload.get("task_summary"),
            payload.get("agent_strategy"),
            json.dumps(payload.get("key_actions") or []),
            payload.get("outcome"),
            payload.get("root_cause"),
            payload.get("success_factor"),
            json.dumps(payload.get("anti_patterns") or []),
            json.dumps(payload.get("components_active") or []),
            json.dumps(payload.get("task_features") or []),
            json.dumps(payload.get("surprising_observations") or []),
            payload.get("confidence"),
            _provenance_model(payload),
            json.dumps(payload.get("extra") or {}),
            _provenance_created_at(payload),
        ],
    )


def _upsert_comparison(
    conn: duckdb.DuckDBPyConnection,
    instance_id: str,
    task_name: str,
    payload: dict[str, Any],
) -> None:
    conn.execute(
        """
        INSERT INTO comparisons (
            instance_id, task_name, winning_leg, runner_up_leg, delta_score,
            why, evidence, legs_compared, critic_model, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT (instance_id, task_name) DO UPDATE SET
            winning_leg = EXCLUDED.winning_leg,
            runner_up_leg = EXCLUDED.runner_up_leg,
            delta_score = EXCLUDED.delta_score,
            why = EXCLUDED.why,
            evidence = EXCLUDED.evidence,
            legs_compared = EXCLUDED.legs_compared,
            critic_model = EXCLUDED.critic_model,
            created_at = EXCLUDED.created_at
        """,
        [
            instance_id,
            task_name,
            payload.get("winning_leg"),
            payload.get("runner_up_leg"),
            payload.get("delta_score"),
            payload.get("why"),
            json.dumps(payload.get("evidence") or {}),
            json.dumps(payload.get("legs_compared") or []),
            _provenance_model(payload),
            _provenance_created_at(payload),
        ],
    )


def _upsert_task_features(
    conn: duckdb.DuckDBPyConnection,
    task_checksum: str,
    payload: dict[str, Any],
) -> None:
    conn.execute(
        """
        INSERT INTO task_features (
            task_checksum, task_name, category, required_tools, env_complexity,
            output_shape, keywords, extra, extracted_by, extracted_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT (task_checksum) DO UPDATE SET
            task_name = EXCLUDED.task_name,
            category = EXCLUDED.category,
            required_tools = EXCLUDED.required_tools,
            env_complexity = EXCLUDED.env_complexity,
            output_shape = EXCLUDED.output_shape,
            keywords = EXCLUDED.keywords,
            extra = EXCLUDED.extra,
            extracted_by = EXCLUDED.extracted_by,
            extracted_at = EXCLUDED.extracted_at
        """,
        [
            task_checksum,
            payload.get("task_name"),
            payload.get("category"),
            json.dumps(payload.get("required_tools") or []),
            payload.get("env_complexity"),
            payload.get("output_shape"),
            json.dumps(payload.get("keywords") or []),
            json.dumps(payload.get("extra") or {}),
            payload.get("extracted_by") or _provenance_model(payload),
            _provenance_created_at(payload),
        ],
    )


def _upsert_component_perf(
    conn: duckdb.DuckDBPyConnection,
    component_id: str,
    task_cluster: str,
    payload: dict[str, Any],
) -> None:
    conn.execute(
        """
        INSERT INTO components_perf (
            component_id, task_cluster, n_trials, win_rate, cost_delta_pct,
            supporting_experiments, notes, updated_at
        ) VALUES (?,?,?,?,?,?,?,?)
        ON CONFLICT (component_id, task_cluster) DO UPDATE SET
            n_trials = EXCLUDED.n_trials,
            win_rate = EXCLUDED.win_rate,
            cost_delta_pct = EXCLUDED.cost_delta_pct,
            supporting_experiments = EXCLUDED.supporting_experiments,
            notes = EXCLUDED.notes,
            updated_at = EXCLUDED.updated_at
        """,
        [
            component_id,
            task_cluster,
            int(payload.get("n_trials", 0)),
            payload.get("win_rate"),
            payload.get("cost_delta_pct"),
            json.dumps(payload.get("supporting_experiments") or []),
            payload.get("notes"),
            _provenance_created_at(payload),
        ],
    )


def _upsert_spawn(
    conn: duckdb.DuckDBPyConnection,
    record: dict[str, Any],
) -> None:
    record = augment_spawn_record(record)
    conn.execute(
        """
        INSERT INTO spawns (
            spawn_id, skill, args, cwd, log_path, started_at,
            finished_at, exit_code, cost_usd_estimate,
            parent_run_dir, notes, provider, model, input_tokens,
            cached_input_tokens, output_tokens, reasoning_output_tokens,
            total_tokens, duration_sec, effective_settings, last_message
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT (spawn_id) DO UPDATE SET
            skill = EXCLUDED.skill,
            args = EXCLUDED.args,
            cwd = EXCLUDED.cwd,
            log_path = EXCLUDED.log_path,
            started_at = EXCLUDED.started_at,
            finished_at = EXCLUDED.finished_at,
            exit_code = EXCLUDED.exit_code,
            cost_usd_estimate = EXCLUDED.cost_usd_estimate,
            parent_run_dir = EXCLUDED.parent_run_dir,
            notes = EXCLUDED.notes,
            provider = EXCLUDED.provider,
            model = EXCLUDED.model,
            input_tokens = EXCLUDED.input_tokens,
            cached_input_tokens = EXCLUDED.cached_input_tokens,
            output_tokens = EXCLUDED.output_tokens,
            reasoning_output_tokens = EXCLUDED.reasoning_output_tokens,
            total_tokens = EXCLUDED.total_tokens,
            duration_sec = EXCLUDED.duration_sec,
            effective_settings = EXCLUDED.effective_settings,
            last_message = EXCLUDED.last_message
        """,
        [
            record["spawn_id"],
            record.get("skill"),
            json.dumps(record.get("args") or []),
            record.get("cwd") or str(REPO_ROOT),
            record.get("log_path"),
            _parse_ts(record.get("started_at")),
            _parse_ts(record.get("finished_at")),
            record.get("exit_code"),
            record.get("cost_usd_estimate"),
            record.get("parent_run_dir"),
            record.get("notes"),
            record.get("provider"),
            record.get("model"),
            record.get("input_tokens"),
            record.get("cached_input_tokens"),
            record.get("output_tokens"),
            record.get("reasoning_output_tokens"),
            record.get("total_tokens"),
            record.get("duration_sec"),
            json.dumps(record.get("effective_settings") or {}),
            record.get("last_message"),
        ],
    )


def _backfill_spawn_usage(conn: duckdb.DuckDBPyConnection) -> int:
    rows = conn.execute(
        """
        SELECT spawn_id, skill, args, cwd, log_path, started_at, finished_at,
               exit_code, cost_usd_estimate, parent_run_dir, notes, provider,
               model, input_tokens, cached_input_tokens, output_tokens,
               reasoning_output_tokens, total_tokens, duration_sec,
               effective_settings, last_message
        FROM spawns
        WHERE log_path IS NOT NULL
          AND (provider IS NULL OR model IS NULL OR total_tokens IS NULL)
        """
    ).fetchall()
    cols = [d[0] for d in conn.description]
    for row in rows:
        _upsert_spawn(conn, dict(zip(cols, row, strict=True)))
    return len(rows)


def ingest_critiques(
    run_dirs: Iterable[Path] | None = None,
    *,
    include_lab_wide: bool = True,
) -> dict[str, int]:
    """Walk on-disk critic artifacts and rebuild the DB cache tables.

    Idempotent. Safe to call repeatedly; each row upserts on its
    natural key. Pass `run_dirs=None` to scan every experiment under
    `runs/experiments/`. Pass `include_lab_wide=False` to skip the
    global artifacts (`task_features`, `components_perf`, `spawns`).
    """
    counts = {
        "trial_critiques": 0,
        "comparisons": 0,
        "experiment_critic_files": 0,
        "task_features": 0,
        "components_perf": 0,
        "spawns": 0,
    }
    if run_dirs is None:
        if not EXPERIMENTS_RUNS_ROOT.is_dir():
            scoped: list[Path] = []
        else:
            scoped = [d for d in EXPERIMENTS_RUNS_ROOT.iterdir() if d.is_dir()]
    else:
        scoped = [Path(d).resolve() for d in run_dirs]

    with labdb.writer() as conn:
        for run_dir in scoped:
            instance_id = _instance_id_for_run_dir(conn, run_dir)
            for trial_dir, payload in critic_io.iter_trial_critiques(run_dir):
                tid = _trial_id_for_dir(conn, trial_dir)
                if not tid:
                    logger.warning(
                        "trial_critique at %s has no matching trials row; skipping",
                        trial_dir,
                    )
                    continue
                _upsert_trial_critique(conn, tid, payload)
                counts["trial_critiques"] += 1
            if instance_id:
                for task_name, payload in critic_io.iter_comparisons(run_dir):
                    _upsert_comparison(conn, instance_id, task_name, payload)
                    counts["comparisons"] += 1
            exp_path = critic_io.experiment_critic_path(run_dir)
            if exp_path.is_file():
                counts["experiment_critic_files"] += 1
        if include_lab_wide:
            for checksum, payload in critic_io.iter_task_features():
                _upsert_task_features(conn, checksum, payload)
                counts["task_features"] += 1
            for cid, cluster, payload in critic_io.iter_components_perf():
                _upsert_component_perf(conn, cid, cluster, payload)
                counts["components_perf"] += 1
            for record in critic_io.iter_spawn_records():
                if "spawn_id" not in record:
                    continue
                _upsert_spawn(conn, record)
                counts["spawns"] += 1
            counts["spawns"] += _backfill_spawn_usage(conn)
    return counts


def dump_db_to_files(
    *,
    instance_id: str | None = None,
    overwrite: bool = False,
) -> dict[str, int]:
    """Materialize existing DB rows to the file scheme.

    One-shot migration: write `<trial_dir>/critic/trial-critic.json`,
    `<run_dir>/critic/comparisons/<task>.json`, and
    `runs/lab/task_features/<checksum>.json` for every row currently
    in the DB. Skips files that already exist unless --overwrite.
    """
    counts = {
        "trial_critiques": 0,
        "comparisons": 0,
        "task_features": 0,
        "components_perf": 0,
    }
    with labdb.reader() as conn:
        if instance_id:
            trial_rows = conn.execute(
                """
                SELECT t.trial_id, t.trial_dir, c.schema_version, c.task_summary,
                       c.agent_strategy, c.key_actions, c.outcome, c.root_cause,
                       c.success_factor, c.anti_patterns, c.components_active,
                       c.task_features, c.surprising_observations, c.confidence,
                       c.critic_model, c.extra, c.created_at
                FROM trial_critiques c JOIN trials t USING (trial_id)
                WHERE t.instance_id = ?
                """,
                [instance_id],
            ).fetchall()
            cmp_rows = conn.execute(
                """
                SELECT instance_id, task_name, winning_leg, runner_up_leg,
                       delta_score, why, evidence, legs_compared, critic_model,
                       created_at
                FROM comparisons WHERE instance_id = ?
                """,
                [instance_id],
            ).fetchall()
            tf_rows = conn.execute(
                """
                SELECT DISTINCT f.task_checksum, f.task_name, f.category,
                       f.required_tools, f.env_complexity, f.output_shape,
                       f.keywords, f.extra, f.extracted_by, f.extracted_at
                FROM task_features f
                JOIN trials t USING (task_checksum)
                WHERE t.instance_id = ?
                """,
                [instance_id],
            ).fetchall()
        else:
            trial_rows = conn.execute(
                """
                SELECT t.trial_id, t.trial_dir, c.schema_version, c.task_summary,
                       c.agent_strategy, c.key_actions, c.outcome, c.root_cause,
                       c.success_factor, c.anti_patterns, c.components_active,
                       c.task_features, c.surprising_observations, c.confidence,
                       c.critic_model, c.extra, c.created_at
                FROM trial_critiques c JOIN trials t USING (trial_id)
                """,
            ).fetchall()
            cmp_rows = conn.execute(
                """
                SELECT instance_id, task_name, winning_leg, runner_up_leg,
                       delta_score, why, evidence, legs_compared, critic_model,
                       created_at
                FROM comparisons
                """,
            ).fetchall()
            tf_rows = conn.execute(
                """
                SELECT task_checksum, task_name, category, required_tools,
                       env_complexity, output_shape, keywords, extra,
                       extracted_by, extracted_at
                FROM task_features
                """,
            ).fetchall()
        cp_rows = conn.execute(
            """
            SELECT component_id, task_cluster, n_trials, win_rate, cost_delta_pct,
                   supporting_experiments, notes, updated_at
            FROM components_perf
            """,
        ).fetchall()
        run_dir_by_instance: dict[str, Path] = {}
        for inst, run_dir in conn.execute(
            "SELECT instance_id, run_dir FROM experiments"
        ).fetchall():
            run_dir_by_instance[inst] = Path(run_dir)

    def _maybe_write(path: Path, body: dict[str, Any]) -> bool:
        if path.is_file() and not overwrite:
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(body, indent=2, default=str))
        return True

    for row in trial_rows:
        (
            trial_id,
            trial_dir,
            schema_version,
            task_summary,
            agent_strategy,
            key_actions,
            outcome,
            root_cause,
            success_factor,
            anti_patterns,
            components_active,
            task_features,
            surprising_observations,
            confidence,
            critic_model,
            extra,
            created_at,
        ) = row
        body = {
            "kind": "trial_critique",
            "schema_version": int(schema_version or 1),
            "provenance": {
                "skill": "trial-critic",
                "critic_model": critic_model,
                "created_at": _ts_iso(created_at),
                "source": "dump-critiques-to-files",
            },
            "trial_id": trial_id,
            "task_summary": task_summary,
            "agent_strategy": agent_strategy,
            "key_actions": _maybe_loads(key_actions, []),
            "outcome": outcome,
            "root_cause": root_cause,
            "success_factor": success_factor,
            "anti_patterns": _maybe_loads(anti_patterns, []),
            "components_active": _maybe_loads(components_active, []),
            "task_features": _maybe_loads(task_features, []),
            "surprising_observations": _maybe_loads(surprising_observations, []),
            "confidence": confidence,
            "extra": _maybe_loads(extra, {}),
        }
        path = critic_io.trial_critique_path(Path(trial_dir))
        if _maybe_write(path, body):
            counts["trial_critiques"] += 1
    for row in cmp_rows:
        (
            inst,
            task_name,
            winning_leg,
            runner_up_leg,
            delta_score,
            why,
            evidence,
            legs_compared,
            critic_model,
            created_at,
        ) = row
        run_dir = run_dir_by_instance.get(inst)
        if run_dir is None:
            continue
        body = {
            "kind": "comparison",
            "schema_version": 1,
            "provenance": {
                "skill": "experiment-critic",
                "critic_model": critic_model,
                "created_at": _ts_iso(created_at),
                "source": "dump-critiques-to-files",
            },
            "instance_id": inst,
            "task_name": task_name,
            "winning_leg": winning_leg,
            "runner_up_leg": runner_up_leg,
            "delta_score": delta_score,
            "why": why,
            "evidence": _maybe_loads(evidence, {}),
            "legs_compared": _maybe_loads(legs_compared, []),
        }
        path = critic_io.comparison_path(run_dir, task_name)
        if _maybe_write(path, body):
            counts["comparisons"] += 1
    for row in tf_rows:
        (
            task_checksum,
            task_name,
            category,
            required_tools,
            env_complexity,
            output_shape,
            keywords,
            extra,
            extracted_by,
            extracted_at,
        ) = row
        body = {
            "kind": "task_features",
            "schema_version": 1,
            "provenance": {
                "skill": "task-features",
                "critic_model": extracted_by,
                "created_at": _ts_iso(extracted_at),
                "source": "dump-critiques-to-files",
            },
            "task_checksum": task_checksum,
            "task_name": task_name,
            "category": category,
            "required_tools": _maybe_loads(required_tools, []),
            "env_complexity": env_complexity,
            "output_shape": output_shape,
            "keywords": _maybe_loads(keywords, []),
            "extra": _maybe_loads(extra, {}),
            "extracted_by": extracted_by,
        }
        path = critic_io.task_features_path(task_checksum)
        if _maybe_write(path, body):
            counts["task_features"] += 1
    for row in cp_rows:
        (
            component_id,
            task_cluster,
            n_trials,
            win_rate,
            cost_delta_pct,
            supporting,
            notes,
            updated_at,
        ) = row
        body = {
            "kind": "component_perf",
            "schema_version": 1,
            "provenance": {
                "skill": "cross-experiment-critic",
                "created_at": _ts_iso(updated_at),
                "source": "dump-critiques-to-files",
            },
            "component_id": component_id,
            "task_cluster": task_cluster,
            "n_trials": n_trials,
            "win_rate": win_rate,
            "cost_delta_pct": cost_delta_pct,
            "supporting_experiments": _maybe_loads(supporting, []),
            "notes": notes,
        }
        path = critic_io.component_perf_path(component_id, task_cluster)
        if _maybe_write(path, body):
            counts["components_perf"] += 1
    return counts


def _maybe_loads(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (list, dict)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default
    return default


def _ts_iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)
