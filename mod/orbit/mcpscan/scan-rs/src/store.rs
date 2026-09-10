//! What one MCP server looks like to this module, and where the index lives.
//!
//! State is off-tree under ~/.mod/mcpscan:
//!   catalog.json  — every server ever seen, with its last probe
//!   sources.json  — the last crawl report per source
//!   server.secret — if present, the crawl/hunt triggers need it as a Bearer

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::path::PathBuf;
use std::time::{SystemTime, UNIX_EPOCH};

pub fn now() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0)
}

pub fn dir() -> PathBuf {
    if let Ok(d) = std::env::var("MCPSCAN_DIR") {
        return PathBuf::from(d);
    }
    let home = std::env::var("HOME").unwrap_or_else(|_| "/root".into());
    PathBuf::from(home).join(".mod").join("mcpscan")
}

/// A tool as the index remembers it: enough to search on, not enough to call
/// blind — the full schema comes from a live re-probe of that one server.
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct ToolLite {
    pub name: String,
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub description: String,
}

/// One MCP server anywhere on the internet.
///
/// `status` is the whole point of the module and means exactly this:
///   live    — it shook hands with us, anonymously, and listed its tools
///   auth    — the endpoint is there and answered, but wants a credential
///   error   — something HTTP-shaped answered and it wasn't MCP
///   down    — nothing answered at all
///   unknown — never probed yet, or there is no endpoint to probe (stdio only)
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct Entry {
    pub id: String,
    pub name: String,
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub title: String,
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub description: String,
    /// Streamable-HTTP/SSE endpoint. Empty for a package-only (stdio) server —
    /// still indexed, because it is still an MCP server that exists.
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub url: String,
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub transport: String,
    /// Every directory that listed it — a server in three directories is one row.
    #[serde(default)]
    pub sources: Vec<String>,
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub homepage: String,
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub repository: String,
    /// How to run it locally, e.g. "npm:@foo/bar", "docker:mcp/foo".
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub packages: Vec<String>,
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub auth_hint: String,
    #[serde(default)]
    pub first_seen: u64,
    #[serde(default)]
    pub last_seen: u64,

    // ── what the prober found ────────────────────────────────────────
    #[serde(default)]
    pub status: String,
    #[serde(default)]
    pub checked_at: u64,
    #[serde(default)]
    pub attempts: u32,
    /// Consecutive failures — the re-probe backoff reads this.
    #[serde(default)]
    pub fails: u32,
    #[serde(default)]
    pub latency_ms: u64,
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub protocol_version: String,
    #[serde(default, skip_serializing_if = "Value::is_null")]
    pub server_info: Value,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub tools: Vec<ToolLite>,
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub error: String,
    /// When the endpoint hunter last knocked on this server's domain, so a
    /// site that has no MCP endpoint isn't knocked on again next cycle.
    #[serde(default)]
    pub hunted_at: u64,

    /// Lowercased search haystack, rebuilt on load — never persisted.
    #[serde(skip)]
    pub hay: String,
}

impl Entry {
    pub fn rebuild_hay(&mut self) {
        let mut h = String::with_capacity(256);
        for s in [
            &self.id,
            &self.name,
            &self.title,
            &self.description,
            &self.url,
            &self.homepage,
            &self.repository,
        ] {
            h.push_str(s);
            h.push(' ');
        }
        for p in &self.packages {
            h.push_str(p);
            h.push(' ');
        }
        for t in &self.tools {
            h.push_str(&t.name);
            h.push(' ');
            h.push_str(&t.description);
            h.push(' ');
        }
        self.hay = h.to_lowercase();
    }

    /// Has an endpoint worth knocking on.
    pub fn probeable(&self) -> bool {
        self.url.starts_with("http://") || self.url.starts_with("https://")
    }

