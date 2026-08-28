//! HTTP surface: MCP Streamable HTTP at /mcp, REST adapters that dispatch
//! through the same MCP tool layer, and an embedded console page.
//!
//! Two ways in, one behaviour. Behind the fleet router the API is reachable at
//! /api/targon (prefix stripped) and the console at /targon (prefix kept), so
//! both paths are served here and one console works in either place.

use crate::{chain, mcp, targon};
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
            "chain": "GET /chain, GET /chain/account?address=, POST /chain/prepare, POST /chain/submit",
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

async fn console() -> Html<&'static str> {
    Html(CONSOLE_HTML)
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

async fn reboot_workload(Path(uid): Path<String>, h: HeaderMap, Query(q): Q) -> Response {
    uid_tool("reboot_workload", "workload_uid", uid, h, q).await
}

/// `command` arrives as one query string or a JSON body; the tool splits either.
async fn exec_workload(Path(uid): Path<String>, headers: HeaderMap, Query(q): Q, body: Option<Json<Value>>) -> Response {
    let args = match body {
        Some(Json(b)) if b.is_object() => b,
        _ => query_args(q),
    };
    via_tool("workload_exec", with(args, "workload_uid", uid), header_key(&headers)).await
}

async fn vm_images(h: HeaderMap, q: Q) -> Response {
    get_tool("vm_images", h, q).await
}

async fn volumes(h: HeaderMap, q: Q) -> Response {
    get_tool("list_volumes", h, q).await
}

async fn create_volume(headers: HeaderMap, Json(body): Json<Value>) -> Response {
    via_tool("create_volume", body, header_key(&headers)).await
}

async fn volume(Path(uid): Path<String>, h: HeaderMap, Query(q): Q) -> Response {
    uid_tool("get_volume", "volume_uid", uid, h, q).await
}

async fn remove_volume(Path(uid): Path<String>, h: HeaderMap, Query(q): Q) -> Response {
    uid_tool("delete_volume", "volume_uid", uid, h, q).await
}

async fn ssh_keys(h: HeaderMap, q: Q) -> Response {
    get_tool("list_ssh_keys", h, q).await
}

async fn create_ssh_key(headers: HeaderMap, Json(body): Json<Value>) -> Response {
    via_tool("create_ssh_key", body, header_key(&headers)).await
}

async fn remove_ssh_key(Path(uid): Path<String>, h: HeaderMap, Query(q): Q) -> Response {
    uid_tool("delete_ssh_key", "ssh_key_uid", uid, h, q).await
}

async fn templates(h: HeaderMap, q: Q) -> Response {
    get_tool("list_templates", h, q).await
}

async fn create_template(headers: HeaderMap, Json(body): Json<Value>) -> Response {
    via_tool("create_template", body, header_key(&headers)).await
}

async fn template(Path(uid): Path<String>, h: HeaderMap, Query(q): Q) -> Response {
    uid_tool("get_template", "template_uid", uid, h, q).await
}

async fn remove_template(Path(uid): Path<String>, h: HeaderMap, Query(q): Q) -> Response {
    uid_tool("delete_template", "template_uid", uid, h, q).await
}

async fn api_keys(h: HeaderMap, q: Q) -> Response {
    get_tool("list_api_keys", h, q).await
}

async fn create_api_key(headers: HeaderMap, Json(body): Json<Value>) -> Response {
    via_tool("create_api_key", body, header_key(&headers)).await
}

async fn remove_api_key(Path(uid): Path<String>, h: HeaderMap, Query(q): Q) -> Response {
    uid_tool("delete_api_key", "key_uid", uid, h, q).await
}

// ── Chain (the console's in-browser wallet) ──────────────────────
//
// Reads and encoding only. The signing key stays in the browser extension —
// nothing here ever holds one, so these routes need no auth of their own.

fn chain_result(r: Result<Value, String>) -> Response {
    match r {
        Ok(v) => Json(v).into_response(),
        Err(e) => (StatusCode::BAD_REQUEST, Json(json!({ "error": e }))).into_response(),
    }
}

