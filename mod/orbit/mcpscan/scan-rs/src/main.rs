//! mcpscan — the internet-wide MCP index.
//!
//! One binary, three faces:
//!   REST on :50700       the index, the crawl reports, the scraper's state
//!   MCP  at POST /mcp    six tools that search and call anything indexed
//!   console on :50701    a single page over both
//!
//! The crawl/probe/hunt loops start with the process and never stop.

mod face;
mod index;
mod prober;
mod sources;
mod store;
mod upstream;

use axum::extract::{Path, Query, State};
use axum::http::{HeaderMap, StatusCode};
use axum::response::{Html, IntoResponse};
use axum::routing::{get, post};
use axum::{Json, Router};
use index::{Index, SearchParams};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::sync::Arc;
use tower_http::cors::CorsLayer;

type App = Arc<Index>;

fn port() -> u16 {
    std::env::var("MCPSCAN_PORT").ok().and_then(|p| p.parse().ok()).unwrap_or(50700)
}

fn console_port() -> u16 {
    std::env::var("MCPSCAN_APP_PORT").ok().and_then(|p| p.parse().ok()).unwrap_or(50701)
}

fn public_url() -> String {
    std::env::var("MCPSCAN_PUBLIC_URL").unwrap_or_else(|_| format!("http://localhost:{}", port()))
}

#[tokio::main]
async fn main() {
    let ix = Index::load();
    println!("mcpscan: {} servers in the index", ix.len().await);

    if std::env::args().any(|a| a == "--stdio") {
        face::run_stdio(ix).await;
        return;
    }
    // One-shot crawl for scripts and first-run seeding: `mcpscan-api --crawl`.
    if std::env::args().any(|a| a == "--crawl") {
        let reports = prober::crawl(&ix, None).await;
        println!("{}", serde_json::to_string_pretty(&reports).unwrap_or_default());
        return;
    }

    if ix.len().await == 0 {
        // Empty index: seed it now rather than waiting for the crawl clock.
        let seed = ix.clone();
        tokio::spawn(async move {
            println!("mcpscan: empty index — seeding from every directory");
            prober::crawl(&seed, None).await;
        });
    }
    prober::spawn_loops(ix.clone());

    let api = Router::new()
        .route("/", get(info))
        .route("/health", get(health))
        .route("/stats", get(stats))
        .route("/catalog", get(catalog))
        .route("/catalog/:id", get(one))
        .route("/catalog/:id/probe", post(probe_one))
        .route("/recent", get(recent))
        .route("/sources", get(source_list))
        .route("/crawl", post(crawl))
        .route("/hunt", post(hunt))
        .route("/probe", post(probe_url))
        .route("/call", post(call))
        .route("/export", get(export))
        .route("/client_config", get(client_config))
        .route("/mcp", post(mcp_http).get(mcp_get))
        .layer(CorsLayer::very_permissive())
        .with_state(ix.clone());

    // The console is its own listener because the router proxies {host}/mcpscan
    // to app_port with the prefix intact — every path there returns the page.
    let console = Router::new().fallback(get(console_page));
    tokio::spawn(async move {
        let addr = format!("0.0.0.0:{}", console_port());
        match tokio::net::TcpListener::bind(&addr).await {
            Ok(l) => {
                println!("mcpscan console on {addr}");
                let _ = axum::serve(l, console).await;
            }
            Err(e) => eprintln!("console bind failed: {e}"),
        }
    });

    let addr = format!("0.0.0.0:{}", port());
    println!("mcpscan api on {addr}");
    let listener = tokio::net::TcpListener::bind(&addr).await.expect("bind");
    axum::serve(listener, api).await.expect("serve");
}

// ── gate ─────────────────────────────────────────────────────────────

/// Reads are open. Kicking a crawl is work this box pays for, so it needs the
/// secret when one has been provisioned.
fn gated(headers: &HeaderMap) -> Result<(), (StatusCode, Json<Value>)> {
    let Some(secret) = store::secret() else { return Ok(()) };
    let token = headers
        .get("authorization")
        .and_then(|v| v.to_str().ok())
        .and_then(|v| v.strip_prefix("Bearer ").or_else(|| v.strip_prefix("bearer ")))
        .unwrap_or("")
        .trim()
        .to_string();
    if token == secret {
        Ok(())
    } else {
        Err(err(StatusCode::UNAUTHORIZED, "needs the hub secret as `Authorization: Bearer …`"))
    }
}

fn err(status: StatusCode, msg: impl Into<String>) -> (StatusCode, Json<Value>) {
    (status, Json(json!({ "error": msg.into() })))
}

fn headers_of(body: &Value) -> HashMap<String, String> {
    body.get("headers")
        .and_then(|h| h.as_object())
        .map(|o| {
            o.iter().filter_map(|(k, v)| v.as_str().map(|s| (k.clone(), s.to_string()))).collect()
        })
        .unwrap_or_default()
}

