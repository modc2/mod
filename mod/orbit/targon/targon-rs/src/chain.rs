//! Bittensor chain access for the console's in-browser wallet.
//!
//! Targon bills in credits and credits are bought by sending TAO to the SS58
//! address the Hub hands you (`GET /wallet`). This module is the other half of
//! that: it reads a coldkey's balance off finney, builds the top-up extrinsic,
//! and posts the signed bytes back to the chain.
//!
//! The browser never encodes anything. It asks for a payload, hands that to a
//! polkadot-js compatible extension for a signature, and gives the signature
//! back — so all the SCALE lives here, where `tests` can pin it against a
//! known-good extrinsic. Keys never leave the extension; this server never
//! sees or stores one.

use blake2::digest::{Update, VariableOutput};
use blake2::Blake2bVar;
use serde_json::{json, Value};

/// finney, unless someone points us at a local node or a testnet.
pub fn rpc_url() -> String {
    std::env::var("BITTENSOR_RPC").unwrap_or_else(|_| "https://entrypoint-finney.opentensor.ai:443".into())
}

pub const SS58_FORMAT: u16 = 42;
pub const DECIMALS: u32 = 9;
const RAO: u128 = 1_000_000_000;

/// `twox128("System") ++ twox128("Account")` — a constant, so the only hashing
/// left for a storage key is the `Blake2_128Concat` of the account id.
const SYSTEM_ACCOUNT: &str = "26aa394eea5630e07c48ae0c9558cef7b99d880ec681799c0cf30e8886371da9";

/// Subtensor's signed extensions, in metadata order (spec 443). The four
/// subtensor-specific ones carry no data on either side of the signature, so a
/// wallet that doesn't recognise them still encodes the payload correctly —
/// but `CheckMetadataHash` does add the trailing mode byte, and leaving it out
/// would produce a valid-looking signature the chain rejects.
const SIGNED_EXTENSIONS: [&str; 13] = [
    "CheckNonZeroSender",
    "CheckSpecVersion",
    "CheckTxVersion",
    "CheckGenesis",
    "CheckMortality",
    "CheckNonce",
    "CheckWeight",
    "ChargeTransactionPayment",
    "SudoTransactionExtension",
    "CheckShieldedTxValidity",
    "SubtensorTransactionExtension",
    "DrandPriority",
    "CheckMetadataHash",
];

// ── bytes ────────────────────────────────────────────────────────

pub fn to_hex(bytes: &[u8]) -> String {
    let mut s = String::with_capacity(2 + bytes.len() * 2);
    s.push_str("0x");
    for b in bytes {
        s.push_str(&format!("{b:02x}"));
    }
    s
}

pub fn from_hex(s: &str) -> Result<Vec<u8>, String> {
    let s = s.trim().trim_start_matches("0x");
    if s.len() % 2 != 0 {
        return Err("hex string has an odd length".into());
    }
    (0..s.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(&s[i..i + 2], 16).map_err(|_| "not hex".to_string()))
        .collect()
}

/// A hex string that may be a plain number (`"0x1bb"`, as polkadot-js writes
/// the numeric fields of a signer payload) rather than a byte string.
fn hex_num(v: &Value, field: &str) -> Result<u128, String> {
    let s = match v {
        Value::String(s) => s.trim_start_matches("0x").to_string(),
        Value::Number(n) => return Ok(n.as_u64().ok_or(format!("{field} is not a whole number"))? as u128),
        Value::Null => "0".into(),
        _ => return Err(format!("{field} must be a hex string or a number")),
    };
    if s.is_empty() {
        return Ok(0);
    }
    u128::from_str_radix(&s, 16).map_err(|_| format!("{field} is not a hex number"))
}

fn blake2_128(data: &[u8]) -> [u8; 16] {
    let mut h = Blake2bVar::new(16).expect("blake2b-128");
    h.update(data);
    let mut out = [0u8; 16];
    h.finalize_variable(&mut out).expect("blake2b-128");
    out
}

/// SCALE compact — the length/nonce/balance encoding used throughout Substrate.
pub fn compact(mut n: u128) -> Vec<u8> {
    match n {
        0..=0x3f => vec![(n as u8) << 2],
        0x40..=0x3fff => ((n as u16) << 2 | 0b01).to_le_bytes().to_vec(),
        0x4000..=0x3fff_ffff => ((n as u32) << 2 | 0b10).to_le_bytes().to_vec(),
        _ => {
            let mut body = Vec::new();
            while n > 0 {
                body.push((n & 0xff) as u8);
                n >>= 8;
            }
            let mut out = vec![((body.len() as u8 - 4) << 2) | 0b11];
            out.extend(body);
            out
        }
    }
}

