//! mod **protocol-auth** token verification — the Rust counterpart of
//! `mod/core/server/auth/auth/auth.py`.
//!
//! A client (browser wallet) builds a time-bounded bearer token:
//!
//!   1. `sigData = JSON.stringify({ data, time })`   (compact, no spaces)
//!   2. `signature = personal_sign(sigData)`         (EIP-191, MetaMask)
//!   3. `token = base64url(JSON.stringify({ data, time, key, signature }))`
//!
//! and sends it as `Authorization: Bearer <token>`. We verify statelessly:
//! reconstruct `sigData` byte-for-byte, recover the signer address from the
//! EIP-191 signature, and require it to equal the embedded `key`, within
//! `max_age` seconds of now. No server-side nonce/session table.
//!
//! Matching the Python verifier exactly matters: `sigData` is rebuilt as
//! `{"data":<data>,"time":"<time>"}` with `serde_json` (compact separators).
//! `preserve_order` keeps `data`'s keys in the order they were signed in.

use base64::Engine;
use k256::ecdsa::{RecoveryId, Signature, VerifyingKey};
use serde_json::Value;
use sha3::{Digest, Keccak256};

/// Verify a protocol-auth bearer token. On success returns the signer's
/// lowercase 0x-prefixed Ethereum address. On any failure returns an error
/// string suitable for a 401 body (no secrets leak through it).
pub fn verify_token(token: &str, max_age_secs: u64) -> Result<String, String> {
    let token = token.trim();
    let raw = b64url_decode(token).map_err(|e| format!("bad token encoding: {e}"))?;
    let envelope: Value =
        serde_json::from_slice(&raw).map_err(|e| format!("bad token json: {e}"))?;

    let data = envelope.get("data").cloned().unwrap_or(Value::Object(Default::default()));
    let time = envelope
        .get("time")
        .and_then(|v| v.as_str())
        .ok_or("token missing time")?;
    let key = envelope
        .get("key")
        .and_then(|v| v.as_str())
        .ok_or("token missing key")?;
    let signature = envelope
        .get("signature")
        .and_then(|v| v.as_str())
        .ok_or("token missing signature")?;

    if !key.starts_with("0x") || key.len() != 42 {
        return Err("token key is not an ethereum address".into());
    }

    // Staleness check (abs diff, mirroring the Python verifier).
    let t: f64 = time.parse().map_err(|_| "token time not a number".to_string())?;
    let now = chrono::Utc::now().timestamp() as f64;
    if (now - t).abs() > max_age_secs as f64 {
        return Err(format!("token is stale ({}s > {}s)", (now - t).abs() as i64, max_age_secs));
    }

    // Reconstruct the exact signed string: {"data":<data>,"time":"<time>"}.
    let data_str = serde_json::to_string(&data).map_err(|e| e.to_string())?;
    let time_str = serde_json::to_string(time).map_err(|e| e.to_string())?; // adds quotes
    let sig_data = format!("{{\"data\":{data_str},\"time\":{time_str}}}");

    // The mod auth scheme accepts two signing modes, mirroring the Python
    // verifier: (1) EIP-191 `personal_sign` — what browser wallets (MetaMask)
    // produce; (2) plain `keccak256(message)` — what an in-process mod key
    // (`eth_keys` `sign_msg`) produces. Try both and accept either match.
    let msg = sig_data.as_bytes();
    let eip191_prehash = {
        let prefix = format!("\x19Ethereum Signed Message:\n{}", msg.len());
        let mut buf = Vec::with_capacity(prefix.len() + msg.len());
        buf.extend_from_slice(prefix.as_bytes());
        buf.extend_from_slice(msg);
        keccak256(&buf)
    };
    let plain_prehash = keccak256(msg);

    for prehash in [eip191_prehash, plain_prehash] {
        if let Ok(addr) = recover_address(&prehash, signature) {
            if addr.eq_ignore_ascii_case(key) {
                return Ok(key.to_lowercase());
            }
        }
    }
    Err("signature does not match token key".into())
}

/// Recover the signer's 0x address from a 65-byte signature over a 32-byte
/// prehash. Accepts both legacy `v ∈ {27,28}` and `v ∈ {0,1}`.
fn recover_address(prehash: &[u8; 32], signature_hex: &str) -> Result<String, String> {
    let sig_hex = signature_hex.strip_prefix("0x").unwrap_or(signature_hex);
    let sig_bytes = hex::decode(sig_hex).map_err(|_| "signature not hex".to_string())?;
    if sig_bytes.len() != 65 {
        return Err(format!("signature must be 65 bytes, got {}", sig_bytes.len()));
    }
    let rec_raw = sig_bytes[64];
    let rec_id = if rec_raw >= 27 { rec_raw - 27 } else { rec_raw };
    let recovery_id = RecoveryId::from_byte(rec_id).ok_or("bad recovery id")?;
    let signature = Signature::from_slice(&sig_bytes[..64]).map_err(|e| format!("bad sig: {e}"))?;
    let vk = VerifyingKey::recover_from_prehash(prehash, &signature, recovery_id)
        .map_err(|e| format!("recover failed: {e}"))?;
    Ok(address_from_pubkey(&vk))
}

/// Ethereum address = last 20 bytes of keccak256(uncompressed_pubkey[1..]).
fn address_from_pubkey(vk: &VerifyingKey) -> String {
    let point = vk.to_encoded_point(false);
    let bytes = point.as_bytes(); // 0x04 || X || Y
    let hash = keccak256(&bytes[1..]);
    format!("0x{}", hex::encode(&hash[12..]))
}

pub fn keccak256(data: &[u8]) -> [u8; 32] {
    let mut h = Keccak256::new();
    h.update(data);
    let out = h.finalize();
    let mut o = [0u8; 32];
    o.copy_from_slice(&out);
    o
}

/// Decode unpadded (or padded) base64url. The JS client strips `=` padding to
/// match Python's `urlsafe_b64encode(...).rstrip(b'=')`.
fn b64url_decode(s: &str) -> Result<Vec<u8>, String> {
    let trimmed = s.trim_end_matches('=');
    base64::engine::general_purpose::URL_SAFE_NO_PAD
        .decode(trimmed)
        .map_err(|e| e.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    // A token captured from the browser client (wallet personal_sign) would be
    // the real fixture; here we just assert the failure paths are sane so a
    // malformed/forged token never authenticates.
    #[test]
    fn rejects_garbage() {
        assert!(verify_token("not-a-token", 3600).is_err());
        assert!(verify_token("", 3600).is_err());
    }

    #[test]
    fn rejects_tampered_address() {
        // Well-formed envelope but signature won't recover to `key`.
        let env = serde_json::json!({
            "data": {"scope": "venice"},
            "time": format!("{}", chrono::Utc::now().timestamp()),
            "key": "0x000000000000000000000000000000000000dead",
            "signature": format!("0x{}", "11".repeat(65)),
        });
        let token = base64::engine::general_purpose::URL_SAFE_NO_PAD
            .encode(serde_json::to_vec(&env).unwrap());
        assert!(verify_token(&token, 3600).is_err());
    }
}
