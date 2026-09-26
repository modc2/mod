use dashmap::DashMap;
use std::sync::Arc;

use crate::cache::DiskCache;
use crate::models::trader::TraderResult;
use crate::pipeline::logs::Coverage;
use crate::pipeline::meta::{PoolMeta, TokenMeta};
use std::collections::HashMap;

#[derive(Clone)]
pub struct CacheEntry {
    pub data: Vec<TraderResult>,
    pub created_at: i64,
}

#[derive(Clone, Copy)]
struct PriceEntry {
    usd: f64,
    at: i64,
}

pub struct AppState {
    pub http: reqwest::Client,
    pub memory_cache: DashMap<String, CacheEntry>,
    pub disk_cache: DiskCache,
    /// Pool address -> resolved tokens/decimals/fee. Immutable on chain, so
    /// this is read once per pool per process.
    pub pool_meta: DashMap<String, PoolMeta>,
    /// "chain:token" -> symbol/decimals, so one ERC-20 is never resolved twice
    /// and can never come back under two different names.
    pub token_meta: DashMap<String, TokenMeta>,
    /// chain -> the discovered pool set that chain's scrapes sample.
    pub chain_pools: DashMap<String, HashMap<String, PoolMeta>>,
    /// "chain:address" -> holds code. Code at an address does not come and go,
    /// so this never expires.
    pub is_contract: DashMap<String, bool>,
    /// How much of the window each cached result actually sampled.
    pub coverage: DashMap<String, Coverage>,
    /// chain -> ETH price in USD, with the time it was read.
    eth_price: DashMap<String, PriceEntry>,
    pub started_at: i64,
}

/// ETH price is re-read this often. Swap USD values are reported to the
/// dollar, not the cent, so minutes of staleness is invisible.
const PRICE_TTL: i64 = 300;

impl AppState {
    pub fn new() -> Self {
        Self {
            http: reqwest::Client::builder()
                .timeout(std::time::Duration::from_secs(30))
                .user_agent("mod-uniswap/0.2")
                .build()
                .unwrap(),
            memory_cache: DashMap::new(),
            disk_cache: DiskCache::new(&format!(
                "{}/.mod/uniswap/cache.db",
                std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string())
            )),
            pool_meta: DashMap::new(),
            token_meta: DashMap::new(),
            chain_pools: DashMap::new(),
            is_contract: DashMap::new(),
            coverage: DashMap::new(),
            eth_price: DashMap::new(),
            started_at: chrono::Utc::now().timestamp(),
        }
    }

    pub fn cache_key(chain: &str, days: u32, pool: u32) -> String {
        format!("{chain}:{days}:{pool}")
    }

    pub fn get_cached(&self, key: &str) -> Option<Vec<TraderResult>> {
        let ttl = 3600; // 1 hour
        let now = chrono::Utc::now().timestamp();

        // Check memory first
        if let Some(entry) = self.memory_cache.get(key) {
            if now - entry.created_at < ttl {
                return Some(entry.data.clone());
            }
            drop(entry);
            self.memory_cache.remove(key);
        }

        // Check disk
        if let Some(data) = self.disk_cache.get(key, ttl * 4) {
            // Promote to memory
            self.memory_cache.insert(
                key.to_string(),
                CacheEntry {
                    data: data.clone(),
                    created_at: now,
                },
            );
            return Some(data);
        }

        None
    }

    pub fn set_cached(&self, key: &str, data: &[TraderResult]) {
        let now = chrono::Utc::now().timestamp();
        self.memory_cache.insert(
            key.to_string(),
            CacheEntry {
                data: data.to_vec(),
                created_at: now,
            },
        );
        self.disk_cache.set(key, data);
    }

    /// Drop a cached window so the next request re-scrapes it.
    pub fn invalidate(&self, key: &str) {
        self.memory_cache.remove(key);
        self.coverage.remove(key);
        self.disk_cache.remove(key);
    }

    pub fn set_coverage(&self, key: &str, coverage: Coverage) {
        self.coverage.insert(key.to_string(), coverage);
    }

    pub fn get_coverage(&self, key: &str) -> Option<Coverage> {
        self.coverage.get(key).map(|c| *c)
    }

    pub fn get_eth_price(&self, chain: &str) -> Option<f64> {
        let now = chrono::Utc::now().timestamp();
        let entry = self.eth_price.get(chain)?;
        (now - entry.at < PRICE_TTL).then_some(entry.usd)
    }

    pub fn set_eth_price(&self, chain: &str, usd: f64) {
        self.eth_price.insert(
            chain.to_string(),
            PriceEntry {
                usd,
                at: chrono::Utc::now().timestamp(),
            },
        );
    }
}

impl Default for AppState {
    fn default() -> Self {
        Self::new()
    }
}

pub type SharedState = Arc<AppState>;
