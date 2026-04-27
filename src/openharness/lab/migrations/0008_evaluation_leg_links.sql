-- 0008_evaluation_leg_links.sql — keep evaluation targets separate from legs.
--
-- `target_id` is the human-facing agent/config/component under evaluation. It
-- can be conceptual (`extended-budget-basic`) and therefore not equal to any
-- concrete leg id. These optional columns let ranking and delta views identify
-- the measured baseline/candidate legs without overloading `target_id`.

ALTER TABLE experiment_evaluations
    ADD COLUMN IF NOT EXISTS baseline_leg TEXT;

ALTER TABLE experiment_evaluations
    ADD COLUMN IF NOT EXISTS candidate_leg TEXT;
