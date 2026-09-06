//! The door a class calls *out* through.
//!
//! Everything else in this module is about code that cannot reach anything: a
//! wasm instance with no syscalls, a python subprocess with no `socket`. That
//! is the whole reason strangers' code can be run here, and it is not being
//! given up. So a class does not get a network — it gets a request.
//!
//!     the class says      arena::mcp("weather", "forecast", "{\"city\":\"Oslo\"}")
//!     the sandbox does    nothing; it hands the host a string
//!     the host asks       the arena, over one HTTP call it did not compose
//!     the arena calls     the MCP server, if that server is on the list
//!
//! Three things follow from routing it this way rather than opening a socket.
//! A class names a *server*, never a URL, so it cannot be talked into calling
//! somewhere else. Credentials for a server live here and are never handed to
//! the code that uses them. And every call is one place to see, count and cut
//! off — which matters, because a player that can call out is a player whose
//! move is no longer a pure function of its view.
//!
//! Say that last part plainly to anyone reading a leaderboard: a class with
//! MCP access is not sandboxed *from the world*, only from this machine. It is
//! off by default for that reason, and a match that used it is marked.

use crate::blobs;
use serde_json::{json, Value};
use std::fs;
use std::sync::OnceLock;
use std::time::Duration;

fn client() -> &'static reqwest::Client {
    static C: OnceLock<reqwest::Client> = OnceLock::new();
    C.get_or_init(|| {
        reqwest::Client::builder()
            .timeout(Duration::from_secs(60))
            .build()
            .unwrap_or_else(|_| reqwest::Client::new())
    })
}

fn config_file() -> std::path::PathBuf {
    blobs::state_dir().join("mcp_servers.json")
}

/// A server a class may call. `headers` is the reason this file lives off-tree
/// under `~/.mod/arena/` rather than in the repo.
#[derive(Clone, Debug)]
pub struct Server {
    pub name: String,
    pub url: String,
    pub description: String,
    pub headers: Vec<(String, String)>,
    pub enabled: bool,
}

impl Server {
    /// What a class or an agent is allowed to see: everything except how the
    /// call is authenticated.
    pub fn card(&self) -> Value {
        json!({
            "name": self.name,
            "url": self.url,
            "description": self.description,
            "enabled": self.enabled,
            "authenticated": !self.headers.is_empty(),
        })
    }
}

/// Every server on the list: this arena, plus whatever is configured.
///
/// The arena is always on it, and that is the interesting one — it is what
/// makes a class able to read the leaderboard it is playing on, look up
/// another module's source, or ask an agent in the next seat for advice.
pub fn servers() -> Vec<Server> {
    let mut out = vec![Server {
        name: "arena".into(),
        url: format!("{}/mcp", crate::mcp::base()),
        description: "this arena — modules, players, matches, the leaderboard, and one \
                      server per game and per agent under /m/<name>/mcp"
            .into(),
        headers: Vec::new(),
        enabled: true,
    }];

    let configured: Value = fs::read_to_string(config_file())
        .ok()
        .and_then(|t| serde_json::from_str(&t).ok())
        .unwrap_or_else(|| json!({ "servers": [] }));
    let list = configured
        .get("servers")
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();

    for entry in list {
        let name = entry.get("name").and_then(|v| v.as_str()).unwrap_or("").trim().to_string();
        let url = entry.get("url").and_then(|v| v.as_str()).unwrap_or("").trim().to_string();
        if name.is_empty() || url.is_empty() {
            continue;
        }
        let headers = entry
            .get("headers")
            .and_then(|v| v.as_object())
            .map(|o| {
                o.iter()
                    .filter_map(|(k, v)| v.as_str().map(|s| (k.clone(), s.to_string())))
                    .collect()
            })
            .unwrap_or_default();
        // A configured entry may replace the built-in arena one, which is how
        // you point a class at an arena that is not this process.
        out.retain(|s| s.name != name);
        out.push(Server {
            name,
            url,
            description: entry.get("description").and_then(|v| v.as_str()).unwrap_or("").into(),
            headers,
            enabled: entry.get("enabled").and_then(|v| v.as_bool()).unwrap_or(true),
        });
    }

    // `ARENA_MCP_ALLOW=a,b` narrows the list without editing the file — the
    // switch a match harness reaches for.
    if let Ok(allow) = std::env::var("ARENA_MCP_ALLOW") {
        let allowed: Vec<&str> = allow.split(',').map(str::trim).filter(|s| !s.is_empty()).collect();
        if !allowed.is_empty() {
            for s in out.iter_mut() {
                s.enabled = s.enabled && allowed.contains(&s.name.as_str());
            }
        }
    }
    out
}

