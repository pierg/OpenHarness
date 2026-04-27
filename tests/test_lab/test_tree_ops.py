"""Unit tests for simplified experiment decisions."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pytest

from openharness.lab import db as labdb
from openharness.lab import tree_ops


def _fresh_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "trials.duckdb"
    conn = labdb.connect(db_path=db_path, read_only=False)
    conn.close()
    return db_path


def _seed_legs(
    conn: duckdb.DuckDBPyConnection,
    *,
    instance_id: str,
    legs: dict[str, str],
) -> None:
    now = datetime.now(timezone.utc)
    conn.execute(
        """
        INSERT INTO experiments (
            instance_id, experiment_id, dataset, spec_path, resolved_spec,
            git_sha, git_dirty, hostname, openharness_ver, harbor_ver,
            python_ver, created_at, updated_at, summary_path, run_dir,
            ingested_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        [instance_id, instance_id, "test", None, None, None, False,
         "host", "0", "0", "3.13", now, now, None, f"/tmp/{instance_id}", now],
    )
    for leg_id, agent_id in legs.items():
        conn.execute(
            """
            INSERT INTO legs (
                instance_id, leg_id, agent_id, agent_architecture, model,
                max_turns, max_tokens, components_active, agent_resolved_yaml,
                agent_config_hash, status, result_status, started_at,
                finished_at, duration_sec
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            [instance_id, leg_id, agent_id, None, "test-model", 30, None, "[]",
             None, None, "completed", "ok", now, now, 0.0],
        )


def _write_experiment_critic(run_dir: Path, payload: dict[str, object]) -> None:
    critic_dir = run_dir / "critic"
    critic_dir.mkdir(parents=True)
    (critic_dir / "experiment-critic.json").write_text(json.dumps(payload))


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return _fresh_db(tmp_path)


def test_load_decision_accept_from_experiment_critic(
    tmp_path: Path,
    db_path: Path,
) -> None:
    conn = labdb.connect(db_path=db_path, read_only=False)
    _seed_legs(
        conn,
        instance_id="accept-1",
        legs={"control": "basic", "candidate": "artifact_first"},
    )
    run_dir = tmp_path / "run"
    _write_experiment_critic(
        run_dir,
        {
            "verdict": "accept",
            "target_id": "artifact_first",
            "baseline_leg": "control",
            "candidate_leg": "candidate",
            "rationale": "The candidate generalizes the output-artifact policy.",
            "confidence": 0.8,
            "promotability_notes": "Uses runtime-visible task instructions only.",
        },
    )

    decision = tree_ops.load_decision("accept-1", db_conn=conn, run_dir=run_dir)

    assert decision.verdict == "accept"
    assert decision.target_id == "artifact_first"
    assert decision.baseline_leg == "control"
    assert decision.candidate_leg == "candidate"
    assert decision.confidence == 0.8
    assert decision.evidence_paths == [run_dir / "critic" / "experiment-critic.json"]


def test_load_decision_rejects_non_schema_verdicts(
    tmp_path: Path,
    db_path: Path,
) -> None:
    conn = labdb.connect(db_path=db_path, read_only=False)
    _seed_legs(conn, instance_id="bad-label-1", legs={"basic": "basic"})
    run_dir = tmp_path / "run"
    _write_experiment_critic(
        run_dir,
        {
            "verdict": "maybe",
            "rationale": "Useful measurement, not a candidate to promote.",
            "confidence": 0.4,
        },
    )

    with pytest.raises(ValueError, match="accept, reject, no_op"):
        tree_ops.load_decision("bad-label-1", db_conn=conn, run_dir=run_dir)


def test_load_decision_requires_verdict(
    tmp_path: Path,
    db_path: Path,
) -> None:
    conn = labdb.connect(db_path=db_path, read_only=False)
    _seed_legs(conn, instance_id="bad-1", legs={"basic": "basic"})
    run_dir = tmp_path / "run"
    _write_experiment_critic(run_dir, {"rationale": "missing verdict"})

    with pytest.raises(ValueError, match="verdict"):
        tree_ops.load_decision("bad-1", db_conn=conn, run_dir=run_dir)
