//! HTTP surface: MCP Streamable HTTP at /mcp, REST adapters that dispatch
//! through the same MCP tool layer, and the console.
//!
//! Two ways in, one behaviour. Behind the fleet router the API is reachable at
//! /api/openarena (prefix stripped) and the console at /openarena (prefix
//! kept), so both paths are served here.

use crate::{arena, mcp};
use axum::{
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

fn info() -> Value {
    let mut v = arena::info();
    v["version"] = json!(mcp::SERVER_VERSION);
    v["backend"] = json!("rust-mcp");
    v["mcp_protocol"] = json!(mcp::PROTOCOL_VERSION);
    v["endpoints"] = json!({
        "mcp": "POST /mcp (Streamable HTTP, JSON-RPC 2.0)",
        "tasks": "GET /tasks | POST /tasks | GET /tasks/:id | DELETE /tasks/:id",
        "agents": "GET /agents | POST /agents | DELETE /agents/:id",
        "matches": "POST /matches {task, agents[]} | GET /matches | GET /matches/:id",
        "submit": "POST /submit {task, code, language?, agent?}",
        "leaderboard": "GET /leaderboard",
        "forward": "POST /forward {action, ...args}",
        "tools": "GET /tools",
        "console": "GET /openarena (browser)"
    });
    v["stdio"] = json!("openarena-api --stdio");
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
            let status = if e.starts_with("no task") || e.starts_with("no competitor") || e.starts_with("no match") {
                StatusCode::NOT_FOUND
            } else {
                StatusCode::BAD_REQUEST
            };
            (status, Json(json!({ "error": e }))).into_response()
        }
    }
}

async fn list_tasks(Query(q): Query<HashMap<String, String>>) -> Response {
    via_tool("list_tasks", json!(q)).await
}

async fn get_task(Path(id): Path<String>, Query(q): Query<HashMap<String, String>>) -> Response {
    let reveal = q.get("reveal").map(|v| v == "1" || v == "true").unwrap_or(false);
    via_tool("get_task", json!({ "task": id, "reveal": reveal })).await
}

async fn create_task(Json(body): Json<Value>) -> Response {
    via_tool("create_task", body).await
}

async fn delete_task(Path(id): Path<String>) -> Response {
    via_tool("delete_task", json!({ "task": id })).await
}

async fn list_agents() -> Response {
    via_tool("list_agents", json!({})).await
}

async fn enter_agent(Json(body): Json<Value>) -> Response {
    via_tool("enter_agent", body).await
}

async fn remove_agent(Path(id): Path<String>) -> Response {
    via_tool("remove_agent", json!({ "agent": id })).await
}

async fn run_match(Json(body): Json<Value>) -> Response {
    via_tool("run_match", body).await
}

async fn list_matches(Query(q): Query<HashMap<String, String>>) -> Response {
    let limit: u64 = q.get("limit").and_then(|v| v.parse().ok()).unwrap_or(20);
    via_tool("list_matches", json!({ "limit": limit })).await
}

async fn get_match(Path(id): Path<String>) -> Response {
    via_tool("get_match", json!({ "id": id })).await
}

async fn submit(Json(body): Json<Value>) -> Response {
    via_tool("submit", body).await
}

async fn leaderboard(Query(q): Query<HashMap<String, String>>) -> Response {
    let limit: u64 = q.get("limit").and_then(|v| v.parse().ok()).unwrap_or(20);
    via_tool("leaderboard", json!({ "limit": limit })).await
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
        .route("/tasks", get(list_tasks).post(create_task))
        .route("/tasks/:id", get(get_task).delete(delete_task))
        .route("/agents", get(list_agents).post(enter_agent))
        .route("/agents/:id", axum::routing::delete(remove_agent))
        .route("/matches", get(list_matches).post(run_match))
        .route("/matches/:id", get(get_match))
        .route("/submit", post(submit))
        .route("/leaderboard", get(leaderboard))
        .route("/forward", post(forward))
        .route("/tools", get(tools))
}

pub async fn serve(port: u16) {
    let app = Router::new()
        .route("/", get(root))
        .route("/openarena", get(console))
        .route("/openarena/", get(console))
        .merge(api_routes())
        // The console is served at /openarena in both worlds, so it always
        // calls /api/openarena. Behind the fleet router caddy strips that
        // prefix; standalone on this port nothing does, so the same routes
        // answer there too and one console works in both places.
        .nest("/api/openarena", api_routes())
        .layer(CorsLayer::permissive());

    let addr = std::net::SocketAddr::from(([0, 0, 0, 0], port));
    println!("openarena listening on {addr} (MCP at /mcp, console at /openarena)");
    let listener = tokio::net::TcpListener::bind(addr).await.expect("bind");
    axum::serve(listener, app).await.expect("serve");
}
