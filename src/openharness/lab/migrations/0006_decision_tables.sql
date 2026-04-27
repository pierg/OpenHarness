-- 0006_decision_tables.sql — convert lab decision caches to current names.
--
-- The markdown journal and critic JSON files remain the source of truth.
-- These tables are query caches with current terminology only.

CREATE TABLE IF NOT EXISTS decisions (
    instance_id          TEXT PRIMARY KEY,
    slug                 TEXT NOT NULL,
    verdict              TEXT NOT NULL,
    target_id            TEXT NOT NULL,
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

INSERT OR REPLACE INTO decisions (
    instance_id, slug, verdict, target_id, rationale, confidence,
    evidence_paths, applied, applied_by, applied_at, promotability_notes,
    pr_url, branch_sha
)
SELECT
    instance_id,
    slug,
    CASE
        WHEN coalesce(decision, kind) IN ('accept', 'reject', 'no_op') THEN coalesce(decision, kind)
        WHEN kind IN ('graduate', 'add_branch') THEN 'accept'
        ELSE 'no_op'
    END AS verdict,
    target_id,
    rationale,
    confidence,
    evidence_paths,
    applied,
    applied_by,
    applied_at,
    promotability_notes,
    pr_url,
    branch_sha
FROM tree_diffs;

CREATE INDEX IF NOT EXISTS decisions_by_verdict ON decisions (verdict);
CREATE INDEX IF NOT EXISTS decisions_by_target ON decisions (target_id);
CREATE INDEX IF NOT EXISTS decisions_pr_url ON decisions (verdict, pr_url);

CREATE TABLE IF NOT EXISTS current_best_changes (
    at_ts        TIMESTAMPTZ NOT NULL,
    from_id      TEXT,
    to_id        TEXT NOT NULL,
    reason       TEXT,
    applied_by   TEXT NOT NULL,
    instance_id  TEXT,
    PRIMARY KEY (at_ts, to_id)
);

INSERT OR REPLACE INTO current_best_changes (
    at_ts, from_id, to_id, reason, applied_by, instance_id
)
SELECT at_ts, from_id, to_id, reason, applied_by, instance_id
FROM trunk_changes;

CREATE INDEX IF NOT EXISTS current_best_changes_by_to ON current_best_changes (to_id);

DROP TABLE tree_diffs;
DROP TABLE trunk_changes;
