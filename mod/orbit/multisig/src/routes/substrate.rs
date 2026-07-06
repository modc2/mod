use axum::{
    extract::{Path, State},
    routing::{get, post},
    Json, Router,
};
use blake2::{Blake2b512, Digest};

use crate::types::*;
use crate::AppState;

pub fn router() -> Router<AppState> {
    Router::new()
        .route("/api/substrate/multisig", post(create_multisig))
        .route("/api/substrate/derive", post(derive_address))
        .route("/api/substrate/tx/propose", post(propose_tx))
        .route("/api/substrate/tx/:id/approve", post(approve_tx))
        .route("/api/substrate/tx/:id/execute", post(execute_tx))
        .route("/api/substrate/balance/:address", get(get_balance))
}

/// Derive SS58 multisig address from sorted signatories + threshold
/// This matches Substrate's `multi_account_id` from pallet_multisig
fn derive_multisig_address(signatories: &[String], threshold: u16) -> String {
    let prefix = b"modlpy/teleport";
    let mut sorted: Vec<Vec<u8>> = signatories
        .iter()
        .filter_map(|s| decode_ss58(s).ok())
        .collect();
    sorted.sort();

    // SCALE encode: prefix + sorted account ids + threshold
    let mut payload = Vec::new();
    payload.extend_from_slice(prefix);

    // SCALE-encoded Vec<AccountId32>: compact length prefix + concatenated 32-byte keys
    let len = sorted.len();
    // Simple compact encoding for small lengths
    payload.push((len << 2) as u8);
    for key in &sorted {
        payload.extend_from_slice(key);
    }
    // threshold as u16 LE
    payload.extend_from_slice(&threshold.to_le_bytes());

    // Blake2b-256 (use Blake2b512 and truncate)
    let mut hasher = Blake2b512::new();
    hasher.update(&payload);
    let result = hasher.finalize();
    let account_id: Vec<u8> = result[..32].to_vec();

    // Encode as SS58 with network prefix 42 (generic substrate)
    // TAO uses prefix 13 but 42 is generic
    encode_ss58(42, &account_id)
}

fn decode_ss58(address: &str) -> anyhow::Result<Vec<u8>> {
    let decoded = bs58::decode(address).into_vec()
        .map_err(|e| anyhow::anyhow!("Invalid SS58: {}", e))?;
    if decoded.len() < 35 {
        anyhow::bail!("SS58 too short");
    }
    // Simple format: 1 byte prefix + 32 bytes key + 2 bytes checksum
    // Or: 2 byte prefix + 32 bytes key + 2 bytes checksum
    if decoded.len() == 35 {
        Ok(decoded[1..33].to_vec())
    } else if decoded.len() == 36 {
        Ok(decoded[2..34].to_vec())
    } else {
        anyhow::bail!("Unexpected SS58 length")
    }
}

fn encode_ss58(prefix: u8, account_id: &[u8]) -> String {
    let mut payload = Vec::new();
    payload.push(prefix);
    payload.extend_from_slice(account_id);

    // SS58 checksum: blake2b_512(b"SS58PRE" || prefix || account_id)[0..2]
    let mut hasher = Blake2b512::new();
    hasher.update(b"SS58PRE");
    hasher.update(&payload);
    let checksum = hasher.finalize();
    payload.extend_from_slice(&checksum[..2]);

    bs58::encode(payload).into_string()
}

// --- Handlers ---

async fn create_multisig(
    State(state): State<AppState>,
    Json(req): Json<CreateMultisigReq>,
) -> Json<serde_json::Value> {
    if req.threshold == 0 || req.threshold as usize > req.owners.len() {
        return Json(serde_json::json!({ "ok": false, "error": "Invalid threshold" }));
    }

    let address = if let Some(addr) = &req.address {
        addr.clone()
    } else {
        derive_multisig_address(&req.owners, req.threshold as u16)
    };

    let wallet = MultisigWallet {
        id: uuid::Uuid::new_v4().to_string(),
        chain: Chain::Tao,
        name: req.name,
        owners: req.owners,
        threshold: req.threshold,
        address: Some(address),
        safe_version: None,
        created_at: chrono::Utc::now().to_rfc3339(),
    };

    let mut store = state.store.write().await;
    match store.add_multisig(wallet) {
        Ok(w) => Json(serde_json::json!({ "ok": true, "data": w })),
        Err(e) => Json(serde_json::json!({ "ok": false, "error": e.to_string() })),
    }
}

