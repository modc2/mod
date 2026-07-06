use dashmap::DashMap;
use std::sync::Arc;

use crate::cache::DiskCache;
use crate::models::trader::TraderResult;

#[derive(Clone)]
pub struct CacheEntry {
    pub data: Vec<TraderResult>,
    pub created_at: i64,
}

pub struct AppState {
    pub http: reqwest::Client,
    pub memory_cache: DashMap<String, CacheEntry>,
    pub disk_cache: DiskCache,
}

impl AppState {
    pub fn new() -> Self {
        Self {
            http: reqwest::Client::builder()
                .timeout(std::time::Duration::from_secs(30))
                .build()
                .unwrap(),
            memory_cache: DashMap::new(),
            disk_cache: DiskCache::new(&format!(
                "{}/.mod/uniswap/cache.db",
                std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string())
            )),
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
            } else {
                drop(entry);
                self.memory_cache.remove(key);
            }
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
}
