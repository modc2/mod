//! Sudo authorization — per-operation, replay-protected signatures from the owner key.
//!
//! The normal bearer/owner gate is enough to *use* claude. But operations that
//! reach OUTSIDE the claude module — editing a sibling module, deleting a module,
//! killing a host process — run with root and can damage the whole host. Those
//! require a *fresh* signature from the owner ("sudo") wallet that is bound to the
//! exact operation, so a captured signature can neither be replayed nor reused for
//! a different target.
//!
//! Wire protocol: callers send an `x-sudo` header whose value is the base64url
//! (no-pad) encoding of:
//!   { "action", "target", "time", "nonce", "key", "signature" }
//!
//! `signature` is an EIP-191 personal_sign over the canonical message produced by
//! [`sudo_message`]. Verification:
//!   1. `action` / `target` must match the operation actually being performed
//!      (the signature is bound to them — it cannot authorize a different write).
//!   2. `time` must be within `WINDOW_SECS` of now (freshness).
//!   3. the recovered signer must equal `key`, and `key` must be the owner.
//!   4. the signature must not have been seen before (replay store on disk).

use crate::auth;
use axum::http::HeaderMap;
use serde::Deserialize;
use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::{Mutex, OnceLock};

/// How fresh a sudo signature must be. Short on purpose: a leaked signature is
/// only dangerous for this many seconds, and it can only be used once anyway.
const WINDOW_SECS: i64 = 120;

/// Cap the token size so a malicious header can't blow up memory.
const MAX_TOKEN_BYTES: usize = 8 * 1024;

#[derive(Deserialize)]
struct SudoToken {
    action: String,
    target: String,
    /// Unix seconds. Accept a JSON number or a stringified number.
    #[serde(deserialize_with = "de_i64")]
    time: i64,
    nonce: String,
    key: String,
    signature: String,
}

fn de_i64<'de, D: serde::Deserializer<'de>>(d: D) -> Result<i64, D::Error> {
    use serde::Deserialize as _;
    match serde_json::Value::deserialize(d)? {
        serde_json::Value::Number(n) => n
            .as_i64()
            .ok_or_else(|| serde::de::Error::custom("time not an integer")),
        serde_json::Value::String(s) => s
            .trim()
            .parse::<i64>()
            .map_err(|_| serde::de::Error::custom("time not a number")),
        _ => Err(serde::de::Error::custom("time wrong type")),
    }
}

/// The exact message the owner signs. MUST stay byte-for-byte in sync with the
/// frontend (`buildSudoMessage`) and any other signer (mod.py `_sudo_message`).
pub fn sudo_message(action: &str, target: &str, time: i64, nonce: &str) -> String {
    [
        "MOD Claude Sudo Authorization".to_string(),
        format!("action: {}", action),
        format!("target: {}", target),
        format!("time: {}", time),
        format!("nonce: {}", nonce),
        String::new(),
        "Authorizing a privileged cross-module operation as the owner. \
         Free signature, not a transaction."
            .to_string(),
    ]
    .join("\n")
}

/// Verify the `x-sudo` header authorizes `(action, target)` right now, as the owner,
/// exactly once. Returns the authorizing address on success or a human error string.
pub fn verify_sudo(headers: &HeaderMap, action: &str, target: &str) -> Result<String, String> {
    let raw = headers
        .get("x-sudo")
        .and_then(|v| v.to_str().ok())
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .ok_or_else(|| {
            "Privileged operation requires a sudo signature (missing x-sudo header)".to_string()
        })?;

    if raw.len() > MAX_TOKEN_BYTES {
        return Err("Sudo token too large".to_string());
    }

    let token = decode_token(raw)?;

    // 1. Binding: the signature is only valid for the operation it was signed for.
    if token.action != action {
        return Err(format!(
            "Sudo action mismatch: token authorizes '{}', request is '{}'",
            token.action, action
        ));
    }
    if token.target != target {
        return Err("Sudo target mismatch: signature was not issued for this path".to_string());
    }

    // 2. Freshness.
    let now = chrono::Utc::now().timestamp();
    if (now - token.time).abs() > WINDOW_SECS {
        return Err(format!(
            "Sudo signature expired or not yet valid (must be within {}s)",
            WINDOW_SECS
        ));
    }

    // Nonce sanity — bounded, non-empty, keeps the on-disk store tidy.
    if token.nonce.is_empty() || token.nonce.len() > 128 {
        return Err("Sudo nonce missing or malformed".to_string());
    }

    // 3. Recover the signer and require it to be the owner (the sudo key).
    let message = sudo_message(&token.action, &token.target, token.time, &token.nonce);
    let recovered = auth::recover_eth_address(&message, &token.signature)
        .map_err(|e| format!("Sudo signature verification failed: {}", e))?;

    if !recovered.eq_ignore_ascii_case(&token.key) {
        return Err(format!(
            "Sudo signer mismatch: recovered {} but token claims {}",
            recovered, token.key
        ));
    }
    if !auth::is_owner(&recovered) {
        return Err("Sudo denied: signer is not the owner (sudo) key".to_string());
    }

    // 4. Replay protection — reject any signature already submitted, then record it.
    //    Keyed on the signature itself: "was this exact signature already used?".
    let sig_key = token.signature.trim_start_matches("0x").to_lowercase();
    guard().check_and_record(&sig_key, now + WINDOW_SECS)?;

    Ok(recovered.to_lowercase())
}

