-- Safe to run against an already-migrated database: duplicate-column errors
-- indicate the column is already present.

ALTER TABLE events ADD COLUMN duration_secs INTEGER;
ALTER TABLE events ADD COLUMN first_assistant_response_ms INTEGER;
ALTER TABLE events ADD COLUMN first_tool_call_ms INTEGER;
ALTER TABLE events ADD COLUMN first_tool_success_ms INTEGER;
ALTER TABLE events ADD COLUMN executed_tool_calls INTEGER DEFAULT 0;
ALTER TABLE events ADD COLUMN executed_tool_successes INTEGER DEFAULT 0;
ALTER TABLE events ADD COLUMN executed_tool_failures INTEGER DEFAULT 0;
ALTER TABLE events ADD COLUMN tool_latency_total_ms INTEGER DEFAULT 0;
ALTER TABLE events ADD COLUMN tool_latency_max_ms INTEGER DEFAULT 0;
ALTER TABLE events ADD COLUMN file_write_calls INTEGER DEFAULT 0;
ALTER TABLE events ADD COLUMN tests_run INTEGER DEFAULT 0;
ALTER TABLE events ADD COLUMN tests_passed INTEGER DEFAULT 0;
ALTER TABLE events ADD COLUMN feature_memory_used INTEGER DEFAULT 0;
ALTER TABLE events ADD COLUMN feature_swarm_used INTEGER DEFAULT 0;
ALTER TABLE events ADD COLUMN feature_web_used INTEGER DEFAULT 0;
ALTER TABLE events ADD COLUMN feature_email_used INTEGER DEFAULT 0;
ALTER TABLE events ADD COLUMN feature_mcp_used INTEGER DEFAULT 0;
ALTER TABLE events ADD COLUMN feature_side_panel_used INTEGER DEFAULT 0;
ALTER TABLE events ADD COLUMN feature_goal_used INTEGER DEFAULT 0;
ALTER TABLE events ADD COLUMN feature_selfdev_used INTEGER DEFAULT 0;
ALTER TABLE events ADD COLUMN feature_background_used INTEGER DEFAULT 0;
ALTER TABLE events ADD COLUMN feature_subagent_used INTEGER DEFAULT 0;
ALTER TABLE events ADD COLUMN unique_mcp_servers INTEGER DEFAULT 0;
ALTER TABLE events ADD COLUMN session_success INTEGER DEFAULT 0;
ALTER TABLE events ADD COLUMN abandoned_before_response INTEGER DEFAULT 0;
ALTER TABLE events ADD COLUMN auth_provider TEXT;
ALTER TABLE events ADD COLUMN auth_method TEXT;
ALTER TABLE events ADD COLUMN from_version TEXT;
