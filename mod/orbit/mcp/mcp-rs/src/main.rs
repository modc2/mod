//! MCP Hub API — one Rust binary, two faces:
//!   REST registry/console API on :50360 (mod protocol; GET / is the null call)
//!   MCP gateway at POST /mcp (and `--stdio`) aggregating every upstream tool.

mod auth;
mod catalog;
mod fleet;
mod hub;
mod hubs;
mod intake;
mod keys;
mod state;
mod stdio;
mod store;
mod upstream;
mod web;

use axum::extract::{Path, Query, State};
use axum::http::{HeaderMap, StatusCode};
use axum::response::IntoResponse;
use axum::routing::{delete, get, post};
use axum::{Json, Router};
use serde_json::{json, Value};
use state::AppState;
use std::collections::HashMap;
use std::sync::Arc;
use tower_http::cors::CorsLayer;

type App = Arc<AppState>;

fn port() -> u16 {
    store::port()
}

fn public_url() -> String {
    store::public_url()
}

#[tokio::main]
async fn main() {
    let app_state = AppState::load();

    if std::env::args().any(|a| a == "--stdio") {
        app_state.refresh_all().await;
        hub::run_stdio(app_state).await;
        return;
    }

    // Warm the probe cache in the background, then keep it fresh.
    {
        let st = app_state.clone();
        tokio::spawn(async move {
            let every = std::env::var("MCP_REFRESH_SECS")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(300u64);
            loop {
                st.refresh_all().await;
                tokio::time::sleep(std::time::Duration::from_secs(every)).await;
            }
        });
    }

    // The sweep is the other half of discovery: it knocks on every port the
    // fleet mentions, so a mod that serves MCP without declaring it is found
    // anyway. It costs a few hundred connection attempts, so it runs on its
    // own slower clock.
    {
        let st = app_state.clone();
        tokio::spawn(async move {
            let every = std::env::var("MCP_SWEEP_SECS")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(900u64);
            loop {
                let found = st.sweep().await;
                println!("sweep: {} undeclared MCP mod(s) — {}", found.len(), found.join(", "));
                tokio::time::sleep(std::time::Duration::from_secs(every)).await;
            }
        });
    }

    // The other hubs — peers, the index, the directories — are asked what
    // they hold on their own clock, so GET /hubs answers from a warm view.
    {
        let st = app_state.clone();
        tokio::spawn(async move {
            let every = std::env::var("MCP_HUBS_REFRESH_SECS")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(900u64);
            tokio::time::sleep(std::time::Duration::from_secs(20)).await;
            loop {
                let hubs = hubs::probe_all(&st, &public_url()).await;
                println!(
                    "hubs: {} known, {} ready",
                    hubs.len(),
                    hubs.iter().filter(|h| h.ready).count()
                );
                *st.hubs_cache.write().await = Some((store::now(), hubs));
                tokio::time::sleep(std::time::Duration::from_secs(every)).await;
            }
        });
    }

    let router = Router::new()
        .route("/", get(info))
        .route("/health", get(health))
        .route("/servers", get(servers).post(add_server))
        .route("/servers/:id", delete(remove_server).get(one_server))
        .route("/servers/:id/refresh", post(refresh_server))
        .route("/servers/:id/toggle", post(toggle_server))
        .route("/probe", post(adhoc_probe))
        .route("/intake", post(parse_intake))
        .route("/tools", get(tools))
        .route("/call", post(rest_call))
        .route("/search", get(web_search).post(web_search_post))
        .route("/fetch", get(web_fetch).post(web_fetch_post))
        .route("/catalog", get(catalog_search))
        .route("/hub", get(hub_manifest))
        .route("/hubs", get(list_hubs).post(add_hub))
        .route("/hubs/refresh", post(refresh_hubs))
        .route("/hubs/:id", get(one_hub).delete(remove_hub))
        .route("/hubs/:id/connect", post(connect_hub))
        .route("/client_config", get(client_config))
        .route("/stats", get(stats))
        .route("/refresh", post(refresh_all))
        .route("/discover", post(discover))
        .route("/auth/me", get(auth_me))
        .route("/auth/config", get(auth_config))
        .route("/keys", get(list_keys).post(create_key))
        .route("/keys/:id", delete(revoke_key))
        .route("/mcp", post(mcp_http).get(mcp_get))
        .layer(CorsLayer::very_permissive())
        .with_state(app_state);

    let addr = format!("0.0.0.0:{}", port());
    println!("mcp-hub api on {addr}");
    let listener = tokio::net::TcpListener::bind(&addr).await.expect("bind");
    axum::serve(listener, router).await.expect("serve");
}

