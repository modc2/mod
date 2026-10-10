//! The web, as two tools.
//!
//! An MCP hub that can reach every server on the box and none of the internet
//! is a strange thing, so the hub carries its own `web_search` / `web_fetch`
//! rather than waiting for someone to register a search server.
//!
//! Search is a provider chain, tried in order and stopping at the first that
//! returns hits:
//!   brave · tavily · exa · serper   — only when a key is configured
//!   keenable                        — a public MCP search server, no key
//!   duckduckgo instant answer       — last resort, definitions not results
//!
//! Keys come from the environment (`BRAVE_API_KEY`, …) or from
//! ~/.mod/mcp/web.json (`{"brave": "…"}`), which is off-tree like every other
//! secret here. Nothing is required: with an empty box, keenable answers.

use crate::store::{self, ServerEntry};
use serde::Serialize;
use serde_json::{json, Value};
use std::collections::HashMap;
use std::time::Duration;

/// A public, keyless MCP server whose whole job is web search. Also the reason
/// `web_search` works on a fresh install.
pub const KEENABLE_URL: &str = "https://api.keenable.ai/mcp";

#[derive(Clone, Debug, Default, Serialize)]
pub struct Hit {
    pub title: String,
    pub url: String,
    #[serde(skip_serializing_if = "String::is_empty")]
    pub snippet: String,
    #[serde(skip_serializing_if = "String::is_empty")]
    pub published: String,
}

#[derive(Debug, Serialize)]
pub struct SearchResult {
    pub query: String,
    /// Which provider actually answered — empty when none did.
    pub provider: String,
    pub results: Vec<Hit>,
    /// Providers that were tried and why they didn't answer — the difference
    /// between "no results" and "your key is wrong" is worth showing.
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub tried: Vec<String>,
    /// Set when nobody answered. A search that finds nothing is still a
    /// search that ran, so this rides along with `tried` in a 200 rather
    /// than becoming a 5xx whose body a proxy is free to throw away.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
}

#[derive(Debug, Serialize)]
pub struct Page {
    pub url: String,
    pub title: String,
    pub text: String,
    pub chars: usize,
    /// "direct" or "keenable" — how the page was actually read.
    pub via: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub truncated: Option<bool>,
}

fn client(secs: u64) -> reqwest::Client {
    reqwest::Client::builder()
        .timeout(Duration::from_secs(secs))
        .user_agent(
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) \
             Chrome/124.0 Safari/537.36 mcp-hub",
        )
        .build()
        .expect("reqwest client")
}

/// A provider key from the environment, else from ~/.mod/mcp/web.json.
pub fn key(provider: &str) -> Option<String> {
    let upper = provider.to_uppercase();
    for name in [format!("{upper}_API_KEY"), format!("MCP_{upper}_KEY")] {
        if let Ok(v) = std::env::var(&name) {
            let v = v.trim().to_string();
            if !v.is_empty() {
                return Some(v);
            }
        }
    }
    let file: HashMap<String, String> = std::fs::read_to_string(store::hub_dir().join("web.json"))
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or_default();
    file.get(provider).map(|v| v.trim().to_string()).filter(|v| !v.is_empty())
}

/// Which providers could run right now, in the order they'd be tried.
pub fn providers() -> Vec<Value> {
    let mut out = Vec::new();
    for p in ["brave", "tavily", "exa", "serper"] {
        out.push(json!({ "name": p, "ready": key(p).is_some(), "needs_key": true }));
    }
    out.push(json!({ "name": "keenable", "ready": true, "needs_key": false,
                     "note": "public MCP search server — the keyless default" }));
    out.push(json!({ "name": "duckduckgo", "ready": true, "needs_key": false,
                     "note": "instant answers only, not a result list" }));
    out
}

fn clip(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}

// ── the providers ────────────────────────────────────────────────────

async fn brave(q: &str, count: usize, k: &str) -> Result<Vec<Hit>, String> {
    let r = client(20)
        .get("https://api.search.brave.com/res/v1/web/search")
        .query(&[("q", q), ("count", &count.to_string())])
        .header("Accept", "application/json")
        .header("X-Subscription-Token", k)
        .send()
        .await
        .map_err(|e| e.to_string())?;
    let v = json_body(r).await?;
    Ok(v["web"]["results"]
        .as_array()
        .map(|a| {
            a.iter()
                .map(|r| Hit {
                    title: r["title"].as_str().unwrap_or_default().into(),
                    url: r["url"].as_str().unwrap_or_default().into(),
                    snippet: clip(r["description"].as_str().unwrap_or_default(), 400),
                    published: r["age"].as_str().unwrap_or_default().into(),
                })
                .collect()
        })
        .unwrap_or_default())
}

