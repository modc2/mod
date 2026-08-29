//! Key types — what kind of key an address is, and how to check a signature from it.
//!
//! This console is multichain: one identity, three curves. A signer proves an
//! address by signing the challenge with whatever key the address encodes, and
//! the address *format itself* says which curve to verify against:
//!
//!   secp256k1  0x + 40 hex          EVM (MetaMask, Rabby, Coinbase, …)
//!   ed25519    base58, 32 bytes     Solana (Phantom, Solflare, Backpack)
//!   sr25519    SS58 (base58+crc)    Substrate (SubWallet, Talisman, polkadot-js, Bittensor)
//!
//! SS58 can also carry an ed25519 key, so the client may state its key type
//! explicitly; when it does we honour it, and when it doesn't we infer from the
//! address and — for SS58 — try sr25519 first, then ed25519 (Substrate
//! addresses give no in-band hint about which curve minted them).
//!
//! CASE MATTERS. Only EVM addresses are hex, and only hex is case-insensitive;
//! base58 and SS58 lose their public key if lowercased. `normalize_addr` is
//! therefore the one canonicaliser every identity-keyed store must use in
//! place of a bare `.to_lowercase()`.

use blake2::{Blake2b512, Digest as _};

/// EIP-191 recovery lives in `auth` (sudo signatures share it); re-exported
/// here so every curve this module knows about is reachable from one place.
pub use crate::auth::recover_eth_address;

/// The curve behind an address.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum KeyType {
    /// ECDSA over secp256k1, EIP-191 `personal_sign` — every EVM chain.
    Secp256k1,
    /// Edwards-curve DSA over curve25519 — Solana, and Substrate's other curve.
    Ed25519,
    /// Schnorr over ristretto25519 — Substrate's default (Polkadot, Bittensor).
    Sr25519,
}

impl KeyType {
    pub fn id(&self) -> &'static str {
        match self {
            KeyType::Secp256k1 => "secp256k1",
            KeyType::Ed25519 => "ed25519",
            KeyType::Sr25519 => "sr25519",
        }
    }

    pub fn label(&self) -> &'static str {
        match self {
            KeyType::Secp256k1 => "secp256k1 · EVM",
            KeyType::Ed25519 => "ed25519 · Solana",
            KeyType::Sr25519 => "sr25519 · Substrate",
        }
    }

    /// Chain families an identity of this key type can act on. Purely
    /// descriptive — the console shows it so a signer knows what their key
    /// reaches before they sign.
    pub fn networks(&self) -> &'static [&'static str] {
        match self {
            KeyType::Secp256k1 => &[
                "Ethereum", "Base", "Arbitrum", "Optimism", "Polygon", "BNB Chain", "Avalanche",
            ],
            KeyType::Ed25519 => &["Solana", "Eclipse"],
            KeyType::Sr25519 => &["Polkadot", "Kusama", "Bittensor", "Substrate parachains"],
        }
    }

    pub fn from_id(s: &str) -> Option<KeyType> {
        match s.trim().to_lowercase().as_str() {
            "secp256k1" | "evm" | "ethereum" => Some(KeyType::Secp256k1),
            "ed25519" | "solana" => Some(KeyType::Ed25519),
            "sr25519" | "substrate" | "polkadot" => Some(KeyType::Sr25519),
            _ => None,
        }
    }
}

/// Which curve an address encodes, judged from its shape alone.
///
/// SS58 and raw-base58 Solana addresses are both base58, so they are told
/// apart by what the bytes decode to: SS58 carries a network prefix and a
/// 2-byte blake2 checksum, a Solana pubkey is a bare 32 bytes.
pub fn detect_key_type(address: &str) -> Option<KeyType> {
    let a = address.trim();
    if a.is_empty() {
        return None;
    }
    if let Some(hex) = a.strip_prefix("0x").or_else(|| a.strip_prefix("0X")) {
        if hex.len() == 40 && hex.chars().all(|c| c.is_ascii_hexdigit()) {
            return Some(KeyType::Secp256k1);
        }
        return None;
    }
    if decode_ss58(a).is_some() {
        // Substrate's default curve. `verify` still falls back to ed25519 for
        // the wallets that hold an ed25519 key at an SS58 address.
        return Some(KeyType::Sr25519);
    }
    if decode_solana(a).is_some() {
        return Some(KeyType::Ed25519);
    }
    None
}

