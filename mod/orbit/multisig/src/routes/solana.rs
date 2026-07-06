use axum::{
    extract::{Path, State},
    routing::{get, post},
    Json, Router,
};

use crate::types::*;
use crate::AppState;

/// Squads V4 program ID on mainnet
const SQUADS_V4_PROGRAM: &str = "SQDS4ep65T869zMMBKyuUq6aD6EgTu8psMjkvj52pCf";

pub fn router() -> Router<AppState> {
    Router::new()
        .route("/api/solana/multisig", post(create_multisig))
        .route("/api/solana/tx/propose", post(propose_tx))
        .route("/api/solana/tx/:id/approve", post(approve_tx))
        .route("/api/solana/tx/:id/execute", post(execute_tx))
        .route("/api/solana/balance/:address", get(get_balance))
        .route("/api/solana/account/:address", get(get_account_info))
}

// --- Handlers ---

async fn create_multisig(
    State(state): State<AppState>,
    Json(req): Json<CreateMultisigReq>,
) -> Json<serde_json::Value> {
    if req.threshold == 0 || req.threshold as usize > req.owners.len() {
        return Json(serde_json::json!({ "ok": false, "error": "Invalid threshold" }));
    }

    // Validate Solana addresses (base58, 32-44 chars)
    for owner in &req.owners {
        if bs58::decode(owner).into_vec().is_err() {
            return Json(serde_json::json!({
                "ok": false,
                "error": format!("Invalid Solana address: {}", owner)
            }));
        }
    }

    let wallet = MultisigWallet {
        id: uuid::Uuid::new_v4().to_string(),
        chain: Chain::Solana,
        name: req.name,
        owners: req.owners,
        threshold: req.threshold,
        address: req.address,
        safe_version: None,
        created_at: chrono::Utc::now().to_rfc3339(),
    };

    let mut store = state.store.write().await;
    match store.add_multisig(wallet) {
        Ok(w) => Json(serde_json::json!({
            "ok": true,
            "data": w,
            "squads_program": SQUADS_V4_PROGRAM,
            "hint": "For on-chain multisig, create a Squads vault at app.squads.so or use the Squads SDK. Set the address field after creation."
        })),
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
    if multisig.chain != Chain::Solana {
        return Json(serde_json::json!({ "ok": false, "error": "Not a Solana multisig" }));
    }
    if !multisig.owners.iter().any(|o| o == &req.proposer) {
        return Json(serde_json::json!({ "ok": false, "error": "Not an owner" }));
    }

    let pending = store_read.list_pending_transactions(&req.multisig_id);
    let nonce = pending.len() as u64;
    drop(store_read);

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
        chain: Chain::Solana,
        to: req.to,
        value: req.value,
        data: req.data,
        description: req.description,
        nonce,
        call_hash: None,
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
            "hint": "Other owners should approve this transaction. Once threshold is met, any owner can execute."
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

    // Build the Solana transfer instruction data for the frontend
    let lamports: u64 = tx.value.parse().unwrap_or(0);

    Json(serde_json::json!({
        "ok": true,
        "data": {
            "tx": tx,
            "multisig_address": multisig.address,
            "to": tx.to,
            "lamports": lamports,
            "instruction_data": tx.data,
            "approvals": tx.approvals.len(),
            "threshold": multisig.threshold,
            "hint": "Construct and sign the Solana transaction via Phantom wallet"
        }
    }))
}

async fn get_balance(
    State(state): State<AppState>,
    Path(address): Path<String>,
) -> Json<serde_json::Value> {
    let body = serde_json::json!({
        "jsonrpc": "2.0",
        "method": "getBalance",
        "params": [address],
        "id": 1
    });
    match state.http.post(&state.config.solana_rpc).json(&body).send().await {
        Ok(resp) => match resp.json::<serde_json::Value>().await {
            Ok(data) => {
                let lamports = data["result"]["value"].as_u64().unwrap_or(0);
                Json(serde_json::json!({
                    "ok": true,
                    "data": {
                        "address": address,
                        "balance_lamports": lamports,
                        "balance_sol": format!("{:.9}", lamports as f64 / 1e9),
                        "chain": "solana",
                    }
                }))
            }
            Err(e) => Json(serde_json::json!({ "ok": false, "error": e.to_string() })),
        },
        Err(e) => Json(serde_json::json!({ "ok": false, "error": e.to_string() })),
    }
}

async fn get_account_info(
    State(state): State<AppState>,
    Path(address): Path<String>,
) -> Json<serde_json::Value> {
    let body = serde_json::json!({
        "jsonrpc": "2.0",
        "method": "getAccountInfo",
        "params": [address, { "encoding": "jsonParsed" }],
        "id": 1
    });
    match state.http.post(&state.config.solana_rpc).json(&body).send().await {
        Ok(resp) => match resp.json::<serde_json::Value>().await {
            Ok(data) => Json(serde_json::json!({ "ok": true, "data": data["result"]["value"] })),
            Err(e) => Json(serde_json::json!({ "ok": false, "error": e.to_string() })),
        },
        Err(e) => Json(serde_json::json!({ "ok": false, "error": e.to_string() })),
    }
}
