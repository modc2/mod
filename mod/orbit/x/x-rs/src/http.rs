//! HTTP surface: MCP Streamable HTTP at /mcp, REST adapters that dispatch
//! through the same MCP tool layer, and an embedded console page.

use crate::{mcp, x};
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
    json!({
        "name": "x",
        "version": mcp::SERVER_VERSION,
        "status": "ok",
        "backend": "rust-mcp",
        "protocol": mcp::PROTOCOL_VERSION,
        "upstream": x::base_url(),
        "auth": { "reads": creds.has_any(), "writes": creds.has_user() },
        "endpoints": {
            "mcp": "POST /mcp (Streamable HTTP, JSON-RPC 2.0)",
            "search": "GET /search?q=",
            "post": "GET /posts/:id | POST /posts {text} | DELETE /posts/:id",
            "user": "GET /users/:handle | GET /users/:handle/timeline",
            "forward": "POST /forward {action, ...args}",
            "tools": "GET /tools",
            "console": "GET / (browser)"
        },
        "stdio": "x-api --stdio"
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

async fn get_post(headers: HeaderMap, Path(id): Path<String>) -> Response {
    via_tool("get_post", json!({ "id": id }), header_key(&headers)).await
}

async fn create_post(headers: HeaderMap, Json(body): Json<Value>) -> Response {
    via_tool("post", body, header_key(&headers)).await
}

async fn delete_post(headers: HeaderMap, Path(id): Path<String>) -> Response {
    via_tool("delete_post", json!({ "id": id }), header_key(&headers)).await
}

async fn get_user(headers: HeaderMap, Path(handle): Path<String>) -> Response {
    via_tool("user", json!({ "username": handle }), header_key(&headers)).await
}

async fn user_timeline(
    headers: HeaderMap,
    Path(handle): Path<String>,
    Query(q): Query<HashMap<String, String>>,
) -> Response {
    let mut args = args_from_query(&q, &[("max_results", "max_results"), ("exclude", "exclude")]);
    args["username"] = json!(handle);
    via_tool("timeline", args, header_key(&headers)).await
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

pub async fn serve(port: u16) {
    let app = Router::new()
        .route("/", get(root))
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
        .route("/search", get(search))
        .route("/posts", post(create_post))
        .route("/posts/:id", get(get_post).delete(delete_post))
        .route("/users/:handle", get(get_user))
        .route("/users/:handle/timeline", get(user_timeline))
        .route("/forward", post(forward))
        .route("/tools", get(tools))
        .layer(CorsLayer::permissive());

    let addr = std::net::SocketAddr::from(([0, 0, 0, 0], port));
    println!("x rust-mcp backend listening on {addr} (MCP at /mcp, console at /)");
    let listener = tokio::net::TcpListener::bind(addr).await.expect("bind");
    axum::serve(listener, app).await.expect("serve");
}
