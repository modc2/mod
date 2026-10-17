//! dydx-api — the tradekit/1 REST + MCP skeleton, copied from the
//! hyperliquid framework (orbit/hyperliquid/src/api).
//!
//! Every canonical mod fn has a route here. The venue-specific part is the
//! `venue` module: replace its bodies with real calls to dYdX's API.
//! The live engine is DRY RUN only until a real order path is written —
//! `auto_execute: true` is refused, never silently downgraded.

mod venue;

use axum::{
    extract::{Path, Query, State},
    http::StatusCode,
    response::{IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use parking_lot::RwLock;
use serde::Deserialize;
use serde_json::{json, Value};
use std::{collections::HashMap, sync::Arc};
use tower_http::cors::CorsLayer;

const NAME: &str = "dydx";
const TOOL_PREFIX: &str = "dydx_";

#[derive(Default)]
struct AppState {
    /// eoa → session config (dry run).
    sessions: RwLock<HashMap<String, Value>>,
}
type Shared = Arc<AppState>;

fn err(code: StatusCode, msg: impl Into<String>) -> Response {
    (code, Json(json!({ "error": msg.into() }))).into_response()
}

async fn health() -> Json<Value> {
    Json(json!({ "ok": true, "module": NAME }))
}

async fn status(State(s): State<Shared>) -> Json<Value> {
    Json(json!({
        "ok": true, "module": NAME, "spec": "tradekit/1", "venue": venue::VENUE,
        "sessions": s.sessions.read().len(),
    }))
}

#[derive(Deserialize)]
struct ListQ { limit: Option<usize>, days: Option<u32>, sort: Option<String> }

async fn markets(Query(q): Query<ListQ>) -> Response {
    match venue::markets(q.limit.unwrap_or(50)).await {
        Ok(v) => Json(v).into_response(),
        Err(e) => err(StatusCode::BAD_GATEWAY, e),
    }
}

async fn traders(Query(q): Query<ListQ>) -> Response {
    match venue::top_traders(q.days.unwrap_or(7), q.limit.unwrap_or(50), q.sort.as_deref().unwrap_or("pnl")).await {
        Ok(v) => Json(v).into_response(),
        Err(e) => err(StatusCode::BAD_GATEWAY, e),
    }
}

async fn trader(Path(addr): Path<String>, Query(q): Query<ListQ>) -> Response {
    match venue::analyze_trader(&addr, q.days.unwrap_or(7)).await {
        Ok(v) => Json(v).into_response(),
        Err(e) => err(StatusCode::BAD_GATEWAY, e),
    }
}

async fn strats_backtest() -> Json<Value> {
    Json(json!({ "results": [], "note": "backtest not wired for this venue yet" }))
}

async fn live_start(State(s): State<Shared>, Json(body): Json<Value>) -> Response {
    let Some(eoa) = body.get("eoa").and_then(Value::as_str).map(str::to_lowercase) else {
        return err(StatusCode::BAD_REQUEST, "eoa required");
    };
    if body.get("auto_execute").and_then(Value::as_bool) == Some(true) {
        return err(StatusCode::BAD_REQUEST,
            "real execution is not implemented for this venue yet — start without auto_execute (dry run)");
    }
    let mut cfg = body.clone();
    cfg["auto_execute"] = json!(false);
    s.sessions.write().insert(eoa.clone(), cfg.clone());
    Json(json!({ "ok": true, "mode": "dry_run", "eoa": eoa, "config": cfg })).into_response()
}

async fn live_stop(State(s): State<Shared>, Json(body): Json<Value>) -> Json<Value> {
    let eoa = body.get("eoa").and_then(Value::as_str).unwrap_or("").to_lowercase();
    let stopped = s.sessions.write().remove(&eoa).is_some();
    Json(json!({ "ok": true, "stopped": stopped }))
}

#[derive(Deserialize)]
struct EoaQ { eoa: Option<String> }

async fn live_status(State(s): State<Shared>, Query(q): Query<EoaQ>) -> Json<Value> {
    let eoa = q.eoa.unwrap_or_default().to_lowercase();
    let cfg = s.sessions.read().get(&eoa).cloned();
    Json(json!({ "eoa": eoa, "running": cfg.is_some(), "config": cfg }))
}

// ── MCP: one tool per canonical fn, same table for schema + dispatch ──

/// (tool suffix, fn, method, path, description)
const TOOLS: &[(&str, &str, &str, &str, &str)] = &[
    ("status", "status", "GET", "/status", "Is the module up?"),
    ("markets", "markets", "GET", "/markets", "Tradable markets on dYdX."),
    ("top_traders", "top_traders", "GET", "/traders", "Leaderboard over `days`."),
    ("analyze_trader", "analyze_trader", "GET", "/traders/{address}", "One trader's stats."),
    ("live_status", "live_status", "GET", "/live/status", "A wallet's copy session."),
];

async fn mcp_schema() -> Json<Value> {
    let tools: Vec<Value> = TOOLS.iter().map(|(t, f, m, p, d)| json!({
        "name": format!("{TOOL_PREFIX}{t}"), "fn": f, "method": m, "path": p,
        "public": true, "description": d,
    })).collect();
    Json(json!({ "module": NAME, "tools": tools }))
}

async fn mcp(State(s): State<Shared>, Json(msg): Json<Value>) -> Json<Value> {
    let id = msg.get("id").cloned().unwrap_or(Value::Null);
    let method = msg.get("method").and_then(Value::as_str).unwrap_or("");
    let reply = |result: Value| Json(json!({ "jsonrpc": "2.0", "id": id, "result": result }));
    match method {
        "initialize" => reply(json!({
            "protocolVersion": "2025-06-18",
            "serverInfo": { "name": NAME, "version": env!("CARGO_PKG_VERSION") },
            "capabilities": { "tools": {} },
        })),
        "tools/list" => reply(json!({ "tools": TOOLS.iter().map(|(t, _, _, _, d)| json!({
            "name": format!("{TOOL_PREFIX}{t}"), "description": d,
            "inputSchema": { "type": "object" },
        })).collect::<Vec<_>>() })),
        "tools/call" => {
            let p = msg.get("params").cloned().unwrap_or(json!({}));
            let name = p.get("name").and_then(Value::as_str).unwrap_or("");
            let a = p.get("arguments").cloned().unwrap_or(json!({}));
            let num = |k: &str, d: u64| a.get(k).and_then(Value::as_u64).unwrap_or(d);
            let out: Result<Value, String> = match name.strip_prefix(TOOL_PREFIX).unwrap_or("") {
                "status" => Ok(status(State(s.clone())).await.0),
                "markets" => venue::markets(num("limit", 50) as usize).await,
                "top_traders" => venue::top_traders(num("days", 7) as u32, num("limit", 50) as usize,
                    a.get("sort").and_then(Value::as_str).unwrap_or("pnl")).await,
                "analyze_trader" => match a.get("address").and_then(Value::as_str) {
                    Some(addr) => venue::analyze_trader(addr, num("days", 7) as u32).await,
                    None => Err("address required".into()),
                },
                "live_status" => Ok(live_status(State(s.clone()), Query(EoaQ {
                    eoa: a.get("eoa").and_then(Value::as_str).map(String::from) })).await.0),
                _ => Err(format!("unknown tool {name}")),
            };
            match out {
                Ok(v) => reply(json!({ "content": [{ "type": "text", "text": v.to_string() }],
                                       "structuredContent": v })),
                Err(e) => reply(json!({ "content": [{ "type": "text", "text": e }], "isError": true })),
            }
        }
        _ => Json(json!({ "jsonrpc": "2.0", "id": id,
                          "error": { "code": -32601, "message": format!("unknown method {method}") } })),
    }
}

#[tokio::main]
async fn main() {
    let port: u16 = std::env::var("PORT").ok().and_then(|p| p.parse().ok()).unwrap_or(51442);
    let state: Shared = Arc::new(AppState::default());
    let app = Router::new()
        .route("/health", get(health))
        .route("/status", get(status))
        .route("/markets", get(markets))
        .route("/traders", get(traders))
        .route("/traders/:address", get(trader))
        .route("/strats/backtest", get(strats_backtest))
        .route("/live/start", post(live_start))
        .route("/live/stop", post(live_stop))
        .route("/live/status", get(live_status))
        .route("/mcp/schema", get(mcp_schema))
        .route("/mcp", post(mcp))
        .layer(CorsLayer::permissive())
        .with_state(state);
    let listener = tokio::net::TcpListener::bind(("127.0.0.1", port)).await.expect("bind");
    println!("[{NAME}] api on :{port}");
    axum::serve(listener, app).await.expect("serve");
}
