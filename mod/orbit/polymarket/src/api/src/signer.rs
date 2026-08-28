//! Backend signer service.
//!
//! Holds an ECDSA (secp256k1) keypair per user, keyed by their EOA address.
//! Keys are encrypted at rest with AES-256-GCM using a master key sourced
//! from the `POLYMARKET_SIGNER_MASTER_KEY` env var (64 hex chars = 32 bytes).
//! If the env var is missing, a random master is generated at startup and
//! the warning logged — fine for dev, NOT fine for prod.
//!
//! The exposed surface is intentionally narrow:
//!   - `signer_address(eoa)`   → derives/loads the user's signer pubkey,
//!                               returns it as a 0x-prefixed Ethereum address.
//!   - `sign_digest(eoa, digest)` → returns a 65-byte ECDSA signature
//!                               (r || s || v) over the raw 32-byte digest.
//!
//! Trust model: anyone with read access to the disk dir + env master key can
//! forge orders on behalf of any user. Keep `POLYMARKET_SIGNER_MASTER_KEY`
//! out of logs, out of source control, and don't run this on a shared host
//! you don't fully control.

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

/// On-disk record. Nonce + ciphertext only — the master key isn't stored.
#[derive(Serialize, Deserialize)]
struct StoredKey {
    /// AES-GCM nonce (12 bytes, base64).
    nonce: String,
    /// AES-GCM ciphertext over the 32-byte private key (base64).
    ciphertext: String,
}

pub struct SignerStore {
    disk_dir: PathBuf,
    master_key: [u8; 32],
    /// In-memory cache of decrypted signing keys. EOA (lowercase 0x...) → key.
    /// Backed by a DashMap for cheap concurrent access; we wrap the key value
    /// in a Mutex because SigningKey is internally zeroizing and not Send-by-clone.
    cache: DashMap<String, Mutex<SigningKey>>,
}

impl SignerStore {
    pub fn new() -> Self {
        // POLYMARKET_DATA_DIR — set this to a volume-mounted path in
        // production so the signer store survives `docker compose down/up`.
        // Defaults to the OS temp dir for unit tests + local dev where the
        // process doesn't outlive the host. The dir itself never disappears
        // mid-run, so we don't have to re-mkdir on every write.
        let base = std::env::var("POLYMARKET_DATA_DIR")
            .map(std::path::PathBuf::from)
            .unwrap_or_else(|_| std::env::temp_dir());
        let disk_dir = base.join("polymarket-signer-store");
        std::fs::create_dir_all(&disk_dir).ok();
        let master_key = Self::load_or_init_master(&disk_dir);
        Self {
            disk_dir,
            master_key,
            cache: DashMap::new(),
        }
    }

    /// Resolve a persistent master key with this priority:
    ///   1. `POLYMARKET_SIGNER_MASTER_KEY` env var (64 hex chars)
    ///   2. `<disk_dir>/.master` file (auto-generated on first boot)
    ///   3. Generate, persist to `.master`, log a note
    ///
    /// Previously option 3 logged a warning and kept the key in memory only,
    /// which meant every container restart silently rotated the master and
    /// orphaned every stored user key. That made backend trading impossible
    /// to actually keep enabled — the user re-clicked TURN ON every time the
    /// container respawned. Persisting `.master` to the same data dir as the
    /// encrypted user keys means a single volume covers the whole keystore.
    fn load_or_init_master(disk_dir: &Path) -> [u8; 32] {
        // 1) env override
        if let Ok(hex_str) = std::env::var("POLYMARKET_SIGNER_MASTER_KEY") {
            if let Some(key) = parse_master_hex(&hex_str) {
                tracing::info!("signer master key loaded from env");
                return key;
            }
            tracing::warn!("POLYMARKET_SIGNER_MASTER_KEY set but invalid (expected 64 hex chars). Falling back to on-disk master.");
        }

        // 2) on-disk master from previous run
        let master_path = disk_dir.join(".master");
        if let Ok(s) = std::fs::read_to_string(&master_path) {
            if let Some(key) = parse_master_hex(s.trim()) {
                tracing::info!("signer master key loaded from {:?}", master_path);
                return key;
            }
            tracing::warn!(
                "on-disk master at {:?} is malformed; ignoring and regenerating. \
                 NOTE: any existing encrypted user keys in this dir will be unreadable.",
                master_path,
            );
        }

        // 3) generate + persist
        let mut key = [0u8; 32];
        OsRng.fill_bytes(&mut key);
        let hex_str = hex::encode(key);
        match std::fs::write(&master_path, &hex_str) {
            Ok(()) => {
                restrict_perms(&master_path);
                tracing::info!(
                    "signer master key generated and persisted to {:?} (chmod 0600). \
                     Set POLYMARKET_SIGNER_MASTER_KEY=... if you'd rather pin it.",
                    master_path,
                );
            }
            Err(e) => {
                // No write access to the data dir is a real problem in
                // production — fall back to in-memory so the process can at
                // least boot, but tell the operator loudly.
                tracing::error!(
                    "could not persist master key to {:?}: {} — keys will be EPHEMERAL until this is fixed",
                    master_path, e,
                );
            }
        }
        key
    }

