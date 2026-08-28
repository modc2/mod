//! MCP (Model Context Protocol) server core — JSON-RPC 2.0 dispatch shared by
//! the Streamable HTTP endpoint (/mcp) and stdio mode (--stdio).
//! Every REST route also funnels through `call_tool`, so the MCP tool layer
//! is the single backend.

use crate::chutes;
use serde_json::{json, Value};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};

pub const PROTOCOL_VERSION: &str = "2025-06-18";
pub const SERVER_NAME: &str = "chutes";
pub const SERVER_VERSION: &str = env!("CARGO_PKG_VERSION");

pub fn tool_list() -> Value {
    json!([
        {
            "name": "chat",
            "description": "Chat completion via chutes.ai (OpenAI-compatible). Pass either `message` (string) or full `messages` array.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "message": { "type": "string", "description": "Single user message (shortcut)" },
                    "messages": { "type": "array", "description": "OpenAI-style messages array", "items": { "type": "object" } },
                    "system": { "type": "string", "description": "Optional system prompt" },
                    "model": { "type": "string", "description": "Model id; defaults to server default" },
                    "temperature": { "type": "number", "default": 0.7 },
                    "max_tokens": { "type": "integer", "default": 4096 }
                }
            }
        },
        {
            "name": "generate_image",
            "description": "Generate images from a text prompt via chutes.ai.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "prompt": { "type": "string" },
                    "model": { "type": "string" },
                    "size": { "type": "string", "default": "1024x1024" },
                    "n": { "type": "integer", "default": 1 }
                },
                "required": ["prompt"]
            }
        },
        {
            "name": "models",
            "description": "List available models/chutes, optionally filtered by a search string.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "search": { "type": "string" },
                    "limit": { "type": "integer", "default": 200 }
                }
            }
        },
        {
            "name": "list_chutes",
            "description": "List deployed chutes (paginated).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "page": { "type": "integer", "default": 1 },
                    "limit": { "type": "integer", "default": 50 }
                }
            }
        },
        {
            "name": "get_chute",
            "description": "Get chute details by id or name.",
            "inputSchema": {
                "type": "object",
                "properties": { "chute_id": { "type": "string" } },
                "required": ["chute_id"]
            }
        },
        {
            "name": "warmup",
            "description": "Pre-initialize a chute to cut cold-start latency.",
            "inputSchema": {
                "type": "object",
                "properties": { "chute_id": { "type": "string" } },
                "required": ["chute_id"]
            }
        },
        {
            "name": "utilization",
            "description": "Current chutes.ai capacity / utilization metrics.",
            "inputSchema": { "type": "object", "properties": {} }
        },
        {
            "name": "deploy_chute",
            "description": "Deploy a new chute from a config object.",
            "inputSchema": {
                "type": "object",
                "properties": { "config": { "type": "object" } },
                "required": ["config"]
            }
        },
        {
            "name": "delete_chute",
            "description": "Delete a chute by id.",
            "inputSchema": {
                "type": "object",
                "properties": { "chute_id": { "type": "string" } },
                "required": ["chute_id"]
            }
        }
    ])
}

fn s<'a>(args: &'a Value, key: &str) -> Option<&'a str> {
    args.get(key).and_then(|v| v.as_str())
}

fn u(args: &Value, key: &str, default: u64) -> u64 {
    args.get(key).and_then(|v| v.as_u64()).unwrap_or(default)
}

/// Execute a tool. `header_key` is a per-request API key override (HTTP
/// x-api-key); arguments may also carry an `api_key` field.
pub async fn call_tool(name: &str, args: &Value, header_key: Option<&str>) -> Result<Value, String> {
    let key = chutes::resolve_key(s(args, "api_key").or(header_key));
    let out = match name {
        "chat" => {
            let mut messages: Vec<Value> = match args.get("messages") {
                Some(Value::Array(m)) if !m.is_empty() => m.clone(),
                _ => match s(args, "message") {
                    Some(m) => vec![json!({ "role": "user", "content": m })],
                    None => return Err("chat requires `message` or `messages`".into()),
                },
            };
            if let Some(sys) = s(args, "system") {
                messages.insert(0, json!({ "role": "system", "content": sys }));
            }
            let body = json!({
                "model": s(args, "model").map(String::from).unwrap_or_else(chutes::default_model),
                "messages": messages,
                "temperature": args.get("temperature").and_then(|v| v.as_f64()).unwrap_or(0.7),
                "max_tokens": u(args, "max_tokens", 4096),
                "stream": false,
            });
            chutes::chat(&key, &body).await
        }
        "generate_image" => {
            let prompt = s(args, "prompt").ok_or("generate_image requires `prompt`")?;
            let mut body = json!({
                "prompt": prompt,
                "size": s(args, "size").unwrap_or("1024x1024"),
                "n": u(args, "n", 1),
                "response_format": s(args, "response_format").unwrap_or("url"),
            });
            if let Some(m) = s(args, "model") {
                body["model"] = json!(m);
            }
            chutes::generate_image(&key, &body).await
        }
        "models" => match s(args, "search") {
            None => chutes::list_chutes(&key, 1, u(args, "limit", 200)).await,
            Some(q) => {
                // Search spans the full (paginated) chute list.
                let ql = q.to_lowercase();
                let mut matches = Vec::new();
                let mut page = 1u64;
                loop {
                    let v = match chutes::list_chutes(&key, page, 200).await {
                        Ok(v) => v,
                        Err(e) => return Err(e.to_string()),
                    };
                    let items = v
                        .as_array()
                        .cloned()
                        .or_else(|| v.get("items").and_then(|i| i.as_array()).cloned())
                        .unwrap_or_default();
                    let n = items.len();
                    matches.extend(
                        items.into_iter().filter(|c| c.to_string().to_lowercase().contains(&ql)),
                    );
                    let total = v.get("total").and_then(|t| t.as_u64()).unwrap_or(0);
                    if n < 200 || page * 200 >= total || page >= 10 {
                        break;
                    }
                    page += 1;
                }
                Ok(json!({ "items": matches, "total": matches.len() }))
            }
        },
        "list_chutes" => chutes::list_chutes(&key, u(args, "page", 1), u(args, "limit", 50)).await,
        "get_chute" => {
            let id = s(args, "chute_id").ok_or("get_chute requires `chute_id`")?;
            chutes::get_chute(&key, id).await
        }
        "warmup" => {
            let id = s(args, "chute_id").ok_or("warmup requires `chute_id`")?;
            chutes::warmup(&key, id).await
        }
        "utilization" => chutes::utilization(&key).await,
        "deploy_chute" => {
            let config = args.get("config").ok_or("deploy_chute requires `config`")?;
            chutes::deploy_chute(&key, config).await
        }
        "delete_chute" => {
            let id = s(args, "chute_id").ok_or("delete_chute requires `chute_id`")?;
            chutes::delete_chute(&key, id).await
        }
        other => return Err(format!("unknown tool: {other}")),
    };
    out.map_err(|e| e.to_string())
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
/// Usage: chutes-api --stdio  (e.g. `claude mcp add chutes -- chutes-api --stdio`)
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
