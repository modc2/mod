use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::{Duration, Instant};

use parking_lot::RwLock;
use serde_json::Value;

use crate::types::{AggPayload, MarketMetric};

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

/// Freshness window for trader activity/trades (seconds). An upper bound, not a
/// match, now that the background warmup runs every 5 min by default (main.rs):
/// warmed entries stay valid for up to an hour instead of expiring early and
/// forcing an on-demand data-api refetch (the dominant 429 source) — in
/// practice a warmup cycle replaces them first. The live copy engine does NOT
/// use this cache — it fetches data-api directly at its own 60s cadence — so copy
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
    /// warmup cycles. Markets/holders/historical data still cache for 24h
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
        // Trader trade/activity entries get a 1-hour memory TTL (an upper bound
        // on the warmup cadence) so on-demand browse requests hit the warmed
        // cache instead of refetching data-api every minute.
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
        // Cap filename length to avoid filesystem issues. A bare truncation
        // made the tail of the key invisible to the filename, so two long
        // queries sharing a 200-char prefix — which every multi-id
        // `condition_ids=…` request does — mapped to ONE file and served each
        // other's data for the endpoint's whole TTL. Keep a readable prefix
        // and pin the identity with a digest of the FULL key.
        if safe.chars().count() > 200 {
            let mut h = <sha2::Sha256 as sha2::Digest>::new();
            sha2::Digest::update(&mut h, key.as_bytes());
            let digest = hex::encode(sha2::Digest::finalize(h));
            // `.chars().take()` rather than a byte slice: `is_alphanumeric`
            // passes non-ASCII letters through, and slicing those mid-codepoint
            // panics.
            let prefix: String = safe.chars().take(184).collect();
            return self.disk_dir.join(format!("{}-{}.json", prefix, &digest[..16]));
        }
        self.disk_dir.join(format!("{}.json", safe))
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

// ─── Pipeline Cache (Memory + Disk, 1h ceiling) ───

// 1 hour — the ceiling on leaderboard staleness when the background warmup is
// paused or falling behind; on the default 5-min cadence a fresh cycle
// overwrites these entries long before the TTL matters. The aggregated
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
///
/// It must cover EVERY warmed window, though, and at 2 it did not. The
/// per-market breakdown is `#[serde(skip)]` — it is far too big to persist —
/// so it exists only in the memory tier, and it is the only thing that lets a
/// market filter recompute a trader's stats from the matching markets
/// (`apply_pagination`). The warmup writes 1D, 7D, 14D and 30D in that order;
/// with room for two, the first two were evicted the moment the last two were
/// written, and every filtered request for the windows people actually browse
/// fell back to the disk copy and answered with LIFETIME numbers. Four is
/// `warmup_cycle`'s combo count: one hot entry per warmed window, so
/// "traders in bitcoin" means their bitcoin record on all of them.
const MEM_ENTRIES_MAX: usize = 4;

struct PipelineCacheEntry {
    /// Behind an `Arc` because serving a hit used to DEEP-CLONE this — ~2k
    /// traders with pnl curves and per-market metrics, tens of megabytes —
    /// and the console's leaderboard is one paged read off it per keystroke.
    /// The clone was the whole cost of a cache HIT (~330ms), on a payload the
    /// reader only ever reads. Sharing the pointer makes a hit a refcount
    /// bump; nothing mutates a cached payload in place, a rebuild replaces
    /// the whole entry.
    payload: Arc<AggPayload>,
    /// When this aggregate was computed — the TTL clock. Never bumped by a
    /// read, or a busy window would serve indefinitely stale traders.
    created_at: Instant,
    /// When it was last served — the EVICTION clock. Separate on purpose:
    /// staleness is about the data, residency is about who is asking. */
    last_access: Instant,
}

pub struct PipelineCache {
    entries: RwLock<HashMap<String, PipelineCacheEntry>>,
    disk_dir: PathBuf,
}

impl PipelineCache {
    pub fn new() -> Self {
        // Durable, NOT temp_dir(): this cache is what lets the board answer
        // instantly after a restart, and /tmp is wiped on reboot — a reboot
        // used to mean a cold leaderboard until the multi-minute warmup
        // finished. Lives beside the other per-deployment state so it
        // survives with the rest of it.
        let disk_dir = crate::access::state_dir().join("active-traders-cache");
        std::fs::create_dir_all(&disk_dir).ok();
        Self {
            entries: RwLock::new(HashMap::new()),
            disk_dir,
        }
    }

    /// A write lock for a read — it is what lets a read record itself for the
    /// eviction order below. Cheap now that the hit clones an `Arc` and not
    /// the payload: the critical section is a hash lookup and a refcount bump.
    pub fn get(&self, key: &str) -> Option<Arc<AggPayload>> {
        let mut entries = self.entries.write();
        if let Some(entry) = entries.get_mut(key) {
            if entry.created_at.elapsed() < AGG_TTL {
                entry.last_access = Instant::now();
                return Some(entry.payload.clone());
            }
        }
        None
    }

