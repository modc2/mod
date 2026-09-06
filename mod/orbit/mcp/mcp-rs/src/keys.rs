//! Hub API keys — the credential an MCP *client* can hold.
//!
//! A wallet session is the right identity for a browser and the wrong one for
//! Claude Code: it expires in 24h and there is no wallet inside a config file.
//! So the owner mints a key here, pastes it into the client's Authorization
//! header, and that client can execute tools until the key is revoked.
//!
//! A key buys tool *calls* and nothing else — never registry edits, never key
//! management. Only the sha256 of a key is stored, so a leaked keys.json can't
//! be replayed, and the plaintext is shown exactly once, at mint time.

use crate::store;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::io::Read;
use std::path::PathBuf;
use std::sync::Mutex;

/// Every hub key starts with this, so a key is never mistaken for a wallet
/// session token (which is `address:timestamp:hmac`).
pub const KEY_PREFIX: &str = "mcphub_";

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct KeyRecord {
    pub id: String,
    pub name: String,
    /// sha256(key) hex — the key itself is never written down.
    pub hash: String,
    /// First few characters of the key, so the owner can tell rows apart.
    #[serde(default)]
    pub hint: String,
    #[serde(default)]
    pub created: u64,
    #[serde(default)]
    pub created_by: String,
    #[serde(default)]
    pub last_used: u64,
    #[serde(default)]
    pub calls: u64,
}

impl KeyRecord {
    /// The row as the console sees it — hash withheld.
    pub fn public(&self) -> Value {
        json!({
            "id": self.id,
            "name": self.name,
            "hint": self.hint,
            "created": self.created,
            "created_by": self.created_by,
            "last_used": self.last_used,
            "calls": self.calls,
        })
    }
}

fn path() -> PathBuf {
    store::hub_dir().join("keys.json")
}

/// Serialize read-modify-write cycles: two concurrent tool calls both bumping
/// `last_used` would otherwise race the file back to an older copy.
fn lock() -> &'static Mutex<()> {
    static LOCK: Mutex<()> = Mutex::new(());
    &LOCK
}

fn read() -> Vec<KeyRecord> {
    std::fs::read_to_string(path())
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or_default()
}

fn write(keys: &[KeyRecord]) {
    let p = path();
    if let Some(dir) = p.parent() {
        let _ = std::fs::create_dir_all(dir);
    }
    let Ok(s) = serde_json::to_string_pretty(keys) else { return };
    let tmp = p.with_extension("tmp");
    if std::fs::write(&tmp, s).is_ok() {
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let _ = std::fs::set_permissions(&tmp, std::fs::Permissions::from_mode(0o600));
        }
        let _ = std::fs::rename(&tmp, &p);
    }
}

fn random_hex(bytes: usize) -> String {
    let mut buf = vec![0u8; bytes];
    if std::fs::File::open("/dev/urandom")
        .and_then(|mut f| f.read_exact(&mut buf))
        .is_err()
    {
        // Never expected on Linux; a time-seeded fallback still beats panicking.
        let seed = store::now().wrapping_mul(0x9e3779b97f4a7c15);
        for (i, b) in buf.iter_mut().enumerate() {
            *b = ((seed >> ((i % 8) * 8)) ^ (i as u64)) as u8;
        }
    }
    hex::encode(buf)
}

fn sha256_hex(s: &str) -> String {
    let mut h = Sha256::new();
    h.update(s.as_bytes());
    hex::encode(h.finalize())
}

pub fn list() -> Vec<KeyRecord> {
    let _g = lock().lock();
    read()
}

/// Mint a key. Returns (record, plaintext) — the plaintext is the only copy
/// that will ever exist, so the caller must hand it straight to the owner.
pub fn create(name: &str, by: &str) -> (KeyRecord, String) {
    let _g = lock().lock();
    let plaintext = format!("{KEY_PREFIX}{}", random_hex(24));
    let rec = KeyRecord {
        id: random_hex(6),
        name: name.trim().chars().take(60).collect::<String>(),
        hash: sha256_hex(&plaintext),
        hint: plaintext.chars().take(KEY_PREFIX.len() + 6).collect(),
        created: store::now(),
        created_by: by.to_string(),
        last_used: 0,
        calls: 0,
    };
    let mut keys = read();
    keys.push(rec.clone());
    write(&keys);
    (rec, plaintext)
}

pub fn revoke(id: &str) -> bool {
    let _g = lock().lock();
    let mut keys = read();
    let before = keys.len();
    keys.retain(|k| k.id != id);
    let removed = keys.len() != before;
    if removed {
        write(&keys);
    }
    removed
}

/// Match a presented key against the store, recording the use. Returns the
/// record it belongs to, or None when nothing matches.
pub fn verify(token: &str) -> Option<KeyRecord> {
    let hash = sha256_hex(token.trim());
    let _g = lock().lock();
    let mut keys = read();
    let hit = keys
        .iter_mut()
        .find(|k| crate::auth::ct_eq(k.hash.as_bytes(), hash.as_bytes()))?;
    hit.last_used = store::now();
    hit.calls += 1;
    let found = hit.clone();
    write(&keys);
    Some(found)
}
