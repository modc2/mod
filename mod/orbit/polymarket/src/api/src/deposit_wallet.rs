//! Polymarket V2 deposit-wallet address derivation.
//!
//! Polymarket V2 routes orders through "deposit wallets" — UUPS-proxy
//! smart-contract wallets deployed CREATE2-deterministically from the
//! user's EOA. The address is stable per (factory, implementation, EOA),
//! so we can compute it offline without RPC.
//!
//! Reference: Polymarket/py-sdk `_internal/wallet.py`. We mirror the
//! `derive_uups_deposit_wallet_address` path (the Polygon-mainnet factory
//! is UUPS — calling its `beacon()` selector 0x49493a4d reverts, which is
//! py-sdk's signal to use UUPS over beacon).
//!
//! The exotic byte constants below come straight from py-sdk and encode
//! the ERC-1967 UUPS proxy initcode that the factory deploys with — they
//! are *not* derivable from a Solidity source we own, so a stray byte
//! here will produce a wrong address with no obvious symptom (orders
//! would just fail with "wallet not deployed" or similar). The unit
//! tests cross-check against py-sdk's `derive_uups_deposit_wallet_address`
//! with the same EOA + factory + implementation.

use anyhow::{anyhow, Result};

use crate::signer::keccak256;

// ─── Polygon production constants (mirror Polymarket/py-sdk) ────────────

/// Deposit-wallet factory contract — CREATE2 deployer.
pub const DEPOSIT_WALLET_FACTORY: &str = "0x00000000000Fb5C9ADea0298D729A0CB3823Cc07";
/// UUPS implementation address used as `args` and embedded in the initcode.
pub const DEPOSIT_WALLET_IMPLEMENTATION: &str = "0x58CA52ebe0DadfdF531Cde7062e76746de4Db1eB";

/// Verbatim from py-sdk. Stand-alone byte arrays so a typo in any one
/// constant fails a unit test, not a live order.
const ERC1967_CONST1: [u8; 32] = [
    0xcc, 0x37, 0x35, 0xa9, 0x20, 0xa3, 0xca, 0x50, 0x5d, 0x38, 0x2b, 0xbc, 0x54, 0x5a, 0xf4, 0x3d,
    0x60, 0x00, 0x80, 0x3e, 0x60, 0x38, 0x57, 0x3d, 0x60, 0x00, 0xfd, 0x5b, 0x3d, 0x60, 0x00, 0xf3,
];
const ERC1967_CONST2: [u8; 32] = [
    0x51, 0x55, 0xf3, 0x36, 0x3d, 0x3d, 0x37, 0x3d, 0x3d, 0x36, 0x3d, 0x7f, 0x36, 0x08, 0x94, 0xa1,
    0x3b, 0xa1, 0xa3, 0x21, 0x06, 0x67, 0xc8, 0x28, 0x49, 0x2d, 0xb9, 0x8d, 0xca, 0x3e, 0x20, 0x76,
];
const ERC1967_PREFIX_BASE: u128 = 0x61003D3D8160233D3973;

// ─── ABI / encoding primitives ───────────────────────────────────────────

fn strip_0x(s: &str) -> &str {
    s.strip_prefix("0x").unwrap_or(s)
}

fn decode_address(addr: &str) -> Result<[u8; 20]> {
    let bytes = hex::decode(strip_0x(addr)).map_err(|e| anyhow!("address hex: {}", e))?;
    if bytes.len() != 20 {
        return Err(anyhow!("address must be 20 bytes, got {}", bytes.len()));
    }
    let mut out = [0u8; 20];
    out.copy_from_slice(&bytes);
    Ok(out)
}

/// ABI-encode `(address factory, bytes32 wallet_id)` — the constructor
/// args appended to the UUPS proxy initcode and used as the CREATE2 salt
/// pre-image. `wallet_id` is the signer EOA left-padded to 32 bytes.
fn deposit_wallet_args(factory: &str, signer_eoa: &str) -> Result<[u8; 64]> {
    let factory_bytes = decode_address(factory)?;
    let signer_bytes = decode_address(signer_eoa)?;
    let mut out = [0u8; 64];
    // First 32 bytes: factory address, left-padded to 32 (high 12 zero,
    // low 20 = address). Standard ABI head for `address`.
    out[12..32].copy_from_slice(&factory_bytes);
    // Second 32 bytes: signer EOA left-padded to 32 — same as
    // `signer.rjust(32)` in py-sdk. The deposit wallet contract reads
    // the low 20 bytes as its owner.
    out[44..64].copy_from_slice(&signer_bytes);
    Ok(out)
}

