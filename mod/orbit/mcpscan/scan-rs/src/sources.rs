//! The crawlers. Every public place that lists MCP servers, read on a loop.
//!
//! Each source is a function that returns rows; nothing here probes anything
//! or decides what is real — that is the prober's job. A source that needs a
//! key this box doesn't have reports `needs` and contributes nothing, rather
//! than failing the crawl.
//!
//! Keyless by default: the official registry (registry.modelcontextprotocol.io),
//! Smithery (registry.smithery.ai), Docker's MCP registry and GitHub search all
//! answer anonymously. PulseMCP and Glama want an API key; set one and they
//! join the rotation.

use crate::store::{clip, slug, Entry};
use crate::upstream::client;
use serde_json::Value;
use std::sync::atomic::{AtomicU64, Ordering};

/// Every source id the module knows, in the order the console lists them.
pub const ALL: [&str; 6] = ["official", "smithery", "docker", "github", "pulsemcp", "glama"];

fn env(k: &str) -> Option<String> {
    std::env::var(k).ok().map(|v| v.trim().to_string()).filter(|v| !v.is_empty())
}

fn max_pages(default: u32) -> u32 {
    env("MCPSCAN_MAX_PAGES").and_then(|v| v.parse().ok()).unwrap_or(default)
}

fn base(id: String, name: String, source: &str) -> Entry {
    Entry { id, name, sources: vec![source.to_string()], ..Default::default() }
}

async fn get_json(url: &str, headers: &[(&str, String)]) -> Result<Value, String> {
    let mut req = client(30).get(url).header("Accept", "application/json");
    for (k, v) in headers {
        req = req.header(*k, v.as_str());
    }
    let resp = req.send().await.map_err(|e| format!("{url} unreachable: {e}"))?;
    let status = resp.status();
    let text = resp.text().await.map_err(|e| format!("read failed: {e}"))?;
    if !status.is_success() {
        return Err(format!("HTTP {} from {url}: {}", status.as_u16(), clip(text.trim(), 160)));
    }
    serde_json::from_str(&text).map_err(|e| format!("bad JSON from {url}: {e}"))
}

// ── registry.modelcontextprotocol.io — the MCP project's own index ────

/// Cursor-paginated, `version=latest` so a server with forty published
/// versions is one row. `remotes[]` is where the endpoints live.
pub async fn official() -> Result<(Vec<Entry>, u64), String> {
    let root = env("MCPSCAN_REGISTRY_URL")
        .unwrap_or_else(|| "https://registry.modelcontextprotocol.io".into());
    let mut out = Vec::new();
    let mut cursor: Option<String> = None;
    for _ in 0..max_pages(400) {
        let url = match &cursor {
            Some(c) => format!(
                "{}/v0/servers?limit=100&version=latest&cursor={}",
                root.trim_end_matches('/'),
                urlencode(c)
            ),
            None => format!("{}/v0/servers?limit=100&version=latest", root.trim_end_matches('/')),
        };
        let page = get_json(&url, &[]).await?;
        let servers = page.get("servers").and_then(|s| s.as_array()).cloned().unwrap_or_default();
        if servers.is_empty() {
            break;
        }
        for row in &servers {
            let s = row.get("server").unwrap_or(row);
            let Some(name) = s.get("name").and_then(|n| n.as_str()) else { continue };
            let mut e = base(slug(name), name.to_string(), "official");
            e.title = s.get("title").and_then(|t| t.as_str()).unwrap_or("").to_string();
            e.description =
                clip(s.get("description").and_then(|d| d.as_str()).unwrap_or("").trim(), 600);
            e.homepage = s.get("websiteUrl").and_then(|w| w.as_str()).unwrap_or("").to_string();
            e.repository = s
                .get("repository")
                .and_then(|r| r.get("url"))
                .and_then(|u| u.as_str())
                .unwrap_or("")
                .to_string();
            if let Some(remotes) = s.get("remotes").and_then(|r| r.as_array()) {
                // Prefer streamable-http; an SSE-only server still gets probed.
                let pick = remotes
                    .iter()
                    .find(|r| {
                        r.get("type").and_then(|t| t.as_str()).unwrap_or("").contains("streamable")
                    })
                    .or_else(|| remotes.first());
                if let Some(r) = pick {
                    e.url = r.get("url").and_then(|u| u.as_str()).unwrap_or("").to_string();
                    e.transport =
                        r.get("type").and_then(|t| t.as_str()).unwrap_or("").to_string();
                }
            }
            if let Some(pkgs) = s.get("packages").and_then(|p| p.as_array()) {
                for p in pkgs.iter().take(6) {
                    let reg = p
                        .get("registryType")
                        .or_else(|| p.get("registry_name"))
                        .and_then(|r| r.as_str())
                        .unwrap_or("pkg");
                    let ident = p
                        .get("identifier")
                        .or_else(|| p.get("name"))
                        .and_then(|i| i.as_str())
                        .unwrap_or("");
                    if !ident.is_empty() {
                        e.packages.push(format!("{reg}:{ident}"));
                    }
                }
                if e.url.is_empty() {
                    e.transport = "stdio".into();
                }
            }
            out.push(e);
        }
        cursor = page
            .get("metadata")
            .and_then(|m| m.get("nextCursor"))
            .and_then(|c| c.as_str())
            .map(String::from);
        if cursor.is_none() {
            break;
        }
    }
    let total = out.len() as u64;
    Ok((out, total))
}

