//! Persistent hub state under ~/.mod/mcp (off-tree, never committed):
//!   hub.json    — user-registered servers + disabled fleet ids
//!   probes.json — last probe result per server id (survives restarts)
//!   reviews.json — last security review per server id (and per url, pre-add)
//!   server.secret — if present, mutating routes require it as a Bearer token

use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::{HashMap, HashSet};
use std::path::PathBuf;
use std::time::{SystemTime, UNIX_EPOCH};

pub fn now() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0)
}

pub fn port() -> u16 {
    std::env::var("MCP_PORT").ok().and_then(|p| p.parse().ok()).unwrap_or(50360)
}

/// Where this hub says it lives — what goes into client configs and into the
/// manifest a peer reads.
pub fn public_url() -> String {
    std::env::var("MCP_PUBLIC_URL")
        .map(|u| u.trim_end_matches('/').to_string())
        .unwrap_or_else(|_| format!("http://localhost:{}", port()))
}

pub fn hub_dir() -> PathBuf {
    if let Ok(d) = std::env::var("MCP_HUB_DIR") {
        return PathBuf::from(d);
    }
    let home = std::env::var("HOME").unwrap_or_else(|_| "/root".into());
    PathBuf::from(home).join(".mod").join("mcp")
}

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct ServerEntry {
    pub id: String,
    pub name: String,
    /// Streamable HTTP endpoint. Empty for a stdio server, which has no URL —
    /// it is a process this host starts.
    #[serde(default)]
    pub url: String,
    #[serde(default)]
    pub headers: HashMap<String, String>,
    /// "user" (registered via POST /servers) or "fleet" (auto-discovered mod)
    pub source: String,
    #[serde(default)]
    pub note: String,
    #[serde(default)]
    pub added_at: u64,
    /// "" / "http" — a URL we POST to; "stdio" — a process we start and speak
    /// JSON-RPC to over its stdin/stdout. Old rows have no field, hence the
    /// empty string meaning http.
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub transport: String,
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub command: String,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub args: Vec<String>,
    /// Environment handed to the child. Holds API keys, so it lives here in
    /// ~/.mod/mcp (off-tree) and the API never echoes the values.
    #[serde(default, skip_serializing_if = "HashMap::is_empty")]
    pub env: HashMap<String, String>,
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub cwd: String,
    /// Where this server came from — a GitHub repo, an npm package, a
    /// directory listing. Shown in the console so a row can be traced back.
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub origin: String,
}

impl ServerEntry {
    pub fn is_stdio(&self) -> bool {
        self.transport == "stdio"
    }

    /// What to show as "where this server is" — a URL, or the command line.
    pub fn location(&self) -> String {
        if self.is_stdio() {
            let mut parts = vec![self.command.clone()];
            parts.extend(self.args.iter().cloned());
            parts.join(" ")
        } else {
            self.url.clone()
        }
    }
}

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct Probe {
    pub ok: bool,
    #[serde(default)]
    pub protocol_version: String,
    #[serde(default)]
    pub server_info: Value,
    #[serde(default)]
    pub tools: Vec<Value>,
    #[serde(default)]
    pub latency_ms: u64,
    #[serde(default)]
    pub checked_at: u64,
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub error: String,
    /// Set when the reply came from somewhere other than the registered URL —
    /// in practice the activator, which woke a sleeping mod for us.
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub via: String,
}

/// One thing the reviewer noticed about a server. `severity` is one of
/// info | low | medium | high | critical; `tool` names the offending tool when
/// the finding is about a single tool rather than the server as a whole.
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct Finding {
    pub severity: String,
    pub title: String,
    #[serde(default)]
    pub detail: String,
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub tool: String,
    /// "static" (deterministic rule) or "agent" (the LLM reviewer said so)
    #[serde(default)]
    pub source: String,
}

/// A security review of one server: what it can do, what looked wrong, and
/// whether the hub thinks you should connect it.
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct Review {
    pub url: String,
    /// low | medium | high | critical | unknown
    pub risk: String,
    /// allow | caution | reject
    pub verdict: String,
    pub summary: String,
    #[serde(default)]
    pub findings: Vec<Finding>,
    /// Capability buckets the tool inventory falls into (exec, filesystem, …)
    #[serde(default)]
    pub capabilities: Vec<String>,
    /// Fingerprint of the tool inventory at review time — a later probe whose
    /// tools hash differently means the server changed its tools under us.
    #[serde(default)]
    pub tools_hash: String,
    #[serde(default)]
    pub tool_count: u64,
    /// Which reviewer produced the narrative half ("static" when no LLM ran)
    #[serde(default)]
    pub agent: String,
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub agent_error: String,
    #[serde(default)]
    pub checked_at: u64,
}