    pub fn get_or_disk(&self, key: &str) -> Option<(Arc<AggPayload>, &'static str)> {
        // Memory first
        if let Some(payload) = self.get(key) {
            return Some((payload, "memory"));
        }
        // Disk fallback
        if let Some(payload) = self.load_from_disk(key) {
            let payload = Arc::new(payload);
            let mut entries = self.entries.write();
            let now = Instant::now();
            entries.insert(key.to_string(), PipelineCacheEntry {
                payload: Arc::clone(&payload),
                created_at: now,
                last_access: now,
            });
            Self::evict_over_cap(&mut entries);
            return Some((payload, "disk"));
        }
        None
    }

    /// A payload of ANY age, for when the alternative is nothing at all.
    ///
    /// `get_or_disk` refuses anything past `DISK_MAX_AGE`, and a refusal here
    /// means a cold request has to rebuild the window from scratch — ~10
    /// minutes of upstream paging that a scale-to-zero stop will interrupt
    /// long before it finishes, leaving the console with a permanently empty
    /// board. A three-day-old 30D leaderboard is worse than a fresh one and
    /// far better than none, as long as the caller says so: this returns the
    /// payload's real `syncedAt` and the route labels the source `stale-disk`
    /// so the UI's sync-age chip tells the truth.
    pub fn get_stale_disk(&self, key: &str) -> Option<Arc<AggPayload>> {
        self.load_from_disk_max_age(key, Duration::from_secs(86_400 * 365))
            .map(Arc::new)
    }

    /// When this window was last actually rebuilt, memory or disk, regardless
    /// of whether that is fresh enough to serve. This is the warmup's
    /// stalest-first sort key — right after a restart, memory is empty, so
    /// asking `get` alone would call every window equally stale and re-fix
    /// the fixed order the sort exists to break.
    pub fn synced_at_any_age(&self, key: &str) -> Option<i64> {
        if let Some(entry) = self.entries.read().get(key) {
            return Some(entry.payload.synced_at);
        }
        let path = self.disk_path(key);
        let meta = std::fs::metadata(&path).ok()?;
        let modified = meta.modified().ok()?;
        modified
            .duration_since(std::time::UNIX_EPOCH)
            .ok()
            .map(|d| d.as_secs() as i64)
    }

    pub fn set(&self, key: &str, payload: AggPayload) {
        let payload = Arc::new(payload);
        // Memory
        let mut entries = self.entries.write();
        let now = Instant::now();
        entries.insert(key.to_string(), PipelineCacheEntry {
            payload: Arc::clone(&payload),
            created_at: now,
            last_access: now,
        });
        Self::evict_over_cap(&mut entries);
        drop(entries);
        // Disk (best-effort)
        self.save_to_disk(key, &payload);
    }

    /// Drop the least recently USED entries until the memory tier fits
    /// MEM_ENTRIES_MAX. Evicted windows reload from disk on the next request
    /// via get_or_disk — but WITHOUT their per-market breakdown, which is the
    /// difference between a market-filtered leaderboard showing a trader's
    /// bitcoin record and showing their lifetime one. So residency should
    /// follow what is being browsed: evicting by insertion order instead meant
    /// one off-cadence window (a `days=10` probe, a small-pool debug request)
    /// pushed out the window the console was actually reading, and it came
    /// back metric-less.
    fn evict_over_cap(entries: &mut HashMap<String, PipelineCacheEntry>) {
        while entries.len() > MEM_ENTRIES_MAX {
            let coldest = entries.iter()
                .min_by_key(|(_, v)| v.last_access)
                .map(|(k, _)| k.clone());
            match coldest {
                Some(k) => { entries.remove(&k); }
                None => break,
            }
        }
    }

    // pub(crate) so tests that write through the REAL cache dir (it is the
    // durable state dir now, not /tmp) can remove what they wrote.
    pub(crate) fn disk_path(&self, key: &str) -> PathBuf {
        let safe_key = key.replace(':', "_");
        self.disk_dir.join(format!("{}.json", safe_key))
    }

    /// The per-market breakdown, persisted BESIDE the payload.
    ///
    /// `Trader.market_metrics` is `#[serde(skip)]` so the leaderboard the
    /// browser receives stays small — but that also kept it off disk, and
    /// every API restart answered "traders in bitcoin" with LIFETIME numbers
    /// until the next warmup pass (up to an hour of a ranking that means
    /// something else). A sidecar file keeps the wire shape and survives the
    /// restart: address → metrics, re-attached on load.
    fn metrics_path(&self, key: &str) -> PathBuf {
        let mut p = self.disk_path(key);
        let name = p.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
        p.set_file_name(format!("{}.metrics.json", name));
        p
    }

