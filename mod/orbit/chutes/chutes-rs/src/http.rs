//! HTTP surface: MCP Streamable HTTP at /mcp, REST adapters that dispatch
//! through the same MCP tool layer, and the embedded 8-bit console.

use crate::{chutes, mcp, owner, upstream};
use axum::{
    body::Body,
    extract::{Path, Query, Request},
    http::{header, HeaderMap, StatusCode},
    response::{Html, IntoResponse, Response},
    routing::{get, post},
    Json, Router, ServiceExt,
};
use futures_util::StreamExt;
use serde_json::{json, Value};
use std::collections::HashMap;
use std::sync::OnceLock;
use tower::{util::MapRequestLayer, Layer};
use tower_http::cors::CorsLayer;

const CONSOLE_HTML: &str = include_str!("console.html");

/// Keys off the request: `x-chutes-key`, plus `x-api-key` /
/// `Authorization: Bearer` as the generic one. Nothing is stored or logged.
fn header_keys(headers: &HeaderMap) -> mcp::Keys {
    let mut keys = mcp::Keys::new();
    let get = |name: &str| headers.get(name).and_then(|v| v.to_str().ok()).map(str::trim).filter(|v| !v.is_empty()).map(String::from);
    if let Some(k) = get("x-chutes-key") {
        keys.insert(chutes::ID.to_string(), k);
    }
    let generic = get("x-api-key").or_else(|| {
        headers
            .get(header::AUTHORIZATION)
            .and_then(|v| v.to_str().ok())
            .and_then(|v| v.strip_prefix("Bearer "))
            .map(|v| v.trim().to_string())
            .filter(|v| !v.is_empty())
    });
    if let Some(k) = generic {
        keys.insert("*".into(), k);
    }
    keys
}

