//! Deterministic wallets for tests and for the dev console.
//!
//! Shipped in the library rather than behind `#[cfg(test)]` because the
//! integration tests are a separate crate and the `/dev/sign` endpoint uses
//! the same keys to drive the console without a browser wallet. The keys are
//! `sha256("prerank:test-key:<n>")` — nothing here is a secret, and nothing
//! here is reachable unless `PRERANK_OPEN=1`.

use k256::ecdsa::{signature::hazmat::PrehashSigner, SigningKey};

use crate::crypto::{eip191_digest, keccak256, sha256_hex};

/// The private key for test wallet `n`, as 32 bytes.
pub fn key_bytes(n: u64) -> [u8; 32] {
    let hex_str = sha256_hex(format!("prerank:test-key:{n}").as_bytes());
    let raw = hex::decode(hex_str).expect("sha256 output is hex");
    let mut buf = [0u8; 32];
    buf.copy_from_slice(&raw);
    buf
}

/// The address of test wallet `n`.
pub fn address(n: u64) -> String {
    let key = SigningKey::from_slice(&key_bytes(n)).expect("valid scalar");
    let point = key.verifying_key().to_encoded_point(false);
    let hash = keccak256(&point.as_bytes()[1..]);
    format!("0x{}", hex::encode(&hash[12..]))
}

/// Sign `message` as test wallet `n`; returns `(address, signature_hex)`.
pub fn sign(n: u64, message: &str) -> (String, String) {
    let key = SigningKey::from_slice(&key_bytes(n)).expect("valid scalar");
    let digest = eip191_digest(message);
    let (sig, rid) = key.sign_prehash(&digest).expect("sign");
    let mut raw = sig.to_bytes().to_vec();
    raw.push(rid.to_byte() + 27);
    (address(n), format!("0x{}", hex::encode(raw)))
}
