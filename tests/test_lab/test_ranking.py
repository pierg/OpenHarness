from __future__ import annotations

from datetime import datetime, timezone

import duckdb

from openharness.lab import ranking


def test_rankings_group_by_model_dataset_and_scope() -> None:
    conn = duckdb.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE experiments (
            instance_id TEXT,
            experiment_id TEXT,
            dataset TEXT,
            created_at TIMESTAMPTZ
        );
        CREATE TABLE legs (
            instance_id TEXT,
            leg_id TEXT,
            agent_id TEXT,
            model TEXT
        );
        CREATE TABLE trials (
            trial_id TEXT,
            instance_id TEXT,
            leg_id TEXT,
            passed BOOLEAN,
            model TEXT,
            cost_usd DOUBLE,
            total_tokens BIGINT,
            duration_sec DOUBLE
        );
        CREATE TABLE experiment_evaluations (
            instance_id TEXT,
            verdict TEXT,
            target_id TEXT,
            baseline_leg TEXT,
            candidate_leg TEXT,
            rationale TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO experiments VALUES (?, ?, ?, ?), (?, ?, ?, ?)",
        [
            "full-1",
            "tb2-full",
            "terminal-bench@2.0",
            datetime(2026, 4, 1, tzinfo=timezone.utc),
            "slice-1",
            "tb2-slice",
            "terminal-bench@2.0",
            datetime(2026, 4, 2, tzinfo=timezone.utc),
        ],
    )
    conn.execute(
        "INSERT INTO legs VALUES (?, ?, ?, ?), (?, ?, ?, ?), (?, ?, ?, ?)",
        [
            "full-1",
            "a",
            "agent-a",
            "model-x",
            "full-1",
            "b",
            "agent-b",
            "model-x",
            "slice-1",
            "c",
            "agent-c",
            "model-x",
        ],
    )
    full_trials = []
    for idx in range(80):
        full_trials.append((f"a-{idx}", "full-1", "a", idx < 40, "model-x", 1.0, 100, 10.0))
        full_trials.append((f"b-{idx}", "full-1", "b", idx < 60, "model-x", 2.0, 200, 20.0))
    for idx in range(4):
        full_trials.append((f"c-{idx}", "slice-1", "c", idx < 4, "model-x", 1.0, 100, 10.0))
    conn.executemany("INSERT INTO trials VALUES (?, ?, ?, ?, ?, ?, ?, ?)", full_trials)
    conn.execute(
        "INSERT INTO experiment_evaluations VALUES (?, ?, ?, ?, ?, ?), (?, ?, ?, ?, ?, ?)",
        [
            "full-1",
            "accept",
            "agent-b",
            "a",
            "b",
            "valid full-suite result",
            "slice-1",
            "no_op",
            "agent-c",
            None,
            "c",
            "diagnostic slice",
        ],
    )

    rows = ranking.rankings(conn)
    full_rows = [r for r in rows if r.evidence_scope == "full_suite"]
    slice_rows = [r for r in rows if r.evidence_scope == "slice"]

    assert [r.agent_id for r in full_rows] == ["agent-b", "agent-a"]
    assert [r.rank for r in full_rows] == [1, 2]
    assert slice_rows[0].rank == 1
    assert slice_rows[0].agent_id == "agent-c"
    assert [r.agent_id for r in ranking.best_by_model(conn)] == ["agent-b"]


def test_rejected_target_is_visible_but_ineligible() -> None:
    conn = duckdb.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE experiments (
            instance_id TEXT,
            experiment_id TEXT,
            dataset TEXT,
            created_at TIMESTAMPTZ
        );
        CREATE TABLE legs (
            instance_id TEXT,
            leg_id TEXT,
            agent_id TEXT,
            model TEXT
        );
        CREATE TABLE trials (
            trial_id TEXT,
            instance_id TEXT,
            leg_id TEXT,
            passed BOOLEAN,
            model TEXT,
            cost_usd DOUBLE,
            total_tokens BIGINT,
            duration_sec DOUBLE
        );
        CREATE TABLE experiment_evaluations (
            instance_id TEXT,
            verdict TEXT,
            target_id TEXT,
            baseline_leg TEXT,
            candidate_leg TEXT,
            rationale TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO experiments VALUES (?, ?, ?, ?)",
        ["reject-1", "reject-test", "terminal-bench@2.0", datetime.now(timezone.utc)],
    )
    conn.execute(
        "INSERT INTO legs VALUES (?, ?, ?, ?)",
        ["reject-1", "candidate", "bad-agent", "model-x"],
    )
    conn.execute(
        "INSERT INTO trials VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ["trial-1", "reject-1", "candidate", True, "model-x", 1.0, 100, 10.0],
    )
    conn.execute(
        "INSERT INTO experiment_evaluations VALUES (?, ?, ?, ?, ?, ?)",
        ["reject-1", "reject", "bad-agent", None, "candidate", "invalid runtime policy"],
    )

    row = ranking.rankings(conn)[0]

    assert row.agent_id == "bad-agent"
    assert row.eligible is False
    assert row.eligibility_reason == "rejected experiment target"


