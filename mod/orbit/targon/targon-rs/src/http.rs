//! HTTP surface: MCP Streamable HTTP at /mcp, REST adapters that dispatch
//! through the same MCP tool layer, and an embedded console page.

use crate::{mcp, targon};
use axum::{
    extract::{Path, Query},
    http::{header, HeaderMap, StatusCode},
    response::{Html, IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use serde_json::{json, Value};
use std::collections::HashMap;
use tower_http::cors::CorsLayer;

const CONSOLE_HTML: &str = include_str!("console.html");

type Q = Query<HashMap<String, String>>;

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

/// Query strings arrive as strings; give booleans and numbers their JSON types
/// so tool arguments look the same however they were sent.
fn query_args(q: HashMap<String, String>) -> Value {
    let mut args = serde_json::Map::new();
    for (k, v) in q {
        let parsed = match v.as_str() {
            "true" => json!(true),
            "false" => json!(false),
            s => s.parse::<f64>().map(|n| json!(n)).unwrap_or(json!(s)),
        };
        args.insert(k, parsed);
    }
    Value::Object(args)
}

fn with(mut args: Value, key: &str, value: String) -> Value {
    args[key] = json!(value);
    args
}

fn info() -> Value {
    json!({
        "name": "targon",
        "version": mcp::SERVER_VERSION,
        "status": "ok",
        "backend": "rust-mcp",
        "protocol": mcp::PROTOCOL_VERSION,
        "upstream": targon::base_url(),
        "network": "Bittensor subnet 4 (Targon) — decentralized GPU compute",
        "tools": mcp::tool_list().as_array().map(|t| t.len()).unwrap_or(0),
        "endpoints": {
            "mcp": "POST /mcp (Streamable HTTP, JSON-RPC 2.0)",
            "tools": "GET /tools",
            "forward": "POST /forward {action, ...args}",
            "inventory": "GET /inventory?gpu=true",
            "cheapest": "GET /cheapest?gpu_type=H200",
            "workloads": "GET|POST /workloads, GET|DELETE /workloads/:uid",
            "workload": "POST /workloads/:uid/deploy | /suspend, GET /workloads/:uid/state | /logs | /events",
            "rent": "POST /rent {name, image, resource_name?}",
            "volumes": "GET|POST /volumes",
            "ssh_keys": "GET|POST /ssh-keys",
            "templates": "GET /templates",
            "account": "GET /credits, GET /wallet",
            "console": "GET / (browser)"
        },
        "auth": "Bearer / x-api-key header, TARGON_API_KEY, or ~/.mod/targon/api_key",
        "stdio": "targon-api --stdio"
    })
}

async fn root(headers: HeaderMap) -> Response {
    let wants_html = headers
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
    match mcp::handle_message(&msg, header_key(&headers).as_deref()).await {
        Some(resp) => Json(resp).into_response(),
        None => StatusCode::ACCEPTED.into_response(),
    }
}

/// REST → MCP tool adapter. Upstream complaints keep their own status code.
async fn via_tool(name: &str, args: Value, key: Option<String>) -> Response {
    match mcp::call_tool(name, &args, key.as_deref()).await {
        Ok(v) => Json(v).into_response(),
        Err(e) => {
            let status = e
                .strip_prefix("upstream ")
                .and_then(|rest| rest.split(':').next())
                .and_then(|c| c.parse::<u16>().ok())
                .and_then(|c| StatusCode::from_u16(c).ok())
                .unwrap_or(StatusCode::BAD_REQUEST);
            (status, Json(json!({ "error": e }))).into_response()
        }
    }
}

// ── REST adapters ────────────────────────────────────────────────

async fn get_tool(name: &'static str, headers: HeaderMap, Query(q): Q) -> Response {
    via_tool(name, query_args(q), header_key(&headers)).await
}

async fn uid_tool(name: &'static str, param: &str, uid: String, headers: HeaderMap, q: HashMap<String, String>) -> Response {
    via_tool(name, with(query_args(q), param, uid), header_key(&headers)).await
}

async fn inventory(h: HeaderMap, q: Q) -> Response {
    get_tool("inventory", h, q).await
}

async fn cheapest(h: HeaderMap, q: Q) -> Response {
    get_tool("cheapest", h, q).await
}

async fn credits(h: HeaderMap, q: Q) -> Response {
    get_tool("credits", h, q).await
}

async fn wallet(h: HeaderMap, q: Q) -> Response {
    get_tool("wallet", h, q).await
}

async fn workloads(h: HeaderMap, q: Q) -> Response {
    get_tool("list_workloads", h, q).await
}

async fn create_workload(headers: HeaderMap, Json(body): Json<Value>) -> Response {
    via_tool("create_workload", body, header_key(&headers)).await
}

async fn rent(headers: HeaderMap, Json(body): Json<Value>) -> Response {
    via_tool("rent", body, header_key(&headers)).await
}

async fn workload(Path(uid): Path<String>, h: HeaderMap, Query(q): Q) -> Response {
    uid_tool("get_workload", "workload_uid", uid, h, q).await
}

async fn remove_workload(Path(uid): Path<String>, h: HeaderMap, Query(q): Q) -> Response {
    uid_tool("delete_workload", "workload_uid", uid, h, q).await
}

async fn deploy_workload(Path(uid): Path<String>, h: HeaderMap, Query(q): Q) -> Response {
    uid_tool("deploy_workload", "workload_uid", uid, h, q).await
}

async fn suspend_workload(Path(uid): Path<String>, h: HeaderMap, Query(q): Q) -> Response {
    uid_tool("suspend_workload", "workload_uid", uid, h, q).await
}

async fn workload_state(Path(uid): Path<String>, h: HeaderMap, Query(q): Q) -> Response {
    uid_tool("workload_state", "workload_uid", uid, h, q).await
}

async fn workload_logs(Path(uid): Path<String>, h: HeaderMap, Query(q): Q) -> Response {
    uid_tool("workload_logs", "workload_uid", uid, h, q).await
}

async fn workload_events(Path(uid): Path<String>, h: HeaderMap, Query(q): Q) -> Response {
    uid_tool("workload_events", "workload_uid", uid, h, q).await
}

async fn volumes(h: HeaderMap, q: Q) -> Response {
    get_tool("list_volumes", h, q).await
}

async fn create_volume(headers: HeaderMap, Json(body): Json<Value>) -> Response {
    via_tool("create_volume", body, header_key(&headers)).await
}

async fn ssh_keys(h: HeaderMap, q: Q) -> Response {
    get_tool("list_ssh_keys", h, q).await
}

async fn create_ssh_key(headers: HeaderMap, Json(body): Json<Value>) -> Response {
    via_tool("create_ssh_key", body, header_key(&headers)).await
}

async fn templates(h: HeaderMap, q: Q) -> Response {
    get_tool("list_templates", h, q).await
}

async fn forward(headers: HeaderMap, Json(mut body): Json<Value>) -> Response {
    let action = body
        .as_object_mut()
        .and_then(|o| o.remove("action").or_else(|| o.remove("fn")))
        .and_then(|a| a.as_str().map(String::from))
        .unwrap_or_else(|| "inventory".into());
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
        .route("/tools", get(tools))
        .route("/forward", post(forward))
        .route("/inventory", get(inventory))
        .route("/cheapest", get(cheapest))
        .route("/credits", get(credits))
        .route("/wallet", get(wallet))
        .route("/rent", post(rent))
        .route("/workloads", get(workloads).post(create_workload))
        .route("/workloads/:uid", get(workload).delete(remove_workload))
        .route("/workloads/:uid/deploy", post(deploy_workload))
        .route("/workloads/:uid/suspend", post(suspend_workload))
        .route("/workloads/:uid/state", get(workload_state))
        .route("/workloads/:uid/logs", get(workload_logs))
        .route("/workloads/:uid/events", get(workload_events))
        .route("/volumes", get(volumes).post(create_volume))
        .route("/ssh-keys", get(ssh_keys).post(create_ssh_key))
        .route("/templates", get(templates))
        .layer(CorsLayer::permissive());

    let addr = std::net::SocketAddr::from(([0, 0, 0, 0], port));
    println!("targon rust-mcp backend listening on {addr} (MCP at /mcp, console at /)");
    let listener = tokio::net::TcpListener::bind(addr).await.expect("bind");
    axum::serve(listener, app).await.expect("serve");
}
