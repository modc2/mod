//! Fleet discovery: every mod under MOD_ROOT that declares an MCP endpoint in
//! its config.json is a server we can aggregate.
//!
//! There is no single blessed spelling for that declaration in the fleet, so we
//! accept all three that mods actually use:
//!   * `endpoints.mcp`      — the route table style (chutes, targon, x, …)
//!   * `urls.mcp`           — a fully-resolved URL (bt, build, defi, docs, …)
//!   * a top-level `mcp` {} — the protocol block (hyperliquid, polymarket, …)
//!
//! URL resolution, first hit wins:
//!   explicit `urls.mcp` / `mcp.url` > {mcp.port|mcp_port|gateway_port|port|
//!   host:port parsed out of urls.api} + {path declared in the mcp block, else
//!   /mcp}.
//!
//! A mod that only declares a stdio command (no port anywhere) is skipped — the
//! hub aggregates Streamable HTTP endpoints only.

use crate::store::ServerEntry;
use serde_json::Value;
use std::collections::HashMap;
use std::path::Path;

fn mod_root() -> String {
    std::env::var("MOD_ROOT").unwrap_or_else(|_| "/root/mod/mod".into())
}

/// Does this config claim to speak MCP at all?
fn declares_mcp(cfg: &Value) -> bool {
    cfg.get("endpoints").and_then(|e| e.get("mcp")).is_some()
        || cfg.get("urls").and_then(|u| u.get("mcp")).is_some()
        || cfg.get("mcp").is_some()
}

/// Pull a request path out of a declaration that may be prose rather than a
/// path: `"/mcp"`, `"POST /mcp"`, `"POST /mcp (Streamable HTTP, JSON-RPC 2.0)"`.
fn path_from(decl: &str) -> Option<String> {
    let tok = decl.split_whitespace().find(|t| t.starts_with('/'))?;
    let tok = tok.trim_end_matches(|c: char| c == ',' || c == ')' || c == ';');
    if tok.len() > 1 { Some(tok.to_string()) } else { None }
}

/// The path the mod serves MCP on, from whichever key it used to say so.
fn declared_path(cfg: &Value) -> Option<String> {
    let mcp = cfg.get("mcp");
    if let Some(p) = mcp.and_then(|m| m.get("path")).and_then(|p| p.as_str()) {
        return path_from(p).or_else(|| Some(p.to_string()));
    }
    for key in ["http", "endpoint", "streamable_http", "url"] {
        if let Some(p) = mcp.and_then(|m| m.get(key)).and_then(|p| p.as_str()).and_then(path_from) {
            return Some(p);
        }
    }
    // `endpoints: { mcp: {...} }` — the key itself is the route; the value may
    // still spell out a different one.
    if let Some(e) = cfg.get("endpoints").and_then(|e| e.get("mcp")) {
        if let Some(p) = e.as_str().and_then(path_from) {
            return Some(p);
        }
    }
    None
}

/// `http://localhost:8919` / `http://localhost:3919/hyperliquid` -> 8919 / 3919.
fn port_in_url(u: &str) -> Option<u64> {
    let rest = u.split("://").nth(1)?;
    let hostport = rest.split('/').next()?;
    hostport.rsplit(':').next()?.parse().ok()
}

/// The port the mod's MCP transport listens on.
fn declared_port(cfg: &Value) -> Option<u64> {
    if let Some(p) = cfg.get("mcp").and_then(|m| m.get("port")).and_then(|p| p.as_u64()) {
        return Some(p);
    }
    for key in ["mcp_port", "gateway_port", "port", "api_port"] {
        if let Some(p) = cfg.get(key).and_then(|p| p.as_u64()) {
            return Some(p);
        }
    }
    if let Some(p) = cfg.get("api").and_then(|a| a.get("port")).and_then(|p| p.as_u64()) {
        return Some(p);
    }
    // Some mods carry no port field at all and only name themselves by URL.
    cfg.get("urls")
        .and_then(|u| u.get("api"))
        .and_then(|u| u.as_str())
        .and_then(port_in_url)
}

