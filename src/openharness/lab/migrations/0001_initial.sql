-- 0001_initial.sql — initial DuckDB schema for the lab pipeline.
--
-- Migrations are append-only files in `src/openharness/lab/migrations/`,
-- named `NNNN_<short_label>.sql`. The runner in `db.py` applies them in
-- lexical order and records each in `_lab_migrations`.
--
-- Conventions:
--   - Trials are the grain of evidence. `trials.trial_id` is unique
--     across all experiments because Harbor includes a per-instance
--     suffix (`<task>__<short_id>`).
--   - All `*_at` columns are TIMESTAMPTZ (DuckDB's TIMESTAMP WITH TIME
--     ZONE alias). Strings and JSON blobs are stored as TEXT/JSON.
--   - Cross-table references are intentional but not declared as foreign
--     keys: ingest is idempotent and may insert child rows before parent
--     rows during partial replays.

-- One row per concrete experiment instance (a single run dir under
-- runs/experiments/<id>/).
CREATE TABLE IF NOT EXISTS experiments (
    instance_id      TEXT PRIMARY KEY,
    experiment_id    TEXT NOT NULL,
    dataset          TEXT,
    spec_path        TEXT,
    resolved_spec    TEXT,
    git_sha          TEXT,
    git_dirty        BOOLEAN,
    hostname         TEXT,
    openharness_ver  TEXT,
    harbor_ver       TEXT,
    python_ver       TEXT,
    created_at       TIMESTAMPTZ,
    updated_at       TIMESTAMPTZ,
    summary_path     TEXT,
    run_dir          TEXT NOT NULL,
    ingested_at      TIMESTAMPTZ NOT NULL
);

-- One row per (instance, leg). A leg is a single agent configuration
-- against the experiment's dataset.
CREATE TABLE IF NOT EXISTS legs (
    instance_id           TEXT NOT NULL,
    leg_id                TEXT NOT NULL,
    agent_id              TEXT NOT NULL,
    agent_architecture    TEXT,
    model                 TEXT,
    max_turns             INTEGER,
    max_tokens            INTEGER,
    components_active     JSON,
    agent_resolved_yaml   TEXT,
    agent_config_hash     TEXT,
    status                TEXT,
    result_status         TEXT,
    started_at            TIMESTAMPTZ,
    finished_at           TIMESTAMPTZ,
    duration_sec          DOUBLE,
    PRIMARY KEY (instance_id, leg_id)
);

-- One row per trial (task × leg × instance).
CREATE TABLE IF NOT EXISTS trials (
    trial_id              TEXT PRIMARY KEY,
    instance_id           TEXT NOT NULL,
    leg_id                TEXT NOT NULL,
    task_name             TEXT NOT NULL,
    task_checksum         TEXT,
    task_git_url          TEXT,
    task_git_commit       TEXT,
    task_path             TEXT,
    score                 DOUBLE,
    passed                BOOLEAN,
    status                TEXT,
    error_type            TEXT,
    error_phase           TEXT,
    error_message         TEXT,
    model                 TEXT,
    input_tokens          BIGINT,
    output_tokens         BIGINT,
    cache_tokens          BIGINT,
    total_tokens          BIGINT,
    cost_usd              DOUBLE,
    duration_sec          DOUBLE,
    agent_duration_sec    DOUBLE,
    env_setup_duration_sec DOUBLE,
    verifier_duration_sec DOUBLE,
    n_turns               INTEGER,
    n_tool_calls          INTEGER,
    components_active     JSON,
    trace_id              TEXT,
    trace_url             TEXT,
    trial_dir             TEXT NOT NULL,
    started_at            TIMESTAMPTZ,
    finished_at           TIMESTAMPTZ,
    final_text            TEXT
);

CREATE INDEX IF NOT EXISTS trials_by_instance ON trials (instance_id);
CREATE INDEX IF NOT EXISTS trials_by_leg ON trials (instance_id, leg_id);
CREATE INDEX IF NOT EXISTS trials_by_task ON trials (task_name);
CREATE INDEX IF NOT EXISTS trials_by_checksum ON trials (task_checksum);

-- Per-task semantic features extracted once per task_checksum by the
-- `task-features` skill. Cached forever.
CREATE TABLE IF NOT EXISTS task_features (
    task_checksum   TEXT PRIMARY KEY,
    task_name       TEXT,
    category        TEXT,
    required_tools  JSON,
    env_complexity  TEXT,
    output_shape    TEXT,
    keywords        JSON,
    extra           JSON,
    extracted_by    TEXT,
    extracted_at    TIMESTAMPTZ NOT NULL
);

-- Per-trial agent critique produced by the `trial-critic` skill.
CREATE TABLE IF NOT EXISTS trial_critiques (
    trial_id                 TEXT PRIMARY KEY,
    schema_version           INTEGER NOT NULL,
    task_summary             TEXT,
    agent_strategy           TEXT,
    key_actions              JSON,
    outcome                  TEXT,
    root_cause               TEXT,
    success_factor           TEXT,
    anti_patterns            JSON,
    components_active        JSON,
    task_features            JSON,
    surprising_observations  JSON,
    confidence               DOUBLE,
    critic_model             TEXT,
    extra                    JSON,
    created_at               TIMESTAMPTZ NOT NULL
);

-- Per-task cross-leg comparisons produced by `experiment-critic`.
CREATE TABLE IF NOT EXISTS comparisons (
    instance_id      TEXT NOT NULL,
    task_name        TEXT NOT NULL,
    winning_leg      TEXT,
    runner_up_leg    TEXT,
    delta_score      DOUBLE,
    why              TEXT,
    evidence         JSON,
    legs_compared    JSON,
    critic_model     TEXT,
    created_at       TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (instance_id, task_name)
);

-- Aggregated per-component performance across experiments × task
-- clusters, maintained by `cross-experiment-critic`.
CREATE TABLE IF NOT EXISTS components_perf (
    component_id            TEXT NOT NULL,
    task_cluster            TEXT NOT NULL,
    n_trials                INTEGER NOT NULL,
    win_rate                DOUBLE,
    cost_delta_pct          DOUBLE,
    supporting_experiments  JSON,
    notes                   TEXT,
    updated_at              TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (component_id, task_cluster)
);

-- Post-hoc misconfiguration findings (Phase 3b).
CREATE TABLE IF NOT EXISTS misconfigurations (
    trial_id        TEXT NOT NULL,
    component_id    TEXT NOT NULL,
    kind            TEXT NOT NULL,
    detail          JSON,
    created_at      TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (trial_id, component_id, kind)
);

-- Codex CLI spawn audit log written by `src/openharness/lab/codex.py`.
CREATE TABLE IF NOT EXISTS spawns (
    spawn_id          TEXT PRIMARY KEY,
    skill             TEXT NOT NULL,
    args              JSON,
    cwd               TEXT,
    log_path          TEXT,
    started_at        TIMESTAMPTZ NOT NULL,
    finished_at       TIMESTAMPTZ,
    exit_code         INTEGER,
    cost_usd_estimate DOUBLE,
    parent_run_dir    TEXT,
    notes             TEXT
);

CREATE INDEX IF NOT EXISTS spawns_by_skill ON spawns (skill);
CREATE INDEX IF NOT EXISTS spawns_by_started ON spawns (started_at);
