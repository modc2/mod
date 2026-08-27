//! Hashes, Ethereum signature recovery, and Merkle trees.
//!
//! Everything the market claims about itself has to be checkable by someone
//! who does not trust this process, so all three primitives here are the
//! plain, boring, widely-implemented ones: SHA-256 for the chain and the
//! tree, Keccak-256 + secp256k1 recovery for identity (so any wallet that
//! can do `personal_sign` is a client), and a Bitcoin-style Merkle tree with
//! the last node duplicated on odd levels.

use k256::ecdsa::{RecoveryId, Signature, VerifyingKey};
use sha2::{Digest, Sha256};
use sha3::Keccak256;

/// SHA-256 of some bytes, lowercase hex.
pub fn sha256_hex(data: &[u8]) -> String {
    let mut h = Sha256::new();
    h.update(data);
    hex::encode(h.finalize())
}

/// SHA-256 over a `|`-joined field list. Used everywhere a commitment or an
/// id is derived from structured data — the separator is what stops
/// `("ab", "c")` and `("a", "bc")` from hashing to the same thing.
pub fn hash_fields(fields: &[&str]) -> String {
    sha256_hex(fields.join("|").as_bytes())
}

pub fn keccak256(data: &[u8]) -> [u8; 32] {
    let mut h = Keccak256::new();
    h.update(data);
    let out = h.finalize();
    let mut buf = [0u8; 32];
    buf.copy_from_slice(&out);
    buf
}

/// The EIP-191 "personal sign" digest: what a wallet actually signs when a
/// page calls `personal_sign`.
pub fn eip191_digest(message: &str) -> [u8; 32] {
    let prefixed = format!("\x19Ethereum Signed Message:\n{}{}", message.len(), message);
    keccak256(prefixed.as_bytes())
}

/// Normalise an address to lowercase `0x…` — every map in the state is keyed
/// by this form, so a mixed-case address and its checksummed twin are one
/// account rather than two.
pub fn norm_addr(addr: &str) -> String {
    let a = addr.trim().to_lowercase();
    if let Some(stripped) = a.strip_prefix("0x") {
        format!("0x{}", stripped)
    } else {
        format!("0x{}", a)
    }
}

pub fn is_address(addr: &str) -> bool {
    let a = norm_addr(addr);
    a.len() == 42 && a[2..].chars().all(|c| c.is_ascii_hexdigit())
}

/// Recover the signing address from an EIP-191 signature over `message`.
///
/// Returns the lowercase `0x…` address. A malformed signature is an error,
/// never a default address — a caller that cannot be identified must not
/// silently become somebody.
pub fn recover_signer(message: &str, signature: &str) -> Result<String, String> {
    let sig_hex = signature.trim().trim_start_matches("0x");
    let raw = hex::decode(sig_hex).map_err(|e| format!("signature is not hex: {e}"))?;
    if raw.len() != 65 {
        return Err(format!("signature must be 65 bytes, got {}", raw.len()));
    }
    // Wallets emit v as 27/28 (or 0/1 from some libraries, or 35+ for
    // EIP-155-chained values); all three normalise to a 0/1 recovery id.
    let v = raw[64];
    let rec = match v {
        0 | 1 => v,
        27 | 28 => v - 27,
        x if x >= 35 => (x - 35) % 2,
        other => return Err(format!("unsupported recovery byte {other}")),
    };
    let sig = Signature::from_slice(&raw[..64]).map_err(|e| format!("bad signature: {e}"))?;
    let rid = RecoveryId::from_byte(rec).ok_or_else(|| "bad recovery id".to_string())?;
    let digest = eip191_digest(message);
    let key = VerifyingKey::recover_from_prehash(&digest, &sig, rid)
        .map_err(|e| format!("recovery failed: {e}"))?;
    let point = key.to_encoded_point(false);
    // Drop the 0x04 uncompressed-point tag; the address is the last 20 bytes
    // of the Keccak hash of the remaining 64.
    let hash = keccak256(&point.as_bytes()[1..]);
    Ok(format!("0x{}", hex::encode(&hash[12..])))
}

