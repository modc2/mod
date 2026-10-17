//! The hub's own MCP face: one JSON-RPC 2.0 endpoint whose tool list is the
//! union of every enabled upstream's tools, each renamed `{server}__{tool}`.
//! tools/call splits on the first `__` and proxies to the owning upstream.
//! Native tools ride along: hub_servers, hub_search, hub_catalog, hub_hubs,
//! hub_connect, web_search and web_fetch.

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
            "description": "Search for MCP servers across every hub this one knows: the public directories (official registry, Smithery, Glama and PulseMCP when keyed, a keyless featured list), the fleet's internet-wide index (mcpscan — rows carry a live/auth/down status), and every peer mod hub added by URL. Read-only; hub_connect registers a row.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "q": { "type": "string", "description": "What the server should do, e.g. 'github issues'" },
                    "registry": { "type": "string", "description": "all | featured | official | smithery | glama | pulsemcp | an index id (mcpscan, mcpscan:docker) | a peer hub id — hub_hubs lists them", "default": "all" },
                    "limit": { "type": "integer", "default": 20 }
                }
            }
        }),
        json!({
            "name": "hub_hubs",
            "description": "List every hub type this hub can see and connect to, with what each holds: this mod hub itself (kind mod), peer mod hubs, the fleet's internet-wide index (kind index) and the public directories (kind directory) — servers/live/tools counts, whether a key is needed, and the registry= value that searches it.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "refresh": { "type": "boolean", "description": "Re-probe every hub now instead of using the cached view", "default": false }
                }
            }
        }),
        json!({
            "name": "hub_connect",
            "description": "Register an MCP server on this hub so its tools become callable as id__tool. Pass a server URL, or `hub` = a hub id from hub_hubs (mod/index kind) to connect that whole hub — its tools then arrive nested as hub__server__tool. The endpoint is probed first; a server that will not shake hands is refused unless force is true. Needs registry-edit rights (owner/editor wallet or a caller on the hub's host).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "url": { "type": "string", "description": "Streamable HTTP MCP endpoint" },
                    "hub": { "type": "string", "description": "A hub id from hub_hubs, instead of url" },
                    "id": { "type": "string", "description": "Tool-name prefix (defaults to the host or hub id)" },
                    "name": { "type": "string" },
                    "headers": { "type": "object", "additionalProperties": { "type": "string" }, "description": "Sent on every upstream request, e.g. Authorization" },
                    "note": { "type": "string" },
                    "force": { "type": "boolean", "default": false }
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

/// Register a server from a request body — shared by POST /servers and the
/// hub_connect tool. Probes first; the caller has already been gated.
pub async fn register_server(state: &Arc<AppState>, body: &Value) -> Result<Value, String> {
    let url = body.get("url").and_then(|v| v.as_str()).ok_or("`url` is required")?.trim().to_string();
    if !url.starts_with("http://") && !url.starts_with("https://") {
        return Err("`url` must be http(s)".into());
    }
    let fallback_id = url
        .split("//")
        .nth(1)
        .unwrap_or("server")
        .split(['/', ':'])
        .next()
        .unwrap_or("server");
    let id = crate::store::clean_id(body.get("id").and_then(|v| v.as_str()).unwrap_or(fallback_id));
    let custom_headers: std::collections::HashMap<String, String> = body
        .get("headers")
        .and_then(|h| h.as_object())
        .map(|o| o.iter().filter_map(|(k, v)| v.as_str().map(|s| (k.clone(), s.to_string()))).collect())
        .unwrap_or_default();
    let entry = crate::store::ServerEntry {
        id: id.clone(),
        name: body.get("name").and_then(|v| v.as_str()).unwrap_or(&id).to_string(),
        url,
        headers: custom_headers,
        source: "user".into(),
        note: body.get("note").and_then(|v| v.as_str()).unwrap_or("").chars().take(300).collect(),
        added_at: crate::store::now(),
        origin: body.get("origin").and_then(|v| v.as_str()).unwrap_or("").to_string(),
        ..Default::default()
    };
    let probe = upstream::probe(&entry).await;
    if !probe.ok && body.get("force").and_then(|v| v.as_bool()) != Some(true) {
        return Err(format!("probe failed ({}). Pass force:true to register anyway.", probe.error));
    }
    {
        let mut user = state.user.write().await;
        user.retain(|s| s.id != entry.id);
        user.push(entry.clone());
    }
    state.disabled.write().await.remove(&id);
    state.persist().await;
    state.set_probe(&id, probe.clone()).await;
    Ok(json!({ "added": entry, "probe": probe_json(&probe) }))
}

/// Route a namespaced tool call. Returns the upstream's tools/call result
/// verbatim (already MCP-shaped), or a hub-native result. `may_write` is the
/// registry-edit privilege, which hub_connect needs on top of the call gate.
pub async fn call_tool(state: &Arc<AppState>, name: &str, args: &Value, may_write: bool) -> Result<Value, String> {
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
            let cat = crate::catalog::search(state, q, registry, limit).await;
            return Ok(wrap_result(serde_json::to_value(cat).unwrap_or_default()));
        }
        "hub_hubs" => {
            let refresh = args.get("refresh").and_then(|v| v.as_bool()).unwrap_or(false);
            let max_age = if refresh { 0 } else { crate::hubs::CACHE_SECS };
            let mut view = crate::hubs::view(state, &crate::store::public_url(), max_age).await;
            view["manifest"] = crate::hubs::manifest(state, &crate::store::public_url()).await;
            return Ok(wrap_result(view));
        }
        "hub_connect" => {
            if !may_write {
                return Err("hub_connect edits the registry: it needs an owner/editor wallet token or a caller on the hub's host (an API key only buys tool calls)".into());
            }
            let mut body = args.clone();
            if let Some(hub_id) = args.get("hub").and_then(|v| v.as_str()).filter(|h| !h.is_empty()) {
                let hubs = crate::hubs::known(state, &crate::store::public_url()).await;
                let h = hubs.iter().find(|h| h.id == hub_id).ok_or_else(|| format!("no hub `{hub_id}` — hub_hubs lists them"))?;
                if h.is_self {
                    return Err("that is this hub".into());
                }
                if h.mcp.is_empty() {
                    return Err(format!("`{hub_id}` is a {} — it has no MCP endpoint of its own; search it with hub_catalog and connect rows one at a time", h.kind));
                }
                body["url"] = json!(h.mcp);
                if body.get("id").is_none() {
                    body["id"] = json!(h.id);
                }
                if body.get("name").is_none() {
                    body["name"] = json!(h.name);
                }
                if body.get("note").is_none() {
                    body["note"] = json!(format!("{} hub — tools arrive as {}__server__tool", h.kind, h.id));
                }
                if body.get("headers").is_none() {
                    let peers = state.peers.read().await;
                    if let Some(p) = peers.iter().find(|p| p.id == h.id) {
                        body["headers"] = json!(p.headers);
                    }
                }
                body["origin"] = json!(format!("hub:{}", h.id));
            }
            let added = register_server(state, &body).await?;
            return Ok(wrap_result(added));
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
    handle_message_gated(state, msg, true, true).await
}

/// As above, but `may_call = false` refuses tools/call while still answering
/// initialize and tools/list — an unauthenticated client can see the
/// catalogue and is told, in the tool result, how to earn the right to run it.
/// `may_write` is the narrower registry-edit right that hub_connect needs.
pub async fn handle_message_gated(
    state: &Arc<AppState>,
    msg: &Value,
    may_call: bool,
    may_write: bool,
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
            match call_tool(state, name, &args, may_write).await {
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