/// Gate for registry edits — add, remove, toggle a server. An owner or a
/// whitelisted editor of the identity issuer passes, as does the legacy hub
/// secret; everyone else is refused with the address they actually presented,
/// because "wrong account active in the wallet" is the usual cause.
fn authorized(headers: &HeaderMap) -> Result<auth::Caller, (StatusCode, Json<Value>)> {
    let caller = auth::caller(headers);
    // A process already on this host can rewrite ~/.mod/mcp/hub.json by hand,
    // so refusing its HTTP request would be ceremony rather than security. The
    // gate is for callers arriving through the public gateway.
    if caller.can_write() || auth::local_request(headers) {
        return Ok(caller);
    }
    Err(unauthorized(&caller, "edit this hub's registry"))
}

/// Gate for executing a tool. Wider than the write gate — a hub API key is
/// enough, and so is being on this host — but closed to anonymous callers
/// arriving through the public gateway: an aggregated tool reaches whatever
/// its upstream can do.
fn may_call(headers: &HeaderMap) -> Result<auth::Caller, (StatusCode, Json<Value>)> {
    let caller = auth::caller(headers);
    if caller.can_call() || auth::local_request(headers) {
        return Ok(caller);
    }
    Err(unauthorized(&caller, "call tools through this hub"))
}

fn unauthorized(caller: &auth::Caller, action: &str) -> (StatusCode, Json<Value>) {
    let issuer = auth::issuer();
    let detail = match caller.address() {
        Some(addr) => format!(
            "signed in as {addr} ({}) — ask the owner for edit access",
            caller.role()
        ),
        None => format!("sign in with your wallet at /{issuer}, or send a hub API key as `Authorization: Bearer mcphub_…`"),
    };
    (
        StatusCode::UNAUTHORIZED,
        Json(json!({
            "error": format!("Not allowed to {action}: {detail}"),
            "role": caller.role(),
            "auth": auth::config_json(),
        })),
    )
}

fn err(status: StatusCode, msg: impl Into<String>) -> (StatusCode, Json<Value>) {
    (status, Json(json!({ "error": msg.into() })))
}

// ── identity ─────────────────────────────────────────────────────────

/// Who the hub thinks you are, and what that lets you do. The console calls
/// this on load with whatever session token the issuer left in localStorage.
async fn auth_me(headers: HeaderMap) -> Json<Value> {
    let caller = auth::caller(&headers);
    let mut me = caller.describe();
    me["local"] = json!(auth::local_request(&headers));
    me["can_call"] = json!(caller.can_call() || auth::local_request(&headers));
    Json(json!({ "me": me, "auth": auth::config_json() }))
}

async fn auth_config() -> Json<Value> {
    Json(auth::config_json())
}

/// The keys an MCP client can hold. Owner-only, both to read and to mint —
/// a key is a standing right to run every aggregated tool.
async fn list_keys(headers: HeaderMap) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    let caller = auth::caller(&headers);
    if !caller.is_owner() {
        return Err(unauthorized(&caller, "manage this hub's API keys"));
    }
    let keys: Vec<Value> = keys::list().iter().map(|k| k.public()).collect();
    Ok(Json(json!({ "keys": keys })))
}

async fn create_key(
    headers: HeaderMap,
    Json(body): Json<Value>,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    let caller = auth::caller(&headers);
    if !caller.is_owner() {
        return Err(unauthorized(&caller, "mint an API key for this hub"));
    }
    let name = body.get("name").and_then(|v| v.as_str()).unwrap_or("mcp client");
    let (rec, plaintext) = keys::create(name, caller.address().unwrap_or(caller.role()));
    Ok(Json(json!({
        "key": rec.public(),
        // The only time this value exists anywhere. Only the hash is stored.
        "secret": plaintext,
        "note": "Copy it now — the hub keeps only its hash.",
    })))
}

