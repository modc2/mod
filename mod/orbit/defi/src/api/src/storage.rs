//! Protocol persistence and content addressing.
//!
//! Saved protocols live as JSON under ~/.mod/defi/protocols/ — off-tree, never
//! in config.json, because the list is per-operator state rather than module
//! configuration.
//!
//! Sharing is by CID. We compute a real CIDv1 (raw codec, sha2-256, base32) over
//! the canonical bytes ourselves so a protocol has a stable identifier even when
//! no storage module is running, and best-effort push the same bytes to localfs
//! so the fleet's gateway can serve them.

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::path::{Path, PathBuf};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Protocol {
    pub id: String,
    pub name: String,
    #[serde(default)]
    pub description: String,
    pub owner: String,
    pub created: u64,
    pub updated: u64,
    pub graph: crate::graph::Graph,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub cid: Option<String>,
    #[serde(default)]
    pub deployments: Vec<Deployment>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub imported_from: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Deployment {
    #[serde(rename = "chainId")]
    pub chain_id: u64,
    #[serde(default)]
    pub network: String,
    pub at: u64,
    pub deployer: String,
    /// node id → deployed address
    pub addresses: serde_json::Map<String, serde_json::Value>,
    #[serde(default)]
    pub txs: Vec<String>,
}

pub struct Store {
    root: PathBuf,
}

impl Store {
    pub fn new(root: &Path) -> Self {
        let _ = std::fs::create_dir_all(root.join("protocols"));
        let _ = std::fs::create_dir_all(root.join("objects"));
        Self { root: root.to_path_buf() }
    }

    fn path(&self, id: &str) -> PathBuf {
        self.root.join("protocols").join(format!("{}.json", sanitize(id)))
    }

    pub fn list(&self) -> Vec<Protocol> {
        let mut out = Vec::new();
        let dir = self.root.join("protocols");
        if let Ok(entries) = std::fs::read_dir(dir) {
            for entry in entries.flatten() {
                if let Ok(body) = std::fs::read_to_string(entry.path()) {
                    if let Ok(p) = serde_json::from_str::<Protocol>(&body) {
                        out.push(p);
                    }
                }
            }
        }
        out.sort_by(|a, b| b.updated.cmp(&a.updated));
        out
    }

    pub fn get(&self, id: &str) -> Option<Protocol> {
        let body = std::fs::read_to_string(self.path(id)).ok()?;
        serde_json::from_str(&body).ok()
    }

    pub fn save(&self, protocol: &Protocol) -> Result<(), String> {
        let body = serde_json::to_string_pretty(protocol).map_err(|e| e.to_string())?;
        std::fs::write(self.path(&protocol.id), body).map_err(|e| e.to_string())
    }

    pub fn delete(&self, id: &str) -> Result<(), String> {
        std::fs::remove_file(self.path(id)).map_err(|e| e.to_string())
    }

    /// Content-address a blob and keep a local copy keyed by its CID.
    pub fn put_object(&self, bytes: &[u8]) -> Result<String, String> {
        let cid = cid_v1_raw(bytes);
        std::fs::write(self.root.join("objects").join(&cid), bytes).map_err(|e| e.to_string())?;
        Ok(cid)
    }

    pub fn get_object(&self, cid: &str) -> Option<Vec<u8>> {
        std::fs::read(self.root.join("objects").join(sanitize(cid))).ok()
    }
}

fn sanitize(id: &str) -> String {
    id.chars()
        .filter(|c| c.is_ascii_alphanumeric() || *c == '-' || *c == '_')
        .take(96)
        .collect()
}

/// CIDv1, raw codec (0x55), sha2-256 (0x12 0x20), multibase base32-lower ('b').
pub fn cid_v1_raw(bytes: &[u8]) -> String {
    let digest = Sha256::digest(bytes);
    let mut buf = Vec::with_capacity(36);
    buf.push(0x01); // cid version
    buf.push(0x55); // raw
    buf.push(0x12); // sha2-256
    buf.push(0x20); // 32 bytes
    buf.extend_from_slice(&digest);
    format!("b{}", base32_lower(&buf))
}

fn base32_lower(data: &[u8]) -> String {
    const ALPHABET: &[u8] = b"abcdefghijklmnopqrstuvwxyz234567";
    let mut out = String::new();
    let mut buffer: u32 = 0;
    let mut bits = 0u32;
    for byte in data {
        buffer = (buffer << 8) | *byte as u32;
        bits += 8;
        while bits >= 5 {
            out.push(ALPHABET[((buffer >> (bits - 5)) & 0x1f) as usize] as char);
            bits -= 5;
        }
    }
    if bits > 0 {
        out.push(ALPHABET[((buffer << (5 - bits)) & 0x1f) as usize] as char);
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cid_is_stable_and_content_addressed() {
        let a = cid_v1_raw(b"{\"name\":\"vault\"}");
        let b = cid_v1_raw(b"{\"name\":\"vault\"}");
        let c = cid_v1_raw(b"{\"name\":\"vaults\"}");
        assert_eq!(a, b);
        assert_ne!(a, c);
        assert!(a.starts_with("bafkrei"), "unexpected CID prefix: {a}");
    }
}
