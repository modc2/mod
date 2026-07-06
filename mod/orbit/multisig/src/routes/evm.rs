use axum::{
    extract::{Path, State},
    routing::{get, post},
    Json, Router,
};
use tiny_keccak::{Hasher, Keccak};

use crate::types::*;
use crate::AppState;

pub fn router() -> Router<AppState> {
    Router::new()
        .route("/api/evm/multisig", post(create_multisig))
        .route("/api/evm/tx/propose", post(propose_tx))
        .route("/api/evm/tx/:id/approve", post(approve_tx))
        .route("/api/evm/tx/:id/execute", post(execute_tx))
        .route("/api/evm/tx/:id/sign-data", get(get_sign_data))
        .route("/api/evm/balance/:address", get(get_balance))
        .route("/api/evm/nonce/:address", get(get_safe_nonce))
}

fn keccak256(data: &[u8]) -> [u8; 32] {
    let mut hasher = Keccak::v256();
    let mut out = [0u8; 32];
    hasher.update(data);
    hasher.finalize(&mut out);
    out
}

fn pad32(data: &[u8]) -> Vec<u8> {
    let mut padded = vec![0u8; 32 - data.len().min(32)];
    padded.extend_from_slice(data);
    padded.truncate(32);
    padded
}

fn u256_bytes(val: u64) -> Vec<u8> {
    pad32(&val.to_be_bytes())
}

fn addr_bytes(addr: &str) -> Vec<u8> {
    let clean = addr.strip_prefix("0x").unwrap_or(addr);
    let bytes = hex::decode(clean).unwrap_or_default();
    pad32(&bytes)
}

/// Compute Safe domain separator: keccak256(abi.encode(DOMAIN_TYPEHASH, chainId, safeAddr))
fn domain_separator(chain_id: u64, safe_addr: &str) -> [u8; 32] {
    let type_hash = keccak256(b"EIP712Domain(uint256 chainId,address verifyingContract)");
    let mut encoded = Vec::with_capacity(96);
    encoded.extend_from_slice(&type_hash);
    encoded.extend_from_slice(&u256_bytes(chain_id));
    encoded.extend_from_slice(&addr_bytes(safe_addr));
    keccak256(&encoded)
}

/// Compute Safe transaction hash
fn safe_tx_hash(
    safe_addr: &str,
    chain_id: u64,
    to: &str,
    value: &str,
    data: &str,
    nonce: u64,
) -> String {
    let safe_tx_typehash = keccak256(
        b"SafeTx(address to,uint256 value,bytes data,uint8 operation,uint256 safeTxGas,uint256 baseGas,uint256 gasPrice,address gasToken,address refundReceiver,uint256 nonce)",
    );
    let data_bytes = hex::decode(data.strip_prefix("0x").unwrap_or(data)).unwrap_or_default();
    let data_hash = keccak256(&data_bytes);

    let value_u128: u128 = value.parse().unwrap_or(0);
    let value_bytes = pad32(&value_u128.to_be_bytes());

    let mut encoded = Vec::new();
    encoded.extend_from_slice(&safe_tx_typehash);
    encoded.extend_from_slice(&addr_bytes(to));
    encoded.extend_from_slice(&value_bytes);
    encoded.extend_from_slice(&data_hash);
    encoded.extend_from_slice(&u256_bytes(0)); // operation: Call
    encoded.extend_from_slice(&u256_bytes(0)); // safeTxGas
    encoded.extend_from_slice(&u256_bytes(0)); // baseGas
    encoded.extend_from_slice(&u256_bytes(0)); // gasPrice
    encoded.extend_from_slice(&addr_bytes("0x0000000000000000000000000000000000000000"));
    encoded.extend_from_slice(&addr_bytes("0x0000000000000000000000000000000000000000"));
    encoded.extend_from_slice(&u256_bytes(nonce));

    let safe_hash = keccak256(&encoded);
    let ds = domain_separator(chain_id, safe_addr);

    let mut final_data = vec![0x19u8, 0x01u8];
    final_data.extend_from_slice(&ds);
    final_data.extend_from_slice(&safe_hash);

    format!("0x{}", hex::encode(keccak256(&final_data)))
}

