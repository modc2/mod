//! The hub's own MCP face: one JSON-RPC 2.0 endpoint whose tool list is the
//! union of every enabled upstream's tools, each renamed `{server}__{tool}`.
//! tools/call splits on the first `__` and proxies to the owning upstream.
//! Two native tools ride along: hub_servers and hub_search.

use crate::state::AppState;
use crate::store::Probe;
use crate::upstream;
use serde_json::{json, Value};
use std::sync::Arc;

pub const SERVER_NAME: &str = "mcp-hub";
pub const SERVER_VERSION: &str = env!("CARGO_PKG_VERSION");

fn native_tools() -> Vec<Value> {
    vec![
        json!({
            "name": "hub_servers",
            "description": "List every MCP server aggregated by this hub: id, url, source (fleet|user), status and tool count.",
            "inputSchema": { "type": "object", "properties": {} }
        }),
        json!({
            "name": "hub_search",
            "description": "Search all aggregated tools by name/description. Returns namespaced tool names callable through this hub.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "q": { "type": "string", "description": "Substring to match (case-insensitive)" },
                    "limit": { "type": "integer", "default": 40 }
                },
                "required": ["q"]
            }
        }),
        json!({
            "name": "web_search",
            "description": "Search the web. Returns ranked results with titles, URLs and snippets. Works with no API key configured; a Brave/Tavily/Exa/Serper key is used first when one is present.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "q": { "type": "string", "description": "What to search for" },
                    "count": { "type": "integer", "description": "How many results (1-25)", "default": 8 },
                    "provider": { "type": "string", "description": "Pin one provider: brave | tavily | exa | serper | keenable | duckduckgo" }
                },
                "required": ["q"]
            }
        }),
        json!({
            "name": "web_fetch",
            "description": "Fetch one URL and return its readable text (HTML stripped). Falls back to a reader service when a site blocks the direct request.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "url": { "type": "string", "description": "http(s) URL to read" },
                    "max_chars": { "type": "integer", "description": "Truncate the text at this many characters", "default": 8000 }
                },
                "required": ["url"]
            }
        }),
        json!({
            "name": "hub_catalog",
            "description": "Search the public MCP directories (the official registry, Smithery, and a keyless featured list) for servers this hub could connect to. Read-only: registering one is a POST /servers on the hub's REST API.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "q": { "type": "string", "description": "What the server should do, e.g. 'github issues'" },
                    "registry": { "type": "string", "description": "featured | official | smithery | all", "default": "all" },
                    "limit": { "type": "integer", "default": 20 }
                }
            }
        }),
    ]
}

/// Every upstream tool, renamed into the hub namespace.
pub async fn aggregated_tools(state: &Arc<AppState>) -> Vec<Value> {
    let servers = state.enabled_servers().await;
    let probes = state.probes.read().await.clone();
    let mut out = native_tools();
    for s in servers {
        let Some(p) = probes.get(&s.id) else { continue };
        if !p.ok {
            continue;
        }
        for t in &p.tools {
            let Some(name) = t.get("name").and_then(|n| n.as_str()) else { continue };
            let mut tool = t.clone();
            tool["name"] = json!(format!("{}__{}", s.id, name));
            let desc = tool.get("description").and_then(|d| d.as_str()).unwrap_or("");
            tool["description"] = json!(format!("[{}] {}", s.id, desc));
            out.push(tool);
        }
    }
    out
}

async fn server_rows(state: &Arc<AppState>) -> Vec<Value> {
    let disabled = state.disabled.read().await.clone();
    let probes = state.probes.read().await.clone();
    state
        .all_servers()
        .await
        .into_iter()
        .map(|s| {
            let p = probes.get(&s.id).cloned().unwrap_or_default();
            json!({
                "id": s.id, "name": s.name, "url": s.url, "source": s.source,
                "note": s.note, "added_at": s.added_at,
                "enabled": !disabled.contains(&s.id),
                "auth_headers": s.headers.keys().collect::<Vec<_>>(),
                "probe": probe_json(&p),
            })
        })
        .collect()
}

pub fn probe_json(p: &Probe) -> Value {
    json!({
        "ok": p.ok,
        "protocolVersion": p.protocol_version,
        "serverInfo": p.server_info,
        "toolCount": p.tools.len(),
        "latency_ms": p.latency_ms,
        "checked_at": p.checked_at,
        "error": p.error,
    })
}

/// Full server rows (registry + probe status) — shared by REST and MCP faces.
pub async fn servers_view(state: &Arc<AppState>) -> Vec<Value> {
    server_rows(state).await
}

