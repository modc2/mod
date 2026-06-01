//! Polymarket order EIP-712 hashing.
//!
//! This module exists so the backend signer can only ever sign things that
//! are *structurally* a Polymarket CLOB order — we never expose a raw
//! "sign-arbitrary-digest" endpoint to the network. Anything the backend
//! signs has to pass through this Rust-side reconstruction of the digest
//! from a fully-typed order struct against Polymarket's known exchange
//! domains. A malicious client can't trick the backend into signing a
//! USDC.transfer or Safe.execTransaction by handing it a crafted digest —
//! it can only ask for signatures over Polymarket orders, and the digest
//! is computed from values the backend can verify (chain id, exchange,
//! domain name).

use anyhow::{anyhow, Result};
use serde::{Deserialize, Serialize};

// ─── Known Polymarket contract addresses on Polygon ─────────────────────
// Mirror of frontend `polymarketContracts.ts` so the backend never sources
// these from client input. Mismatched signing domains are the single most
// common foot-gun and we kill it by hard-coding here.

// Polymarket migrated to **Exchange V2** in 2026. V1 still exists on-chain
// for legacy orders but the CLOB matcher now expects V2 signatures for any
// market that's been registered with the new exchange — orders signed
// against V1 come back as `order_version_mismatch` 400s. We sign V2 only.
const POLYGON_CHAIN_ID: u64 = 137;
const CTF_EXCHANGE_V2: &str = "0xE111180000d2663C0091e4f400237545B87B996B";
const NEG_RISK_CTF_EXCHANGE_V2: &str = "0xe2222d279d744050d28e00520010520000310F59";
const DOMAIN_NAME: &str = "Polymarket CTF Exchange";
const DOMAIN_VERSION: &str = "2";

// ─── Order representation ────────────────────────────────────────────────

/// One Polymarket CLOB V2 order — the value the EIP-712 digest is computed
/// over. Numeric amounts are base-unit integer strings (Polymarket rejects
/// floats, and the digest depends on the exact integer value).
///
/// V2 dropped `taker`, `expiration`, `nonce`, `feeRateBps` from the signed
/// struct (they still appear in the HTTP JSON body for `taker`/`expiration`
/// per orderToJsonV2, but they are NOT inputs to the hash). V2 added
/// `timestamp`, `metadata`, `builder` to the struct.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OrderInput {
    pub salt: String,        // uint256 decimal
    pub maker: String,       // 0x-prefixed address
    pub signer: String,      // 0x-prefixed address (the recovering address)
    #[serde(rename = "tokenId")]
    pub token_id: String,    // uint256 decimal
    #[serde(rename = "makerAmount")]
    pub maker_amount: String,
    #[serde(rename = "takerAmount")]
    pub taker_amount: String,
    pub side: u8,            // 0 BUY, 1 SELL
    #[serde(rename = "signatureType")]
    pub signature_type: u8,  // 0 EOA, 1 POLY_PROXY, 2 POLY_GNOSIS_SAFE, 3 POLY_1271
    /// Order timestamp — `Date.now().toString()` in @polymarket/clob-client-v2
    /// (unix milliseconds as a decimal string). Polymarket uses this to
    /// scope per-maker order replay protection in lieu of the V1 `nonce`.
    pub timestamp: String,   // uint256 decimal (unix milliseconds)
    /// Free 32-byte client-supplied tag. Defaults to all-zero — Polymarket
    /// echoes it back through fill webhooks for client-side reconciliation.
    pub metadata: String,    // bytes32 hex, lowercase, 0x-prefixed
    /// Builder code (32-byte). Used by Polymarket's builder-fee program.
    /// All-zero for unbranded orders.
    pub builder: String,     // bytes32 hex
    /// "standard" | "negrisk" — selects which V2 exchange address signs into.
    pub exchange: ExchangeKind,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum ExchangeKind {
    Standard,
    Negrisk,
}

