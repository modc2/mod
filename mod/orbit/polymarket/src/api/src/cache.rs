use std::collections::HashMap;
use std::path::PathBuf;
use std::time::{Duration, Instant};

use parking_lot::RwLock;
use serde_json::Value;

use crate::types::AggPayload;

// ─── Proxy Cache (TTL-based in-memory + disk for persistent endpoints) ───

struct CacheEntry {
    data: Value,
    inserted_at: Instant,
    ttl: Duration,
}

pub struct ProxyCache {
    entries: RwLock<HashMap<String, CacheEntry>>,
    max_entries: usize,
    disk_dir: PathBuf,
}

/// Freshness window for trader activity/trades (seconds). Matches the hourly
/// warmup cadence in main.rs and `AGG_TTL` below: warmed entries stay valid for
/// the full hour instead of expiring early and forcing an on-demand data-api
/// refetch (the dominant 429 source). The live copy engine does NOT use this
/// cache — it fetches data-api directly at its own 60s cadence — so copy
/// responsiveness is unaffected by this window.
const FRESHNESS_TTL_SECS: u64 = 3600;

/// Freshness window for the user's OWN portfolio reads (seconds). Positions
/// and total value change with every fill/redeem — serving them for 24h made
/// the portfolio panel show positions that no longer exist (stale-$227 bug:
/// data-api said $0 while the proxy kept returning a day-old /value). 90s
/// keeps data-api load trivial (the panel polls every 30s) while bounding
/// staleness to ~1 poll cycle.
const PORTFOLIO_TTL_SECS: u64 = 90;

/// Endpoints whose responses get persisted to disk (trader + historical data).
/// These survive server restarts and are never re-fetched from Polymarket
/// once cached.
const PERSIST_PREFIXES: &[&str] = &[
    // Trader data (data-api)
    "activity", "positions", "users/", "trades", "v1/", "holders", "value",
    // Historical data (clob)
    "prices-history", "market-trades",
];

impl ProxyCache {
    pub fn new(max_entries: usize) -> Self {
        let disk_dir = std::env::temp_dir().join("polymarket-proxy-cache");
        std::fs::create_dir_all(&disk_dir).ok();
        Self {
            entries: RwLock::new(HashMap::new()),
            max_entries,
            disk_dir,
        }
    }

    /// Check if an endpoint's data should be persisted to disk.
    pub fn is_persistent(endpoint: &str) -> bool {
        let ep = endpoint.to_lowercase();
        PERSIST_PREFIXES.iter().any(|p| ep.starts_with(p))
    }

    pub fn get(&self, key: &str, endpoint: &str) -> Option<(Value, bool)> {
        // Memory first
        {
            let entries = self.entries.read();
            if let Some(entry) = entries.get(key) {
                let fresh = entry.inserted_at.elapsed() < entry.ttl;
                return Some((entry.data.clone(), fresh));
            }
        }
        // Disk fallback for persistent endpoints
        if Self::is_persistent(endpoint) {
            if let Some((data, age)) = self.load_from_disk(key) {
                // Re-populate memory, backdating the entry by the file's age
                // so its freshness matches reality — a 3h-old disk snapshot
                // must NOT be served as fresh for another full window (that's
                // exactly how a $0 portfolio kept rendering as $227).
                let ttl = Self::persist_freshness(endpoint);
                let inserted_at = Instant::now().checked_sub(age).unwrap_or_else(Instant::now);
                let fresh = age < ttl;
                let mut entries = self.entries.write();
                if entries.len() >= self.max_entries {
                    self.evict_one(&mut entries);
                }
                entries.insert(key.to_string(), CacheEntry {
                    data: data.clone(),
                    inserted_at,
                    ttl,
                });
                return Some((data, fresh));
            }
        }
        None
    }

    /// Trader trade/activity endpoints persist to disk (survive restarts) but
    /// memory-cache for only `FRESHNESS_TTL_SECS` (1h), not 24h. This bounds how
    /// stale a trader's activity can get while still letting it survive between
    /// hourly warmup cycles. Markets/holders/historical data still cache for 24h
    /// — those don't churn meaningfully.
    fn is_freshness_critical(endpoint: &str) -> bool {
        let ep = endpoint.to_lowercase();
        ep.starts_with("activity") || ep.starts_with("trades")
    }

    /// Portfolio endpoints (the signed-in user's own holdings) must stay
    /// near-live; see PORTFOLIO_TTL_SECS.
    fn is_portfolio(endpoint: &str) -> bool {
        let ep = endpoint.to_lowercase();
        ep.starts_with("positions") || ep.starts_with("value") || ep.starts_with("closed-positions")
    }

