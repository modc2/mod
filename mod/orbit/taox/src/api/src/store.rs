use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use parking_lot::Mutex;

use crate::types::Order;

/// File-backed JSON store for orders. Mirrors the layout of `~/.taox/orders.json`
/// used by the Python implementation, so the two can share state during migration.
pub struct OrderStore {
    path: PathBuf,
    inner: Mutex<BTreeMap<String, Order>>,
}

impl OrderStore {
    pub fn open(dir: &Path) -> anyhow::Result<Self> {
        std::fs::create_dir_all(dir)?;
        let path = dir.join("orders.json");
        let inner = if path.exists() {
            let txt = std::fs::read_to_string(&path)?;
            if txt.trim().is_empty() {
                BTreeMap::new()
            } else {
                serde_json::from_str(&txt).unwrap_or_default()
            }
        } else {
            BTreeMap::new()
        };
        Ok(OrderStore { path, inner: Mutex::new(inner) })
    }

    fn flush_locked(&self, map: &BTreeMap<String, Order>) -> anyhow::Result<()> {
        let tmp = self.path.with_extension("json.tmp");
        let body = serde_json::to_string_pretty(map)?;
        std::fs::write(&tmp, body)?;
        std::fs::rename(&tmp, &self.path)?;
        Ok(())
    }

    pub fn insert(&self, order: Order) -> anyhow::Result<Order> {
        let mut g = self.inner.lock();
        g.insert(order.id.clone(), order.clone());
        self.flush_locked(&g)?;
        Ok(order)
    }

    pub fn get(&self, id: &str) -> Option<Order> {
        self.inner.lock().get(id).cloned()
    }

    pub fn list(&self) -> Vec<Order> {
        self.inner.lock().values().cloned().collect()
    }

    /// Mutate an order in place under the lock, persisting on success.
    pub fn update<F>(&self, id: &str, mut f: F) -> anyhow::Result<Option<Order>>
    where
        F: FnMut(&mut Order) -> Result<(), String>,
    {
        let mut g = self.inner.lock();
        let Some(order) = g.get_mut(id) else { return Ok(None) };
        if let Err(e) = f(order) {
            return Err(anyhow::anyhow!(e));
        }
        let snapshot = order.clone();
        self.flush_locked(&g)?;
        Ok(Some(snapshot))
    }
}