fn entry_from_config(dir_name: &str, cfg: &Value) -> Option<ServerEntry> {
    if !declares_mcp(cfg) {
        return None;
    }
    let name = cfg.get("name").and_then(|n| n.as_str()).unwrap_or(dir_name);
    if name == "mcp" {
        return None; // never aggregate ourselves
    }

    // An explicit absolute URL always wins — the mod already did the resolving.
    let explicit = cfg
        .get("urls")
        .and_then(|u| u.get("mcp"))
        .and_then(|u| u.as_str())
        .or_else(|| cfg.get("mcp").and_then(|m| m.get("url")).and_then(|u| u.as_str()))
        .filter(|u| u.starts_with("http://") || u.starts_with("https://"));

    let url = match explicit {
        Some(u) => u.to_string(),
        None => {
            let port = declared_port(cfg)?;
            let path = declared_path(cfg).unwrap_or_else(|| "/mcp".into());
            format!("http://localhost:{port}{path}")
        }
    };

    Some(ServerEntry {
        id: crate::store::clean_id(name),
        name: cfg
            .get("title")
            .and_then(|t| t.as_str())
            .unwrap_or(name)
            .to_string(),
        url,
        headers: HashMap::new(),
        source: "fleet".into(),
        note: cfg
            .get("description")
            .and_then(|d| d.as_str())
            .unwrap_or("")
            .chars()
            .take(200)
            .collect(),
        added_at: 0,
        ..Default::default()
    })
}

/// Scan {MOD_ROOT}/{core,orbit}/*/config.json for MCP-capable mods.
pub fn discover() -> HashMap<String, ServerEntry> {
    let mut found = HashMap::new();
    for ring in ["core", "orbit"] {
        let base = Path::new(&mod_root()).join(ring);
        let Ok(entries) = std::fs::read_dir(&base) else { continue };
        for e in entries.flatten() {
            let dir = e.path();
            let cfg_path = dir.join("config.json");
            let Ok(raw) = std::fs::read_to_string(&cfg_path) else { continue };
            let Ok(cfg) = serde_json::from_str::<Value>(&raw) else { continue };
            let dir_name = dir.file_name().and_then(|n| n.to_str()).unwrap_or("").to_string();
            if let Some(entry) = entry_from_config(&dir_name, &cfg) {
                // core wins on name collision (mirrors gateway routing)
                found.entry(entry.id.clone()).or_insert(entry);
            }
        }
    }
    found
}

// ── the live sweep ───────────────────────────────────────────────────
//
// `discover()` believes config.json. Plenty of mods serve /mcp and never say
// so (nyc and tdot both do), and a config can also point at the wrong port.
// The sweep takes the other route: collect every port any mod mentions, knock
// on /mcp, and keep whatever actually speaks MCP. It is deliberately a second
// pass rather than a replacement — a declared endpoint that is merely asleep
// must keep its row, and a sweep can only see what is awake right now.

/// Every port a config mentions, in the order they're worth trying.
fn all_ports(cfg: &Value) -> Vec<u64> {
    let mut ports: Vec<u64> = Vec::new();
    let mut push = |p: Option<u64>| {
        if let Some(p) = p.filter(|p| (1024..65536).contains(p)) {
            if !ports.contains(&p) {
                ports.push(p);
            }
        }
    };
    push(cfg.get("mcp").and_then(|m| m.get("port")).and_then(|p| p.as_u64()));
    for key in ["mcp_port", "port", "api_port", "gateway_port", "app_port"] {
        push(cfg.get(key).and_then(|p| p.as_u64()));
    }
    for sub in ["api", "app"] {
        push(cfg.get(sub).and_then(|s| s.get("port")).and_then(|p| p.as_u64()));
    }
    if let Some(urls) = cfg.get("urls").and_then(|u| u.as_object()) {
        for v in urls.values() {
            push(v.as_str().and_then(port_in_url));
        }
    }
    ports
}

