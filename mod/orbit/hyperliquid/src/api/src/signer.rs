//! Backend signer for Hyperliquid agent wallets.
//!
//! Holds an ECDSA (secp256k1) keypair per user EOA, encrypted at rest with
//! AES-256-GCM. The master key is sourced from `HYPERLIQUID_SIGNER_MASTER_KEY`
//! or persisted on disk; user keys live as `<eoa>.json` in the signer dir.
//!
//! On Hyperliquid these backend keys act as **agent wallets** — the user
//! signs an `approveAgent` action in their browser wallet once, authorizing
//! this agent address to trade on their behalf. After that the engine signs
//! all subsequent orders/cancels/modifies/vault-actions with the agent key
//! and the user never needs to be online.

use std::path::{Path, PathBuf};

use aes_gcm::aead::{Aead, KeyInit};
use aes_gcm::{Aes256Gcm, Nonce};
use anyhow::{anyhow, Context, Result};
use dashmap::DashMap;
use k256::ecdsa::{signature::hazmat::PrehashSigner, RecoveryId, Signature, SigningKey, VerifyingKey};
use parking_lot::Mutex;
use rand::rngs::OsRng;
use rand::RngCore;
use serde::{Deserialize, Serialize};

#[derive(Serialize, Deserialize)]
struct StoredKey {
    nonce: String,
    ciphertext: String,
}

pub struct SignerStore {
    disk_dir: PathBuf,
    master_key: [u8; 32],
    cache: DashMap<String, Mutex<SigningKey>>,
}

impl SignerStore {
    pub fn new() -> Self {
        let base = std::env::var("HYPERLIQUID_DATA_DIR")
            .map(PathBuf::from)
            .unwrap_or_else(|_| {
                let home = std::env::var("HOME").unwrap_or_else(|_| ".".into());
                PathBuf::from(format!("{home}/.hyperliquid"))
            });
        let disk_dir = base.join("signer-store");
        std::fs::create_dir_all(&disk_dir).ok();
        let master_key = Self::load_or_init_master(&disk_dir);
        Self { disk_dir, master_key, cache: DashMap::new() }
    }

    fn load_or_init_master(disk_dir: &Path) -> [u8; 32] {
        if let Ok(s) = std::env::var("HYPERLIQUID_SIGNER_MASTER_KEY") {
            if let Some(k) = parse_master_hex(&s) {
                tracing::info!("signer master key loaded from env");
                return k;
            }
            tracing::warn!("HYPERLIQUID_SIGNER_MASTER_KEY invalid (need 64 hex chars); trying disk");
        }
        let p = disk_dir.join(".master");
        if let Ok(s) = std::fs::read_to_string(&p) {
            if let Some(k) = parse_master_hex(s.trim()) {
                tracing::info!("signer master key loaded from {:?}", p);
                return k;
            }
            tracing::warn!("on-disk master at {:?} malformed; regenerating (old user keys will be unreadable)", p);
        }
        let mut k = [0u8; 32];
        OsRng.fill_bytes(&mut k);
        if let Err(e) = std::fs::write(&p, hex::encode(k)) {
            tracing::error!("could not persist master to {:?}: {} — keys will be EPHEMERAL", p, e);
        } else {
            restrict_perms(&p);
            tracing::info!("signer master key generated and persisted to {:?}", p);
        }
        k
    }

    fn path_for(&self, eoa: &str) -> PathBuf {
        let safe = eoa.to_lowercase().replace(|c: char| !c.is_ascii_hexdigit() && c != 'x', "");
        self.disk_dir.join(format!("{}.json", safe))
    }

