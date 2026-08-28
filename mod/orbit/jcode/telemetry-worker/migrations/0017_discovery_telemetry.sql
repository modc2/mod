-- Dedicated detail table for privacy-safe sponsored-discovery telemetry.
-- The parent events table is intentionally left unchanged because it is near
-- D1's 100-column cap.
CREATE TABLE IF NOT EXISTS discovery_details (
    event_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    phase TEXT NOT NULL,
    category TEXT,
    selected_tool TEXT,
    outcome TEXT NOT NULL,
    failure_reason TEXT,
    http_status INTEGER,
    latency_ms INTEGER NOT NULL DEFAULT 0,
    response_bytes INTEGER,
    result_count INTEGER,
    query_present INTEGER NOT NULL DEFAULT 0,
    reason_present INTEGER NOT NULL DEFAULT 0,
    custom_endpoint INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (event_id) REFERENCES events(event_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_discovery_request_id ON discovery_details(request_id);
CREATE INDEX IF NOT EXISTS idx_discovery_phase_outcome ON discovery_details(phase, outcome);
CREATE INDEX IF NOT EXISTS idx_discovery_category_outcome ON discovery_details(category, outcome);
CREATE INDEX IF NOT EXISTS idx_discovery_selected_tool ON discovery_details(selected_tool);
CREATE INDEX IF NOT EXISTS idx_discovery_failure_reason ON discovery_details(failure_reason);
