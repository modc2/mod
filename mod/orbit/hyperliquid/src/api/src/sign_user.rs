//! Hyperliquid "user-signed" action signing.
//!
//! A handful of bridging actions don't use the L1 msgpack-hash scheme; they
//! use direct EIP-712 typed-data over the action struct, with the real
//! Arbitrum chain id (42161 mainnet / 421614 testnet) in the domain.
//!
//! Covered here:
//!   - withdraw3        (withdraw USDC to L1)
//!   - usdSend          (HL → HL USDC transfer)
//!   - spotSend         (HL → HL spot token transfer)
//!   - usdClassTransfer (perp ↔ spot account)
//!
//! These map to the "HyperliquidTransaction:*" type names in HL's EIP-712
//! type registry.

use serde_json::{json, Value};

use crate::signer::keccak256;

/// Arbitrum One.
pub const SIGNATURE_CHAIN_ID_MAINNET: u64 = 42161;
/// Arbitrum Sepolia.
pub const SIGNATURE_CHAIN_ID_TESTNET: u64 = 421614;

pub fn hyperliquid_chain(is_mainnet: bool) -> &'static str {
    if is_mainnet { "Mainnet" } else { "Testnet" }
}
pub fn signature_chain_id_hex(is_mainnet: bool) -> String {
    let n = if is_mainnet { SIGNATURE_CHAIN_ID_MAINNET } else { SIGNATURE_CHAIN_ID_TESTNET };
    format!("0x{:x}", n)
}

/// Build the EIP-712 digest for a user-signed action.
///
/// `type_name` is the primary type name (e.g. "HyperliquidTransaction:Withdraw").
/// `type_def_body` is the body of the type definition WITHOUT the leading
/// name and parens (e.g. "string hyperliquidChain,string destination,...").
/// `field_hashes_in_order` is the concatenated 32-byte encoding of each
/// field in declaration order.
pub fn user_signed_digest(
    type_name: &str,
    type_def_body: &str,
    field_hashes_in_order: &[u8],
    is_mainnet: bool,
) -> [u8; 32] {
    // Domain separator. Verifying contract is the zero address, as with L1.
    let domain_type_hash = keccak256(
        b"EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)",
    );
    let name_hash = keccak256(b"HyperliquidSignTransaction");
    let version_hash = keccak256(b"1");
    let chain_id = if is_mainnet { SIGNATURE_CHAIN_ID_MAINNET } else { SIGNATURE_CHAIN_ID_TESTNET };
    let mut chain_enc = [0u8; 32];
    chain_enc[24..].copy_from_slice(&chain_id.to_be_bytes());
    let verifying_contract = [0u8; 32];

    let mut domain_enc = Vec::with_capacity(32 * 5);
    domain_enc.extend_from_slice(&domain_type_hash);
    domain_enc.extend_from_slice(&name_hash);
    domain_enc.extend_from_slice(&version_hash);
    domain_enc.extend_from_slice(&chain_enc);
    domain_enc.extend_from_slice(&verifying_contract);
    let domain_separator = keccak256(&domain_enc);

    // Struct hash.
    let mut type_def = String::new();
    type_def.push_str(type_name);
    type_def.push('(');
    type_def.push_str(type_def_body);
    type_def.push(')');
    let type_hash = keccak256(type_def.as_bytes());

    let mut struct_enc = Vec::with_capacity(32 + field_hashes_in_order.len());
    struct_enc.extend_from_slice(&type_hash);
    struct_enc.extend_from_slice(field_hashes_in_order);
    let struct_hash = keccak256(&struct_enc);

    let mut pre = Vec::with_capacity(2 + 32 + 32);
    pre.extend_from_slice(&[0x19, 0x01]);
    pre.extend_from_slice(&domain_separator);
    pre.extend_from_slice(&struct_hash);
    keccak256(&pre)
}

// ─── encode helpers (EIP-712 field encoding) ────────────────────────────