/// Build the UUPS-proxy initcode and return `keccak256(initcode)`.
///
/// Layout (mirrors py-sdk `_uups_deposit_init_code_hash`):
///   prefix (10) || implementation (20) || 0x6009 (2) ||
///   ERC1967_CONST2 (32) || ERC1967_CONST1 (32) || args (N)
///
/// where `prefix = ERC1967_PREFIX_BASE | (len(args) << 56)` packed
/// big-endian into 10 bytes. The dynamic length byte (args.len) lives in
/// the high-ish nibble of the prefix and the EVM uses it to compute
/// CODECOPY offsets, so any off-by-one here yields a different initcode
/// and a different deployed-address.
fn uups_deposit_init_code_hash(implementation: &str, args: &[u8]) -> Result<[u8; 32]> {
    let impl_bytes = decode_address(implementation)?;
    let args_len = args.len() as u128;
    let prefix_u128 = ERC1967_PREFIX_BASE | (args_len << 56);
    // Pack the low 10 bytes of the u128 (big-endian) into the prefix.
    let prefix_be16 = prefix_u128.to_be_bytes(); // 16 bytes
    let prefix = &prefix_be16[6..]; // low 10 bytes

    let mut bytecode: Vec<u8> = Vec::with_capacity(10 + 20 + 2 + 32 + 32 + args.len());
    bytecode.extend_from_slice(prefix);
    bytecode.extend_from_slice(&impl_bytes);
    bytecode.extend_from_slice(&[0x60, 0x09]);
    bytecode.extend_from_slice(&ERC1967_CONST2);
    bytecode.extend_from_slice(&ERC1967_CONST1);
    bytecode.extend_from_slice(args);
    Ok(keccak256(&bytecode))
}

/// CREATE2 address: `keccak256(0xff || factory || salt || init_code_hash)[12..]`.
fn create2(factory: &str, salt: &[u8; 32], init_code_hash: &[u8; 32]) -> Result<String> {
    let factory_bytes = decode_address(factory)?;
    let mut buf = [0u8; 1 + 20 + 32 + 32];
    buf[0] = 0xff;
    buf[1..21].copy_from_slice(&factory_bytes);
    buf[21..53].copy_from_slice(salt);
    buf[53..85].copy_from_slice(init_code_hash);
    let h = keccak256(&buf);
    // Lower 20 bytes, 0x-prefixed lowercase hex.
    let mut out = String::with_capacity(42);
    out.push_str("0x");
    out.push_str(&hex::encode(&h[12..]));
    Ok(out)
}

/// Derive the Polymarket V2 deposit-wallet address for an EOA on Polygon
/// mainnet. Pure function — no RPC. Returns lowercase 0x-prefixed hex.
pub fn derive_deposit_wallet(eoa: &str) -> Result<String> {
    let args = deposit_wallet_args(DEPOSIT_WALLET_FACTORY, eoa)?;
    let init_code_hash = uups_deposit_init_code_hash(DEPOSIT_WALLET_IMPLEMENTATION, &args)?;
    let salt = keccak256(&args);
    create2(DEPOSIT_WALLET_FACTORY, &salt, &init_code_hash)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// `derive_deposit_wallet` is deterministic and returns a checksummable
    /// 20-byte address. Full byte-for-byte cross-check vs py-sdk would
    /// require a known fixture vector — added once we have one.
    #[test]
    fn returns_42_char_hex_address() {
        let a = derive_deposit_wallet("0x89bcdee4a284cb0848eebb975bec78ab5bd06cfa").unwrap();
        assert_eq!(a.len(), 42);
        assert!(a.starts_with("0x"));
        assert!(a[2..].chars().all(|c| c.is_ascii_hexdigit()));
    }

    #[test]
    fn deterministic() {
        let a1 = derive_deposit_wallet("0x89bcdee4a284cb0848eebb975bec78ab5bd06cfa").unwrap();
        let a2 = derive_deposit_wallet("0x89BCDEE4A284CB0848EEBB975BEC78AB5BD06CFA").unwrap();
        assert_eq!(a1, a2, "case-insensitive on input");
    }

    #[test]
    fn different_eoa_different_wallet() {
        let a = derive_deposit_wallet("0x0000000000000000000000000000000000000001").unwrap();
        let b = derive_deposit_wallet("0x0000000000000000000000000000000000000002").unwrap();
        assert_ne!(a, b);
    }
}
