-- Website visit -> successful first Jcode launch funnel, last 30 days.
-- Usage:
--   wrangler d1 execute jcode-telemetry --remote --file=conversion.sql

WITH site_traffic AS (
    SELECT
        COUNT(*) AS pageviews,
        COUNT(DISTINCT w.visitor_id) AS visitors,
        COUNT(DISTINCT e.session_id) AS sessions
    FROM events e
    JOIN web_details w ON w.event_id = e.event_id
    WHERE e.event = 'web_pageview'
      AND e.created_at > datetime('now', '-30 days')
), intents AS (
    SELECT
        w.conversion_id,
        MIN(e.created_at) AS intent_at,
        MAX(w.visitor_id) AS visitor_id,
        MAX(e.session_id) AS session_id,
        MAX(w.placement) AS placement,
        MAX(w.install_method) AS install_method,
        MAX(w.utm_source) AS utm_source,
        MAX(w.utm_medium) AS utm_medium,
        MAX(w.utm_campaign) AS utm_campaign
    FROM events e
    JOIN web_details w ON w.event_id = e.event_id
    WHERE e.event = 'web_cta_click'
      AND w.conversion_id IS NOT NULL
      AND e.created_at > datetime('now', '-30 days')
    GROUP BY w.conversion_id
), stages AS (
    SELECT
        d.conversion_id,
        MAX(CASE WHEN d.stage = 'command_copy' AND d.outcome = 'success' THEN 1 ELSE 0 END) AS command_copied,
        MAX(CASE WHEN d.stage = 'script_request' AND d.outcome = 'success' THEN 1 ELSE 0 END) AS script_requested,
        MAX(CASE WHEN d.stage = 'installer_start' AND d.outcome = 'success' THEN 1 ELSE 0 END) AS installer_started,
        MAX(CASE WHEN d.stage = 'installer_finish' AND d.outcome = 'success' THEN 1 ELSE 0 END) AS installer_succeeded,
        MAX(CASE WHEN d.stage = 'installer_finish' AND d.outcome = 'failure' THEN 1 ELSE 0 END) AS installer_failed,
        MAX(CASE WHEN d.stage = 'first_run' AND d.outcome = 'success' THEN 1 ELSE 0 END) AS first_run,
        MIN(CASE WHEN d.stage = 'script_request' AND d.outcome = 'success' THEN e.created_at END) AS script_requested_at,
        MIN(CASE WHEN d.stage = 'installer_finish' AND d.outcome = 'success' THEN e.created_at END) AS installer_succeeded_at,
        MIN(CASE WHEN d.stage = 'first_run' AND d.outcome = 'success' THEN e.created_at END) AS first_run_at
    FROM install_details d
    JOIN events e ON e.event_id = d.event_id
    WHERE d.conversion_id IS NOT NULL
      AND e.created_at > datetime('now', '-30 days')
    GROUP BY d.conversion_id
), totals AS (
    SELECT
        COUNT(*) AS install_intents,
        COUNT(DISTINCT visitor_id) AS intending_visitors,
        COUNT(DISTINCT session_id) AS intending_sessions,
        SUM(CASE WHEN install_method = 'shell' THEN 1 ELSE 0 END) AS attributable_shell_intents,
        SUM(COALESCE(command_copied, 0)) AS commands_copied,
        SUM(COALESCE(script_requested, 0)) AS scripts_requested,
        SUM(COALESCE(installer_started, 0)) AS installers_started,
        SUM(COALESCE(installer_succeeded, 0)) AS installers_succeeded,
        SUM(COALESCE(installer_failed, 0)) AS installers_failed,
        SUM(COALESCE(first_run, 0)) AS first_runs,
        ROUND(AVG(CASE WHEN script_requested_at IS NOT NULL
            THEN (julianday(script_requested_at) - julianday(intent_at)) * 24 END), 2) AS avg_hours_intent_to_script,
        ROUND(AVG(CASE WHEN installer_succeeded_at IS NOT NULL
            THEN (julianday(installer_succeeded_at) - julianday(intent_at)) * 24 END), 2) AS avg_hours_intent_to_installer_success,
        ROUND(AVG(CASE WHEN first_run_at IS NOT NULL
            THEN (julianday(first_run_at) - julianday(intent_at)) * 24 END), 2) AS avg_hours_intent_to_first_run
    FROM intents
    LEFT JOIN stages USING (conversion_id)
)
SELECT
    site_traffic.pageviews AS site_pageviews,
    site_traffic.sessions AS site_sessions,
    site_traffic.visitors AS site_visitors,
    totals.*,
    ROUND(1.0 * intending_visitors / MAX(1, site_traffic.visitors), 4) AS visitor_to_intent,
    ROUND(1.0 * intending_sessions / MAX(1, site_traffic.sessions), 4) AS session_to_intent,
    ROUND(1.0 * commands_copied / MAX(1, attributable_shell_intents), 4) AS shell_intent_to_copy,
    ROUND(1.0 * scripts_requested / MAX(1, commands_copied), 4) AS copy_to_script,
    ROUND(1.0 * installers_succeeded / MAX(1, installers_started), 4) AS installer_success,
    ROUND(1.0 * first_runs / MAX(1, attributable_shell_intents), 4) AS shell_intent_to_first_run,
    ROUND(1.0 * first_runs / MAX(1, site_traffic.visitors), 4) AS tracked_visitor_to_first_run
