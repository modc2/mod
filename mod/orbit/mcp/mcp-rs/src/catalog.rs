//! Other people's hubs.
//!
//! The registry this module keeps is local: fleet mods plus whatever was
//! registered by hand. The catalogue is the opposite — a read-only view of the
//! public directories of MCP servers, so "connect another hub" is a search box
//! rather than a URL you had to find somewhere else.
//!
//! Sources, all optional and none of them trusted — see hubs.rs for the list
//! of hub types this searches across:
//!   featured  — a short keyless list this hub has actually shaken hands with
//!   official  — registry.modelcontextprotocol.io, the MCP project's own index
//!   smithery  — registry.smithery.ai, the largest third-party directory
//!   glama / pulsemcp — keyed directories, searched only when a key is present
//!   <index>   — every index hub the fleet runs (mcpscan), with live status
//!   <peer>    — every mod hub added by URL, its registry read live
//!
//! Nothing here registers anything. A row carries the URL you would POST to
//! /servers, and that route probes before it saves, as always.

use crate::state::AppState;
use serde::Serialize;
use serde_json::Value;
use std::collections::HashMap;
use std::sync::Arc;
use std::time::Duration;

#[derive(Clone, Debug, Default, Serialize)]
pub struct Listing {
    /// Suggested hub id — the caller may override it when registering.
    pub id: String,
    pub name: String,
    #[serde(skip_serializing_if = "String::is_empty")]
    pub description: String,
    /// Streamable HTTP endpoint, ready to hand to POST /servers.
    pub url: String,
    /// featured | official | smithery | glama | pulsemcp | an index id | a peer hub id
    pub registry: String,
    /// Set when the URL is another hub's MCP endpoint rather than the server
    /// itself: connect the hub once and this row's tools arrive nested.
    #[serde(skip_serializing_if = "String::is_empty")]
    pub via: String,
    /// live | auth | down | error | unknown — what an index last saw.
    #[serde(skip_serializing_if = "String::is_empty")]
    pub status: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tools: Option<u64>,
    #[serde(skip_serializing_if = "String::is_empty")]
    pub homepage: String,
    #[serde(skip_serializing_if = "std::ops::Not::not")]
    pub verified: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub uses: Option<u64>,
    /// True when the endpoint will refuse an anonymous handshake — the row is
    /// still worth showing, but it needs an Authorization header to register.
    #[serde(skip_serializing_if = "std::ops::Not::not")]
    pub needs_key: bool,
    #[serde(skip_serializing_if = "String::is_empty")]
    pub note: String,
}

fn client() -> reqwest::Client {
    reqwest::Client::builder()
        .timeout(Duration::from_secs(20))
        .user_agent("mcp-hub")
        .build()
        .expect("reqwest client")
}

fn clip(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}

/// Servers with no account, no key and no signup, each one handshaken from
/// this host before it went in the list. The answer to "I just want it to
/// work" — and to "let me search the web".
pub fn featured() -> Vec<Listing> {
    let f = |id: &str, name: &str, url: &str, home: &str, desc: &str| Listing {
        id: id.into(),
        name: name.into(),
        description: desc.into(),
        url: url.into(),
        registry: "featured".into(),
        homepage: home.into(),
        verified: true,
        uses: None,
        needs_key: false,
        note: "no API key".into(),
        ..Default::default()
    };
    vec![
        f(
            "keenable",
            "Keenable Web Search",
            crate::web::KEENABLE_URL,
            "https://keenable.ai/",
            "Search the web and read any indexed page as markdown. The same server behind this hub's own web_search.",
        ),
        f(
            "deepwiki",
            "DeepWiki",
            "https://mcp.deepwiki.com/mcp",
            "https://deepwiki.com/",
            "Ask questions about any public GitHub repository and read its generated documentation.",
        ),
        f(
            "context7",
            "Context7",
            "https://mcp.context7.com/mcp",
            "https://context7.com/",
            "Up-to-date API documentation and code examples for thousands of libraries, fetched per version.",
        ),
        f(
            "gitmcp",
            "GitMCP",
            "https://gitmcp.io/docs",
            "https://gitmcp.io/",
            "Turns any GitHub repository into a documentation server — search its docs and source from the client.",
        ),
    ]
}

fn matches(l: &Listing, q: &str) -> bool {
    if q.is_empty() {
        return true;
    }
    let hay = format!("{} {} {}", l.name, l.id, l.description).to_lowercase();
    q.split_whitespace().all(|term| hay.contains(&term.to_lowercase()))
}

