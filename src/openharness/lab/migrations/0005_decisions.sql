-- 0005_decisions.sql — simplify tree_diffs into experiment decisions.
--
-- Keep the historical table name for compatibility, but store the new
-- three-label decision surface explicitly. `kind` remains populated so
-- older queries keep working; `decision` is the preferred column.

ALTER TABLE tree_diffs ADD COLUMN IF NOT EXISTS decision TEXT;
ALTER TABLE tree_diffs ADD COLUMN IF NOT EXISTS promotability_notes TEXT;

UPDATE tree_diffs
   SET decision = CASE
       WHEN kind IN ('graduate', 'add_branch') THEN 'accept'
       WHEN kind = 'reject' THEN 'reject'
       ELSE 'no_op'
   END
 WHERE decision IS NULL;

CREATE INDEX IF NOT EXISTS tree_diffs_by_decision ON tree_diffs (decision);