    fn load_or_create(&self, eoa: &str) -> Result<()> {
        let key_lc = eoa.to_lowercase();
        if self.cache.contains_key(&key_lc) { return Ok(()); }
        let path = self.path_for(&key_lc);
        if path.exists() {
            let raw = std::fs::read_to_string(&path).context("read stored signer")?;
            let stored: StoredKey = serde_json::from_str(&raw).context("parse stored signer")?;
            let nonce = base64_decode(&stored.nonce)?;
            let ct = base64_decode(&stored.ciphertext)?;
            let cipher = Aes256Gcm::new_from_slice(&self.master_key).map_err(|e| anyhow!("cipher init: {e}"))?;
            let pt = cipher.decrypt(Nonce::from_slice(&nonce), ct.as_ref()).map_err(|e| anyhow!("decrypt: {e}"))?;
            if pt.len() != 32 { return Err(anyhow!("decrypted key wrong size")); }
            let sk = SigningKey::from_slice(&pt).context("import signing key")?;
            self.cache.insert(key_lc, Mutex::new(sk));
            return Ok(());
        }
        let mut bytes = [0u8; 32];
        OsRng.fill_bytes(&mut bytes);
        let sk = SigningKey::from_slice(&bytes).context("new signing key")?;
        let cipher = Aes256Gcm::new_from_slice(&self.master_key).map_err(|e| anyhow!("cipher init: {e}"))?;
        let mut nonce = [0u8; 12];
        OsRng.fill_bytes(&mut nonce);
        let ct = cipher.encrypt(Nonce::from_slice(&nonce), bytes.as_ref()).map_err(|e| anyhow!("encrypt: {e}"))?;
        let stored = StoredKey { nonce: base64_encode(&nonce), ciphertext: base64_encode(&ct) };
        std::fs::write(&path, serde_json::to_string(&stored)?).context("write stored signer")?;
        restrict_perms(&path);
        self.cache.insert(key_lc, Mutex::new(sk));
        Ok(())
    }

    pub fn signer_address(&self, eoa: &str) -> Result<String> {
        self.load_or_create(eoa)?;
        let entry = self.cache.get(&eoa.to_lowercase()).ok_or_else(|| anyhow!("signer missing"))?;
        let sk = entry.lock();
        let vk = VerifyingKey::from(&*sk);
        Ok(address_from_pubkey(&vk))
    }

    pub fn sign_digest(&self, eoa: &str, digest: &[u8; 32]) -> Result<[u8; 65]> {
        self.load_or_create(eoa)?;
        let entry = self.cache.get(&eoa.to_lowercase()).ok_or_else(|| anyhow!("signer missing"))?;
        let sk = entry.lock();
        let (sig, recid): (Signature, RecoveryId) = sk.sign_prehash(digest).map_err(|e| anyhow!("sign: {e}"))?;
        let b = sig.to_bytes();
        let mut out = [0u8; 65];
        out[..32].copy_from_slice(&b[..32]);
        out[32..64].copy_from_slice(&b[32..64]);
        out[64] = 27 + u8::from(recid);
        Ok(out)
    }
}

fn parse_master_hex(s: &str) -> Option<[u8; 32]> {
    if s.len() != 64 { return None; }
    let v = hex::decode(s).ok()?;
    if v.len() != 32 { return None; }
    let mut o = [0u8; 32];
    o.copy_from_slice(&v);
    Some(o)
}

pub fn address_from_pubkey(vk: &VerifyingKey) -> String {
    let pt = vk.to_encoded_point(false);
    let bytes = pt.as_bytes();
    let pubkey = &bytes[1..]; // strip 0x04 prefix
    let h = keccak256(pubkey);
    format!("0x{}", hex::encode(&h[12..]))
}

/// Keccak-256 (FIPS-202). Exposed for action signing.
pub fn keccak256(input: &[u8]) -> [u8; 32] {
    let mut k = Keccak256::new();
    k.update(input);
    k.finalize()
}

// ─── Keccak-256 (FIPS-202) — inline so we don't pull in `sha3` for one hash ───
struct Keccak256 { state: [u64; 25], buf: Vec<u8> }
const RATE: usize = 136;

impl Keccak256 {
    fn new() -> Self { Self { state: [0u64; 25], buf: Vec::with_capacity(RATE) } }
    fn update(&mut self, data: &[u8]) { self.buf.extend_from_slice(data); }
    fn finalize(mut self) -> [u8; 32] {
        let mut padded = std::mem::take(&mut self.buf);
        padded.push(0x01);
        while padded.len() % RATE != RATE - 1 { padded.push(0x00); }
        padded.push(0x80);
        for chunk in padded.chunks(RATE) {
            for (i, lane) in chunk.chunks(8).enumerate() {
                let mut b = [0u8; 8];
                b[..lane.len()].copy_from_slice(lane);
                self.state[i] ^= u64::from_le_bytes(b);
            }
            keccak_f1600(&mut self.state);
        }
        let mut out = [0u8; 32];
        for (i, lane) in self.state.iter().take(4).enumerate() {
            out[i * 8..i * 8 + 8].copy_from_slice(&lane.to_le_bytes());
        }
        out
    }
}

