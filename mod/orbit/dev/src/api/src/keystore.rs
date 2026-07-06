//! Per-user **BYOK** key store. Holds each user's own provider API key, keyed
//! by `(provider, wallet address)`, encrypted at rest with AES-256-GCM.
//!
//! Master key resolution:
//!   1. `DEV_MASTER_KEY` env var (64 hex chars = 32 bytes)
//!   2. `<data_dir>/.master` (auto-generated on first boot, chmod 0600)
//!   3. generated + persisted, with a log note
//!
//! Data dir: `DEV_DATA_DIR` (set to a mounted volume in prod) else `~/.mod/dev`.
//! Layout: `<data_dir>/byok/<provider>/<address>.json` holding `{nonce, ciphertext}`.
//! The plaintext is the user's provider key string (never logged, never returned
//! to the client — only "do you have a key on file" booleans cross the wire).

use std::path::{Path, PathBuf};

use aes_gcm::aead::{Aead, KeyInit};
use aes_gcm::{Aes256Gcm, Nonce};
use anyhow::{anyhow, Context, Result};
use base64::Engine;
use rand::rngs::OsRng;
use rand::RngCore;
use serde::{Deserialize, Serialize};

#[derive(Serialize, Deserialize)]
struct Stored {
    nonce: String,      // 12-byte AES-GCM nonce, base64
    ciphertext: String, // AES-GCM ciphertext over the UTF-8 key string, base64
}

pub struct KeyStore {
    dir: PathBuf, // <data_dir>/byok
    master_key: [u8; 32],
}

impl KeyStore {
    pub fn new() -> Self {
        let base = std::env::var("DEV_DATA_DIR")
            .map(PathBuf::from)
            .unwrap_or_else(|_| {
                let home = std::env::var("HOME").unwrap_or_else(|_| "/root".into());
                PathBuf::from(home).join(".mod").join("dev")
            });
        let dir = base.join("byok");
        std::fs::create_dir_all(&dir).ok();
        let master_key = Self::load_or_init_master(&dir);
        Self { dir, master_key }
    }

    fn load_or_init_master(dir: &Path) -> [u8; 32] {
        if let Ok(hex_str) = std::env::var("DEV_MASTER_KEY") {
            if let Some(k) = parse_master_hex(&hex_str) {
                tracing::info!("byok master key loaded from env");
                return k;
            }
            tracing::warn!("DEV_MASTER_KEY set but invalid (need 64 hex chars); using on-disk master");
        }
        let master_path = dir.join(".master");
        if let Ok(s) = std::fs::read_to_string(&master_path) {
            if let Some(k) = parse_master_hex(s.trim()) {
                return k;
            }
            tracing::warn!("on-disk master malformed; regenerating — existing BYOK keys become unreadable");
        }
        let mut key = [0u8; 32];
        OsRng.fill_bytes(&mut key);
        match std::fs::write(&master_path, hex::encode(key)) {
            Ok(()) => {
                restrict_perms(&master_path);
                tracing::info!("byok master key generated at {:?} (chmod 0600)", master_path);
            }
            Err(e) => tracing::error!("could not persist master key: {e} — BYOK keys will be EPHEMERAL"),
        }
        key
    }

    fn provider_dir(&self, provider: &str) -> PathBuf {
        let safe: String = provider
            .to_lowercase()
            .chars()
            .filter(|c| c.is_ascii_alphanumeric() || *c == '-' || *c == '_')
            .collect();
        self.dir.join(safe)
    }

    fn path_for(&self, provider: &str, address: &str) -> PathBuf {
        let safe: String = address
            .to_lowercase()
            .chars()
            .filter(|c| c.is_ascii_hexdigit() || *c == 'x')
            .collect();
        self.provider_dir(provider).join(format!("{safe}.json"))
    }

