//! Wallet recovery for the two auth schemes we accept on `X-Mod-Auth`:
//!
//!   eth <0x-prefixed 65-byte personal_sign signature>
//!   sub <hex pubkey (32B)> <hex signature (64B)> [ss58_prefix]
//!
//! For Ethereum the canonical identity is the lowercase 0x-checksumless address.
//! For Substrate the canonical identity is the SS58-encoded address, with the
//! prefix defaulting to 42 (the Bittensor / generic Substrate prefix).
//!
//! All identities are namespaced (`eth:0x…` or `sub:5…`) so an Ethereum address
//! can never collide with a Substrate one in the JSON store.

use anyhow::{anyhow, bail, Result};
use blake2::Blake2b512;
use k256::ecdsa::{RecoveryId, Signature, VerifyingKey};
use sha3::{Digest, Keccak256};

/// What the JSON store keys by — wallet identity, including scheme prefix.
pub type WalletId = String;

#[derive(Debug, Clone)]
pub enum Scheme {
    Eth,
    Sub { ss58_prefix: u16 },
}

#[derive(Debug, Clone)]
pub struct Wallet {
    pub id: WalletId,
    pub scheme: Scheme,
    pub address: String, // 0x… for eth, ss58 for sub
}

/// Parse and verify the `X-Mod-Auth` header, returning the recovered identity.
pub fn verify_header(header: &str, challenge: &str) -> Result<Wallet> {
    let header = header.trim();
    let mut it = header.split_whitespace();
    let scheme = it.next().ok_or_else(|| anyhow!("empty X-Mod-Auth"))?;

    match scheme {
        "eth" => {
            let sig = it.next().ok_or_else(|| anyhow!("eth requires signature"))?;
            verify_eth(sig, challenge)
        }
        "sub" => {
            let pubkey = it.next().ok_or_else(|| anyhow!("sub requires pubkey"))?;
            let sig = it.next().ok_or_else(|| anyhow!("sub requires signature"))?;
            let prefix: u16 = it
                .next()
                .map(|s| s.parse().unwrap_or(42))
                .unwrap_or(42);
            verify_sub(pubkey, sig, challenge, prefix)
        }
        other => bail!("unknown auth scheme: {other}"),
    }
}

// ── Ethereum personal_sign recovery ──────────────────────────────────────

fn verify_eth(sig_hex: &str, challenge: &str) -> Result<Wallet> {
    let raw = decode_hex(sig_hex).map_err(|e| anyhow!("bad eth sig hex: {e}"))?;
    if raw.len() != 65 {
        bail!("eth signature must be 65 bytes, got {}", raw.len());
    }
    let r_s: [u8; 64] = raw[..64].try_into().unwrap();
    let mut v = raw[64];
    if v >= 27 {
        v -= 27;
    }
    let recid = RecoveryId::try_from(v).map_err(|e| anyhow!("bad v: {e}"))?;
    let signature = Signature::from_slice(&r_s)?;

    let prefix = format!("\x19Ethereum Signed Message:\n{}", challenge.len());
    let mut buf = Vec::with_capacity(prefix.len() + challenge.len());
    buf.extend_from_slice(prefix.as_bytes());
    buf.extend_from_slice(challenge.as_bytes());
    let digest = keccak256(&buf);

    let vk = VerifyingKey::recover_from_prehash(&digest, &signature, recid)?;
    let pt = vk.to_encoded_point(false);
    let bytes = pt.as_bytes();
    let pubkey_bytes = &bytes[1..]; // strip 0x04 tag
    let hash = keccak256(pubkey_bytes);
    let addr = format!("0x{}", hex::encode(&hash[12..]));
    Ok(Wallet {
        id: format!("eth:{addr}"),
        scheme: Scheme::Eth,
        address: addr,
    })
}

fn keccak256(bytes: &[u8]) -> [u8; 32] {
    let mut h = Keccak256::new();
    h.update(bytes);
    let out = h.finalize();
    let mut arr = [0u8; 32];
    arr.copy_from_slice(&out);
    arr
}

// ── Substrate sr25519 verification ───────────────────────────────────────

