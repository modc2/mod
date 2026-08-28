-- Website analytics + token subscription plan events (migration 0016).
--
-- New event types:
--   web_pageview, web_cta_click            (website beacon, anonymous visitor_id)
--   subscription_login, subscription_activated,
--   subscription_budget_exhausted, subscription_router_error
--   account_linked                          (telemetry_id <-> account_id join anchor)
--
-- Column placement: production events sits at 96 of D1's 100-column hard cap
-- (see the note in schema.sql and migrations/0013), so only the two columns
-- every subscription dashboard query needs (account_id, tier) go on events
-- (96 -> 98; scarce headroom remains, spend it carefully). The web-only
-- fields live in a web_details detail table keyed by event_id, following the
-- session_details / turn_details house pattern. Subscription events that
-- carry a model reuse the existing generic events.model_start column (these
-- are new event types, so no historical rows are re-interpreted).

ALTER TABLE events ADD COLUMN account_id TEXT;
ALTER TABLE events ADD COLUMN tier TEXT;

CREATE TABLE IF NOT EXISTS web_details (
    event_id TEXT PRIMARY KEY,
    path TEXT,
    referrer TEXT,
    visitor_id TEXT,
    utm_source TEXT,
    utm_medium TEXT,
    utm_campaign TEXT,
    cta TEXT,
    FOREIGN KEY (event_id) REFERENCES events(event_id)
);

-- Indexes for the README dashboard queries.
-- Daily web visitors / pricing-page funnel group by visitor_id, path, cta:
CREATE INDEX IF NOT EXISTS idx_web_details_visitor_id ON web_details(visitor_id);
CREATE INDEX IF NOT EXISTS idx_web_details_path ON web_details(path);
CREATE INDEX IF NOT EXISTS idx_web_details_cta ON web_details(cta);
-- account_linked join + subscription rollups filter/group on these:
CREATE INDEX IF NOT EXISTS idx_events_account_id ON events(account_id);
CREATE INDEX IF NOT EXISTS idx_events_event_tier_created ON events(event, tier, created_at);
