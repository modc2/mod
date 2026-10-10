//! mcpscan's own MCP face.
//!
//! Twenty thousand servers is roughly two hundred thousand tools, and no
//! client can hold that in a tools/list. So this module aggregates the
//! internet the only way that actually works at that size: six tools that
//! search the index, read one server's real schemas, and call any tool on any
//! indexed server on demand. Point a client here and it can reach anything
//! that has ever been published — without loading it first.

use crate::index::{Index, SearchParams};
use crate::{prober, store, upstream};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::sync::Arc;

pub const SERVER_NAME: &str = "mcpscan";
pub const SERVER_VERSION: &str = env!("CARGO_PKG_VERSION");

pub fn tools() -> Vec<Value> {
    vec![
        json!({
            "name": "mcp_find",
            "description": "Search every MCP server on the internet that this index knows about — the official registry, Smithery, Docker's registry, GitHub, plus endpoints found by probing. Matches names, descriptions and tool names. Returns each server's endpoint, live status and tool names.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "q": { "type": "string", "description": "What the server should do, e.g. 'github issues' or 'postgres'" },
                    "status": { "type": "string", "description": "live (shook hands anonymously) | auth (endpoint exists, needs a key) | down | error | unknown" },
                    "source": { "type": "string", "description": "Only servers listed by one directory: official | smithery | docker | github | pulsemcp | glama | hunt" },
                    "limit": { "type": "integer", "default": 20 }
                }
            }
        }),
        json!({
            "name": "mcp_server",
            "description": "Everything the index holds about one server: endpoint, directories that list it, install packages, last probe and its tool list.",
            "inputSchema": {
                "type": "object",
                "properties": { "id": { "type": "string", "description": "Index id from mcp_find" } },
                "required": ["id"]
            }
        }),
        json!({
            "name": "mcp_tools",
            "description": "Live tools/list against one indexed server — full input schemas, fetched now rather than from the index.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "id": { "type": "string", "description": "Index id" },
                    "url": { "type": "string", "description": "…or an endpoint URL directly" },
                    "headers": { "type": "object", "description": "Auth headers to send, e.g. {\"Authorization\": \"Bearer …\"}" }
                }
            }
        }),
        json!({
            "name": "mcp_call",
            "description": "Call any tool on any indexed MCP server. The index opens a fresh session, runs the tool and returns the upstream result verbatim — nothing has to be registered first.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "server": { "type": "string", "description": "Index id (or an endpoint URL)" },
                    "tool": { "type": "string", "description": "Tool name on that server" },
                    "args": { "type": "object", "description": "Tool arguments" },
                    "headers": { "type": "object", "description": "Auth headers for that server, if it needs any" }
                },
                "required": ["server", "tool"]
            }
        }),
        json!({
            "name": "mcp_probe",
            "description": "Handshake with an MCP endpoint right now and add it to the index. Returns protocol version, server info and the tool list.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "url": { "type": "string" },
                    "headers": { "type": "object" }
                },
                "required": ["url"]
            }
        }),
        json!({
            "name": "mcp_stats",
            "description": "Index size and scraper state: servers indexed, endpoints known, how many are live, per-directory counts, and what the crawler has been doing.",
            "inputSchema": { "type": "object", "properties": {} }
        }),
    ]
}

fn wrap(v: Value) -> Value {
    json!({
        "content": [{ "type": "text", "text": serde_json::to_string(&v).unwrap_or_default() }],
        "structuredContent": v,
        "isError": false
    })
}

fn headers_of(args: &Value) -> HashMap<String, String> {
    args.get("headers")
        .and_then(|h| h.as_object())
        .map(|o| {
            o.iter().filter_map(|(k, v)| v.as_str().map(|s| (k.clone(), s.to_string()))).collect()
        })
        .unwrap_or_default()
}

/// Resolve "index id, or a raw URL" to (id, url).
async fn resolve(index: &Arc<Index>, who: &str) -> Result<(String, String), String> {
    if who.starts_with("http://") || who.starts_with("https://") {
        let id = index
            .by_url
            .read()
            .await
            .get(&store::canon_url(who))
            .cloned()
            .unwrap_or_else(|| store::slug(who));
        return Ok((id, who.to_string()));
    }
    let e = index.get(who).await.ok_or_else(|| format!("no indexed server `{who}`"))?;
    if !e.probeable() {
        return Err(format!(
            "`{who}` is indexed but has no HTTP endpoint (install it locally: {})",
            if e.packages.is_empty() { "no package published".into() } else { e.packages.join(", ") }
        ));
    }
    Ok((e.id, e.url))
}