/// Canonical on-disk form of an address.
///
/// EVM addresses are hex and therefore case-insensitive — lowercasing them
/// keeps one identity from splitting across checksum and non-checksum spellings.
/// Everything else is base58, where case IS the key, so it is left exactly as
/// the wallet gave it.
pub fn normalize_addr(address: &str) -> String {
    let a = address.trim();
    match detect_key_type(a) {
        Some(KeyType::Secp256k1) => a.to_lowercase(),
        Some(_) => a.to_string(),
        // Unrecognised shape: guests (`guest_…`), `local`, legacy rows. These
        // were all lowercased before this module existed — keep that.
        None => a.to_lowercase(),
    }
}

/// True when two addresses name the same identity, respecting each format's
/// own case rules.
pub fn addr_eq(a: &str, b: &str) -> bool {
    normalize_addr(a) == normalize_addr(b)
}

// ── Address decoding ─────────────────────────────────────────────────

/// Decode an SS58 address to (network prefix, 32-byte public key).
///
/// Layout: `prefix ++ pubkey ++ blake2b512("SS58PRE" ++ prefix ++ pubkey)[..2]`,
/// base58 over the whole thing. The prefix is 1 byte below 64, else 2 bytes
/// with the six low bits of the first byte holding the high bits of the id.
pub fn decode_ss58(address: &str) -> Option<(u16, [u8; 32])> {
    let raw = bs58::decode(address).into_vec().ok()?;
    // 1-or-2 byte prefix + 32 byte key + 2 byte checksum
    if raw.len() != 35 && raw.len() != 36 {
        return None;
    }
    let (prefix_len, prefix) = match raw[0] {
        0..=63 => (1usize, raw[0] as u16),
        64..=127 => {
            // Two-byte prefix, six bits from each byte, low-nibble-first.
            let lower = (raw[0] << 2) | (raw[1] >> 6);
            let upper = raw[1] & 0b0011_1111;
            (2usize, ((upper as u16) << 8) | lower as u16)
        }
        _ => return None,
    };
    if raw.len() != prefix_len + 34 {
        return None;
    }
    let body = &raw[..prefix_len + 32];
    let checksum = &raw[prefix_len + 32..];

    let mut hasher = Blake2b512::new();
    hasher.update(b"SS58PRE");
    hasher.update(body);
    let hash = hasher.finalize();
    if hash[..2] != *checksum {
        return None;
    }

    let mut pubkey = [0u8; 32];
    pubkey.copy_from_slice(&body[prefix_len..]);
    Some((prefix, pubkey))
}

/// Decode a Solana address (bare base58 of a 32-byte ed25519 public key).
pub fn decode_solana(address: &str) -> Option<[u8; 32]> {
    let raw = bs58::decode(address).into_vec().ok()?;
    if raw.len() != 32 {
        return None;
    }
    let mut pubkey = [0u8; 32];
    pubkey.copy_from_slice(&raw);
    Some(pubkey)
}

/// Accept a signature as hex (`0x…` or bare) or base58 — Phantom hands back
/// raw bytes the browser encodes either way, polkadot-js returns hex, and
/// MetaMask returns hex.
fn decode_signature(sig: &str, expect: usize) -> Result<Vec<u8>, String> {
    let s = sig.trim();
    let hex_body = s.strip_prefix("0x").or_else(|| s.strip_prefix("0X")).unwrap_or(s);
    if hex_body.len() == expect * 2 && hex_body.chars().all(|c| c.is_ascii_hexdigit()) {
        return hex::decode(hex_body).map_err(|e| format!("bad hex signature: {}", e));
    }
    let bytes = bs58::decode(s)
        .into_vec()
        .map_err(|_| format!("signature is neither {}-byte hex nor base58", expect))?;
    if bytes.len() != expect {
        return Err(format!(
            "signature is {} bytes, expected {}",
            bytes.len(),
            expect
        ));
    }
    Ok(bytes)
}

// ── Verification ─────────────────────────────────────────────────────