/// Pack signatures for Safe execTransaction (sorted by signer address)
fn pack_signatures(mut approvals: Vec<Approval>) -> String {
    approvals.sort_by(|a, b| {
        a.owner.to_lowercase().cmp(&b.owner.to_lowercase())
    });
    let mut packed = Vec::new();
    for approval in &approvals {
        let sig = hex::decode(
            approval.signature.strip_prefix("0x").unwrap_or(&approval.signature),
        )
        .unwrap_or_default();
        if sig.len() == 65 {
            // r (32) + s (32) + v (1)
            // For eth_sign signatures, v needs +4
            let mut adjusted = sig.clone();
            if adjusted[64] < 30 {
                adjusted[64] += 4; // eth_sign adjustment
            }
            packed.extend_from_slice(&adjusted);
        }
    }
    format!("0x{}", hex::encode(packed))
}

/// Encode execTransaction calldata
fn encode_exec_transaction(
    to: &str,
    value: &str,
    data: &str,
    signatures: &str,
) -> String {
    let selector = &keccak256(
        b"execTransaction(address,uint256,bytes,uint8,uint256,uint256,uint256,address,address,bytes)",
    )[..4];

    let data_bytes = hex::decode(data.strip_prefix("0x").unwrap_or(data)).unwrap_or_default();
    let sig_bytes = hex::decode(signatures.strip_prefix("0x").unwrap_or(signatures)).unwrap_or_default();

    let value_u128: u128 = value.parse().unwrap_or(0);

    // ABI encode all parameters
    let mut encoded = Vec::new();
    encoded.extend_from_slice(selector);

    // Head section: 10 params, each 32 bytes (dynamic ones have offset pointers)
    encoded.extend_from_slice(&addr_bytes(to));                    // to
    encoded.extend_from_slice(&pad32(&value_u128.to_be_bytes()));  // value
    // data offset: 10 * 32 = 320
    encoded.extend_from_slice(&u256_bytes(320));                   // data offset
    encoded.extend_from_slice(&u256_bytes(0));                     // operation
    encoded.extend_from_slice(&u256_bytes(0));                     // safeTxGas
    encoded.extend_from_slice(&u256_bytes(0));                     // baseGas
    encoded.extend_from_slice(&u256_bytes(0));                     // gasPrice
    encoded.extend_from_slice(&addr_bytes("0x0000000000000000000000000000000000000000"));
    encoded.extend_from_slice(&addr_bytes("0x0000000000000000000000000000000000000000"));
    // signatures offset: 320 + 32 + data_bytes padded
    let data_padded_len = ((data_bytes.len() + 31) / 32) * 32;
    let sig_offset = 320 + 32 + data_padded_len;
    encoded.extend_from_slice(&u256_bytes(sig_offset as u64));     // signatures offset

    // data (dynamic)
    encoded.extend_from_slice(&u256_bytes(data_bytes.len() as u64));
    encoded.extend_from_slice(&data_bytes);
    // pad to 32
    let pad = data_padded_len - data_bytes.len();
    encoded.extend_from_slice(&vec![0u8; pad]);

    // signatures (dynamic)
    encoded.extend_from_slice(&u256_bytes(sig_bytes.len() as u64));
    encoded.extend_from_slice(&sig_bytes);
    let sig_pad = ((sig_bytes.len() + 31) / 32) * 32 - sig_bytes.len();
    encoded.extend_from_slice(&vec![0u8; sig_pad]);

    format!("0x{}", hex::encode(encoded))
}

// --- Handlers ---

async fn create_multisig(
    State(state): State<AppState>,
    Json(req): Json<CreateMultisigReq>,
) -> Json<serde_json::Value> {
    if req.chain != Chain::Base {
        return Json(serde_json::json!({ "ok": false, "error": "Use /api/evm/* for Base chain" }));
    }
    if req.threshold == 0 || req.threshold as usize > req.owners.len() {
        return Json(serde_json::json!({ "ok": false, "error": "Invalid threshold" }));
    }

    let wallet = MultisigWallet {
        id: uuid::Uuid::new_v4().to_string(),
        chain: Chain::Base,
        name: req.name,
        owners: req.owners,
        threshold: req.threshold,
        address: req.address,
        safe_version: Some("1.4.1".into()),
        created_at: chrono::Utc::now().to_rfc3339(),
    };

    let mut store = state.store.write().await;
    match store.add_multisig(wallet) {
        Ok(w) => Json(serde_json::json!({ "ok": true, "data": w })),
        Err(e) => Json(serde_json::json!({ "ok": false, "error": e.to_string() })),
    }
}

