// Signature verification for the bridge.
//
// substrate: SubWallet/Polkadot.js signs sr25519 over UTF-8 message bytes.
//            Browser extensions wrap data as `<Bytes>...</Bytes>` before signing,
//            so we must try both forms.
// solana:    Phantom signs ed25519 over raw UTF-8 bytes.
//
// Replay protection lives outside this module — we only check signature math.

use blake2::{Blake2b512, Digest};
use schnorrkel::{PublicKey, Signature, signing_context};

/// Substrate "wallet" wrappers.
const POLKADOT_BYTES_PREFIX: &[u8] = b"<Bytes>";
const POLKADOT_BYTES_SUFFIX: &[u8] = b"</Bytes>";

#[derive(Debug, thiserror::Error)]
pub enum CryptoError {
    #[error("invalid address")]
    InvalidAddress,
}

// ── SS58 (substrate) decoding ────────────────────────────────────

/// Decode an SS58-encoded address back to its 32-byte AccountId, validating the
/// blake2b checksum. Returns Err on garbage input — never panics.
pub fn ss58_decode(address: &str) -> Result<[u8; 32], CryptoError> {
    let raw = bs58::decode(address)
        .into_vec()
        .map_err(|_| CryptoError::InvalidAddress)?;

    // SS58 layouts: 1B prefix + 32B id + 2B checksum, or 2B prefix + 32B id + 2B checksum.
    let (payload_end, account_start) = match raw.len() {
        35 => (33, 1),
        36 => (34, 2),
        _ => return Err(CryptoError::InvalidAddress),
    };

    let mut hasher = Blake2b512::new();
    hasher.update(b"SS58PRE");
    hasher.update(&raw[..payload_end]);
    let digest = hasher.finalize();
    if digest[..2] != raw[payload_end..payload_end + 2] {
        return Err(CryptoError::InvalidAddress);
    }

    let mut out = [0u8; 32];
    out.copy_from_slice(&raw[account_start..account_start + 32]);
    Ok(out)
}

// ── sr25519 ──────────────────────────────────────────────────────

pub fn verify_sr25519(public_key_bytes: &[u8], signature: &[u8], message: &[u8]) -> bool {
    if public_key_bytes.len() != 32 || signature.len() != 64 {
        return false;
    }
    let pk = match PublicKey::from_bytes(public_key_bytes) {
        Ok(p) => p,
        Err(_) => return false,
    };
    let sig = match Signature::from_bytes(signature) {
        Ok(s) => s,
        Err(_) => return false,
    };
    // schnorrkel's signing context for "substrate" — matches sr25519 lib defaults.
    let ctx = signing_context(b"substrate");

    if pk.verify(ctx.bytes(message), &sig).is_ok() {
        return true;
    }
    // Fall back to wallet-wrapped form.
    let mut wrapped = Vec::with_capacity(POLKADOT_BYTES_PREFIX.len() + message.len() + POLKADOT_BYTES_SUFFIX.len());
    wrapped.extend_from_slice(POLKADOT_BYTES_PREFIX);
    wrapped.extend_from_slice(message);
    wrapped.extend_from_slice(POLKADOT_BYTES_SUFFIX);
    pk.verify(ctx.bytes(&wrapped), &sig).is_ok()
}

// ── ed25519 / solana ─────────────────────────────────────────────

pub fn solana_address_to_pubkey(address: &str) -> Result<[u8; 32], CryptoError> {
    let raw = bs58::decode(address)
        .into_vec()
        .map_err(|_| CryptoError::InvalidAddress)?;
    if raw.len() != 32 {
        return Err(CryptoError::InvalidAddress);
    }
    let mut out = [0u8; 32];
    out.copy_from_slice(&raw);
    Ok(out)
}

pub fn verify_ed25519(public_key_bytes: &[u8], signature: &[u8], message: &[u8]) -> bool {
    use ed25519_dalek::{Signature as EdSignature, Verifier, VerifyingKey};

    if public_key_bytes.len() != 32 || signature.len() != 64 {
        return false;
    }
    let mut pk_arr = [0u8; 32];
    pk_arr.copy_from_slice(public_key_bytes);
    let vk = match VerifyingKey::from_bytes(&pk_arr) {
        Ok(v) => v,
        Err(_) => return false,
    };
    let mut sig_arr = [0u8; 64];
    sig_arr.copy_from_slice(signature);
    let sig = EdSignature::from_bytes(&sig_arr);
    vk.verify(message, &sig).is_ok()
}

