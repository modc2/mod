//! MCP client for upstream servers (Streamable HTTP, JSON-RPC 2.0).
//! Speaks both reply styles: plain application/json bodies and
//! text/event-stream (the response is mined for the event carrying our id).
//! Sessions (Mcp-Session-Id) are cached per server and re-established once
//! on failure, so stateful spec servers and stateless fleet servers both work.

use crate::store::{now, Probe, ServerEntry};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::sync::Mutex;
use std::time::{Duration, Instant};

pub const PROTOCOL_VERSION: &str = "2025-06-18";

static SESSIONS: Mutex<Option<HashMap<String, String>>> = Mutex::new(None);

fn session_get(id: &str) -> Option<String> {
    SESSIONS.lock().ok()?.as_ref()?.get(id).cloned()
}

fn session_set(id: &str, sid: Option<String>) {
    if let Ok(mut g) = SESSIONS.lock() {
        let map = g.get_or_insert_with(HashMap::new);
        match sid {
            Some(s) => {
                map.insert(id.to_string(), s);
            }
            None => {
                map.remove(id);
            }
        }
    }
}

/// How long a normal upstream request may take. The sweep uses its own much
/// shorter budget — see `SWEEP_TIMEOUT`.
pub fn default_timeout() -> u64 {
    std::env::var("MCP_UPSTREAM_TIMEOUT").ok().and_then(|v| v.parse().ok()).unwrap_or(60)
}

/// A port that nothing listens on refuses instantly; this budget only has to
/// cover a server that answers slowly, and the sweep tries ~200 of them.
pub const SWEEP_TIMEOUT: u64 = 5;

fn client(secs: u64) -> reqwest::Client {
    reqwest::Client::builder()
        .timeout(Duration::from_secs(secs))
        .build()
        .expect("reqwest client")
}

/// Where the scale-to-zero proxy lives. A fleet mod that has been put to sleep
/// refuses connections on its own port but wakes when reached through here.
fn activator() -> String {
    std::env::var("MCP_ACTIVATOR_URL").unwrap_or_else(|_| "http://127.0.0.1:9000".into())
}

/// The same endpoint, addressed through the activator so a sleeping mod is
/// woken instead of refused. Only local fleet mods get this — a remote URL is
/// never rewritten, and neither is one that already points at the proxy.
///
/// The proxy routes `/{mod}/…` to a mod's app and `/api/{mod}/…` to its API,
/// which is where MCP lives; either one wakes the mod.
pub fn wake_url(server: &ServerEntry) -> Option<String> {
    if server.source == "user" {
        return None;
    }
    let rest = server.url.split("//").nth(1)?;
    let (hostport, path) = match rest.find('/') {
        Some(i) => (&rest[..i], &rest[i..]),
        None => (rest, "/mcp"),
    };
    let host = hostport.split(':').next().unwrap_or("");
    if !matches!(host, "localhost" | "127.0.0.1" | "0.0.0.0") {
        return None;
    }
    let proxy = activator();
    if server.url.starts_with(&proxy) {
        return None;
    }
    Some(format!("{}/api/{}{}", proxy.trim_end_matches('/'), server.id, path))
}

/// The same server, addressed through the activator.
fn woken(server: &ServerEntry) -> Option<ServerEntry> {
    let url = wake_url(server)?;
    Some(ServerEntry { url, ..server.clone() })
}

/// Only a connection-level failure is worth retrying through the activator —
/// an HTTP or JSON-RPC error means something did answer.
fn is_unreachable(e: &str) -> bool {
    e.starts_with("unreachable")
}

/// Pull the JSON-RPC reply matching `want_id` out of a raw response body,
/// whether it's a bare JSON document or an SSE stream.
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
                let is_ours = v.get("id").and_then(|i| i.as_u64()) == Some(want_id);
                if is_ours {
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
    server: &ServerEntry,
    method: &str,
    params: Value,
    session: Option<&str>,
    rpc_id: u64,
    timeout: u64,
) -> Result<(Value, Option<String>), String> {
    let mut req = client(timeout)
        .post(&server.url)
        .header("Content-Type", "application/json")
        .header("Accept", "application/json, text/event-stream")
        .header("MCP-Protocol-Version", PROTOCOL_VERSION);
    if let Some(sid) = session {
        req = req.header("Mcp-Session-Id", sid);
    }
    for (k, v) in &server.headers {
        req = req.header(k.as_str(), v.as_str());
    }
    let body = json!({ "jsonrpc": "2.0", "id": rpc_id, "method": method, "params": params });
    let resp = req.json(&body).send().await.map_err(|e| format!("unreachable: {e}"))?;
    let status = resp.status();
    let new_session = resp
        .headers()
        .get("mcp-session-id")
        .and_then(|v| v.to_str().ok())
        .map(String::from);
    let ctype = resp
        .headers()
        .get("content-type")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("")
        .to_string();
    let text = resp.text().await.map_err(|e| format!("read failed: {e}"))?;
    if !status.is_success() {
        let snippet: String = text.chars().take(300).collect();
        return Err(format!("HTTP {status}: {snippet}"));
    }
    let reply = extract_reply(&text, &ctype, rpc_id)?;
    if let Some(err) = reply.get("error") {
        let msg = err.get("message").and_then(|m| m.as_str()).unwrap_or("rpc error");
        return Err(format!("{method}: {msg}"));
    }
    Ok((reply.get("result").cloned().unwrap_or(Value::Null), new_session))
}