/// Candidate (mod, port) pairs to knock on. Several per mod is normal — the
/// caller probes them all and keeps the first that answers.
pub fn sweep_candidates() -> Vec<ServerEntry> {
    let mut out = Vec::new();
    let self_port: u64 =
        std::env::var("MCP_PORT").ok().and_then(|p| p.parse().ok()).unwrap_or(50360);
    for ring in ["core", "orbit"] {
        let base = Path::new(&mod_root()).join(ring);
        let Ok(entries) = std::fs::read_dir(&base) else { continue };
        for e in entries.flatten() {
            let dir = e.path();
            let Ok(raw) = std::fs::read_to_string(dir.join("config.json")) else { continue };
            let Ok(cfg) = serde_json::from_str::<Value>(&raw) else { continue };
            let dir_name = dir.file_name().and_then(|n| n.to_str()).unwrap_or("");
            let name = cfg.get("name").and_then(|n| n.as_str()).unwrap_or(dir_name);
            if name == "mcp" || name.is_empty() {
                continue;
            }
            let path = declared_path(&cfg).unwrap_or_else(|| "/mcp".into());
            for port in all_ports(&cfg) {
                if port == self_port {
                    continue;
                }
                out.push(ServerEntry {
                    id: crate::store::clean_id(name),
                    name: cfg.get("title").and_then(|t| t.as_str()).unwrap_or(name).to_string(),
                    url: format!("http://127.0.0.1:{port}{path}"),
                    headers: HashMap::new(),
                    source: "sweep".into(),
                    note: cfg
                        .get("description")
                        .and_then(|d| d.as_str())
                        .unwrap_or("")
                        .chars()
                        .take(200)
                        .collect(),
                    added_at: 0,
                    ..Default::default()
                });
            }
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn url_for(cfg: Value) -> Option<String> {
        entry_from_config("d", &cfg).map(|e| e.url)
    }

    #[test]
    fn explicit_urls_mcp_wins() {
        // docs serves MCP behind its app route, not on the bare port
        let u = url_for(json!({"name": "docs", "port": 50191, "mcp_port": 50192,
                               "urls": {"mcp": "http://localhost:50191/docs/mcp"}}));
        assert_eq!(u.as_deref(), Some("http://localhost:50191/docs/mcp"));
    }

    #[test]
    fn top_level_mcp_block_with_own_port() {
        // polymarket: API on 50091, MCP on its own 50092
        let u = url_for(json!({"name": "polymarket", "port": 50091,
                               "mcp": {"port": 50092, "path": "/mcp"}}));
        assert_eq!(u.as_deref(), Some("http://localhost:50092/mcp"));
    }

    #[test]
    fn path_parsed_out_of_prose() {
        // hyperliquid has no port field at all — only urls.api
        let u = url_for(json!({"name": "hyperliquid",
                               "urls": {"api": "http://localhost:8919"},
                               "mcp": {"endpoint": "POST /mcp",
                                       "transport": "streamable-http + stdio"}}));
        assert_eq!(u.as_deref(), Some("http://localhost:8919/mcp"));
        assert_eq!(path_from("POST /mcp (Streamable HTTP, JSON-RPC 2.0)").as_deref(), Some("/mcp"));
    }

    #[test]
    fn endpoints_style_still_works() {
        let u = url_for(json!({"name": "chutes", "port": 50300, "endpoints": {"mcp": {"method": "POST"}}}));
        assert_eq!(u.as_deref(), Some("http://localhost:50300/mcp"));
    }

    #[test]
    fn no_mcp_declaration_is_skipped() {
        assert!(url_for(json!({"name": "plain", "port": 1234})).is_none());
    }

    #[test]
    fn stdio_only_without_any_port_is_skipped() {
        assert!(url_for(json!({"name": "s", "mcp": {"stdio": "foo --stdio"}})).is_none());
    }

    #[test]
    fn never_aggregates_itself() {
        assert!(url_for(json!({"name": "mcp", "port": 50360, "urls": {"mcp": "http://localhost:50360/mcp"}})).is_none());
    }
}
