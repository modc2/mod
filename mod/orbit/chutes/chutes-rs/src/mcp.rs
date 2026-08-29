//! MCP (Model Context Protocol) server core — JSON-RPC 2.0 dispatch shared by
//! the Streamable HTTP endpoint (/mcp) and stdio mode (--stdio).
//! Every REST route also funnels through `call_tool`, so the MCP tool layer is
//! the single backend. Everything talks to chutes.ai.

use crate::chutes;
use crate::upstream;
use futures_util::future::join_all;
use serde_json::{json, Value};
use std::collections::HashMap;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};

pub const PROTOCOL_VERSION: &str = "2025-06-18";
pub const SERVER_NAME: &str = "chutes";
pub const SERVER_VERSION: &str = env!("CARGO_PKG_VERSION");

/// Per-request keys: `chutes` from `x-chutes-key`, plus "*" for a generic
/// `x-api-key` / `Authorization: Bearer`.
pub type Keys = HashMap<String, String>;

pub fn tool_list() -> Value {
    json!([
        {
            "name": "chat",
            "description": "Chat completion on chutes.ai — OpenAI-compatible shape. Pass either `message` (string) or a full `messages` array.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "message": { "type": "string", "description": "Single user message (shortcut)" },
                    "messages": { "type": "array", "description": "OpenAI-style messages array", "items": { "type": "object" } },
                    "system": { "type": "string", "description": "Optional system prompt" },
                    "model": { "type": "string", "description": "Chute name; defaults to the box default (with stand-ins)" },
                    "temperature": { "type": "number", "default": 0.7 },
                    "max_tokens": { "type": "integer", "default": 4096 }
                }
            }
        },
        {
            "name": "compare",
            "description": "Race the same prompt across several chutes at once and return every answer with latency, tokens and estimated USD cost. Fastest wins.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "message": { "type": "string" },
                    "system": { "type": "string" },
                    "models": {
                        "type": "array",
                        "description": "Chute names to race. Omit to race the box's default-model list.",
                        "items": { "type": "string" }
                    },
                    "temperature": { "type": "number", "default": 0.7 },
                    "max_tokens": { "type": "integer", "default": 1024 }
                },
                "required": ["message"]
            }
        },
        {
            "name": "route",
            "description": "Model router: rank chutes by price (or invocations) under filters, and optionally run the prompt on the winner — walking down the ranking if it can't answer.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "search": { "type": "string", "description": "Substring match on chute name / tags" },
                    "kind": { "type": "string", "enum": ["chat", "image", "embedding", "any"], "default": "chat" },
                    "max_price": { "type": "number", "description": "Max input USD per 1M tokens" },
                    "sort": { "type": "string", "enum": ["price", "invocations", "name"], "default": "price" },
                    "limit": { "type": "integer", "default": 10 },
                    "ask": { "type": "string", "description": "If set, run this prompt on the top-ranked chute and return the answer too" }
                }
            }
        },
        {
            "name": "models",
            "description": "The chute catalog, normalized to {id, chute_id, in_price, out_price, kind, tags, invocations} with USD per 1M tokens.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "search": { "type": "string" },
                    "kind": { "type": "string", "enum": ["chat", "image", "embedding", "custom", "any"], "default": "any" },
                    "sort": { "type": "string", "enum": ["price", "name", "invocations"], "default": "price" },
                    "limit": { "type": "integer", "default": 200 },
                    "refresh": { "type": "boolean", "default": false }
                }
            }
        },
        {
            "name": "status",
            "description": "Base URL, default model (and stand-ins), whether a key resolves and where it came from — never the key itself.",
            "inputSchema": {
                "type": "object",
                "properties": { "counts": { "type": "boolean", "default": false, "description": "Also fetch the catalog size (network)" } }
            }
        },
        {
            "name": "generate_image",
            "description": "Generate an image from a text prompt on a diffusion chute.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "prompt": { "type": "string" },
                    "model": { "type": "string", "description": "Diffusion chute name" },
                    "size": { "type": "string", "default": "1024x1024" },
                    "n": { "type": "integer", "default": 1 }
                },
                "required": ["prompt"]
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
    args.get(key).and_then(|v| v.as_str()).filter(|v| !v.trim().is_empty())
}

