-- Todo tool telemetry: dedicated tool category, feature flag, and quality-gate
-- counters (ownership, hill-climbability, completion-confidence, confidence
-- spike). The `events` table sits at D1's 100-column cap, so all new columns
-- live in the detail tables like migration 0013's fields.

ALTER TABLE session_details ADD COLUMN tool_cat_todo INTEGER DEFAULT 0;
ALTER TABLE session_details ADD COLUMN feature_todo_used INTEGER DEFAULT 0;
ALTER TABLE session_details ADD COLUMN todo_gate_ownership_count INTEGER DEFAULT 0;
ALTER TABLE session_details ADD COLUMN todo_gate_hill_count INTEGER DEFAULT 0;
ALTER TABLE session_details ADD COLUMN todo_gate_completion_count INTEGER DEFAULT 0;
ALTER TABLE session_details ADD COLUMN todo_gate_spike_count INTEGER DEFAULT 0;

ALTER TABLE turn_details ADD COLUMN tool_cat_todo INTEGER DEFAULT 0;
ALTER TABLE turn_details ADD COLUMN feature_todo_used INTEGER DEFAULT 0;
ALTER TABLE turn_details ADD COLUMN todo_gate_ownership_count INTEGER DEFAULT 0;
ALTER TABLE turn_details ADD COLUMN todo_gate_hill_count INTEGER DEFAULT 0;
ALTER TABLE turn_details ADD COLUMN todo_gate_completion_count INTEGER DEFAULT 0;
ALTER TABLE turn_details ADD COLUMN todo_gate_spike_count INTEGER DEFAULT 0;
