-- 0003_pr_url.sql — record the PR URL and the discarded-branch SHA on tree_diffs.
--
-- Background. Every experiment that produces a positive verdict
-- (`add_branch` or `graduate`) opens a PR via `lab-finalize-pr` and
-- the PR URL is stored in the journal markdown's `**Branch:**`
-- bullet. That's the human-readable source of truth, but the web UI
-- and downstream queries want it in the DB cache too — so we don't
-- have to scrape markdown to render "show me every open lab PR" or
-- "is the AddBranch from experiment X already merged".
--
-- Background — discarded branches. For `reject` / `no_op` verdicts
-- the worktree is removed and the branch deleted, so the audit
-- trail otherwise loses the last commit SHA. We capture it here so
-- a curious human can fetch it back later (`git fetch origin
-- <sha>:retro/<slug>`).
--
-- Both columns are nullable because:
--   - tree_diffs rows are created BEFORE the PR exists (the daemon's
--     critique phase writes them; finalize fills these in later).
--   - reject/no_op rows naturally have no pr_url; add_branch/graduate
--     rows naturally have no branch_sha (the branch survives on the
--     remote until the PR is merged).

ALTER TABLE tree_diffs ADD COLUMN IF NOT EXISTS pr_url TEXT;
ALTER TABLE tree_diffs ADD COLUMN IF NOT EXISTS branch_sha TEXT;

-- Allow the web UI to filter on PR-bearing rows cheaply. DuckDB does
-- not support partial indexes, so we index on (kind, pr_url) and let
-- the query planner do the rest.
CREATE INDEX IF NOT EXISTS tree_diffs_pr_url ON tree_diffs (kind, pr_url);