async fn revoke_key(
    headers: HeaderMap,
    Path(id): Path<String>,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    let caller = auth::caller(&headers);
    if !caller.is_owner() {
        return Err(unauthorized(&caller, "revoke an API key"));
    }
    if !keys::revoke(&id) {
        return Err(err(StatusCode::NOT_FOUND, format!("no key `{id}`")));
    }
    Ok(Json(json!({ "revoked": id })))
}

// ── read routes ──────────────────────────────────────────────────────

async fn info(State(st): State<App>) -> Json<Value> {
    let servers = st.all_servers().await;
    Json(json!({
        "name": "mcp",
        "title": "MCP Hub",
        "version": hub::SERVER_VERSION,
        "description": "Aggregates many MCP servers behind one registry and one gateway endpoint. Fleet mods are auto-discovered; anyone can register a remote server. POST /mcp speaks MCP itself — every upstream tool, namespaced server__tool.",
        "servers": servers.len(),
        "hub_type": "mod",
        "manifest": format!("{}/hub", public_url()),
        "mcp_endpoint": format!("{}/mcp", public_url()),
        "endpoints": {
            "GET /servers": "registry + live probe status per server",
            "POST /servers": "{url, id?, name?, headers?} — probe then register (write-gated)",
            "GET /servers/:id": "one server row",
            "DELETE /servers/:id": "unregister (user) or disable (fleet) (write-gated)",
            "POST /servers/:id/refresh": "re-probe now",
            "POST /servers/:id/toggle": "{enabled} include/exclude from aggregation (write-gated)",
            "POST /probe": "{url, headers?} ad-hoc probe, nothing saved",
            "POST /intake": "{text} — parse a URL, CID, client config, `claude mcp add` line or QR payload into candidate servers",
            "GET /tools": "aggregated namespaced tool list (?server= filters)",
            "POST /call": "{tool: 'server__tool', args} REST shortcut around tools/call",
            "GET|POST /search": "?q=&count=&provider= — search the web (no key required)",
            "GET|POST /fetch": "?url=&max_chars= — read one page as text",
            "GET /catalog": "?q=&registry=all|featured|official|smithery|glama|pulsemcp|<index>|<peer> — search for servers across every hub",
            "GET /hub": "this hub's manifest — what kind of hub it is (mod) and what it holds; what a peer reads",
            "GET /hubs": "every hub type this one can see: itself (mod), peer mod hubs, the fleet's index, the public directories — with counts (?refresh=1 re-probes)",
            "POST /hubs": "{url, id?, name?, headers?} — add a peer hub by URL (write-gated); it must answer /hub or /stats like a hub",
            "DELETE /hubs/:id": "forget a peer hub (write-gated)",
            "POST /hubs/:id/connect": "register a mod/index hub's MCP endpoint as one upstream server — its tools arrive nested (write-gated)",
            "GET /client_config": "?client=claude|cursor|vscode|json — paste-ready client config",
            "GET /stats": "hub totals",
            "POST /refresh": "re-scan fleet + re-probe everything",
            "POST /discover": "live sweep: knock on every fleet port and adopt whatever speaks MCP",
            "POST /mcp": "the hub as an MCP server (JSON-RPC 2.0 Streamable HTTP)"
        }
    }))
}

async fn health(State(st): State<App>) -> Json<Value> {
    let probes = st.probes.read().await;
    let up = probes.values().filter(|p| p.ok).count();
    Json(json!({ "ok": true, "servers_up": up, "started_at": st.started_at }))
}

async fn servers(State(st): State<App>) -> Json<Value> {
    Json(json!({ "servers": hub::servers_view(&st).await }))
}

async fn one_server(State(st): State<App>, Path(id): Path<String>) -> impl IntoResponse {
    let rows = hub::servers_view(&st).await;
    match rows.into_iter().find(|r| r.get("id").and_then(|i| i.as_str()) == Some(&id)) {
        Some(row) => Ok(Json(row)),
        None => Err(err(StatusCode::NOT_FOUND, format!("no server `{id}`"))),
    }
}

async fn tools(
    State(st): State<App>,
    Query(q): Query<HashMap<String, String>>,
) -> Json<Value> {
    let mut all = hub::aggregated_tools(&st).await;
    if let Some(server) = q.get("server") {
        let prefix = format!("{server}__");
        all.retain(|t| {
            t.get("name").and_then(|n| n.as_str()).map(|n| n.starts_with(&prefix)).unwrap_or(false)
        });
    }
    Json(json!({ "count": all.len(), "tools": all }))
}

