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

fn find(name: &str) -> Result<Server, String> {
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
async fn rpc(server: &Server, method: &str, params: Value) -> Result<Value, String> {
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

fn text_of(result: &Value) -> String {
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
