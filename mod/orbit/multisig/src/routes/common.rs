use axum::{
    extract::{Path, Query, State},
    routing::get,
    Json, Router,
};
use serde::Deserialize;

use crate::types::*;
use crate::AppState;

pub fn router() -> Router<AppState> {
    Router::new()
        .route("/api/health", get(health))
        .route("/api/multisigs", get(list_multisigs))
        .route("/api/multisigs/:id", get(get_multisig).delete(delete_multisig))
        .route("/api/multisigs/:id/txs", get(list_txs))
        .route("/api/tx/:id", get(get_tx))
}

async fn health() -> Json<serde_json::Value> {
    Json(serde_json::json!({
        "ok": true,
        "service": "multisig",
        "chains": ["base", "tao", "solana"]
    }))
}

#[derive(Debug, Deserialize)]
struct ListQuery {
    chain: Option<String>,
}

async fn list_multisigs(
    State(state): State<AppState>,
    Query(q): Query<ListQuery>,
) -> Json<serde_json::Value> {
    let store = state.store.read().await;
    let chain = q.chain.as_deref().and_then(|c| match c {
        "base" => Some(Chain::Base),
        "tao" => Some(Chain::Tao),
        "solana" => Some(Chain::Solana),
        _ => None,
    });
    let wallets = store.list_multisigs(chain.as_ref());
    Json(serde_json::json!({ "ok": true, "data": wallets }))
}

async fn get_multisig(
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Json<serde_json::Value> {
    let store = state.store.read().await;
    match store.get_multisig(&id) {
        Some(m) => {
            let txs = store.list_transactions(&id);
            Json(serde_json::json!({ "ok": true, "data": { "multisig": m, "transactions": txs } }))
        }
        None => Json(serde_json::json!({ "ok": false, "error": "Not found" })),
    }
}

async fn delete_multisig(
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Json<serde_json::Value> {
    let mut store = state.store.write().await;
    match store.delete_multisig(&id) {
        Ok(true) => Json(serde_json::json!({ "ok": true })),
        Ok(false) => Json(serde_json::json!({ "ok": false, "error": "Not found" })),
        Err(e) => Json(serde_json::json!({ "ok": false, "error": e.to_string() })),
    }
}

async fn list_txs(
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Json<serde_json::Value> {
    let store = state.store.read().await;
    let txs = store.list_transactions(&id);
    Json(serde_json::json!({ "ok": true, "data": txs }))
}

async fn get_tx(
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Json<serde_json::Value> {
    let store = state.store.read().await;
    match store.get_transaction(&id) {
        Some(tx) => Json(serde_json::json!({ "ok": true, "data": tx })),
        None => Json(serde_json::json!({ "ok": false, "error": "Not found" })),
    }
}