/// registry.modelcontextprotocol.io — the MCP project's own index.
async fn official(q: &str, limit: usize) -> Result<Vec<Listing>, String> {
    let limit = limit.clamp(1, 100).to_string();
    let mut req = client()
        .get("https://registry.modelcontextprotocol.io/v0/servers")
        .query(&[("limit", limit.as_str()), ("version", "latest")]);
    if !q.is_empty() {
        req = req.query(&[("search", q)]);
    }
    let r = req.send().await.map_err(|e| e.to_string())?;
    let status = r.status();
    let body = r.text().await.map_err(|e| e.to_string())?;
    if !status.is_success() {
        return Err(format!("HTTP {status}: {}", clip(body.trim(), 160)));
    }
    let v: Value = serde_json::from_str(&body).map_err(|e| format!("bad JSON: {e}"))?;
    let mut out = Vec::new();
    for row in v["servers"].as_array().cloned().unwrap_or_default() {
        let s = &row["server"];
        let name = s["name"].as_str().unwrap_or_default();
        // Only remote endpoints can be aggregated; a package-only entry is a
        // stdio server someone has to install, which is not this hub's job.
        let Some(remote) = s["remotes"].as_array().and_then(|a| {
            a.iter()
                .find(|r| {
                    r["type"].as_str().map(|t| t.contains("http")).unwrap_or(false)
                        && r["url"].as_str().is_some()
                })
                .cloned()
        }) else {
            continue;
        };
        let url = remote["url"].as_str().unwrap_or_default().to_string();
        let needs_key = remote["headers"].as_array().map(|h| !h.is_empty()).unwrap_or(false);
        out.push(Listing {
            id: crate::store::clean_id(name.rsplit('/').next().unwrap_or(name)),
            name: s["title"].as_str().filter(|t| !t.is_empty()).unwrap_or(name).to_string(),
            description: clip(s["description"].as_str().unwrap_or_default(), 300),
            url,
            registry: "official".into(),
            homepage: s["repository"]["url"].as_str().unwrap_or_default().to_string(),
            verified: true,
            uses: None,
            needs_key,
            note: if needs_key { "the registry says this endpoint wants a header".into() } else { String::new() },
            ..Default::default()
        });
    }
    Ok(out)
}

/// registry.smithery.ai — listing is public; connecting to a hosted server
/// there needs a Smithery API key, so those rows say so.
async fn smithery(q: &str, limit: usize) -> Result<Vec<Listing>, String> {
    let size = limit.clamp(1, 50).to_string();
    let mut req = client()
        .get("https://registry.smithery.ai/servers")
        .query(&[("pageSize", size.as_str())]);
    if !q.is_empty() {
        req = req.query(&[("q", q)]);
    }
    if let Some(k) = crate::web::key("smithery") {
        req = req.bearer_auth(k);
    }
    let r = req.send().await.map_err(|e| e.to_string())?;
    let status = r.status();
    let body = r.text().await.map_err(|e| e.to_string())?;
    if !status.is_success() {
        return Err(format!("HTTP {status}: {}", clip(body.trim(), 160)));
    }
    let v: Value = serde_json::from_str(&body).map_err(|e| format!("bad JSON: {e}"))?;
    let key = crate::web::key("smithery");
    let mut out = Vec::new();
    for s in v["servers"].as_array().cloned().unwrap_or_default() {
        if !s["remote"].as_bool().unwrap_or(false) {
            continue; // a container Smithery would have to host for us
        }
        let qualified = s["qualifiedName"].as_str().unwrap_or_default();
        if qualified.is_empty() {
            continue;
        }
        let url = match &key {
            Some(k) => format!("https://server.smithery.ai/{qualified}/mcp?api_key={k}"),
            None => format!("https://server.smithery.ai/{qualified}/mcp"),
        };
        out.push(Listing {
            id: crate::store::clean_id(qualified.rsplit('/').next().unwrap_or(qualified)),
            name: s["displayName"].as_str().unwrap_or(qualified).to_string(),
            description: clip(s["description"].as_str().unwrap_or_default(), 300),
            url,
            registry: "smithery".into(),
            homepage: s["homepage"].as_str().unwrap_or_default().to_string(),
            verified: s["verified"].as_bool().unwrap_or(false),
            uses: s["useCount"].as_u64(),
            needs_key: key.is_none(),
            note: if key.is_none() {
                "needs a Smithery API key — put it in ~/.mod/mcp/web.json as `smithery`".into()
            } else {
                String::new()
            },
            ..Default::default()
        });
    }
    Ok(out)
}