fn u64_le(bytes: &[u8]) -> u128 {
    let mut buf = [0u8; 8];
    buf.copy_from_slice(&bytes[..8]);
    u64::from_le_bytes(buf) as u128
}

// ── SS58 ─────────────────────────────────────────────────────────

/// Decode an SS58 address to its 32-byte account id, checksum and all. A
/// mistyped address that still base58-decodes fails the checksum here rather
/// than on-chain, where the TAO would be gone.
pub fn ss58_decode(address: &str) -> Result<[u8; 32], String> {
    let raw = bs58::decode(address.trim())
        .into_vec()
        .map_err(|_| "not a valid SS58 address (bad base58)".to_string())?;
    // One prefix byte for formats < 64 (Bittensor is 42), 32 key bytes, 2 checksum.
    if raw.len() != 35 {
        return Err(format!("SS58 address should decode to 35 bytes, got {}", raw.len()));
    }
    if raw[0] as u16 != SS58_FORMAT {
        return Err(format!(
            "address is for SS58 format {}, Bittensor is {SS58_FORMAT}",
            raw[0]
        ));
    }
    let mut h = Blake2bVar::new(64).expect("blake2b-512");
    h.update(b"SS58PRE");
    h.update(&raw[..33]);
    let mut sum = [0u8; 64];
    h.finalize_variable(&mut sum).expect("blake2b-512");
    if sum[..2] != raw[33..35] {
        return Err("SS58 checksum does not match — check the address".into());
    }
    let mut id = [0u8; 32];
    id.copy_from_slice(&raw[1..33]);
    Ok(id)
}

// ── RPC ──────────────────────────────────────────────────────────

async fn rpc(method: &str, params: Value) -> Result<Value, String> {
    let body = json!({ "jsonrpc": "2.0", "id": 1, "method": method, "params": params });
    let resp = reqwest::Client::new()
        .post(rpc_url())
        .json(&body)
        .timeout(std::time::Duration::from_secs(30))
        .send()
        .await
        .map_err(|e| format!("chain rpc unreachable: {e}"))?;
    let v: Value = resp
        .json()
        .await
        .map_err(|e| format!("chain rpc returned no JSON: {e}"))?;
    if let Some(err) = v.get("error") {
        // The node puts the useful half in `data` — "Inability to pay some
        // fees" rather than a bare "Invalid Transaction".
        let msg = err.get("message").and_then(|m| m.as_str()).unwrap_or("rpc error");
        let detail = err.get("data").and_then(|d| d.as_str()).map(|d| format!(": {d}")).unwrap_or_default();
        return Err(format!("chain rpc {method}: {msg}{detail}"));
    }
    Ok(v.get("result").cloned().unwrap_or(Value::Null))
}

fn tao(rao: u128) -> f64 {
    rao as f64 / RAO as f64
}

/// `AccountInfo`: nonce/consumers/providers/sufficients as u32, then
/// `AccountData` — and subtensor's Balance is a u64, not the u128 most
/// Substrate chains use, so the whole entry is 56 bytes rather than 80.
fn decode_account_info(b: &[u8]) -> Result<(u64, u128, u128, u128), String> {
    if b.len() < 40 {
        return Err(format!("short AccountInfo ({} bytes)", b.len()));
    }
    Ok((
        u32::from_le_bytes([b[0], b[1], b[2], b[3]]) as u64,
        u64_le(&b[16..24]),
        u64_le(&b[24..32]),
        u64_le(&b[32..40]),
    ))
}

/// `System.Account` — free/reserved balance and the nonce, in one storage read.
pub async fn account(address: &str) -> Result<Value, String> {
    let id = ss58_decode(address)?;
    let mut suffix = blake2_128(&id).to_vec(); // Blake2_128Concat: hash ++ key
    suffix.extend(id);
    let key = format!("0x{SYSTEM_ACCOUNT}{}", &to_hex(&suffix)[2..]);
    let raw = rpc("state_getStorage", json!([key])).await?;

    // An account that has never held TAO simply has no entry at all.
    let (nonce, free, reserved, frozen) = match raw.as_str() {
        Some(hex) => decode_account_info(&from_hex(hex)?)?,
        None => (0, 0, 0, 0),
    };
    Ok(json!({
        "address": address,
        "account_id": to_hex(&id),
        "nonce": nonce,
        "free_rao": free.to_string(),
        "free": tao(free),
        "reserved": tao(reserved),
        "frozen": tao(frozen),
        "transferable": tao(free.saturating_sub(frozen)),
        "symbol": "TAO",
        "network": rpc_url(),
    }))
}

// ── Transfer ─────────────────────────────────────────────────────

