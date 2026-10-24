//! HTTP surface: MCP Streamable HTTP at /mcp, REST adapters that dispatch
//! through the same MCP tool layer, the blob endpoint the execution layer
//! fetches modules from, and the console.
//!
//! Behind the fleet router the API is reachable at /api/modarena (prefix
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
/// stores the modules — so a tab needs nothing but this port. `pyhost.mjs` and
/// `host.py` are here to be read rather than imported: a tab cannot start a
/// python process, and serving them is how the console can show what the
/// sandbox a class runs in actually does.
const RUNTIME: [(&str, &str); 6] = [
    ("host.mjs", include_str!("../../runtime/host.mjs")),
    ("abi.mjs", include_str!("../../runtime/abi.mjs")),
    ("match.mjs", include_str!("../../runtime/match.mjs")),
    ("worker.mjs", include_str!("../../runtime/worker.mjs")),
    ("pyhost.mjs", include_str!("../../runtime/pyhost.mjs")),
    ("host.py", include_str!("../../runtime/host.py")),
];

fn info() -> Value {
    let mut v = arena::info();
    v["version"] = json!(mcp::SERVER_VERSION);
    v["backend"] = json!("rust-mcp");
    v["mcp_protocol"] = json!(mcp::PROTOCOL_VERSION);
    v["endpoints"] = json!({
        "mcp": "POST /mcp (Streamable HTTP, JSON-RPC 2.0)",
        "modules": "GET /modules | POST /modules | GET /modules/:id | DELETE /modules/:id",
        "classes": "GET /classes — the Python classes | POST /classes {source} — upload one as text",
        "blob": "GET /blob/:id — the anchor bytes of a mod, immutable",
        "folder": "GET /mods/:id/files — the whole folder | GET /mods/:id/file/<path> — one file",
        "template": "GET /template?kind=game&lang=python — the folder a new mod starts as",
        "verify": "POST /verify {files} — every check the registry runs, without storing",
        "generate": "POST /generate {prompt, kind, lang} — the agent writes one and it is verified before it is stored",
        "agent": "GET /agent — whether there is an agent here to ask",
        "per_mod_mcp": "POST /m/<name>/mcp — one MCP server per stored mod",
        "inspect": "POST /inspect {bytes|text}",
        "players": "GET /players | POST /players | GET /players/:id | DELETE /players/:id",
        "play": "POST /play {player, view, seat} — one move from a server-driven player",
        "matches": "GET /matches | POST /matches (record one) | GET /matches/:id",
        "run": "POST /run {game, players[]} — play one headlessly via the node runner",
        "leaderboard": "GET /leaderboard?game=",
        "abi": "GET /abi?role=game&lang=wasm|class — the contract a module implements",
        "runtime": "GET /runtime/host.mjs — the execution layer itself, host.py included",
        "forward": "POST /forward {action, ...args}",
        "tools": "GET /tools",
        "console": "GET /modarena (browser)"
    });
    v["stdio"] = json!("modarena-api --stdio");
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

/// The class half of the registry. Exactly `/modules?lang=python`, named for
/// what people come looking for.
async fn list_classes(Query(q): Query<HashMap<String, String>>) -> Response {
    let mut args = json!(q);
    args["lang"] = json!("python");
    via_tool("list_modules", args).await
}

async fn put_module(Json(body): Json<Value>) -> Response {
    via_tool("put_module", body).await
}

/// A class, as text — the same store, without making anyone base64 a file
/// they are looking at in an editor.
async fn put_class(Json(body): Json<Value>) -> Response {
    via_tool("put_class", body).await
}

async fn get_module(Path(id): Path<String>, Query(q): Query<HashMap<String, String>>) -> Response {
    let with_source = q.get("source").map(|v| v != "0" && v != "false").unwrap_or(true);
    via_tool("get_module", json!({ "module": id, "source": with_source })).await
}

async fn delete_module(Path(id): Path<String>) -> Response {
    via_tool("delete_module", json!({ "module": id })).await
}

async fn inspect(Json(body): Json<Value>) -> Response {
    via_tool("inspect_module", body).await
}

// ── mod folders ──────────────────────────────────────────────────────────

/// The folder a new mod starts as.
async fn template(Query(q): Query<HashMap<String, String>>) -> Response {
    via_tool("mod_template", json!(q)).await
}

/// Every check the registry runs, run against a folder nobody has stored.
async fn verify(Json(body): Json<Value>) -> Response {
    via_tool("verify_mod", body).await
}

/// The whole folder of a stored mod.
async fn mod_files(Path(id): Path<String>) -> Response {
    via_tool("mod_files", json!({ "module": id })).await
}

/// One file out of a folder, as itself. `mod.py` reads as text in a browser;
/// `mod.wasm` comes back as wasm, which is what the execution layer wants.
async fn mod_file(Path((id, path)): Path<(String, String)>) -> Response {
    match arena::file_bytes(&id, &path) {
        Ok((blob, bytes)) => (
            [
                (header::CONTENT_TYPE, content_type(&path, &bytes).to_string()),
                (header::CACHE_CONTROL, "public, max-age=31536000, immutable".to_string()),
                (header::ETAG, format!("\"{blob}\"")),
            ],
            Body::from(bytes),
        )
            .into_response(),
        Err(e) => (StatusCode::NOT_FOUND, Json(json!({ "error": e }))).into_response(),
    }
}

fn content_type(path: &str, bytes: &[u8]) -> &'static str {
    if bytes.starts_with(b"\0asm") || path.ends_with(".wasm") {
        "application/wasm"
    } else if path.ends_with(".json") {
        "application/json"
    } else {
        "text/plain; charset=utf-8"
    }
}

