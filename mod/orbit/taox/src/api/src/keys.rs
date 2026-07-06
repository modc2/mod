use std::fs;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use tiny_keccak::{Hasher, Keccak};

#[derive(Serialize, Deserialize)]
struct StoredKey {
    chain: String,
    address: String,
    private_key_hex: String,
}

fn keys_dir(store_dir: &Path) -> PathBuf {
    store_dir.join("keys")
}

fn write_secure(path: &Path, body: &str) -> anyhow::Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    fs::write(path, body)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mut perm = fs::metadata(path)?.permissions();
        perm.set_mode(0o600);
        fs::set_permissions(path, perm)?;
    }
    Ok(())
}

fn keccak256(input: &[u8]) -> [u8; 32] {
    let mut out = [0u8; 32];
    let mut hasher = Keccak::v256();
    hasher.update(input);
    hasher.finalize(&mut out);
    out
}

/// Load `{store_dir}/keys/eth.json` if present, otherwise generate a new
/// secp256k1 keypair, derive its 0x-prefixed Ethereum address, persist the
/// secret with 0600 permissions, and return the address.
pub fn eth_address(store_dir: &Path) -> anyhow::Result<String> {
    let path = keys_dir(store_dir).join("eth.json");
    if path.exists() {
        let body = fs::read_to_string(&path)?;
        let stored: StoredKey = serde_json::from_str(&body)?;
        return Ok(stored.address);
    }

    let signing_key = k256::ecdsa::SigningKey::random(&mut rand::thread_rng());
    let secret_bytes = signing_key.to_bytes();
    let verifying_key = signing_key.verifying_key();
    // Uncompressed SEC1: 0x04 || X(32) || Y(32). Drop the 0x04 byte before
    // hashing per Ethereum's address derivation.
    let pub_uncompressed = verifying_key.to_encoded_point(false);
    let pub_xy = &pub_uncompressed.as_bytes()[1..];
    let hash = keccak256(pub_xy);
    let mut addr_bytes = [0u8; 20];
    addr_bytes.copy_from_slice(&hash[12..]);
    let address = format!("0x{}", hex::encode(addr_bytes));

    let stored = StoredKey {
        chain: "ethereum".into(),
        address: address.clone(),
        private_key_hex: hex::encode(secret_bytes),
    };
    write_secure(&path, &serde_json::to_string_pretty(&stored)?)?;
    Ok(address)
}

/// Load `{store_dir}/keys/sol.json` if present, otherwise generate a new
/// ed25519 keypair, base58-encode the public key as the Solana address,
/// persist the secret seed with 0600 permissions, and return the address.
pub fn sol_address(store_dir: &Path) -> anyhow::Result<String> {
    let path = keys_dir(store_dir).join("sol.json");
    if path.exists() {
        let body = fs::read_to_string(&path)?;
        let stored: StoredKey = serde_json::from_str(&body)?;
        return Ok(stored.address);
    }

    let signing = ed25519_dalek::SigningKey::generate(&mut rand::thread_rng());
    let secret_bytes = signing.to_bytes();
    let pub_bytes = signing.verifying_key().to_bytes();
    let address = bs58::encode(&pub_bytes).into_string();

    let stored = StoredKey {
        chain: "solana".into(),
        address: address.clone(),
        private_key_hex: hex::encode(secret_bytes),
    };
    write_secure(&path, &serde_json::to_string_pretty(&stored)?)?;
    Ok(address)
}