fn info() -> Value {
    json!({
        "name": "chutes",
        "version": mcp::SERVER_VERSION,
        "status": "ok",
        "backend": "rust-mcp",
        "protocol": mcp::PROTOCOL_VERSION,
        "upstream": chutes::base_url(),
        "default_model": chutes::default_model(),
        "key": chutes::key_source() != "none",
        "endpoints": {
            "mcp": "POST /mcp (Streamable HTTP, JSON-RPC 2.0)",
            "chat": "POST /chat {message|messages, model, stream}",
            "compare": "POST /compare {message, models:[...]}",
            "route": "POST /route {search, kind, max_price, ask}",
            "images": "POST /images {prompt, model}",
            "models": "GET /models?q=&kind=&sort=",
            "status": "GET /status?counts=1",
            "forward": "POST /forward {action, ...args}",
            "tools": "GET /tools",
            "key": "GET /key · POST /key {key} · DELETE /key (owner, mod-protocol token)",
            "console": "GET / (browser)"
        },
        "stdio": "chutes-api --stdio"
    })
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

async fn mcp_endpoint(headers: HeaderMap, Json(msg): Json<Value>) -> Response {
    match mcp::handle_message(&msg, &header_keys(&headers)).await {
        Some(resp) => Json(resp).into_response(),
        None => StatusCode::ACCEPTED.into_response(),
    }
}

/// REST → MCP tool adapter; errors surface as {error} with upstream-ish status.
async fn via_tool(name: &str, args: Value, keys: mcp::Keys) -> Response {
    match mcp::call_tool(name, &args, &keys).await {
        Ok(v) => Json(v).into_response(),
        Err(e) => {
            let status = if e.starts_with("upstream 4") {
                StatusCode::BAD_GATEWAY // upstream complaint, pass detail through
            } else {
                StatusCode::BAD_REQUEST
            };
            (status, Json(json!({ "error": e }))).into_response()
        }
    }
}

async fn chat(headers: HeaderMap, Json(mut body): Json<Value>) -> Response {
    let keys = header_keys(&headers);
    let stream = body.get("stream").and_then(|s| s.as_bool()).unwrap_or(false);
    if !stream {
        return via_tool("chat", body, keys).await;
    }
    // Streaming bypasses tools/call (MCP tool results are unary) — SSE pass-through.
    let resolved = mcp::key_for(&body, &keys);
    if let Some(o) = body.as_object_mut() {
        o.remove("api_key");
    }
    // `message`/`system` shorthands work on the streaming path too.
    if body.get("messages").and_then(|m| m.as_array()).map(|a| a.is_empty()).unwrap_or(true) {
        let mut msgs = Vec::new();
        if let Some(sys) = body.get("system").and_then(|v| v.as_str()) {
            msgs.push(json!({ "role": "system", "content": sys }));
        }
        if let Some(m) = body.get("message").and_then(|v| v.as_str()) {
            msgs.push(json!({ "role": "user", "content": m }));
        }
        if !msgs.is_empty() {
            body["messages"] = json!(msgs);
        }
    }
    if let Some(o) = body.as_object_mut() {
        o.remove("message");
        o.remove("system");
    }
    // Same default-model list (and same fall-through on a chute that can't
    // answer) as the unary path in `run_chat`.
    let candidates = match body.get("model").and_then(|v| v.as_str()).map(str::trim).filter(|m| !m.is_empty()) {
        Some(m) => vec![m.to_string()],
        None => chutes::default_models(),
    };
    body["stream"] = json!(true);
    let attempt = upstream::try_models(&candidates, |model| {
        let key = resolved.clone();
        let mut body = body.clone();
        body["model"] = json!(model);
        async move { upstream::chat_stream_raw(&key, &body).await }
    })
    .await;
    match attempt {
        Ok((model, resp)) => {
            let stream = resp.bytes_stream().map(|c| c.map_err(std::io::Error::other));
            Response::builder()
                .header(header::CONTENT_TYPE, "text/event-stream")
                .header(header::CACHE_CONTROL, "no-cache")
                // Which chute actually answered — may not be the first choice.
                .header("x-model", model)
                .body(Body::from_stream(stream))
                .unwrap()
        }
        Err(e) => (
            StatusCode::from_u16(e.status).unwrap_or(StatusCode::BAD_GATEWAY),
            Json(json!({ "error": e.message })),
        )
            .into_response(),
    }
}

async fn images(headers: HeaderMap, Json(body): Json<Value>) -> Response {
    via_tool("generate_image", body, header_keys(&headers)).await
}

async fn compare(headers: HeaderMap, Json(body): Json<Value>) -> Response {
    via_tool("compare", body, header_keys(&headers)).await
}

async fn route_pick(headers: HeaderMap, Json(body): Json<Value>) -> Response {
    via_tool("route", body, header_keys(&headers)).await
}

/// Query params → tool args (`q` is an alias for `search`, flags are strings).
fn args_from_query(q: &HashMap<String, String>) -> Value {
    let mut args = json!({});
    for (from, to) in [
        ("q", "search"), ("search", "search"), ("kind", "kind"), ("sort", "sort"),
        ("tag", "tag"), ("gpu", "gpu"), ("owner", "owner"),
    ] {
        if let Some(v) = q.get(from).filter(|v| !v.is_empty()) {
            args[to] = json!(v);
        }
    }
    for k in ["limit", "offset"] {
        if let Some(v) = q.get(k).and_then(|v| v.parse::<u64>().ok()) {
            args[k] = json!(v);
        }
    }
    for k in ["max_price", "min_instances"] {
        if let Some(v) = q.get(k).and_then(|v| v.parse::<f64>().ok()) {
            args[k] = json!(v);
        }
    }
    for k in ["refresh", "counts", "live", "facets"] {
        if q.get(k).map(|v| v == "1" || v == "true").unwrap_or(false) {
            args[k] = json!(true);
        }
    }
    args
}

async fn models(headers: HeaderMap, Query(q): Query<HashMap<String, String>>) -> Response {
    via_tool("models", args_from_query(&q), header_keys(&headers)).await
}

/// One chute, in full, by chute_id or name (names contain slashes).
async fn chute(headers: HeaderMap, Path(id): Path<String>) -> Response {
    via_tool("get_chute", json!({ "chute_id": id.trim_start_matches('/') }), header_keys(&headers)).await
}

async fn status(headers: HeaderMap, Query(q): Query<HashMap<String, String>>) -> Response {
    via_tool("status", args_from_query(&q), header_keys(&headers)).await
}

async fn forward(headers: HeaderMap, Json(mut body): Json<Value>) -> Response {
    let action = body
        .as_object_mut()
        .and_then(|o| o.remove("action").or_else(|| o.remove("fn")))
        .and_then(|a| a.as_str().map(String::from))
        .unwrap_or_else(|| "chat".into());
    via_tool(&action, body, header_keys(&headers)).await
}

async fn tools() -> Json<Value> {
    Json(json!({ "tools": mcp::tool_list() }))
}

async fn health() -> Json<Value> {
    Json(info())
}

// ── the box's key, from the console ──────────────────────────────────────────

/// The mod-protocol token on a `/key` request: `x-mod-token`, else the
/// Bearer. (Elsewhere a Bearer is a chutes key; on these routes it is who
/// you are — they are the only routes that need to know.)
fn mod_token(headers: &HeaderMap) -> Option<String> {
    let get = |name: &str| headers.get(name).and_then(|v| v.to_str().ok()).map(str::trim).filter(|v| !v.is_empty()).map(String::from);
    get("x-mod-token").or_else(|| {
        get("authorization").map(|v| v.strip_prefix("Bearer ").map(str::trim).unwrap_or(&v).to_string()).filter(|v| !v.is_empty())
    })
}

/// Who signed, or why not — never a hard failure on a GET.
fn caller(headers: &HeaderMap) -> (Option<String>, Option<String>) {
    match mod_token(headers) {
        None => (None, None),
        Some(t) => match owner::verify_token(&t) {
            Ok(addr) => (Some(addr), None),
            Err(e) => (None, Some(e)),
        },
    }
}

/// Owner-only gate for writes: 401 with no/bad token, 403 for a signed
/// stranger. Resolving the owner may ask Python once, hence spawn_blocking.
async fn require_owner(headers: &HeaderMap) -> Result<String, Response> {
    let (you, err) = caller(headers);
    let Some(you) = you else {
        let msg = err.unwrap_or_else(|| "sign in first — send a mod-protocol token as Authorization: Bearer <token> (or x-mod-token)".into());
        return Err((StatusCode::UNAUTHORIZED, Json(json!({ "error": msg, "signed_in": false }))).into_response());
    };
    let (owner_addr, source) = tokio::task::spawn_blocking(owner::owner).await.unwrap_or((None, "none"));
    match owner_addr {
        Some(o) if o == you => Ok(you),
        Some(o) => Err((
            StatusCode::FORBIDDEN,
            Json(json!({ "error": format!("{you} is not the owner of this box — the key belongs to {o} ({source})"), "you": you, "owner": o, "owner_source": source })),
        )
            .into_response()),
        None => Err((
            StatusCode::FORBIDDEN,
            Json(json!({ "error": format!("this box declares no owner — set {} or ~/.mod/chutes/owner.json", owner::ENV_OWNER), "you": you })),
        )
            .into_response()),
    }
}

/// GET /key — where the server key stands, who owns the box, whether you may write.
async fn key_get(headers: HeaderMap) -> Response {
    let (you, err) = caller(&headers);
    let report = tokio::task::spawn_blocking(move || owner::report(you.as_deref(), err.as_deref())).await;
    match report {
        Ok(v) => Json(v).into_response(),
        Err(e) => (StatusCode::INTERNAL_SERVER_ERROR, Json(json!({ "error": e.to_string() }))).into_response(),
    }
}

/// POST /key {key, verify?} — write ~/.mod/chutes/api_key. Owner only. With
/// `verify` (default true) the key is tried against chutes.ai first, on a
/// free authenticated call, so a typo never lands on disk.
async fn key_set(headers: HeaderMap, Json(body): Json<Value>) -> Response {
    let you = match require_owner(&headers).await {
        Ok(y) => y,
        Err(r) => return r,
    };
    let key = body.get("key").or_else(|| body.get("api_key")).and_then(|k| k.as_str()).map(str::trim).unwrap_or("").to_string();
    if key.is_empty() {
        return (StatusCode::BAD_REQUEST, Json(json!({ "error": "POST /key needs {key}" }))).into_response();
    }
    let verify = body.get("verify").and_then(|v| v.as_bool()).unwrap_or(true);
    if verify {
        if let Err(e) = upstream::list_chutes(&key, 1, 1).await {
            if (401..=403).contains(&e.status) {
                return (StatusCode::UNPROCESSABLE_ENTITY, Json(json!({ "error": format!("chutes.ai rejected that key ({})", e.status), "upstream": e.message, "saved": false }))).into_response();
            }
            // Anything else (rate limit, outage) is not the key's fault — save, but say so.
        }
    }
    match owner::write_key(&key) {
        Ok(path) => Json(json!({
            "saved": true,
            "file": path.display().to_string(),
            "key_source": chutes::key_source(),
            "shadowed_by": if chutes::key_source() == "env" { Some("env") } else { None },
            "by": you,
            "verified": verify,
        }))
        .into_response(),
        Err(e) => (StatusCode::BAD_REQUEST, Json(json!({ "error": e, "saved": false }))).into_response(),
    }
}

/// DELETE /key — remove ~/.mod/chutes/api_key. Owner only.
async fn key_clear(headers: HeaderMap) -> Response {
    let you = match require_owner(&headers).await {
        Ok(y) => y,
        Err(r) => return r,
    };
    match owner::clear_key() {
        Ok(removed) => Json(json!({ "removed": removed, "key_source": chutes::key_source(), "by": you })).into_response(),
        Err(e) => (StatusCode::BAD_REQUEST, Json(json!({ "error": e }))).into_response(),
    }
}

/// The whole surface, rooted at `/`. Requests carrying the gateway prefix are
/// rewritten onto it by `strip_base`.
fn routes() -> Router {
    Router::new()
        .route("/", get(root))
        .route("/console", get(console))
        .route("/info", get(health))
        .route("/health", get(health))
        .route("/mcp", post(mcp_endpoint).get(|| async {
            (StatusCode::METHOD_NOT_ALLOWED, Json(json!({ "error": "POST JSON-RPC messages to /mcp" })))
        }))
        .route("/chat", post(chat))
        .route("/compare", post(compare))
        .route("/route", post(route_pick))
        .route("/images", post(images))
        .route("/models", get(models))
        .route("/chute/*id", get(chute))
        .route("/status", get(status))
        .route("/forward", post(forward))
        .route("/tools", get(tools))
        .route("/key", get(key_get).post(key_set).delete(key_clear))
}

/// Gateway prefix the same surface answers on, so `{host}/chutes` reaches the
/// console *and* the console's own `BASE`-relative fetches — the mod router
/// keeps the prefix on app routes. `CHUTES_BASE_PATH=` (empty) turns it off.
fn base_path() -> &'static str {
    static BASE: OnceLock<String> = OnceLock::new();
    BASE.get_or_init(|| {
        let raw = std::env::var("CHUTES_BASE_PATH").unwrap_or_else(|_| "/chutes".into());
        let raw = raw.trim().trim_end_matches('/').to_string();
        match raw.as_str() {
            "" | "/" => String::new(),
            _ if raw.starts_with('/') => raw,
            _ => format!("/{raw}"),
        }
    })
}