impl ExchangeKind {
    fn verifying_contract(self) -> &'static str {
        match self {
            ExchangeKind::Standard => CTF_EXCHANGE_V2,
            ExchangeKind::Negrisk => NEG_RISK_CTF_EXCHANGE_V2,
        }
    }
}

// ─── EIP-712 hashing primitives ──────────────────────────────────────────

use crate::signer::keccak256;

fn left_pad_32(bytes: &[u8]) -> [u8; 32] {
    let mut out = [0u8; 32];
    if bytes.len() >= 32 {
        out.copy_from_slice(&bytes[bytes.len() - 32..]);
    } else {
        out[32 - bytes.len()..].copy_from_slice(bytes);
    }
    out
}

fn encode_uint256_decimal(s: &str) -> Result<[u8; 32]> {
    // Polymarket sends amounts as decimal strings of base-unit integers.
    // We need to parse to a big-endian 32-byte representation. Limit to
    // 78 decimal digits (max uint256 = 2^256-1 ≈ 1.1e77, so 78 digits is
    // an overestimate but safe).
    if s.is_empty() || s.len() > 80 {
        return Err(anyhow!("invalid uint256 decimal: length"));
    }
    if !s.chars().all(|c| c.is_ascii_digit()) {
        return Err(anyhow!("invalid uint256 decimal: non-digit"));
    }
    // Use u256-as-u128-pair via manual base-10 multiply since we have no
    // bigint dep. For Polymarket orders we never exceed u128 in any single
    // field (max ~1e30 USDC base units for a $1T order), but salt can be
    // larger. Use 4×u64 limbs.
    let mut limbs: [u64; 4] = [0; 4]; // little-endian
    for ch in s.chars() {
        let d = ch.to_digit(10).unwrap() as u64;
        let mut carry: u128 = d as u128;
        for limb in limbs.iter_mut() {
            let v = (*limb as u128) * 10 + carry;
            *limb = (v & 0xFFFFFFFFFFFFFFFF) as u64;
            carry = v >> 64;
        }
        if carry != 0 {
            return Err(anyhow!("uint256 overflow at digit"));
        }
    }
    let mut out = [0u8; 32];
    // Encode big-endian: most significant limb at the start.
    for (i, limb) in limbs.iter().enumerate() {
        let off = 24 - i * 8;
        out[off..off + 8].copy_from_slice(&limb.to_be_bytes());
    }
    Ok(out)
}

fn encode_address(addr: &str) -> Result<[u8; 32]> {
    let s = addr.strip_prefix("0x").unwrap_or(addr);
    let bytes = hex::decode(s).map_err(|e| anyhow!("address hex: {}", e))?;
    if bytes.len() != 20 {
        return Err(anyhow!("address must be 20 bytes, got {}", bytes.len()));
    }
    Ok(left_pad_32(&bytes))
}

fn encode_uint8(v: u8) -> [u8; 32] {
    let mut out = [0u8; 32];
    out[31] = v;
    out
}

/// Parse a hex string (with or without 0x prefix) as exactly 32 bytes.
/// V2's `metadata` and `builder` fields are bytes32 in the typed data —
/// encoded as-is (no left-pad, no hashing), so a malformed value would
/// produce a digest mismatch that surfaces as "invalid signature" rather
/// than a structural error.
fn encode_bytes32_hex(s: &str) -> Result<[u8; 32]> {
    let stripped = s.strip_prefix("0x").unwrap_or(s);
    let bytes = hex::decode(stripped).map_err(|e| anyhow!("bytes32 hex: {}", e))?;
    if bytes.len() != 32 {
        return Err(anyhow!("bytes32 must be 32 bytes, got {}", bytes.len()));
    }
    let mut out = [0u8; 32];
    out.copy_from_slice(&bytes);
    Ok(out)
}

// ─── Domain + type hash ──────────────────────────────────────────────────

