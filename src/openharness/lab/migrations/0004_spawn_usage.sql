-- 0004_spawn_usage.sql — cache provider/model/token usage for model skill spawns.
--
-- `spawns` is the durable audit table for lab pipeline model calls
-- (Codex design/implement/critique/replan/finalize, Gemini
-- trial-critic, task-features, etc.). Trial-agent usage already lives
-- in `trials`; these columns make pipeline usage queryable without
-- reparsing JSONL logs on every web request.

ALTER TABLE spawns ADD COLUMN IF NOT EXISTS provider TEXT;
ALTER TABLE spawns ADD COLUMN IF NOT EXISTS model TEXT;
ALTER TABLE spawns ADD COLUMN IF NOT EXISTS input_tokens BIGINT;
ALTER TABLE spawns ADD COLUMN IF NOT EXISTS cached_input_tokens BIGINT;
ALTER TABLE spawns ADD COLUMN IF NOT EXISTS output_tokens BIGINT;
ALTER TABLE spawns ADD COLUMN IF NOT EXISTS reasoning_output_tokens BIGINT;
ALTER TABLE spawns ADD COLUMN IF NOT EXISTS total_tokens BIGINT;
ALTER TABLE spawns ADD COLUMN IF NOT EXISTS duration_sec DOUBLE;
ALTER TABLE spawns ADD COLUMN IF NOT EXISTS effective_settings JSON;
ALTER TABLE spawns ADD COLUMN IF NOT EXISTS last_message TEXT;

CREATE INDEX IF NOT EXISTS spawns_by_provider_model ON spawns (provider, model);
