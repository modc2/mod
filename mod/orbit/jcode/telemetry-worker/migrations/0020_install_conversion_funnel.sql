-- Privacy-safe website -> installer -> first-run conversion attribution.
-- The opaque conversion_id contains no browsing or account information and is
-- nulled after 90 days by the worker retention job.

ALTER TABLE web_details ADD COLUMN pageview_id TEXT;
ALTER TABLE web_details ADD COLUMN conversion_id TEXT;
ALTER TABLE web_details ADD COLUMN placement TEXT;
ALTER TABLE web_details ADD COLUMN install_method TEXT;

CREATE TABLE IF NOT EXISTS install_details (
    event_id TEXT PRIMARY KEY,
    conversion_id TEXT,
    stage TEXT,
    outcome TEXT,
    source TEXT,
    placement TEXT,
    install_method TEXT,
    failure_stage TEXT,
    FOREIGN KEY (event_id) REFERENCES events(event_id)
);

CREATE INDEX IF NOT EXISTS idx_web_details_conversion_id ON web_details(conversion_id)
    WHERE conversion_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_web_details_pageview_id ON web_details(pageview_id)
    WHERE pageview_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_install_details_conversion_id ON install_details(conversion_id)
    WHERE conversion_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_install_details_stage_outcome ON install_details(stage, outcome);
