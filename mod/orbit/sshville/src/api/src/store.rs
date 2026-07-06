//! JSON-file-backed connection store. Bucketed by wallet identity, mutex-guarded
//! for the in-process write side; reads return cloned snapshots so handlers can
//! work on owned data without holding the lock.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use anyhow::Result;
use parking_lot::Mutex;
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Connection {
    pub id: String,
    pub name: String,
    pub host: String,
    pub port: u16,
    pub user: String,
    pub auth_type: String, // "password" | "key"
    pub ciphertext: String,
    pub iv: String,
    pub created_at: i64,
    pub updated_at: i64,
}

/// A trimmed Connection view used by /connections (list) — strips ciphertext.
#[derive(Debug, Clone, Serialize)]
pub struct ConnectionPublic {
    pub id: String,
    pub name: String,
    pub host: String,
    pub port: u16,
    pub user: String,
    pub auth_type: String,
    pub created_at: i64,
    pub updated_at: i64,
}

impl From<&Connection> for ConnectionPublic {
    fn from(c: &Connection) -> Self {
        Self {
            id: c.id.clone(),
            name: c.name.clone(),
            host: c.host.clone(),
            port: c.port,
            user: c.user.clone(),
            auth_type: c.auth_type.clone(),
            created_at: c.created_at,
            updated_at: c.updated_at,
        }
    }
}

pub struct Store {
    path: PathBuf,
    inner: Mutex<BTreeMap<String, BTreeMap<String, Connection>>>,
}

impl Store {
    pub fn open(path: PathBuf) -> Result<Self> {
        let inner = if path.exists() {
            let text = std::fs::read_to_string(&path)?;
            if text.trim().is_empty() {
                BTreeMap::new()
            } else {
                serde_json::from_str(&text)?
            }
        } else {
            BTreeMap::new()
        };
        Ok(Self {
            path,
            inner: Mutex::new(inner),
        })
    }

    fn persist(&self, snap: &BTreeMap<String, BTreeMap<String, Connection>>) -> Result<()> {
        if let Some(parent) = self.path.parent() {
            std::fs::create_dir_all(parent).ok();
        }
        let tmp = self.path.with_extension("json.tmp");
        std::fs::write(&tmp, serde_json::to_string_pretty(snap)?)?;
        std::fs::rename(tmp, &self.path)?;
        Ok(())
    }

    pub fn list(&self, wallet: &str) -> Vec<ConnectionPublic> {
        let guard = self.inner.lock();
        guard
            .get(wallet)
            .map(|m| m.values().map(ConnectionPublic::from).collect())
            .unwrap_or_default()
    }

    pub fn get(&self, wallet: &str, id: &str) -> Option<Connection> {
        let guard = self.inner.lock();
        guard.get(wallet).and_then(|m| m.get(id)).cloned()
    }

    pub fn upsert(&self, wallet: &str, mut conn: Connection) -> Result<Connection> {
        let now = chrono::Utc::now().timestamp();
        let mut guard = self.inner.lock();
        let bucket = guard.entry(wallet.to_string()).or_default();
        if let Some(prior) = bucket.get(&conn.id) {
            conn.created_at = prior.created_at;
        } else {
            conn.created_at = now;
        }
        conn.updated_at = now;
        bucket.insert(conn.id.clone(), conn.clone());
        let snap = guard.clone();
        drop(guard);
        self.persist(&snap)?;
        Ok(conn)
    }

    pub fn remove(&self, wallet: &str, id: &str) -> Result<bool> {
        let mut guard = self.inner.lock();
        let bucket = match guard.get_mut(wallet) {
            Some(b) => b,
            None => return Ok(false),
        };
        let removed = bucket.remove(id).is_some();
        if bucket.is_empty() {
            guard.remove(wallet);
        }
        let snap = guard.clone();
        drop(guard);
        self.persist(&snap)?;
        Ok(removed)
    }

    pub fn counts(&self) -> (usize, usize) {
        let guard = self.inner.lock();
        let wallets = guard.len();
        let conns = guard.values().map(|m| m.len()).sum();
        (wallets, conns)
    }

    pub fn store_path(&self) -> &Path {
        &self.path
    }
}