/// `Balances.transfer_keep_alive(dest, value)` — pallet 5, call 3 on subtensor.
/// Keep-alive rather than allow-death so a top-up can never reap the coldkey.
fn transfer_call(dest: &[u8; 32], rao: u128) -> Vec<u8> {
    let mut call = vec![5u8, 3u8, 0u8]; // pallet, call, MultiAddress::Id
    call.extend(dest);
    call.extend(compact(rao));
    call
}

/// Everything a wallet needs to sign a top-up: the call and an immortal-era
/// signer payload. Immortal keeps the payload self-contained (no block window
/// to chase) at the cost of a transaction that stays replayable until the
/// nonce moves — fine for a deposit you are making right now.
pub async fn prepare(from: &str, to: &str, amount_tao: f64) -> Result<Value, String> {
    if !(amount_tao.is_finite() && amount_tao > 0.0) {
        return Err("amount must be a positive number of TAO".into());
    }
    let rao = (amount_tao * RAO as f64).round() as u128;
    if rao == 0 {
        return Err("amount rounds to zero rao".into());
    }
    if rao > u64::MAX as u128 {
        return Err("amount is larger than a TAO balance can hold".into());
    }
    let from_id = ss58_decode(from)?;
    let dest = ss58_decode(to)?;

    let (runtime, genesis, nonce) = tokio::join!(
        rpc("state_getRuntimeVersion", json!([])),
        rpc("chain_getBlockHash", json!([0])),
        rpc("system_accountNextIndex", json!([from])),
    );
    let runtime = runtime?;
    let genesis = genesis?
        .as_str()
        .ok_or("chain did not return a genesis hash")?
        .to_string();
    let nonce = nonce?.as_u64().ok_or("chain did not return a nonce")?;
    let spec = runtime.get("specVersion").and_then(|v| v.as_u64()).ok_or("no specVersion")?;
    let tx_ver = runtime
        .get("transactionVersion")
        .and_then(|v| v.as_u64())
        .ok_or("no transactionVersion")?;

    let call = to_hex(&transfer_call(&dest, rao));
    Ok(json!({
        "call": call,
        "amount_tao": amount_tao,
        "amount_rao": rao.to_string(),
        "from": from,
        "from_account_id": to_hex(&from_id),
        "to": to,
        "payload": {
            "address": from,
            "method": call,
            "genesisHash": genesis,
            "blockHash": genesis,          // immortal era anchors on genesis
            "blockNumber": "0x00",
            "era": "0x00",
            "nonce": format!("0x{nonce:x}"),
            "tip": "0x00",
            "mode": 0,                     // CheckMetadataHash::Disabled
            "specVersion": format!("0x{spec:x}"),
            "transactionVersion": format!("0x{tx_ver:x}"),
            "signedExtensions": SIGNED_EXTENSIONS,
            "version": 4,
        },
    }))
}

/// Glue a wallet's signature onto the payload it signed.
///
/// The payload is echoed back by the browser rather than kept server-side: the
/// signature covers every field of it, so a tampered payload is rejected by the
/// chain, not trusted by us.
fn assemble(payload: &Value, signature: &str) -> Result<String, String> {
    let address = payload.get("address").and_then(|v| v.as_str()).ok_or("payload has no address")?;
    let signer = ss58_decode(address)?;
    let call = from_hex(payload.get("method").and_then(|v| v.as_str()).ok_or("payload has no method")?)?;

    let era = payload.get("era").and_then(|v| v.as_str()).unwrap_or("0x00");
    if era.trim_start_matches("0x") != "00" {
        return Err("only immortal-era payloads are assembled here".into());
    }
    let nonce = hex_num(payload.get("nonce").unwrap_or(&Value::Null), "nonce")?;
    let tip = hex_num(payload.get("tip").unwrap_or(&Value::Null), "tip")?;
    let mode = hex_num(payload.get("mode").unwrap_or(&json!(0)), "mode")? as u8;

    // Wallets return a MultiSignature — one type byte then 64 bytes. A wallet
    // that hands back the bare 64 is taken to be sr25519, which all of them are.
    let mut sig = from_hex(signature)?;
    if sig.len() == 64 {
        sig.insert(0, 0x01);
    }
    if sig.len() != 65 {
        return Err(format!("signature should be 64 or 65 bytes, got {}", sig.len()));
    }

    let mut ext = vec![0x84u8]; // v4, signed
    ext.push(0x00); // MultiAddress::Id
    ext.extend(signer);
    ext.extend(sig);
    ext.push(0x00); // era: immortal
    ext.extend(compact(nonce));
    ext.extend(compact(tip));
    ext.push(mode);
    ext.extend(call);

    let mut framed = compact(ext.len() as u128);
    framed.extend(&ext);
    Ok(to_hex(&framed))
}

