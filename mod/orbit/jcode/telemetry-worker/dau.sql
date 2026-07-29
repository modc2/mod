-- Current UTC-day and trailing-24h DAU dashboard.
-- Usage:
--   wrangler d1 execute jcode-telemetry --remote --file=dau.sql
--
-- Note: production `events` never got migration 0005's per-turn columns (D1
-- caps tables at 100 columns), so turn_end activity lives in `turn_details`
-- keyed by event_id. The trailing-24h tiers join through it; the today tiers
-- read the daily_active_users rollup, which is classified at insert time from
-- the full client payload.

WITH today AS (
    SELECT
        COUNT(*) AS raw_today,
        SUM(CASE WHEN meaningful_active > 0 THEN 1 ELSE 0 END) AS meaningful_today,
        SUM(CASE WHEN release_active > 0 THEN 1 ELSE 0 END) AS raw_release_today,
        SUM(CASE WHEN meaningful_release_active > 0 THEN 1 ELSE 0 END) AS meaningful_release_today,
        -- Headline product metric: real users on the release channel, excluding
        -- automated CI traffic (ephemeral runners that mint a fresh id per job).
        SUM(CASE WHEN meaningful_release_active > 0 AND last_is_ci = 0 THEN 1 ELSE 0 END) AS meaningful_release_today_noci,
        SUM(CASE WHEN last_is_ci > 0 THEN 1 ELSE 0 END) AS ci_today
    FROM daily_active_users
    WHERE activity_date = date('now')
), recent AS (
    SELECT
        e.telemetry_id,
        e.event,
        e.build_channel,
        e.is_ci,
        CASE
            WHEN e.event IN ('session_end', 'session_crash') AND (
                e.turns > 0 OR e.had_user_prompt > 0 OR e.had_assistant_response > 0
                OR e.assistant_responses > 0 OR e.tool_calls > 0 OR e.executed_tool_calls > 0
                OR e.duration_secs > 0 OR e.error_provider_timeout > 0 OR e.error_auth_failed > 0
                OR e.error_tool_error > 0 OR e.error_mcp_error > 0 OR e.error_rate_limited > 0
                OR e.provider_switches > 0 OR e.model_switches > 0
            ) THEN 1
            WHEN e.event = 'turn_end' AND (
                td.assistant_responses > 0 OR td.tool_calls > 0 OR td.executed_tool_calls > 0
                OR td.file_write_calls > 0 OR td.tests_run > 0
            ) THEN 1
            ELSE 0
        END AS meaningful
    FROM events e
    LEFT JOIN turn_details td ON td.event_id = e.event_id
    WHERE e.event IN ('session_start', 'turn_end', 'session_end', 'session_crash')
      AND e.created_at > datetime('now', '-1 day')
), trailing_24h AS (
    SELECT
        COUNT(DISTINCT telemetry_id) AS raw_24h,
        COUNT(DISTINCT CASE WHEN meaningful = 1 THEN telemetry_id END) AS meaningful_24h,
        COUNT(DISTINCT CASE WHEN build_channel = 'release' THEN telemetry_id END) AS raw_release_24h,
        COUNT(DISTINCT CASE WHEN build_channel = 'release' AND meaningful = 1 THEN telemetry_id END) AS meaningful_release_24h,
        -- Same headline metric over a rolling 24h window, excluding CI traffic.
        COUNT(DISTINCT CASE WHEN build_channel = 'release' AND is_ci = 0 AND meaningful = 1 THEN telemetry_id END) AS meaningful_release_24h_noci,
        COUNT(DISTINCT CASE WHEN is_ci = 1 THEN telemetry_id END) AS ci_24h
    FROM recent
)
SELECT * FROM today, trailing_24h;
