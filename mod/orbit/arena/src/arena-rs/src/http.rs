//! HTTP surface: MCP Streamable HTTP at /mcp, REST adapters that dispatch
//! through the same MCP tool layer, the blob endpoint the execution layer
//! fetches modules from, and the console.
//!
//! Behind the fleet router the API is reachable at /api/arena (prefix
//! stripped) and the console at /arena (prefix kept), so both are served here
//! and one console works in both places.

use crate::{arena, mcp};
use axum::{
    body::Body,
    extract::{Path, Query, Request},
    http::{header, StatusCode},
    response::{Html, IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use serde_json::{json, Value};
use std::collections::HashMap;
use tower_http::cors::CorsLayer;

const CONSOLE_HTML: &str = include_str!("console.html");

/// The execution layer, served to the browser from the same binary that
/// stores the modules — so a tab needs nothing but this port.
const RUNTIME: [(&str, &str); 4] = [
    ("host.mjs", include_str!("../../runtime/host.mjs")),
    ("abi.mjs", include_str!("../../runtime/abi.mjs")),
    ("match.mjs", include_str!("../../runtime/match.mjs")),
    ("worker.mjs", include_str!("../../runtime/worker.mjs")),
];

fn info() -> Value {
    let mut v = arena::info();
    v["version"] = json!(mcp::SERVER_VERSION);
    v["backend"] = json!("rust-mcp");
    v["mcp_protocol"] = json!(mcp::PROTOCOL_VERSION);
    v["endpoints"] = json!({
        "mcp": "POST /mcp (Streamable HTTP, JSON-RPC 2.0)",
        "modules": "GET /modules | POST /modules | GET /modules/:id | DELETE /modules/:id",
        "blob": "GET /blob/:id — the module bytes, immutable (the id is their hash)",
        "inspect": "POST /inspect {bytes}",
        "players": "GET /players | POST /players | GET /players/:id | DELETE /players/:id",
        "play": "POST /play {player, view, seat} — one move from a server-driven player",
        "matches": "GET /matches | POST /matches (record one) | GET /matches/:id",
        "run": "POST /run {game, players[]} — play one headlessly via the node runner",
        "leaderboard": "GET /leaderboard?game=",
        "abi": "GET /abi?role=game — the contract a module implements",
        "runtime": "GET /runtime/host.mjs — the execution layer itself",
        "forward": "POST /forward {action, ...args}",
        "tools": "GET /tools",
        "console": "GET /arena (browser)"
    });
    v["stdio"] = json!("arena-api --stdio");
    v
}

async fn root(req: Request) -> Response {
    let wants_html = req
        .headers()
        .get(header::ACCEPT)
        .and_then(|v| v.to_str().ok())
        .map(|a| a.contains("text/html"))
        .unwrap_or(false);
    if wants_html {
        Html(CONSOLE_HTML).into_response()
    } else {
        Json(info()).into_response()
    }
}

async fn console() -> Html<&'static str> {
    Html(CONSOLE_HTML)
}

async fn mcp_endpoint(Json(msg): Json<Value>) -> Response {
    match mcp::handle_message(&msg).await {
        Some(resp) => Json(resp).into_response(),
        None => StatusCode::ACCEPTED.into_response(),
    }
}

/// REST → MCP tool adapter: one definition of every capability, two doors.
async fn via_tool(name: &str, args: Value) -> Response {
    match mcp::call_tool(name, &args).await {
        Ok(v) => Json(v).into_response(),
        Err(e) => {
            let status = if e.starts_with("no module") || e.starts_with("no player") || e.starts_with("no match") || e.starts_with("no game") {
                StatusCode::NOT_FOUND
            } else {
                StatusCode::BAD_REQUEST
            };
            (status, Json(json!({ "error": e }))).into_response()
        }
    }
}

// ── modules ──────────────────────────────────────────────────────────────

async fn list_modules(Query(q): Query<HashMap<String, String>>) -> Response {
    via_tool("list_modules", json!(q)).await
}

async fn put_module(Json(body): Json<Value>) -> Response {
    via_tool("put_module", body).await
}

async fn get_module(Path(id): Path<String>) -> Response {
    via_tool("get_module", json!({ "module": id })).await
}

async fn delete_module(Path(id): Path<String>) -> Response {
    via_tool("delete_module", json!({ "module": id })).await
}

async fn inspect(Json(body): Json<Value>) -> Response {
    via_tool("inspect_module", body).await
}

/// The bytes. Content-addressed, so this can be cached forever — the id
/// changing *is* the invalidation.
async fn blob(Path(id): Path<String>) -> Response {
    match arena::module_bytes(&id) {
        Ok((id, bytes)) => (
            [
                (header::CONTENT_TYPE, "application/wasm".to_string()),
                (header::CACHE_CONTROL, "public, max-age=31536000, immutable".to_string()),
                (header::ETAG, format!("\"{id}\"")),
            ],
            Body::from(bytes),
        )
            .into_response(),
        Err(e) => (StatusCode::NOT_FOUND, Json(json!({ "error": e }))).into_response(),
    }
}

// ── players and matches ──────────────────────────────────────────────────

async fn list_players(Query(q): Query<HashMap<String, String>>) -> Response {
    via_tool("list_players", json!(q)).await
}

async fn enter_player(Json(body): Json<Value>) -> Response {
    via_tool("enter_player", body).await
}

async fn get_player(Path(id): Path<String>) -> Response {
    via_tool("get_player", json!({ "player": id })).await
}

async fn remove_player(Path(id): Path<String>) -> Response {
    via_tool("remove_player", json!({ "player": id })).await
}

async fn play(Json(body): Json<Value>) -> Response {
    via_tool("play_move", body).await
}

async fn list_matches(Query(q): Query<HashMap<String, String>>) -> Response {
    let mut args = json!(q);
    if let Some(l) = q.get("limit").and_then(|v| v.parse::<u64>().ok()) {
        args["limit"] = json!(l);
    }
    via_tool("list_matches", args).await
}

async fn record_match(Json(body): Json<Value>) -> Response {
    via_tool("record_match", body).await
}

async fn get_match(Path(id): Path<String>) -> Response {
    via_tool("get_match", json!({ "id": id })).await
}

async fn run(Json(body): Json<Value>) -> Response {
    via_tool("run_match", body).await
}

async fn leaderboard(Query(q): Query<HashMap<String, String>>) -> Response {
    let mut args = json!(q);
    if let Some(l) = q.get("limit").and_then(|v| v.parse::<u64>().ok()) {
        args["limit"] = json!(l);
    }
    via_tool("leaderboard", args).await
}

async fn abi(Query(q): Query<HashMap<String, String>>) -> Response {
    via_tool("game_abi", json!(q)).await
}

async fn plant() -> Response {
    via_tool("plant_examples", json!({})).await
}

/// Generic escape hatch — any tool by name, the same shape as the mod
/// protocol's `forward`.
async fn forward(Json(mut body): Json<Value>) -> Response {
    let action = body
        .as_object_mut()
        .and_then(|o| o.remove("action").or_else(|| o.remove("fn")))
        .and_then(|a| a.as_str().map(String::from))
        .unwrap_or_else(|| "arena_info".into());
    via_tool(&action, body).await
}

async fn tools() -> Json<Value> {
    Json(json!({ "tools": mcp::tool_list() }))
}

async fn health() -> Json<Value> {
    Json(info())
}

async fn runtime_file(Path(name): Path<String>) -> Response {
    match RUNTIME.iter().find(|(n, _)| *n == name) {
        Some((_, body)) => (
            [(header::CONTENT_TYPE, "text/javascript; charset=utf-8")],
            *body,
        )
            .into_response(),
        None => (
            StatusCode::NOT_FOUND,
            Json(json!({ "error": format!("no runtime file `{name}`"),
                         "available": RUNTIME.iter().map(|(n, _)| *n).collect::<Vec<_>>() })),
        )
            .into_response(),
    }
}

fn api_routes() -> Router {
    Router::new()
        .route("/info", get(health))
        .route("/health", get(health))
        .route(
            "/mcp",
            post(mcp_endpoint).get(|| async {
                (
                    StatusCode::METHOD_NOT_ALLOWED,
                    Json(json!({ "error": "POST JSON-RPC messages to /mcp" })),
                )
            }),
        )
        .route("/modules", get(list_modules).post(put_module))
        .route("/modules/:id", get(get_module).delete(delete_module))
        .route("/blob/:id", get(blob))
        .route("/inspect", post(inspect))
        .route("/players", get(list_players).post(enter_player))
        .route("/players/:id", get(get_player).delete(remove_player))
        .route("/play", post(play))
        .route("/matches", get(list_matches).post(record_match))
        .route("/matches/:id", get(get_match))
        .route("/run", post(run))
        .route("/leaderboard", get(leaderboard))
        .route("/abi", get(abi))
        .route("/examples", post(plant))
        .route("/runtime/:name", get(runtime_file))
        .route("/forward", post(forward))
        .route("/tools", get(tools))
}

pub async fn serve(port: u16) {
    mcp::set_base(format!("http://127.0.0.1:{port}"));
    let planted = arena::plant_examples();
    println!(
        "arena: {} example module(s) in the registry",
        planted["planted"].as_u64().unwrap_or(0)
    );

    let app = Router::new()
        .route("/", get(root))
        .route("/arena", get(console))
        .route("/arena/", get(console))
        .merge(api_routes())
        // The console lives at /arena in both worlds and always calls
        // /api/arena. Behind the fleet router caddy strips that prefix;
        // standalone on this port nothing does, so the same routes answer
        // there too and one console works in both places.
        .nest("/api/arena", api_routes())
        .layer(CorsLayer::permissive());

    let addr = std::net::SocketAddr::from(([0, 0, 0, 0], port));
    println!("arena listening on {addr} (MCP at /mcp, console at /arena)");
    let listener = tokio::net::TcpListener::bind(addr).await.expect("bind");
    axum::serve(listener, app).await.expect("serve");
}
