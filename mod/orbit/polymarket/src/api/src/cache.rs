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
            if let Some(data) = self.load_from_disk(key) {
                // Re-populate memory with a long TTL
                let mut entries = self.entries.write();
                if entries.len() >= self.max_entries {
                    self.evict_one(&mut entries);
                }
                // Match the same freshness rule the writer uses — disk-loaded
                // trader entries also expire after 1h so they re-fetch.
                let ttl = if Self::is_freshness_critical(endpoint) {
                    Duration::from_secs(60)
                } else {
                    Duration::from_secs(86400)
                };
                entries.insert(key.to_string(), CacheEntry {
                    data: data.clone(),
                    inserted_at: Instant::now(),
                    ttl,
                });
                return Some((data, true));
            }
        }
        None
    }

    /// Trader trade/activity endpoints persist to disk (survive restarts) but
    /// memory-cache for only 1 hour, not 24h. Without this an entry written at
    /// 9 AM serves stale "no recent trades" responses until 9 AM the next day,
    /// which hides newly-active traders from the leaderboard. Markets/holders/
    /// historical data still cache for 24h — those don't churn meaningfully.
    fn is_freshness_critical(endpoint: &str) -> bool {
        let ep = endpoint.to_lowercase();
        ep.starts_with("activity") || ep.starts_with("trades")
    }

    pub fn set(&self, key: String, data: Value, ttl: Duration, endpoint: &str) {
        let persist = Self::is_persistent(endpoint);
        // Trader trade/activity entries get a 1-hour memory TTL so the next
        // warmup tick re-fetches them — without this, a 9 AM cache hides
        // dormant→active transitions until 9 AM the next day.
        let mem_ttl = if Self::is_freshness_critical(endpoint) {
            Duration::from_secs(60)
        } else if persist {
            Duration::from_secs(86400)
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
        if Self::is_persistent(&ep) {
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

    fn load_from_disk(&self, key: &str) -> Option<Value> {
        let path = self.disk_path(key);
        if !path.exists() { return None; }
        // Freshness gate: trader activity/trades on disk older than 1h are
        // considered stale. Without this, a 48h-old cache file gets loaded
        // as if fresh and propagates "0 trades in last 24h" everywhere.
        // The endpoint prefix lives in the filename (proxy_endpoint_<ep>_…).
        let name = path.file_name().and_then(|n| n.to_str()).unwrap_or("");
        let is_freshness_critical = name.contains("endpoint_activity")
            || name.contains("endpoint_trades");
        if is_freshness_critical {
            if let Ok(meta) = std::fs::metadata(&path) {
                if let Ok(modified) = meta.modified() {
                    if modified.elapsed().unwrap_or(Duration::ZERO) > Duration::from_secs(60) {
                        // Delete the stale file so next save_to_disk doesn't
                        // bump mtime on a stale payload by accident.
                        let _ = std::fs::remove_file(&path);
                        return None;
                    }
                }
            }
        }
        let raw = std::fs::read_to_string(&path).ok()?;
        serde_json::from_str(&raw).ok()
    }
}

// ─── Pipeline Cache (Memory + Disk, hourly) ───

// 1 hour — matches the hourly warmup cadence in main.rs. The aggregated
// leaderboard (30d trader Sharpe/ROI) is slow-moving, so serving it from
// cache for an hour avoids re-pulling ~6k traders from the data-api on every
// on-demand request (the old 60s TTL was a major 429 source).
const AGG_TTL: Duration = Duration::from_secs(3600);
const DISK_MAX_AGE: Duration = Duration::from_secs(86400); // 24h — keep disk cache across restarts

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
        drop(entries);
        // Disk (best-effort)
        self.save_to_disk(key, &payload);
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
            Some(payload)
        } else {
            None
        }
    }
}