fn decode_token(raw: &str) -> Result<SudoToken, String> {
    use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
    let bytes = URL_SAFE_NO_PAD
        .decode(raw)
        .map_err(|e| format!("Sudo token base64 decode failed: {}", e))?;
    serde_json::from_slice::<SudoToken>(&bytes)
        .map_err(|e| format!("Sudo token JSON parse failed: {}", e))
}

// ── Replay store ────────────────────────────────────────────────────────────
// Used signatures live in memory and are mirrored to disk so a restart can't
// reset the anti-replay set. Each entry carries an expiry == the last instant
// the signature could possibly be accepted; expired entries are pruned, which
// keeps the file bounded (a signature can't be replayed after WINDOW_SECS anyway,
// but persisting it for its full validity window closes the restart-replay gap).

struct SudoStore {
    used: HashMap<String, i64>, // signature → expiry unix secs
    path: PathBuf,
    loaded: bool,
}

fn store_path() -> PathBuf {
    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string());
    PathBuf::from(home)
        .join(".mod")
        .join("claude")
        .join("sudo_used.json")
}

fn guard() -> &'static Mutex<SudoStore> {
    static STORE: OnceLock<Mutex<SudoStore>> = OnceLock::new();
    STORE.get_or_init(|| {
        Mutex::new(SudoStore {
            used: HashMap::new(),
            path: store_path(),
            loaded: false,
        })
    })
}

impl SudoStore {
    fn load_once(&mut self) {
        if self.loaded {
            return;
        }
        self.loaded = true;
        if let Ok(content) = std::fs::read_to_string(&self.path) {
            if let Ok(map) = serde_json::from_str::<HashMap<String, i64>>(&content) {
                self.used = map;
            }
        }
    }

    fn persist(&self) {
        if let Some(parent) = self.path.parent() {
            let _ = std::fs::create_dir_all(parent);
        }
        if let Ok(json) = serde_json::to_string(&self.used) {
            let _ = std::fs::write(&self.path, json);
        }
    }

    fn check_and_record(&mut self, sig_key: &str, expiry: i64) -> Result<(), String> {
        self.load_once();
        let now = chrono::Utc::now().timestamp();
        self.used.retain(|_, &mut exp| exp > now); // prune expired
        if self.used.contains_key(sig_key) {
            return Err("Sudo signature already used — replay rejected".to_string());
        }
        self.used.insert(sig_key.to_string(), expiry);
        self.persist();
        Ok(())
    }
}

trait GuardExt {
    fn check_and_record(&self, sig_key: &str, expiry: i64) -> Result<(), String>;
}

impl GuardExt for Mutex<SudoStore> {
    fn check_and_record(&self, sig_key: &str, expiry: i64) -> Result<(), String> {
        let mut store = self
            .lock()
            .map_err(|_| "Sudo store lock poisoned".to_string())?;
        store.check_and_record(sig_key, expiry)
    }
}