    /// Memory-freshness window for a persistent endpoint.
    fn persist_freshness(endpoint: &str) -> Duration {
        if Self::is_portfolio(endpoint) {
            Duration::from_secs(PORTFOLIO_TTL_SECS)
        } else if Self::is_freshness_critical(endpoint) {
            Duration::from_secs(FRESHNESS_TTL_SECS)
        } else {
            Duration::from_secs(86400)
        }
    }

    pub fn set(&self, key: String, data: Value, ttl: Duration, endpoint: &str) {
        let persist = Self::is_persistent(endpoint);
        // Trader trade/activity entries get a 1-hour memory TTL (matching the
        // hourly warmup) so on-demand browse requests hit the warmed cache
        // instead of refetching data-api every minute.
        let mem_ttl = if persist {
            Self::persist_freshness(endpoint)
        } else {
            ttl
        };

        let mut entries = self.entries.write();
        if entries.len() >= self.max_entries && !entries.contains_key(&key) {
            self.evict_one(&mut entries);
        }
        entries.insert(key.clone(), CacheEntry {
            data: data.clone(),
            inserted_at: Instant::now(),
            ttl: mem_ttl,
        });
        drop(entries);

        // Persist to disk
        if persist {
            self.save_to_disk(&key, &data);
        }
    }

    /// Get TTL for an endpoint (used for Cache-Control headers).
    pub fn ttl_for_endpoint(endpoint: &str) -> Duration {
        let ep = endpoint.to_lowercase();
        if ep.starts_with("user-trades") {
            // The signed-in user's own fill tape — must be near-live so an
            // executed order shows up within a poll cycle, not an hour later.
            // (Checked before is_persistent: "user-trades" doesn't prefix-match
            // "trades" so it's memory-only, but keep the guard explicit.)
            Duration::from_secs(60)
        } else if ep.starts_with("live-") {
            // Near-live CLOB reads for sub-hour candle strats (live-prices-history,
            // live-midpoint). The markets they track live ~5 minutes end to end,
            // so a cache generation must be a fraction of one poll cycle. The
            // "live-" name also keeps them off every PERSIST prefix — a 24h disk
            // snapshot of a 5-minute market is worse than no data.
            Duration::from_secs(15)
        } else if Self::is_persistent(&ep) {
            Duration::from_secs(86400) // 24 hours — data is on disk, no need to refetch
        } else if ep.starts_with("markets") || ep.starts_with("events") || ep.contains("search") {
            Duration::from_secs(90) // 90 seconds — keep markets fresh
        } else if ep.starts_with("book") || ep.starts_with("midpoint") || ep.starts_with("price") {
            Duration::from_secs(120) // 2 minutes — live orderbook
        } else {
            Duration::from_secs(300) // 5 minutes
        }
    }

    fn evict_one(&self, entries: &mut HashMap<String, CacheEntry>) {
        let oldest_key = entries
            .iter()
            .min_by_key(|(_, v)| v.inserted_at)
            .map(|(k, _)| k.clone());
        if let Some(k) = oldest_key {
            entries.remove(&k);
        }
    }

    // ── Disk persistence ──

    fn disk_path(&self, key: &str) -> PathBuf {
        let safe: String = key.chars()
            .map(|c| if c.is_alphanumeric() || c == '-' || c == '_' || c == '.' { c } else { '_' })
            .collect();
        // Cap filename length to avoid filesystem issues
        let name = if safe.len() > 200 { &safe[..200] } else { &safe };
        self.disk_dir.join(format!("{}.json", name))
    }

    fn save_to_disk(&self, key: &str, data: &Value) {
        let path = self.disk_path(key);
        if let Ok(json) = serde_json::to_string(data) {
            std::fs::write(path, json).ok();
        }
    }

    /// Load a persisted entry along with its age (from file mtime). The
    /// caller decides freshness per-endpoint — stale entries are still
    /// returned so the proxy can serve them as a fallback when the upstream
    /// errors, but they no longer masquerade as fresh (which used to
    /// propagate day-old positions/activity as live data).
    fn load_from_disk(&self, key: &str) -> Option<(Value, Duration)> {
        let path = self.disk_path(key);
        if !path.exists() { return None; }
        let age = std::fs::metadata(&path)
            .ok()
            .and_then(|m| m.modified().ok())
            .and_then(|m| m.elapsed().ok())
            .unwrap_or(Duration::from_secs(86400 * 365));
        let raw = std::fs::read_to_string(&path).ok()?;
        let data = serde_json::from_str(&raw).ok()?;
        Some((data, age))
    }
}

// ─── Pipeline Cache (Memory + Disk, hourly) ───

// 1 hour — matches the hourly warmup cadence in main.rs. The aggregated
// leaderboard (30d trader Sharpe/ROI) is slow-moving, so serving it from
// cache for an hour avoids re-pulling ~6k traders from the data-api on every
// on-demand request (the old 60s TTL was a major 429 source).
const AGG_TTL: Duration = Duration::from_secs(3600);
const DISK_MAX_AGE: Duration = Duration::from_secs(86400); // 24h — keep disk cache across restarts

