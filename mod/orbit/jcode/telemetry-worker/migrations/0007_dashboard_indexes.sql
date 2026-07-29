-- Composite indexes for telemetry dashboard/read-heavy queries.
-- D1/SQLite can use these to satisfy event + time filters and distinct telemetry_id
-- counts without repeatedly scanning the full events table.

CREATE INDEX IF NOT EXISTS idx_events_event_created_telemetry
    ON events(event, created_at, telemetry_id);

CREATE INDEX IF NOT EXISTS idx_events_event_telemetry_created
    ON events(event, telemetry_id, created_at);
