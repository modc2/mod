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
    /// Inline secret, encrypted client-side (legacy signature-derived key).
    /// Empty when `key_id` points at a vault key instead.
    #[serde(default)]
    pub ciphertext: String,
    #[serde(default)]
    pub iv: String,
    /// Reference into the wallet's key vault; the client decrypts that key
    /// with its vault passphrase at test/exec time.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub key_id: Option<String>,
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
    #[serde(skip_serializing_if = "Option::is_none")]
    pub key_id: Option<String>,
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
            key_id: c.key_id.clone(),
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

// ── key vault ─────────────────────────────────────────────────────────────
//
// Named SSH secrets (passwords / private keys), reusable across connections.
// Everything sensitive arrives already encrypted: the client derives an
// AES-GCM key from a vault passphrase (PBKDF2) that never leaves the browser,
// so — unlike the legacy signature-derived scheme, whose signature travels in
// the X-Mod-Auth header — the server cannot reconstruct the key material.
// `VaultMeta` holds the public KDF parameters plus a verifier ciphertext the
// client uses to detect a wrong passphrase before touching real keys.

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct KeyEntry {
    pub id: String,
    pub name: String,
    pub kind: String, // "password" | "key"
    pub ciphertext: String,
    pub iv: String,
    /// OpenSSH public key — plaintext on purpose (it's public), so it can be
    /// listed, copied, and QR-shared without unlocking the vault.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub public_key: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub fingerprint: Option<String>,
    pub created_at: i64,
    pub updated_at: i64,
}

/// Trimmed view for /keys (list) — strips ciphertext.
#[derive(Debug, Clone, Serialize)]
pub struct KeyEntryPublic {
    pub id: String,
    pub name: String,
    pub kind: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub public_key: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub fingerprint: Option<String>,
    pub created_at: i64,
    pub updated_at: i64,
}

impl From<&KeyEntry> for KeyEntryPublic {
    fn from(k: &KeyEntry) -> Self {
        Self {
            id: k.id.clone(),
            name: k.name.clone(),
            kind: k.kind.clone(),
            public_key: k.public_key.clone(),
            fingerprint: k.fingerprint.clone(),
            created_at: k.created_at,
            updated_at: k.updated_at,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct VaultMeta {
    pub salt: String, // base64, random per vault
    pub verifier_ct: String,
    pub verifier_iv: String,
    #[serde(default = "default_kdf_iters")]
    pub kdf_iters: u32,
    #[serde(default)]
    pub updated_at: i64,
}

pub fn default_kdf_iters() -> u32 {
    310_000
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct KeyBucket {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub vault: Option<VaultMeta>,
    #[serde(default)]
    pub keys: BTreeMap<String, KeyEntry>,
}

pub struct KeyStore {
    path: PathBuf,
    inner: Mutex<BTreeMap<String, KeyBucket>>,
}

impl KeyStore {
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

    fn persist(&self, snap: &BTreeMap<String, KeyBucket>) -> Result<()> {
        if let Some(parent) = self.path.parent() {
            std::fs::create_dir_all(parent).ok();
        }
        let tmp = self.path.with_extension("json.tmp");
        std::fs::write(&tmp, serde_json::to_string_pretty(snap)?)?;
        std::fs::rename(tmp, &self.path)?;
        Ok(())
    }

    pub fn vault(&self, wallet: &str) -> Option<VaultMeta> {
        let guard = self.inner.lock();
        guard.get(wallet).and_then(|b| b.vault.clone())
    }

    /// Set or rotate the vault. On rotation the client re-encrypts every key
    /// under the new passphrase and sends the full set; passing `Some(keys)`
    /// replaces the bucket's keys atomically with the vault metadata.
    pub fn set_vault(
        &self,
        wallet: &str,
        mut vault: VaultMeta,
        keys: Option<Vec<KeyEntry>>,
    ) -> Result<VaultMeta> {
        let now = chrono::Utc::now().timestamp();
        vault.updated_at = now;
        let mut guard = self.inner.lock();
        let bucket = guard.entry(wallet.to_string()).or_default();
        bucket.vault = Some(vault.clone());
        if let Some(new_keys) = keys {
            let prior = std::mem::take(&mut bucket.keys);
            for mut k in new_keys {
                if let Some(p) = prior.get(&k.id) {
                    k.created_at = p.created_at;
                } else if k.created_at == 0 {
                    k.created_at = now;
                }
                k.updated_at = now;
                bucket.keys.insert(k.id.clone(), k);
            }
        }
        let snap = guard.clone();
        drop(guard);
        self.persist(&snap)?;
        Ok(vault)
    }

    pub fn list_keys(&self, wallet: &str) -> Vec<KeyEntryPublic> {
        let guard = self.inner.lock();
        guard
            .get(wallet)
            .map(|b| b.keys.values().map(KeyEntryPublic::from).collect())
            .unwrap_or_default()
    }

    pub fn key_count(&self, wallet: &str) -> usize {
        let guard = self.inner.lock();
        guard.get(wallet).map(|b| b.keys.len()).unwrap_or(0)
    }

    pub fn get_key(&self, wallet: &str, id: &str) -> Option<KeyEntry> {
        let guard = self.inner.lock();
        guard.get(wallet).and_then(|b| b.keys.get(id)).cloned()
    }

    pub fn upsert_key(&self, wallet: &str, mut key: KeyEntry) -> Result<KeyEntry> {
        let now = chrono::Utc::now().timestamp();
        let mut guard = self.inner.lock();
        let bucket = guard.entry(wallet.to_string()).or_default();
        if let Some(prior) = bucket.keys.get(&key.id) {
            key.created_at = prior.created_at;
        } else {
            key.created_at = now;
        }
        key.updated_at = now;
        bucket.keys.insert(key.id.clone(), key.clone());
        let snap = guard.clone();
        drop(guard);
        self.persist(&snap)?;
        Ok(key)
    }

    pub fn remove_key(&self, wallet: &str, id: &str) -> Result<bool> {
        let mut guard = self.inner.lock();
        let bucket = match guard.get_mut(wallet) {
            Some(b) => b,
            None => return Ok(false),
        };
        let removed = bucket.keys.remove(id).is_some();
        if bucket.keys.is_empty() && bucket.vault.is_none() {
            guard.remove(wallet);
        }
        let snap = guard.clone();
        drop(guard);
        self.persist(&snap)?;
        Ok(removed)
    }

    pub fn counts(&self) -> (usize, usize) {
        let guard = self.inner.lock();
        let vaults = guard.values().filter(|b| b.vault.is_some()).count();
        let keys = guard.values().map(|b| b.keys.len()).sum();
        (vaults, keys)
    }
}
