//! MCP (Model Context Protocol) server core — JSON-RPC 2.0 dispatch shared by
//! the Streamable HTTP endpoint (/mcp) and stdio mode (--stdio).
//! Every REST route funnels through `call_tool`, so the MCP tool layer is the
//! single backend: one definition of each capability, three doors.

use crate::lium::{self, ApiError};
use serde_json::{json, Value};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};

pub const PROTOCOL_VERSION: &str = "2025-06-18";
pub const SERVER_NAME: &str = "lium";
pub const SERVER_VERSION: &str = env!("CARGO_PKG_VERSION");

pub fn tool_list() -> Value {
    json!([
        {
            "name": "lium_info",
            "description": "Server + upstream status: Lium API version, whether a key is loaded, MCP protocol, subnet id.",
            "inputSchema": { "type": "object", "properties": {} }
        },
        {
            "name": "executors",
            "description": "Browse the Bittensor SN51 GPU marketplace. Filter by gpu_type, price, gpu count, country, tier, availability; sort by price/reliability/gpu_count/uptime/vram. Public — no key needed.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "gpu_type": { "type": "string", "description": "Substring of the GPU name, e.g. H200, 4090, B300" },
                    "max_price": { "type": "number", "description": "Max USD per GPU-hour" },
                    "min_gpus": { "type": "integer", "description": "Minimum GPUs on the node" },
                    "country": { "type": "string", "description": "Country or city substring" },
                    "tier": { "type": "string", "description": "Node tier, e.g. secure" },
                    "available_only": { "type": "boolean", "description": "Only nodes with a free GPU right now", "default": false },
                    "sort": { "type": "string", "enum": ["price", "reliability", "gpu_count", "uptime", "vram"], "default": "price" },
                    "limit": { "type": "integer", "default": 50 },
                    "raw": { "type": "boolean", "description": "Return full upstream objects instead of compact rows", "default": false }
                }
            }
        },
        {
            "name": "executor",
            "description": "One node by id (full uuid or unique prefix), with live hardware utilization when a key is set.",
            "inputSchema": {
                "type": "object",
                "properties": { "executor_id": { "type": "string" } },
                "required": ["executor_id"]
            }
        },
        {
            "name": "gpu_types",
            "description": "Rented vs total nodes per GPU type across the subnet (public).",
            "inputSchema": { "type": "object", "properties": {} }
        },
        {
            "name": "capacity",
            "description": "Open earning capacity and hourly rate per GPU model — what providers can still bring online (public).",
            "inputSchema": { "type": "object", "properties": {} }
        },
        {
            "name": "subnet",
            "description": "Bittensor subnet 51 state in one call: supply (nodes/GPUs/providers/validators), utilization by GPU, capacity, and the latest validator weights with top-scoring uids.",
            "inputSchema": { "type": "object", "properties": {} }
        },
        {
            "name": "provider",
            "description": "Statistics for one provider (miner) by Bittensor hotkey, optionally with their nodes.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "miner_hotkey": { "type": "string" },
                    "executors": { "type": "boolean", "description": "Also list the provider's nodes", "default": false }
                },
                "required": ["miner_hotkey"]
            }
        },
        {
            "name": "templates",
            "description": "Docker templates available to launch on a pod. Filter with q (name/image substring) or gpu_model + driver_version.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "q": { "type": "string" },
                    "gpu_model": { "type": "string" },
                    "driver_version": { "type": "string" },
                    "limit": { "type": "integer", "default": 50 }
                }
            }
        },
        {
            "name": "pods",
            "description": "Your running pods (requires an API key).",
            "inputSchema": { "type": "object", "properties": { "raw": { "type": "boolean", "default": false } } }
        },
        {
            "name": "pod",
            "description": "One pod by id, including ssh command and port mappings (requires an API key).",
            "inputSchema": {
                "type": "object",
                "properties": { "pod_id": { "type": "string" } },
                "required": ["pod_id"]
            }
        },
        {
            "name": "up",
            "description": "Rent a node — start a pod. Picks a verified template for the node's GPU/driver when template_id is omitted, and uses your registered SSH keys when public_key is omitted. Costs money (requires an API key).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "executor_id": { "type": "string", "description": "Node id (uuid or unique prefix)" },
                    "name": { "type": "string", "description": "Pod name", "default": "mod-pod" },
                    "template_id": { "type": "string" },
                    "gpu_count": { "type": "integer" },
                    "public_key": { "type": "string", "description": "SSH public key; defaults to every key registered on the account" },
                    "termination_hours": { "type": "integer", "description": "Auto-terminate after N hours" },
                    "enable_jupyter": { "type": "boolean" },
                    "volume_id": { "type": "string" }
                },
                "required": ["executor_id"]
            }
        },
        {
            "name": "down",
            "description": "Stop and remove a pod, ending the rental (requires an API key).",
            "inputSchema": {
                "type": "object",
                "properties": { "pod_id": { "type": "string" } },
                "required": ["pod_id"]
            }
        },
        {
            "name": "reboot",
            "description": "Reboot a pod (requires an API key).",
            "inputSchema": {
                "type": "object",
                "properties": { "pod_id": { "type": "string" }, "volume_id": { "type": "string" } },
                "required": ["pod_id"]
            }
        },
        {
            "name": "logs",
            "description": "Container logs for a pod (requires an API key).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "pod_id": { "type": "string" },
                    "tail": { "type": "integer", "default": 200 }
                },
                "required": ["pod_id"]
            }
        },
        {
            "name": "ssh_keys",
            "description": "SSH keys registered on the account (requires an API key).",
            "inputSchema": { "type": "object", "properties": {} }
        },
        {
            "name": "add_ssh_key",
            "description": "Register an SSH public key on the account (requires an API key).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "public_key": { "type": "string" },
                    "name": { "type": "string" }
                },
                "required": ["public_key"]
            }
        },
        {
            "name": "me",
            "description": "The account behind the key: identity and credit balance (requires an API key).",
            "inputSchema": { "type": "object", "properties": {} }
        },
        {
            "name": "volumes",
            "description": "Persistent volumes on the account (requires an API key).",
            "inputSchema": { "type": "object", "properties": {} }
        },
        {
            "name": "endpoints",
            "description": "Every operation on the live Lium platform API, read from its published OpenAPI 3.1 spec.",
            "inputSchema": {
                "type": "object",
                "properties": { "q": { "type": "string", "description": "Filter by path/summary substring" } }
            }
        },
        {
            "name": "api",
            "description": "Call any Lium API endpoint directly: {method, path, query, body}. The escape hatch for anything the named tools do not cover.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": { "type": "string", "description": "Path under the API base, e.g. /executors/stats" },
                    "method": { "type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"], "default": "GET" },
                    "query": { "type": "object" },
                    "body": { "type": "object" }
                },
                "required": ["path"]
            }
        }
    ])
}