// ── Tests ────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
    use k256::ecdsa::SigningKey;
    use sha3::{Digest, Keccak256};

    fn eth_sign(key: &SigningKey, message: &str) -> String {
        let prefix = format!("\x19Ethereum Signed Message:\n{}", message.len());
        let mut hasher = Keccak256::new();
        hasher.update(prefix.as_bytes());
        hasher.update(message.as_bytes());
        let hash = hasher.finalize();
        let (sig, recid) = key.sign_prehash_recoverable(&hash).unwrap();
        let mut bytes = Vec::with_capacity(65);
        bytes.extend_from_slice(&sig.to_bytes());
        bytes.push(if recid.is_y_odd().into() { 28 } else { 27 });
        format!("0x{}", hex::encode(&bytes))
    }

    fn address_of(key: &SigningKey) -> String {
        let vk = key.verifying_key();
        let pt = vk.to_encoded_point(false);
        let mut h = Keccak256::new();
        h.update(&pt.as_bytes()[1..]);
        let digest = h.finalize();
        format!("0x{}", hex::encode(&digest[12..]))
    }

    fn make_header(action: &str, target: &str, time: i64, nonce: &str, key: &SigningKey) -> HeaderMap {
        let addr = address_of(key);
        let message = sudo_message(action, target, time, nonce);
        let signature = eth_sign(key, &message);
        let token = serde_json::json!({
            "action": action, "target": target, "time": time,
            "nonce": nonce, "key": addr, "signature": signature,
        });
        let encoded = URL_SAFE_NO_PAD.encode(token.to_string().as_bytes());
        let mut h = HeaderMap::new();
        h.insert("x-sudo", encoded.parse().unwrap());
        h
    }

    // Point the owner + replay store at a temp dir so tests are isolated and the
    // owner check passes for our throwaway key.
    // HOME is process-global; serialize the tests that mutate it.
    fn test_lock() -> std::sync::MutexGuard<'static, ()> {
        static LOCK: OnceLock<Mutex<()>> = OnceLock::new();
        LOCK.get_or_init(|| Mutex::new(()))
            .lock()
            .unwrap_or_else(|e| e.into_inner())
    }

    fn setup_owner(key: &SigningKey) -> tempdir_guard::TempHome {
        let home = tempdir_guard::TempHome::new();
        let claude = home.path().join(".mod").join("claude");
        std::fs::create_dir_all(&claude).unwrap();
        std::fs::write(
            claude.join("owner.json"),
            serde_json::json!({ "owner": address_of(key).to_lowercase() }).to_string(),
        )
        .unwrap();
        home
    }

    #[test]
    fn valid_signature_accepted_once_then_replay_rejected() {
        let _g = test_lock();
        let key = SigningKey::random(&mut rand::thread_rng());
        let _home = setup_owner(&key);
        let now = chrono::Utc::now().timestamp();
        let headers = make_header("write", "/root/mod/mod/orbit/venice/x.rs", now, "abc123", &key);

        let first = verify_sudo(&headers, "write", "/root/mod/mod/orbit/venice/x.rs");
        assert!(first.is_ok(), "first use should pass: {:?}", first.err());

        let replay = verify_sudo(&headers, "write", "/root/mod/mod/orbit/venice/x.rs");
        assert!(replay.is_err());
        assert!(replay.unwrap_err().contains("replay"));
    }

    #[test]
    fn target_mismatch_rejected() {
        let _g = test_lock();
        let key = SigningKey::random(&mut rand::thread_rng());
        let _home = setup_owner(&key);
        let now = chrono::Utc::now().timestamp();
        let headers = make_header("write", "/a/b.rs", now, "n1", &key);
        let r = verify_sudo(&headers, "write", "/a/EVIL.rs");
        assert!(r.unwrap_err().contains("target mismatch"));
    }

    #[test]
    fn stale_signature_rejected() {
        let _g = test_lock();
        let key = SigningKey::random(&mut rand::thread_rng());
        let _home = setup_owner(&key);
        let old = chrono::Utc::now().timestamp() - 10_000;
        let headers = make_header("write", "/a/b.rs", old, "n2", &key);
        let r = verify_sudo(&headers, "write", "/a/b.rs");
        assert!(r.unwrap_err().contains("expired"));
    }

    #[test]
    fn non_owner_rejected() {
        let _g = test_lock();
        let owner = SigningKey::random(&mut rand::thread_rng());
        let attacker = SigningKey::random(&mut rand::thread_rng());
        let _home = setup_owner(&owner);
        let now = chrono::Utc::now().timestamp();
        // attacker signs a well-formed, fresh token for their own key
        let headers = make_header("write", "/a/b.rs", now, "n3", &attacker);
        let r = verify_sudo(&headers, "write", "/a/b.rs");
        assert!(r.unwrap_err().contains("not the owner"));
    }

    #[test]
    fn missing_header_rejected() {
        let r = verify_sudo(&HeaderMap::new(), "write", "/a/b.rs");
        assert!(r.unwrap_err().contains("missing x-sudo"));
    }
}

// Minimal HOME override for tests (no external deps). Sets $HOME for the process
// duration of the test and restores it on drop. Tests using it run serially via
// the shared owner/replay state, which is acceptable for this small suite.
#[cfg(test)]
mod tempdir_guard {
    use std::path::{Path, PathBuf};

    pub struct TempHome {
        dir: PathBuf,
        prev: Option<String>,
    }

    impl TempHome {
        pub fn new() -> Self {
            let base = std::env::temp_dir().join(format!(
                "claude-sudo-test-{}",
                hex::encode(rand::random::<[u8; 8]>())
            ));
            std::fs::create_dir_all(&base).unwrap();
            let prev = std::env::var("HOME").ok();
            std::env::set_var("HOME", &base);
            TempHome { dir: base, prev }
        }
        pub fn path(&self) -> &Path {
            &self.dir
        }
    }

    impl Drop for TempHome {
        fn drop(&mut self) {
            match &self.prev {
                Some(p) => std::env::set_var("HOME", p),
                None => std::env::remove_var("HOME"),
            }
            let _ = std::fs::remove_dir_all(&self.dir);
        }
    }
}
