mod vali;
mod consensus;
mod circuit;
mod store;

use axum::{
    Router,
    routing::{get, post},
    extract::{State as AxumState, Path},
    Json,
};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::sync::Arc;
use tokio::sync::Mutex;
use tower_http::cors::CorsLayer;

use circuit::gate::{Circuit, Gate};
use circuit::proof::Proof;
use store::Store;
use vali::validator::ValidatorInfo;

type AppState = Arc<Mutex<Store>>;

// -- request / response types --

#[derive(Deserialize)]
struct PutReq {
    key: String,
    value: String,
}

#[derive(Serialize)]
struct PutRes {
    ok: bool,
    block: Option<u64>,
    hash: Option<String>,
    error: Option<String>,
}

#[derive(Deserialize)]
struct AddValidatorReq {
    name: String,
    passphrase: Option<String>,
}

#[derive(Serialize)]
struct AddValidatorRes {
    name: String,
    pub_key: String,
    priv_key: String,
}

#[derive(Serialize)]
struct InfoRes {
    height: u64,
    validators: usize,
    quorum: usize,
    keys: Vec<String>,
    pending: usize,
    circuits: Vec<String>,
}

#[derive(Serialize)]
struct GetRes {
    key: String,
    value: Option<String>,
}

#[derive(Deserialize)]
struct CircuitReq {
    name: String,
    inputs: Vec<String>,
    outputs: Vec<String>,
    gates: Vec<Gate>,
}

#[derive(Deserialize)]
struct ProveReq {
    inputs: HashMap<String, String>,
}

#[derive(Serialize)]
struct ProveRes {
    ok: bool,
    outputs: Option<HashMap<String, String>>,
    proof: Option<Proof>,
    error: Option<String>,
}

#[derive(Deserialize)]
struct VerifyReq {
    name: Option<String>,
    circuit: Option<Circuit>,
    proof: Proof,
}

#[derive(Serialize)]
struct VerifyRes {
    valid: bool,
    error: Option<String>,
}

// -- handlers: store --

async fn handle_put(
    AxumState(state): AxumState<AppState>,
    Json(req): Json<PutReq>,
) -> Json<PutRes> {
    let mut store = state.lock().await;
    store.put(req.key, req.value);
    match store.commit() {
        Ok(Some(block)) => Json(PutRes {
            ok: true,
            block: Some(block.index),
            hash: Some(hex::encode(&block.hash()[..8])),
            error: None,
        }),
        Ok(None) => Json(PutRes {
            ok: false,
            block: None,
            hash: None,
            error: Some("nothing to commit".into()),
        }),
        Err(e) => Json(PutRes {
            ok: false,
            block: None,
            hash: None,
            error: Some(e),
        }),
    }
}

async fn handle_get(
    AxumState(state): AxumState<AppState>,
    Path(key): Path<String>,
) -> Json<GetRes> {
    let store = state.lock().await;
    Json(GetRes {
        key: key.clone(),
        value: store.get(&key),
    })
}

async fn handle_info(AxumState(state): AxumState<AppState>) -> Json<InfoRes> {
    let store = state.lock().await;
    Json(InfoRes {
        height: store.height(),
        validators: store.vset.size(),
        quorum: store.vset.quorum(),
        keys: store.state.keys(),
        pending: store.pending_count(),
        circuits: store.list_circuits(),
    })
}

async fn handle_snapshot(
    AxumState(state): AxumState<AppState>,
) -> Json<HashMap<String, String>> {
    let store = state.lock().await;
    Json(store.snapshot())
}

// -- handlers: validators --

async fn handle_add_validator(
    AxumState(state): AxumState<AppState>,
    Json(req): Json<AddValidatorReq>,
) -> Json<AddValidatorRes> {
    let mut store = state.lock().await;
    let keys = store.add_validator(&req.name, req.passphrase.as_deref(), 8);
    Json(AddValidatorRes {
        name: req.name,
        pub_key: keys.pub_key,
        priv_key: keys.priv_key,
    })
}

async fn handle_validators(
    AxumState(state): AxumState<AppState>,
) -> Json<Vec<ValidatorInfo>> {
    let store = state.lock().await;
    let infos: Vec<_> = store.validators.values().map(|v| v.info()).collect();
    Json(infos)
}

// -- handlers: circuits --

async fn handle_register_circuit(
    AxumState(state): AxumState<AppState>,
    Json(req): Json<CircuitReq>,
) -> Json<serde_json::Value> {
    let circuit = Circuit {
        name: req.name.clone(),
        inputs: req.inputs,
        outputs: req.outputs,
        gates: req.gates,
    };
    let hash = circuit.hash();
    let mut store = state.lock().await;
    store.register_circuit(circuit);
    Json(serde_json::json!({
        "ok": true,
        "name": req.name,
        "circuit_hash": hash,
    }))
}

async fn handle_list_circuits(
    AxumState(state): AxumState<AppState>,
) -> Json<Vec<String>> {
    let store = state.lock().await;
    Json(store.list_circuits())
}

async fn handle_get_circuit(
    AxumState(state): AxumState<AppState>,
    Path(name): Path<String>,
) -> Json<serde_json::Value> {
    let store = state.lock().await;
    match store.get_circuit(&name) {
        Some(c) => Json(serde_json::to_value(c).unwrap()),
        None => Json(serde_json::json!({"error": "not found"})),
    }
}

async fn handle_prove(
    AxumState(state): AxumState<AppState>,
    Path(name): Path<String>,
    Json(req): Json<ProveReq>,
) -> Json<ProveRes> {
    let store = state.lock().await;
    match store.prove(&name, &req.inputs) {
        Ok((outputs, proof)) => Json(ProveRes {
            ok: true,
            outputs: Some(outputs),
            proof: Some(proof),
            error: None,
        }),
        Err(e) => Json(ProveRes {
            ok: false,
            outputs: None,
            proof: None,
            error: Some(e),
        }),
    }
}

async fn handle_verify(
    AxumState(state): AxumState<AppState>,
    Json(req): Json<VerifyReq>,
) -> Json<VerifyRes> {
    let store = state.lock().await;
    match store.verify_proof(req.name.as_deref(), req.circuit.as_ref(), &req.proof) {
        Ok(valid) => Json(VerifyRes {
            valid,
            error: None,
        }),
        Err(e) => Json(VerifyRes {
            valid: false,
            error: Some(e),
        }),
    }
}

// -- main --

#[tokio::main]
async fn main() {
    let port = std::env::var("PORT").unwrap_or_else(|_| "50100".to_string());
    let store = Arc::new(Mutex::new(Store::new()));

    let app = Router::new()
        // store
        .route("/put", post(handle_put))
        .route("/get/{key}", get(handle_get))
        .route("/info", get(handle_info))
        .route("/snapshot", get(handle_snapshot))
        // validators
        .route("/validator", post(handle_add_validator))
        .route("/validators", get(handle_validators))
        // circuits
        .route("/circuits", get(handle_list_circuits))
        .route("/circuits", post(handle_register_circuit))
        .route("/circuits/{name}", get(handle_get_circuit))
        .route("/circuits/{name}/prove", post(handle_prove))
        .route("/verify", post(handle_verify))
        // middleware
        .layer(CorsLayer::permissive())
        .with_state(store);

    let addr = format!("0.0.0.0:{}", port);
    println!("qbit api listening on {}", addr);
    let listener = tokio::net::TcpListener::bind(&addr).await.unwrap();
    axum::serve(listener, app).await.unwrap();
}