async fn propose_tx(
    State(state): State<AppState>,
    Json(req): Json<ProposeTxReq>,
) -> Json<serde_json::Value> {
    let store_read = state.store.read().await;
    let multisig = match store_read.get_multisig(&req.multisig_id) {
        Some(m) => m.clone(),
        None => return Json(serde_json::json!({ "ok": false, "error": "Multisig not found" })),
    };

    let safe_addr = match &multisig.address {
        Some(a) => a.clone(),
        None => return Json(serde_json::json!({ "ok": false, "error": "Safe address not set" })),
    };

    if !multisig.owners.iter().any(|o| o.to_lowercase() == req.proposer.to_lowercase()) {
        return Json(serde_json::json!({ "ok": false, "error": "Not an owner" }));
    }

    let pending = store_read.list_pending_transactions(&req.multisig_id);
    let nonce = pending.len() as u64;
    drop(store_read);

    let hash = safe_tx_hash(
        &safe_addr,
        state.config.base_chain_id,
        &req.to,
        &req.value,
        &req.data,
        nonce,
    );

    let mut approvals = Vec::new();
    if let Some(sig) = &req.signature {
        approvals.push(Approval {
            owner: req.proposer.clone(),
            signature: sig.clone(),
            approved_at: chrono::Utc::now().to_rfc3339(),
        });
    }

    let status = if approvals.len() >= multisig.threshold as usize {
        TxStatus::Approved
    } else {
        TxStatus::Pending
    };

    let tx = Transaction {
        id: uuid::Uuid::new_v4().to_string(),
        multisig_id: req.multisig_id,
        chain: Chain::Base,
        to: req.to,
        value: req.value,
        data: req.data,
        description: req.description,
        nonce,
        call_hash: Some(hash.clone()),
        approvals,
        status,
        tx_hash: None,
        created_at: chrono::Utc::now().to_rfc3339(),
    };

    let mut store = state.store.write().await;
    match store.add_transaction(tx) {
        Ok(t) => Json(serde_json::json!({
            "ok": true,
            "data": t,
            "safe_tx_hash": hash,
        })),
        Err(e) => Json(serde_json::json!({ "ok": false, "error": e.to_string() })),
    }
}

async fn get_sign_data(
    State(state): State<AppState>,
    Path(tx_id): Path<String>,
) -> Json<serde_json::Value> {
    let store = state.store.read().await;
    let tx = match store.get_transaction(&tx_id) {
        Some(t) => t,
        None => return Json(serde_json::json!({ "ok": false, "error": "Transaction not found" })),
    };
    let multisig = match store.get_multisig(&tx.multisig_id) {
        Some(m) => m,
        None => return Json(serde_json::json!({ "ok": false, "error": "Multisig not found" })),
    };
    let safe_addr = multisig.address.as_deref().unwrap_or_default();

    let sign_data = EvmSignData {
        safe_address: safe_addr.to_string(),
        chain_id: state.config.base_chain_id,
        safe_tx_hash: tx.call_hash.clone().unwrap_or_default(),
        domain: Eip712Domain {
            chain_id: state.config.base_chain_id,
            verifying_contract: safe_addr.to_string(),
        },
        message: SafeTxMessage {
            to: tx.to.clone(),
            value: tx.value.clone(),
            data: tx.data.clone(),
            operation: 0,
            safe_tx_gas: "0".into(),
            base_gas: "0".into(),
            gas_price: "0".into(),
            gas_token: "0x0000000000000000000000000000000000000000".into(),
            refund_receiver: "0x0000000000000000000000000000000000000000".into(),
            nonce: tx.nonce,
        },
    };

    Json(serde_json::json!({ "ok": true, "data": sign_data }))
}

async fn approve_tx(
    State(state): State<AppState>,
    Path(tx_id): Path<String>,
    Json(req): Json<ApproveReq>,
) -> Json<serde_json::Value> {
    let threshold = {
        let store = state.store.read().await;
        let tx = match store.get_transaction(&tx_id) {
            Some(t) => t,
            None => return Json(serde_json::json!({ "ok": false, "error": "Transaction not found" })),
        };
        let multisig = match store.get_multisig(&tx.multisig_id) {
            Some(m) => m,
            None => return Json(serde_json::json!({ "ok": false, "error": "Multisig not found" })),
        };
        if !multisig.owners.iter().any(|o| o.to_lowercase() == req.owner.to_lowercase()) {
            return Json(serde_json::json!({ "ok": false, "error": "Not an owner" }));
        }
        multisig.threshold
    };

    let mut store = state.store.write().await;
    match store.add_approval(&tx_id, &req.owner, &req.signature, threshold) {
        Ok(Some(tx)) => Json(serde_json::json!({ "ok": true, "data": tx })),
        Ok(None) => Json(serde_json::json!({ "ok": false, "error": "Transaction not found" })),
        Err(e) => Json(serde_json::json!({ "ok": false, "error": e.to_string() })),
    }
}