fn u(args: &Value, key: &str, default: u64) -> u64 {
    args.get(key).and_then(|v| v.as_u64()).unwrap_or(default)
}

fn fl(args: &Value, key: &str) -> Option<f64> {
    args.get(key).and_then(|v| v.as_f64())
}

/// Explicit `api_key` arg > `x-chutes-key` header > generic x-api-key > env/file.
pub fn key_for(args: &Value, keys: &Keys) -> String {
    let explicit = s(args, "api_key")
        .or_else(|| keys.get(chutes::ID).map(|k| k.as_str()))
        .or_else(|| keys.get("*").map(|k| k.as_str()));
    chutes::resolve_key(explicit)
}

fn messages_from(args: &Value) -> Result<Vec<Value>, String> {
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
    Ok(messages)
}

/// Best-effort USD cost of one completion, from the cached catalog prices.
fn estimate_cost(model: &str, usage: Option<&Value>) -> Option<f64> {
    let usage = usage?;
    let prompt = usage.get("prompt_tokens").and_then(|v| v.as_f64()).unwrap_or(0.0);
    let completion = usage.get("completion_tokens").and_then(|v| v.as_f64()).unwrap_or(0.0);
    let catalog = chutes::cached()?;
    let row = catalog.iter().find(|m| m.get("id").and_then(|v| v.as_str()) == Some(model))?;
    let inp = row.get("in_price").and_then(|v| v.as_f64()).unwrap_or(0.0);
    let out = row.get("out_price").and_then(|v| v.as_f64()).unwrap_or(0.0);
    Some((prompt * inp + completion * out) / 1_000_000.0)
}

/// One chat turn, timed — the shared core of `chat`, `compare` and `route`.
async fn run_chat(key: &str, args: &Value) -> Result<Value, String> {
    let messages = messages_from(args)?;
    // A model the caller named is the one we call. Otherwise the box's default
    // list, tried in order — see `try_models`.
    let candidates = match s(args, "model") {
        Some(m) => vec![m.to_string()],
        None => chutes::default_models(),
    };
    let body = json!({
        "messages": messages,
        "temperature": fl(args, "temperature").unwrap_or(0.7),
        "max_tokens": u(args, "max_tokens", 4096),
        "stream": false,
    });
    let started = std::time::Instant::now();
    let (model, out) = upstream::try_models(&candidates, |model| {
        let key = key.to_string();
        let mut body = body.clone();
        body["model"] = json!(model);
        async move { upstream::chat(&key, &body).await }
    })
    .await
    .map_err(|e| e.to_string())?;
    let mut out = out;
    let ms = started.elapsed().as_millis() as u64;
    // chutes echoes an OpenAI response; we staple provenance onto it so the
    // console and `compare` can show which chute answered, how fast and for
    // how much.
    if let Some(obj) = out.as_object_mut() {
        obj.insert("_model".into(), json!(model));
        obj.insert("_latency_ms".into(), json!(ms));
        if let Some(cost) = estimate_cost(&model, obj.get("usage")) {
            obj.insert("_cost_usd".into(), json!(cost));
        }
    }
    Ok(out)
}

fn text_of(resp: &Value) -> String {
    resp.pointer("/choices/0/message/content")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string()
}