/// Full handshake: initialize (+ initialized notification). Returns
/// (initialize result, session id if the server issued one).
async fn handshake(server: &ServerEntry, timeout: u64) -> Result<(Value, Option<String>), String> {
    let params = json!({
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": {},
        "clientInfo": { "name": "mcp-hub", "version": env!("CARGO_PKG_VERSION") }
    });
    let (result, sid) = post_rpc(server, "initialize", params, None, 1, timeout).await?;
    // Fire the initialized notification; stateless servers may 4xx it — ignore.
    let mut req = client(timeout)
        .post(&server.url)
        .header("Content-Type", "application/json")
        .header("Accept", "application/json, text/event-stream")
        .header("MCP-Protocol-Version", PROTOCOL_VERSION);
    if let Some(s) = &sid {
        req = req.header("Mcp-Session-Id", s.as_str());
    }
    for (k, v) in &server.headers {
        req = req.header(k.as_str(), v.as_str());
    }
    let _ = req
        .json(&json!({ "jsonrpc": "2.0", "method": "notifications/initialized" }))
        .send()
        .await;
    Ok((result, sid))
}

/// One request against an upstream with automatic (re-)handshake. A local mod
/// that is simply asleep is retried once through the activator, which wakes it:
/// calling a tool is exactly the moment the caller wants the mod running.
pub async fn rpc(server: &ServerEntry, method: &str, params: Value) -> Result<Value, String> {
    if server.is_stdio() {
        return crate::stdio::rpc(server, method, params).await;
    }
    match rpc_direct(server, method, params.clone()).await {
        Err(e) if is_unreachable(&e) => match woken(server) {
            Some(w) => rpc_direct(&w, method, params).await.map_err(|e2| {
                format!("{e} — and waking it through the activator failed too: {e2}")
            }),
            None => Err(e),
        },
        other => other,
    }
}

async fn rpc_direct(server: &ServerEntry, method: &str, params: Value) -> Result<Value, String> {
    let t = default_timeout();
    let cached = session_get(&server.id);
    if cached.is_some() {
        match post_rpc(server, method, params.clone(), cached.as_deref(), 2, t).await {
            Ok((v, _)) => return Ok(v),
            Err(_) => session_set(&server.id, None), // stale session — re-handshake below
        }
    }
    let (_, sid) = handshake(server, t).await?;
    session_set(&server.id, sid.clone());
    let (v, _) = post_rpc(server, method, params, sid.as_deref(), 2, t).await?;
    Ok(v)
}

/// initialize + tools/list → a Probe record (never panics, errors are data).
pub async fn probe(server: &ServerEntry) -> Probe {
    probe_in(server, default_timeout(), false).await
}

/// `wake` decides what an unreachable local mod means: during a background
/// re-probe it means "asleep, leave it alone" (the fleet scales to zero on
/// purpose), and on an explicit refresh it means "wake it and try again".
pub async fn probe_in(server: &ServerEntry, timeout: u64, wake: bool) -> Probe {
    // A stdio server is never "asleep" in the activator's sense — starting it
    // is the probe, so `wake` has nothing to decide. Background re-probes
    // would otherwise keep every installed process running forever, so they
    // only report what is already up.
    if server.is_stdio() {
        return if wake || crate::stdio::is_running(&server.id).await {
            crate::stdio::probe(server).await
        } else {
            Probe {
                ok: false,
                error: "not running (stdio server; a tool call or an explicit re-probe starts it)".into(),
                checked_at: now(),
                ..Default::default()
            }
        };
    }
    let p = probe_once(server, timeout).await;
    if p.ok || !wake || !is_unreachable(&p.error) {
        return p;
    }
    match woken(server) {
        Some(w) => {
            let mut woke = probe_once(&w, timeout.max(default_timeout())).await;
            if woke.ok {
                woke.via = w.url;
            } else {
                woke.error = format!("{} (asleep; waking it failed: {})", p.error, woke.error);
            }
            woke
        }
        None => p,
    }
}

async fn probe_once(server: &ServerEntry, timeout: u64) -> Probe {
    let started = Instant::now();
    let (init, sid) = match handshake(server, timeout).await {
        Ok(x) => x,
        Err(e) => {
            return Probe { ok: false, error: e, checked_at: now(), ..Default::default() }
        }
    };
    session_set(&server.id, sid.clone());
    let tools = match post_rpc(server, "tools/list", json!({}), sid.as_deref(), 2, timeout).await {
        Ok((v, _)) => v.get("tools").and_then(|t| t.as_array()).cloned().unwrap_or_default(),
        Err(e) => {
            return Probe {
                ok: false,
                error: format!("initialized but tools/list failed: {e}"),
                protocol_version: init
                    .get("protocolVersion")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .into(),
                server_info: init.get("serverInfo").cloned().unwrap_or(Value::Null),
                checked_at: now(),
                ..Default::default()
            }
        }
    };
    Probe {
        ok: true,
        protocol_version: init
            .get("protocolVersion")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .into(),
        server_info: init.get("serverInfo").cloned().unwrap_or(Value::Null),
        tools,
        latency_ms: started.elapsed().as_millis() as u64,
        checked_at: now(),
        error: String::new(),
        via: String::new(),
    }
}