    /// Store (or replace) the user's key for a provider.
    pub fn set(&self, provider: &str, address: &str, api_key: &str) -> Result<()> {
        let cipher = Aes256Gcm::new_from_slice(&self.master_key)
            .map_err(|e| anyhow!("cipher init: {e}"))?;
        let mut nonce = [0u8; 12];
        OsRng.fill_bytes(&mut nonce);
        let ct = cipher
            .encrypt(Nonce::from_slice(&nonce), api_key.as_bytes())
            .map_err(|e| anyhow!("encrypt: {e}"))?;
        let stored = Stored {
            nonce: b64(&nonce),
            ciphertext: b64(&ct),
        };
        std::fs::create_dir_all(self.provider_dir(provider)).ok();
        let path = self.path_for(provider, address);
        std::fs::write(&path, serde_json::to_string(&stored)?).context("write byok key")?;
        restrict_perms(&path);
        Ok(())
    }

    /// Retrieve the user's key for a provider, if one is on file.
    pub fn get(&self, provider: &str, address: &str) -> Result<Option<String>> {
        let path = self.path_for(provider, address);
        if !path.exists() {
            return Ok(None);
        }
        let raw = std::fs::read_to_string(&path).context("read byok key")?;
        let stored: Stored = serde_json::from_str(&raw).context("parse byok key")?;
        let nonce = unb64(&stored.nonce)?;
        let ct = unb64(&stored.ciphertext)?;
        let cipher = Aes256Gcm::new_from_slice(&self.master_key)
            .map_err(|e| anyhow!("cipher init: {e}"))?;
        let pt = cipher
            .decrypt(Nonce::from_slice(&nonce), ct.as_ref())
            .map_err(|e| anyhow!("decrypt: {e}"))?;
        Ok(Some(String::from_utf8(pt).context("byok key not utf8")?))
    }

    pub fn has(&self, provider: &str, address: &str) -> bool {
        self.path_for(provider, address).exists()
    }

    pub fn delete(&self, provider: &str, address: &str) -> Result<bool> {
        let path = self.path_for(provider, address);
        if path.exists() {
            std::fs::remove_file(&path).context("remove byok key")?;
            Ok(true)
        } else {
            Ok(false)
        }
    }

    /// Which of the given providers does this address have a key on file for?
    pub fn providers_with_key(&self, providers: &[String], address: &str) -> Vec<String> {
        providers
            .iter()
            .filter(|p| self.has(p, address))
            .cloned()
            .collect()
    }
}

fn parse_master_hex(s: &str) -> Option<[u8; 32]> {
    if s.len() != 64 {
        return None;
    }
    let bytes = hex::decode(s).ok()?;
    let mut out = [0u8; 32];
    out.copy_from_slice(&bytes);
    Some(out)
}

fn b64(data: &[u8]) -> String {
    base64::engine::general_purpose::STANDARD.encode(data)
}

fn unb64(s: &str) -> Result<Vec<u8>> {
    base64::engine::general_purpose::STANDARD
        .decode(s)
        .context("base64 decode")
}

#[cfg(unix)]
fn restrict_perms(path: &Path) {
    use std::os::unix::fs::PermissionsExt;
    if let Ok(meta) = std::fs::metadata(path) {
        let mut perms = meta.permissions();
        perms.set_mode(0o600);
        let _ = std::fs::set_permissions(path, perms);
    }
}

#[cfg(not(unix))]
fn restrict_perms(_path: &Path) {}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn roundtrip_set_get_delete() {
        std::env::set_var("DEV_MASTER_KEY", "22".repeat(32));
        let dir = std::env::temp_dir().join(format!(
            "dev-byok-test-{}",
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::env::set_var("DEV_DATA_DIR", &dir);
        let store = KeyStore::new();
        let addr = "0x000000000000000000000000000000000000beef";
        assert!(!store.has("venice", addr));
        store.set("venice", addr, "vk-secret-123").unwrap();
        assert!(store.has("venice", addr));
        assert_eq!(store.get("venice", addr).unwrap().as_deref(), Some("vk-secret-123"));
        // Per-provider isolation: the same address has no openrouter key.
        assert!(!store.has("openrouter", addr));
        // Case-insensitive address handling.
        assert!(store.has("venice", &addr.to_uppercase().replacen("0X", "0x", 1)));
        assert!(store.delete("venice", addr).unwrap());
        assert!(!store.has("venice", addr));
    }
}