fn verify_sub(pubkey_hex: &str, sig_hex: &str, challenge: &str, ss58_prefix: u16) -> Result<Wallet> {
    let pubkey_vec = decode_hex(pubkey_hex).map_err(|e| anyhow!("bad pubkey hex: {e}"))?;
    if pubkey_vec.len() != 32 {
        bail!("sr25519 pubkey must be 32 bytes, got {}", pubkey_vec.len());
    }
    let mut pubkey = [0u8; 32];
    pubkey.copy_from_slice(&pubkey_vec);
    let sig_bytes = decode_hex(sig_hex).map_err(|e| anyhow!("bad sub sig hex: {e}"))?;
    if sig_bytes.len() != 64 {
        bail!("sr25519 sig must be 64 bytes, got {}", sig_bytes.len());
    }

    let pk = schnorrkel::PublicKey::from_bytes(&pubkey)
        .map_err(|e| anyhow!("invalid sr25519 pubkey: {e}"))?;
    let sig = schnorrkel::Signature::from_bytes(&sig_bytes)
        .map_err(|e| anyhow!("invalid sr25519 sig: {e}"))?;

    // Polkadot.js / SubWallet's signRaw wraps the message in `<Bytes>…</Bytes>`
    // before signing, to prevent the user from accidentally signing extrinsics.
    // We try both wrapped + unwrapped so headless / programmatic signers
    // (which skip the wrap) also work.
    let context = schnorrkel::signing_context(b"substrate");
    let wrapped = format!("<Bytes>{challenge}</Bytes>");

    let wrapped_ok = pk
        .verify(context.bytes(wrapped.as_bytes()), &sig)
        .is_ok();
    let raw_ok = pk
        .verify(context.bytes(challenge.as_bytes()), &sig)
        .is_ok();
    if !wrapped_ok && !raw_ok {
        bail!("sr25519 signature verification failed");
    }

    let ss58 = ss58_encode(&pubkey, ss58_prefix);
    Ok(Wallet {
        id: format!("sub:{ss58}"),
        scheme: Scheme::Sub { ss58_prefix },
        address: ss58,
    })
}

/// Encode a 32-byte public key as a Substrate SS58 address with the given prefix.
///
/// SS58 layout:  prefix-bytes || pubkey || blake2b("SS58PRE" || prefix || pubkey)[..2]
/// Then base58-encode the whole thing.
///
/// For prefixes <64 the prefix encodes as a single byte; for larger prefixes
/// (e.g. 1284 for Moonbeam) it spans two bytes per the SS58 spec, but Bittensor
/// (42), Polkadot (0), Kusama (2), and Substrate-generic (42) all fit the
/// single-byte path which is the only branch we need here.
fn ss58_encode(pubkey: &[u8; 32], prefix: u16) -> String {
    let mut payload = Vec::with_capacity(35);
    if prefix < 64 {
        payload.push(prefix as u8);
    } else {
        // SS58 two-byte prefix encoding (rfc'd in substrate)
        let lo = ((prefix & 0xfc) >> 2) | 0x40;
        let hi = ((prefix >> 8) & 0xff) | ((prefix & 0x03) << 6);
        payload.push(lo as u8);
        payload.push(hi as u8);
    }
    payload.extend_from_slice(pubkey);

    let mut hasher = Blake2b512::new();
    hasher.update(b"SS58PRE");
    hasher.update(&payload);
    let checksum = hasher.finalize();
    payload.extend_from_slice(&checksum[..2]);

    bs58::encode(payload).into_string()
}

fn decode_hex(s: &str) -> Result<Vec<u8>> {
    let s = s.strip_prefix("0x").unwrap_or(s);
    hex::decode(s).map_err(Into::into)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ss58_alice_42() {
        // Substrate test account "Alice" — pubkey is well-known.
        let pubkey: [u8; 32] = hex::decode(
            "d43593c715fdd31c61141abd04a99fd6822c8558854ccde39a5684e7a56da27d",
        )
        .unwrap()
        .try_into()
        .unwrap();
        // Generic Substrate / Bittensor (prefix 42) ss58 for Alice.
        let got = ss58_encode(&pubkey, 42);
        assert_eq!(got, "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY");
    }
}
