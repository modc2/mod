//! EIP-712 digest for Polymarket's ClobAuth message.
//!
//! Polymarket binds every CLOB API key to a single EOA. To trade from the
//! backend without per-order MetaMask popups, the backend signs this
//! ClobAuth itself (with its per-user key from `signer_store`) and mints
//! a fresh API key against ITS OWN address. Subsequent orders then use
//! `POLY_ADDRESS = backend_addr` + `order.signer = backend_addr` (which
//! the user's CLOB API key wouldn't allow). The backend key is already
//! registered as a Safe owner on the user's proxy, so the on-chain
//! `Safe.isValidSignature` check accepts these orders against the user's
//! maker address.
//!
//! Schema (from @polymarket/clob-client / app/lib/auth.ts):
//!
//!   domain  = EIP712Domain(string name, string version, uint256 chainId)
//!            { name: "ClobAuthDomain", version: "1", chainId: 137 }
//!
//!   type    = ClobAuth(address address, string timestamp,
//!                     uint256 nonce, string message)
//!
//!   message = "This message attests that I control the given wallet"

use anyhow::Result;

use crate::signer::keccak256;

const CHAIN_ID: u64 = 137;
const DOMAIN_NAME: &str = "ClobAuthDomain";
const DOMAIN_VERSION: &str = "1";
pub const CLOB_AUTH_MESSAGE: &str = "This message attests that I control the given wallet";

fn left_pad_32(bytes: &[u8]) -> [u8; 32] {
    let mut out = [0u8; 32];
    if bytes.len() >= 32 {
        out.copy_from_slice(&bytes[bytes.len() - 32..]);
    } else {
        out[32 - bytes.len()..].copy_from_slice(bytes);
    }
    out
}

fn encode_address(addr: &str) -> Result<[u8; 32]> {
    let s = addr.strip_prefix("0x").unwrap_or(addr);
    let bytes = hex::decode(s).map_err(|e| anyhow::anyhow!("address hex: {}", e))?;
    if bytes.len() != 20 {
        return Err(anyhow::anyhow!("address must be 20 bytes"));
    }
    Ok(left_pad_32(&bytes))
}

fn encode_uint256(n: u64) -> [u8; 32] {
    let mut out = [0u8; 32];
    out[24..].copy_from_slice(&n.to_be_bytes());
    out
}

fn domain_separator() -> [u8; 32] {
    // The Polymarket frontend's domain intentionally OMITS verifyingContract
    // from EIP712Domain. We mirror that exactly — adding a verifyingContract
    // field here would change the type hash and make our signatures
    // unverifiable.
    let type_hash = keccak256(b"EIP712Domain(string name,string version,uint256 chainId)");
    let mut buf = Vec::with_capacity(4 * 32);
    buf.extend_from_slice(&type_hash);
    buf.extend_from_slice(&keccak256(DOMAIN_NAME.as_bytes()));
    buf.extend_from_slice(&keccak256(DOMAIN_VERSION.as_bytes()));
    buf.extend_from_slice(&encode_uint256(CHAIN_ID));
    keccak256(&buf)
}

fn struct_hash(address: &str, timestamp: &str, nonce: u64) -> Result<[u8; 32]> {
    let type_hash = keccak256(
        b"ClobAuth(address address,string timestamp,uint256 nonce,string message)",
    );
    let mut buf = Vec::with_capacity(5 * 32);
    buf.extend_from_slice(&type_hash);
    buf.extend_from_slice(&encode_address(address)?);
    buf.extend_from_slice(&keccak256(timestamp.as_bytes()));
    buf.extend_from_slice(&encode_uint256(nonce));
    buf.extend_from_slice(&keccak256(CLOB_AUTH_MESSAGE.as_bytes()));
    Ok(keccak256(&buf))
}

/// Final EIP-712 digest = keccak256(0x1901 || domainSeparator || structHash).
pub fn clob_auth_digest(address: &str, timestamp: &str, nonce: u64) -> Result<[u8; 32]> {
    let dom = domain_separator();
    let sh = struct_hash(address, timestamp, nonce)?;
    let mut prefix = Vec::with_capacity(2 + 32 + 32);
    prefix.push(0x19);
    prefix.push(0x01);
    prefix.extend_from_slice(&dom);
    prefix.extend_from_slice(&sh);
    Ok(keccak256(&prefix))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn digest_is_deterministic_and_32_bytes() {
        let addr = "0x71ef1e4b2c3ad5901e204ff0c45709d0f7dff092";
        let d1 = clob_auth_digest(addr, "1700000000", 0).unwrap();
        let d2 = clob_auth_digest(addr, "1700000000", 0).unwrap();
        assert_eq!(d1, d2);
        assert_eq!(d1.len(), 32);
    }

    #[test]
    fn different_addresses_yield_different_digests() {
        let a = clob_auth_digest("0x71ef1e4b2c3ad5901e204ff0c45709d0f7dff092", "1700000000", 0).unwrap();
        let b = clob_auth_digest("0x89BCdEe4a284CB0848eEBB975BEc78AB5BD06cfa", "1700000000", 0).unwrap();
        assert_ne!(a, b);
    }
}
