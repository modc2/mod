//! HTTP surface: MCP Streamable HTTP at /mcp, REST adapters that dispatch
//! through the same MCP tool layer, and the console.
//!
//! Two ways in, one behaviour. Behind the fleet router the API is reachable at
//! /api/lium (prefix stripped) and the console at /lium (prefix kept), so both
//! paths are served here and one console works in both places.

use crate::{lium, mcp};
use axum::{
    extract::{Path, Query, Request},
    http::{header, HeaderMap, StatusCode},
    response::{Html, IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use serde_json::{json, Value};
use std::collections::HashMap;
use tower_http::cors::CorsLayer;

const CONSOLE_HTML: &str = include_str!("console.html");

/// The caller's own Lium key, never the server's: x-api-key or Bearer.
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
        .filter(|k| !k.trim().is_empty())
}

fn info() -> Value {
    json!({
        "name": mcp::SERVER_NAME,
        "version": mcp::SERVER_VERSION,
        "status": "ok",
        "backend": "rust-mcp",
        "protocol": mcp::PROTOCOL_VERSION,
        "upstream": lium::base_url(),
        "netuid": lium::NETUID,
        "chain": "bittensor",
        "endpoints": {
            "mcp": "POST /mcp (Streamable HTTP, JSON-RPC 2.0)",
            "executors": "GET /executors?gpu_type=&max_price=&available_only=&sort=&limit=",
            "executor": "GET /executors/:id",
            "stats": "GET /stats | GET /capacity | GET /subnet",
            "provider": "GET /provider/:miner_hotkey",
            "templates": "GET /templates?q=",
            "pods": "GET /pods | GET /pods/:id | DELETE /pods/:id | POST /pods/:id/reboot | GET /pods/:id/logs",
            "up": "POST /up {executor_id, name?, template_id?, gpu_count?}",
            "account": "GET /me | GET /ssh-keys | POST /ssh-keys | GET /volumes",
            "api_explorer": "GET /endpoints?q= | POST /api {method, path, query, body}",
            "forward": "POST /forward {action, ...args}",
            "tools": "GET /tools",
            "console": "GET /lium (browser)"
        },
        "stdio": "lium-api --stdio"
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

async fn health() -> Json<Value> {
    Json(info())
}

async fn mcp_endpoint(headers: HeaderMap, Json(msg): Json<Value>) -> Response {
    match mcp::handle_message(&msg, header_key(&headers).as_deref()).await {
        Some(resp) => Json(resp).into_response(),
        None => StatusCode::ACCEPTED.into_response(),
    }
}

/// REST → MCP tool adapter; upstream complaints keep their status and meaning.
async fn via_tool(name: &str, args: Value, key: Option<String>) -> Response {
    match mcp::call_tool_raw(name, &args, key.as_deref()).await {
        Ok(v) => Json(v).into_response(),
        Err(e) => {
            // An upstream 5xx is our 502: we are the ones failing to serve it.
            let status = if e.upstream && e.status >= 500 {
                StatusCode::BAD_GATEWAY
            } else {
                StatusCode::from_u16(e.status).unwrap_or(StatusCode::BAD_REQUEST)
            };
            (status, Json(json!({ "error": e.to_string(), "upstream": e.upstream }))).into_response()
        }
    }
}

/// Query string → tool arguments, with the numeric/boolean fields typed.
fn args_from_query(q: HashMap<String, String>) -> Value {
    let mut args = json!({});
    for (k, v) in q {
        if v.is_empty() {
            continue;
        }
        let typed = match k.as_str() {
            "max_price" => v.parse::<f64>().ok().map(|n| json!(n)),
            "min_gpus" | "limit" | "tail" => v.parse::<u64>().ok().map(|n| json!(n)),
            "available_only" | "raw" | "executors" => v.parse::<bool>().ok().map(|b| json!(b)),
            _ => None,
        };
        args[k] = typed.unwrap_or(json!(v));
    }
    args
}

async fn executors(headers: HeaderMap, Query(q): Query<HashMap<String, String>>) -> Response {
    via_tool("executors", args_from_query(q), header_key(&headers)).await
}

async fn executor(headers: HeaderMap, Path(id): Path<String>) -> Response {
    via_tool("executor", json!({ "executor_id": id }), header_key(&headers)).await
}

async fn stats(headers: HeaderMap) -> Response {
    via_tool("gpu_types", json!({}), header_key(&headers)).await
}

async fn capacity(headers: HeaderMap) -> Response {
    via_tool("capacity", json!({}), header_key(&headers)).await
}

async fn subnet(headers: HeaderMap) -> Response {
    via_tool("subnet", json!({}), header_key(&headers)).await
}

async fn provider(
    headers: HeaderMap,
    Path(hotkey): Path<String>,
    Query(q): Query<HashMap<String, String>>,
) -> Response {
    let mut args = args_from_query(q);
    args["miner_hotkey"] = json!(hotkey);
    via_tool("provider", args, header_key(&headers)).await
}

async fn templates(headers: HeaderMap, Query(q): Query<HashMap<String, String>>) -> Response {
    via_tool("templates", args_from_query(q), header_key(&headers)).await
}

async fn pods(headers: HeaderMap, Query(q): Query<HashMap<String, String>>) -> Response {
    via_tool("pods", args_from_query(q), header_key(&headers)).await
}

async fn pod(headers: HeaderMap, Path(id): Path<String>) -> Response {
    via_tool("pod", json!({ "pod_id": id }), header_key(&headers)).await
}

async fn pod_down(headers: HeaderMap, Path(id): Path<String>) -> Response {
    via_tool("down", json!({ "pod_id": id }), header_key(&headers)).await
}

async fn pod_reboot(headers: HeaderMap, Path(id): Path<String>) -> Response {
    via_tool("reboot", json!({ "pod_id": id }), header_key(&headers)).await
}

async fn pod_logs(
    headers: HeaderMap,
    Path(id): Path<String>,
    Query(q): Query<HashMap<String, String>>,
) -> Response {
    let mut args = args_from_query(q);
    args["pod_id"] = json!(id);
    via_tool("logs", args, header_key(&headers)).await
}

async fn up(headers: HeaderMap, Json(body): Json<Value>) -> Response {
    via_tool("up", body, header_key(&headers)).await
}

async fn me(headers: HeaderMap) -> Response {
    via_tool("me", json!({}), header_key(&headers)).await
}

async fn ssh_keys(headers: HeaderMap) -> Response {
    via_tool("ssh_keys", json!({}), header_key(&headers)).await
}

async fn add_ssh_key(headers: HeaderMap, Json(body): Json<Value>) -> Response {
    via_tool("add_ssh_key", body, header_key(&headers)).await
}

async fn volumes(headers: HeaderMap) -> Response {
    via_tool("volumes", json!({}), header_key(&headers)).await
}

async fn endpoints(headers: HeaderMap, Query(q): Query<HashMap<String, String>>) -> Response {
    via_tool("endpoints", args_from_query(q), header_key(&headers)).await
}

/// The API explorer's playground: any Lium endpoint, the caller's own key.
async fn api(headers: HeaderMap, Json(body): Json<Value>) -> Response {
    via_tool("api", body, header_key(&headers)).await
}

async fn forward(headers: HeaderMap, Json(mut body): Json<Value>) -> Response {
    let action = body
        .as_object_mut()
        .and_then(|o| o.remove("action").or_else(|| o.remove("fn")))
        .and_then(|a| a.as_str().map(String::from))
        .unwrap_or_else(|| "lium_info".into());
    via_tool(&action, body, header_key(&headers)).await
}

async fn tools() -> Json<Value> {
    Json(json!({ "tools": mcp::tool_list() }))
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
        .route("/executors", get(executors))
        .route("/executors/:id", get(executor))
        .route("/stats", get(stats))
        .route("/capacity", get(capacity))
        .route("/subnet", get(subnet))
        .route("/provider/:hotkey", get(provider))
        .route("/templates", get(templates))
        .route("/pods", get(pods))
        .route("/pods/:id", get(pod).delete(pod_down))
        .route("/pods/:id/reboot", post(pod_reboot))
        .route("/pods/:id/logs", get(pod_logs))
        .route("/up", post(up))
        .route("/me", get(me))
        .route("/ssh-keys", get(ssh_keys).post(add_ssh_key))
        .route("/volumes", get(volumes))
        .route("/endpoints", get(endpoints))
        .route("/api", post(api))
        .route("/forward", post(forward))
        .route("/tools", get(tools))
}

pub async fn serve(port: u16) {
    let app = Router::new()
        .route("/", get(root))
        .route("/lium", get(console))
        .route("/lium/", get(console))
        .merge(api_routes())
        .nest("/api/lium", api_routes())
        // Gateway alias, matching the fleet convention for app-served APIs.
        .nest("/lium/_api", api_routes())
        .layer(CorsLayer::permissive());

    let addr = std::net::SocketAddr::from(([0, 0, 0, 0], port));
    println!("lium listening on {addr} (MCP at /mcp, console at /lium, upstream {})", lium::base_url());
    let listener = tokio::net::TcpListener::bind(addr).await.expect("bind");
    axum::serve(listener, app).await.expect("serve");
}