pub fn list() -> Value {
    let all = servers();
    json!({
        "count": all.iter().filter(|s| s.enabled).count(),
        "servers": all.iter().map(|s| s.card()).collect::<Vec<_>>(),
        "config": config_file().to_string_lossy(),
        "how": "a class calls these by name — `arena::mcp(server, tool, args)` in Rust, \
                `self.mcp(server, tool, args)` in Python. It never sees a URL and never \
                opens a socket; this server makes the call.",
    })
}

// ── the fleet ────────────────────────────────────────────────────────────
//
// The list above is what a *class* may call — a short, deliberate, credentialed
// list, because that side is untrusted code. This part is the other question:
// which modules of this fleet could take a seat. Anyone asking that is the
// person running the arena, at their own console, so the answer can be the
// whole fleet rather than an allowlist.
//
// A module in this fleet answers at {gateway}/api/{name}, and its MCP server
// at {gateway}/api/{name}/mcp. Going through the gateway rather than a port is
// what wakes a module that the activator has put to sleep.

/// The fleet router. Every module is reachable behind it whether or not it is
/// awake — which a port is not.
pub fn gateway() -> String {
    std::env::var("ARENA_GATEWAY")
        .ok()
        .map(|s| s.trim().trim_end_matches('/').to_string())
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| "http://127.0.0.1:9000".to_string())
}

/// Where one fleet module's own MCP server answers.
pub fn module_mcp_url(name: &str) -> String {
    format!("{}/api/{}/mcp", gateway(), name.trim().trim_matches('/'))
}

/// A module of this fleet, addressed as a server. No headers: the gateway is
/// on this box, and a module that wants a token is one to add to the list
/// above by hand.
pub fn fleet_server(name: &str) -> Server {
    Server {
        name: name.trim().to_string(),
        url: module_mcp_url(name),
        description: format!("the {} module of this fleet, through the gateway", name.trim()),
        headers: Vec::new(),
        enabled: true,
    }
}

/// A server named however a player config named it: a configured server by
/// name, a fleet module by name, or a URL given outright. The first two are
/// tried in that order so a configured entry — the one that may carry a
/// credential — always wins over the bare gateway route of the same name.
pub fn resolve(server: Option<&str>, module: Option<&str>, url: Option<&str>) -> Result<Server, String> {
    if let Some(name) = server.map(str::trim).filter(|s| !s.is_empty()) {
        if let Ok(s) = find(name) {
            return Ok(s);
        }
    }
    for name in [server, module].into_iter().flatten().map(str::trim).filter(|s| !s.is_empty()) {
        return Ok(fleet_server(name));
    }
    if let Some(u) = url.map(str::trim).filter(|s| !s.is_empty()) {
        return Ok(Server {
            name: u.to_string(),
            url: u.to_string(),
            description: "named by URL".into(),
            headers: Vec::new(),
            enabled: true,
        });
    }
    Err("name a `module` of this fleet, a configured `server`, or a `url`".into())
}

/// What one server offers. Used by the console to fill in a tool picker, and
/// by the mcp player driver to work out which argument the view goes in.
pub async fn tools_of(server: &Server) -> Result<Vec<Value>, String> {
    let v = rpc(server, "tools/list", json!({})).await?;
    Ok(v.get("tools").and_then(|t| t.as_array()).cloned().unwrap_or_default())
}

