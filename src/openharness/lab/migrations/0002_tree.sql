-- 0002_tree.sql — historical schema for lab decisions.
--
-- `lab/configs.md` is the persistent state for the current best,
-- rejected, and proposed agent configs. `lab/components.md` is the
-- catalog of building-block atoms. `lab/experiments.md` is the linear
-- log of dated events. Each experiment now produces an experiment
-- decision (accept / reject / no_op); historical rows may still carry
-- older decision labels.
--
-- The two tables here are DERIVED CACHES of those files (the markdown
-- and the per-experiment critic JSONs are the source of truth) so we
-- can answer "what decisions have ever been applied?" with a SQL
-- query without re-parsing markdown. Rebuilt by
-- `uv run lab ingest-critiques`.

-- Audit log of current-best changes. Append-only.
CREATE TABLE IF NOT EXISTS trunk_changes (
    at_ts        TIMESTAMPTZ NOT NULL,
    from_id      TEXT,                 -- nullable for the very first swap
    to_id        TEXT NOT NULL,
    reason       TEXT,
    applied_by   TEXT NOT NULL,
    instance_id  TEXT,                 -- the experiment that justified the swap (if any)
    PRIMARY KEY (at_ts, to_id)
);

CREATE INDEX IF NOT EXISTS trunk_changes_by_to ON trunk_changes (to_id);

-- One row per `### Tree effect` block in the journal.
CREATE TABLE IF NOT EXISTS tree_diffs (
    instance_id     TEXT PRIMARY KEY,  -- one diff per experiment instance
    slug            TEXT NOT NULL,     -- the journal entry slug
    kind            TEXT NOT NULL,     -- accept | reject | no_op (or historical labels)
    target_id       TEXT NOT NULL,     -- component / agent id this diff is about
    rationale       TEXT,
    use_when        JSON,              -- historical scoped-accept metadata only
    confidence      DOUBLE,
    evidence_paths  JSON,
    applied         BOOLEAN NOT NULL DEFAULT FALSE,
    applied_by      TEXT,              -- 'auto:daemon' | 'human:<user>' | 'proposed'
    applied_at      TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS tree_diffs_by_kind ON tree_diffs (kind);
CREATE INDEX IF NOT EXISTS tree_diffs_by_target ON tree_diffs (target_id);
