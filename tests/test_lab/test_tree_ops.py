"""Unit tests for ``openharness.lab.tree_ops``.

Covers the verdict thresholds (graduate / add_branch / reject /
no_op) end-to-end against synthetic DBs built with the real
schema, plus a sanity check against the existing tb2-baseline
data when it's present in the workspace (the historical broad-sweep
should classify cleanly under the new rules).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pytest

from openharness.lab import db as labdb
from openharness.lab import tree_ops


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fresh_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "trials.duckdb"
    conn = labdb.connect(db_path=db_path, read_only=False)
    conn.close()
    return db_path


def _seed_experiment(
    conn: duckdb.DuckDBPyConnection,
    *,
    instance_id: str,
    legs: dict[str, dict],
    tasks: list[dict],
) -> None:
    """Seed an experiments + legs + trials + (optional) task_features.

    `legs[leg_id]` may carry `{"agent_id": ..., "outcomes": {task_name: (passed, cost_usd)}}`.
    `tasks[i]` is `{"task_name": ..., "task_checksum": ..., "category": ...}`.
    """
    now = datetime.now(timezone.utc)
    run_dir = f"/tmp/{instance_id}"
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
         "host", "0", "0", "3.13", now, now, None, run_dir, now],
    )
    for leg_id, info in legs.items():
        conn.execute(
            """
            INSERT INTO legs (
                instance_id, leg_id, agent_id, agent_architecture, model,
                max_turns, max_tokens, components_active, agent_resolved_yaml,
                agent_config_hash, status, result_status, started_at,
                finished_at, duration_sec
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            [instance_id, leg_id, info.get("agent_id", leg_id),
             None, "test-model", 30, None, "[]", None, None,
             "completed", "ok", now, now, 0.0],
        )
    for task in tasks:
        if "category" in task:
            conn.execute(
                """
                INSERT INTO task_features (
                    task_checksum, task_name, category, required_tools,
                    env_complexity, output_shape, keywords, extra,
                    extracted_by, extracted_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT (task_checksum) DO NOTHING
                """,
                [task["task_checksum"], task["task_name"], task["category"],
                 "[]", "low", "creates_new_file", "[]", "{}",
                 "test", now],
            )
        for leg_id, info in legs.items():
            outcome = info["outcomes"].get(task["task_name"])
            if outcome is None:
                continue
            passed, cost = outcome
            trial_id = f"{task['task_name']}__{leg_id}"
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
                """,
                [trial_id, instance_id, leg_id, task["task_name"],
                 task["task_checksum"], None, None, None,
                 1.0 if passed else 0.0, bool(passed),
                 "passed" if passed else "failed",
                 None, None, None,
                 "test-model", None, None, None, None,
                 cost, 0.0, 0.0, 0.0, 0.0,
                 None, None, "[]", None, None,
                 f"/tmp/{trial_id}", None, None, None],
            )


@pytest.fixture
def db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("OPENHARNESS_REPO_ROOT", str(tmp_path))
    # Make sure a stray trunk.yaml in the real repo doesn't bleed in.
    (tmp_path / "lab").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "openharness" / "agents" / "configs").mkdir(
        parents=True, exist_ok=True
    )
    return _fresh_db(tmp_path)


# ---------------------------------------------------------------------------
# Verdict tests on synthetic data
# ---------------------------------------------------------------------------


def test_evaluate_no_legs(db_path: Path) -> None:
    diff = tree_ops.evaluate(
        "ghost-instance",
        db_conn=labdb.connect(db_path=db_path, read_only=False),
    )
    assert diff.kind == "no_op"
    assert "no legs" in diff.rationale.lower()


def test_evaluate_graduate(db_path: Path) -> None:
    """Mutation lifts pass rate by >> 5pp with no per-cluster regression."""
    conn = labdb.connect(db_path=db_path, read_only=False)
    tasks = [
        {"task_name": f"t{i}", "task_checksum": f"c{i}", "category": "c_build"}
        for i in range(20)
    ]
    # trunk: 4/20 pass; mutation: 12/20 pass (+40pp). Same cluster, no regression.
    trunk_outcomes = {f"t{i}": (i < 4, 0.10) for i in range(20)}
    mut_outcomes = {f"t{i}": (i < 12, 0.12) for i in range(20)}
    _seed_experiment(
        conn,
        instance_id="grad-1",
        legs={
            "basic": {"agent_id": "basic", "outcomes": trunk_outcomes},
            "mutation": {"agent_id": "mutation", "outcomes": mut_outcomes},
        },
        tasks=tasks,
    )
    diff = tree_ops.evaluate("grad-1", db_conn=conn)
    assert diff.kind == "graduate", diff.rationale
    assert diff.target_id == "mutation"
    assert diff.pass_rate_delta_pp is not None
    assert diff.pass_rate_delta_pp >= tree_ops.GRADUATE_PASS_RATE_DELTA_PP


def test_evaluate_reject(db_path: Path) -> None:
    """Mutation regresses overall and on its only cluster."""
    conn = labdb.connect(db_path=db_path, read_only=False)
    tasks = [
        {"task_name": f"t{i}", "task_checksum": f"c{i}", "category": "python_data"}
        for i in range(10)
    ]
    trunk_outcomes = {f"t{i}": (i < 5, 0.10) for i in range(10)}    # 5/10
    mut_outcomes = {f"t{i}": (i < 1, 0.30) for i in range(10)}      # 1/10
    _seed_experiment(
        conn,
        instance_id="rej-1",
        legs={
            "basic": {"agent_id": "basic", "outcomes": trunk_outcomes},
            "candidate": {"agent_id": "candidate", "outcomes": mut_outcomes},
        },
        tasks=tasks,
    )
    diff = tree_ops.evaluate("rej-1", db_conn=conn)
    assert diff.kind == "reject", diff.rationale
    assert diff.target_id == "candidate"


def test_evaluate_add_branch(db_path: Path) -> None:
    """Trunk wins overall but mutation wins on >=2 coherent clusters.

    3 clusters of 8 tasks each. Trunk dominates `c_build` by 8-0
    (which keeps it ahead overall, 10/24 > 6/24), but mutation wins
    `python_ml` and `scientific_computing` by 3-1 each — two
    coherent positive clusters at +25pp ⇒ AddBranch.
    """
    conn = labdb.connect(db_path=db_path, read_only=False)
    tasks = []
    trunk_outcomes: dict[str, tuple[bool, float]] = {}
    mut_outcomes: dict[str, tuple[bool, float]] = {}
    for cluster, trunk_pass, mut_pass in [
        ("c_build", 8, 0),
        ("python_ml", 1, 3),
        ("scientific_computing", 1, 3),
    ]:
        for i in range(8):
            name = f"{cluster}-{i}"
            tasks.append({
                "task_name": name,
                "task_checksum": f"{name}-cs",
                "category": cluster,
            })
            trunk_outcomes[name] = (i < trunk_pass, 0.10)
            mut_outcomes[name] = (i < mut_pass, 0.10)
    _seed_experiment(
        conn,
        instance_id="branch-1",
        legs={
            "basic": {"agent_id": "basic", "outcomes": trunk_outcomes},
            "specialist": {"agent_id": "specialist", "outcomes": mut_outcomes},
        },
        tasks=tasks,
    )
    diff = tree_ops.evaluate("branch-1", db_conn=conn)
    assert diff.kind == "add_branch", diff.rationale
    assert diff.target_id == "specialist"
    assert diff.use_when is not None
    cats = {p["task_features.category"] for p in diff.use_when["any_of"]}
    assert "python_ml" in cats and "scientific_computing" in cats


def test_evaluate_no_op(db_path: Path) -> None:
    """Tiny effect on aggregate, no coherent cluster wins."""
    conn = labdb.connect(db_path=db_path, read_only=False)
    tasks = [
        {"task_name": f"t{i}", "task_checksum": f"c{i}", "category": "c_build"}
        for i in range(20)
    ]
    # Same pass rate, same cluster → no-op.
    trunk_outcomes = {f"t{i}": (i % 2 == 0, 0.10) for i in range(20)}     # 10/20
    mut_outcomes = {f"t{i}": (i % 2 == 0, 0.105) for i in range(20)}      # 10/20
    _seed_experiment(
        conn,
        instance_id="noop-1",
        legs={
            "basic": {"agent_id": "basic", "outcomes": trunk_outcomes},
            "twin": {"agent_id": "twin", "outcomes": mut_outcomes},
        },
        tasks=tasks,
    )
    diff = tree_ops.evaluate("noop-1", db_conn=conn)
    assert diff.kind == "no_op", diff.rationale
    assert diff.confidence == 0.0


def test_evaluate_only_trunk(db_path: Path) -> None:
    """Single-leg experiment can't justify a tree mutation."""
    conn = labdb.connect(db_path=db_path, read_only=False)
    tasks = [
        {"task_name": f"t{i}", "task_checksum": f"c{i}", "category": "c_build"}
        for i in range(5)
    ]
    _seed_experiment(
        conn,
        instance_id="solo-1",
        legs={"basic": {"agent_id": "basic", "outcomes": {
            f"t{i}": (i < 3, 0.10) for i in range(5)
        }}},
        tasks=tasks,
    )
    diff = tree_ops.evaluate("solo-1", db_conn=conn)
    assert diff.kind == "no_op"
    assert "only trunk" in diff.rationale.lower() or "single-leg" in diff.rationale.lower()