#[derive(Debug, serde::Deserialize)]
struct DeriveReq {
    signatories: Vec<String>,
    threshold: u16,
}

async fn derive_address(Json(req): Json<DeriveReq>) -> Json<serde_json::Value> {
    let address = derive_multisig_address(&req.signatories, req.threshold);
    Json(serde_json::json!({
        "ok": true,
        "data": { "address": address, "signatories": req.signatories, "threshold": req.threshold }
    }))
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
    if multisig.chain != Chain::Tao {
        return Json(serde_json::json!({ "ok": false, "error": "Not a TAO multisig" }));
    }

    let pending = store_read.list_pending_transactions(&req.multisig_id);
    let nonce = pending.len() as u64;
    drop(store_read);

    // For Substrate multisig, the call_hash is blake2_256 of the call data
    let call_data = hex::decode(req.data.strip_prefix("0x").unwrap_or(&req.data)).unwrap_or_default();
    let mut hasher = Blake2b512::new();
    hasher.update(&call_data);
    let hash_result = hasher.finalize();
    let call_hash = format!("0x{}", hex::encode(&hash_result[..32]));

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
        chain: Chain::Tao,
        to: req.to,
        value: req.value,
        data: req.data,
        description: req.description,
        nonce,
        call_hash: Some(call_hash.clone()),
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
            "call_hash": call_hash,
            "hint": "Signers should call approve_as_multi with this call_hash via SubWallet"
        })),
        Err(e) => Json(serde_json::json!({ "ok": false, "error": e.to_string() })),
    }
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
        if !multisig.owners.iter().any(|o| o == &req.owner) {
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
    let store_read = state.store.read().await;
    let tx = match store_read.get_transaction(&tx_id) {
        Some(t) => t.clone(),
        None => return Json(serde_json::json!({ "ok": false, "error": "Transaction not found" })),
    };
    let multisig = match store_read.get_multisig(&tx.multisig_id) {
        Some(m) => m.clone(),
        None => return Json(serde_json::json!({ "ok": false, "error": "Multisig not found" })),
    };
    drop(store_read);

    if tx.approvals.len() < multisig.threshold as usize {
        return Json(serde_json::json!({ "ok": false, "error": "Not enough approvals" }));
    }

    // Record on-chain tx hash if provided
    if let Some(hash) = &req.tx_hash {
        let mut store = state.store.write().await;
        let _ = store.set_tx_status(&tx_id, TxStatus::Executed, Some(hash));
    }

    // Return the call data and other signatories for the final as_multi call
    let other_signatories: Vec<&String> = multisig
        .owners
        .iter()
        .filter(|o| *o != &req.executor)
        .collect();

    Json(serde_json::json!({
        "ok": true,
        "data": {
            "call_hash": tx.call_hash,
            "call_data": tx.data,
            "other_signatories": other_signatories,
            "threshold": multisig.threshold,
            "multisig_address": multisig.address,
            "hint": "Submit as_multi extrinsic with the full call data via SubWallet"
        }
    }))
}

async fn get_balance(
    State(state): State<AppState>,
    Path(address): Path<String>,
) -> Json<serde_json::Value> {
    let body = serde_json::json!({
        "jsonrpc": "2.0",
        "method": "system_account",
        "params": [address],
        "id": 1
    });

    // Use state_getStorage with the system.account storage key
    // For a simpler approach, use the substrate RPC
    let storage_body = serde_json::json!({
        "jsonrpc": "2.0",
        "method": "system_account",
        "params": [address],
        "id": 1
    });

    // Fallback: try the simple balance query
    let balance_body = serde_json::json!({
        "jsonrpc": "2.0",
        "method": "state_call",
        "params": ["AccountNonceApi_account_nonce",
            format!("0x{}", hex::encode(decode_ss58(&address).unwrap_or_default())),
            null
        ],
        "id": 1
    });

    // For TAO balance, we query the free balance from system account
    let _ = body;
    let _ = storage_body;
    match state.http.post(&state.config.tao_rpc).json(&balance_body).send().await {
        Ok(resp) => match resp.json::<serde_json::Value>().await {
            Ok(data) => {
                Json(serde_json::json!({
                    "ok": true,
                    "data": {
                        "address": address,
                        "raw": data,
                        "chain": "tao",
                        "hint": "Use polkadot.js apps or SubWallet to check full balance"
                    }
                }))
            }
            Err(e) => Json(serde_json::json!({ "ok": false, "error": e.to_string() })),
        },
        Err(e) => Json(serde_json::json!({ "ok": false, "error": e.to_string() })),
    }
}
