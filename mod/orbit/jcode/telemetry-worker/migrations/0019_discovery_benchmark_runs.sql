-- Keep live Discovery benchmarks out of ordinary sponsored-discovery analysis.
ALTER TABLE discovery_details
    ADD COLUMN benchmark_run INTEGER NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_discovery_benchmark_run
    ON discovery_details(benchmark_run);