fn domain_typehash() -> [u8; 32] {
    keccak256(
        b"EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)",
    )
}

fn order_typehash() -> [u8; 32] {
    // V2 Order struct — must match @polymarket/clob-client-v2's
    // CTF_EXCHANGE_V2_ORDER_STRUCT exactly (field order matters for the
    // keccak256 type string). Drops V1 fields (taker, expiration, nonce,
    // feeRateBps) and adds (timestamp, metadata, builder).
    keccak256(
        b"Order(uint256 salt,address maker,address signer,uint256 tokenId,uint256 makerAmount,uint256 takerAmount,uint8 side,uint8 signatureType,uint256 timestamp,bytes32 metadata,bytes32 builder)",
    )
}

fn domain_separator(exchange: ExchangeKind) -> Result<[u8; 32]> {
    // domainSeparator = keccak256(abi.encode(
    //   keccak256("EIP712Domain(...)"),
    //   keccak256("Polymarket CTF Exchange"),
    //   keccak256("1"),
    //   uint256(137),
    //   verifyingContract))
    let mut buf = Vec::with_capacity(5 * 32);
    buf.extend_from_slice(&domain_typehash());
    buf.extend_from_slice(&keccak256(DOMAIN_NAME.as_bytes()));
    buf.extend_from_slice(&keccak256(DOMAIN_VERSION.as_bytes()));
    let mut chain_be = [0u8; 32];
    chain_be[24..].copy_from_slice(&POLYGON_CHAIN_ID.to_be_bytes());
    buf.extend_from_slice(&chain_be);
    buf.extend_from_slice(&encode_address(exchange.verifying_contract())?);
    Ok(keccak256(&buf))
}

fn struct_hash(order: &OrderInput) -> Result<[u8; 32]> {
    if order.side > 1 {
        return Err(anyhow!("side must be 0 (BUY) or 1 (SELL)"));
    }
    if order.signature_type > 3 {
        return Err(anyhow!("signatureType must be 0, 1, 2, or 3"));
    }
    // V2 layout: typehash + 11 32-byte words matching
    // (salt, maker, signer, tokenId, makerAmount, takerAmount, side,
    //  signatureType, timestamp, metadata, builder).
    let mut buf = Vec::with_capacity(12 * 32);
    buf.extend_from_slice(&order_typehash());
    buf.extend_from_slice(&encode_uint256_decimal(&order.salt)?);
    buf.extend_from_slice(&encode_address(&order.maker)?);
    buf.extend_from_slice(&encode_address(&order.signer)?);
    buf.extend_from_slice(&encode_uint256_decimal(&order.token_id)?);
    buf.extend_from_slice(&encode_uint256_decimal(&order.maker_amount)?);
    buf.extend_from_slice(&encode_uint256_decimal(&order.taker_amount)?);
    buf.extend_from_slice(&encode_uint8(order.side));
    buf.extend_from_slice(&encode_uint8(order.signature_type));
    buf.extend_from_slice(&encode_uint256_decimal(&order.timestamp)?);
    buf.extend_from_slice(&encode_bytes32_hex(&order.metadata)?);
    buf.extend_from_slice(&encode_bytes32_hex(&order.builder)?);
    Ok(keccak256(&buf))
}

/// Public helper: the order's `contents hash` (struct hash). Same value
/// the wrapped POLY_1271 envelope embeds, and what an EIP-712 verifier
/// recomputes on the matcher side.
pub fn order_contents_hash(order: &OrderInput) -> Result<[u8; 32]> {
    struct_hash(order)
}

/// Public helper: the V2 app domain separator for the exchange the order
/// targets. The POLY_1271 wrapped signature embeds this verbatim.
pub fn app_domain_separator_for(exchange: ExchangeKind) -> Result<[u8; 32]> {
    domain_separator(exchange)
}