const RC: [u64; 24] = [
    0x0000000000000001, 0x0000000000008082, 0x800000000000808a,
    0x8000000080008000, 0x000000000000808b, 0x0000000080000001,
    0x8000000080008081, 0x8000000000008009, 0x000000000000008a,
    0x0000000000000088, 0x0000000080008009, 0x000000008000000a,
    0x000000008000808b, 0x800000000000008b, 0x8000000000008089,
    0x8000000000008003, 0x8000000000008002, 0x8000000000000080,
    0x000000000000800a, 0x800000008000000a, 0x8000000080008081,
    0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
];
const ROT: [u32; 25] = [
     0,  1, 62, 28, 27,
    36, 44,  6, 55, 20,
     3, 10, 43, 25, 39,
    41, 45, 15, 21,  8,
    18,  2, 61, 56, 14,
];

fn keccak_f1600(s: &mut [u64; 25]) {
    for &rc in RC.iter() {
        let mut c = [0u64; 5];
        for x in 0..5 { c[x] = s[x] ^ s[x+5] ^ s[x+10] ^ s[x+15] ^ s[x+20]; }
        let mut d = [0u64; 5];
        for x in 0..5 { d[x] = c[(x+4)%5] ^ c[(x+1)%5].rotate_left(1); }
        for x in 0..5 { for y in 0..5 { s[x + 5*y] ^= d[x]; } }
        let mut b = [0u64; 25];
        for x in 0..5 { for y in 0..5 {
            b[y + 5 * ((2*x + 3*y) % 5)] = s[x + 5*y].rotate_left(ROT[x + 5*y]);
        } }
        for x in 0..5 { for y in 0..5 {
            s[x + 5*y] = b[x + 5*y] ^ ((!b[(x+1)%5 + 5*y]) & b[(x+2)%5 + 5*y]);
        } }
        s[0] ^= rc;
    }
}

fn base64_encode(d: &[u8]) -> String { use base64::Engine; base64::engine::general_purpose::STANDARD.encode(d) }
fn base64_decode(d: &str) -> Result<Vec<u8>> {
    use base64::Engine;
    Ok(base64::engine::general_purpose::STANDARD.decode(d).context("base64 decode")?)
}

#[cfg(unix)]
fn restrict_perms(p: &Path) {
    use std::os::unix::fs::PermissionsExt;
    if let Ok(m) = std::fs::metadata(p) {
        let mut perms = m.permissions();
        perms.set_mode(0o600);
        let _ = std::fs::set_permissions(p, perms);
    }
}
#[cfg(not(unix))]
fn restrict_perms(_p: &Path) {}

#[cfg(test)]
mod tests {
    use super::*;

    // Reference vector: keccak256("") = 0xc5d246...
    #[test]
    fn keccak_empty() {
        let h = keccak256(b"");
        assert_eq!(hex::encode(h), "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470");
    }

    #[test]
    fn keccak_hello() {
        let h = keccak256(b"hello");
        assert_eq!(hex::encode(h), "1c8aff950685c2ed4bc3174f3472287b56d9517b9c948127319a09a7a36deac8");
    }

    #[test]
    fn signer_address_is_deterministic() {
        std::env::set_var("HYPERLIQUID_SIGNER_MASTER_KEY", "00".repeat(32));
        let s = SignerStore::new();
        let eoa = format!("0x{:040x}", std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos());
        let a1 = s.signer_address(&eoa).unwrap();
        let a2 = s.signer_address(&eoa.to_uppercase().replacen("0X", "0x", 1)).unwrap();
        assert_eq!(a1, a2);
        assert!(a1.starts_with("0x") && a1.len() == 42);
    }
}
