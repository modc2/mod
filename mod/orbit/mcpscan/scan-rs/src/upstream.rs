//! A minimal MCP client, built for volume.
//!
//! Adapted from orbit/mcp's upstream.rs, with the two things that matter when
//! you are knocking on twenty thousand strangers' endpoints instead of eleven
//! neighbours': no session cache (a session per server would be a memory leak
//! at this scale, and a probe is one round trip anyway), and every failure is
//! classified rather than just recorded — "wants a key" and "nothing there"
//! are completely different facts about a server.

use crate::store::{now, ToolLite};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::time::{Duration, Instant};

pub const PROTOCOL_VERSION: &str = "2025-06-18";
pub const USER_AGENT: &str = "mcpscan/1.0 (+https://modc2.com/mcpscan; MCP index crawler)";

/// What one handshake told us.
#[derive(Clone, Debug, Default)]
pub struct ProbeOut {
    /// live | auth | error | down
    pub status: String,
    pub protocol_version: String,
    pub server_info: Value,
    pub tools: Vec<ToolLite>,
    pub latency_ms: u64,
    pub checked_at: u64,
    pub error: String,
    /// Full tool objects (schemas included) — kept out of the index, handed
    /// straight to a caller who asked for one server's tools.
    pub raw_tools: Vec<Value>,
}

pub fn client(secs: u64) -> reqwest::Client {
    reqwest::Client::builder()
        .timeout(Duration::from_secs(secs))
        .connect_timeout(Duration::from_secs(secs.min(6)))
        .user_agent(USER_AGENT)
        .build()
        .expect("reqwest client")
}

/// Which of the four statuses an error string means. The distinction is worth
/// the string matching: a 401 proves an MCP server is *there*.
pub fn classify(err: &str) -> &'static str {
    let e = err.to_lowercase();
    if e.starts_with("unreachable") || e.contains("timed out") || e.contains("timeout") {
        return "down";
    }
    if e.contains("http 401")
        || e.contains("http 403")
        || e.contains("http 402")
        || e.contains("unauthorized")
        || e.contains("authentication")
        || e.contains("api key")
        || e.contains("invalid token")
    {
        return "auth";
    }
    "error"
}

/// Pull the JSON-RPC reply matching `want_id` out of a body that may be plain
/// JSON or an SSE stream.
fn extract_reply(body: &str, content_type: &str, want_id: u64) -> Result<Value, String> {
    if !content_type.contains("text/event-stream") {
        return serde_json::from_str(body).map_err(|e| format!("bad JSON reply: {e}"));
    }
    let mut candidate: Option<Value> = None;
    let mut data = String::new();
    for line in body.lines().chain(std::iter::once("")) {
        if let Some(rest) = line.strip_prefix("data:") {
            if !data.is_empty() {
                data.push('\n');
            }
            data.push_str(rest.trim_start());
        } else if line.is_empty() && !data.is_empty() {
            if let Ok(v) = serde_json::from_str::<Value>(&data) {
                if v.get("id").and_then(|i| i.as_u64()) == Some(want_id) {
                    return Ok(v);
                }
                if v.get("result").is_some() || v.get("error").is_some() {
                    candidate = Some(v);
                }
            }
            data.clear();
        }
    }
    candidate.ok_or_else(|| "SSE stream held no JSON-RPC reply".into())
}

async fn post_rpc(
    url: &str,
    headers: &HashMap<String, String>,
    method: &str,
    params: Value,
    session: Option<&str>,
    rpc_id: u64,
    timeout: u64,
) -> Result<(Value, Option<String>), String> {
    let mut req = client(timeout)
        .post(url)
        .header("Content-Type", "application/json")
        .header("Accept", "application/json, text/event-stream")
        .header("MCP-Protocol-Version", PROTOCOL_VERSION);
    if let Some(sid) = session {
        req = req.header("Mcp-Session-Id", sid);
    }
    for (k, v) in headers {
        req = req.header(k.as_str(), v.as_str());
    }
    let body = json!({ "jsonrpc": "2.0", "id": rpc_id, "method": method, "params": params });
    let resp = req.json(&body).send().await.map_err(|e| {
        if e.is_timeout() {
            "unreachable: timed out".to_string()
        } else {
            format!("unreachable: {e}")
        }
    })?;
    let status = resp.status();
    let session_id =
        resp.headers().get("mcp-session-id").and_then(|v| v.to_str().ok()).map(String::from);
    let ctype = resp
        .headers()
        .get("content-type")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("")
        .to_string();
    let text = resp.text().await.map_err(|e| format!("read failed: {e}"))?;
    if !status.is_success() {
        return Err(format!("HTTP {}: {}", status.as_u16(), crate::store::clip(text.trim(), 200)));
    }
    let reply = extract_reply(&text, &ctype, rpc_id)?;
    if let Some(err) = reply.get("error") {
        let msg = err.get("message").and_then(|m| m.as_str()).unwrap_or("rpc error");
        return Err(format!("{method}: {msg}"));
    }
    Ok((reply.get("result").cloned().unwrap_or(Value::Null), session_id))
}

