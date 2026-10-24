//! A TTL'd, bounded cache shared by searches and READMEs.
//!
//! At ten anonymous GitHub calls a minute, the cache is what makes iterating on
//! a query practical instead of a rate-limit game. It is kept in memory and
//! mirrored to `~/.mod/github/cache.json` so a restart starts warm; the mirror
//! is written on a debounce rather than on every insert, because ranking one
//! search touches it a few dozen times.

use std::collections::HashMap;

use parking_lot::Mutex;
use serde::{Deserialize, Serialize};

pub const SEARCH_TTL: f64 = 900.0;
pub const README_TTL: f64 = 86_400.0;
const MAX_ENTRIES: usize = 400;
const FLUSH_EVERY: f64 = 10.0;

#[derive(Debug, Clone, Serialize, Deserialize)]
struct Entry {
    t: f64,
    v: serde_json::Value,
}

#[derive(Default, Serialize, Deserialize)]
struct Disk(HashMap<String, Entry>);

pub struct Cache {
    inner: Mutex<HashMap<String, Entry>>,
    dirty: Mutex<f64>,
}

impl Cache {
    pub fn load() -> Self {
        let disk: Disk = crate::store::read(&crate::store::path("cache.json"));
        Self { inner: Mutex::new(disk.0), dirty: Mutex::new(0.0) }
    }

    pub fn get(&self, key: &str, ttl: f64) -> Option<serde_json::Value> {
        let map = self.inner.lock();
        map.get(key).filter(|e| crate::store::now() - e.t < ttl).map(|e| e.v.clone())
    }

    pub fn put(&self, key: &str, value: serde_json::Value) {
        {
            let mut map = self.inner.lock();
            map.insert(key.to_string(), Entry { t: crate::store::now(), v: value });
            if map.len() > MAX_ENTRIES {
                let mut by_age: Vec<(String, f64)> =
                    map.iter().map(|(k, e)| (k.clone(), e.t)).collect();
                by_age.sort_by(|a, b| a.1.total_cmp(&b.1));
                for (k, _) in by_age.iter().take(map.len() - MAX_ENTRIES) {
                    map.remove(k);
                }
            }
        }
        self.maybe_flush();
    }

    fn maybe_flush(&self) {
        let now = crate::store::now();
        {
            let mut last = self.dirty.lock();
            if now - *last < FLUSH_EVERY {
                return;
            }
            *last = now;
        }
        self.flush();
    }

    pub fn flush(&self) {
        let snapshot = Disk(self.inner.lock().clone());
        let _ = crate::store::write(&crate::store::path("cache.json"), &snapshot, false);
    }

    pub fn clear(&self) -> usize {
        let n = {
            let mut map = self.inner.lock();
            let n = map.len();
            map.clear();
            n
        };
        self.flush();
        n
    }

    /// What is warm — keys and ages only. The cached bodies are never returned
    /// by the endpoint: a private repo's README can be in here.
    pub fn summary(&self, top: usize) -> (usize, Vec<serde_json::Value>) {
        let map = self.inner.lock();
        let now = crate::store::now();
        let mut rows: Vec<(&String, &Entry)> = map.iter().collect();
        rows.sort_by(|a, b| b.1.t.total_cmp(&a.1.t));
        let keys = rows
            .iter()
            .take(top)
            .map(|(k, e)| serde_json::json!({ "key": k, "age": (now - e.t).round() }))
            .collect();
        (map.len(), keys)
    }
}