async fn tavily(q: &str, count: usize, k: &str) -> Result<Vec<Hit>, String> {
    let r = client(25)
        .post("https://api.tavily.com/search")
        .json(&json!({ "api_key": k, "query": q, "max_results": count }))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    let v = json_body(r).await?;
    Ok(v["results"]
        .as_array()
        .map(|a| {
            a.iter()
                .map(|r| Hit {
                    title: r["title"].as_str().unwrap_or_default().into(),
                    url: r["url"].as_str().unwrap_or_default().into(),
                    snippet: clip(r["content"].as_str().unwrap_or_default(), 400),
                    published: r["published_date"].as_str().unwrap_or_default().into(),
                })
                .collect()
        })
        .unwrap_or_default())
}

async fn exa(q: &str, count: usize, k: &str) -> Result<Vec<Hit>, String> {
    let r = client(25)
        .post("https://api.exa.ai/search")
        .header("x-api-key", k)
        .json(&json!({ "query": q, "numResults": count, "contents": { "text": { "maxCharacters": 400 } } }))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    let v = json_body(r).await?;
    Ok(v["results"]
        .as_array()
        .map(|a| {
            a.iter()
                .map(|r| Hit {
                    title: r["title"].as_str().unwrap_or_default().into(),
                    url: r["url"].as_str().unwrap_or_default().into(),
                    snippet: clip(r["text"].as_str().unwrap_or_default(), 400),
                    published: r["publishedDate"].as_str().unwrap_or_default().into(),
                })
                .collect()
        })
        .unwrap_or_default())
}

async fn serper(q: &str, count: usize, k: &str) -> Result<Vec<Hit>, String> {
    let r = client(20)
        .post("https://google.serper.dev/search")
        .header("X-API-KEY", k)
        .json(&json!({ "q": q, "num": count }))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    let v = json_body(r).await?;
    Ok(v["organic"]
        .as_array()
        .map(|a| {
            a.iter()
                .map(|r| Hit {
                    title: r["title"].as_str().unwrap_or_default().into(),
                    url: r["link"].as_str().unwrap_or_default().into(),
                    snippet: clip(r["snippet"].as_str().unwrap_or_default(), 400),
                    published: r["date"].as_str().unwrap_or_default().into(),
                })
                .collect()
        })
        .unwrap_or_default())
}

/// The keyless path: call a public MCP search server the same way the hub
/// calls any other upstream, then read its prose back into hits.
fn keenable_entry() -> ServerEntry {
    ServerEntry {
        id: "keenable-web".into(),
        name: "Keenable Web Search".into(),
        url: std::env::var("MCP_WEB_SEARCH_URL").unwrap_or_else(|_| KEENABLE_URL.into()),
        headers: HashMap::new(),
        source: "web".into(),
        note: String::new(),
        added_at: 0,
        ..Default::default()
    }
}

async fn keenable(q: &str, count: usize) -> Result<Vec<Hit>, String> {
    let res = crate::upstream::rpc(
        &keenable_entry(),
        "tools/call",
        json!({ "name": "search_web_pages", "arguments": { "query": q } }),
    )
    .await?;
    let text = res["content"]
        .as_array()
        .map(|a| {
            a.iter()
                .filter_map(|c| c.get("text").and_then(|t| t.as_str()))
                .collect::<Vec<_>>()
                .join("\n")
        })
        .unwrap_or_default();
    if text.trim().is_empty() {
        return Err("search server returned nothing".into());
    }
    Ok(parse_keenable(&text).into_iter().take(count).collect())
}

/// Keenable answers in labelled blocks — `Title:`, `URL:`, `Published:`,
/// `Snippets:` then free text, one block per result.
fn parse_keenable(text: &str) -> Vec<Hit> {
    let mut hits: Vec<Hit> = Vec::new();
    let mut cur = Hit::default();
    let mut in_snippet = false;
    let mut push = |cur: &mut Hit| {
        if !cur.url.is_empty() {
            cur.snippet = clip(cur.snippet.trim(), 600);
            hits.push(std::mem::take(cur));
        }
    };
    for line in text.lines() {
        let t = line.trim();
        if let Some(v) = t.strip_prefix("Title:") {
            push(&mut cur);
            in_snippet = false;
            cur.title = v.trim().to_string();
        } else if let Some(v) = t.strip_prefix("URL:") {
            in_snippet = false;
            cur.url = v.trim().to_string();
        } else if let Some(v) = t.strip_prefix("Published:") {
            in_snippet = false;
            cur.published = v.trim().to_string();
        } else if t.starts_with("Snippets:") {
            in_snippet = true;
        } else if in_snippet && !t.is_empty() && t != "[...]" {
            if !cur.snippet.is_empty() {
                cur.snippet.push(' ');
            }
            cur.snippet.push_str(t);
        }
    }
    push(&mut cur);
    hits
}