// ── registry.smithery.ai — the biggest third-party directory ──────────

/// Keyless listing stops at five pages — 500 of the ~11k Smithery holds — but
/// a *query* gets its own five pages, and different queries return different
/// servers. So the plain listing runs every crawl and a rotating slice of
/// queries digs past the ceiling a little further each time.
const SM_QUERIES: [&str; 34] = [
    "is:deployed", "is:verified", "api", "data", "search", "github", "database", "cloud",
    "agent", "file", "web", "browser", "sql", "chat", "image", "code",
    "a", "b", "c", "d", "e", "f", "g", "h", "i", "j", "k", "l", "m", "n", "o", "p", "s", "t",
];
static SM_ROTATION: AtomicU64 = AtomicU64::new(0);

async fn smithery_page(
    query: &str,
    page: u32,
    headers: &[(&str, String)],
) -> Result<(Vec<Entry>, u64, u32), String> {
    let q = if query.is_empty() { String::new() } else { format!("&q={}", urlencode(query)) };
    let url = format!("https://registry.smithery.ai/servers?pageSize=100&page={page}{q}");
    let body = get_json(&url, headers).await?;
    let servers = body.get("servers").and_then(|s| s.as_array()).cloned().unwrap_or_default();
    let mut out = Vec::new();
    for s in &servers {
        let Some(qualified) = s.get("qualifiedName").and_then(|q| q.as_str()) else { continue };
        let mut e = base(slug(qualified), qualified.to_string(), "smithery");
        e.title = s.get("displayName").and_then(|d| d.as_str()).unwrap_or("").to_string();
        e.description = clip(s.get("description").and_then(|d| d.as_str()).unwrap_or("").trim(), 600);
        e.homepage = s.get("homepage").and_then(|h| h.as_str()).unwrap_or("").to_string();
        let remote = s.get("remote").and_then(|r| r.as_bool()).unwrap_or(false)
            || s.get("isDeployed").and_then(|r| r.as_bool()).unwrap_or(false);
        if remote {
            // Smithery hosts every deployed server at one shape of URL.
            e.url = format!("https://server.smithery.ai/{qualified}/mcp");
            e.transport = "streamable-http".into();
            e.auth_hint = "smithery api key (?api_key=…) for most servers".into();
        }
        out.push(e);
    }
    let pag = body.get("pagination");
    let total = pag.and_then(|p| p.get("totalCount")).and_then(|t| t.as_u64()).unwrap_or(0);
    let pages = pag.and_then(|p| p.get("totalPages")).and_then(|t| t.as_u64()).unwrap_or(0) as u32;
    Ok((out, total, pages))
}

pub async fn smithery() -> Result<(Vec<Entry>, u64), String> {
    let key = env("SMITHERY_API_KEY").or_else(|| env("MCPSCAN_SMITHERY_KEY"));
    let headers: Vec<(&str, String)> = match &key {
        Some(k) => vec![("Authorization", format!("Bearer {k}"))],
        None => vec![],
    };
    let cap = max_pages(400);
    let mut out = Vec::new();
    let mut catalogue_total = 0u64;

    // The plain listing, then a slice of queries that moves every crawl.
    let per_crawl = env("MCPSCAN_SMITHERY_QUERIES").and_then(|v| v.parse().ok()).unwrap_or(12u64);
    let start = SM_ROTATION.fetch_add(per_crawl, Ordering::Relaxed);
    let mut queries: Vec<String> = vec![String::new()];
    for i in 0..per_crawl {
        queries.push(SM_QUERIES[((start + i) as usize) % SM_QUERIES.len()].to_string());
    }

    for query in queries {
        for page in 1..=cap {
            let (rows, total, pages) = smithery_page(&query, page, &headers).await?;
            if query.is_empty() {
                catalogue_total = catalogue_total.max(total);
            }
            let n = rows.len();
            out.extend(rows);
            if n < 100 || page >= pages.max(1) {
                break;
            }
        }
    }
    Ok((out, catalogue_total))
}