FROM site_traffic, totals;

-- Attribution and placement breakdown. Each conversion_id is counted once.
WITH intents AS (
    SELECT
        w.conversion_id,
        COALESCE(w.utm_source, '(direct)') AS utm_source,
        COALESCE(w.utm_medium, '(none)') AS utm_medium,
        COALESCE(w.utm_campaign, '(none)') AS utm_campaign,
        COALESCE(w.path, '(unknown)') AS path,
        COALESCE(w.placement, '(unknown)') AS placement,
        COALESCE(w.install_method, '(unknown)') AS install_method
    FROM events e
    JOIN web_details w ON w.event_id = e.event_id
    WHERE e.event = 'web_cta_click'
      AND w.conversion_id IS NOT NULL
      AND e.created_at > datetime('now', '-30 days')
    GROUP BY w.conversion_id
), first_runs AS (
    SELECT DISTINCT d.conversion_id
    FROM install_details d
    JOIN events e ON e.event_id = d.event_id
    WHERE d.stage = 'first_run'
      AND d.outcome = 'success'
      AND d.conversion_id IS NOT NULL
      AND e.created_at > datetime('now', '-30 days')
)
SELECT
    utm_source, utm_medium, utm_campaign, path, placement, install_method,
    COUNT(*) AS intents,
    SUM(CASE WHEN first_runs.conversion_id IS NOT NULL THEN 1 ELSE 0 END) AS first_runs,
    ROUND(1.0 * SUM(CASE WHEN first_runs.conversion_id IS NOT NULL THEN 1 ELSE 0 END) / COUNT(*), 4) AS intent_to_first_run
FROM intents
LEFT JOIN first_runs USING (conversion_id)
GROUP BY 1, 2, 3, 4, 5, 6
ORDER BY intents DESC;

-- Installer completion by coarse platform. Each conversion is counted once.
WITH platform_stages AS (
    SELECT
        d.conversion_id,
        MAX(CASE WHEN d.stage = 'installer_start' THEN e.os END) AS os,
        MAX(CASE WHEN d.stage = 'installer_start' THEN e.arch END) AS arch,
        MAX(CASE WHEN d.stage = 'installer_start' AND d.outcome = 'success' THEN 1 ELSE 0 END) AS started,
        MAX(CASE WHEN d.stage = 'installer_finish' AND d.outcome = 'success' THEN 1 ELSE 0 END) AS succeeded,
        MAX(CASE WHEN d.stage = 'installer_finish' AND d.outcome = 'failure' THEN 1 ELSE 0 END) AS had_failure
    FROM install_details d
    JOIN events e ON e.event_id = d.event_id
    WHERE d.conversion_id IS NOT NULL
      AND d.stage IN ('installer_start', 'installer_finish')
      AND e.created_at > datetime('now', '-30 days')
    GROUP BY d.conversion_id
)
SELECT
    COALESCE(os, '(unknown)') AS os,
    COALESCE(arch, '(unknown)') AS arch,
    SUM(started) AS installers_started,
    SUM(succeeded) AS installers_succeeded,
    SUM(had_failure) AS conversions_with_failure,
    ROUND(1.0 * SUM(succeeded) / MAX(1, SUM(started)), 4) AS installer_success
FROM platform_stages
GROUP BY os, arch
ORDER BY installers_started DESC;

-- Installer failures by coarse stage and platform, without messages or machine details.
SELECT
    COALESCE(d.failure_stage, '(unknown)') AS failure_stage,
    COALESCE(e.os, '(unknown)') AS os,
    COALESCE(e.arch, '(unknown)') AS arch,
    COUNT(*) AS failure_attempts,
    COUNT(DISTINCT d.conversion_id) AS conversions_with_failure
FROM install_details d
JOIN events e ON e.event_id = d.event_id
WHERE d.stage = 'installer_finish'
  AND d.outcome = 'failure'
  AND d.conversion_id IS NOT NULL
  AND e.created_at > datetime('now', '-30 days')
GROUP BY d.failure_stage, e.os, e.arch
ORDER BY failure_attempts DESC;