/// Assemble and post. Returns the chain's transaction hash.
pub async fn submit(payload: &Value, signature: &str) -> Result<Value, String> {
    let hex = assemble(payload, signature)?;
    let tx = rpc("author_submitExtrinsic", json!([hex])).await?;
    Ok(json!({
        "tx_hash": tx,
        "extrinsic": hex,
        "explorer": tx.as_str().map(|h| format!("https://taostats.io/extrinsic/{h}")),
        "note": "submitted — the deposit shows up as credits once Targon sees the transfer",
    }))
}

#[cfg(test)]
mod tests {
    use super::*;

    const ALICE: &str = "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY";
    const BOB: &str = "5FHneW46xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty";

    #[test]
    fn compact_matches_scale() {
        assert_eq!(compact(0), vec![0x00]);
        assert_eq!(compact(7), vec![0x1c]);
        assert_eq!(compact(145), vec![0x45, 0x02]);
        // 1.5 TAO: past the four-byte mode, so big-integer encoding.
        assert_eq!(compact(1_500_000_000), from_hex("0x03002f6859").unwrap());
    }

    #[test]
    fn ss58_round_trip() {
        let id = ss58_decode(ALICE).unwrap();
        assert_eq!(
            to_hex(&id),
            "0xd43593c715fdd31c61141abd04a99fd6822c8558854ccde39a5684e7a56da27d"
        );
        // One character out: base58 still decodes, the checksum does not.
        assert!(ss58_decode("5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQZ").is_err());
        assert!(ss58_decode("not-an-address").is_err());
    }

    /// Pinned against an extrinsic built by py-substrate-interface on finney
    /// (spec 443, tx 1): same call, same nonce, same signature bytes.
    #[test]
    fn transfer_call_matches_reference() {
        let dest = ss58_decode(BOB).unwrap();
        assert_eq!(
            to_hex(&transfer_call(&dest, 1_234_567_890_123)),
            "0x0503008eaf04151687736326c9fea17e25fc5287613693c912909cb226aa4794f26a480bcb04fb711f01"
        );
    }

    /// Pinned byte-for-byte against `create_signed_extrinsic` from
    /// py-substrate-interface on finney: Alice → Bob, 1234.567890123 TAO,
    /// nonce 7, no tip, immortal. Same signature in, same extrinsic out.
    #[test]
    fn signed_extrinsic_matches_reference() {
        let call = "0x0503008eaf04151687736326c9fea17e25fc5287613693c912909cb226aa4794f26a480bcb04fb711f01";
        let sig = "0x0192db675614b98e16e8abc1b082fd05c8e570f0a74f34ff72a760f68a0b7fcd7a\
                   1f575ddbfdc286eaa37867e4fafacbd19643b1dafbe2a0902b29d5d7d76e918a";
        let payload =
            json!({ "address": ALICE, "method": call, "era": "0x00", "nonce": "0x7", "tip": "0x0", "mode": 0 });

        assert_eq!(
            assemble(&payload, sig).unwrap(),
            "0x45028400d43593c715fdd31c61141abd04a99fd6822c8558854ccde39a5684e7a56da27d\
             0192db675614b98e16e8abc1b082fd05c8e570f0a74f34ff72a760f68a0b7fcd7a1f575ddb\
             fdc286eaa37867e4fafacbd19643b1dafbe2a0902b29d5d7d76e918a001c00000503008eaf\
             04151687736326c9fea17e25fc5287613693c912909cb226aa4794f26a480bcb04fb711f01"
        );
    }

    /// A real `System.Account` entry off finney, with the balance the node's
    /// own SDK reported for it: 28454065 rao, nonce 661.
    #[test]
    fn account_info_decodes_u64_balances() {
        let raw = from_hex(
            "0x95020000000000000100000000000000b12cb201000000000000000000000000000000\
             000000000000000000000000000000000000000080",
        )
        .unwrap();
        assert_eq!(raw.len(), 56);
        assert_eq!(decode_account_info(&raw).unwrap(), (661, 28_454_065, 0, 0));
        assert!(decode_account_info(&[0u8; 8]).is_err());
    }

    #[test]
    fn assemble_rejects_a_mangled_signature() {
        let payload = json!({ "address": ALICE, "method": "0x0503", "era": "0x00", "nonce": "0x0" });
        assert!(assemble(&payload, "0xdeadbeef").is_err());
        // Mortal eras would need the era bytes and their block hash; say so.
        let mortal = json!({ "address": ALICE, "method": "0x0503", "era": "0x2503", "nonce": "0x0" });
        assert!(assemble(&mortal, &format!("0x01{}", "aa".repeat(64))).is_err());
    }
}
