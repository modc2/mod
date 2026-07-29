-- Phase 1/2/3 telemetry enrichment using a split schema.
-- Keep the core `events` table compact enough for D1, and store the
-- wider Phase 2/3 per-session analytics in `session_details` keyed by event_id.
-- Safe to run against an already-migrated database: duplicate-column errors
-- indicate the column is already present.

ALTER TABLE events ADD COLUMN event_id TEXT;
ALTER TABLE events ADD COLUMN session_id TEXT;
ALTER TABLE events ADD COLUMN schema_version INTEGER DEFAULT 1;
ALTER TABLE events ADD COLUMN build_channel TEXT;
ALTER TABLE events ADD COLUMN is_git_checkout INTEGER DEFAULT 0;
ALTER TABLE events ADD COLUMN is_ci INTEGER DEFAULT 0;
ALTER TABLE events ADD COLUMN ran_from_cargo INTEGER DEFAULT 0;
ALTER TABLE events ADD COLUMN step TEXT;
ALTER TABLE events ADD COLUMN milestone_elapsed_ms INTEGER;
ALTER TABLE events ADD COLUMN feedback_rating TEXT;
ALTER TABLE events ADD COLUMN feedback_reason TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_events_event_id ON events(event_id);
CREATE INDEX IF NOT EXISTS idx_events_session_id ON events(session_id);
CREATE INDEX IF NOT EXISTS idx_events_step ON events(step);
CREATE INDEX IF NOT EXISTS idx_events_feedback_rating ON events(feedback_rating);

CREATE TABLE IF NOT EXISTS session_details (
    event_id TEXT PRIMARY KEY,
    first_file_edit_ms INTEGER,
    first_test_pass_ms INTEGER,
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
    command_login_used INTEGER DEFAULT 0,
    command_model_used INTEGER DEFAULT 0,
    command_usage_used INTEGER DEFAULT 0,
    command_resume_used INTEGER DEFAULT 0,
    command_memory_used INTEGER DEFAULT 0,
    command_swarm_used INTEGER DEFAULT 0,
    command_goal_used INTEGER DEFAULT 0,
    command_selfdev_used INTEGER DEFAULT 0,
    command_feedback_used INTEGER DEFAULT 0,
    command_other_used INTEGER DEFAULT 0,
    workflow_chat_only INTEGER DEFAULT 0,
    workflow_coding_used INTEGER DEFAULT 0,
    workflow_research_used INTEGER DEFAULT 0,
    workflow_tests_used INTEGER DEFAULT 0,
    workflow_background_used INTEGER DEFAULT 0,
    workflow_subagent_used INTEGER DEFAULT 0,
    workflow_swarm_used INTEGER DEFAULT 0,
    project_repo_present INTEGER DEFAULT 0,
    project_lang_rust INTEGER DEFAULT 0,
    project_lang_js_ts INTEGER DEFAULT 0,
    project_lang_python INTEGER DEFAULT 0,
    project_lang_go INTEGER DEFAULT 0,
    project_lang_markdown INTEGER DEFAULT 0,
    project_lang_mixed INTEGER DEFAULT 0,
    days_since_install INTEGER,
    active_days_7d INTEGER DEFAULT 0,
    active_days_30d INTEGER DEFAULT 0,
    FOREIGN KEY (event_id) REFERENCES events(event_id)
);