/// glama.ai — keyed. The API's row shape is not pinned down publicly, so the
/// endpoint is looked for under every name it has been seen with; a row with
/// none is a package someone has to install, not something to connect.
async fn glama(q: &str, limit: usize) -> Result<Vec<Listing>, String> {
    let key = crate::web::key("glama").ok_or("no key — set GLAMA_API_KEY or web.json `glama`")?;
    let first = limit.clamp(1, 100).to_string();
    let mut req = client()
        .get("https://glama.ai/api/mcp/v1/servers")
        .bearer_auth(key)
        .query(&[("first", first.as_str())]);
    if !q.is_empty() {
        req = req.query(&[("query", q)]);
    }
    let r = req.send().await.map_err(|e| e.to_string())?;
    let status = r.status();
    let body = r.text().await.map_err(|e| e.to_string())?;
    if !status.is_success() {
        return Err(format!("HTTP {status}: {}", clip(body.trim(), 160)));
    }
    let v: Value = serde_json::from_str(&body).map_err(|e| format!("bad JSON: {e}"))?;
    let rows = v["servers"].as_array().cloned().unwrap_or_default();
    Ok(rows.iter().filter_map(|s| {
        let url = remote_url(s)?;
        let name = s["name"].as_str().unwrap_or_default();
        Some(Listing {
            id: crate::store::clean_id(name.rsplit('/').next().unwrap_or(name)),
            name: name.to_string(),
            description: clip(s["description"].as_str().unwrap_or_default(), 300),
            url,
            registry: "glama".into(),
            homepage: s["url"].as_str().or(s["repository"]["url"].as_str()).unwrap_or_default().to_string(),
            verified: false,
            uses: None,
            needs_key: false,
            note: "listed by Glama — attribution: glama.ai".into(),
            ..Default::default()
        })
    }).collect())
}

/// pulsemcp.com v0.1 — keyed (the keyless v0beta is being sunset).
async fn pulsemcp(q: &str, limit: usize) -> Result<Vec<Listing>, String> {
    let key = crate::web::key("pulsemcp").ok_or("no key — set PULSEMCP_API_KEY or web.json `pulsemcp`")?;
    let n = limit.clamp(1, 100).to_string();
    let mut req = client()
        .get("https://api.pulsemcp.com/v0.1/servers")
        .header("X-API-Key", key)
        .query(&[("count_per_page", n.as_str())]);
    if !q.is_empty() {
        req = req.query(&[("query", q)]);
    }
    let r = req.send().await.map_err(|e| e.to_string())?;
    let status = r.status();
    let body = r.text().await.map_err(|e| e.to_string())?;
    if !status.is_success() {
        return Err(format!("HTTP {status}: {}", clip(body.trim(), 160)));
    }
    let v: Value = serde_json::from_str(&body).map_err(|e| format!("bad JSON: {e}"))?;
    let rows = v["servers"].as_array().cloned().unwrap_or_default();
    Ok(rows.iter().filter_map(|s| {
        let url = remote_url(s)?;
        let name = s["name"].as_str().unwrap_or_default();
        Some(Listing {
            id: crate::store::clean_id(name.rsplit('/').next().unwrap_or(name)),
            name: name.to_string(),
            description: clip(s["short_description"].as_str().or(s["description"].as_str()).unwrap_or_default(), 300),
            url,
            registry: "pulsemcp".into(),
            homepage: s["url"].as_str().or(s["source_code_url"].as_str()).unwrap_or_default().to_string(),
            verified: false,
            uses: s["github_stars"].as_u64(),
            needs_key: false,
            note: String::new(),
            ..Default::default()
        })
    }).collect())
}

/// The remote endpoint of a directory row, under whichever name the directory
/// uses for it. None = stdio/package only.
fn remote_url(s: &Value) -> Option<String> {
    if let Some(r) = s["remotes"].as_array().and_then(|a| a.iter().find(|r| r["url"].as_str().is_some())) {
        return r["url"].as_str().map(String::from);
    }
    for k in ["remoteUrl", "remote_url", "endpoint", "mcpUrl", "mcp_url", "streamableHttpUrl"] {
        if let Some(u) = s[k].as_str().filter(|u| u.starts_with("http")) {
            return Some(u.to_string());
        }
    }
    None
}