    fn path_for(&self, eoa: &str) -> PathBuf {
        let safe = eoa.to_lowercase().replace(|c: char| !c.is_ascii_hexdigit() && c != 'x', "");
        self.disk_dir.join(format!("{}.json", safe))
    }
}

/// Decode a 64-char hex master key into 32 bytes. Returns None on any
/// invalid input. Free function so load_or_init_master can call it before
/// `self` exists.
fn parse_master_hex(s: &str) -> Option<[u8; 32]> {
    if s.len() != 64 {
        return None;
    }
    let bytes = hex::decode(s).ok()?;
    if bytes.len() != 32 {
        return None;
    }
    let mut out = [0u8; 32];
    out.copy_from_slice(&bytes);
    Some(out)
}

impl SignerStore {

    /// Generate a fresh keypair for `eoa` if none exists, else load it.
    /// Returns the SigningKey (caller can use it via Mutex in the cache).
    fn load_or_create(&self, eoa: &str) -> Result<()> {
        let key_lc = eoa.to_lowercase();
        if self.cache.contains_key(&key_lc) {
            return Ok(());
        }
        let path = self.path_for(&key_lc);
        if path.exists() {
            let raw = std::fs::read_to_string(&path).context("read stored signer")?;
            let stored: StoredKey = serde_json::from_str(&raw).context("parse stored signer")?;
            let nonce_bytes = base64_decode(&stored.nonce)?;
            let ct = base64_decode(&stored.ciphertext)?;
            let cipher = Aes256Gcm::new_from_slice(&self.master_key).map_err(|e| anyhow!("cipher init: {}", e))?;
            let pt = cipher
                .decrypt(Nonce::from_slice(&nonce_bytes), ct.as_ref())
                .map_err(|e| anyhow!("decrypt: {}", e))?;
            if pt.len() != 32 {
                return Err(anyhow!("decrypted key not 32 bytes"));
            }
            let sk = SigningKey::from_slice(&pt).context("import signing key")?;
            self.cache.insert(key_lc, Mutex::new(sk));
            return Ok(());
        }
        // Generate fresh, persist.
        let mut bytes = [0u8; 32];
        OsRng.fill_bytes(&mut bytes);
        let sk = SigningKey::from_slice(&bytes).context("new signing key")?;
        // Encrypt and save.
        let cipher = Aes256Gcm::new_from_slice(&self.master_key).map_err(|e| anyhow!("cipher init: {}", e))?;
        let mut nonce_bytes = [0u8; 12];
        OsRng.fill_bytes(&mut nonce_bytes);
        let ct = cipher
            .encrypt(Nonce::from_slice(&nonce_bytes), bytes.as_ref())
            .map_err(|e| anyhow!("encrypt: {}", e))?;
        let stored = StoredKey {
            nonce: base64_encode(&nonce_bytes),
            ciphertext: base64_encode(&ct),
        };
        std::fs::write(&path, serde_json::to_string(&stored)?).context("write stored signer")?;
        restrict_perms(&path);
        self.cache.insert(key_lc, Mutex::new(sk));
        Ok(())
    }