async fn stats(State(st): State<App>) -> Json<Value> {
    let rows = hub::servers_view(&st).await;
    let up = rows
        .iter()
        .filter(|r| r["probe"]["ok"].as_bool().unwrap_or(false))
        .count();
    let by_source = rows.iter().fold(HashMap::<String, u64>::new(), |mut m, r| {
        let s = r["source"].as_str().unwrap_or("?").to_string();
        *m.entry(s).or_default() += 1;
        m
    });
    let tool_total: u64 = rows
        .iter()
        .map(|r| r["probe"]["toolCount"].as_u64().unwrap_or(0))
        .sum();
    Json(json!({
        "servers": rows.len(),
        "up": up,
        "down": rows.len() as u64 - up as u64,
        "tools": tool_total,
        "by_source": by_source,
        "write_gate": store::secret().is_some(),
        "swept_at": *st.swept_at.read().await,
        "hubs": match st.hubs_cache.read().await.as_ref() {
            Some((at, hubs)) => json!({
                "count": hubs.len(),
                "ready": hubs.iter().filter(|h| h.ready).count(),
                "checked_at": at,
            }),
            None => json!({ "count": hubs::known(&st, &public_url()).await.len(), "ready": null, "checked_at": 0 }),
        },
        "web": {
            "provider": web::providers()
                .into_iter()
                .find(|p| p["ready"].as_bool() == Some(true))
                .and_then(|p| p["name"].as_str().map(String::from)),
            "providers": web::providers(),
        },
        "auth": auth::config_json(),
    }))
}

// ── the web ──────────────────────────────────────────────────────────

async fn run_search(q: &HashMap<String, String>) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    let query = q.get("q").or_else(|| q.get("query")).map(String::as_str).unwrap_or("");
    let count = q.get("count").and_then(|c| c.parse().ok()).unwrap_or(8);
    let provider = q.get("provider").map(String::as_str).filter(|p| !p.is_empty());
    match web::search(query, count, provider).await {
        Ok(r) => Ok(Json(serde_json::to_value(r).unwrap_or_default())),
        // Only an unusable request lands here — a search that merely found
        // nothing comes back Ok with its `tried` trace. Never 5xx: Cloudflare
        // replaces a 502 body with its own text and the reason is lost.
        Err(e) => Err(err(StatusCode::BAD_REQUEST, e)),
    }
}

async fn web_search(
    Query(q): Query<HashMap<String, String>>,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    run_search(&q).await
}

async fn web_search_post(Json(body): Json<Value>) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    run_search(&flatten(&body)).await
}

async fn run_fetch(q: &HashMap<String, String>) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    let url = q
        .get("url")
        .ok_or_else(|| err(StatusCode::BAD_REQUEST, "`url` is required"))?;
    let max = q.get("max_chars").and_then(|c| c.parse().ok()).unwrap_or(8000);
    match web::fetch(url, max).await {
        Ok(p) => Ok(Json(serde_json::to_value(p).unwrap_or_default())),
        // 422, not 502, for the same reason: a 4xx body reaches the caller
        // intact through every proxy in front of us, and "that page wouldn't
        // read" is worth more than a bare status code.
        Err(e) => Err(err(StatusCode::UNPROCESSABLE_ENTITY, e)),
    }
}

async fn web_fetch(
    Query(q): Query<HashMap<String, String>>,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    run_fetch(&q).await
}

async fn web_fetch_post(Json(body): Json<Value>) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    run_fetch(&flatten(&body)).await
}

/// A JSON body as the same string map a query string produces, so GET and POST
/// share one implementation.
fn flatten(body: &Value) -> HashMap<String, String> {
    body.as_object()
        .map(|o| {
            o.iter()
                .map(|(k, v)| {
                    let s = match v {
                        Value::String(s) => s.clone(),
                        other => other.to_string(),
                    };
                    (k.clone(), s)
                })
                .collect()
        })
        .unwrap_or_default()
}