// ── reads ────────────────────────────────────────────────────────────

async fn info(State(ix): State<App>) -> Json<Value> {
    Json(json!({
        "name": "mcpscan",
        "title": "MCP Scan",
        "version": face::SERVER_VERSION,
        "description": "An index of every MCP server on the internet: crawls the public directories, probes every endpoint it finds on a loop, hunts for endpoints nobody published, and re-exposes the whole thing as one MCP server you can search and call through.",
        "servers": ix.len().await,
        "mcp_endpoint": format!("{}/mcp", public_url()),
        "sources": sources::ALL,
        "endpoints": {
            "GET /catalog": "search the index — ?q=&status=live|auth|down|error|unknown&source=&sort=relevance|tools|recent|fast|name&limit=&offset=&tools=1",
            "GET /catalog/:id": "one server, full tool list",
            "POST /catalog/:id/probe": "re-probe one server now",
            "GET /stats": "index size, status split, per-directory counts, scraper telemetry",
            "GET /recent": "the scraper's live feed — servers whose status just changed",
            "GET /sources": "per-directory crawl reports",
            "POST /crawl": "{source?} run the crawl now (gated when a secret exists)",
            "POST /hunt": "{budget?} knock on domains that never published an endpoint",
            "POST /probe": "{url, headers?} handshake with any endpoint and index it",
            "POST /call": "{server, tool, args, headers?} call a tool on any indexed server",
            "GET /export": "?status=live&format=json|ndjson|mcp — the index, or a client config of every live server",
            "POST /mcp": "the index as an MCP server (mcp_find, mcp_server, mcp_tools, mcp_call, mcp_probe, mcp_stats)"
        }
    }))
}

async fn health(State(ix): State<App>) -> Json<Value> {
    Json(json!({ "ok": true, "servers": ix.len().await, "started_at": ix.started_at }))
}

async fn stats(State(ix): State<App>) -> Json<Value> {
    Json(ix.stats().await)
}

async fn catalog(State(ix): State<App>, Query(q): Query<HashMap<String, String>>) -> Json<Value> {
    let p = SearchParams::from_query(&q);
    let (total, rows) = ix.search(&p).await;
    Json(json!({ "total": total, "count": rows.len(), "offset": p.offset, "servers": rows }))
}

async fn one(State(ix): State<App>, Path(id): Path<String>) -> impl IntoResponse {
    match ix.get(&id).await {
        Some(e) => Ok(Json(e.row(true))),
        None => Err(err(StatusCode::NOT_FOUND, format!("no indexed server `{id}`"))),
    }
}

async fn recent(State(ix): State<App>, Query(q): Query<HashMap<String, String>>) -> Json<Value> {
    let n = q.get("limit").and_then(|v| v.parse().ok()).unwrap_or(25usize).min(40);
    Json(json!({ "events": ix.recent(n).await }))
}

async fn source_list(State(ix): State<App>) -> Json<Value> {
    let reports = ix.reports.read().await.clone();
    Json(json!({ "sources": sources::ALL, "reports": reports }))
}

async fn export(
    State(ix): State<App>,
    Query(q): Query<HashMap<String, String>>,
) -> impl IntoResponse {
    let mut p = SearchParams::from_query(&q);
    p.limit = q.get("limit").and_then(|v| v.parse().ok()).unwrap_or(5000).min(50_000);
    let (_, rows) = ix.search(&p).await;
    match q.get("format").map(String::as_str).unwrap_or("json") {
        // A paste-ready mcpServers block of everything currently live.
        "mcp" | "config" => {
            let mut servers = serde_json::Map::new();
            for r in &rows {
                let (Some(id), Some(url)) = (r.get("id").and_then(|v| v.as_str()), r.get("url").and_then(|v| v.as_str()))
                else {
                    continue;
                };
                if url.is_empty() {
                    continue;
                }
                servers.insert(id.to_string(), json!({ "transport": "http", "url": url }));
            }
            Json(json!({ "mcpServers": servers })).into_response()
        }
        "ndjson" => {
            let body: String =
                rows.iter().map(|r| format!("{r}\n")).collect::<Vec<_>>().concat();
            ([("content-type", "application/x-ndjson")], body).into_response()
        }
        _ => Json(json!({ "servers": rows })).into_response(),
    }
}

async fn client_config(Query(q): Query<HashMap<String, String>>) -> Json<Value> {
    let url = format!("{}/mcp", public_url());
    let client = q.get("client").map(String::as_str).unwrap_or("json");
    let config = match client {
        "claude" | "cli" => json!({ "command": format!("claude mcp add mcpscan --transport http {url}") }),
        "cursor" | "vscode" => json!({ "mcpServers": { "mcpscan": { "url": url, "type": "http" } } }),
        _ => json!({ "mcpServers": { "mcpscan": { "transport": "http", "url": url } } }),
    };
    Json(json!({ "client": client, "url": url, "config": config }))
}