    fn save_to_disk(&self, key: &str, payload: &AggPayload) {
        let path = self.disk_path(key);
        if let Ok(json) = serde_json::to_string(payload) {
            std::fs::write(path, json).ok();
        }
        let metrics: HashMap<&str, &Vec<MarketMetric>> = payload
            .traders
            .iter()
            .filter_map(|t| t.market_metrics.as_ref().map(|m| (t.address.as_str(), m)))
            .collect();
        let mpath = self.metrics_path(key);
        if metrics.is_empty() {
            std::fs::remove_file(mpath).ok();
        } else if let Ok(json) = serde_json::to_string(&metrics) {
            std::fs::write(mpath, json).ok();
        }
    }

    /// Re-attach the sidecar's breakdown to a payload loaded from disk. A
    /// missing or unreadable sidecar leaves the payload metric-less, which is
    /// exactly what it was before — the console detects that and says so.
    fn attach_metrics(&self, key: &str, payload: &mut AggPayload) {
        let raw = match std::fs::read_to_string(self.metrics_path(key)) {
            Ok(r) => r,
            Err(_) => return,
        };
        let mut metrics: HashMap<String, Vec<MarketMetric>> = match serde_json::from_str(&raw) {
            Ok(m) => m,
            Err(_) => return,
        };
        for t in payload.traders.iter_mut() {
            if let Some(m) = metrics.remove(&t.address) {
                t.market_metrics = Some(m);
            }
        }
    }

    fn load_from_disk(&self, key: &str) -> Option<AggPayload> {
        self.load_from_disk_max_age(key, DISK_MAX_AGE)
    }

    fn load_from_disk_max_age(&self, key: &str, max_age: Duration) -> Option<AggPayload> {
        let path = self.disk_path(key);
        if path.exists() {
            // Skip if file is older than max_age
            let mut mtime_secs: i64 = 0;
            if let Ok(meta) = std::fs::metadata(&path) {
                if let Ok(modified) = meta.modified() {
                    if modified.elapsed().unwrap_or(Duration::ZERO) > max_age {
                        return None;
                    }
                    if let Ok(dur) = modified.duration_since(std::time::UNIX_EPOCH) {
                        mtime_secs = dur.as_secs() as i64;
                    }
                }
            }
            let data = std::fs::read_to_string(&path).ok()?;
            let mut payload: AggPayload = serde_json::from_str(&data).ok()?;
            self.attach_metrics(key, &mut payload);
            // Old disk payloads (before synced_at existed) deserialize with
            // synced_at=0. Fall back to the file's mtime so the client still
            // sees a sensible "last sync" instead of 1970.
            if payload.synced_at == 0 {
                payload.synced_at = mtime_secs;
            }
            // Payloads written before the Sharpe epsilon guard carry
            // degenerate values (float-noise stdev on identical returns).
            // Zero them so a stale disk cache can't put junk traders on
            // top of the sharpe-sorted leaderboard until the next warmup
            // recompute. Two tests: the crude magnitude bound, and the
            // guard's own stdev threshold reconstructed from what the
            // payload keeps (exit_entry = 1 + roi, so stdev = roi/sharpe)
            // — that's what catches a pre-1e-4-guard trader whose sharpe
            // (e.g. 8e5) slid under the 1e6 bound.
            for t in &mut payload.traders {
                if !t.sharpe.is_finite() || t.sharpe.abs() > 1e6 {
                    t.sharpe = 0.0;
                } else if t.sharpe != 0.0 && t.exit_entry >= 0.0
                    && ((t.exit_entry - 1.0) / t.sharpe).abs() <= 1e-4
                {
                    t.sharpe = 0.0;
                }
            }
            Some(payload)
        } else {
            None
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::types::AggPayload;

    fn payload(days: u32) -> AggPayload {
        AggPayload {
            count: 0,
            candidate_pool: 2000,
            days_window: days,
            min_trades_per_day: 0.0,
            synced_at: 0,
            traders: vec![],
        }
    }

    /// The memory tier has to hold every WARMED window, because the per-market
    /// breakdown a market filter needs exists only there. So a fifth key must
    /// evict whichever window nobody is reading — not the one being read.
    #[test]
    fn eviction_drops_the_least_recently_read_window() {
        let cache = PipelineCache::new();
        // Unique keys: the disk tier is a shared temp dir, and a bare "1:0:2000"
        // would clobber the real cache of whatever is running on this box.
        let k = |n: u32| format!("test_lru_{}_{}", std::process::id(), n);

        for n in 1..=4 {
            cache.set(&k(n), payload(n));
        }
        // Read 1 — it is now the most recently used, and 2 the least.
        assert!(cache.get(&k(1)).is_some());

        cache.set(&k(5), payload(5));

        assert!(cache.get(&k(1)).is_some(), "the window being read was evicted");
        assert!(cache.get(&k(2)).is_none(), "the coldest window should have gone");
        assert!(cache.get(&k(5)).is_some());

        for n in 1..=5 {
            std::fs::remove_file(cache.disk_path(&k(n))).ok();
        }
    }
}