// ─── POLY_1271 / Solady TypedDataSign envelope ──────────────────────────
//
// When a maker is a deposit wallet (sigType 3), Polymarket V2 wants a
// signature that the wallet contract can verify via EIP-1271. The wallet
// uses Solady's nested TypedDataSign scheme (rfc draft EIP-7739) — the
// outer digest is signed by the EOA, and the wallet contract reconstructs
// it from the wrapped envelope's tail bytes.
//
// Wire format (mirrors rs-clob-client-v2 `sign_poly1271_order`):
//
//   inner_sig (65) || app_domain_sep (32) || contents_hash (32)
//                  || ORDER_TYPE_STRING (raw bytes)
//                  || uint16_be(len(ORDER_TYPE_STRING))
//
// The outer EOA-signed digest is:
//
//   keccak256(0x1901 || app_domain_sep || typed_data_sign_struct_hash)
//
// where typed_data_sign_struct_hash hashes Solady's `TypedDataSign` over
// (contents, name="DepositWallet", version="1", chainId, verifyingContract=signer, salt=0).

/// Solady's nested wrapper type. The exchange's order struct is embedded
/// by reference via `contents`, and its full definition is appended.
pub const SOLADY_TYPE_STRING: &str = concat!(
    "TypedDataSign(Order contents,string name,string version,uint256 chainId,",
    "address verifyingContract,bytes32 salt)",
    "Order(uint256 salt,address maker,address signer,uint256 tokenId,",
    "uint256 makerAmount,uint256 takerAmount,uint8 side,uint8 signatureType,",
    "uint256 timestamp,bytes32 metadata,bytes32 builder)"
);

/// Order struct in standalone form — needed twice in the wrapped envelope
/// because the wallet's EIP-1271 verifier reconstructs the hash from the
/// trailing bytes.
pub const ORDER_TYPE_STRING: &str = concat!(
    "Order(uint256 salt,address maker,address signer,uint256 tokenId,",
    "uint256 makerAmount,uint256 takerAmount,uint8 side,uint8 signatureType,",
    "uint256 timestamp,bytes32 metadata,bytes32 builder)"
);

const DEPOSIT_WALLET_NAME: &str = "DepositWallet";
const DEPOSIT_WALLET_VERSION: &str = "1";

/// Build the outer EOA digest for a POLY_1271 order. The caller's signer
/// signs this and the resulting 65-byte signature gets wrapped via
/// [`wrap_poly1271_signature`] before being sent to the CLOB.
///
/// `wallet_signer` is the deposit-wallet contract address that the order
/// names as `order.signer` (also the verifyingContract in the inner
/// `TypedDataSign` — Solady's convention).
pub fn poly1271_outer_digest(
    order: &OrderInput,
    wallet_signer: &str,
) -> Result<([u8; 32], [u8; 32], [u8; 32])> {
    let app_sep = domain_separator(order.exchange)?;
    let contents = struct_hash(order)?;

    // typed_data_sign_struct_hash = keccak(
    //   keccak(SOLADY_TYPE_STRING)
    //   || contents
    //   || keccak("DepositWallet") || keccak("1")
    //   || chainId (uint256) || wallet_signer (address-padded) || bytes32(0)
    // )
    let mut tds = Vec::with_capacity(7 * 32);
    tds.extend_from_slice(&keccak256(SOLADY_TYPE_STRING.as_bytes()));
    tds.extend_from_slice(&contents);
    tds.extend_from_slice(&keccak256(DEPOSIT_WALLET_NAME.as_bytes()));
    tds.extend_from_slice(&keccak256(DEPOSIT_WALLET_VERSION.as_bytes()));
    let mut chain_be = [0u8; 32];
    chain_be[24..].copy_from_slice(&POLYGON_CHAIN_ID.to_be_bytes());
    tds.extend_from_slice(&chain_be);
    tds.extend_from_slice(&encode_address(wallet_signer)?);
    tds.extend_from_slice(&[0u8; 32]); // bytes32(0) salt
    let tds_hash = keccak256(&tds);

    let mut prefix = Vec::with_capacity(2 + 32 + 32);
    prefix.push(0x19);
    prefix.push(0x01);
    prefix.extend_from_slice(&app_sep);
    prefix.extend_from_slice(&tds_hash);
    let outer = keccak256(&prefix);
    Ok((outer, app_sep, contents))
}

