//! HTTP surface: MCP Streamable HTTP at /mcp, a full REST API where every MCP
//! tool has a route, a self-describing OpenAPI document, and the embedded
//! console app.
//!
//! Every REST handler dispatches through `mcp::call_tool`, so a capability is
//! still defined exactly once — REST is a projection of the tool layer, not a
//! second implementation of it.

use crate::{mcp, x};
use axum::{
    extract::{ConnectInfo, Path, Query, Request},
    http::{header, HeaderMap, StatusCode},
    response::{Html, IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::net::SocketAddr;
use tower_http::cors::CorsLayer;

const CONSOLE_HTML: &str = include_str!("console.html");

/// One REST route, and the MCP tool it projects. This table is the source of
/// truth for `/openapi.json` and the endpoint list in `/health`; the router
/// below mirrors it, and `tests/test_x.py` asserts the two agree.
struct RouteDoc {
    method: &'static str,
    /// OpenAPI-style path (`{id}`), not axum-style (`:id`).
    path: &'static str,
    tool: &'static str,
    summary: &'static str,
    /// Query parameters accepted, as aliased into tool args.
    query: &'static [&'static str],
}

const ROUTES: &[RouteDoc] = &[
    RouteDoc { method: "GET", path: "/search", tool: "search",
        summary: "Search public posts from the last 7 days (full X query syntax).",
        query: &["q", "max_results", "sort_order", "start_time", "end_time", "next_token"] },
    RouteDoc { method: "GET", path: "/counts", tool: "counts",
        summary: "Post volume matching a query, bucketed by minute/hour/day.",
        query: &["q", "granularity"] },
    RouteDoc { method: "GET", path: "/posts/{id}", tool: "get_post",
        summary: "One post by id or pasted x.com URL. Works with no credentials \
                  via the syndication CDN.", query: &[] },
    RouteDoc { method: "POST", path: "/posts", tool: "post",
        summary: "Publish a post as the authenticated account.", query: &[] },
    RouteDoc { method: "DELETE", path: "/posts/{id}", tool: "delete_post",
        summary: "Delete one of the authenticated account's posts.", query: &[] },
    RouteDoc { method: "POST", path: "/posts/{id}/like", tool: "like",
        summary: "Like a post as the authenticated account.", query: &[] },
    RouteDoc { method: "POST", path: "/posts/{id}/repost", tool: "repost",
        summary: "Repost a post as the authenticated account.", query: &[] },
    RouteDoc { method: "GET", path: "/users/{handle}", tool: "user",
        summary: "Account profile by @handle or numeric id.", query: &[] },
    RouteDoc { method: "GET", path: "/users/{handle}/timeline", tool: "timeline",
        summary: "Recent posts from an account.", query: &["max_results", "exclude"] },
    RouteDoc { method: "GET", path: "/users/{handle}/mentions", tool: "mentions",
        summary: "Posts mentioning an account.", query: &["max_results"] },
    RouteDoc { method: "GET", path: "/users/{handle}/followers", tool: "followers",
        summary: "Accounts following the given account.", query: &["max_results"] },
    RouteDoc { method: "GET", path: "/users/{handle}/following", tool: "following",
        summary: "Accounts the given account follows.", query: &["max_results"] },
    RouteDoc { method: "POST", path: "/users/{handle}/follow", tool: "follow",
        summary: "Follow an account as the authenticated account.", query: &[] },
    RouteDoc { method: "GET", path: "/me", tool: "me",
        summary: "The authenticated account (needs user-context credentials).", query: &[] },
    RouteDoc { method: "GET", path: "/mentions", tool: "mentions",
        summary: "Posts mentioning the authenticated account.", query: &["max_results"] },
    RouteDoc { method: "GET", path: "/auth", tool: "auth_status",
        summary: "Which credential rails are configured (never the secrets).", query: &[] },
    RouteDoc { method: "POST", path: "/forward", tool: "*",
        summary: "Generic adapter: {action, ...args} → any MCP tool.", query: &[] },
];

fn header_key(headers: &HeaderMap) -> Option<String> {
    headers
        .get("x-api-key")
        .and_then(|v| v.to_str().ok())
        .map(String::from)
        .or_else(|| {
            headers
                .get(header::AUTHORIZATION)
                .and_then(|v| v.to_str().ok())
                .and_then(|v| v.strip_prefix("Bearer "))
                .map(String::from)
        })
}

fn info() -> Value {
    let creds = x::resolve(None);
    let endpoints: Vec<Value> = ROUTES
        .iter()
        .map(|r| json!({ "method": r.method, "path": r.path, "tool": r.tool, "summary": r.summary }))
        .collect();
    json!({
        "name": "x",
        "version": mcp::SERVER_VERSION,
        "status": "ok",
        "backend": "rust-mcp",
        "protocol": mcp::PROTOCOL_VERSION,
        "upstream": x::base_url(),
        "auth": { "reads": creds.has_any(), "writes": creds.has_user() },
        "mcp": "POST /mcp (Streamable HTTP, JSON-RPC 2.0)",
        "stdio": "x-api --stdio",
        "app": "GET / (browser)",
        "openapi": "GET /openapi.json",
        "endpoints": endpoints
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

async fn mcp_endpoint(headers: HeaderMap, Json(msg): Json<Value>) -> Response {
    let key = header_key(&headers);
    match mcp::handle_message(&msg, key.as_deref()).await {
        Some(resp) => Json(resp).into_response(),
        None => StatusCode::ACCEPTED.into_response(),
    }
}

/// REST → MCP tool adapter. Upstream status codes are preserved where the tool
/// layer reported one, so a 429 from X still reads as a 429 here.
async fn via_tool(name: &str, args: Value, key: Option<String>) -> Response {
    match mcp::call_tool(name, &args, key.as_deref()).await {
        Ok(v) => Json(v).into_response(),
        Err(e) => {
            let status = e
                .strip_prefix("x api ")
                .and_then(|rest| rest.split(':').next())
                .and_then(|code| code.parse::<u16>().ok())
                .and_then(|code| StatusCode::from_u16(code).ok())
                .unwrap_or(StatusCode::BAD_REQUEST);
            (status, Json(json!({ "error": e }))).into_response()
        }
    }
}

fn args_from_query(q: &HashMap<String, String>, aliases: &[(&str, &str)]) -> Value {
    let mut args = json!({});
    for (from, to) in aliases {
        if let Some(v) = q.get(*from).filter(|v| !v.is_empty()) {
            // Numeric-looking params (max_results…) must not arrive as strings.
            args[*to] = v.parse::<u64>().map(|n| json!(n)).unwrap_or_else(|_| json!(v));
        }
    }
    args
}

/// `max_results` plus one extra alias, the shape most list routes want.
fn list_args(q: &HashMap<String, String>, extra: &[(&str, &str)]) -> Value {
    let mut aliases: Vec<(&str, &str)> = vec![("max_results", "max_results"), ("limit", "max_results")];
    aliases.extend_from_slice(extra);
    args_from_query(q, &aliases)
}

async fn search(headers: HeaderMap, Query(q): Query<HashMap<String, String>>) -> Response {
    let args = args_from_query(
        &q,
        &[
            ("q", "query"),
            ("query", "query"),
            ("max_results", "max_results"),
            ("limit", "max_results"),
            ("sort_order", "sort_order"),
            ("start_time", "start_time"),
            ("end_time", "end_time"),
            ("next_token", "next_token"),
        ],
    );
    via_tool("search", args, header_key(&headers)).await
}

async fn counts(headers: HeaderMap, Query(q): Query<HashMap<String, String>>) -> Response {
    let args = args_from_query(
        &q,
        &[("q", "query"), ("query", "query"), ("granularity", "granularity")],
    );
    via_tool("counts", args, header_key(&headers)).await
}

async fn get_post(headers: HeaderMap, Path(id): Path<String>) -> Response {
    via_tool("get_post", json!({ "id": id }), header_key(&headers)).await
}

async fn create_post(headers: HeaderMap, Json(body): Json<Value>) -> Response {
    via_tool("post", body, header_key(&headers)).await
}

async fn delete_post(headers: HeaderMap, Path(id): Path<String>) -> Response {
    via_tool("delete_post", json!({ "id": id }), header_key(&headers)).await
}

async fn like_post(headers: HeaderMap, Path(id): Path<String>) -> Response {
    via_tool("like", json!({ "id": id }), header_key(&headers)).await
}

async fn repost_post(headers: HeaderMap, Path(id): Path<String>) -> Response {
    via_tool("repost", json!({ "id": id }), header_key(&headers)).await
}

async fn get_user(headers: HeaderMap, Path(handle): Path<String>) -> Response {
    via_tool("user", json!({ "username": handle }), header_key(&headers)).await
}

async fn follow_user(headers: HeaderMap, Path(handle): Path<String>) -> Response {
    via_tool("follow", json!({ "username": handle }), header_key(&headers)).await
}

/// One handler for the three per-account list routes — they differ only in the
/// tool they reach.
async fn user_list(
    tool: &str,
    headers: HeaderMap,
    handle: String,
    q: HashMap<String, String>,
    extra: &[(&str, &str)],
) -> Response {
    let mut args = list_args(&q, extra);
    args["username"] = json!(handle);
    via_tool(tool, args, header_key(&headers)).await
}

async fn user_timeline(
    headers: HeaderMap,
    Path(handle): Path<String>,
    Query(q): Query<HashMap<String, String>>,
) -> Response {
    user_list("timeline", headers, handle, q, &[("exclude", "exclude")]).await
}

async fn user_mentions(
    headers: HeaderMap,
    Path(handle): Path<String>,
    Query(q): Query<HashMap<String, String>>,
) -> Response {
    user_list("mentions", headers, handle, q, &[]).await
}

async fn user_followers(
    headers: HeaderMap,
    Path(handle): Path<String>,
    Query(q): Query<HashMap<String, String>>,
) -> Response {
    user_list("followers", headers, handle, q, &[]).await
}

async fn user_following(
    headers: HeaderMap,
    Path(handle): Path<String>,
    Query(q): Query<HashMap<String, String>>,
) -> Response {
    user_list("following", headers, handle, q, &[]).await
}

async fn me(headers: HeaderMap) -> Response {
    via_tool("me", json!({}), header_key(&headers)).await
}

async fn my_mentions(headers: HeaderMap, Query(q): Query<HashMap<String, String>>) -> Response {
    via_tool("mentions", list_args(&q, &[]), header_key(&headers)).await
}

async fn auth_status(headers: HeaderMap) -> Response {
    via_tool("auth_status", json!({}), header_key(&headers)).await
}

/// Write credentials to `~/.mod/x/credentials.json` (0600). Loopback only:
/// these are the keys that let this server act as the account, so a request
/// from anywhere but this machine has no business setting them. `resolve`
/// re-reads the file per request, so there is nothing to restart.
async fn set_keys(ConnectInfo(peer): ConnectInfo<SocketAddr>, Json(body): Json<Value>) -> Response {
    if !peer.ip().is_loopback() {
        return (
            StatusCode::FORBIDDEN,
            Json(json!({
                "error": "credentials can only be set from this machine (loopback); \
                          use `m x/set_keys ... persist=True` on the host"
            })),
        )
            .into_response();
    }
    match x::save_creds(&body) {
        Ok((set, cleared)) => {
            let creds = x::resolve(None);
            Json(json!({
                "set": set,
                "cleared": cleared,
                "path": x::creds_path(),
                "auth": { "reads": creds.has_any(), "writes": creds.has_user() }
            }))
            .into_response()
        }
        Err(e) => (StatusCode::BAD_REQUEST, Json(json!({ "error": e }))).into_response(),
    }
}

/// Generic escape hatch: any MCP tool by name, same shape as the mod protocol
/// `forward` call.
async fn forward(headers: HeaderMap, Json(mut body): Json<Value>) -> Response {
    let action = body
        .as_object_mut()
        .and_then(|o| o.remove("action").or_else(|| o.remove("fn")))
        .and_then(|a| a.as_str().map(String::from))
        .unwrap_or_else(|| "search".into());
    via_tool(&action, body, header_key(&headers)).await
}

async fn tools() -> Json<Value> {
    Json(json!({ "tools": mcp::tool_list() }))
}

async fn health() -> Json<Value> {
    Json(info())
}

/// OpenAPI 3.1, generated from `ROUTES` + the MCP tool schemas, so the document
/// can't drift from the tool definitions it describes.
fn openapi() -> Value {
    let schemas = mcp::tool_list();
    let schema_for = |tool: &str| -> Option<Value> {
        schemas
            .as_array()?
            .iter()
            .find(|t| t.get("name").and_then(|n| n.as_str()) == Some(tool))
            .and_then(|t| t.get("inputSchema").cloned())
    };

    let mut paths = serde_json::Map::new();
    for r in ROUTES {
        let mut op = json!({
            "summary": r.summary,
            "operationId": format!("{}_{}", r.method.to_lowercase(),
                r.path.trim_matches('/').replace(['/', '{', '}'], "_")),
            "tags": [if r.path.starts_with("/users") { "users" }
                     else if r.path.starts_with("/posts") { "posts" }
                     else { "meta" }],
            "responses": {
                "200": { "description": "X API v2 payload (or the tool's own result object)" },
                "401": { "description": "credentials missing for this rail" },
                "429": { "description": "upstream rate limit, passed through" }
            }
        });

        let mut params: Vec<Value> = vec![];
        for seg in r.path.split('/') {
            if let Some(name) = seg.strip_prefix('{').and_then(|s| s.strip_suffix('}')) {
                params.push(json!({
                    "name": name, "in": "path", "required": true,
                    "schema": { "type": "string" },
                    "description": if name == "handle" { "@handle or numeric user id" }
                                   else { "post id, or a pasted x.com status URL" }
                }));
            }
        }
        for name in r.query {
            params.push(json!({
                "name": name, "in": "query", "required": false,
                "schema": { "type": if *name == "max_results" { "integer" } else { "string" } }
            }));
        }
        if !params.is_empty() {
            op["parameters"] = json!(params);
        }

        if r.method == "POST" {
            let body = if r.tool == "*" {
                json!({ "type": "object",
                        "properties": { "action": { "type": "string",
                                                    "description": "MCP tool name" } },
                        "required": ["action"], "additionalProperties": true })
            } else {
                // Path params are already in the URL; the body carries the rest.
                let mut s = schema_for(r.tool).unwrap_or_else(|| json!({ "type": "object" }));
                if r.path.contains('{') {
                    s = json!({ "type": "object" });
                }
                s
            };
            op["requestBody"] = json!({
                "required": r.tool != "*" && !r.path.contains('{'),
                "content": { "application/json": { "schema": body } }
            });
        }

        let entry = paths
            .entry(r.path.to_string())
            .or_insert_with(|| json!({}));
        entry[r.method.to_lowercase()] = op;
    }

    // The two routes that aren't tool projections.
    paths.insert("/mcp".into(), json!({ "post": {
        "summary": "MCP Streamable HTTP — JSON-RPC 2.0 (initialize, ping, tools/list, tools/call).",
        "operationId": "mcp", "tags": ["mcp"],
        "requestBody": { "required": true, "content": { "application/json": { "schema": {
            "type": "object",
            "properties": { "jsonrpc": { "const": "2.0" }, "id": {}, "method": { "type": "string" },
                            "params": { "type": "object" } },
            "required": ["jsonrpc", "method"] } } } },
        "responses": { "200": { "description": "JSON-RPC response" },
                       "202": { "description": "notification accepted, no reply" } } } }));
    paths.insert("/auth/keys".into(), json!({ "post": {
        "summary": "Store X credentials in ~/.mod/x/credentials.json (0600). Loopback only. \
                    An empty value clears a field.",
        "operationId": "set_keys", "tags": ["meta"],
        "requestBody": { "required": true, "content": { "application/json": { "schema": {
            "type": "object",
            "properties": x::CRED_FIELDS.iter()
                .map(|f| (f.to_string(), json!({ "type": "string" })))
                .collect::<serde_json::Map<_, _>>() } } } },
        "responses": { "200": { "description": "fields set/cleared, never echoed" },
                       "403": { "description": "not loopback" } } } }));
    paths.insert("/tools".into(), json!({ "get": {
        "summary": "MCP tool registry (REST view of tools/list).",
        "operationId": "tools", "tags": ["mcp"],
        "responses": { "200": { "description": "{ tools: [...] }" } } } }));
    paths.insert("/health".into(), json!({ "get": {
        "summary": "Server info: version, protocol, upstream, auth rails, route table.",
        "operationId": "health", "tags": ["meta"],
        "responses": { "200": { "description": "info object" } } } }));

    json!({
        "openapi": "3.1.0",
        "info": {
            "title": "x — X (Twitter) API v2",
            "version": mcp::SERVER_VERSION,
            "description": "A REST projection of an MCP tool layer. Every route below \
                            dispatches through the same `tools/call` handler that MCP \
                            clients reach at POST /mcp.\n\nReads use an app-only bearer \
                            token; anything acting as the account uses OAuth 1.0a user \
                            context. GET /posts/{id} works with no credentials at all."
        },
        "servers": [{ "url": "/" }],
        "components": { "securitySchemes": {
            "bearer": { "type": "http", "scheme": "bearer",
                        "description": "X app-only bearer token; overrides stored credentials for this request." },
            "apiKey": { "type": "apiKey", "in": "header", "name": "x-api-key",
                        "description": "Same token, header alias." } } },
        "security": [{ "bearer": [] }, { "apiKey": [] }],
        "paths": paths
    })
}

async fn openapi_doc() -> Json<Value> {
    Json(openapi())
}

pub async fn serve(port: u16) {
    let app = Router::new()
        .route("/", get(root))
        .route("/info", get(health))
        .route("/health", get(health))
        .route("/openapi.json", get(openapi_doc))
        .route(
            "/mcp",
            post(mcp_endpoint).get(|| async {
                (
                    StatusCode::METHOD_NOT_ALLOWED,
                    Json(json!({ "error": "POST JSON-RPC messages to /mcp" })),
                )
            }),
        )
        .route("/search", get(search))
        .route("/counts", get(counts))
        .route("/posts", post(create_post))
        .route("/posts/:id", get(get_post).delete(delete_post))
        .route("/posts/:id/like", post(like_post))
        .route("/posts/:id/repost", post(repost_post))
        .route("/users/:handle", get(get_user))
        .route("/users/:handle/timeline", get(user_timeline))
        .route("/users/:handle/mentions", get(user_mentions))
        .route("/users/:handle/followers", get(user_followers))
        .route("/users/:handle/following", get(user_following))
        .route("/users/:handle/follow", post(follow_user))
        .route("/me", get(me))
        .route("/mentions", get(my_mentions))
        .route("/auth", get(auth_status))
        .route("/auth/keys", post(set_keys))
        .route("/forward", post(forward))
        .route("/tools", get(tools))
        .layer(CorsLayer::permissive());

    let addr = std::net::SocketAddr::from(([0, 0, 0, 0], port));
    println!("x rust-mcp backend listening on {addr} (app at /, MCP at /mcp, REST per /openapi.json)");
    let listener = tokio::net::TcpListener::bind(addr).await.expect("bind");
    axum::serve(listener, app.into_make_service_with_connect_info::<SocketAddr>())
        .await
        .expect("serve");
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The documented surface must describe tools that actually exist —
    /// otherwise /openapi.json is fiction.
    #[test]
    fn every_documented_route_maps_to_a_real_tool() {
        let tools = mcp::tool_list();
        let names: Vec<&str> = tools
            .as_array()
            .unwrap()
            .iter()
            .map(|t| t["name"].as_str().unwrap())
            .collect();
        for r in ROUTES {
            assert!(
                r.tool == "*" || names.contains(&r.tool),
                "route {} {} points at missing tool {}",
                r.method,
                r.path,
                r.tool
            );
        }
    }

    #[test]
    fn openapi_covers_every_route() {
        let doc = openapi();
        for r in ROUTES {
            let op = &doc["paths"][r.path][r.method.to_lowercase()];
            assert!(!op.is_null(), "openapi missing {} {}", r.method, r.path);
            assert!(op["summary"].as_str().is_some_and(|s| !s.is_empty()));
        }
    }
}