fn s<'a>(args: &'a Value, key: &str) -> Option<&'a str> {
    args.get(key).and_then(|v| v.as_str()).filter(|v| !v.is_empty())
}

fn need<'a>(args: &'a Value, key: &str, tool: &str) -> Result<&'a str, ApiError> {
    s(args, key).ok_or_else(|| ApiError::local(400, format!("{tool} requires `{key}`")))
}

fn as_list(v: &Value) -> Vec<Value> {
    v.as_array()
        .cloned()
        .or_else(|| v.get("items").and_then(|i| i.as_array()).cloned())
        .unwrap_or_default()
}

/// Resolve a node id that may be a unique prefix, returning its compact row.
async fn find_executor(key: &str, id: &str) -> Result<Value, ApiError> {
    let rows = lium::executor_rows(key, 500).await?;
    rows.into_iter()
        .find(|r| r["id"].as_str().map(|x| x == id || x.starts_with(id)).unwrap_or(false))
        .ok_or_else(|| ApiError::local(404, format!("no node matching `{id}` is listed right now")))
}

/// Compact pod row — the fields you act on, not the whole executor tree.
fn compact_pod(p: &Value) -> Value {
    json!({
        "id": p.get("id").cloned().unwrap_or(Value::Null),
        "name": p.get("pod_name").cloned().unwrap_or(Value::Null),
        "status": p.get("status").cloned().unwrap_or(Value::Null),
        "gpu": p.get("gpu_name").cloned().unwrap_or(Value::Null),
        "gpu_count": p.get("gpu_count").cloned().unwrap_or(Value::Null),
        "price_per_hr": p.get("price").cloned().unwrap_or(Value::Null),
        "ssh": p.get("ssh_connect_cmd").cloned().unwrap_or(Value::Null),
        "ports": p.get("ports_mapping").cloned().unwrap_or(Value::Null),
        "jupyter_url": p.get("jupyter_url").cloned().unwrap_or(Value::Null),
        "template": p.get("template").and_then(|t| t.get("name")).cloned().unwrap_or(Value::Null),
        "executor_id": p.get("executor_id").cloned().unwrap_or(Value::Null),
        "created_at": p.get("created_at").cloned().unwrap_or(Value::Null),
        "removal_scheduled_at": p.get("removal_scheduled_at").cloned().unwrap_or(Value::Null),
    })
}