fn enc_string(s: &str) -> [u8; 32] { keccak256(s.as_bytes()) }
fn enc_uint64(n: u64) -> [u8; 32] {
    let mut o = [0u8; 32];
    o[24..].copy_from_slice(&n.to_be_bytes());
    o
}
fn enc_bool(b: bool) -> [u8; 32] {
    let mut o = [0u8; 32];
    o[31] = if b { 1 } else { 0 };
    o
}

// ─── Action builders ────────────────────────────────────────────────────

/// `withdraw3` — withdraw USDC to L1 (Arbitrum). `destination` is the L1
/// address; `amount` is a string of USDC (e.g. "10.5" for $10.50).
pub fn build_withdraw3(destination: &str, amount: &str, time_ms: u64, is_mainnet: bool) -> (Value, [u8; 32]) {
    let action = json!({
        "type": "withdraw3",
        "hyperliquidChain": hyperliquid_chain(is_mainnet),
        "signatureChainId": signature_chain_id_hex(is_mainnet),
        "destination": destination,
        "amount": amount,
        "time": time_ms,
    });
    let mut fields = Vec::with_capacity(32 * 4);
    fields.extend_from_slice(&enc_string(hyperliquid_chain(is_mainnet)));
    fields.extend_from_slice(&enc_string(destination));
    fields.extend_from_slice(&enc_string(amount));
    fields.extend_from_slice(&enc_uint64(time_ms));
    let d = user_signed_digest(
        "HyperliquidTransaction:Withdraw",
        "string hyperliquidChain,string destination,string amount,uint64 time",
        &fields,
        is_mainnet,
    );
    (action, d)
}

/// `usdSend` — HL → HL USDC transfer to another user.
pub fn build_usd_send(destination: &str, amount: &str, time_ms: u64, is_mainnet: bool) -> (Value, [u8; 32]) {
    let action = json!({
        "type": "usdSend",
        "hyperliquidChain": hyperliquid_chain(is_mainnet),
        "signatureChainId": signature_chain_id_hex(is_mainnet),
        "destination": destination,
        "amount": amount,
        "time": time_ms,
    });
    let mut fields = Vec::with_capacity(32 * 4);
    fields.extend_from_slice(&enc_string(hyperliquid_chain(is_mainnet)));
    fields.extend_from_slice(&enc_string(destination));
    fields.extend_from_slice(&enc_string(amount));
    fields.extend_from_slice(&enc_uint64(time_ms));
    let d = user_signed_digest(
        "HyperliquidTransaction:UsdSend",
        "string hyperliquidChain,string destination,string amount,uint64 time",
        &fields,
        is_mainnet,
    );
    (action, d)
}

/// `spotSend` — HL → HL spot token transfer. `token` is the canonical
/// `<symbol>:<token_id>` string (e.g. "PURR:0x...").
pub fn build_spot_send(destination: &str, token: &str, amount: &str, time_ms: u64, is_mainnet: bool) -> (Value, [u8; 32]) {
    let action = json!({
        "type": "spotSend",
        "hyperliquidChain": hyperliquid_chain(is_mainnet),
        "signatureChainId": signature_chain_id_hex(is_mainnet),
        "destination": destination,
        "token": token,
        "amount": amount,
        "time": time_ms,
    });
    let mut fields = Vec::with_capacity(32 * 5);
    fields.extend_from_slice(&enc_string(hyperliquid_chain(is_mainnet)));
    fields.extend_from_slice(&enc_string(destination));
    fields.extend_from_slice(&enc_string(token));
    fields.extend_from_slice(&enc_string(amount));
    fields.extend_from_slice(&enc_uint64(time_ms));
    let d = user_signed_digest(
        "HyperliquidTransaction:SpotSend",
        "string hyperliquidChain,string destination,string token,string amount,uint64 time",
        &fields,
        is_mainnet,
    );
    (action, d)
}

