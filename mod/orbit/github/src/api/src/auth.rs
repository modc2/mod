//! Who is calling — mod-protocol identity, verified here rather than trusted.
//!
//! A token is base64url(JSON) of `{data, time, key, signature}`, exactly what
//! the shared `auth` module mints. The signature covers the compact JSON of
//! `{"data":…,"time":…}` and is a 65-byte secp256k1 signature. Two digests are
//! in circulation across the fleet and both are accepted:
//!
//!   * raw `keccak256(sig_data)`      — what a server key (`m github/token`) signs
//!   * EIP-191 `personal_sign`        — what a browser wallet signs
//!
//! We recover the signer under each and require one to equal the address the
//! token claims. No shared secret, no session table, nothing to leak: a token
//! is a signature or it is nothing.
//!
//! Caveat kept honest: `sig_data` is re-serialized from the parsed payload
//! (serde_json, preserve_order), which reproduces Python's
//! `separators=(',',':')` byte-for-byte for ASCII payloads. A token whose
//! `data` carries non-ASCII would re-serialize differently (Python escapes it
//! as \uXXXX) and is rejected rather than mis-verified.

use base64::Engine;
use k256::ecdsa::{RecoveryId, Signature, VerifyingKey};
use serde::{Deserialize, Serialize};
use tiny_keccak::{Hasher, Keccak};

/// How stale a signed token may be. Matches the Python module's TOKEN_TTL.
pub const TOKEN_TTL: f64 = 3600.0;

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct Token {
    pub data: serde_json::Value,
    pub time: String,
    pub key: String,
    pub signature: String,
}

/// A verified caller. `address` is `None` for anonymous traffic, which is a
/// first-class case here — reads are meant to work with no identity at all.
#[derive(Debug, Clone)]
pub struct Caller {
    pub address: Option<String>,
    pub ip: String,
    /// Why a presented token was rejected, if one was presented. Surfaced by
    /// /whoami so a bad token is debuggable instead of silently anonymous.
    pub token_error: Option<String>,
}

impl Caller {
    /// The rate-limit / audit subject: the key when signed, else the IP.
    pub fn principal(&self) -> String {
        match &self.address {
            Some(a) => a.to_lowercase(),
            None => format!("ip:{}", self.ip),
        }
    }
}

fn keccak256(bytes: &[u8]) -> [u8; 32] {
    let mut k = Keccak::v256();
    let mut out = [0u8; 32];
    k.update(bytes);
    k.finalize(&mut out);
    out
}

fn personal_digest(msg: &str) -> [u8; 32] {
    let mut buf = format!("\x19Ethereum Signed Message:\n{}", msg.len()).into_bytes();
    buf.extend_from_slice(msg.as_bytes());
    keccak256(&buf)
}

/// Recover the lowercase 0x address that signed `digest` with `sig_hex`.
fn recover(digest: &[u8; 32], sig_hex: &str) -> Result<String, String> {
    let raw = hex::decode(sig_hex.trim().trim_start_matches("0x"))
        .map_err(|_| "signature is not hex".to_string())?;
    if raw.len() != 65 {
        return Err(format!("signature must be 65 bytes, got {}", raw.len()));
    }
    let sig = Signature::from_slice(&raw[..64]).map_err(|e| format!("bad signature: {e}"))?;
    // MetaMask signs with the legacy v = 27/28; server keys emit 0/1.
    let v = match raw[64] {
        0 | 27 => 0u8,
        1 | 28 => 1u8,
        other => return Err(format!("unsupported recovery id {other}")),
    };
    let rec = RecoveryId::from_byte(v).ok_or_else(|| "bad recovery id".to_string())?;
    let key = VerifyingKey::recover_from_prehash(digest, &sig, rec)
        .map_err(|e| format!("could not recover signer: {e}"))?;
    let point = key.to_encoded_point(false);
    // Skip the 0x04 SEC1 tag; the address is the last 20 bytes of the hash.
    let hash = keccak256(&point.as_bytes()[1..]);
    Ok(format!("0x{}", hex::encode(&hash[12..])))
}

/// The exact bytes the fleet signs: compact JSON over the `data` and `time`
/// fields, in that order.
fn sig_data(t: &Token) -> Result<String, String> {
    let mut map = serde_json::Map::new();
    map.insert("data".into(), t.data.clone());
    map.insert("time".into(), serde_json::Value::String(t.time.clone()));
    let s = serde_json::to_string(&serde_json::Value::Object(map))
        .map_err(|e| format!("cannot re-serialize token payload: {e}"))?;
    if !s.is_ascii() {
        return Err("token payload contains non-ASCII — cannot reproduce the signed bytes".into());
    }
    Ok(s)
}

/// Verify a bearer token and return the address it proves.
pub fn verify(token: &str, now: f64) -> Result<String, String> {
    let raw = token.trim();
    if raw.is_empty() {
        return Err("empty token".into());
    }
    // base64url, with the padding the minter strips put back.
    let mut b = raw.to_string();
    while b.len() % 4 != 0 {
        b.push('=');
    }
    let bytes = base64::engine::general_purpose::URL_SAFE
        .decode(b.as_bytes())
        .map_err(|_| "token is not base64url".to_string())?;
    let mut parsed: serde_json::Value =
        serde_json::from_slice(&bytes).map_err(|_| "token is not JSON".to_string())?;
    // `m <mod>/token` can hand back {"token": "<inner>"}; unwrap one level.
    if let Some(inner) = parsed.get("token").and_then(|v| v.as_str()) {
        return verify(inner, now);
    }
    let t: Token = serde_json::from_value(parsed.take())
        .map_err(|e| format!("token is missing a field: {e}"))?;

    let ts: f64 = t.time.parse().map_err(|_| "token time is not a number".to_string())?;
    let age = (now - ts).abs();
    if age > TOKEN_TTL {
        return Err(format!("token expired ({age:.0}s old, max {TOKEN_TTL:.0}s)"));
    }

    let msg = sig_data(&t)?;
    let claimed = t.key.to_lowercase();
    if !claimed.starts_with("0x") || claimed.len() != 42 {
        return Err(format!("unsupported key {} — this module verifies 0x/ecdsa keys", t.key));
    }
    for digest in [keccak256(msg.as_bytes()), personal_digest(&msg)] {
        if let Ok(addr) = recover(&digest, &t.signature) {
            if addr == claimed {
                return Ok(t.key.clone());
            }
        }
    }
    Err("signature does not match the key the token claims".into())
}
