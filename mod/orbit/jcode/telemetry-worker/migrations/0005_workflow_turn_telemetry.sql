-- Workflow cadence and per-turn telemetry expansion.
-- Safe to re-run against a partially migrated database: duplicate-column errors
-- indicate the column already exists.

ALTER TABLE events ADD COLUMN session_start_hour_utc INTEGER;
ALTER TABLE events ADD COLUMN session_start_weekday_utc INTEGER;
ALTER TABLE events ADD COLUMN session_end_hour_utc INTEGER;
ALTER TABLE events ADD COLUMN session_end_weekday_utc INTEGER;
ALTER TABLE events ADD COLUMN previous_session_gap_secs INTEGER;
ALTER TABLE events ADD COLUMN sessions_started_24h INTEGER DEFAULT 0;
ALTER TABLE events ADD COLUMN sessions_started_7d INTEGER DEFAULT 0;
ALTER TABLE events ADD COLUMN active_sessions_at_start INTEGER DEFAULT 0;
ALTER TABLE events ADD COLUMN other_active_sessions_at_start INTEGER DEFAULT 0;
ALTER TABLE events ADD COLUMN max_concurrent_sessions INTEGER DEFAULT 0;
ALTER TABLE events ADD COLUMN multi_sessioned INTEGER DEFAULT 0;
ALTER TABLE events ADD COLUMN turn_index INTEGER;
ALTER TABLE events ADD COLUMN turn_started_ms INTEGER;
ALTER TABLE events ADD COLUMN turn_active_duration_ms INTEGER;
ALTER TABLE events ADD COLUMN idle_before_turn_ms INTEGER;
ALTER TABLE events ADD COLUMN idle_after_turn_ms INTEGER;
ALTER TABLE events ADD COLUMN turn_success INTEGER DEFAULT 0;
ALTER TABLE events ADD COLUMN turn_abandoned INTEGER DEFAULT 0;
ALTER TABLE events ADD COLUMN turn_end_reason TEXT;

CREATE INDEX IF NOT EXISTS idx_events_turn_index ON events(turn_index);
CREATE INDEX IF NOT EXISTS idx_events_session_start_hour_utc ON events(session_start_hour_utc);
CREATE INDEX IF NOT EXISTS idx_events_multi_sessioned ON events(multi_sessioned);

CREATE TABLE IF NOT EXISTS turn_details (
    event_id TEXT PRIMARY KEY,
    assistant_responses INTEGER DEFAULT 0,
    first_assistant_response_ms INTEGER,
    first_tool_call_ms INTEGER,
    first_tool_success_ms INTEGER,
    first_file_edit_ms INTEGER,
    first_test_pass_ms INTEGER,
    tool_calls INTEGER DEFAULT 0,
    tool_failures INTEGER DEFAULT 0,
    executed_tool_calls INTEGER DEFAULT 0,
    executed_tool_successes INTEGER DEFAULT 0,
    executed_tool_failures INTEGER DEFAULT 0,
    tool_latency_total_ms INTEGER DEFAULT 0,
    tool_latency_max_ms INTEGER DEFAULT 0,
    file_write_calls INTEGER DEFAULT 0,
    tests_run INTEGER DEFAULT 0,
    tests_passed INTEGER DEFAULT 0,
    feature_memory_used INTEGER DEFAULT 0,
    feature_swarm_used INTEGER DEFAULT 0,
    feature_web_used INTEGER DEFAULT 0,
    feature_email_used INTEGER DEFAULT 0,
    feature_mcp_used INTEGER DEFAULT 0,
    feature_side_panel_used INTEGER DEFAULT 0,
    feature_goal_used INTEGER DEFAULT 0,
    feature_selfdev_used INTEGER DEFAULT 0,
    feature_background_used INTEGER DEFAULT 0,
    feature_subagent_used INTEGER DEFAULT 0,
    unique_mcp_servers INTEGER DEFAULT 0,
    tool_cat_read_search INTEGER DEFAULT 0,
    tool_cat_write INTEGER DEFAULT 0,
    tool_cat_shell INTEGER DEFAULT 0,
    tool_cat_web INTEGER DEFAULT 0,
    tool_cat_memory INTEGER DEFAULT 0,
    tool_cat_subagent INTEGER DEFAULT 0,
    tool_cat_swarm INTEGER DEFAULT 0,
    tool_cat_email INTEGER DEFAULT 0,
    tool_cat_side_panel INTEGER DEFAULT 0,
    tool_cat_goal INTEGER DEFAULT 0,
    tool_cat_mcp INTEGER DEFAULT 0,
    tool_cat_other INTEGER DEFAULT 0,
    workflow_chat_only INTEGER DEFAULT 0,
    workflow_coding_used INTEGER DEFAULT 0,
    workflow_research_used INTEGER DEFAULT 0,
    workflow_tests_used INTEGER DEFAULT 0,
    workflow_background_used INTEGER DEFAULT 0,
    workflow_subagent_used INTEGER DEFAULT 0,
    workflow_swarm_used INTEGER DEFAULT 0,
    FOREIGN KEY (event_id) REFERENCES events(event_id)
);
