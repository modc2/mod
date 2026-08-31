//! HTTP surface: MCP Streamable HTTP at /mcp, one more MCP server per stored
//! module at /m/<name>/mcp, REST adapters that dispatch through the same tool
//! layer, the blob and wasm endpoints the execution layer fetches modules
//! from, and the console.
//!
//! Behind the fleet router the API is reachable at /api/arena (prefix
//! stripped) and the console at /arena (prefix kept), so both are served here
//! and one console works in both places.

use crate::{arena, hostcard, mcp, mcpout, modmcp, rustc, store, storelink, vibe};
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
const RUNTIME: [(&str, &str); 9] = [
    ("host.mjs", include_str!("../../runtime/host.mjs")),
    ("abi.mjs", include_str!("../../runtime/abi.mjs")),
    ("match.mjs", include_str!("../../runtime/match.mjs")),
    ("worker.mjs", include_str!("../../runtime/worker.mjs")),
    ("mcpsync.mjs", include_str!("../../runtime/mcpsync.mjs")),
    ("pyhost.mjs", include_str!("../../runtime/pyhost.mjs")),
    ("syncfetch.mjs", include_str!("../../runtime/syncfetch.mjs")),
    ("host.py", include_str!("../../runtime/host.py")),
    // Not imported by anything in a tab — served so the console can show what
    // a Rust class is actually compiled against, which is the only honest way
    // to document a prelude.
    ("prelude.rs", include_str!("../../rustclass/prelude.rs")),
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
        "blob": "GET /blob/:id — the module bytes, immutable (the id is their hash)",
        "inspect": "POST /inspect {bytes|text}",
        "players": "GET /players | POST /players | GET /players/:id | DELETE /players/:id",
        "play": "POST /play {player, view, seat} — one move from a server-driven player",
        "matches": "GET /matches | POST /matches (record one) | GET /matches/:id",
        "run": "POST /run {game, players[]} — play one headlessly via the node runner",
        "leaderboard": "GET /leaderboard?game=",
        "abi": "GET /abi?role=game&lang=wasm|class — the contract a module implements",
        "docs": "GET /docs — the contents | GET /docs/:slug (?format=md) | GET /docs/search?q=",
        "runtime": "GET /runtime/host.mjs — the execution layer itself, host.py included",
        "forward": "POST /forward {action, ...args}",
        "tools": "GET /tools",
        "store": "GET /store — the bridge to the store module | POST /store/sync {force?, verify?}",
        "host": "GET /host — the box running this arena: its key, uptime, store and toolchain",
        "fleet": "GET /fleet — every module of this fleet a player can be seated on | GET /fleet/:name/tools",
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

/// The bytes. Content-addressed, so this can be cached forever — the id
/// changing *is* the invalidation.
/// The wasm a module runs as. For a Rust class this compiles it, which is
/// slow exactly once — the artefact is cached under the id, and the id is the
/// hash of the source, so the cache cannot go stale.
async fn wasm(Path(id): Path<String>) -> Response {
    // A compile is CPU work with a process in it; off the async runtime it
    // goes, or one slow class stalls every other request on this server.
    let out = tokio::task::spawn_blocking(move || arena::compiled(&id))
        .await
        .unwrap_or_else(|e| Err(format!("the compile task failed: {e}")));
    match out {
        Ok((id, bytes)) => (
            [
                (header::CONTENT_TYPE, "application/wasm".to_string()),
                (header::CACHE_CONTROL, "public, max-age=31536000, immutable".to_string()),
                (header::ETAG, format!("\"{id}\"")),
            ],
            Body::from(bytes),
        )
            .into_response(),
        Err(e) => (StatusCode::BAD_REQUEST, Json(json!({ "error": e }))).into_response(),
    }
}

/// The outward door, as an endpoint. Both sandboxes end here — the wasm one
/// through a spawned synchronous fetch, the python one through its line
/// protocol — and so can anything else that would rather have this arena make
/// a call than make it itself.
async fn mcp_call(Json(body): Json<Value>) -> Response {
    Json(mcpout::call(&body).await).into_response()
}

async fn mcp_servers() -> Json<Value> {
    Json(mcpout::list())
}

/// One module's own MCP server. Same JSON-RPC, a different and much smaller
/// world: this game, or this agent, and nothing else in the arena.
async fn module_mcp(Path(name): Path<String>, Json(msg): Json<Value>) -> Response {
    match modmcp::handle_message(&name, &msg).await {
        Some(reply) => Json(reply).into_response(),
        None => StatusCode::ACCEPTED.into_response(),
    }
}

async fn module_card(Path(name): Path<String>) -> Response {
    match modmcp::call_tool(&name, "about", &json!({})).await {
        Ok(v) => Json(v).into_response(),
        Err(e) => (StatusCode::NOT_FOUND, Json(json!({ "error": e }))).into_response(),
    }
}

async fn module_tools(Path(name): Path<String>) -> Response {
    match modmcp::module_of(&name) {
        Ok(m) => Json(json!({
            "module": m.name, "role": m.role,
            "mcp": format!("{}/m/{}/mcp", mcp::base(), m.name),
            "tools": modmcp::tools_for(&m.role),
        }))
        .into_response(),
        Err(e) => (StatusCode::NOT_FOUND, Json(json!({ "error": e }))).into_response(),
    }
}

/// Every module, with the endpoint and the mod name each one answers to. The
/// index for the whole "one module, one server, one mod" idea.
async fn servers() -> Json<Value> {
    let base = mcp::base();
    let list = store::read(|s| {
        s.module_list()
            .into_iter()
            .map(|m| {
                json!({
                    "name": m.name,
                    "role": m.role,
                    "lang": m.lang(),
                    "id": m.short(),
                    "description": m.description,
                    "mod": format!("arena.{}", m.name),
                    "mcp": format!("{base}/m/{}/mcp", m.name),
                    "tools": modmcp::tools_for(&m.role).as_array()
                        .map(|a| a.iter().filter_map(|t| t["name"].as_str())
                            .collect::<Vec<_>>()).unwrap_or_default(),
                })
            })
            .collect::<Vec<_>>()
    });
    Json(json!({
        "count": list.len(),
        "arena": format!("{base}/mcp"),
        "servers": list,
        "how": "every stored module is also an MCP server of its own and a mod of its own \
                under orbit/arena/mods/. `m arena/mint` writes the directories; the servers \
                are here whether or not anybody minted anything.",
    }))
}

async fn toolchain() -> Json<Value> {
    Json(rustc::toolchain())
}

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

/// The documentation. `GET /docs` is the contents, `/docs/search?q=` finds a
/// section, and `/docs/:slug` is one page — as JSON by default, as the raw
/// markdown with `?format=md` or an `Accept: text/markdown`, which is what
/// makes it readable by curl as well as by the console.
async fn docs_index() -> Response {
    Json(crate::docs::index()).into_response()
}

async fn docs_search(Query(q): Query<HashMap<String, String>>) -> Response {
    via_tool("docs_search", json!(q)).await
}

async fn docs_page(
    Path(slug): Path<String>,
    Query(q): Query<HashMap<String, String>>,
    req: Request,
) -> Response {
    let page = match crate::docs::page(&json!({ "slug": slug })) {
        Ok(v) => v,
        Err(e) => return (StatusCode::NOT_FOUND, Json(json!({ "error": e }))).into_response(),
    };
    let raw = q.get("format").map(|f| f == "md" || f == "markdown").unwrap_or(false)
        || req
            .headers()
            .get(header::ACCEPT)
            .and_then(|v| v.to_str().ok())
            .map(|a| a.contains("text/markdown") || a.contains("text/plain"))
            .unwrap_or(false);
    if raw {
        let body = page["markdown"].as_str().unwrap_or_default().to_string();
        return ([(header::CONTENT_TYPE, "text/markdown; charset=utf-8")], body).into_response();
    }
    Json(page).into_response()
}

async fn plant() -> Response {
    via_tool("plant_examples", json!({})).await
}

async fn store_status() -> Response {
    via_tool("store_status", json!({})).await
}

async fn store_sync(body: Option<Json<Value>>) -> Response {
    via_tool("store_sync", body.map(|Json(v)| v).unwrap_or_else(|| json!({}))).await
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

/// Who is running this arena — the box, its key, its uptime, what it can
/// build, and where its bytes went. `?store=0` skips the round trip to the
/// store module.
async fn host(Query(q): Query<HashMap<String, String>>) -> Json<Value> {
    let with_store = q.get("store").map(|v| v != "0" && v != "false").unwrap_or(true);
    Json(hostcard::card(with_store).await)
}

/// The fleet, as somewhere a player could sit. Every module the MCP hub knows
/// about, every module the activator can wake, and the servers a class here
/// may call out to — each addressed through the gateway, which is what makes
/// naming a module enough.
async fn fleet() -> Json<Value> {
    Json(mcpout::fleet().await)
}

/// What one module of the fleet offers, so a seat can be filled by picking a
/// tool rather than by knowing one. Also the argument each tool wants the
/// position in — the console shows it, the mcp driver infers the same thing.
async fn fleet_tools(Path(name): Path<String>) -> Response {
    let server = match mcpout::resolve(None, Some(&name), None) {
        Ok(s) => s,
        Err(e) => return (StatusCode::BAD_REQUEST, Json(json!({ "error": e }))).into_response(),
    };
    match mcpout::tools_of(&server).await {
        Ok(tools) => Json(json!({
            "module": name, "mcp": server.url, "count": tools.len(), "tools": tools,
        }))
        .into_response(),
        Err(e) => (
            StatusCode::BAD_GATEWAY,
            Json(json!({ "error": e, "module": name, "mcp": server.url })),
        )
            .into_response(),
    }
}

// ── vibe ─────────────────────────────────────────────────────────────────
//
// Writing a game or a player with the build agent. A session is a file the
// agent edits a sentence at a time; storing it is an upload of the text, so
// the registry, not the session, says what it became. An error that begins
// with `build:` is the agent being unreachable, which is the caller's
// dependency failing rather than the caller's request being wrong — 424, and
// never a 5xx, which a proxy in front would replace with a bare code.

fn vibe_response(out: Result<Value, String>) -> Response {
    match out {
        Ok(v) => Json(v).into_response(),
        Err(e) if e.starts_with("build:") => (StatusCode::FAILED_DEPENDENCY, Json(json!({ "error": e }))).into_response(),
        Err(e) if e.starts_with("no vibe session") || e.starts_with("no module") => {
            (StatusCode::NOT_FOUND, Json(json!({ "error": e }))).into_response()
        }
        Err(e) => (StatusCode::BAD_REQUEST, Json(json!({ "error": e }))).into_response(),
    }
}

async fn vibe_list() -> Response {
    let mut v = vibe::list();
    v["build"] = vibe::availability().await;
    Json(v).into_response()
}

async fn vibe_start(Json(body): Json<Value>) -> Response {
    vibe_response(vibe::vibe(&body).await)
}

async fn vibe_get(Path(id): Path<String>) -> Response {
    vibe_response(vibe::get(&id).await)
}

async fn vibe_delete(Path(id): Path<String>) -> Response {
    vibe_response(vibe::delete(&id))
}

async fn vibe_store(Path(id): Path<String>, Json(body): Json<Value>) -> Response {
    let mut args = body;
    args["session"] = json!(id);
    vibe_response(vibe::store(&args).await)
}

async fn vibe_cancel(Path(id): Path<String>) -> Response {
    vibe_response(vibe::cancel(&id).await)
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
        .route("/wasm/:id", get(wasm))
        .route("/toolchain", get(toolchain))
        .route("/vibe", get(vibe_list).post(vibe_start))
        .route("/vibe/:id", get(vibe_get).delete(vibe_delete))
        .route("/vibe/:id/store", post(vibe_store))
        .route("/vibe/:id/cancel", post(vibe_cancel))
        .route("/store", get(store_status))
        .route("/store/sync", post(store_sync))
        .route("/mcp/call", post(mcp_call))
        .route("/mcp/servers", get(mcp_servers))
        .route("/servers", get(servers))
        .route("/host", get(host))
        .route("/fleet", get(fleet))
        .route("/fleet/:name/tools", get(fleet_tools))
        .route("/m/:name", get(module_card))
        .route("/m/:name/tools", get(module_tools))
        .route(
            "/m/:name/mcp",
            post(module_mcp).get(|Path(name): Path<String>| async move {
                (
                    StatusCode::METHOD_NOT_ALLOWED,
                    Json(json!({
                        "error": format!("POST JSON-RPC messages to /m/{name}/mcp"),
                        "tools": format!("GET /m/{name}/tools to see what it offers"),
                    })),
                )
            }),
        )
        .route("/docs", get(docs_index))
        .route("/docs/search", get(docs_search))
        .route("/docs/:slug", get(docs_page))
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
    hostcard::mark_start();
    mcp::set_base(format!("http://127.0.0.1:{port}"));
    let planted = arena::plant_examples();
    println!(
        "arena: {} example module(s) in the registry",
        planted["planted"].as_u64().unwrap_or(0)
    );
    // From here on an upload pushes itself to the store; what was planted
    // before now, and anything older without a cid, goes in one pass.
    storelink::backfill_later();

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