/// Max AggPayloads held in memory at once. Each is ~2k traders with pnl
/// curves + per-market metrics (12–48MB as JSON, several× that as structs),
/// and the warmup writes one per window (1/7/10/14/30d) — keeping them all
/// resident duplicated the whole disk cache in RAM. Disk is authoritative;
/// memory is a hot tier for the window(s) actually being browsed.
const MEM_ENTRIES_MAX: usize = 2;

struct PipelineCacheEntry {
    payload: AggPayload,
    created_at: Instant,
}

pub struct PipelineCache {
    entries: RwLock<HashMap<String, PipelineCacheEntry>>,
    disk_dir: PathBuf,
}

impl PipelineCache {
    pub fn new() -> Self {
        let disk_dir = std::env::temp_dir().join("polymarket-active-traders-cache");
        std::fs::create_dir_all(&disk_dir).ok();
        Self {
            entries: RwLock::new(HashMap::new()),
            disk_dir,
        }
    }

    pub fn get(&self, key: &str) -> Option<AggPayload> {
        let entries = self.entries.read();
        if let Some(entry) = entries.get(key) {
            if entry.created_at.elapsed() < AGG_TTL {
                return Some(entry.payload.clone());
            }
        }
        None
    }

    pub fn get_or_disk(&self, key: &str) -> Option<(AggPayload, &'static str)> {
        // Memory first
        if let Some(payload) = self.get(key) {
            return Some((payload, "memory"));
        }
        // Disk fallback
        if let Some(payload) = self.load_from_disk(key) {
            let mut entries = self.entries.write();
            entries.insert(key.to_string(), PipelineCacheEntry {
                payload: payload.clone(),
                created_at: Instant::now(),
            });
            Self::evict_over_cap(&mut entries);
            return Some((payload, "disk"));
        }
        None
    }

    pub fn set(&self, key: &str, payload: AggPayload) {
        // Memory
        let mut entries = self.entries.write();
        entries.insert(key.to_string(), PipelineCacheEntry {
            payload: payload.clone(),
            created_at: Instant::now(),
        });
        Self::evict_over_cap(&mut entries);
        drop(entries);
        // Disk (best-effort)
        self.save_to_disk(key, &payload);
    }

    /// Drop the oldest entries until the memory tier fits MEM_ENTRIES_MAX.
    /// Evicted windows reload from disk on next request via get_or_disk.
    fn evict_over_cap(entries: &mut HashMap<String, PipelineCacheEntry>) {
        while entries.len() > MEM_ENTRIES_MAX {
            let oldest = entries.iter()
                .min_by_key(|(_, v)| v.created_at)
                .map(|(k, _)| k.clone());
            match oldest {
                Some(k) => { entries.remove(&k); }
                None => break,
            }
        }
    }

    fn disk_path(&self, key: &str) -> PathBuf {
        let safe_key = key.replace(':', "_");
        self.disk_dir.join(format!("{}.json", safe_key))
    }

    fn save_to_disk(&self, key: &str, payload: &AggPayload) {
        let path = self.disk_path(key);
        if let Ok(json) = serde_json::to_string(payload) {
            std::fs::write(path, json).ok();
        }
    }

    fn load_from_disk(&self, key: &str) -> Option<AggPayload> {
        let path = self.disk_path(key);
        if path.exists() {
            // Skip if file is older than DISK_MAX_AGE
            let mut mtime_secs: i64 = 0;
            if let Ok(meta) = std::fs::metadata(&path) {
                if let Ok(modified) = meta.modified() {
                    if modified.elapsed().unwrap_or(Duration::ZERO) > DISK_MAX_AGE {
                        return None;
                    }
                    if let Ok(dur) = modified.duration_since(std::time::UNIX_EPOCH) {
                        mtime_secs = dur.as_secs() as i64;
                    }
                }
            }
            let data = std::fs::read_to_string(&path).ok()?;
            let mut payload: AggPayload = serde_json::from_str(&data).ok()?;
            // Old disk payloads (before synced_at existed) deserialize with
            // synced_at=0. Fall back to the file's mtime so the client still
            // sees a sensible "last sync" instead of 1970.
            if payload.synced_at == 0 {
                payload.synced_at = mtime_secs;
            }
            // Payloads written before the Sharpe epsilon guard carry
            // degenerate ~1e15 values (float-noise stdev on identical
            // returns). Zero them so a stale disk cache can't put junk
            // traders on top of the sharpe-sorted leaderboard until the
            // next warmup recompute.
            for t in &mut payload.traders {
                if !t.sharpe.is_finite() || t.sharpe.abs() > 1e6 {
                    t.sharpe = 0.0;
                }
            }
            Some(payload)
        } else {
            None
        }
    }
}