# ---------------------------------------------------------------------------
# Sanity check vs the real tb2-baseline data
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not (Path(os.environ.get("OPENHARNESS_REPO_ROOT", "."))
         / "runs/lab/trials.duckdb").is_file()
    and not (Path(__file__).resolve().parents[2]
             / "runs/lab/trials.duckdb").is_file(),
    reason="tb2-baseline DB not present in this checkout",
)
def test_evaluate_tb2_baseline_classifies_cleanly() -> None:
    """The historical tb2-baseline broad-sweep must produce a non-crashing,
    intuitively-correct verdict under the new rules.

    From `results/summary.md`:
        basic            20/89 = 22.5%, $5.84
        planner_executor 10/89 = 11.2%, $7.22
        react            12/89 = 13.5%, $22.64

    Both non-trunk legs regress overall; with trunk == basic, the
    primary verdict should be `reject` for the most-decisive non-trunk
    leg (the bigger regression with the more dramatic cost spike), or
    `add_branch` if both legs win cleanly on >=2 clusters. Either is
    fine — we just want the loop to be non-trivial and stable.
    """
    diff = tree_ops.evaluate("tb2-baseline-20260417-234913")
    assert diff.kind in {"reject", "add_branch", "no_op"}, diff
    assert diff.target_id in {"planner_executor", "react"}, diff
    assert diff.trunk_leg == "basic"
    # Trunk wins overall, so neither non-trunk leg should graduate.
    assert diff.kind != "graduate"
