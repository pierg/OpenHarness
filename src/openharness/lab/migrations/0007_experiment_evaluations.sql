-- 0007_experiment_evaluations.sql — separate per-experiment evaluation from ranking.
--
-- `experiment_evaluations` records what the critic/finalize workflow decided
-- about one experiment PR. It intentionally does not mean "global best".
-- Leaderboards are derived dynamically from experiments/legs/trials plus this
-- table, grouped by comparable dimensions such as model id and dataset.

CREATE TABLE IF NOT EXISTS experiment_evaluations (
    instance_id          TEXT PRIMARY KEY,
    slug                 TEXT NOT NULL,
    verdict              TEXT NOT NULL,       -- accept | reject | no_op
    target_id            TEXT NOT NULL,       -- evaluated agent/config id
    baseline_leg         TEXT,
    candidate_leg        TEXT,
    rationale            TEXT,
    confidence           DOUBLE,
    evidence_paths       JSON,
    applied              BOOLEAN NOT NULL DEFAULT FALSE,
    applied_by           TEXT,
    applied_at           TIMESTAMPTZ,
    promotability_notes  TEXT,
    pr_url               TEXT,
    branch_sha           TEXT
);

INSERT OR REPLACE INTO experiment_evaluations (
    instance_id, slug, verdict, target_id, baseline_leg, candidate_leg, rationale, confidence,
    evidence_paths, applied, applied_by, applied_at, promotability_notes,
    pr_url, branch_sha
)
SELECT
    instance_id, slug, verdict, target_id, NULL, NULL, rationale, confidence,
    evidence_paths, applied, applied_by, applied_at, promotability_notes,
    pr_url, branch_sha
FROM decisions;

CREATE INDEX IF NOT EXISTS experiment_evaluations_by_verdict
    ON experiment_evaluations (verdict);
CREATE INDEX IF NOT EXISTS experiment_evaluations_by_target
    ON experiment_evaluations (target_id);
CREATE INDEX IF NOT EXISTS experiment_evaluations_pr_url
    ON experiment_evaluations (verdict, pr_url);

DROP TABLE IF EXISTS decisions;
DROP TABLE IF EXISTS current_best_changes;