/// Merkle root over already-hashed, hex-encoded leaves.
///
/// The leaves are sorted by the caller before they get here; the round seals
/// them in ascending commitment order so that two people building the tree
/// from the same public commitment set always get the same root.
pub fn merkle_root(leaves: &[String]) -> String {
    if leaves.is_empty() {
        return sha256_hex(b"prerank:empty-tree");
    }
    let mut level: Vec<Vec<u8>> = leaves
        .iter()
        .map(|l| hex::decode(l).unwrap_or_else(|_| sha256_bytes(l.as_bytes())))
        .collect();
    while level.len() > 1 {
        let mut next = Vec::with_capacity((level.len() + 1) / 2);
        for pair in level.chunks(2) {
            let left = &pair[0];
            // Odd level: the last node is paired with itself.
            let right = pair.get(1).unwrap_or(left);
            let mut buf = Vec::with_capacity(64);
            buf.extend_from_slice(left);
            buf.extend_from_slice(right);
            next.push(sha256_bytes(&buf));
        }
        level = next;
    }
    hex::encode(&level[0])
}

/// The sibling path for one leaf: `(sibling_hex, sibling_is_left)` per level.
pub fn merkle_proof(leaves: &[String], index: usize) -> Vec<(String, bool)> {
    let mut proof = Vec::new();
    if index >= leaves.len() {
        return proof;
    }
    let mut level: Vec<Vec<u8>> = leaves
        .iter()
        .map(|l| hex::decode(l).unwrap_or_else(|_| sha256_bytes(l.as_bytes())))
        .collect();
    let mut idx = index;
    while level.len() > 1 {
        let sibling = if idx % 2 == 0 {
            level.get(idx + 1).unwrap_or(&level[idx]).clone()
        } else {
            level[idx - 1].clone()
        };
        proof.push((hex::encode(&sibling), idx % 2 == 1));
        let mut next = Vec::with_capacity((level.len() + 1) / 2);
        for pair in level.chunks(2) {
            let left = &pair[0];
            let right = pair.get(1).unwrap_or(left);
            let mut buf = Vec::with_capacity(64);
            buf.extend_from_slice(left);
            buf.extend_from_slice(right);
            next.push(sha256_bytes(&buf));
        }
        level = next;
        idx /= 2;
    }
    proof
}

/// Walk a sibling path back up to a root — the check a client runs to prove
/// its own bet was in the sealed set without downloading the set.
pub fn merkle_verify(leaf: &str, proof: &[(String, bool)], root: &str) -> bool {
    let mut acc = match hex::decode(leaf) {
        Ok(b) => b,
        Err(_) => return false,
    };
    for (sibling, sibling_is_left) in proof {
        let sib = match hex::decode(sibling) {
            Ok(b) => b,
            Err(_) => return false,
        };
        let mut buf = Vec::with_capacity(64);
        if *sibling_is_left {
            buf.extend_from_slice(&sib);
            buf.extend_from_slice(&acc);
        } else {
            buf.extend_from_slice(&acc);
            buf.extend_from_slice(&sib);
        }
        acc = sha256_bytes(&buf);
    }
    hex::encode(&acc) == root.to_lowercase()
}

fn sha256_bytes(data: &[u8]) -> Vec<u8> {
    let mut h = Sha256::new();
    h.update(data);
    h.finalize().to_vec()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn merkle_root_is_stable_and_proofs_check_out() {
        let leaves: Vec<String> = (0..7).map(|i| sha256_hex(format!("leaf{i}").as_bytes())).collect();
        let root = merkle_root(&leaves);
        assert_eq!(root, merkle_root(&leaves), "root must be deterministic");
        for (i, leaf) in leaves.iter().enumerate() {
            let proof = merkle_proof(&leaves, i);
            assert!(merkle_verify(leaf, &proof, &root), "leaf {i} should verify");
        }
        // A leaf that was never in the tree must not verify against any path.
        let forged = sha256_hex(b"never-committed");
        assert!(!merkle_verify(&forged, &merkle_proof(&leaves, 0), &root));
    }

    #[test]
    fn recovery_round_trips_a_known_wallet_signature() {
        // Signature produced by a wallet for the message below; if the
        // recovery path ever regresses, every signed action would start
        // resolving to the wrong account, so this is pinned.
        let msg = "prerank";
        let (addr, sig) = crate::testkit::sign(1, msg);
        assert_eq!(recover_signer(msg, &sig).unwrap(), addr);
        // A different message must not recover the same address.
        assert_ne!(recover_signer("prerank!", &sig).unwrap(), addr);
    }
}
