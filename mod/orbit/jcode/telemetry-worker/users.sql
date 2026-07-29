-- Canonical "total users" definitions for jcode telemetry.
-- Usage:
--   wrangler d1 execute jcode-telemetry --remote --file=users.sql
--
-- Headline number: total_users. A "user" is a distinct, non-CI telemetry_id that
-- ever either installed jcode or did meaningful work in it. We exclude CI traffic
-- (ephemeral runners mint a fresh id per job) and exclude empty open/close
-- sessions that never did anything. Raw, less-filtered tiers are reported
-- alongside it so no signal is hidden.
--
-- Implementation note: high-volume raw events (turn_end, session_start,
-- onboarding_step) are pruned on a retention schedule to stay under the D1
-- 500 MB cap, so meaningful-activity membership comes from the durable
-- daily_active_users rollup (maintained at insert time and backfilled across
-- full history by migration 0014). install events are never pruned and anchor
-- the install tier.
--
-- Caveats (see README "Accuracy notes"): telemetry_id is per-machine, so one
-- person on N machines counts as N; opt-outs and network-blocked clients are
-- never counted; CI rows created before the is_ci column existed default to 0
-- and may slip in.

WITH install_ids AS (
    SELECT DISTINCT telemetry_id
    FROM events INDEXED BY idx_events_event_telemetry_created
    WHERE event = 'install' AND is_ci = 0
), meaningful_ids AS (
    SELECT DISTINCT telemetry_id
    FROM daily_active_users
    WHERE meaningful_active > 0 AND last_is_ci = 0
), reached_ids AS (
    SELECT DISTINCT telemetry_id FROM daily_active_users WHERE last_is_ci = 0
    UNION
    SELECT DISTINCT telemetry_id FROM events WHERE is_ci = 0
), all_ids AS (
    SELECT DISTINCT telemetry_id FROM daily_active_users
    UNION
    SELECT DISTINCT telemetry_id FROM events
), ci_ids AS (
    SELECT DISTINCT telemetry_id FROM daily_active_users WHERE last_is_ci = 1
    UNION
    SELECT DISTINCT telemetry_id FROM events WHERE is_ci = 1
)
SELECT
    -- HEADLINE: real people who installed or meaningfully used jcode.
    (SELECT COUNT(*) FROM (
        SELECT telemetry_id FROM install_ids
        UNION
        SELECT telemetry_id FROM meaningful_ids
    )) AS total_users,

    -- Core users: did meaningful work (excludes install-only, never-used ids).
    (SELECT COUNT(*) FROM meaningful_ids) AS core_users,

    -- Reach: every distinct non-CI id that ever launched jcode (incl. empty
    -- open/close sessions). Upper bound on "people who ran it at least once".
    (SELECT COUNT(*) FROM reached_ids) AS reached_users,

    -- Installs only (non-CI), for comparison with total_users.
    (SELECT COUNT(*) FROM install_ids) AS installed_users,

    -- Unfiltered grand total (includes CI + dev). Never use as the headline;
    -- kept for transparency and for sizing CI noise.
    (SELECT COUNT(*) FROM all_ids) AS all_ids_including_ci,

    -- CI-only ids, so the gap between all_ids and total_users is explainable.
    (SELECT COUNT(*) FROM ci_ids) AS ci_ids;