// ── writes / work ────────────────────────────────────────────────────

async fn crawl(
    State(ix): State<App>,
    headers: HeaderMap,
    body: Option<Json<Value>>,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    gated(&headers)?;
    let body = body.map(|Json(b)| b).unwrap_or(json!({}));
    let source = body.get("source").and_then(|v| v.as_str()).map(String::from);
    if let Some(s) = &source {
        if !sources::ALL.contains(&s.as_str()) {
            return Err(err(StatusCode::BAD_REQUEST, format!("unknown source `{s}`")));
        }
    }
    // A full crawl is minutes of work; answer immediately and let it run.
    if body.get("wait").and_then(|v| v.as_bool()) == Some(true) {
        let reports = prober::crawl(&ix, source.as_deref()).await;
        return Ok(Json(json!({ "reports": reports })));
    }
    let running = source.clone();
    tokio::spawn(async move {
        prober::crawl(&ix, running.as_deref()).await;
    });
    Ok(Json(json!({ "started": true, "source": source, "note": "watch GET /sources" })))
}

async fn hunt(
    State(ix): State<App>,
    headers: HeaderMap,
    body: Option<Json<Value>>,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    gated(&headers)?;
    let budget = body
        .and_then(|Json(b)| b.get("budget").and_then(|v| v.as_u64()))
        .unwrap_or(12)
        .clamp(1, 200) as usize;
    let hits = prober::hunt_cycle(&ix, budget).await;
    Ok(Json(json!({ "knocked": budget, "found": hits })))
}

async fn probe_url(
    State(ix): State<App>,
    Json(body): Json<Value>,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    let url = body
        .get("url")
        .and_then(|v| v.as_str())
        .ok_or_else(|| err(StatusCode::BAD_REQUEST, "`url` is required"))?
        .trim()
        .to_string();
    if !url.starts_with("http://") && !url.starts_with("https://") {
        return Err(err(StatusCode::BAD_REQUEST, "`url` must be http(s)"));
    }
    let (id, out) = prober::probe_url(&ix, &url, &headers_of(&body), "probe").await;
    Ok(Json(json!({
        "id": id, "url": url, "status": out.status, "error": out.error,
        "protocolVersion": out.protocol_version, "serverInfo": out.server_info,
        "latency_ms": out.latency_ms, "toolCount": out.raw_tools.len(), "tools": out.raw_tools,
    })))
}

async fn probe_one(
    State(ix): State<App>,
    Path(id): Path<String>,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    let e = ix.get(&id).await.ok_or_else(|| err(StatusCode::NOT_FOUND, format!("no server `{id}`")))?;
    if !e.probeable() {
        return Err(err(StatusCode::BAD_REQUEST, format!("`{id}` has no HTTP endpoint")));
    }
    let out = upstream::probe(&e.url, &HashMap::new(), 20).await;
    ix.apply_probe(&id, &out).await;
    Ok(Json(json!({
        "id": id, "url": e.url, "status": out.status, "error": out.error,
        "latency_ms": out.latency_ms, "toolCount": out.raw_tools.len(),
    })))
}

async fn call(
    State(ix): State<App>,
    Json(body): Json<Value>,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    let args = json!({
        "server": body.get("server").cloned().unwrap_or(Value::Null),
        "tool": body.get("tool").cloned().unwrap_or(Value::Null),
        "args": body.get("args").or_else(|| body.get("arguments")).cloned().unwrap_or(json!({})),
        "headers": body.get("headers").cloned().unwrap_or(json!({})),
    });
    match face::call_tool(&ix, "mcp_call", &args).await {
        Ok(v) => Ok(Json(json!({ "result": v }))),
        Err(e) => Err(err(StatusCode::BAD_GATEWAY, e)),
    }
}

// ── the MCP face ─────────────────────────────────────────────────────

async fn mcp_http(State(ix): State<App>, Json(msg): Json<Value>) -> impl IntoResponse {
    if let Some(batch) = msg.as_array() {
        let mut replies = Vec::new();
        for m in batch {
            if let Some(r) = face::handle_message(&ix, m).await {
                replies.push(r);
            }
        }
        return Json(json!(replies)).into_response();
    }
    match face::handle_message(&ix, &msg).await {
        Some(reply) => Json(reply).into_response(),
        None => StatusCode::ACCEPTED.into_response(),
    }
}

async fn mcp_get() -> Json<Value> {
    Json(json!({
        "protocol": "MCP (JSON-RPC 2.0 over Streamable HTTP)",
        "hint": "POST JSON-RPC here: initialize → tools/list → tools/call.",
        "tools": face::tools().iter().filter_map(|t| t.get("name").cloned()).collect::<Vec<_>>(),
    }))
}

async fn console_page() -> Html<&'static str> {
    Html(include_str!("console.html"))
}