    /// The row the API and the console show.
    pub fn row(&self, with_tools: bool) -> Value {
        let mut v = json!({
            "id": self.id,
            "name": self.name,
            "title": self.title,
            "description": clip(&self.description, 400),
            "url": self.url,
            "transport": self.transport,
            "sources": self.sources,
            "homepage": self.homepage,
            "repository": self.repository,
            "packages": self.packages,
            "auth_hint": self.auth_hint,
            "status": if self.status.is_empty() { "unknown" } else { &self.status },
            "checked_at": self.checked_at,
            "attempts": self.attempts,
            "latency_ms": self.latency_ms,
            "protocolVersion": self.protocol_version,
            "serverInfo": self.server_info,
            "toolCount": self.tools.len(),
            "error": self.error,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
        });
        if with_tools {
            v["tools"] = json!(self.tools);
        } else {
            v["toolNames"] = json!(self.tools.iter().map(|t| &t.name).collect::<Vec<_>>());
        }
        v
    }
}

pub fn clip(s: &str, n: usize) -> String {
    if s.chars().count() <= n {
        return s.to_string();
    }
    s.chars().take(n).collect::<String>() + "…"
}

/// What one directory gave us on the last pass.
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct SourceReport {
    pub source: String,
    pub ok: bool,
    pub found: u64,
    /// What the directory says it holds, when it says so — a keyless crawl of
    /// Smithery reaches 500 of ~11k, and the console should admit that.
    #[serde(default, skip_serializing_if = "is_zero")]
    pub total: u64,
    pub new: u64,
    pub updated: u64,
    pub ms: u64,
    pub ran_at: u64,
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub error: String,
    /// Set when the source is real but needs a key this box doesn't have.
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub needs: String,
}

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct CatalogFile {
    #[serde(default)]
    pub entries: Vec<Entry>,
    #[serde(default)]
    pub saved_at: u64,
}

fn read_json<T: for<'a> Deserialize<'a> + Default>(path: &PathBuf) -> T {
    std::fs::read_to_string(path)
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or_default()
}

/// Written compactly and atomically: the catalogue is megabytes and is
/// rewritten while the prober keeps working.
fn write_json<T: Serialize>(path: &PathBuf, v: &T, pretty: bool) {
    if let Some(d) = path.parent() {
        let _ = std::fs::create_dir_all(d);
    }
    let body = if pretty { serde_json::to_string_pretty(v) } else { serde_json::to_string(v) };
    if let Ok(s) = body {
        let tmp = path.with_extension("tmp");
        if std::fs::write(&tmp, s).is_ok() {
            let _ = std::fs::rename(&tmp, path);
        }
    }
}

pub fn load_catalog() -> CatalogFile {
    read_json(&dir().join("catalog.json"))
}

pub fn save_catalog(c: &CatalogFile) {
    write_json(&dir().join("catalog.json"), c, false);
}

pub fn load_reports() -> Vec<SourceReport> {
    read_json(&dir().join("sources.json"))
}

pub fn save_reports(r: &[SourceReport]) {
    write_json(&dir().join("sources.json"), &r.to_vec(), true);
}

/// The gate on crawl/hunt triggers. None → open (dev default).
pub fn secret() -> Option<String> {
    if std::env::var("ACCESS_OPEN").ok().as_deref() == Some("1") {
        return None;
    }
    std::fs::read_to_string(dir().join("server.secret"))
        .ok()
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
}

/// Slug for an index id: lowercase, [a-z0-9._-], never empty.
pub fn slug(raw: &str) -> String {
    let s: String = raw
        .trim()
        .to_lowercase()
        .chars()
        .map(|c| if c.is_ascii_alphanumeric() || c == '-' || c == '.' || c == '_' { c } else { '-' })
        .collect();
    let mut s = s.trim_matches(['-', '.'].as_ref()).to_string();
    while s.contains("--") {
        s = s.replace("--", "-");
    }
    if s.is_empty() {
        "server".into()
    } else {
        clip(&s, 120).trim_end_matches('…').to_string()
    }
}

/// Compare endpoints without being fooled by a trailing slash or ://WWW.
pub fn canon_url(url: &str) -> String {
    let u = url.trim().trim_end_matches('/').to_lowercase();
    u.replacen("https://www.", "https://", 1).replacen("http://www.", "http://", 1)
}

pub fn host_of(url: &str) -> String {
    url.split("//").nth(1).unwrap_or(url).split(['/', ':', '?']).next().unwrap_or("").to_string()
}

fn is_zero(n: &u64) -> bool {
    *n == 0
}