/// Execute a tool, keeping the error's status so HTTP callers can answer with
/// it. `header_key` is a per-request API key (HTTP x-api-key); arguments may
/// also carry `api_key`.
pub async fn call_tool_raw(name: &str, args: &Value, header_key: Option<&str>) -> Result<Value, ApiError> {
    let key = lium::resolve_key(s(args, "api_key").or(header_key));
    let authed = |tool: &str| -> Result<(), ApiError> {
        if key.is_empty() {
            Err(ApiError::needs_key(tool))
        } else {
            Ok(())
        }
    };

    async {
        match name {
            "lium_info" => {
                let version = lium::get("/version", &key, &[]).await.ok();
                Ok(json!({
                    "name": SERVER_NAME,
                    "version": SERVER_VERSION,
                    "backend": "rust-mcp",
                    "protocol": PROTOCOL_VERSION,
                    "upstream": lium::base_url(),
                    "upstream_up": version.is_some(),
                    "upstream_version": version,
                    "netuid": lium::NETUID,
                    "chain": "bittensor",
                    "key_loaded": !key.is_empty(),
                    "tools": tool_list().as_array().map(|t| t.len()).unwrap_or(0),
                }))
            }
            "executors" => {
                let raw = args.get("raw").and_then(|v| v.as_bool()).unwrap_or(false);
                if raw {
                    let v = lium::get("/executors", &key, &[("size".into(), "500".into())]).await?;
                    return Ok(json!({ "executors": as_list(&v) }));
                }
                let rows = lium::executor_rows(&key, 500).await?;
                let listed = rows.len();
                let rows = lium::filter_sort(rows, args);
                Ok(json!({ "count": rows.len(), "listed": listed, "executors": rows }))
            }
            "executor" => {
                let id = need(args, "executor_id", "executor")?;
                let mut row = find_executor(&key, id).await?;
                if !key.is_empty() {
                    let uuid = row["id"].as_str().unwrap_or(id).to_string();
                    if let Ok(u) = lium::get(&format!("/executors/{uuid}/hardware-utilization"), &key, &[]).await {
                        row["hardware_utilization"] = u;
                    }
                }
                Ok(row)
            }
            "gpu_types" => Ok(json!({ "stats": lium::get("/executors/stats", &key, &[]).await? })),
            "capacity" => Ok(json!({ "capacity": lium::get("/machines/capacity", &key, &[]).await? })),
            "subnet" => lium::subnet(&key).await,
            "provider" => {
                let hotkey = need(args, "miner_hotkey", "provider")?;
                let mut out = json!({
                    "miner_hotkey": hotkey,
                    "stats": lium::get(&format!("/provider-stats/{hotkey}"), &key, &[]).await?,
                });
                if args.get("executors").and_then(|v| v.as_bool()).unwrap_or(false) {
                    let v = lium::get(&format!("/provider-stats/{hotkey}/executors"), &key, &[]).await?;
                    out["executors"] = json!(as_list(&v).iter().map(lium::compact_executor).collect::<Vec<_>>());
                }
                Ok(out)
            }
            "templates" => {
                let mut q = Vec::new();
                if let Some(g) = s(args, "gpu_model") {
                    q.push(("gpu_model".to_string(), g.to_string()));
                }
                if let Some(d) = s(args, "driver_version") {
                    q.push(("driver_version".to_string(), d.to_string()));
                }
                let v = lium::get("/templates", &key, &q).await?;
                let mut list = as_list(&v);
                if let Some(needle) = s(args, "q") {
                    let needle = needle.to_lowercase();
                    list.retain(|t| t.to_string().to_lowercase().contains(&needle));
                }
                let limit = args.get("limit").and_then(|v| v.as_u64()).unwrap_or(50) as usize;
                list.truncate(limit);
                let templates: Vec<Value> = list
                    .iter()
                    .map(|t| {
                        json!({
                            "id": t.get("id").cloned().unwrap_or(Value::Null),
                            "name": t.get("name").cloned().unwrap_or(Value::Null),
                            "image": format!(
                                "{}:{}",
                                t.get("docker_image").and_then(|i| i.as_str()).unwrap_or(""),
                                t.get("docker_image_tag").and_then(|i| i.as_str()).unwrap_or("latest")
                            ),
                            "status": t.get("status").cloned().unwrap_or(Value::Null),
                            "category": t.get("category").cloned().unwrap_or(Value::Null),
                            "description": t.get("description").cloned().unwrap_or(Value::Null),
                        })
                    })
                    .collect();
                Ok(json!({ "count": templates.len(), "templates": templates }))
            }
            "pods" => {
                authed("pods")?;
                let v = lium::get("/pods", &key, &[]).await?;
                if args.get("raw").and_then(|v| v.as_bool()).unwrap_or(false) {
                    return Ok(json!({ "pods": as_list(&v) }));
                }
                let pods: Vec<Value> = as_list(&v).iter().map(compact_pod).collect();
                Ok(json!({ "count": pods.len(), "pods": pods }))
            }
            "pod" => {
                authed("pod")?;
                let id = need(args, "pod_id", "pod")?;
                lium::get(&format!("/pods/{id}"), &key, &[]).await
            }
            "up" => {
                authed("up")?;
                let id = need(args, "executor_id", "up")?;
                let node = find_executor(&key, id).await?;
                let uuid = node["id"].as_str().unwrap_or(id).to_string();

                // SSH keys: explicit one, else every key on the account —
                // a pod you cannot log into is money burned.
                let keys: Vec<String> = match s(args, "public_key") {
                    Some(k) => vec![k.to_string()],
                    None => {
                        let v = lium::get("/ssh-keys", &key, &[]).await?;
                        as_list(&v)
                            .iter()
                            .filter_map(|k| k.get("public_key").and_then(|p| p.as_str()).map(String::from))
                            .collect()
                    }
                };
                if keys.is_empty() {
                    return Err(ApiError::local(
                        400,
                        "no SSH key: pass `public_key` or register one with add_ssh_key",
                    ));
                }

                // Template: explicit, else the first verified image built for
                // this node's GPU + driver.
                let template_id = match s(args, "template_id") {
                    Some(t) => t.to_string(),
                    None => {
                        let q = vec![
                            ("gpu_model".to_string(), node["gpu"].as_str().unwrap_or("").to_string()),
                            ("driver_version".to_string(), node["driver"].as_str().unwrap_or("").to_string()),
                        ];
                        let v = lium::get("/templates", &key, &q).await?;
                        as_list(&v)
                            .iter()
                            .find(|t| t.get("status").and_then(|s| s.as_str()) == Some("VERIFY_SUCCESS"))
                            .and_then(|t| t.get("id").and_then(|i| i.as_str()).map(String::from))
                            .ok_or_else(|| ApiError::local(
                                400,
                                "no verified template for this GPU/driver — pass `template_id` (see the templates tool)",
                            ))?
                    }
                };

                let mut body = json!({
                    "pod_name": s(args, "name").unwrap_or("mod-pod"),
                    "template_id": template_id,
                    "user_public_key": keys,
                });
                for k in ["gpu_count", "termination_hours", "initial_port_count"] {
                    if let Some(v) = args.get(k).and_then(|v| v.as_u64()) {
                        body[k] = json!(v);
                    }
                }
                for k in ["enable_jupyter", "enable_volume_encryption"] {
                    if let Some(v) = args.get(k).and_then(|v| v.as_bool()) {
                        body[k] = json!(v);
                    }
                }
                if let Some(v) = s(args, "volume_id") {
                    body["volume_id"] = json!(v);
                }

                let result = lium::post(&format!("/executors/{uuid}/rent"), &key, &body).await?;
                Ok(json!({
                    "rented": result,
                    "executor": node,
                    "template_id": body["template_id"],
                    "price_per_hr": node["price_per_hr"],
                }))
            }
            "down" => {
                authed("down")?;
                let id = need(args, "pod_id", "down")?;
                let r = lium::del(&format!("/pods/{id}"), &key).await?;
                Ok(json!({ "stopped": id, "result": r }))
            }
            "reboot" => {
                authed("reboot")?;
                let id = need(args, "pod_id", "reboot")?;
                let mut body = json!({});
                if let Some(v) = s(args, "volume_id") {
                    body["volume_id"] = json!(v);
                }
                lium::post(&format!("/pods/{id}/reboot"), &key, &body).await
            }
            "logs" => {
                authed("logs")?;
                let id = need(args, "pod_id", "logs")?;
                let tail = args.get("tail").and_then(|v| v.as_u64()).unwrap_or(200);
                lium::get(
                    &format!("/pods/{id}/logs"),
                    &key,
                    &[("tail".into(), tail.to_string()), ("follow".into(), "false".into())],
                )
                .await
            }
            "ssh_keys" => {
                authed("ssh_keys")?;
                let v = lium::get("/ssh-keys", &key, &[]).await?;
                Ok(json!({ "ssh_keys": as_list(&v) }))
            }
            "add_ssh_key" => {
                authed("add_ssh_key")?;
                let pubkey = need(args, "public_key", "add_ssh_key")?;
                let name = s(args, "name").unwrap_or("mod-lium");
                lium::post("/ssh-keys", &key, &json!({ "name": name, "public_key": pubkey })).await
            }
            "me" => {
                authed("me")?;
                lium::get("/users/me", &key, &[]).await
            }
            "volumes" => {
                authed("volumes")?;
                let v = lium::get("/volumes", &key, &[]).await?;
                Ok(json!({ "volumes": as_list(&v) }))
            }
            "endpoints" => {
                let mut v = lium::endpoints().await?;
                if let Some(needle) = s(args, "q") {
                    let needle = needle.to_lowercase();
                    if let Some(rows) = v.get_mut("endpoints").and_then(|e| e.as_array_mut()) {
                        rows.retain(|r| r.to_string().to_lowercase().contains(&needle));
                        let n = rows.len();
                        v["count"] = json!(n);
                    }
                }
                Ok(v)
            }
            "api" => {
                let path = need(args, "path", "api")?;
                if !path.starts_with('/') {
                    return Err(ApiError::local(400, "path must start with /"));
                }
                let method = s(args, "method").unwrap_or("GET").to_uppercase();
                let method = reqwest::Method::from_bytes(method.as_bytes())
                    .map_err(|_| ApiError::local(400, "unsupported method"))?;
                let query: Vec<(String, String)> = args
                    .get("query")
                    .and_then(|q| q.as_object())
                    .map(|o| {
                        o.iter()
                            .map(|(k, v)| {
                                let s = v.as_str().map(String::from).unwrap_or_else(|| v.to_string());
                                (k.clone(), s)
                            })
                            .collect()
                    })
                    .unwrap_or_default();
                let body = args.get("body").cloned();
                lium::request(method, path, &key, body.as_ref(), &query).await
            }
            other => Err(ApiError::local(400, format!("unknown tool: {other}"))),
        }
    }
    .await
}

/// MCP-facing wrapper: tool errors travel as text inside the result.
pub async fn call_tool(name: &str, args: &Value, header_key: Option<&str>) -> Result<Value, String> {
    call_tool_raw(name, args, header_key).await.map_err(|e| e.to_string())
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
                Err(e) => rpc_result(id, json!({ "content": [{ "type": "text", "text": e }], "isError": true })),
            }
        }
        "resources/list" => rpc_result(id, json!({ "resources": [] })),
        "prompts/list" => rpc_result(id, json!({ "prompts": [] })),
        _ => rpc_error(id, -32601, &format!("method not found: {method}")),
    })
}

/// stdio transport: newline-delimited JSON-RPC on stdin/stdout.
/// Usage: lium-api --stdio  (e.g. `claude mcp add lium -- lium-api --stdio`)
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