async fn catalog_search(
    State(st): State<App>,
    Query(q): Query<HashMap<String, String>>,
) -> Json<Value> {
    let query = q.get("q").or_else(|| q.get("query")).map(String::as_str).unwrap_or("");
    let registry = q.get("registry").or_else(|| q.get("hub")).map(String::as_str).unwrap_or("all");
    let limit = q.get("limit").and_then(|l| l.parse().ok()).unwrap_or(20);
    let cat = catalog::search(&st, query, registry, limit).await;
    Json(serde_json::to_value(cat).unwrap_or_default())
}

// ── hub types ────────────────────────────────────────────────────────

/// What this hub is, for another hub to read.
async fn hub_manifest(State(st): State<App>) -> Json<Value> {
    Json(hubs::manifest(&st, &public_url()).await)
}

async fn list_hubs(State(st): State<App>, Query(q): Query<HashMap<String, String>>) -> Json<Value> {
    let refresh = q.get("refresh").map(|v| v == "1" || v == "true").unwrap_or(false);
    let max_age = if refresh { 0 } else { hubs::CACHE_SECS };
    Json(hubs::view(&st, &public_url(), max_age).await)
}

async fn refresh_hubs(State(st): State<App>) -> Json<Value> {
    Json(hubs::view(&st, &public_url(), 0).await)
}

async fn one_hub(State(st): State<App>, Path(id): Path<String>) -> impl IntoResponse {
    let view = hubs::view(&st, &public_url(), hubs::CACHE_SECS).await;
    let row = view["hubs"]
        .as_array()
        .and_then(|a| a.iter().find(|h| h["id"].as_str() == Some(&id)).cloned());
    match row {
        Some(h) => Ok(Json(h)),
        None => Err(err(StatusCode::NOT_FOUND, format!("no hub `{id}` — GET /hubs lists them"))),
    }
}

/// Add a peer hub by URL. It has to answer like a hub — a manifest at /hub or
/// a hub-shaped /stats — which is how its kind is worked out; a bare MCP
/// endpoint is a server, and belongs in POST /servers.
async fn add_hub(
    State(st): State<App>,
    headers: HeaderMap,
    Json(body): Json<Value>,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    authorized(&headers)?;
    let url = body
        .get("url")
        .and_then(|v| v.as_str())
        .ok_or_else(|| err(StatusCode::BAD_REQUEST, "`url` is required"))?;
    let custom_headers: HashMap<String, String> = body
        .get("headers")
        .and_then(|h| h.as_object())
        .map(|o| o.iter().filter_map(|(k, v)| v.as_str().map(|s| (k.clone(), s.to_string()))).collect())
        .unwrap_or_default();
    let found = hubs::identify(url, &custom_headers)
        .await
        .map_err(|e| err(StatusCode::UNPROCESSABLE_ENTITY, e))?;
    if found.url.trim_end_matches('/') == public_url().trim_end_matches('/') {
        return Err(err(StatusCode::UNPROCESSABLE_ENTITY, "that is this hub"));
    }
    let id = store::clean_id(body.get("id").and_then(|v| v.as_str()).unwrap_or(&found.id));
    if id == "mod" {
        return Err(err(StatusCode::BAD_REQUEST, "`mod` is this hub's own id — pick another"));
    }
    let peer = store::PeerHub {
        id: id.clone(),
        name: body.get("name").and_then(|v| v.as_str()).unwrap_or(&found.name).to_string(),
        url: found.url.clone(),
        kind: found.kind.clone(),
        headers: custom_headers,
        note: body.get("note").and_then(|v| v.as_str()).unwrap_or("").chars().take(300).collect(),
        added_at: store::now(),
    };
    {
        let mut peers = st.peers.write().await;
        peers.retain(|p| p.id != id);
        peers.push(peer.clone());
    }
    st.persist_hubs().await;
    let mut row = found;
    row.id = id;
    row.name = peer.name.clone();
    row.source = "user".into();
    Ok(Json(json!({ "added": serde_json::to_value(&row).unwrap_or_default(), "kind": peer.kind })))
}

async fn remove_hub(
    State(st): State<App>,
    headers: HeaderMap,
    Path(id): Path<String>,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    authorized(&headers)?;
    let removed = {
        let mut peers = st.peers.write().await;
        let before = peers.len();
        peers.retain(|p| p.id != id);
        peers.len() != before
    };
    if !removed {
        return Err(err(StatusCode::NOT_FOUND, format!("no peer hub `{id}` — built-in hubs cannot be removed")));
    }
    st.persist_hubs().await;
    Ok(Json(json!({ "removed": id })))
}