/// Wrap a 65-byte EOA signature in the EIP-7739 / Solady envelope so the
/// deposit wallet's EIP-1271 verifier can reconstruct the typed data.
///
/// Returns a 0x-prefixed lowercase hex string.
pub fn wrap_poly1271_signature(
    inner_sig: &[u8],
    app_domain_sep: &[u8; 32],
    contents_hash: &[u8; 32],
) -> String {
    let type_str = ORDER_TYPE_STRING.as_bytes();
    let len_be = (type_str.len() as u16).to_be_bytes();
    let mut out = String::with_capacity(
        2 + (inner_sig.len() + 32 + 32 + type_str.len() + 2) * 2,
    );
    out.push_str("0x");
    out.push_str(&hex::encode(inner_sig));
    out.push_str(&hex::encode(app_domain_sep));
    out.push_str(&hex::encode(contents_hash));
    out.push_str(&hex::encode(type_str));
    out.push_str(&hex::encode(len_be));
    out
}

/// Final EIP-712 digest: `keccak256(0x1901 || domainSeparator || structHash)`.
/// This is the 32-byte hash that gets ECDSA-signed.
pub fn order_digest(order: &OrderInput) -> Result<[u8; 32]> {
    let dom = domain_separator(order.exchange)?;
    let sh = struct_hash(order)?;
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

    // Reference values cross-checked against a fresh `signTypedData` run from
    // the frontend's polymarketOrderSigning.ts with the same input.
    const ZERO_BYTES32: &str = "0x0000000000000000000000000000000000000000000000000000000000000000";

    fn fixture_order() -> OrderInput {
        OrderInput {
            salt: "1".into(),
            maker: "0x9A86ede983F707A0694B9ec37b36f70032333476".into(),
            signer: "0x89bcdee4a284cb0848eebb975bec78ab5bd06cfa".into(),
            token_id: "1234567890".into(),
            maker_amount: "100000".into(),
            taker_amount: "200000".into(),
            side: 0,
            signature_type: 2,
            timestamp: "1780000000000".into(),
            metadata: ZERO_BYTES32.into(),
            builder: ZERO_BYTES32.into(),
            exchange: ExchangeKind::Standard,
        }
    }

    #[test]
    fn digest_is_deterministic_and_32_bytes() {
        let o = fixture_order();
        let d1 = order_digest(&o).unwrap();
        let d2 = order_digest(&o).unwrap();
        assert_eq!(d1, d2);
        assert_eq!(d1.len(), 32);
    }

    #[test]
    fn negrisk_produces_different_digest_than_standard() {
        let mut o = fixture_order();
        let standard = order_digest(&o).unwrap();
        o.exchange = ExchangeKind::Negrisk;
        let negrisk = order_digest(&o).unwrap();
        assert_ne!(standard, negrisk, "different verifyingContract → different digest");
    }

    #[test]
    fn rejects_invalid_address() {
        let mut o = fixture_order();
        o.maker = "0xnothex".into();
        assert!(order_digest(&o).is_err());
    }

    #[test]
    fn rejects_invalid_decimal() {
        let mut o = fixture_order();
        o.salt = "1abc".into();
        assert!(order_digest(&o).is_err());
    }

    #[test]
    fn encode_uint256_handles_zero_and_one() {
        let zero = encode_uint256_decimal("0").unwrap();
        assert_eq!(zero, [0u8; 32]);
        let one = encode_uint256_decimal("1").unwrap();
        let mut expected = [0u8; 32];
        expected[31] = 1;
        assert_eq!(one, expected);
    }
}
