use crate::records::{RecordRequest, RecordType as MyRecordType, DnsRecord, GossipMessage};
use crate::store::Store;
use axum::extract::{Path, State};
use axum::http::StatusCode;
use axum::response::Json;
use axum::routing::{delete, get, put};
use axum::Router;
use serde_json::{json, Value};
use std::net::SocketAddr;
use std::str::FromStr;
use std::sync::Arc;
use tokio::sync::mpsc;
use tower_http::cors::{Any, CorsLayer};
use tracing::info;

pub struct ApiState {
    pub store: Arc<Store>,
    pub gossip_tx: mpsc::Sender<GossipMessage>,
    pub zones: Vec<String>,
    pub peer_count: Arc<std::sync::atomic::AtomicUsize>,
}

pub async fn run(
    store: Arc<Store>,
    addr: SocketAddr,
    zones: Vec<String>,
    gossip_tx: mpsc::Sender<GossipMessage>,
    peer_count: Arc<std::sync::atomic::AtomicUsize>,
) -> anyhow::Result<()> {
    let state = Arc::new(ApiState {
        store,
        gossip_tx,
        zones,
        peer_count,
    });

    let cors = CorsLayer::new()
        .allow_origin(Any)
        .allow_methods(Any)
        .allow_headers(Any);

    let app = Router::new()
        .route("/health", get(health))
        .route("/zones", get(list_zones))
        .route("/zones/{zone}/records", get(list_records))
        .route("/zones/{zone}/records", put(upsert_record))
        .route("/zones/{zone}/records/{name}/{rtype}", get(get_record))
        .route("/zones/{zone}/records/{name}/{rtype}", delete(delete_record))
        .route("/peers", get(list_peers))
        .route("/stats", get(stats))
        .layer(cors)
        .with_state(state);

    info!(%addr, "HTTP API listening");
    let listener = tokio::net::TcpListener::bind(addr).await?;
    axum::serve(listener, app).await?;
    Ok(())
}

async fn health() -> Json<Value> {
    Json(json!({"status": "ok"}))
}

async fn list_zones(State(state): State<Arc<ApiState>>) -> Json<Value> {
    let zones = state.store.zones();
    let configured = &state.zones;
    // Merge configured + discovered zones
    let mut all: Vec<String> = configured.clone();
    for z in zones {
        if !all.contains(&z) {
            all.push(z);
        }
    }
    Json(json!({"zones": all}))
}

async fn list_records(
    State(state): State<Arc<ApiState>>,
    Path(zone): Path<String>,
) -> Result<Json<Value>, StatusCode> {
    match state.store.list(&zone) {
        Ok(records) => Ok(Json(json!({"zone": zone, "records": records}))),
        Err(_) => Err(StatusCode::INTERNAL_SERVER_ERROR),
    }
}

async fn get_record(
    State(state): State<Arc<ApiState>>,
    Path((zone, name, rtype)): Path<(String, String, String)>,
) -> Result<Json<Value>, StatusCode> {
    let rtype = MyRecordType::from_str(&rtype).map_err(|_| StatusCode::BAD_REQUEST)?;
    match state.store.get(&zone, &name, rtype) {
        Ok(Some(rec)) => Ok(Json(json!(rec))),
        Ok(None) => Err(StatusCode::NOT_FOUND),
        Err(_) => Err(StatusCode::INTERNAL_SERVER_ERROR),
    }
}

async fn upsert_record(
    State(state): State<Arc<ApiState>>,
    Path(zone): Path<String>,
    Json(req): Json<RecordRequest>,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    let record = DnsRecord::new(
        req.name,
        req.rtype,
        req.value,
        req.ttl,
        state.store.node_id.clone(),
    );

    match state.store.put(&zone, record) {
        Ok(stored) => {
            // Broadcast to peers
            let msg = GossipMessage {
                zone: zone.clone(),
                record: stored.clone(),
            };
            let _ = state.gossip_tx.send(msg).await;

            Ok(Json(json!({
                "status": "ok",
                "record": stored,
            })))
        }
        Err(e) => Err((
            StatusCode::BAD_REQUEST,
            Json(json!({"error": e})),
        )),
    }
}

async fn delete_record(
    State(state): State<Arc<ApiState>>,
    Path((zone, name, rtype)): Path<(String, String, String)>,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    let rtype = MyRecordType::from_str(&rtype)
        .map_err(|e| (StatusCode::BAD_REQUEST, Json(json!({"error": e}))))?;

    match state.store.delete(&zone, &name, rtype) {
        Ok(Some(deleted)) => {
            // Broadcast deletion to peers
            let msg = GossipMessage {
                zone: zone.clone(),
                record: deleted.clone(),
            };
            let _ = state.gossip_tx.send(msg).await;

            Ok(Json(json!({"status": "deleted", "record": deleted})))
        }
        Ok(None) => Err((
            StatusCode::NOT_FOUND,
            Json(json!({"error": "record not found"})),
        )),
        Err(e) => Err((
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({"error": e})),
        )),
    }
}

async fn list_peers(State(state): State<Arc<ApiState>>) -> Json<Value> {
    let count = state
        .peer_count
        .load(std::sync::atomic::Ordering::Relaxed);
    Json(json!({
        "peer_count": count,
        "node_id": state.store.node_id,
    }))
}

async fn stats(State(state): State<Arc<ApiState>>) -> Json<Value> {
    let mut s = state.store.stats();
    let peer_count = state
        .peer_count
        .load(std::sync::atomic::Ordering::Relaxed);
    s.insert(
        "peers".into(),
        serde_json::Value::Number(peer_count.into()),
    );
    Json(json!(s))
}
