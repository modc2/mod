-- Privacy-safe website performance and error classifications.
--
-- web_vital stores only a bounded standard metric name/value/rating tuple.
-- web_error stores only a coarse error kind. Error messages, stacks, and URLs
-- are intentionally not represented in the schema.

ALTER TABLE web_details ADD COLUMN metric_name TEXT;
ALTER TABLE web_details ADD COLUMN metric_value REAL;
ALTER TABLE web_details ADD COLUMN rating TEXT;
ALTER TABLE web_details ADD COLUMN error_kind TEXT;