/// Another hub this one knows by URL — a peer mod-protocol hub or an index —
/// kept in hubs.json. Headers hold whatever the peer wants on every request.
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct PeerHub {
    pub id: String,
    #[serde(default)]
    pub name: String,
    pub url: String,
    /// mod | index
    #[serde(default)]
    pub kind: String,
    #[serde(default, skip_serializing_if = "HashMap::is_empty")]
    pub headers: HashMap<String, String>,
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub note: String,
    #[serde(default)]
    pub added_at: u64,
}

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct HubsFile {
    #[serde(default)]
    pub hubs: Vec<PeerHub>,
}

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct HubFile {
    #[serde(default)]
    pub servers: Vec<ServerEntry>,
    #[serde(default)]
    pub disabled: HashSet<String>,
    /// Mods the live sweep found serving MCP without declaring it in their
    /// config.json. Kept here so a restart doesn't lose them before the first
    /// sweep of the new process finishes.
    #[serde(default)]
    pub swept: Vec<ServerEntry>,
}

fn read_json<T: for<'a> Deserialize<'a> + Default>(path: &PathBuf) -> T {
    std::fs::read_to_string(path)
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or_default()
}

fn write_json<T: Serialize>(path: &PathBuf, v: &T) {
    if let Some(dir) = path.parent() {
        let _ = std::fs::create_dir_all(dir);
    }
    if let Ok(s) = serde_json::to_string_pretty(v) {
        let tmp = path.with_extension("tmp");
        if std::fs::write(&tmp, s).is_ok() {
            let _ = std::fs::rename(&tmp, path);
        }
    }
}

pub fn load_hub() -> HubFile {
    read_json(&hub_dir().join("hub.json"))
}

pub fn save_hub(h: &HubFile) {
    write_json(&hub_dir().join("hub.json"), h);
}

pub fn load_hubs() -> Vec<PeerHub> {
    read_json::<HubsFile>(&hub_dir().join("hubs.json")).hubs
}

pub fn save_hubs(h: &[PeerHub]) {
    write_json(&hub_dir().join("hubs.json"), &HubsFile { hubs: h.to_vec() });
}

pub fn load_probes() -> HashMap<String, Probe> {
    read_json(&hub_dir().join("probes.json"))
}

pub fn save_probes(p: &HashMap<String, Probe>) {
    write_json(&hub_dir().join("probes.json"), p);
}

pub fn load_reviews() -> HashMap<String, Review> {
    read_json(&hub_dir().join("reviews.json"))
}

pub fn save_reviews(r: &HashMap<String, Review>) {
    write_json(&hub_dir().join("reviews.json"), r);
}

/// Key a review by endpoint so a review run before registration can be adopted
/// by the server row once it's added. FNV-1a — a fingerprint, not a digest.
pub fn fnv1a(s: &str) -> String {
    let mut h: u64 = 0xcbf29ce484222325;
    for b in s.as_bytes() {
        h ^= *b as u64;
        h = h.wrapping_mul(0x100000001b3);
    }
    format!("{h:016x}")
}

pub fn url_key(url: &str) -> String {
    format!("url:{}", fnv1a(url.trim()))
}

/// The write-gate secret. None → open mode (no secret provisioned).
pub fn secret() -> Option<String> {
    if std::env::var("ACCESS_OPEN").ok().as_deref() == Some("1") {
        return None;
    }
    std::fs::read_to_string(hub_dir().join("server.secret"))
        .ok()
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
}

/// Sanitize a proposed server id: lowercase [a-z0-9_-], never empty.
pub fn clean_id(raw: &str) -> String {
    let id: String = raw
        .to_lowercase()
        .chars()
        .map(|c| if c.is_ascii_alphanumeric() || c == '-' || c == '_' { c } else { '-' })
        .collect();
    // "__" is the hub's namespace separator (server__tool) — collapse runs so
    // an id can never be mistaken for a namespaced tool name.
    let mut id = id.trim_matches('-').to_string();
    while id.contains("__") {
        id = id.replace("__", "_");
    }
    if id.is_empty() { "server".into() } else { id }
}