pub async fn call_tool(index: &Arc<Index>, name: &str, args: &Value) -> Result<Value, String> {
    match name {
        "mcp_find" => {
            let p = SearchParams {
                q: args.get("q").and_then(|v| v.as_str()).unwrap_or("").to_string(),
                status: args.get("status").and_then(|v| v.as_str()).unwrap_or("").to_string(),
                source: args.get("source").and_then(|v| v.as_str()).unwrap_or("").to_string(),
                limit: args.get("limit").and_then(|v| v.as_u64()).unwrap_or(20).clamp(1, 200)
                    as usize,
                ..Default::default()
            };
            let (total, rows) = index.search(&p).await;
            Ok(wrap(json!({ "matches": total, "showing": rows.len(), "servers": rows })))
        }
        "mcp_server" => {
            let id = args.get("id").and_then(|v| v.as_str()).ok_or("mcp_server requires `id`")?;
            let e = index.get(id).await.ok_or_else(|| format!("no indexed server `{id}`"))?;
            Ok(wrap(e.row(true)))
        }
        "mcp_tools" => {
            let who = args
                .get("id")
                .or_else(|| args.get("url"))
                .and_then(|v| v.as_str())
                .ok_or("mcp_tools requires `id` or `url`")?;
            let (id, url) = resolve(index, who).await?;
            let out = upstream::probe(&url, &headers_of(args), 20).await;
            index.apply_probe(&id, &out).await;
            if out.status != "live" {
                return Err(format!("{url} → {} ({})", out.status, out.error));
            }
            Ok(wrap(json!({ "server": id, "url": url, "tools": out.raw_tools })))
        }
        "mcp_call" => {
            let who = args.get("server").and_then(|v| v.as_str()).ok_or("mcp_call requires `server`")?;
            let tool = args.get("tool").and_then(|v| v.as_str()).ok_or("mcp_call requires `tool`")?;
            let (_, url) = resolve(index, who).await?;
            let call_args = args.get("args").cloned().unwrap_or(json!({}));
            let out = upstream::call(&url, &headers_of(args), tool, &call_args, 60).await?;
            Ok(out)
        }
        "mcp_probe" => {
            let url = args.get("url").and_then(|v| v.as_str()).ok_or("mcp_probe requires `url`")?;
            let (id, out) = prober::probe_url(index, url, &headers_of(args), "probe").await;
            Ok(wrap(json!({
                "id": id, "url": url, "status": out.status,
                "protocolVersion": out.protocol_version, "serverInfo": out.server_info,
                "latency_ms": out.latency_ms, "error": out.error,
                "tools": out.raw_tools,
            })))
        }
        "mcp_stats" => Ok(wrap(index.stats().await)),
        other => Err(format!("unknown tool `{other}`")),
    }
}

fn rpc_result(id: Value, result: Value) -> Value {
    json!({ "jsonrpc": "2.0", "id": id, "result": result })
}

fn rpc_error(id: Value, code: i64, message: &str) -> Value {
    json!({ "jsonrpc": "2.0", "id": id, "error": { "code": code, "message": message } })
}

/// One JSON-RPC message. None means it was a notification.
pub async fn handle_message(index: &Arc<Index>, msg: &Value) -> Option<Value> {
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
                "capabilities": { "tools": { "listChanged": false } },
                "serverInfo": { "name": SERVER_NAME, "version": SERVER_VERSION }
            }),
        ),
        "ping" => rpc_result(id, json!({})),
        "tools/list" => rpc_result(id, json!({ "tools": tools() })),
        "tools/call" => {
            let name = params.get("name").and_then(|n| n.as_str()).unwrap_or("");
            let args = params.get("arguments").cloned().unwrap_or(json!({}));
            match call_tool(index, name, &args).await {
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

/// `mcpscan-api --stdio` for clients that speak stdio only.
pub async fn run_stdio(index: Arc<Index>) {
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
        if let Some(resp) = handle_message(&index, &msg).await {
            let _ = stdout.write_all(format!("{resp}\n").as_bytes()).await;
            let _ = stdout.flush().await;
        }
    }
}