/// The fleet, as far as this box can see it: every module the MCP hub module
/// has indexed, every module the activator manages, and the servers a class
/// may call out to. Each one is somewhere a player could be seated.
pub async fn fleet() -> Value {
    use std::collections::BTreeMap;
    let mut seen: BTreeMap<String, Value> = BTreeMap::new();
    let gw = gateway();

    // The MCP hub module indexes the fleet's servers and probes them, so it
    // knows both what exists and what is answering. It is the best answer
    // when it is up, and never the only one.
    let hub = client()
        .get(format!("{gw}/api/mcp/servers"))
        .timeout(Duration::from_secs(20))
        .send()
        .await
        .ok();
    if let Some(r) = hub {
        if let Ok(v) = r.json::<Value>().await {
            for s in v.get("servers").and_then(|a| a.as_array()).cloned().unwrap_or_default() {
                let id = s.get("id").or_else(|| s.get("name")).and_then(|v| v.as_str()).unwrap_or("").to_string();
                if id.is_empty() {
                    continue;
                }
                let probe = s.get("probe").cloned().unwrap_or(Value::Null);
                seen.insert(id.clone(), json!({
                    "name": id,
                    "title": s.get("name").and_then(|v| v.as_str()).unwrap_or(&id),
                    "description": s.get("note").and_then(|v| v.as_str()).unwrap_or(""),
                    "mcp": module_mcp_url(&id),
                    "direct": s.get("url").cloned().unwrap_or(Value::Null),
                    "tools": probe.get("toolCount").cloned().unwrap_or(Value::Null),
                    "up": probe.get("ok").and_then(|v| v.as_bool()).unwrap_or(false),
                    "source": "hub",
                }));
            }
        }
    }

    // The activator knows every module it can wake, which includes modules
    // asleep right now — exactly the ones a probe would have missed.
    let act = client()
        .get(format!("{gw}/_activator/state"))
        .timeout(Duration::from_secs(10))
        .send()
        .await
        .ok();
    if let Some(r) = act {
        if let Ok(v) = r.json::<Value>().await {
            for m in v.get("modules").and_then(|a| a.as_array()).cloned().unwrap_or_default() {
                let id = m.get("module").and_then(|v| v.as_str()).unwrap_or("").to_string();
                if id.is_empty() {
                    continue;
                }
                let running = m.get("running").and_then(|v| v.as_bool()).unwrap_or(false);
                seen.entry(id.clone())
                    .and_modify(|e| { e["managed"] = json!(true); e["running"] = json!(running); })
                    .or_insert_with(|| json!({
                        "name": id, "title": id, "description": "",
                        "mcp": module_mcp_url(&id), "direct": Value::Null,
                        "tools": Value::Null, "up": running, "running": running,
                        "managed": true, "source": "activator",
                    }));
            }
        }
    }

    for s in servers() {
        seen.entry(s.name.clone())
            .and_modify(|e| { e["callable_by_a_class"] = json!(s.enabled); })
            .or_insert_with(|| json!({
                "name": s.name, "title": s.name, "description": s.description,
                "mcp": s.url, "direct": s.url, "tools": Value::Null, "up": Value::Null,
                "callable_by_a_class": s.enabled, "source": "configured",
            }));
    }

    // Whoever is in the agent module can be seated by name, so hand the
    // console the list rather than making somebody guess at one.
    let agents = client()
        .get(format!("{gw}/api/agent/agents"))
        .timeout(Duration::from_secs(20))
        .send()
        .await
        .ok();
    let agents = match agents {
        Some(r) => r.json::<Value>().await.ok().unwrap_or(Value::Null),
        None => Value::Null,
    };

    json!({
        "gateway": gw,
        "count": seen.len(),
        "modules": seen.into_values().collect::<Vec<_>>(),
        "agents": agents.get("agents").cloned().unwrap_or_else(|| json!([])),
        "agent_host": agents.get("host").cloned().unwrap_or(Value::Null),
        "how": "every module here is one an `mcp` player can be seated on: it is asked \
                one of its own tools each move, and whatever it says back is read for a \
                move. `module` names it, `tool` says which tool, and the arena makes the \
                call through the gateway — which wakes a module that is asleep.",
    })
}

pub fn find(name: &str) -> Result<Server, String> {
    let all = servers();
    let hit = all
        .iter()
        .find(|s| s.name.eq_ignore_ascii_case(name.trim()))
        .cloned()
        .ok_or_else(|| {
            format!(
                "no MCP server called `{name}` — this arena knows {}. Add one to {} to \
                 make it callable.",
                all.iter().map(|s| s.name.as_str()).collect::<Vec<_>>().join(", "),
                config_file().to_string_lossy()
            )
        })?;
    if !hit.enabled {
        return Err(format!("the MCP server `{}` is switched off for this arena", hit.name));
    }
    Ok(hit)
}