async fn execute_tx(
    State(state): State<AppState>,
    Path(tx_id): Path<String>,
    Json(req): Json<ExecuteReq>,
) -> Json<serde_json::Value> {
    let store = state.store.read().await;
    let tx = match store.get_transaction(&tx_id) {
        Some(t) => t.clone(),
        None => return Json(serde_json::json!({ "ok": false, "error": "Transaction not found" })),
    };
    let multisig = match store.get_multisig(&tx.multisig_id) {
        Some(m) => m.clone(),
        None => return Json(serde_json::json!({ "ok": false, "error": "Multisig not found" })),
    };
    drop(store);

    if tx.status != TxStatus::Approved && tx.approvals.len() < multisig.threshold as usize {
        return Json(serde_json::json!({ "ok": false, "error": "Not enough approvals" }));
    }

    let safe_addr = multisig.address.as_deref().unwrap_or_default();
    let packed_sigs = pack_signatures(tx.approvals.clone());
    let calldata = encode_exec_transaction(&tx.to, &tx.value, &tx.data, &packed_sigs);

    // If the frontend submitted the on-chain tx hash, store it
    if let Some(hash) = &req.tx_hash {
        let mut store = state.store.write().await;
        let _ = store.set_tx_status(&tx_id, TxStatus::Executed, Some(hash));
    }

    Json(serde_json::json!({
        "ok": true,
        "data": {
            "to": safe_addr,
            "data": calldata,
            "value": "0",
            "signatures": packed_sigs,
            "tx": tx,
        }
    }))
}

async fn get_balance(
    State(state): State<AppState>,
    Path(address): Path<String>,
) -> Json<serde_json::Value> {
    let body = serde_json::json!({
        "jsonrpc": "2.0",
        "method": "eth_getBalance",
        "params": [address, "latest"],
        "id": 1
    });
    match state.http.post(&state.config.base_rpc).json(&body).send().await {
        Ok(resp) => match resp.json::<serde_json::Value>().await {
            Ok(data) => {
                let hex_balance = data["result"].as_str().unwrap_or("0x0");
                let balance = u128::from_str_radix(
                    hex_balance.strip_prefix("0x").unwrap_or("0"),
                    16,
                ).unwrap_or(0);
                Json(serde_json::json!({
                    "ok": true,
                    "data": {
                        "address": address,
                        "balance_wei": balance.to_string(),
                        "balance_eth": format!("{:.6}", balance as f64 / 1e18),
                        "chain": "base",
                    }
                }))
            }
            Err(e) => Json(serde_json::json!({ "ok": false, "error": e.to_string() })),
        },
        Err(e) => Json(serde_json::json!({ "ok": false, "error": e.to_string() })),
    }
}

async fn get_safe_nonce(
    State(state): State<AppState>,
    Path(address): Path<String>,
) -> Json<serde_json::Value> {
    // Call nonce() on Safe contract: selector = 0xaffed0e0
    let body = serde_json::json!({
        "jsonrpc": "2.0",
        "method": "eth_call",
        "params": [{
            "to": address,
            "data": "0xaffed0e0"
        }, "latest"],
        "id": 1
    });
    match state.http.post(&state.config.base_rpc).json(&body).send().await {
        Ok(resp) => match resp.json::<serde_json::Value>().await {
            Ok(data) => {
                let hex_nonce = data["result"].as_str().unwrap_or("0x0");
                let nonce = u64::from_str_radix(
                    hex_nonce.strip_prefix("0x").unwrap_or("0"),
                    16,
                ).unwrap_or(0);
                Json(serde_json::json!({
                    "ok": true,
                    "data": { "address": address, "nonce": nonce }
                }))
            }
            Err(e) => Json(serde_json::json!({ "ok": false, "error": e.to_string() })),
        },
        Err(e) => Json(serde_json::json!({ "ok": false, "error": e.to_string() })),
    }
}
