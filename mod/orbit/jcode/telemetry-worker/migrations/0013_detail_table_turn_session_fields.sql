-- Move schema-v5 per-turn and session-cadence fields into the detail tables.
--
-- Production D1 `events` is at 96 columns and D1 caps tables at 100 columns,
-- so migration 0005's ALTER TABLE events additions (turn_index, turn timings,
-- turn_success, session hour/cadence fields, ...) can never apply there:
-- `too many columns on sqlite_altertab_events`. The worker filters inserts by
-- the live column set, so these client-sent fields were silently dropped.
-- The detail tables have plenty of headroom, so record the fields there.

ALTER TABLE turn_details ADD COLUMN turn_index INTEGER;
ALTER TABLE turn_details ADD COLUMN turn_started_ms INTEGER;
ALTER TABLE turn_details ADD COLUMN turn_active_duration_ms INTEGER;
ALTER TABLE turn_details ADD COLUMN idle_before_turn_ms INTEGER;
ALTER TABLE turn_details ADD COLUMN idle_after_turn_ms INTEGER;
ALTER TABLE turn_details ADD COLUMN turn_success INTEGER DEFAULT 0;
ALTER TABLE turn_details ADD COLUMN turn_abandoned INTEGER DEFAULT 0;
ALTER TABLE turn_details ADD COLUMN turn_end_reason TEXT;
ALTER TABLE turn_details ADD COLUMN input_tokens INTEGER DEFAULT 0;
ALTER TABLE turn_details ADD COLUMN output_tokens INTEGER DEFAULT 0;
ALTER TABLE turn_details ADD COLUMN total_tokens INTEGER DEFAULT 0;

ALTER TABLE session_details ADD COLUMN session_start_hour_utc INTEGER;
ALTER TABLE session_details ADD COLUMN session_start_weekday_utc INTEGER;
ALTER TABLE session_details ADD COLUMN session_end_hour_utc INTEGER;
ALTER TABLE session_details ADD COLUMN session_end_weekday_utc INTEGER;
ALTER TABLE session_details ADD COLUMN previous_session_gap_secs INTEGER;
ALTER TABLE session_details ADD COLUMN sessions_started_24h INTEGER DEFAULT 0;
ALTER TABLE session_details ADD COLUMN sessions_started_7d INTEGER DEFAULT 0;
ALTER TABLE session_details ADD COLUMN active_sessions_at_start INTEGER DEFAULT 0;
ALTER TABLE session_details ADD COLUMN other_active_sessions_at_start INTEGER DEFAULT 0;
ALTER TABLE session_details ADD COLUMN max_concurrent_sessions INTEGER DEFAULT 0;
ALTER TABLE session_details ADD COLUMN multi_sessioned INTEGER DEFAULT 0;
