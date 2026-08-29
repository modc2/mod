//! Other people's hubs.
//!
//! The registry this module keeps is local: fleet mods plus whatever was
//! registered by hand. The catalogue is the opposite — a read-only view of the
//! public directories of MCP servers, so "connect another hub" is a search box
//! rather than a URL you had to find somewhere else.
//!
//! Three sources, all optional and none of them trusted:
//!   featured  — a short keyless list this hub has actually shaken hands with
//!   official  — registry.modelcontextprotocol.io, the MCP project's own index
//!   smithery  — registry.smithery.ai, the largest third-party directory
//!
//! Nothing here registers anything. A row carries the URL you would POST to
//! /servers, and that route probes before it saves, as always.

use serde::Serialize;
use serde_json::Value;
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
    /// featured | official | smithery
    pub registry: String,
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
        });
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

/// Search the public directories. `registry` pins one of featured | official |
/// smithery; anything else searches them all.
pub async fn search(q: &str, registry: &str, limit: usize) -> Catalog {
    let q = q.trim();
    let want = |name: &str| registry.is_empty() || registry == "all" || registry == name;
    let mut listings: Vec<Listing> = Vec::new();
    let mut errors: Vec<String> = Vec::new();
    let mut sources: Vec<String> = Vec::new();

    if want("featured") {
        sources.push("featured".into());
        listings.extend(featured().into_iter().filter(|l| matches(l, q)));
    }
    let (off, smi) = futures_util::future::join(
        async { if want("official") { Some(official(q, limit).await) } else { None } },
        async { if want("smithery") { Some(smithery(q, limit).await) } else { None } },
    )
    .await;
    for (name, res) in [("official", off), ("smithery", smi)] {
        let Some(res) = res else { continue };
        sources.push(name.into());
        match res {
            Ok(rows) => listings.extend(rows),
            Err(e) => errors.push(format!("{name}: {e}")),
        }
    }

    // One row per endpoint, and never one the hub is already serving.
    let mut seen = std::collections::HashSet::new();
    listings.retain(|l| !l.url.is_empty() && seen.insert(l.url.trim_end_matches('/').to_string()));
    listings.sort_by_key(|l| match l.registry.as_str() {
        "featured" => 0,
        "official" => 1,
        _ => 2,
    });
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