/// `usdClassTransfer` — move USDC between perp and spot wallets on HL.
/// `nonce` is the action nonce (typically the time_ms).
pub fn build_usd_class_transfer(amount: &str, to_perp: bool, nonce: u64, is_mainnet: bool) -> (Value, [u8; 32]) {
    let action = json!({
        "type": "usdClassTransfer",
        "hyperliquidChain": hyperliquid_chain(is_mainnet),
        "signatureChainId": signature_chain_id_hex(is_mainnet),
        "amount": amount,
        "toPerp": to_perp,
        "nonce": nonce,
    });
    let mut fields = Vec::with_capacity(32 * 4);
    fields.extend_from_slice(&enc_string(hyperliquid_chain(is_mainnet)));
    fields.extend_from_slice(&enc_string(amount));
    fields.extend_from_slice(&enc_bool(to_perp));
    fields.extend_from_slice(&enc_uint64(nonce));
    let d = user_signed_digest(
        "HyperliquidTransaction:UsdClassTransfer",
        "string hyperliquidChain,string amount,bool toPerp,uint64 nonce",
        &fields,
        is_mainnet,
    );
    (action, d)
}

/// `approveAgent` — authorizes a backend agent wallet to sign L1 actions on
/// behalf of the master EOA. The user signs this in their browser wallet
/// before turning on autonomous trading; the backend uses the returned
/// (action, digest) to verify the browser-supplied signature, or relays a
/// pre-built action for the user to sign.
pub fn build_approve_agent(agent_address: &str, agent_name: Option<&str>, nonce: u64, is_mainnet: bool) -> (Value, [u8; 32]) {
    let name = agent_name.unwrap_or("");
    let action = json!({
        "type": "approveAgent",
        "hyperliquidChain": hyperliquid_chain(is_mainnet),
        "signatureChainId": signature_chain_id_hex(is_mainnet),
        "agentAddress": agent_address,
        "agentName": name,
        "nonce": nonce,
    });
    let mut fields = Vec::with_capacity(32 * 4);
    fields.extend_from_slice(&enc_string(hyperliquid_chain(is_mainnet)));
    fields.extend_from_slice(&enc_string(agent_address));
    fields.extend_from_slice(&enc_string(name));
    fields.extend_from_slice(&enc_uint64(nonce));
    let d = user_signed_digest(
        "HyperliquidTransaction:ApproveAgent",
        "string hyperliquidChain,address agentAddress,string agentName,uint64 nonce",
        &fields,
        is_mainnet,
    );
    (action, d)
}

/// Split a 65-byte signature into the {r, s, v} envelope HL expects.
pub fn signature_to_rsv(sig: &[u8; 65]) -> Value {
    let r = format!("0x{}", hex::encode(&sig[..32]));
    let s = format!("0x{}", hex::encode(&sig[32..64]));
    let v = sig[64];
    json!({ "r": r, "s": s, "v": v })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn digests_change_with_amount() {
        let (_, d1) = build_withdraw3("0x1111111111111111111111111111111111111111", "10", 1, true);
        let (_, d2) = build_withdraw3("0x1111111111111111111111111111111111111111", "11", 1, true);
        assert_ne!(d1, d2);
    }

    #[test]
    fn mainnet_testnet_digests_differ() {
        let (_, d1) = build_withdraw3("0x1111111111111111111111111111111111111111", "10", 1, true);
        let (_, d2) = build_withdraw3("0x1111111111111111111111111111111111111111", "10", 1, false);
        assert_ne!(d1, d2);
    }

    #[test]
    fn approve_agent_includes_address_in_action() {
        let agent = "0xabcdefabcdefabcdefabcdefabcdefabcdefabcd";
        let (a, _) = build_approve_agent(agent, Some("hl-engine"), 1700000000, true);
        assert_eq!(a["agentAddress"].as_str().unwrap(), agent);
        assert_eq!(a["agentName"].as_str().unwrap(), "hl-engine");
        assert_eq!(a["type"].as_str().unwrap(), "approveAgent");
    }
}