/// Connect a whole mod/index hub as one upstream: its MCP endpoint becomes a
/// server row and its tools arrive nested as id__server__tool.
async fn connect_hub(
    State(st): State<App>,
    headers: HeaderMap,
    Path(id): Path<String>,
    body: Option<Json<Value>>,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    authorized(&headers)?;
    let mut args = body.map(|Json(b)| b).unwrap_or(json!({}));
    args["hub"] = json!(id);
    match hub::call_tool(&st, "hub_connect", &args, true).await {
        Ok(v) => Ok(Json(v["structuredContent"].clone())),
        Err(e) => Err(err(StatusCode::UNPROCESSABLE_ENTITY, e)),
    }
}

/// Whatever you paste — URL, CID, client config, `claude mcp add` line, QR
/// payload — resolved into candidate servers. Nothing is registered here.
async fn parse_intake(Json(body): Json<Value>) -> Json<Value> {
    let text = body
        .get("text")
        .or_else(|| body.get("input"))
        .and_then(|v| v.as_str())
        .unwrap_or("");
    let parsed = intake::parse(text, 0).await;
    Json(serde_json::to_value(parsed).unwrap_or_default())
}

async fn client_config(Query(q): Query<HashMap<String, String>>) -> Json<Value> {
    let url = format!("{}/mcp", public_url());
    let client = q.get("client").map(String::as_str).unwrap_or("json");
    let cfg = match client {
        "claude" | "cli" => json!({
            "command": format!("claude mcp add hub --transport http {url}"),
            "note": "One entry gives Claude every aggregated tool, namespaced server__tool."
        }),
        "cursor" | "vscode" => json!({
            "mcpServers": { "hub": { "url": url, "type": "http" } }
        }),
        _ => json!({
            "mcpServers": { "hub": { "transport": "http", "url": url } }
        }),
    };
    Json(json!({ "client": client, "url": url, "config": cfg }))
}

// ── write routes ─────────────────────────────────────────────────────

async fn add_server(
    State(st): State<App>,
    headers: HeaderMap,
    Json(body): Json<Value>,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    authorized(&headers)?;
    match hub::register_server(&st, &body).await {
        Ok(v) => Ok(Json(v)),
        Err(e) if e.starts_with("probe failed") => Err(err(StatusCode::UNPROCESSABLE_ENTITY, e)),
        Err(e) => Err(err(StatusCode::BAD_REQUEST, e)),
    }
}

async fn remove_server(
    State(st): State<App>,
    headers: HeaderMap,
    Path(id): Path<String>,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    authorized(&headers)?;
    let was_user = {
        let mut user = st.user.write().await;
        let before = user.len();
        user.retain(|s| s.id != id);
        user.len() != before
    };
    if was_user {
        st.probes.write().await.remove(&id);
    } else if st.fleet.read().await.contains_key(&id) {
        // Fleet mods re-appear on every scan — disable instead of delete.
        st.disabled.write().await.insert(id.clone());
    } else {
        return Err(err(StatusCode::NOT_FOUND, format!("no server `{id}`")));
    }
    st.persist().await;
    Ok(Json(json!({ "removed": id, "was_user": was_user })))
}

async fn toggle_server(
    State(st): State<App>,
    headers: HeaderMap,
    Path(id): Path<String>,
    Json(body): Json<Value>,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    authorized(&headers)?;
    if st.get(&id).await.is_none() {
        return Err(err(StatusCode::NOT_FOUND, format!("no server `{id}`")));
    }
    let enabled = body.get("enabled").and_then(|v| v.as_bool()).unwrap_or(true);
    {
        let mut d = st.disabled.write().await;
        if enabled {
            d.remove(&id);
        } else {
            d.insert(id.clone());
        }
    }
    st.persist().await;
    Ok(Json(json!({ "id": id, "enabled": enabled })))
}