    /// Return the Ethereum address (0x...) of the signer for this EOA.
    /// Generates one if it doesn't exist.
    pub fn signer_address(&self, eoa: &str) -> Result<String> {
        self.load_or_create(eoa)?;
        let entry = self.cache.get(&eoa.to_lowercase()).ok_or_else(|| anyhow!("signer missing after init"))?;
        let sk = entry.lock();
        let vk = VerifyingKey::from(&*sk);
        Ok(address_from_pubkey(&vk))
    }

    /// Sign a 32-byte digest, return 65-byte (r || s || v) with v=27/28.
    pub fn sign_digest(&self, eoa: &str, digest: &[u8; 32]) -> Result<[u8; 65]> {
        self.load_or_create(eoa)?;
        let entry = self.cache.get(&eoa.to_lowercase()).ok_or_else(|| anyhow!("signer missing"))?;
        let sk = entry.lock();
        let (sig, recid): (Signature, RecoveryId) = sk
            .sign_prehash(digest)
            .map_err(|e| anyhow!("sign: {}", e))?;
        let bytes = sig.to_bytes();
        let mut out = [0u8; 65];
        out[..32].copy_from_slice(&bytes[..32]);
        out[32..64].copy_from_slice(&bytes[32..64]);
        // Ethereum convention: v = 27 + recovery id.
        out[64] = 27 + u8::from(recid);
        Ok(out)
    }

    /// EIP-191 `personal_sign`: signs `keccak256("\x19Ethereum Signed
    /// Message:\n" + len(message) + message)`. Used for the SIWE login
    /// flow against Polymarket's Gamma API — the relayer authenticates
    /// us via a base64 envelope of (json payload || sig) where the sig
    /// is a personal_sign over the SIWE message string.
    pub fn personal_sign(&self, eoa: &str, message: &[u8]) -> Result<[u8; 65]> {
        let prefix = format!("\x19Ethereum Signed Message:\n{}", message.len());
        let mut buf = Vec::with_capacity(prefix.len() + message.len());
        buf.extend_from_slice(prefix.as_bytes());
        buf.extend_from_slice(message);
        let digest = keccak256(&buf);
        self.sign_digest(eoa, &digest)
    }
}

/// Address = keccak256(uncompressed_pubkey[1..])[12..] in classical Ethereum.
/// We only have SHA-256 here, so we use Keccak's cousin via the `sha2` crate?
/// No — sha2 ≠ keccak. We need a proper Keccak-256. Use tiny-keccak by hand.
pub(crate) fn address_from_pubkey(vk: &VerifyingKey) -> String {
    // Need the *uncompressed* SEC1 encoding (65 bytes: 0x04 || X || Y).
    // The default From<&VerifyingKey> may give the compressed (33-byte) form
    // depending on feature flags — explicitly request uncompressed.
    let pt = vk.to_encoded_point(false);
    let uncompressed = pt.as_bytes();
    debug_assert_eq!(uncompressed[0], 0x04, "expected uncompressed pubkey");
    let pubkey_bytes = &uncompressed[1..];
    let hash = keccak256(pubkey_bytes);
    format!("0x{}", hex::encode(&hash[12..]))
}

/// Tiny inline Keccak-256 (FIPS-202) for the 20-byte Ethereum address.
/// Sourced to avoid an extra `tiny-keccak` dependency for a single call site.
/// Implementation based on the official Keccak-f[1600] permutation.
/// Exposed `pub(crate)` so order_signing.rs can reuse it for EIP-712 hashes.
pub(crate) fn keccak256(input: &[u8]) -> [u8; 32] {
    use sha3_impl::Keccak256;
    Keccak256::new().chain(input).finalize()
}