/// Rewrite `/chutes/...` → `/...` *before* routing, so the prefixed and bare
/// forms are the same server — including `/chutes` and `/chutes/`, which both
/// land on the console. Wrapped around the Router (not `Router::layer`, which
/// only runs after a route has already matched).
fn strip_base(mut req: Request) -> Request {
    let base = base_path();
    if base.is_empty() {
        return req;
    }
    let uri = req.uri().clone();
    let Some(rest) = uri.path().strip_prefix(base) else { return req };
    if !(rest.is_empty() || rest.starts_with('/')) {
        return req; // /chutesomething is not /chutes
    }
    let rest = if rest.is_empty() || rest == "/" { "/" } else { rest };
    let rewritten = match uri.query() {
        Some(q) => format!("{rest}?{q}"),
        None => rest.to_string(),
    };
    if let Ok(u) = rewritten.parse() {
        *req.uri_mut() = u;
    }
    req
}

pub async fn serve(port: u16) {
    let app = MapRequestLayer::new(strip_base).layer(routes().layer(CorsLayer::permissive()));

    let addr = std::net::SocketAddr::from(([0, 0, 0, 0], port));
    println!(
        "chutes rust-mcp backend listening on {addr} (MCP at /mcp, console at /{})",
        if base_path().is_empty() { String::new() } else { format!(" and {}", base_path()) }
    );
    println!("upstream: {} · key: {}", chutes::base_url(), chutes::key_source());
    let listener = tokio::net::TcpListener::bind(addr).await.expect("bind");
    axum::serve(listener, ServiceExt::<Request>::into_make_service(app)).await.expect("serve");
}