/// DuckDuckGo's instant-answer API. It is not a result list — it answers
/// "what is X" and little else — but it needs no key and never blocks us.
async fn ddg(q: &str) -> Result<Vec<Hit>, String> {
    let r = client(15)
        .get("https://api.duckduckgo.com/")
        .query(&[("q", q), ("format", "json"), ("no_html", "1"), ("no_redirect", "1")])
        .send()
        .await
        .map_err(|e| e.to_string())?;
    let v = json_body(r).await?;
    let mut hits = Vec::new();
    let abstract_text = v["AbstractText"].as_str().unwrap_or_default();
    if !abstract_text.is_empty() {
        hits.push(Hit {
            title: v["Heading"].as_str().unwrap_or(q).into(),
            url: v["AbstractURL"].as_str().unwrap_or_default().into(),
            snippet: clip(abstract_text, 600),
            published: String::new(),
        });
    }
    if let Some(topics) = v["RelatedTopics"].as_array() {
        for t in topics.iter().take(8) {
            let (Some(url), Some(text)) = (t["FirstURL"].as_str(), t["Text"].as_str()) else {
                continue;
            };
            hits.push(Hit {
                title: text.split(" - ").next().unwrap_or(text).to_string(),
                url: url.to_string(),
                snippet: clip(text, 300),
                published: String::new(),
            });
        }
    }
    if hits.is_empty() {
        return Err("no instant answer for that query".into());
    }
    Ok(hits)
}

async fn json_body(r: reqwest::Response) -> Result<Value, String> {
    let status = r.status();
    let text = r.text().await.map_err(|e| e.to_string())?;
    if !status.is_success() {
        return Err(format!("HTTP {status}: {}", clip(text.trim(), 200)));
    }
    serde_json::from_str(&text).map_err(|e| format!("bad JSON: {e}"))
}

// ── the chain ────────────────────────────────────────────────────────

/// Search the web. `provider` pins one (and reports its failure rather than
/// silently falling through); otherwise the chain runs in order.
///
/// `Err` means the request itself was unusable — no query, or a pinned
/// provider with no key. A chain that ran and came up empty is `Ok` with
/// `results: []`, the per-provider `tried` trace, and `error` set: the caller
/// wants to read that trace, and only a 200 reliably delivers it.
pub async fn search(q: &str, count: usize, provider: Option<&str>) -> Result<SearchResult, String> {
    let q = q.trim();
    if q.is_empty() {
        return Err("`q` is required".into());
    }
    let count = count.clamp(1, 25);
    let mut tried: Vec<String> = Vec::new();

    for name in ["brave", "tavily", "exa", "serper", "keenable", "duckduckgo"] {
        if let Some(pin) = provider {
            if pin != name {
                continue;
            }
        }
        let keyed = matches!(name, "brave" | "tavily" | "exa" | "serper");
        let k = if keyed { key(name) } else { Some(String::new()) };
        let Some(k) = k else {
            if provider.is_some() {
                return Err(format!("no API key configured for {name}"));
            }
            continue;
        };
        let attempt = match name {
            "brave" => brave(q, count, &k).await,
            "tavily" => tavily(q, count, &k).await,
            "exa" => exa(q, count, &k).await,
            "serper" => serper(q, count, &k).await,
            "keenable" => keenable(q, count).await,
            _ => ddg(q).await,
        };
        match attempt {
            Ok(results) if !results.is_empty() => {
                return Ok(SearchResult {
                    query: q.to_string(),
                    provider: name.to_string(),
                    results,
                    tried,
                    error: None,
                })
            }
            Ok(_) => tried.push(format!("{name}: no results")),
            Err(e) => tried.push(format!("{name}: {}", clip(&e, 160))),
        }
        if provider.is_some() {
            break;
        }
    }
    let summary = match (provider, tried.first()) {
        // A pinned provider that came up empty: its own reason, unprefixed
        // noise removed — the UI already shows which provider was pinned.
        (Some(pin), Some(only)) => {
            let why = only.strip_prefix(&format!("{pin}: ")).unwrap_or(only);
            format!("{pin} found nothing — {why}")
        }
        (_, None) => "no search provider is configured".to_string(),
        _ => format!("every provider came up empty — {}", tried.join("; ")),
    };
    Ok(SearchResult {
        query: q.to_string(),
        provider: String::new(),
        results: Vec::new(),
        tried,
        error: Some(summary),
    })
}