// ── github ────────────────────────────────────────────────────────────

fn gh_headers() -> Vec<(&'static str, String)> {
    let mut h = vec![("Accept", "application/vnd.github+json".to_string())];
    if let Some(t) = env("MCPSCAN_GITHUB_TOKEN").or_else(|| env("GITHUB_TOKEN")) {
        h.push(("Authorization", format!("Bearer {t}")));
    }
    h
}

/// docker/mcp-registry — one directory per containerised server. The contents
/// API ignores `per_page` and returns the whole listing, so this is one call.
pub async fn docker() -> Result<(Vec<Entry>, u64), String> {
    let mut out = Vec::new();
    {
        let url = "https://api.github.com/repos/docker/mcp-registry/contents/servers";
        let body = get_json(url, &gh_headers()).await?;
        let items = body.as_array().cloned().unwrap_or_default();
        for it in &items {
            let Some(name) = it.get("name").and_then(|n| n.as_str()) else { continue };
            if it.get("type").and_then(|t| t.as_str()) != Some("dir") {
                continue;
            }
            let mut e = base(slug(&format!("docker-{name}")), name.to_string(), "docker");
            e.title = name.to_string();
            e.description = "Containerised MCP server from Docker's MCP registry.".to_string();
            e.repository =
                format!("https://github.com/docker/mcp-registry/tree/main/servers/{name}");
            e.packages.push(format!("docker:mcp/{name}"));
            e.transport = "stdio".into();
            out.push(e);
        }
    }
    let total = out.len() as u64;
    Ok((out, total))
}

/// Rotating GitHub code search. Each crawl takes a different slice, so the
/// long tail of repos that no directory lists gets covered over time.
static GH_ROTATION: AtomicU64 = AtomicU64::new(0);

pub async fn github() -> Result<(Vec<Entry>, u64), String> {
    const QUERIES: [&str; 5] = [
        "topic:mcp-server",
        "topic:model-context-protocol",
        "mcp-server in:name",
        "mcp server in:description language:python",
        "mcp server in:description language:typescript",
    ];
    let pages_per_run = env("MCPSCAN_GITHUB_PAGES").and_then(|v| v.parse().ok()).unwrap_or(3u64);
    let start = GH_ROTATION.fetch_add(pages_per_run, Ordering::Relaxed);
    let mut out = Vec::new();
    let mut err: Option<String> = None;
    for i in 0..pages_per_run {
        let n = start + i;
        // 10 pages of 100 is GitHub's hard ceiling per query; walk queries too.
        let q = QUERIES[(n / 10) as usize % QUERIES.len()];
        let page = (n % 10) + 1;
        let url = format!(
            "https://api.github.com/search/repositories?q={}&sort=updated&per_page=100&page={}",
            urlencode(q),
            page
        );
        match get_json(&url, &gh_headers()).await {
            Ok(body) => {
                let items = body.get("items").and_then(|i| i.as_array()).cloned().unwrap_or_default();
                for it in &items {
                    let Some(full) = it.get("full_name").and_then(|f| f.as_str()) else { continue };
                    let mut e = base(slug(full), full.to_string(), "github");
                    e.title = it.get("name").and_then(|n| n.as_str()).unwrap_or("").to_string();
                    e.description =
                        clip(it.get("description").and_then(|d| d.as_str()).unwrap_or("").trim(), 400);
                    e.repository = it.get("html_url").and_then(|h| h.as_str()).unwrap_or("").to_string();
                    e.homepage = it
                        .get("homepage")
                        .and_then(|h| h.as_str())
                        .unwrap_or("")
                        .trim()
                        .to_string();
                    e.transport = "unknown".into();
                    out.push(e);
                }
            }
            // Rate limits are the normal case here, not a crawl failure.
            Err(e) => {
                err = Some(e);
                break;
            }
        }
        tokio::time::sleep(std::time::Duration::from_secs(7)).await; // search: 10/min anon
    }
    match (out.is_empty(), err) {
        (true, Some(e)) => Err(e),
        // GitHub's search ceiling is 1000 results per query; the rotation is
        // how the rest of the tail gets reached, crawl after crawl.
        _ => Ok((out, 0)),
    }
}

// ── key-gated directories ─────────────────────────────────────────────

