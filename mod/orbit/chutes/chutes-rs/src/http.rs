//! HTTP surface: MCP Streamable HTTP at /mcp, REST adapters that dispatch
//! through the same MCP tool layer, and an embedded console page.

use crate::{chutes, mcp};
use axum::{
    body::Body,
    extract::{Query, Request},
    http::{header, HeaderMap, StatusCode},
    response::{Html, IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use futures_util::StreamExt;
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
    json!({
        "name": "chutes",
        "version": mcp::SERVER_VERSION,
        "status": "ok",
        "backend": "rust-mcp",
        "protocol": mcp::PROTOCOL_VERSION,
        "upstream": chutes::base_url(),
        "endpoints": {
            "mcp": "POST /mcp (Streamable HTTP, JSON-RPC 2.0)",
            "chat": "POST /chat",
            "images": "POST /images",
            "models": "GET /models?q=",
            "forward": "POST /forward {action, ...args}",
            "tools": "GET /tools",
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

async fn mcp_endpoint(headers: HeaderMap, Json(msg): Json<Value>) -> Response {
    let key = header_key(&headers);
    match mcp::handle_message(&msg, key.as_deref()).await {
        Some(resp) => Json(resp).into_response(),
        None => StatusCode::ACCEPTED.into_response(),
    }
}

/// REST → MCP tool adapter; errors surface as {error} with upstream-ish status.
async fn via_tool(name: &str, args: Value, key: Option<String>) -> Response {
    match mcp::call_tool(name, &args, key.as_deref()).await {
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
    let key = header_key(&headers);
    let stream = body.get("stream").and_then(|s| s.as_bool()).unwrap_or(false);
    if !stream {
        return via_tool("chat", body, key).await;
    }
    // Streaming bypasses tools/call (MCP tool results are unary) — SSE pass-through.
    let resolved = chutes::resolve_key(key.as_deref());
    if body.get("model").is_none() {
        body["model"] = json!(chutes::default_model());
    }
    body["stream"] = json!(true);
    match chutes::chat_stream_raw(&resolved, &body).await {
        Ok(resp) => {
            let stream = resp.bytes_stream().map(|c| c.map_err(std::io::Error::other));
            Response::builder()
                .header(header::CONTENT_TYPE, "text/event-stream")
                .header(header::CACHE_CONTROL, "no-cache")
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
    via_tool("generate_image", body, header_key(&headers)).await
}

async fn models(headers: HeaderMap, Query(q): Query<HashMap<String, String>>) -> Response {
    let mut args = json!({});
    if let Some(search) = q.get("q").or_else(|| q.get("search")) {
        if !search.is_empty() {
            args["search"] = json!(search);
        }
    }
    via_tool("models", args, header_key(&headers)).await
}

async fn forward(headers: HeaderMap, Json(mut body): Json<Value>) -> Response {
    let action = body
        .as_object_mut()
        .and_then(|o| o.remove("action").or_else(|| o.remove("fn")))
        .and_then(|a| a.as_str().map(String::from))
        .unwrap_or_else(|| "chat".into());
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
        .route("/mcp", post(mcp_endpoint).get(|| async {
            (StatusCode::METHOD_NOT_ALLOWED, Json(json!({ "error": "POST JSON-RPC messages to /mcp" })))
        }))
        .route("/chat", post(chat))
        .route("/images", post(images))
        .route("/models", get(models))
        .route("/forward", post(forward))
        .route("/tools", get(tools))
        .layer(CorsLayer::permissive());

    let addr = std::net::SocketAddr::from(([0, 0, 0, 0], port));
    println!("chutes rust-mcp backend listening on {addr} (MCP at /mcp, console at /)");
    let listener = tokio::net::TcpListener::bind(addr).await.expect("bind");
    axum::serve(listener, app).await.expect("serve");
}