// ── reading a page ───────────────────────────────────────────────────

fn entity_decode(s: &str) -> String {
    let mut out = s
        .replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", "\"")
        .replace("&#39;", "'")
        .replace("&apos;", "'")
        .replace("&mdash;", "—")
        .replace("&ndash;", "–")
        .replace("&hellip;", "…");
    // numeric entities, decimal only — the common case in scraped HTML
    while let Some(start) = out.find("&#") {
        let Some(end) = out[start..].find(';').map(|i| start + i) else { break };
        let body = &out[start + 2..end];
        let Ok(code) = body.parse::<u32>() else { break };
        let ch = char::from_u32(code).map(String::from).unwrap_or_default();
        out.replace_range(start..=end, &ch);
    }
    out
}

/// Drop everything a reader wouldn't see, then the tags themselves.
fn html_to_text(html: &str) -> String {
    let mut cleaned = String::with_capacity(html.len());
    let mut rest = html;
    // strip script/style/noscript bodies wholesale
    loop {
        let lower = rest.to_lowercase();
        let next = ["<script", "<style", "<noscript", "<svg"]
            .iter()
            .filter_map(|t| lower.find(t).map(|i| (i, *t)))
            .min_by_key(|(i, _)| *i);
        let Some((start, tag)) = next else {
            cleaned.push_str(rest);
            break;
        };
        cleaned.push_str(&rest[..start]);
        let close = format!("</{}", tag.trim_start_matches('<'));
        match lower[start..].find(&close).map(|i| start + i) {
            Some(end) => rest = &rest[end..],
            None => break, // unclosed <script> — the rest of the document is it
        }
    }
    let mut text = String::with_capacity(cleaned.len() / 2);
    let mut in_tag = false;
    for c in cleaned.chars() {
        match c {
            '<' => {
                in_tag = true;
                text.push(' ');
            }
            '>' => in_tag = false,
            _ if !in_tag => text.push(c),
            _ => {}
        }
    }
    // collapse the whitespace the tags left behind, keeping paragraph breaks
    let mut out = String::with_capacity(text.len());
    let mut blank = 0;
    for line in entity_decode(&text).lines() {
        let l = line.split_whitespace().collect::<Vec<_>>().join(" ");
        if l.is_empty() {
            blank += 1;
            if blank < 2 && !out.is_empty() {
                out.push('\n');
            }
        } else {
            blank = 0;
            out.push_str(&l);
            out.push('\n');
        }
    }
    out.trim().to_string()
}

fn title_of(html: &str) -> String {
    let lower = html.to_lowercase();
    let Some(start) = lower.find("<title") else { return String::new() };
    let Some(open) = lower[start..].find('>').map(|i| start + i + 1) else { return String::new() };
    let Some(end) = lower[open..].find("</title").map(|i| open + i) else { return String::new() };
    entity_decode(html[open..end].trim()).split_whitespace().collect::<Vec<_>>().join(" ")
}

/// Hosts `web_fetch` refuses. The hub answers on a public route, so without
/// this the tool would be a window onto every service bound to loopback on
/// this box — the whole fleet, most of it unauthenticated because it expects
/// to be unreachable. Set MCP_FETCH_ALLOW_LOCAL=1 to lift it.
fn is_private_host(url: &str) -> bool {
    if std::env::var("MCP_FETCH_ALLOW_LOCAL").ok().as_deref() == Some("1") {
        return false;
    }
    let Some(rest) = url.split("//").nth(1) else { return true };
    let hostport = rest.split(['/', '?', '#']).next().unwrap_or("");
    let host = hostport.rsplit('@').next().unwrap_or(hostport);
    let host = host.split(':').next().unwrap_or(host).trim_matches(['[', ']']).to_lowercase();
    if host.is_empty() || host == "localhost" || host.ends_with(".localhost") || host.ends_with(".local")
    {
        return true;
    }
    if host == "::1" || host.starts_with("fc") || host.starts_with("fd") || host.starts_with("fe80") {
        return true;
    }
    let octets: Vec<u8> = host.split('.').filter_map(|o| o.parse::<u8>().ok()).collect();
    if octets.len() == 4 && host.split('.').count() == 4 {
        return match (octets[0], octets[1]) {
            (127, _) | (10, _) | (0, _) => true,
            (192, 168) => true,
            (169, 254) => true,
            (172, b) if (16..=31).contains(&b) => true,
            _ => false,
        };
    }
    // A bare name with no dot is an intranet host, not a website.
    !host.contains('.')
}