// ── Constant-time string compare for admin auth ─────────────────

pub fn constant_eq(a: &str, b: &str) -> bool {
    use subtle::ConstantTimeEq;
    let a = a.as_bytes();
    let b = b.as_bytes();
    if a.len() != b.len() {
        // Short-circuit on length is fine — we don't leak the value.
        return false;
    }
    a.ct_eq(b).into()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ss58_round_trip_known() {
        // Alice's well-known dev address (sr25519).
        let alice = "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY";
        let pk = ss58_decode(alice).expect("alice decodes");
        assert_eq!(pk.len(), 32);
        // Alice public key is well-known.
        let expected = hex::decode("d43593c715fdd31c61141abd04a99fd6822c8558854ccde39a5684e7a56da27d").unwrap();
        assert_eq!(&pk[..], &expected[..]);
    }

    #[test]
    fn ss58_rejects_bad_checksum() {
        // Flip last char so checksum fails.
        let bad = "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQX";
        assert!(ss58_decode(bad).is_err());
    }

    #[test]
    fn ss58_rejects_garbage() {
        assert!(ss58_decode("").is_err());
        assert!(ss58_decode("not-base58!!").is_err());
        assert!(ss58_decode("aaaaaa").is_err());
    }

    #[test]
    fn sig_rejects_bad_lengths() {
        assert!(!verify_sr25519(&[0; 31], &[0; 64], b"x"));
        assert!(!verify_sr25519(&[0; 32], &[0; 63], b"x"));
        assert!(!verify_ed25519(&[0; 31], &[0; 64], b"x"));
        assert!(!verify_ed25519(&[0; 32], &[0; 63], b"x"));
    }

    #[test]
    fn solana_address_decode() {
        // Random Phantom-style address (well-known: System Program is all 1s).
        let addr = "11111111111111111111111111111111";
        let pk = solana_address_to_pubkey(addr).expect("decodes");
        assert_eq!(pk, [0u8; 32]);
        assert!(solana_address_to_pubkey("not-base58!").is_err());
    }

    #[test]
    fn sr25519_sign_then_verify_accepts_real_signature() {
        // Generate a fresh sr25519 keypair, sign a message, and confirm we
        // accept the resulting signature. Catches bugs in the schnorrkel
        // signing-context wiring that pure negative tests would miss.
        use schnorrkel::{signing_context, Keypair};
        let mut csprng = rand_core::OsRng;
        let kp = Keypair::generate_with(&mut csprng);
        let msg = b"commit 0x742d35Cc6634C0532925a3b844Bc9e7595f2bD18";
        let ctx = signing_context(b"substrate");
        let sig = kp.sign(ctx.bytes(msg));
        let sig_bytes = sig.to_bytes();
        let pk_bytes = kp.public.to_bytes();
        assert!(verify_sr25519(&pk_bytes, &sig_bytes, msg));
        // Negative: tweaking the message must reject.
        assert!(!verify_sr25519(&pk_bytes, &sig_bytes, b"different message"));
    }

    #[test]
    fn ed25519_sign_then_verify_accepts_real_signature() {
        use ed25519_dalek::{Signer, SigningKey};
        use rand_core::RngCore;
        let mut csprng = rand_core::OsRng;
        let mut secret = [0u8; 32];
        csprng.fill_bytes(&mut secret);
        let sk = SigningKey::from_bytes(&secret);
        let vk = sk.verifying_key();
        let msg = b"commit 0x742d35Cc6634C0532925a3b844Bc9e7595f2bD18";
        let sig = sk.sign(msg);
        assert!(verify_ed25519(&vk.to_bytes(), &sig.to_bytes(), msg));
        assert!(!verify_ed25519(&vk.to_bytes(), &sig.to_bytes(), b"tampered"));
    }

    #[test]
    fn constant_eq_basic() {
        assert!(constant_eq("hello", "hello"));
        assert!(!constant_eq("hello", "world"));
        assert!(!constant_eq("hello", "hello_"));
        assert!(!constant_eq("", "x"));
    }
}