/// Filter + sort the normalized catalog. Shared by `models` and `route`.
fn rank(mut rows: Vec<Value>, args: &Value, default_kind: &str) -> Vec<Value> {
    let kind = s(args, "kind").unwrap_or(default_kind);
    if kind != "any" {
        rows.retain(|m| m.get("kind").and_then(|v| v.as_str()) == Some(kind));
    }
    if let Some(q) = s(args, "search") {
        let ql = q.to_lowercase();
        rows.retain(|m| {
            let hay = format!(
                "{} {} {}",
                m.get("id").and_then(|v| v.as_str()).unwrap_or(""),
                m.get("description").and_then(|v| v.as_str()).unwrap_or(""),
                m.get("tags").map(|t| t.to_string()).unwrap_or_default()
            )
            .to_lowercase();
            ql.split_whitespace().all(|term| hay.contains(term))
        });
    }
    if let Some(max) = fl(args, "max_price") {
        rows.retain(|m| m.get("in_price").and_then(|v| v.as_f64()).unwrap_or(0.0) <= max);
    }
    let num = |m: &Value, k: &str| m.get(k).and_then(|v| v.as_f64()).unwrap_or(0.0);
    match s(args, "sort").unwrap_or("price") {
        "invocations" => rows.sort_by(|a, b| num(b, "invocations").total_cmp(&num(a, "invocations"))),
        "name" => rows.sort_by(|a, b| {
            a.get("id").and_then(|v| v.as_str()).unwrap_or("").cmp(b.get("id").and_then(|v| v.as_str()).unwrap_or(""))
        }),
        _ => rows.sort_by(|a, b| {
            // A chute with no published price is unknown, not free — it sorts
            // last, behind everything that quotes a number.
            let key = |m: &Value| {
                let v = num(m, "in_price");
                if v > 0.0 { v } else { f64::MAX }
            };
            key(a).total_cmp(&key(b))
        }),
    }
    rows
}

