-- 0002_tree.sql — schema for the tree-shaped lab.
--
-- The configuration tree (trunk + branches + rejected, in
-- `lab/configs.md`) is the persistent state for which agent configs
-- are canonical. `lab/components.md` is the catalog of building-block
-- atoms. `lab/experiments.md` is the linear log of dated events.
-- Each experiment produces one TreeDiff (graduate / add_branch /
-- reject / no_op) which the daemon either auto-applies (branch /
-- reject / no_op) or stages for human confirmation (graduate).
--
-- The two tables here are DERIVED CACHES of those files (the markdown
-- and the per-experiment critic JSONs are the source of truth) so we
-- can answer "what's the current trunk?", "what tree diffs have ever
-- been applied?", and "show me trunk swaps over time" with a SQL
-- query without re-parsing markdown. Rebuilt by
-- `uv run lab ingest-critiques`.

-- Audit log of trunk swaps. Append-only.
CREATE TABLE IF NOT EXISTS trunk_changes (
    at_ts        TIMESTAMPTZ NOT NULL,
    from_id      TEXT,                 -- nullable for the very first swap
    to_id        TEXT NOT NULL,
    reason       TEXT,
    applied_by   TEXT NOT NULL,        -- 'human:<user>' | 'graduate-confirm:<slug>'
    instance_id  TEXT,                 -- the experiment that justified the swap (if any)
    PRIMARY KEY (at_ts, to_id)
);

CREATE INDEX IF NOT EXISTS trunk_changes_by_to ON trunk_changes (to_id);

-- One row per `### Tree effect` block in the journal. Updated by
-- `tree apply` writing to disk and `ingest-critiques` mirroring it.
CREATE TABLE IF NOT EXISTS tree_diffs (
    instance_id     TEXT PRIMARY KEY,  -- one diff per experiment instance
    slug            TEXT NOT NULL,     -- the journal entry slug
    kind            TEXT NOT NULL,     -- graduate | add_branch | reject | no_op
    target_id       TEXT NOT NULL,     -- component / agent id this diff is about
    rationale       TEXT,
    use_when        JSON,              -- only for add_branch
    confidence      DOUBLE,            -- only for no_op (drives reflect-and-plan)
    evidence_paths  JSON,
    applied         BOOLEAN NOT NULL DEFAULT FALSE,
    applied_by      TEXT,              -- 'auto:daemon' | 'human:<user>' | 'proposed'
    applied_at      TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS tree_diffs_by_kind ON tree_diffs (kind);
CREATE INDEX IF NOT EXISTS tree_diffs_by_target ON tree_diffs (target_id);