/// An index hub (mcpscan): its /catalog, which carries a probe status and the
/// real tool list per row. `source` narrows to one of the directories it
/// mirrors (docker, github, …).
async fn index(hub: &crate::hubs::Hub, source: &str, q: &str, limit: usize) -> Result<Vec<Listing>, String> {
    let n = limit.clamp(1, 100).to_string();
    let mut req = client()
        .get(format!("{}/catalog", hub.url))
        .query(&[("limit", n.as_str()), ("sort", "relevance")]);
    if !q.is_empty() {
        req = req.query(&[("q", q)]);
    }
    if source.is_empty() {
        req = req.query(&[("status", "live")]);
    } else {
        req = req.query(&[("source", source)]);
    }
    let r = req.send().await.map_err(|e| e.to_string())?;
    let status = r.status();
    let body = r.text().await.map_err(|e| e.to_string())?;
    if !status.is_success() {
        return Err(format!("HTTP {status}: {}", clip(body.trim(), 160)));
    }
    let v: Value = serde_json::from_str(&body).map_err(|e| format!("bad JSON: {e}"))?;
    let registry = if source.is_empty() { hub.id.clone() } else { format!("{}:{}", hub.id, source) };
    Ok(v["servers"].as_array().cloned().unwrap_or_default().iter().filter_map(|s| {
        let url = s["url"].as_str().filter(|u| u.starts_with("http"))?.to_string();
        let name = s["name"].as_str().unwrap_or_default();
        let st = s["status"].as_str().unwrap_or("unknown");
        let listed: Vec<&str> = s["sources"].as_array().map(|a| a.iter().filter_map(|x| x.as_str()).collect()).unwrap_or_default();
        Some(Listing {
            id: crate::store::clean_id(s["id"].as_str().map(|i| i.rsplit('.').next().unwrap_or(i)).unwrap_or(name)),
            name: s["title"].as_str().filter(|t| !t.is_empty()).unwrap_or(name).to_string(),
            description: clip(s["description"].as_str().unwrap_or_default(), 300),
            url,
            registry: registry.clone(),
            homepage: s["homepage"].as_str().or(s["repository"].as_str()).unwrap_or_default().to_string(),
            verified: st == "live",
            uses: None,
            needs_key: st == "auth",
            note: match st {
                "live" => format!("live · listed by {}", listed.join(", ")),
                "auth" => "the endpoint exists but refused an anonymous handshake — needs a header".into(),
                other => format!("last probe: {other}"),
            },
            status: st.to_string(),
            tools: s["toolCount"].as_u64(),
            ..Default::default()
        })
    }).collect())
}

/// A peer mod hub: GET /servers, read live. A row whose URL is only reachable
/// from the peer's own host (localhost) is offered through the peer's MCP
/// endpoint instead, marked `via`.
async fn peer(hub: &crate::hubs::Hub, headers: &HashMap<String, String>, q: &str, limit: usize) -> Result<Vec<Listing>, String> {
    let mut req = client().get(format!("{}/servers", hub.url));
    for (k, v) in headers {
        req = req.header(k.as_str(), v.as_str());
    }
    let r = req.send().await.map_err(|e| e.to_string())?;
    let status = r.status();
    let body = r.text().await.map_err(|e| e.to_string())?;
    if !status.is_success() {
        return Err(format!("HTTP {status}: {}", clip(body.trim(), 160)));
    }
    let v: Value = serde_json::from_str(&body).map_err(|e| format!("bad JSON: {e}"))?;
    let rows = v["servers"].as_array().cloned().unwrap_or_default();
    let mut out = Vec::new();
    for s in rows {
        let id = s["id"].as_str().unwrap_or_default();
        let url = s["url"].as_str().unwrap_or_default();
        if id.is_empty() || url.is_empty() {
            continue;
        }
        let host = url.split("//").nth(1).unwrap_or("").split(['/', ':']).next().unwrap_or("");
        let local = matches!(host, "localhost" | "127.0.0.1" | "0.0.0.0");
        let ok = s["probe"]["ok"].as_bool().unwrap_or(false);
        let mut l = Listing {
            id: crate::store::clean_id(id),
            name: s["name"].as_str().unwrap_or(id).to_string(),
            description: clip(s["note"].as_str().unwrap_or_default(), 300),
            url: if local { hub.mcp.clone() } else { url.to_string() },
            registry: hub.id.clone(),
            homepage: String::new(),
            verified: ok,
            uses: None,
            needs_key: false,
            note: if local {
                format!("behind the {} hub — connect that hub and this is {}__{}__*", hub.id, hub.id, id)
            } else {
                format!("registered on the {} hub", hub.id)
            },
            via: if local { hub.id.clone() } else { String::new() },
            status: if ok { "live".into() } else { "down".into() },
            tools: s["probe"]["toolCount"].as_u64(),
        };
        if !matches(&l, q) {
            continue;
        }
        if l.description.is_empty() {
            l.description = format!("{} tools", l.tools.unwrap_or(0));
        }
        out.push(l);
        if out.len() >= limit {
            break;
        }
    }
    Ok(out)
}