/// Execute a tool. `keys` carries per-request API keys (headers / api_key arg).
pub async fn call_tool(name: &str, args: &Value, keys: &Keys) -> Result<Value, String> {
    let key = key_for(args, keys);
    match name {
        "chat" => run_chat(&key, args).await,

        "compare" => {
            let models: Vec<String> = match args.get("models").and_then(|t| t.as_array()) {
                Some(t) if !t.is_empty() => t
                    .iter()
                    .filter_map(|m| m.as_str().or_else(|| m.get("model").and_then(|v| v.as_str())))
                    .map(str::trim)
                    .filter(|m| !m.is_empty())
                    .map(String::from)
                    .collect(),
                // No fighters named → the box's default-model list.
                _ => chutes::default_models(),
            };
            if models.is_empty() {
                return Err("compare needs at least one model".into());
            }
            let runs = models.iter().map(|model| {
                let mut call = args.clone();
                if let Some(o) = call.as_object_mut() {
                    o.remove("models");
                    o.insert("max_tokens".into(), json!(u(args, "max_tokens", 1024)));
                    o.insert("model".into(), json!(model));
                }
                let key = key.clone();
                async move {
                    let started = std::time::Instant::now();
                    match run_chat(&key, &call).await {
                        Ok(resp) => json!({
                            "model": model,
                            "text": text_of(&resp),
                            "latency_ms": resp.get("_latency_ms").cloned().unwrap_or(json!(started.elapsed().as_millis() as u64)),
                            "usage": resp.get("usage").cloned().unwrap_or(json!(null)),
                            "cost_usd": resp.get("_cost_usd").cloned().unwrap_or(json!(null)),
                        }),
                        Err(e) => json!({
                            "model": model,
                            "error": e,
                            "latency_ms": started.elapsed().as_millis() as u64,
                        }),
                    }
                }
            });
            let mut results = join_all(runs).await;
            // Fastest successful lane wins the round.
            let winner = results
                .iter()
                .filter(|r| r.get("error").is_none())
                .min_by_key(|r| r.get("latency_ms").and_then(|v| v.as_u64()).unwrap_or(u64::MAX))
                .and_then(|r| r.get("model").and_then(|v| v.as_str()))
                .map(String::from);
            for r in results.iter_mut() {
                if let Some(o) = r.as_object_mut() {
                    let is_winner = winner.as_deref() == o.get("model").and_then(|v| v.as_str()) && o.get("error").is_none();
                    o.insert("winner".into(), json!(is_winner));
                }
            }
            Ok(json!({ "results": results, "fastest": winner }))
        }

        "route" => {
            let refresh = args.get("refresh").and_then(|v| v.as_bool()).unwrap_or(false);
            let rows = rank(upstream::models(&key, refresh).await.map_err(|e| e.to_string())?, args, "chat");
            let limit = u(args, "limit", 10) as usize;
            let ranked: Vec<Value> = rows.into_iter().take(limit.max(1)).collect();
            let pick = ranked.first().cloned();
            let mut out = json!({ "ranked": ranked, "pick": pick });
            // `ask` runs the winner — and, if the winner can't answer (cold,
            // out of capacity, delisted), the next ranked chute down, up to
            // ASK_TRIES. Ranking a chute first is a claim about price, not
            // about whether it can answer right now.
            const ASK_TRIES: usize = 5;
            if let Some(prompt) = s(args, "ask") {
                if key.is_empty() {
                    return Err(format!("`ask` needs a chutes key — {} or ~/.mod/chutes/api_key", chutes::ENV_KEY));
                }
                let mut last: Option<String> = None;
                for row in out["ranked"].as_array().cloned().unwrap_or_default().iter().take(ASK_TRIES) {
                    let call = json!({
                        "message": prompt,
                        "system": args.get("system").cloned().unwrap_or(json!(null)),
                        "model": row.get("id").cloned().unwrap_or(json!(null)),
                        "max_tokens": u(args, "max_tokens", 2048),
                    });
                    match run_chat(&key, &call).await {
                        Ok(resp) => {
                            out["answer"] = json!(text_of(&resp));
                            out["answered_by"] = row.clone();
                            out["response"] = resp;
                            last = None;
                            break;
                        }
                        Err(e) => last = Some(e),
                    }
                }
                if let Some(e) = last {
                    out["error"] = json!(e);
                }
            }
            Ok(out)
        }

        "models" => {
            let refresh = args.get("refresh").and_then(|v| v.as_bool()).unwrap_or(false);
            let rows = rank(upstream::models(&key, refresh).await.map_err(|e| e.to_string())?, args, "any");
            let total = rows.len();
            let limit = u(args, "limit", 200) as usize;
            Ok(json!({
                "total": total,
                "items": rows.into_iter().take(limit).collect::<Vec<_>>(),
            }))
        }

        "status" => {
            let mut out = chutes::describe();
            if args.get("counts").and_then(|v| v.as_bool()).unwrap_or(false) {
                if let Ok(m) = upstream::models(&key, false).await {
                    out["models"] = json!(m.len());
                }
            }
            Ok(out)
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
            upstream::generate_image(&key, &body).await.map_err(|e| e.to_string())
        }

        // control plane
        "list_chutes" => upstream::list_chutes(&key, u(args, "page", 1), u(args, "limit", 50)).await.map_err(|e| e.to_string()),
        "get_chute" => upstream::get_chute(&key, s(args, "chute_id").ok_or("get_chute requires `chute_id`")?).await.map_err(|e| e.to_string()),
        "warmup" => upstream::warmup(&key, s(args, "chute_id").ok_or("warmup requires `chute_id`")?).await.map_err(|e| e.to_string()),
        "utilization" => upstream::utilization(&key).await.map_err(|e| e.to_string()),
        "deploy_chute" => {
            let config = args.get("config").ok_or("deploy_chute requires `config`")?;
            upstream::deploy_chute(&key, config).await.map_err(|e| e.to_string())
        }
        "delete_chute" => upstream::delete_chute(&key, s(args, "chute_id").ok_or("delete_chute requires `chute_id`")?).await.map_err(|e| e.to_string()),

        other => Err(format!("unknown tool: {other}")),
    }
}

fn rpc_result(id: Value, result: Value) -> Value {
    json!({ "jsonrpc": "2.0", "id": id, "result": result })
}

fn rpc_error(id: Value, code: i64, message: &str) -> Value {
    json!({ "jsonrpc": "2.0", "id": id, "error": { "code": code, "message": message } })
}

/// Handle one JSON-RPC message. Returns None for notifications (no reply).
pub async fn handle_message(msg: &Value, keys: &Keys) -> Option<Value> {
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
            match call_tool(name, &args, keys).await {
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
    let keys: Keys = Keys::new();
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
        if let Some(resp) = handle_message(&msg, &keys).await {
            let _ = stdout.write_all(format!("{resp}\n").as_bytes()).await;
            let _ = stdout.flush().await;
        }
    }
}