/// Verify `signature` over `message` for `address`, using the curve the
/// address encodes (or the one the client declared).
///
/// Returns the canonical address on success. The caller has already matched
/// the message against a live challenge, so this is purely "did this key sign
/// this text".
pub fn verify_signature(
    address: &str,
    message: &str,
    signature: &str,
    declared: Option<&str>,
) -> Result<String, String> {
    let addr = address.trim();
    let key_type = declared
        .and_then(KeyType::from_id)
        .or_else(|| detect_key_type(addr))
        .ok_or_else(|| format!("unrecognised address format: {}", addr))?;

    match key_type {
        KeyType::Secp256k1 => {
            let recovered = recover_eth_address(message, signature)?;
            if !addr_eq(&recovered, addr) {
                return Err("signer does not match address".into());
            }
            Ok(normalize_addr(addr))
        }
        KeyType::Ed25519 => {
            // Solana pubkeys are bare base58; an SS58 address can also hold an
            // ed25519 key, so accept either encoding of the same 32 bytes.
            let pubkey = decode_solana(addr)
                .or_else(|| decode_ss58(addr).map(|(_, k)| k))
                .ok_or_else(|| "not a valid ed25519 address".to_string())?;
            verify_ed25519(&pubkey, message, signature)?;
            Ok(normalize_addr(addr))
        }
        KeyType::Sr25519 => {
            let (_, pubkey) =
                decode_ss58(addr).ok_or_else(|| "not a valid SS58 address".to_string())?;
            // Substrate wallets that hold an ed25519 key present the same SS58
            // shape, so a failed schnorr check falls through to ed25519 rather
            // than locking those accounts out.
            match verify_sr25519(&pubkey, message, signature) {
                Ok(()) => Ok(normalize_addr(addr)),
                Err(sr_err) => match verify_ed25519(&pubkey, message, signature) {
                    Ok(()) => Ok(normalize_addr(addr)),
                    Err(_) => Err(sr_err),
                },
            }
        }
    }
}

/// The exact bytes a Substrate wallet signed.
///
/// `signRaw({type: 'bytes'})` — what every polkadot-js-compatible extension
/// exposes to a dapp — wraps the payload in `<Bytes>…</Bytes>` before signing,
/// so a plain challenge string never verifies on its own. Try the wrapped form
/// first (the one the browser actually produces) and the bare form second, for
/// signers that skip the wrapper.
fn substrate_payloads(message: &str) -> [Vec<u8>; 2] {
    [
        format!("<Bytes>{}</Bytes>", message).into_bytes(),
        message.as_bytes().to_vec(),
    ]
}

fn verify_sr25519(pubkey: &[u8; 32], message: &str, signature: &str) -> Result<(), String> {
    let sig_bytes = decode_signature(signature, 64)?;
    let pk = schnorrkel::PublicKey::from_bytes(pubkey)
        .map_err(|e| format!("bad sr25519 public key: {}", e))?;
    let sig = schnorrkel::Signature::from_bytes(&sig_bytes)
        .map_err(|e| format!("bad sr25519 signature: {}", e))?;
    for payload in substrate_payloads(message) {
        // "substrate" is the signing context every Substrate wallet uses for
        // off-chain message signing.
        if pk.verify_simple(b"substrate", &payload, &sig).is_ok() {
            return Ok(());
        }
    }
    Err("sr25519 signature verification failed".into())
}

fn verify_ed25519(pubkey: &[u8; 32], message: &str, signature: &str) -> Result<(), String> {
    use ed25519_dalek::{Signature as EdSignature, Verifier, VerifyingKey};
    let sig_bytes = decode_signature(signature, 64)?;
    let vk =
        VerifyingKey::from_bytes(pubkey).map_err(|e| format!("bad ed25519 public key: {}", e))?;
    let mut sig_arr = [0u8; 64];
    sig_arr.copy_from_slice(&sig_bytes);
    let sig = EdSignature::from_bytes(&sig_arr);

    // Solana's `signMessage` signs the raw UTF-8 bytes; Substrate's ed25519
    // accounts go through the same `<Bytes>` wrapper as sr25519.
    if vk.verify(message.as_bytes(), &sig).is_ok() {
        return Ok(());
    }
    for payload in substrate_payloads(message) {
        if vk.verify(&payload, &sig).is_ok() {
            return Ok(());
        }
    }
    Err("ed25519 signature verification failed".into())
}

