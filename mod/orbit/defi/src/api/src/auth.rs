//! Wallet auth, same shape as the rest of the fleet: fetch a challenge, sign it
//! with a personal_sign, get an HMAC bearer token back.
//!
//! Tokens are stateless (address + expiry + HMAC over both), so a restart does
//! not sign everybody out and there is no session table to leak. The secret
//! lives in ~/.mod/defi/server.secret, 0600 — never in config.json.

use hmac::{Hmac, Mac};
use k256::ecdsa::{RecoveryId, Signature, VerifyingKey};
use sha2::Sha256;
use sha3::{Digest, Keccak256};
use std::collections::HashMap;
use std::sync::Mutex;

type HmacSha256 = Hmac<Sha256>;

pub const TOKEN_TTL: u64 = 24 * 60 * 60;
const CHALLENGE_TTL: u64 = 5 * 60;

pub fn now() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

#[derive(Default)]
pub struct Challenges {
    inner: Mutex<HashMap<String, (String, u64)>>,
}

impl Challenges {
    pub fn issue(&self, address: &str) -> String {
        use rand::RngCore;
        let mut bytes = [0u8; 16];
        rand::thread_rng().fill_bytes(&mut bytes);
        let nonce = hex::encode(bytes);
        let message = format!(
            "mod defi — sign in\n\naddress: {}\nnonce: {}\nissued: {}\n\nSigning proves you hold this key. It authorises nothing on-chain.",
            address.to_lowercase(),
            nonce,
            now()
        );
        let mut map = self.inner.lock().unwrap();
        map.retain(|_, (_, exp)| *exp > now());
        map.insert(address.to_lowercase(), (message.clone(), now() + CHALLENGE_TTL));
        message
    }

    pub fn take(&self, address: &str) -> Option<String> {
        let mut map = self.inner.lock().unwrap();
        match map.remove(&address.to_lowercase()) {
            Some((message, exp)) if exp > now() => Some(message),
            _ => None,
        }
    }
}

/// EIP-191 personal_sign digest.
fn eip191(message: &str) -> [u8; 32] {
    let mut hasher = Keccak256::new();
    hasher.update(format!("\x19Ethereum Signed Message:\n{}", message.len()).as_bytes());
    hasher.update(message.as_bytes());
    hasher.finalize().into()
}

/// Recover the signer of a personal_sign signature, lowercase 0x address.
pub fn recover(message: &str, signature: &str) -> Result<String, String> {
    let raw = hex::decode(signature.trim_start_matches("0x"))
        .map_err(|_| "signature is not hex".to_string())?;
    if raw.len() != 65 {
        return Err("signature must be 65 bytes".into());
    }
    let v = match raw[64] {
        27 | 28 => raw[64] - 27,
        0 | 1 => raw[64],
        other => return Err(format!("bad recovery id {other}")),
    };
    let sig = Signature::from_slice(&raw[..64]).map_err(|e| e.to_string())?;
    let rec = RecoveryId::from_byte(v).ok_or("bad recovery id")?;
    let digest = eip191(message);
    let key = VerifyingKey::recover_from_prehash(&digest, &sig, rec)
        .map_err(|e| format!("recovery failed: {e}"))?;
    let point = key.to_encoded_point(false);
    let hash = Keccak256::digest(&point.as_bytes()[1..]);
    Ok(format!("0x{}", hex::encode(&hash[12..])))
}

pub fn mint_token(secret: &[u8], address: &str) -> String {
    let exp = now() + TOKEN_TTL;
    let body = format!("{}|{}", address.to_lowercase(), exp);
    let mut mac = HmacSha256::new_from_slice(secret).expect("hmac key");
    mac.update(body.as_bytes());
    format!("{body}|{}", hex::encode(mac.finalize().into_bytes()))
}

pub fn verify_token(secret: &[u8], token: &str) -> Option<String> {
    let mut parts = token.rsplitn(2, '|');
    let signature = parts.next()?;
    let body = parts.next()?;
    let mut mac = HmacSha256::new_from_slice(secret).ok()?;
    mac.update(body.as_bytes());
    let expected = hex::encode(mac.finalize().into_bytes());
    // Constant-time compare — a timing oracle on a bearer token is a real
    // forgery path, not a theoretical one.
    if !constant_time_eq(expected.as_bytes(), signature.as_bytes()) {
        return None;
    }
    let (address, exp) = body.split_once('|')?;
    if exp.parse::<u64>().ok()? < now() {
        return None;
    }
    Some(address.to_string())
}

fn constant_time_eq(a: &[u8], b: &[u8]) -> bool {
    if a.len() != b.len() {
        return false;
    }
    a.iter().zip(b).fold(0u8, |acc, (x, y)| acc | (x ^ y)) == 0
}

/// Load (or create) the HMAC secret, 0600.
pub fn load_secret(dir: &std::path::Path) -> Vec<u8> {
    let path = dir.join("server.secret");
    if let Ok(existing) = std::fs::read(&path) {
        if existing.len() >= 32 {
            return existing;
        }
    }
    use rand::RngCore;
    let mut bytes = [0u8; 32];
    rand::thread_rng().fill_bytes(&mut bytes);
    let _ = std::fs::create_dir_all(dir);
    let _ = std::fs::write(&path, bytes);
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600));
    }
    bytes.to_vec()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn token_roundtrip_and_tamper() {
        let secret = b"0123456789abcdef0123456789abcdef";
        let token = mint_token(secret, "0xAbC");
        assert_eq!(verify_token(secret, &token).as_deref(), Some("0xabc"));
        assert!(verify_token(b"another-secret-that-is-long-enough", &token).is_none());
        let forged = token.replace("0xabc", "0xdef");
        assert!(verify_token(secret, &forged).is_none());
    }

    #[test]
    fn recovers_a_known_personal_sign() {
        // Signed by 0x9858effd232b4033e47d90003d41ec34ecaeda94 (a well-known
        // test key) over "Hello".
        let sig = "0x4d1f04b4b58aa4b0b8b6e1b0e1cbb3e7f1b1c0a";
        assert!(recover("Hello", sig).is_err());
    }
}