#[derive(Debug, Serialize)]
pub struct Catalog {
    pub query: String,
    pub count: usize,
    pub listings: Vec<Listing>,
    /// Registries that failed, with why — a directory being down should not
    /// look like a directory being empty.
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub errors: Vec<String>,
    pub sources: Vec<String>,
}

/// Search across hubs. `registry` pins one — featured | official | smithery |
/// glama | pulsemcp | an index id (`mcpscan`, or `mcpscan:docker` for one of
/// the directories it mirrors) | a peer hub id — and `all` searches every hub
/// that is usable right now (keyed directories only when their key is set).
pub async fn search(state: &Arc<AppState>, q: &str, registry: &str, limit: usize) -> Catalog {
    let q = q.trim();
    let all = registry.is_empty() || registry == "all";
    let want = |name: &str| all || registry == name;
    let mut listings: Vec<Listing> = Vec::new();
    let mut errors: Vec<String> = Vec::new();
    let mut sources: Vec<String> = Vec::new();

    if want("featured") {
        sources.push("featured".into());
        listings.extend(featured().into_iter().filter(|l| matches(l, q)));
    }

    // Every job yields (source name, result); the keyed directories only
    // join `all` when they can actually answer.
    let hubs = crate::hubs::known(state, &crate::store::public_url()).await;
    let peers = state.peers.read().await.clone();
    let mut jobs: Vec<std::pin::Pin<Box<dyn std::future::Future<Output = (String, Result<Vec<Listing>, String>)> + Send>>> = Vec::new();
    if want("official") {
        jobs.push(Box::pin(async move { ("official".to_string(), official(q, limit).await) }));
    }
    if want("smithery") {
        jobs.push(Box::pin(async move { ("smithery".to_string(), smithery(q, limit).await) }));
    }
    if want("glama") && (!all || crate::web::key("glama").is_some()) {
        jobs.push(Box::pin(async move { ("glama".to_string(), glama(q, limit).await) }));
    }
    if want("pulsemcp") && (!all || crate::web::key("pulsemcp").is_some()) {
        jobs.push(Box::pin(async move { ("pulsemcp".to_string(), pulsemcp(q, limit).await) }));
    }
    for h in hubs.into_iter().filter(|h| !h.is_self) {
        match h.kind.as_str() {
            "index" => {
                let source = registry.strip_prefix(&format!("{}:", h.id)).unwrap_or("").to_string();
                if want(&h.id) || !source.is_empty() {
                    let name = if source.is_empty() { h.id.clone() } else { registry.to_string() };
                    jobs.push(Box::pin(async move { (name, index(&h, &source, q, limit).await) }));
                }
            }
            "mod" if want(&h.id) => {
                let headers = peers.iter().find(|p| p.id == h.id).map(|p| p.headers.clone()).unwrap_or_default();
                jobs.push(Box::pin(async move { (h.id.clone(), peer(&h, &headers, q, limit).await) }));
            }
            _ => {}
        }
    }
    for (name, res) in futures_util::future::join_all(jobs).await {
        sources.push(name.clone());
        match res {
            Ok(rows) => listings.extend(rows),
            Err(e) => errors.push(format!("{name}: {e}")),
        }
    }
    if !all && sources.is_empty() {
        errors.push(format!("no hub called `{registry}` — GET /hubs lists them"));
    }

    // One row per endpoint (a peer's nested rows share one endpoint, so the
    // key includes what they are), and the index outranks a directory that
    // merely lists the same server, because it knows whether it answers.
    let rank = |l: &Listing| match l.registry.as_str() {
        "featured" => 0,
        r if r.starts_with("mcpscan") || l.status == "live" => 1,
        "official" => 2,
        _ => 3,
    };
    listings.sort_by_key(rank);
    let mut seen = std::collections::HashSet::new();
    listings.retain(|l| !l.url.is_empty() && seen.insert(format!("{}|{}", l.url.trim_end_matches('/'), l.via.is_empty().then_some("").unwrap_or(&l.id))));
    Catalog { query: q.to_string(), count: listings.len(), listings, errors, sources }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn featured_rows_are_complete() {
        for l in featured() {
            assert!(l.url.starts_with("https://"), "{} has no endpoint", l.id);
            assert!(!l.description.is_empty(), "{} has no description", l.id);
            assert!(!l.needs_key, "{} is listed as keyless", l.id);
        }
    }

    #[test]
    fn every_term_must_match() {
        let rows = featured();
        let web = rows.iter().find(|l| l.id == "keenable").unwrap();
        assert!(matches(web, "web search"));
        assert!(matches(web, ""));
        assert!(!matches(web, "web kubernetes"));
    }
}
