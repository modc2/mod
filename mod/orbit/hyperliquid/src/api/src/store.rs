// Persistent (file-backed) store for user-defined indexes, copy-trade
// follows, and vault metadata. JSON-on-disk for portability.

use parking_lot::RwLock;
use serde::{Deserialize, Serialize};
use std::path::PathBuf;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct IndexLeg {
    pub address: String,
    pub weight: f64,        // 0..1, normalised across legs
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Index {
    pub id: String,
    pub name: String,
    pub owner: String,                  // creator wallet
    #[serde(default)]
    pub description: String,
    pub legs: Vec<IndexLeg>,
    #[serde(default)]
    pub days_window: u32,               // window used to score legs
    #[serde(default)]
    pub created_ms: i64,
    #[serde(default)]
    pub vault_address: Option<String>,  // HL vault tied to the index, if any
    #[serde(default)]
    pub max_leverage: f64,              // 0 = no cap
    #[serde(default)]
    pub notional_pct: f64,              // % of vault to deploy per signal (0..100)
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Follow {
    pub id: String,
    pub follower: String,               // copying account
    pub leader: String,                 // trader being copied
    pub size_pct: f64,                  // % of follower equity per leader fill
    #[serde(default)]
    pub max_per_trade_usd: f64,         // hard cap per copy
    #[serde(default)]
    pub coins_allow: Vec<String>,       // empty = all
    #[serde(default)]
    pub coins_deny: Vec<String>,
    #[serde(default)]
    pub created_ms: i64,
    #[serde(default)]
    pub last_seen_tid: u64,             // dedup cursor
    #[serde(default)]
    pub paused: bool,
    #[serde(default)]
    pub vault_address: Option<String>,  // optional: route fills through a vault
}

#[derive(Debug, Default, Serialize, Deserialize)]
pub struct State {
    #[serde(default)]
    pub indexes: Vec<Index>,
    #[serde(default)]
    pub follows: Vec<Follow>,
}

pub struct Store {
    path: PathBuf,
    inner: RwLock<State>,
}

impl Store {
    pub fn load(dir: &str) -> anyhow::Result<Self> {
        let path = PathBuf::from(dir).join("state.json");
        let inner = if path.exists() {
            let s = std::fs::read_to_string(&path)?;
            serde_json::from_str(&s).unwrap_or_default()
        } else {
            State::default()
        };
        Ok(Self { path, inner: RwLock::new(inner) })
    }

    fn flush(&self) -> anyhow::Result<()> {
        let s = serde_json::to_string_pretty(&*self.inner.read())?;
        std::fs::write(&self.path, s)?;
        Ok(())
    }

    // ── Indexes ──

    pub fn list_indexes(&self) -> Vec<Index> { self.inner.read().indexes.clone() }

    pub fn get_index(&self, id: &str) -> Option<Index> {
        self.inner.read().indexes.iter().find(|i| i.id == id).cloned()
    }

    pub fn upsert_index(&self, mut idx: Index) -> anyhow::Result<Index> {
        normalize_legs(&mut idx.legs);
        if idx.id.is_empty() { idx.id = uuid::Uuid::new_v4().to_string(); }
        if idx.created_ms == 0 { idx.created_ms = chrono::Utc::now().timestamp_millis(); }
        let mut g = self.inner.write();
        if let Some(slot) = g.indexes.iter_mut().find(|i| i.id == idx.id) {
            *slot = idx.clone();
        } else {
            g.indexes.push(idx.clone());
        }
        drop(g);
        self.flush()?;
        Ok(idx)
    }

    pub fn delete_index(&self, id: &str) -> anyhow::Result<bool> {
        let mut g = self.inner.write();
        let n = g.indexes.len();
        g.indexes.retain(|i| i.id != id);
        let removed = g.indexes.len() != n;
        drop(g);
        if removed { self.flush()?; }
        Ok(removed)
    }

    // ── Follows ──

    pub fn list_follows(&self, follower: Option<&str>) -> Vec<Follow> {
        let g = self.inner.read();
        match follower {
            Some(f) => g.follows.iter().filter(|x| x.follower.eq_ignore_ascii_case(f)).cloned().collect(),
            None => g.follows.clone(),
        }
    }

    pub fn upsert_follow(&self, mut f: Follow) -> anyhow::Result<Follow> {
        if f.id.is_empty() { f.id = uuid::Uuid::new_v4().to_string(); }
        if f.created_ms == 0 { f.created_ms = chrono::Utc::now().timestamp_millis(); }
        let mut g = self.inner.write();
        if let Some(slot) = g.follows.iter_mut().find(|x| x.id == f.id) {
            *slot = f.clone();
        } else {
            g.follows.push(f.clone());
        }
        drop(g);
        self.flush()?;
        Ok(f)
    }

    pub fn delete_follow(&self, id: &str) -> anyhow::Result<bool> {
        let mut g = self.inner.write();
        let n = g.follows.len();
        g.follows.retain(|x| x.id != id);
        let removed = g.follows.len() != n;
        drop(g);
        if removed { self.flush()?; }
        Ok(removed)
    }

    pub fn update_follow_cursor(&self, id: &str, last_tid: u64) -> anyhow::Result<()> {
        {
            let mut g = self.inner.write();
            if let Some(f) = g.follows.iter_mut().find(|x| x.id == id) {
                f.last_seen_tid = f.last_seen_tid.max(last_tid);
            }
        }
        self.flush()
    }
}

fn normalize_legs(legs: &mut [IndexLeg]) {
    let total: f64 = legs.iter().map(|l| l.weight.max(0.0)).sum();
    if total <= 0.0 {
        let n = legs.len() as f64;
        if n > 0.0 { for l in legs.iter_mut() { l.weight = 1.0 / n; } }
        return;
    }
    for l in legs.iter_mut() { l.weight = l.weight.max(0.0) / total; }
}