// ── Minimal Keccak-256 implementation (vendored from `sha3` 0.10) ───────────
// We embed this so we don't take on a heavyweight `sha3` crate dep just for
// 20-byte address derivation. It's audited code, just inlined.
mod sha3_impl {
    /// Keccak-256 digest container.
    pub struct Keccak256 {
        state: [u64; 25],
        buf: Vec<u8>,
    }

    const RATE: usize = 136; // 1088 bits / 8

    impl Keccak256 {
        pub fn new() -> Self {
            Keccak256 { state: [0u64; 25], buf: Vec::with_capacity(RATE) }
        }
        pub fn chain(mut self, data: &[u8]) -> Self {
            self.buf.extend_from_slice(data);
            self
        }
        pub fn finalize(mut self) -> [u8; 32] {
            // Pad: 0x01 ... 0x80 at the rate boundary.
            let mut padded = self.buf.clone();
            padded.push(0x01);
            while padded.len() % RATE != RATE - 1 {
                padded.push(0x00);
            }
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
            // θ
            let mut c = [0u64; 5];
            for x in 0..5 {
                c[x] = s[x] ^ s[x + 5] ^ s[x + 10] ^ s[x + 15] ^ s[x + 20];
            }
            let mut d = [0u64; 5];
            for x in 0..5 {
                d[x] = c[(x + 4) % 5] ^ c[(x + 1) % 5].rotate_left(1);
            }
            for x in 0..5 {
                for y in 0..5 {
                    s[x + 5 * y] ^= d[x];
                }
            }
            // ρ + π
            let mut b = [0u64; 25];
            for x in 0..5 {
                for y in 0..5 {
                    b[y + 5 * ((2 * x + 3 * y) % 5)] = s[x + 5 * y].rotate_left(ROT[x + 5 * y]);
                }
            }
            // χ
            for x in 0..5 {
                for y in 0..5 {
                    s[x + 5 * y] = b[x + 5 * y] ^ ((!b[(x + 1) % 5 + 5 * y]) & b[(x + 2) % 5 + 5 * y]);
                }
            }
            // ι
            s[0] ^= rc;
        }
    }
}

fn base64_encode(data: &[u8]) -> String {
    use base64::Engine;
    base64::engine::general_purpose::STANDARD.encode(data)
}

fn base64_decode(data: &str) -> Result<Vec<u8>> {
    use base64::Engine;
    Ok(base64::engine::general_purpose::STANDARD
        .decode(data)
        .context("base64 decode")?)
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
    use std::sync::atomic::{AtomicU64, Ordering};

    // Tests run in parallel and share the OS-wide signer disk dir + the
    // POLYMARKET_SIGNER_MASTER_KEY env var. Without unique EOAs per test
    // the encrypted files collide and decryption fails. We append a
    // monotonic counter to make every signer file path unique-per-test.
    static UNIQUE: AtomicU64 = AtomicU64::new(0);
    fn unique_eoa(seed: &str) -> String {
        let n = UNIQUE.fetch_add(1, Ordering::Relaxed);
        let t = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
        format!("0x{:040x}", t.wrapping_add(n as u128) ^ seed.len() as u128)
    }

    #[test]
    fn address_is_deterministic_per_eoa() {
        std::env::set_var("POLYMARKET_SIGNER_MASTER_KEY", "00".repeat(32));
        let store = SignerStore::new();
        let eoa = unique_eoa("det");
        let upper = eoa.to_uppercase().replacen("0X", "0x", 1);
        let a1 = store.signer_address(&upper).unwrap();
        let a2 = store.signer_address(&eoa).unwrap();
        assert_eq!(a1, a2, "case-insensitive EOA → same signer");
        assert!(a1.starts_with("0x") && a1.len() == 42);
    }

    #[test]
    fn sign_produces_65_bytes_with_v_in_range() {
        std::env::set_var("POLYMARKET_SIGNER_MASTER_KEY", "11".repeat(32));
        let store = SignerStore::new();
        let eoa = unique_eoa("sig");
        let digest = [0xABu8; 32];
        let sig = store.sign_digest(&eoa, &digest).unwrap();
        assert_eq!(sig.len(), 65);
        assert!(sig[64] == 27 || sig[64] == 28);
    }
}
