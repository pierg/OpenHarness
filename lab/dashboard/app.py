"""Streamlit dashboard for the lab pipeline (read-only).

Phase 0 skeleton: one page with leg-level pass/cost overview, a per-task
heatmap across legs, and a top-failures list. The dashboard opens the
DuckDB file in read-only mode so the orchestrator's writer connection
is never blocked.

Run with: `uv run lab dashboard` (or
`uv run streamlit run lab/dashboard/app.py`).
"""

from __future__ import annotations

import streamlit as st

from openharness.lab import db as labdb
from openharness.lab.paths import LAB_DB_PATH

st.set_page_config(
    page_title="OpenHarness lab",
    page_icon="🧪",
    layout="wide",
)

st.title("🧪 OpenHarness lab")
st.caption(f"Read-only view of `{LAB_DB_PATH}`")

if not LAB_DB_PATH.exists():
    st.warning(
        "No lab DB yet. Run `uv run lab init` and `uv run lab ingest "
        "runs/experiments/<id>` to populate it."
    )
    st.stop()


@st.cache_data(ttl=15)
def load_legs() -> "pd.DataFrame":  # type: ignore[name-defined]
    with labdb.reader() as conn:
        return conn.execute(
            """
            SELECT
                t.instance_id,
                t.leg_id,
                MAX(l.agent_id)            AS agent_id,
                MAX(l.model)               AS model,
                COUNT(*)                   AS n_trials,
                SUM(CAST(t.passed AS INT)) AS n_passed,
                SUM(CASE WHEN t.status = 'errored' THEN 1 ELSE 0 END) AS n_errored,
                ROUND(100.0 * AVG(CAST(t.passed AS DOUBLE)), 2) AS pass_rate_pct,
                ROUND(SUM(t.cost_usd), 2)  AS cost_usd,
                ROUND(SUM(t.total_tokens) / 1e6, 2) AS tokens_m,
                ROUND(MEDIAN(t.duration_sec), 1)    AS median_dur_s
            FROM trials t
            LEFT JOIN legs l USING (instance_id, leg_id)
            GROUP BY t.instance_id, t.leg_id
            ORDER BY t.instance_id, pass_rate_pct DESC
            """
        ).fetchdf()


@st.cache_data(ttl=15)
def load_per_task(instance_id: str) -> "pd.DataFrame":  # type: ignore[name-defined]
    with labdb.reader() as conn:
        return conn.execute(
            """
            SELECT task_name, leg_id, passed, score, cost_usd, duration_sec
            FROM trials
            WHERE instance_id = ?
            ORDER BY task_name, leg_id
            """,
            [instance_id],
        ).fetchdf()


@st.cache_data(ttl=15)
def load_top_failures(instance_id: str, limit: int = 30) -> "pd.DataFrame":  # type: ignore[name-defined]
    with labdb.reader() as conn:
        return conn.execute(
            """
            SELECT
                task_name,
                COUNT(*)                                AS n_trials,
                SUM(CAST(passed AS INT))                AS n_passed,
                SUM(CASE WHEN status='errored' THEN 1 ELSE 0 END) AS n_errored,
                ROUND(100.0 * AVG(CAST(passed AS DOUBLE)), 1) AS pass_rate_pct
            FROM trials
            WHERE instance_id = ?
            GROUP BY task_name
            HAVING SUM(CAST(passed AS INT)) = 0
            ORDER BY n_errored DESC, task_name
            LIMIT ?
            """,
            [instance_id, limit],
        ).fetchdf()


legs = load_legs()
if legs.empty:
    st.info("DB is empty. Run `uv run lab ingest runs/experiments/<id>`.")
    st.stop()

instances = sorted(legs["instance_id"].unique().tolist())
instance_id = st.sidebar.selectbox("Experiment instance", instances, index=len(instances) - 1)

st.subheader("Legs")
st.dataframe(
    legs[legs["instance_id"] == instance_id].drop(columns=["instance_id"]),
    use_container_width=True,
    hide_index=True,
)

st.subheader("Per-task pass matrix")
per_task = load_per_task(instance_id)
if per_task.empty:
    st.info("No trials for this instance yet.")
else:
    pivot = per_task.pivot_table(
        index="task_name", columns="leg_id", values="passed", aggfunc="max"
    ).fillna(False)
    pivot = pivot.astype(bool)
    st.dataframe(pivot, use_container_width=True)

st.subheader("Top failing tasks (0 passes across legs)")
fails = load_top_failures(instance_id)
if fails.empty:
    st.success("No tasks failed across all legs in this instance.")
else:
    st.dataframe(fails, use_container_width=True, hide_index=True)

st.divider()
st.caption(
    "DB opened read-only. Use `uv run lab` to mutate; this dashboard never writes."
)