/// PulseMCP's v0.1 API. The old keyless v0beta is being sunset (it now fails a
/// share of requests on purpose), so this asks for a key and says so when it
/// hasn't got one.
pub async fn pulsemcp() -> Result<(Vec<Entry>, u64), String> {
    let Some(key) = env("PULSEMCP_API_KEY").or_else(|| env("MCPSCAN_PULSEMCP_KEY")) else {
        return Err("needs:PULSEMCP_API_KEY".into());
    };
    let headers = vec![("X-API-Key", key)];
    let mut out = Vec::new();
    let mut offset = 0u64;
    for _ in 0..max_pages(60) {
        let url =
            format!("https://api.pulsemcp.com/v0.1/servers?count_per_page=100&offset={offset}");
        let body = get_json(&url, &headers).await?;
        let servers = body.get("servers").and_then(|s| s.as_array()).cloned().unwrap_or_default();
        if servers.is_empty() {
            break;
        }
        for s in &servers {
            let Some(name) = s.get("name").and_then(|n| n.as_str()) else { continue };
            let mut e = base(slug(name), name.to_string(), "pulsemcp");
            e.title = name.to_string();
            e.description =
                clip(s.get("short_description").and_then(|d| d.as_str()).unwrap_or("").trim(), 600);
            e.homepage = s.get("external_url").and_then(|u| u.as_str()).unwrap_or("").to_string();
            e.repository = s.get("source_code_url").and_then(|u| u.as_str()).unwrap_or("").to_string();
            if let Some(r) = s.get("remotes").and_then(|r| r.as_array()).and_then(|a| a.first()) {
                e.url = r.get("url_direct").and_then(|u| u.as_str()).unwrap_or("").to_string();
                e.transport = r.get("transport").and_then(|t| t.as_str()).unwrap_or("").to_string();
                e.auth_hint = r
                    .get("authentication_method")
                    .and_then(|a| a.as_str())
                    .unwrap_or("")
                    .to_string();
            }
            if let Some(p) = s.get("package_name").and_then(|p| p.as_str()) {
                let reg = s.get("package_registry").and_then(|r| r.as_str()).unwrap_or("pkg");
                e.packages.push(format!("{reg}:{p}"));
            }
            out.push(e);
        }
        offset += servers.len() as u64;
    }
    let total = out.len() as u64;
    Ok((out, total))
}

/// Glama's directory. Also key-gated as of 2026.
pub async fn glama() -> Result<(Vec<Entry>, u64), String> {
    let Some(key) = env("GLAMA_API_KEY").or_else(|| env("MCPSCAN_GLAMA_KEY")) else {
        return Err("needs:GLAMA_API_KEY".into());
    };
    let headers = vec![("Authorization", format!("Bearer {key}"))];
    let mut out = Vec::new();
    let mut after: Option<String> = None;
    for _ in 0..max_pages(60) {
        let url = match &after {
            Some(a) => format!("https://glama.ai/api/mcp/v1/servers?first=100&after={}", urlencode(a)),
            None => "https://glama.ai/api/mcp/v1/servers?first=100".to_string(),
        };
        let body = get_json(&url, &headers).await?;
        let servers = body.get("servers").and_then(|s| s.as_array()).cloned().unwrap_or_default();
        if servers.is_empty() {
            break;
        }
        for s in &servers {
            let Some(name) = s.get("name").and_then(|n| n.as_str()) else { continue };
            let id = s.get("id").and_then(|i| i.as_str()).unwrap_or(name);
            let mut e = base(slug(id), name.to_string(), "glama");
            e.description =
                clip(s.get("description").and_then(|d| d.as_str()).unwrap_or("").trim(), 600);
            e.repository = s
                .get("repository")
                .and_then(|r| r.get("url"))
                .and_then(|u| u.as_str())
                .unwrap_or("")
                .to_string();
            e.url = s
                .get("url")
                .and_then(|u| u.as_str())
                .filter(|u| u.contains("/mcp") || u.contains("/sse"))
                .unwrap_or("")
                .to_string();
            out.push(e);
        }
        let has_next = body
            .get("pageInfo")
            .and_then(|p| p.get("hasNextPage"))
            .and_then(|h| h.as_bool())
            .unwrap_or(false);
        after = body
            .get("pageInfo")
            .and_then(|p| p.get("endCursor"))
            .and_then(|c| c.as_str())
            .map(String::from);
        if !has_next || after.is_none() {
            break;
        }
    }
    let total = out.len() as u64;
    Ok((out, total))
}

pub async fn run(source: &str) -> Result<(Vec<Entry>, u64), String> {
    match source {
        "official" => official().await,
        "smithery" => smithery().await,
        "docker" => docker().await,
        "github" => github().await,
        "pulsemcp" => pulsemcp().await,
        "glama" => glama().await,
        other => Err(format!("unknown source `{other}`")),
    }
}

fn urlencode(s: &str) -> String {
    s.bytes()
        .map(|b| match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                (b as char).to_string()
            }
            b' ' => "+".to_string(),
            _ => format!("%{b:02X}"),
        })
        .collect()
}
