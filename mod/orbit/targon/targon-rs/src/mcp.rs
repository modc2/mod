//! MCP (Model Context Protocol) server core — JSON-RPC 2.0 dispatch shared by
//! the Streamable HTTP endpoint (/mcp) and stdio mode (--stdio).
//! Every REST route also funnels through `call_tool`, so the MCP tool layer
//! is the single backend.

use crate::{targon, tools};
use serde_json::{json, Value};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};

pub const PROTOCOL_VERSION: &str = "2025-06-18";
pub const SERVER_NAME: &str = "targon";
pub const SERVER_VERSION: &str = env!("CARGO_PKG_VERSION");

pub fn tool_list() -> Value {
    tools::tool_list()
}

/// Execute a tool. `header_key` is a per-request API key override (HTTP
/// x-api-key / Authorization); arguments may also carry an `api_key` field.
pub async fn call_tool(name: &str, args: &Value, header_key: Option<&str>) -> Result<Value, String> {
    let key = targon::resolve_key(
        args.get("api_key")
            .and_then(|v| v.as_str())
            .or(header_key),
    );
    if tools::is_composed(name) {
        if name != "cheapest" && key.is_empty() {
            return Err(format!(
                "{name} needs a Targon API key — set TARGON_API_KEY, write ~/.mod/targon/api_key, or pass api_key"
            ));
        }
        return tools::call_composed(name, args, &key).await;
    }
    match tools::find(name) {
        Some(spec) => tools::call_spec(spec, args, &key).await,
        None => Err(format!("unknown tool: {name}")),
    }
}

fn rpc_result(id: Value, result: Value) -> Value {
    json!({ "jsonrpc": "2.0", "id": id, "result": result })
}

fn rpc_error(id: Value, code: i64, message: &str) -> Value {
    json!({ "jsonrpc": "2.0", "id": id, "error": { "code": code, "message": message } })
}

/// Handle one JSON-RPC message. Returns None for notifications (no reply).
pub async fn handle_message(msg: &Value, header_key: Option<&str>) -> Option<Value> {
    let method = msg.get("method").and_then(|m| m.as_str()).unwrap_or("");
    let id = msg.get("id").cloned();
    let params = msg.get("params").cloned().unwrap_or(json!({}));

    // Notifications carry no id and get no response.
    let id = match id {
        Some(id) if !id.is_null() => id,
        _ => return None,
    };

    Some(match method {
        "initialize" => rpc_result(
            id,
            json!({
                "protocolVersion": params.get("protocolVersion").and_then(|v| v.as_str()).unwrap_or(PROTOCOL_VERSION),
                "capabilities": { "tools": {} },
                "serverInfo": { "name": SERVER_NAME, "version": SERVER_VERSION }
            }),
        ),
        "ping" => rpc_result(id, json!({})),
        "tools/list" => rpc_result(id, json!({ "tools": tool_list() })),
        "tools/call" => {
            let name = params.get("name").and_then(|n| n.as_str()).unwrap_or("");
            let args = params.get("arguments").cloned().unwrap_or(json!({}));
            match call_tool(name, &args, header_key).await {
                Ok(v) => rpc_result(
                    id,
                    json!({
                        "content": [{ "type": "text", "text": serde_json::to_string(&v).unwrap_or_default() }],
                        "structuredContent": v,
                        "isError": false
                    }),
                ),
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

/// stdio transport: newline-delimited JSON-RPC on stdin/stdout.
/// Usage: targon-api --stdio  (e.g. `claude mcp add targon -- targon-api --stdio`)
pub async fn run_stdio() {
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
                let _ = stdout.flush().await;
                continue;
            }
        };
        if let Some(resp) = handle_message(&msg, None).await {
            let _ = stdout.write_all(format!("{resp}\n").as_bytes()).await;
            let _ = stdout.flush().await;
        }
    }
}