def test_rejected_conceptual_target_uses_candidate_leg_for_eligibility() -> None:
    conn = duckdb.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE experiments (
            instance_id TEXT,
            experiment_id TEXT,
            dataset TEXT,
            created_at TIMESTAMPTZ
        );
        CREATE TABLE legs (
            instance_id TEXT,
            leg_id TEXT,
            agent_id TEXT,
            model TEXT
        );
        CREATE TABLE trials (
            trial_id TEXT,
            instance_id TEXT,
            leg_id TEXT,
            passed BOOLEAN,
            model TEXT,
            cost_usd DOUBLE,
            total_tokens BIGINT,
            duration_sec DOUBLE
        );
        CREATE TABLE experiment_evaluations (
            instance_id TEXT,
            verdict TEXT,
            target_id TEXT,
            baseline_leg TEXT,
            candidate_leg TEXT,
            rationale TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO experiments VALUES (?, ?, ?, ?)",
        ["reject-2", "reject-test", "terminal-bench@2.0", datetime.now(timezone.utc)],
    )
    conn.execute(
        "INSERT INTO legs VALUES (?, ?, ?, ?), (?, ?, ?, ?), (?, ?, ?, ?)",
        [
            "reject-2", "basic_30_8192", "basic", "model-x",
            "reject-2", "basic_60_16384", "basic", "model-x",
            "reject-2", "basic_120_32768", "basic", "model-x",
        ],
    )
    conn.executemany(
        "INSERT INTO trials VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("a", "reject-2", "basic_30_8192", False, "model-x", 1.0, 100, 10.0),
            ("b", "reject-2", "basic_60_16384", True, "model-x", 2.0, 200, 20.0),
            ("c", "reject-2", "basic_120_32768", True, "model-x", 3.0, 300, 30.0),
        ],
    )
    conn.execute(
        "INSERT INTO experiment_evaluations VALUES (?, ?, ?, ?, ?, ?)",
        [
            "reject-2",
            "reject",
            "extended-budget-basic",
            "basic_30_8192",
            "basic_60_16384,basic_120_32768",
            "variants too inefficient",
        ],
    )

    rows = {row.leg_id: row for row in ranking.rankings(conn)}

    assert rows["basic_30_8192"].eligible is True
    assert rows["basic_60_16384"].eligible is False
    assert rows["basic_120_32768"].eligible is False


def test_best_by_model_picks_best_full_suite_across_datasets() -> None:
    conn = duckdb.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE experiments (
            instance_id TEXT,
            experiment_id TEXT,
            dataset TEXT,
            created_at TIMESTAMPTZ
        );
        CREATE TABLE legs (
            instance_id TEXT,
            leg_id TEXT,
            agent_id TEXT,
            model TEXT
        );
        CREATE TABLE trials (
            trial_id TEXT,
            instance_id TEXT,
            leg_id TEXT,
            passed BOOLEAN,
            model TEXT,
            cost_usd DOUBLE,
            total_tokens BIGINT,
            duration_sec DOUBLE
        );
        CREATE TABLE experiment_evaluations (
            instance_id TEXT,
            verdict TEXT,
            target_id TEXT,
            baseline_leg TEXT,
            candidate_leg TEXT,
            rationale TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO experiments VALUES (?, ?, ?, ?), (?, ?, ?, ?)",
        [
            "full-a",
            "tb2-a",
            "benchmark-a",
            datetime(2026, 4, 1, tzinfo=timezone.utc),
            "full-b",
            "tb2-b",
            "benchmark-b",
            datetime(2026, 4, 2, tzinfo=timezone.utc),
        ],
    )
    conn.execute(
        "INSERT INTO legs VALUES (?, ?, ?, ?), (?, ?, ?, ?)",
        [
            "full-a",
            "candidate",
            "agent-low",
            "model-x",
            "full-b",
            "candidate",
            "agent-high",
            "model-x",
        ],
    )
    trials = []
    for idx in range(80):
        trials.append((f"a-{idx}", "full-a", "candidate", idx < 40, "model-x", 1.0, 100, 10.0))
        trials.append((f"b-{idx}", "full-b", "candidate", idx < 60, "model-x", 1.0, 100, 10.0))
    conn.executemany("INSERT INTO trials VALUES (?, ?, ?, ?, ?, ?, ?, ?)", trials)
    conn.execute(
        "INSERT INTO experiment_evaluations VALUES (?, ?, ?, ?, ?, ?), (?, ?, ?, ?, ?, ?)",
        [
            "full-a",
            "accept",
            "agent-low",
            None,
            "candidate",
            "accepted",
            "full-b",
            "accept",
            "agent-high",
            None,
            "candidate",
            "accepted",
        ],
    )

    assert [r.agent_id for r in ranking.best_by_model(conn)] == ["agent-high"]