async fn chain_account(Query(q): Q) -> Response {
    match q.get("address") {
        Some(a) => chain_result(chain::account(a).await),
        None => (StatusCode::BAD_REQUEST, Json(json!({ "error": "address is required" }))).into_response(),
    }
}

/// Build the top-up extrinsic and the payload a wallet signs.
async fn chain_prepare(Json(body): Json<Value>) -> Response {
    let s = |k: &str| body.get(k).and_then(|v| v.as_str()).unwrap_or("").to_string();
    let tao = body
        .get("tao")
        .or_else(|| body.get("amount"))
        .and_then(|v| v.as_f64().or_else(|| v.as_str().and_then(|s| s.parse().ok())))
        .unwrap_or(0.0);
    chain_result(chain::prepare(&s("from"), &s("to"), tao).await)
}

async fn chain_submit(Json(body): Json<Value>) -> Response {
    let sig = body.get("signature").and_then(|v| v.as_str()).unwrap_or("");
    match body.get("payload") {
        Some(p) => chain_result(chain::submit(p, sig).await),
        None => (StatusCode::BAD_REQUEST, Json(json!({ "error": "payload is required" }))).into_response(),
    }
}

async fn chain_info() -> Json<Value> {
    Json(json!({
        "network": "bittensor",
        "rpc": chain::rpc_url(),
        "ss58_format": chain::SS58_FORMAT,
        "decimals": chain::DECIMALS,
        "symbol": "TAO",
        "call": "Balances.transfer_keep_alive",
        "custody": "none — the browser extension holds the key and signs; this server only encodes and relays",
    }))
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
        .route("/tools", get(tools))
        .route("/forward", post(forward))
        .route("/inventory", get(inventory))
        .route("/cheapest", get(cheapest))
        .route("/credits", get(credits))
        .route("/wallet", get(wallet))
        .route("/api-keys", get(api_keys).post(create_api_key))
        .route("/api-keys/:uid", axum::routing::delete(remove_api_key))
        .route("/rent", post(rent))
        .route("/workloads", get(workloads).post(create_workload))
        .route("/workloads/:uid", get(workload).delete(remove_workload))
        .route("/workloads/:uid/deploy", post(deploy_workload))
        .route("/workloads/:uid/suspend", post(suspend_workload))
        .route("/workloads/:uid/reboot", post(reboot_workload))
        .route("/workloads/:uid/exec", post(exec_workload))
        .route("/workloads/:uid/state", get(workload_state))
        .route("/workloads/:uid/logs", get(workload_logs))
        .route("/workloads/:uid/events", get(workload_events))
        .route("/vm-images", get(vm_images))
        .route("/volumes", get(volumes).post(create_volume))
        .route("/volumes/:uid", get(volume).delete(remove_volume))
        .route("/ssh-keys", get(ssh_keys).post(create_ssh_key))
        .route("/ssh-keys/:uid", axum::routing::delete(remove_ssh_key))
        .route("/templates", get(templates).post(create_template))
        .route("/templates/:uid", get(template).delete(remove_template))
        .route("/chain", get(chain_info))
        .route("/chain/account", get(chain_account))
        .route("/chain/prepare", post(chain_prepare))
        .route("/chain/submit", post(chain_submit))
}

pub async fn serve(port: u16) {
    let app = Router::new()
        .route("/", get(root))
        .route("/targon", get(console))
        .route("/targon/", get(console))
        .merge(api_routes())
        .nest("/api/targon", api_routes())
        // Gateway alias, matching the fleet convention for app-served APIs.
        .nest("/targon/_api", api_routes())
        .layer(CorsLayer::permissive());

    let addr = std::net::SocketAddr::from(([0, 0, 0, 0], port));
    println!("targon rust-mcp backend listening on {addr} (MCP at /mcp, console at /targon)");
    let listener = tokio::net::TcpListener::bind(addr).await.expect("bind");
    axum::serve(listener, app).await.expect("serve");
}