/// One JSON-RPC round trip to an MCP server.
pub async fn rpc(server: &Server, method: &str, params: Value) -> Result<Value, String> {
    let body = json!({ "jsonrpc": "2.0", "id": 1, "method": method, "params": params });
    let mut req = client()
        .post(&server.url)
        .header("content-type", "application/json")
        .header("accept", "application/json, text/event-stream")
        .json(&body);
    for (k, v) in &server.headers {
        req = req.header(k.as_str(), v.as_str());
    }
    let resp = req
        .send()
        .await
        .map_err(|e| format!("{} is unreachable: {e}", server.url))?;
    let status = resp.status();
    let text = resp
        .text()
        .await
        .map_err(|e| format!("{} gave no body ({status}): {e}", server.url))?;
    if !status.is_success() {
        return Err(format!(
            "{} answered {status}: {}",
            server.url,
            text.chars().take(300).collect::<String>()
        ));
    }
    // Streamable HTTP servers may answer with SSE even for a single call.
    let payload = if text.trim_start().starts_with("event:") || text.contains("\ndata:") {
        text.lines()
            .filter_map(|l| l.strip_prefix("data:"))
            .map(str::trim)
            .last()
            .unwrap_or("")
            .to_string()
    } else {
        text
    };
    let value: Value = serde_json::from_str(&payload)
        .map_err(|e| format!("{} did not answer JSON-RPC ({e})", server.url))?;
    if let Some(err) = value.get("error") {
        let message = err.get("message").and_then(|m| m.as_str()).unwrap_or("call failed");
        return Err(format!("{}: {message}", server.name));
    }
    Ok(value.get("result").cloned().unwrap_or(value))
}

/// Call a tool on a named server. `__tools__` lists what it offers.
///
/// The reply is always JSON and never a raised error, because a class has to
/// be able to lose this call and carry on — over a network it eventually will.
pub async fn call(args: &Value) -> Value {
    let name = args.get("server").and_then(|v| v.as_str()).unwrap_or("").to_string();
    let tool = args.get("tool").and_then(|v| v.as_str()).unwrap_or("").to_string();
    let arguments = args
        .get("arguments")
        .cloned()
        .unwrap_or_else(|| json!({}));

    let server = match find(&name) {
        Ok(s) => s,
        Err(e) => return json!({ "error": e }),
    };

    if tool.trim().is_empty() || tool == "__tools__" {
        return match rpc(&server, "tools/list", json!({})).await {
            Ok(v) => json!({
                "server": server.name,
                "tools": v.get("tools").cloned().unwrap_or_else(|| json!([]))
            }),
            Err(e) => json!({ "error": e }),
        };
    }

    match rpc(&server, "tools/call", json!({ "name": tool, "arguments": arguments })).await {
        Ok(v) => {
            // An MCP result is `content` plus, on a good server, the same thing
            // structured. Hand back the structured form when there is one — a
            // class parsing a blob of text is nobody's idea of an interface.
            if v.get("isError").and_then(|b| b.as_bool()).unwrap_or(false) {
                return json!({ "error": text_of(&v), "server": server.name, "tool": tool });
            }
            if let Some(structured) = v.get("structuredContent") {
                return structured.clone();
            }
            let text = text_of(&v);
            // Text that is itself JSON is handed over as JSON.
            match serde_json::from_str::<Value>(&text) {
                Ok(parsed) if parsed.is_object() || parsed.is_array() => parsed,
                _ => json!({ "text": text }),
            }
        }
        Err(e) => json!({ "error": e }),
    }
}

pub fn text_of(result: &Value) -> String {
    result
        .get("content")
        .and_then(|c| c.as_array())
        .map(|items| {
            items
                .iter()
                .filter_map(|i| i.get("text").and_then(|t| t.as_str()))
                .collect::<Vec<_>>()
                .join("\n")
        })
        .unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_arena_is_always_on_the_list() {
        let all = servers();
        assert!(all.iter().any(|s| s.name == "arena" && s.enabled));
    }

    #[test]
    fn a_server_card_never_carries_the_credential() {
        let s = Server {
            name: "x".into(),
            url: "http://x/mcp".into(),
            description: String::new(),
            headers: vec![("authorization".into(), "Bearer hunter2".into())],
            enabled: true,
        };
        let card = s.card().to_string();
        assert!(!card.contains("hunter2"), "{card}");
        assert!(card.contains("\"authenticated\":true"));
    }

    #[tokio::test]
    async fn a_server_nobody_configured_is_an_error_and_not_a_connection() {
        let out = call(&json!({ "server": "nowhere", "tool": "anything" })).await;
        assert!(out["error"].as_str().unwrap_or("").contains("no MCP server"));
    }

    #[test]
    fn the_text_of_a_result_is_read_out_of_its_content() {
        let v = json!({ "content": [{ "type": "text", "text": "hello" }] });
        assert_eq!(text_of(&v), "hello");
    }
}