/// What this server accepts, described for the sign-in screen.
pub fn supported() -> serde_json::Value {
    let entry = |k: KeyType, format: &str, wallets: &[&str]| {
        serde_json::json!({
            "id": k.id(),
            "label": k.label(),
            "address_format": format,
            "networks": k.networks(),
            "wallets": wallets,
        })
    };
    serde_json::json!({
        "key_types": [
            entry(KeyType::Secp256k1, "0x + 40 hex", &["MetaMask", "Rabby", "Coinbase Wallet", "SubWallet (EVM)"]),
            entry(KeyType::Ed25519, "base58 (32 bytes)", &["Phantom", "Solflare", "Backpack"]),
            entry(KeyType::Sr25519, "SS58", &["SubWallet", "Talisman", "polkadot-js"]),
        ],
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Alice's well-known Substrate address (sr25519, network prefix 42).
    const ALICE_SS58: &str = "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY";
    /// A real Solana address (the SPL token program).
    const SOL_ADDR: &str = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA";

    #[test]
    fn detects_each_key_type() {
        assert_eq!(
            detect_key_type("0xD779eB61CEd815570F74AB15a52eE8378a66996f"),
            Some(KeyType::Secp256k1)
        );
        assert_eq!(detect_key_type(ALICE_SS58), Some(KeyType::Sr25519));
        assert_eq!(detect_key_type(SOL_ADDR), Some(KeyType::Ed25519));
        assert_eq!(detect_key_type("not-an-address"), None);
    }

    #[test]
    fn ss58_roundtrips_to_alices_public_key() {
        let (prefix, pubkey) = decode_ss58(ALICE_SS58).expect("alice decodes");
        assert_eq!(prefix, 42);
        assert_eq!(
            hex::encode(pubkey),
            "d43593c715fdd31c61141abd04a99fd6822c8558854ccde39a5684e7a56da27d"
        );
    }

    #[test]
    fn ss58_rejects_a_corrupted_checksum() {
        // Flip one character in the checksum tail.
        let mut bad = ALICE_SS58.to_string();
        bad.pop();
        bad.push('X');
        assert!(decode_ss58(&bad).is_none());
    }

    #[test]
    fn only_evm_addresses_get_lowercased() {
        // Hex is case-insensitive — collapse it so one wallet is one identity.
        assert_eq!(
            normalize_addr("0xD779eB61CEd815570F74AB15a52eE8378a66996f"),
            "0xd779eb61ced815570f74ab15a52ee8378a66996f"
        );
        // base58 case IS the key — lowercasing would destroy the address.
        assert_eq!(normalize_addr(ALICE_SS58), ALICE_SS58);
        assert_eq!(normalize_addr(SOL_ADDR), SOL_ADDR);
    }

    #[test]
    fn ed25519_signature_verifies_and_a_tampered_message_does_not() {
        use ed25519_dalek::{Signer, SigningKey};
        let signing = SigningKey::from_bytes(&[7u8; 32]);
        let address = bs58::encode(signing.verifying_key().to_bytes()).into_string();
        let message = "Sign this message to authenticate with Build Jobs.";
        let sig = hex::encode(signing.sign(message.as_bytes()).to_bytes());

        assert_eq!(detect_key_type(&address), Some(KeyType::Ed25519));
        assert_eq!(
            verify_signature(&address, message, &sig, None).unwrap(),
            address
        );
        assert!(verify_signature(&address, "a different message", &sig, None).is_err());
    }

    #[test]
    fn sr25519_verifies_the_wrapped_payload_a_browser_wallet_signs() {
        use schnorrkel::{signing_context, MiniSecretKey};
        let mini = MiniSecretKey::from_bytes(&[3u8; 32]).unwrap();
        let keypair = mini.expand_to_keypair(MiniSecretKey::ED25519_MODE);
        let pubkey = keypair.public.to_bytes();

        // Re-encode the public key as SS58 so verification goes through the
        // same address path the browser uses.
        let mut body = vec![42u8];
        body.extend_from_slice(&pubkey);
        let mut hasher = Blake2b512::new();
        hasher.update(b"SS58PRE");
        hasher.update(&body);
        let hash = hasher.finalize();
        body.extend_from_slice(&hash[..2]);
        let address = bs58::encode(&body).into_string();

        let message = "Sign this message to authenticate with Build Jobs.";
        let wrapped = format!("<Bytes>{}</Bytes>", message);
        let sig = keypair.sign(signing_context(b"substrate").bytes(wrapped.as_bytes()));
        let sig_hex = format!("0x{}", hex::encode(sig.to_bytes()));

        assert_eq!(detect_key_type(&address), Some(KeyType::Sr25519));
        assert_eq!(
            verify_signature(&address, message, &sig_hex, None).unwrap(),
            address
        );
        assert!(verify_signature(&address, "tampered", &sig_hex, None).is_err());
    }
}