/// Route a namespaced tool call. Returns the upstream's tools/call result
/// verbatim (already MCP-shaped), or a hub-native result.
pub async fn call_tool(state: &Arc<AppState>, name: &str, args: &Value) -> Result<Value, String> {
    match name {
        "hub_servers" => {
            let rows = server_rows(state).await;
            return Ok(wrap_result(json!({ "servers": rows })));
        }
        "hub_search" => {
            let q = args
                .get("q")
                .and_then(|v| v.as_str())
                .ok_or("hub_search requires `q`")?
                .to_lowercase();
            let limit = args.get("limit").and_then(|v| v.as_u64()).unwrap_or(40) as usize;
            let hits: Vec<Value> = aggregated_tools(state)
                .await
                .into_iter()
                .filter(|t| t.to_string().to_lowercase().contains(&q))
                .take(limit)
                .map(|t| {
                    json!({
                        "name": t.get("name"),
                        "description": t.get("description"),
                    })
                })
                .collect();
            return Ok(wrap_result(json!({ "tools": hits })));
        }
        "web_search" => {
            let q = args
                .get("q")
                .or_else(|| args.get("query"))
                .and_then(|v| v.as_str())
                .ok_or("web_search requires `q`")?;
            let count = args.get("count").and_then(|v| v.as_u64()).unwrap_or(8) as usize;
            let provider = args.get("provider").and_then(|v| v.as_str()).filter(|p| !p.is_empty());
            let res = crate::web::search(q, count, provider).await?;
            return Ok(wrap_result(serde_json::to_value(res).unwrap_or_default()));
        }
        "web_fetch" => {
            let url = args
                .get("url")
                .and_then(|v| v.as_str())
                .ok_or("web_fetch requires `url`")?;
            let max = args.get("max_chars").and_then(|v| v.as_u64()).unwrap_or(8000) as usize;
            let page = crate::web::fetch(url, max).await?;
            return Ok(wrap_result(serde_json::to_value(page).unwrap_or_default()));
        }
        "hub_catalog" => {
            let q = args.get("q").and_then(|v| v.as_str()).unwrap_or("");
            let registry = args.get("registry").and_then(|v| v.as_str()).unwrap_or("all");
            let limit = args.get("limit").and_then(|v| v.as_u64()).unwrap_or(20) as usize;
            let cat = crate::catalog::search(q, registry, limit).await;
            return Ok(wrap_result(serde_json::to_value(cat).unwrap_or_default()));
        }
        _ => {}
    }
    let (server_id, tool) = name
        .split_once("__")
        .ok_or_else(|| format!("unknown tool `{name}` — hub tools are named server__tool"))?;
    let server = state
        .get(server_id)
        .await
        .ok_or_else(|| format!("no aggregated server `{server_id}`"))?;
    if state.disabled.read().await.contains(server_id) {
        return Err(format!("server `{server_id}` is disabled on this hub"));
    }
    upstream::rpc(&server, "tools/call", json!({ "name": tool, "arguments": args })).await
}

fn wrap_result(v: Value) -> Value {
    json!({
        "content": [{ "type": "text", "text": serde_json::to_string(&v).unwrap_or_default() }],
        "structuredContent": v,
        "isError": false
    })
}

fn rpc_result(id: Value, result: Value) -> Value {
    json!({ "jsonrpc": "2.0", "id": id, "result": result })
}

fn rpc_error(id: Value, code: i64, message: &str) -> Value {
    json!({ "jsonrpc": "2.0", "id": id, "error": { "code": code, "message": message } })
}

/// Handle one JSON-RPC message aimed at the hub itself. None = notification.
pub async fn handle_message(state: &Arc<AppState>, msg: &Value) -> Option<Value> {
    handle_message_gated(state, msg, true).await
}

/// As above, but `may_call = false` refuses tools/call while still answering
/// initialize and tools/list — an unauthenticated client can see the
/// catalogue and is told, in the tool result, how to earn the right to run it.
pub async fn handle_message_gated(
    state: &Arc<AppState>,
    msg: &Value,
    may_call: bool,
) -> Option<Value> {
    let method = msg.get("method").and_then(|m| m.as_str()).unwrap_or("");
    let params = msg.get("params").cloned().unwrap_or(json!({}));
    let id = match msg.get("id") {
        Some(id) if !id.is_null() => id.clone(),
        _ => return None,
    };
    Some(match method {
        "initialize" => rpc_result(
            id,
            json!({
                "protocolVersion": params.get("protocolVersion").and_then(|v| v.as_str()).unwrap_or(upstream::PROTOCOL_VERSION),
                "capabilities": { "tools": { "listChanged": true } },
                "serverInfo": { "name": SERVER_NAME, "version": SERVER_VERSION }
            }),
        ),
        "ping" => rpc_result(id, json!({})),
        "tools/list" => rpc_result(id, json!({ "tools": aggregated_tools(state).await })),
        "tools/call" if !may_call => rpc_result(
            id,
            json!({
                "content": [{ "type": "text", "text":
                    "This hub is not open to anonymous callers from outside the host. \
                     Send a hub API key as `Authorization: Bearer mcphub_…` (mint one in the console) \
                     or a wallet session token." }],
                "isError": true
            }),
        ),
        "tools/call" => {
            let name = params.get("name").and_then(|n| n.as_str()).unwrap_or("");
            let args = params.get("arguments").cloned().unwrap_or(json!({}));
            match call_tool(state, name, &args).await {
                Ok(v) => rpc_result(id, v),
                Err(e) => rpc_result(
                    id,
                    json!({ "content": [{ "type": "text", "text": e }], "isError": true }),
                ),
            }
        }
        "resources/list" => rpc_result(id, json!({ "resources": [] })),
        "prompts/list" => rpc_result(id, json!({ "prompts": [] })),
        _ => rpc_error(id, -32601, &format!("method not found: {method}")),
    })
}

/// stdio transport: `mcp-api --stdio` for local MCP clients.
pub async fn run_stdio(state: Arc<AppState>) {
    use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
    let stdin = BufReader::new(tokio::io::stdin());
    let mut stdout = tokio::io::stdout();
    let mut lines = stdin.lines();
    while let Ok(Some(line)) = lines.next_line().await {
        let line = line.trim().to_string();
        if line.is_empty() {
            continue;
        }
        let msg: Value = match serde_json::from_str(&line) {
            Ok(v) => v,
            Err(e) => {
                let err = rpc_error(Value::Null, -32700, &format!("parse error: {e}"));
                let _ = stdout.write_all(format!("{err}\n").as_bytes()).await;
                continue;
            }
        };
        if let Some(resp) = handle_message(&state, &msg).await {
            let _ = stdout.write_all(format!("{resp}\n").as_bytes()).await;
            let _ = stdout.flush().await;
        }
    }
}
