-- Telemetry health dashboard query.
-- Usage:
--   wrangler d1 execute jcode-telemetry --remote --file=health.sql

WITH install_ids AS (
    SELECT DISTINCT telemetry_id
    FROM events INDEXED BY idx_events_event_telemetry_created
    WHERE event = 'install'
), lifecycle AS (
    SELECT telemetry_id, created_at
    FROM events INDEXED BY idx_events_event_telemetry_created
    WHERE event IN ('session_end', 'session_crash')
), session_starts_by_id AS (
    SELECT DISTINCT telemetry_id
    FROM events INDEXED BY idx_events_event_telemetry_created
    WHERE event = 'session_start'
), event_counts AS (
    SELECT
        SUM(CASE WHEN event = 'install' THEN 1 ELSE 0 END) AS install_events,
        SUM(CASE WHEN event = 'session_start' THEN 1 ELSE 0 END) AS session_starts,
        SUM(CASE WHEN event = 'session_end' THEN 1 ELSE 0 END) AS session_ends,
        SUM(CASE WHEN event = 'session_crash' THEN 1 ELSE 0 END) AS session_crashes
    FROM events INDEXED BY idx_events_event_created_telemetry
    WHERE event IN ('install', 'session_start', 'session_end', 'session_crash')
), identity_counts AS (
    SELECT
        (SELECT COUNT(*) FROM install_ids) AS install_ids,
        (SELECT COUNT(DISTINCT telemetry_id) FROM lifecycle) AS lifecycle_ids,
        (SELECT COUNT(*) FROM session_starts_by_id) AS session_start_ids,
        (SELECT COUNT(DISTINCT lifecycle.telemetry_id)
         FROM lifecycle
         LEFT JOIN install_ids USING (telemetry_id)
         WHERE install_ids.telemetry_id IS NULL) AS lifecycle_ids_without_install
),
meaningful AS (
    SELECT
        SUM(CASE WHEN e.event IN ('session_end', 'session_crash') THEN 1 ELSE 0 END) AS meaningful_sessions,
        COUNT(DISTINCT e.telemetry_id) AS meaningful_users_30d
    FROM events e
    LEFT JOIN turn_details td ON td.event_id = e.event_id
    WHERE e.event IN ('session_end', 'session_crash', 'turn_end')
      AND e.created_at > datetime('now', '-30 days')
      AND (
        (e.event IN ('session_end', 'session_crash') AND (
          e.turns > 0
          OR e.duration_mins > 0
          OR e.error_provider_timeout > 0
          OR e.error_auth_failed > 0
          OR e.error_tool_error > 0
          OR e.error_mcp_error > 0
          OR e.error_rate_limited > 0
          OR e.provider_switches > 0
          OR e.model_switches > 0
          OR e.had_user_prompt > 0
          OR e.had_assistant_response > 0
          OR e.assistant_responses > 0
          OR e.tool_calls > 0
          OR e.tool_failures > 0
          OR e.executed_tool_calls > 0
          OR e.feature_memory_used > 0
          OR e.feature_swarm_used > 0
          OR e.feature_web_used > 0
          OR e.feature_email_used > 0
          OR e.feature_mcp_used > 0
          OR e.feature_side_panel_used > 0
          OR e.feature_goal_used > 0
          OR e.feature_selfdev_used > 0
          OR e.feature_background_used > 0
          OR e.feature_subagent_used > 0
        ))
        -- turn_end activity lives in turn_details: production events never got
        -- migration 0005's per-turn columns (D1 caps tables at 100 columns).
        OR (e.event = 'turn_end' AND (
          td.assistant_responses > 0
          OR td.tool_calls > 0
          OR td.executed_tool_calls > 0
          OR td.file_write_calls > 0
          OR td.tests_run > 0
        ))
      )
),
outliers AS (
    SELECT
        MAX(session_events) AS max_session_events_one_id,
        SUM(CASE WHEN rn <= 5 THEN session_events ELSE 0 END) AS top5_session_events,
        SUM(session_events) AS total_session_events
    FROM (
        SELECT telemetry_id, COUNT(*) AS session_events,
               ROW_NUMBER() OVER (ORDER BY COUNT(*) DESC) AS rn
        FROM lifecycle
        GROUP BY telemetry_id
    )
),
ci_noise AS (
    SELECT
        COUNT(DISTINCT telemetry_id) AS ci_ids_30d,
        COUNT(DISTINCT CASE WHEN event = 'install' THEN telemetry_id END) AS ci_install_ids,
        COUNT(DISTINCT CASE WHEN event IN ('session_end', 'session_crash') THEN telemetry_id END) AS ci_lifecycle_ids
    FROM events
    INDEXED BY idx_events_event_created_telemetry
    WHERE event IN ('install', 'session_start', 'turn_end', 'session_end', 'session_crash')
      AND created_at > datetime('now', '-30 days')
      AND is_ci = 1
),
-- Auth failure health: count affected sessions/users, NOT SUM(error_auth_failed).
-- Raw sums are dominated by runaway retry loops (one session logged 18k+ auth
-- failures pre-breaker), which makes a single broken install look like a
-- fleet-wide auth outage. Affected-session/user counts are outlier-resistant.
auth_failures AS (
    SELECT
        COUNT(*) AS auth_failed_sessions_30d,
        COUNT(DISTINCT telemetry_id) AS auth_failed_users_30d,
        MAX(error_auth_failed) AS max_auth_fails_one_session_30d
    FROM events
    WHERE event IN ('session_end', 'session_crash')
      AND error_auth_failed > 0
      AND created_at > datetime('now', '-30 days')
)
SELECT
    install_events,
    session_starts,
    session_ends,
    session_crashes,
    install_ids,
    lifecycle_ids,
    session_start_ids,
    lifecycle_ids_without_install,
    meaningful_sessions,
    meaningful_users_30d,
    ci_ids_30d,
    ci_install_ids,
    ci_lifecycle_ids,
    max_session_events_one_id,
    top5_session_events,
    total_session_events,
    auth_failed_sessions_30d,
    auth_failed_users_30d,
    max_auth_fails_one_session_30d,
    ROUND(CAST(session_ends + session_crashes AS REAL) / NULLIF(session_starts, 0), 3) AS lifecycle_completion_ratio
FROM event_counts, identity_counts, meaningful, outliers, ci_noise, auth_failures;