/// `?wake=0` opts out of waking a sleeping local mod; by default an explicit
/// re-probe means "I want this server up".
async fn refresh_server(
    State(st): State<App>,
    Path(id): Path<String>,
    Query(q): Query<HashMap<String, String>>,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    let wake = q.get("wake").map(|v| v != "0" && v != "false").unwrap_or(true);
    match st.refresh_one_wake(&id, wake).await {
        Some(p) => Ok(Json(json!({ "id": id, "probe": hub::probe_json(&p) }))),
        None => Err(err(StatusCode::NOT_FOUND, format!("no server `{id}`"))),
    }
}

async fn refresh_all(State(st): State<App>) -> Json<Value> {
    st.refresh_all().await;
    let probes = st.probes.read().await;
    Json(json!({ "refreshed": probes.len(), "up": probes.values().filter(|p| p.ok).count() }))
}

/// Run the live sweep now: knock on every port the fleet mentions and adopt
/// whatever speaks MCP, declared or not.
async fn discover(State(st): State<App>) -> Json<Value> {
    let found = st.sweep().await;
    Json(json!({
        "swept": found.len(),
        "servers": found,
        "note": "mods serving MCP without declaring it in config.json",
    }))
}

async fn adhoc_probe(
    State(_st): State<App>,
    Json(body): Json<Value>,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    let url = body
        .get("url")
        .and_then(|v| v.as_str())
        .ok_or_else(|| err(StatusCode::BAD_REQUEST, "`url` is required"))?;
    let headers: HashMap<String, String> = body
        .get("headers")
        .and_then(|h| h.as_object())
        .map(|o| {
            o.iter()
                .filter_map(|(k, v)| v.as_str().map(|s| (k.clone(), s.to_string())))
                .collect()
        })
        .unwrap_or_default();
    let entry = store::ServerEntry {
        id: "adhoc".into(),
        name: "adhoc".into(),
        url: url.to_string(),
        headers,
        source: "probe".into(),
        note: String::new(),
        added_at: 0,
        ..Default::default()
    };
    let probe = upstream::probe(&entry).await;
    Ok(Json(json!({ "url": url, "probe": hub::probe_json(&probe), "tools": probe.tools })))
}

async fn rest_call(
    State(st): State<App>,
    headers: HeaderMap,
    Json(body): Json<Value>,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    may_call(&headers)?;
    let may_write = authorized(&headers).is_ok();
    // Either {tool: "server__tool"} or the {server, tool} pair form.
    let server = body.get("server").and_then(|v| v.as_str());
    let tool = match (server, body.get("tool").and_then(|v| v.as_str())) {
        (Some(s), Some(t)) => format!("{s}__{t}"),
        (None, Some(t)) => t.to_string(),
        _ => return Err(err(StatusCode::BAD_REQUEST, "`tool` (server__tool) is required")),
    };
    let args = body.get("args").or(body.get("arguments")).cloned().unwrap_or(json!({}));
    match hub::call_tool(&st, &tool, &args, may_write).await {
        Ok(v) => Ok(Json(json!({ "tool": tool, "result": v }))),
        // The upstream said no. 422 rather than 502 so its reason survives the
        // proxies between here and the console.
        Err(e) => Err(err(StatusCode::UNPROCESSABLE_ENTITY, e)),
    }
}

// ── the hub's own MCP endpoint ───────────────────────────────────────

async fn mcp_http(
    State(st): State<App>,
    headers: HeaderMap,
    Json(msg): Json<Value>,
) -> impl IntoResponse {
    // Browsing the catalogue and running a tool are different privileges, so
    // the gate sits on tools/call only: any client may initialize and list.
    let may_run = may_call(&headers).is_ok();
    let may_write = authorized(&headers).is_ok();

    // Batch or single message, per JSON-RPC 2.0.
    if let Some(batch) = msg.as_array() {
        let mut replies = Vec::new();
        for m in batch {
            if let Some(r) = hub::handle_message_gated(&st, m, may_run, may_write).await {
                replies.push(r);
            }
        }
        return Json(json!(replies)).into_response();
    }
    match hub::handle_message_gated(&st, &msg, may_run, may_write).await {
        Some(reply) => Json(reply).into_response(),
        None => StatusCode::ACCEPTED.into_response(), // notification
    }
}

async fn mcp_get() -> Json<Value> {
    Json(json!({
        "protocol": "MCP (JSON-RPC 2.0 over Streamable HTTP)",
        "hint": "POST JSON-RPC here. initialize → tools/list → tools/call. Tool names are server__tool.",
    }))
}
