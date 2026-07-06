//! Authentication via MetaMask signatures and HMAC bearer tokens

use hmac::{Hmac, Mac};
use k256::ecdsa::{RecoveryId, Signature, VerifyingKey};
use k256::ecdsa::signature::hazmat::PrehashVerifier;
use rand::Rng;
use sha2::Sha256;
use sha3::Keccak256;
use sha3::Digest;
use std::sync::OnceLock;

type HmacSha256 = Hmac<Sha256>;

static HMAC_SECRET: OnceLock<Vec<u8>> = OnceLock::new();

pub fn init_secret() {
    let secret: Vec<u8> = rand::thread_rng()
        .sample_iter(rand::distributions::Standard)
        .take(32)
        .collect();
    HMAC_SECRET.set(secret).ok();
}

/// Sign an address with HMAC to create a bearer token
pub fn sign_address(address: &str) -> String {
    let secret = HMAC_SECRET.get().expect("HMAC secret not initialized");
    let mut mac = HmacSha256::new_from_slice(secret).expect("HMAC init failed");
    mac.update(address.as_bytes());
    let result = mac.finalize().into_bytes();
    hex::encode(result)
}

/// Verify a bearer token matches the given address
pub fn verify_token(address: &str, token: &str) -> bool {
    sign_address(address) == token
}

/// Recover Ethereum address from MetaMask personal_sign signature
///
/// MetaMask uses the format:
/// "\x19Ethereum Signed Message:\n" + len(message) + message
pub fn recover_address(message: &str, signature: &str) -> Result<String, String> {
    let sig_bytes = hex::decode(signature.strip_prefix("0x").unwrap_or(signature))
        .map_err(|_| "Invalid signature hex".to_string())?;

    if sig_bytes.len() != 65 {
        return Err("Signature must be 65 bytes".to_string());
    }

    // Parse r, s, v
    let r = &sig_bytes[0..32];
    let s = &sig_bytes[32..64];
    let mut v = sig_bytes[64];

    // Normalize v (MetaMask sends 27/28, we need 0/1)
    if v >= 27 {
        v -= 27;
    }

    let recovery_id = RecoveryId::try_from(v)
        .map_err(|_| "Invalid recovery ID".to_string())?;

    // Construct the Ethereum signed message prefix
    let prefix = format!("\x19Ethereum Signed Message:\n{}", message.len());
    let mut eth_message = Vec::new();
    eth_message.extend_from_slice(prefix.as_bytes());
    eth_message.extend_from_slice(message.as_bytes());

    // Hash the prefixed message
    let mut hasher = Keccak256::new();
    hasher.update(&eth_message);
    let message_hash = hasher.finalize();

    // Create signature
    let mut sig_bytes_64 = [0u8; 64];
    sig_bytes_64[0..32].copy_from_slice(r);
    sig_bytes_64[32..64].copy_from_slice(s);

    let signature = Signature::from_bytes(&sig_bytes_64.into())
        .map_err(|_| "Invalid signature".to_string())?;

    // Recover public key
    let recovered_key = VerifyingKey::recover_from_prehash(
        &message_hash[..],
        &signature,
        recovery_id,
    )
    .map_err(|_| "Failed to recover public key".to_string())?;

    // Verify signature
    recovered_key
        .verify_prehash(&message_hash[..], &signature)
        .map_err(|_| "Signature verification failed".to_string())?;

    // Extract address from public key (last 20 bytes of keccak256(pubkey))
    let pubkey_bytes = recovered_key.to_encoded_point(false);
    let pubkey_uncompressed = &pubkey_bytes.as_bytes()[1..]; // Skip 0x04 prefix

    let mut hasher = Keccak256::new();
    hasher.update(pubkey_uncompressed);
    let hash = hasher.finalize();
    let address = &hash[12..]; // Last 20 bytes

    Ok(format!("0x{}", hex::encode(address)))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_sign_and_verify() {
        init_secret();
        let address = "0x742d35Cc6634C0532925a3b844Bc9e7595f0bEb";
        let token = sign_address(address);
        assert!(verify_token(address, &token));
        assert!(!verify_token("0x0000000000000000000000000000000000000000", &token));
    }
}