/// Read one page as text. Tries the plain HTTP fetch first and falls back to
/// the search server's reader when a site refuses robots (403/challenge).
pub async fn fetch(url: &str, max_chars: usize) -> Result<Page, String> {
    let url = url.trim();
    if !url.starts_with("http://") && !url.starts_with("https://") {
        return Err("`url` must be http(s)".into());
    }
    if is_private_host(url) {
        return Err(
            "that host is on this machine's own network — web_fetch only reads the public web".into(),
        );
    }
    let max = max_chars.clamp(200, 200_000);
    let direct = client(30).get(url).send().await;
    let why;
    match direct {
        Ok(r) if r.status().is_success() => {
            let ctype =
                r.headers().get("content-type").and_then(|v| v.to_str().ok()).unwrap_or("").to_string();
            let body = r.text().await.map_err(|e| e.to_string())?;
            let is_html = ctype.contains("html") || body.trim_start().starts_with('<');
            let text = if is_html { html_to_text(&body) } else { body.clone() };
            if !text.trim().is_empty() {
                let full = text.chars().count();
                return Ok(Page {
                    url: url.to_string(),
                    title: if is_html { title_of(&body) } else { String::new() },
                    text: clip(&text, max),
                    chars: full,
                    via: "direct".into(),
                    truncated: Some(full > max),
                });
            }
            why = "the page carried no readable text".into();
        }
        Ok(r) => why = format!("HTTP {}", r.status()),
        Err(e) => why = clip(&e.to_string(), 160),
    }

    // Second chance: ask the public search server to read it for us.
    let res = crate::upstream::rpc(
        &keenable_entry(),
        "tools/call",
        json!({ "name": "fetch_page_content", "arguments": { "url": url } }),
    )
    .await
    .map_err(|e| format!("direct fetch failed ({why}), and the reader fallback failed too: {e}"))?;
    let text = res["content"]
        .as_array()
        .map(|a| {
            a.iter()
                .filter_map(|c| c.get("text").and_then(|t| t.as_str()))
                .collect::<Vec<_>>()
                .join("\n")
        })
        .unwrap_or_default();
    if text.trim().is_empty() {
        return Err(format!("direct fetch failed ({why}), and the reader returned nothing"));
    }
    let full = text.chars().count();
    Ok(Page {
        url: url.to_string(),
        title: String::new(),
        text: clip(&text, max),
        chars: full,
        via: "keenable".into(),
        truncated: Some(full > max),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn keenable_blocks_become_hits() {
        let text = "Title: One\nURL: https://a.example/x\nPublished: 2026-01-02\nSnippets:\nfirst line\n[...]\nsecond line\nTitle: Two\nURL: https://b.example/y\nSnippets:\nonly\n";
        let hits = parse_keenable(text);
        assert_eq!(hits.len(), 2);
        assert_eq!(hits[0].url, "https://a.example/x");
        assert_eq!(hits[0].published, "2026-01-02");
        assert_eq!(hits[0].snippet, "first line second line");
        assert_eq!(hits[1].title, "Two");
    }

    #[test]
    fn scripts_and_tags_leave_only_prose() {
        let html = "<html><head><title>Hi &amp; bye</title><style>p{color:red}</style></head>\
                    <body><script>var x = '<b>no</b>';</script><p>Hello&nbsp;world</p></body></html>";
        assert_eq!(title_of(html), "Hi & bye");
        let text = html_to_text(html);
        assert!(text.contains("Hello world"), "{text}");
        assert!(!text.contains("var x"), "{text}");
        assert!(!text.contains("color:red"), "{text}");
    }

    #[test]
    fn numeric_entities_decode() {
        assert_eq!(entity_decode("caf&#233; &#8212; ok"), "café — ok");
    }

    #[test]
    fn the_fleet_is_not_the_web() {
        for u in [
            "http://localhost:50360/mcp",
            "http://127.0.0.1:8080/",
            "http://[::1]:3000/",
            "http://10.0.0.5/admin",
            "http://192.168.1.1/",
            "http://172.20.0.3/",
            "http://169.254.169.254/latest/meta-data/",
            "http://store/health",
        ] {
            assert!(is_private_host(u), "{u} should be refused");
        }
        for u in ["https://example.com/x", "https://api.keenable.ai/mcp", "http://8.8.8.8/"] {
            assert!(!is_private_host(u), "{u} should be allowed");
        }
    }
}