async fn handshake(
    url: &str,
    headers: &HashMap<String, String>,
    timeout: u64,
) -> Result<(Value, Option<String>), String> {
    let params = json!({
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": {},
        "clientInfo": { "name": "mcpscan", "version": env!("CARGO_PKG_VERSION") }
    });
    let (result, sid) = post_rpc(url, headers, "initialize", params, None, 1, timeout).await?;
    // Best-effort initialized notification; stateless servers 4xx it — ignore.
    let mut req = client(timeout)
        .post(url)
        .header("Content-Type", "application/json")
        .header("Accept", "application/json, text/event-stream")
        .header("MCP-Protocol-Version", PROTOCOL_VERSION);
    if let Some(s) = &sid {
        req = req.header("Mcp-Session-Id", s.as_str());
    }
    for (k, v) in headers {
        req = req.header(k.as_str(), v.as_str());
    }
    let _ = req.json(&json!({ "jsonrpc": "2.0", "method": "notifications/initialized" })).send().await;
    Ok((result, sid))
}

fn tool_lite(v: &Value) -> Option<ToolLite> {
    let name = v.get("name")?.as_str()?.to_string();
    Some(ToolLite {
        name,
        description: crate::store::clip(
            v.get("description").and_then(|d| d.as_str()).unwrap_or("").trim(),
            160,
        ),
    })
}

/// initialize + tools/list against one endpoint. Never panics: every failure
/// mode comes back as a status.
pub async fn probe(url: &str, headers: &HashMap<String, String>, timeout: u64) -> ProbeOut {
    let started = Instant::now();
    let (init, sid) = match handshake(url, headers, timeout).await {
        Ok(x) => x,
        Err(e) => {
            return ProbeOut {
                status: classify(&e).into(),
                error: e,
                checked_at: now(),
                latency_ms: started.elapsed().as_millis() as u64,
                ..Default::default()
            }
        }
    };
    let protocol_version =
        init.get("protocolVersion").and_then(|v| v.as_str()).unwrap_or("").to_string();
    let server_info = init.get("serverInfo").cloned().unwrap_or(Value::Null);
    let raw = match post_rpc(url, headers, "tools/list", json!({}), sid.as_deref(), 2, timeout).await
    {
        Ok((v, _)) => v.get("tools").and_then(|t| t.as_array()).cloned().unwrap_or_default(),
        Err(e) => {
            // It spoke MCP but wouldn't list — still a server, still classified.
            return ProbeOut {
                status: classify(&e).into(),
                protocol_version,
                server_info,
                error: format!("initialized but tools/list failed: {e}"),
                checked_at: now(),
                latency_ms: started.elapsed().as_millis() as u64,
                ..Default::default()
            };
        }
    };
    ProbeOut {
        status: "live".into(),
        protocol_version,
        server_info,
        tools: raw.iter().filter_map(tool_lite).take(200).collect(),
        latency_ms: started.elapsed().as_millis() as u64,
        checked_at: now(),
        error: String::new(),
        raw_tools: raw,
    }
}

/// Call one tool on one indexed server: handshake, then tools/call. Every call
/// is a fresh session — the index has no standing connection to anyone.
pub async fn call(
    url: &str,
    headers: &HashMap<String, String>,
    tool: &str,
    args: &Value,
    timeout: u64,
) -> Result<Value, String> {
    let (_, sid) = handshake(url, headers, timeout).await?;
    let params = json!({ "name": tool, "arguments": args });
    let (v, _) = post_rpc(url, headers, "tools/call", params, sid.as_deref(), 3, timeout).await?;
    Ok(v)
}