/// Have the agent write one. Slow by nature — it is a model writing a game and
/// then fixing it — so nothing here is on a short timeout.
async fn generate(Json(body): Json<Value>) -> Response {
    via_tool("generate_mod", body).await
}

async fn agent_status() -> Response {
    via_tool("agent_status", json!({})).await
}

/// One MCP server per stored mod: a game you can open and take a turn at, an
/// agent you can ask for a move, scoped to that mod alone.
async fn mod_mcp(Path(name): Path<String>, Json(msg): Json<Value>) -> Response {
    match crate::modmcp::handle_message(&name, &msg).await {
        Some(resp) => Json(resp).into_response(),
        None => StatusCode::ACCEPTED.into_response(),
    }
}

/// The bytes. Content-addressed, so this can be cached forever — the id
/// changing *is* the invalidation.
async fn blob(Path(id): Path<String>) -> Response {
    match arena::module_bytes(&id) {
        Ok((id, bytes)) => (
            [
                (
                    header::CONTENT_TYPE,
                    // The bytes say which they are, the same way the registry
                    // decided what to call them in the first place.
                    if bytes.starts_with(b"\0asm") {
                        "application/wasm".to_string()
                    } else {
                        "text/plain; charset=utf-8".to_string()
                    },
                ),
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
        Some((n, body)) => (
            [(
                header::CONTENT_TYPE,
                if n.ends_with(".py") {
                    "text/plain; charset=utf-8"
                } else {
                    "text/javascript; charset=utf-8"
                },
            )],
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
        .route("/classes", get(list_classes).post(put_class))
        .route("/modules/:id", get(get_module).delete(delete_module))
        .route("/blob/:id", get(blob))
        .route("/mods/:id/files", get(mod_files))
        .route("/mods/:id/file/*path", get(mod_file))
        .route("/template", get(template))
        .route("/verify", post(verify))
        .route("/generate", post(generate))
        .route("/agent", get(agent_status))
        .route("/m/:name/mcp", post(mod_mcp))
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
        "modarena: {} example mod(s) in the registry",
        planted["planted"].as_u64().unwrap_or(0)
    );

    let app = Router::new()
        .route("/", get(root))
        .route("/modarena", get(console))
        .route("/modarena/", get(console))
        .merge(api_routes())
        // The console lives at /arena in both worlds and always calls
        // /api/modarena. Behind the fleet router caddy strips that prefix;
        // standalone on this port nothing does, so the same routes answer
        // there too and one console works in both places.
        .nest("/api/modarena", api_routes())
        .layer(CorsLayer::permissive());

    let addr = std::net::SocketAddr::from(([0, 0, 0, 0], port));
    println!("modarena listening on {addr} (MCP at /mcp, console at /modarena)");
    let listener = tokio::net::TcpListener::bind(addr).await.expect("bind");
    axum::serve(listener, app).await.expect("serve");
}
