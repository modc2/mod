//! One input box, many formats. Whatever you paste — a bare URL, an IPFS/store
//! CID, a client config blob, a `claude mcp add` one-liner, or the text a QR
//! code decoded to — lands here and comes back as a list of candidate servers
//! the hub could register.
//!
//! QR codes are decoded in the browser (a QR is just a text carrier); by the
//! time a payload reaches this module it is already one of the text forms.

use crate::store::clean_id;
use serde::Serialize;
use serde_json::Value;
use std::collections::HashMap;
use std::time::Duration;

#[derive(Clone, Debug, Default, Serialize)]
pub struct Candidate {
    pub id: String,
    pub name: String,
    pub url: String,
    #[serde(default)]
    pub headers: HashMap<String, String>,
    #[serde(default)]
    pub note: String,
    /// How this candidate was recognised — shown in the UI so the user can see
    /// the hub understood what they pasted.
    pub kind: String,
}

#[derive(Debug, Default, Serialize)]
pub struct Intake {
    /// url | cid | json | cli | qr | text
    pub kind: String,
    pub candidates: Vec<Candidate>,
    pub warnings: Vec<String>,
    /// When the input was a CID, what it resolved to (trimmed) — so a user can
    /// see what they actually fetched.
    #[serde(skip_serializing_if = "String::is_empty")]
    pub resolved: String,
    #[serde(skip_serializing_if = "String::is_empty")]
    pub source: String,
}

fn http_client() -> reqwest::Client {
    reqwest::Client::builder()
        .timeout(Duration::from_secs(20))
        .build()
        .unwrap_or_default()
}

/// Does this look like a content id (CIDv0 `Qm…` or CIDv1 `bafy…`/`b…`)?
pub fn looks_like_cid(s: &str) -> bool {
    let s = s.trim().trim_start_matches("ipfs://").trim_start_matches("/ipfs/");
    if s.len() < 32 || s.len() > 100 || s.contains(char::is_whitespace) {
        return false;
    }
    if s.starts_with("Qm") && s.len() == 46 {
        return s[2..].chars().all(|c| c.is_ascii_alphanumeric());
    }
    (s.starts_with("baf") || s.starts_with("bafy") || s.starts_with("bafk"))
        && s.chars().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit())
}

/// Fetch a CID's bytes as text: the fleet's own store first, then a public
/// IPFS gateway (both overridable by env).
async fn fetch_cid(cid: &str) -> Result<(String, String), String> {
    let cid = cid.trim().trim_start_matches("ipfs://").trim_start_matches("/ipfs/");
    let store = std::env::var("MCP_STORE_URL").unwrap_or_else(|_| "http://localhost:50152".into());
    let gateway = std::env::var("MCP_IPFS_GATEWAY").unwrap_or_else(|_| "https://ipfs.io/ipfs".into());
    let mut last = String::new();
    for base in [format!("{}/get/{}", store.trim_end_matches('/'), cid), format!("{}/{}", gateway.trim_end_matches('/'), cid)] {
        match http_client().get(&base).send().await {
            Ok(r) if r.status().is_success() => {
                let text = r.text().await.unwrap_or_default();
                if !text.trim().is_empty() {
                    return Ok((text, base));
                }
                last = format!("{base} returned an empty body");
            }
            Ok(r) => last = format!("{base} → HTTP {}", r.status()),
            Err(e) => last = format!("{base} unreachable: {e}"),
        }
    }
    Err(if last.is_empty() { format!("could not resolve {cid}") } else { last })
}

fn normalize_url(raw: &str) -> Option<String> {
    let u = raw.trim().trim_matches(|c| c == ',' || c == '"' || c == '\'' || c == '`');
    if u.starts_with("http://") || u.starts_with("https://") {
        return Some(u.trim_end_matches(['.', ')', ']']).to_string());
    }
    // host:port/path or host/path — assume http for loopback, https otherwise.
    let looks_hostish = u.contains('.') || u.starts_with("localhost");
    if looks_hostish && !u.contains(' ') && !u.starts_with('{') {
        let scheme = if u.starts_with("localhost") || u.starts_with("127.") { "http" } else { "https" };
        return Some(format!("{scheme}://{u}"));
    }
    None
}

fn host_of(url: &str) -> String {
    url.split("//")
        .nth(1)
        .unwrap_or(url)
        .split(['/', ':'])
        .next()
        .unwrap_or("server")
        .to_string()
}

fn candidate(url: String, id: Option<&str>, name: Option<&str>, headers: HashMap<String, String>, note: &str, kind: &str) -> Candidate {
    let fallback = host_of(&url);
    let id = clean_id(id.filter(|s| !s.trim().is_empty()).unwrap_or(&fallback));
    Candidate {
        name: name.filter(|s| !s.trim().is_empty()).unwrap_or(&id).to_string(),
        id,
        url,
        headers,
        note: note.chars().take(300).collect(),
        kind: kind.to_string(),
    }
}

fn headers_of(v: &Value) -> HashMap<String, String> {
    v.get("headers")
        .or_else(|| v.get("requestInit").and_then(|r| r.get("headers")))
        .and_then(|h| h.as_object())
        .map(|o| o.iter().filter_map(|(k, val)| val.as_str().map(|s| (k.clone(), s.to_string()))).collect())
        .unwrap_or_default()
}

fn url_of(v: &Value) -> Option<String> {
    for key in ["url", "endpoint", "serverUrl", "httpUrl", "uri", "mcp"] {
        if let Some(u) = v.get(key).and_then(|x| x.as_str()) {
            if let Some(u) = normalize_url(u) {
                return Some(u);
            }
        }
    }
    None
}

/// One entry of an `mcpServers`-style map, or a bare server object.
fn from_entry(key: Option<&str>, v: &Value, out: &mut Vec<Candidate>, warn: &mut Vec<String>) {
    if let Some(url) = url_of(v) {
        let name = v.get("name").and_then(|n| n.as_str()).or(key);
        let note = v.get("description").or_else(|| v.get("note")).and_then(|n| n.as_str()).unwrap_or("");
        out.push(candidate(url, key.or(name), name, headers_of(v), note, "json"));
        return;
    }
    if v.get("command").is_some() {
        let label = key.unwrap_or("entry");
        warn.push(format!(
            "`{label}` is a stdio server (command: {}) — the hub aggregates Streamable HTTP endpoints only, so it can't be registered.",
            v.get("command").and_then(|c| c.as_str()).unwrap_or("?")
        ));
    }
}

/// A mod-protocol config.json — the fleet's own descriptor format.
fn from_mod_config(v: &Value, out: &mut Vec<Candidate>) -> bool {
    let name = v.get("name").and_then(|n| n.as_str());
    let mcp_url = v
        .get("urls")
        .and_then(|u| u.get("mcp"))
        .and_then(|u| u.as_str())
        .map(String::from)
        .or_else(|| {
            // endpoints.mcp declares the route; the port tells us where it lives
            v.get("endpoints").and_then(|e| e.get("mcp"))?;
            let port = ["mcp_port", "gateway_port", "port"]
                .iter()
                .find_map(|k| v.get(*k).and_then(|p| p.as_u64()))?;
            Some(format!("http://localhost:{port}/mcp"))
        });
    let Some(url) = mcp_url.and_then(|u| normalize_url(&u)) else { return false };
    let note = v.get("description").and_then(|d| d.as_str()).unwrap_or("");
    out.push(candidate(url, name, v.get("title").and_then(|t| t.as_str()).or(name), HashMap::new(), note, "mod config"));
    true
}

fn from_json(v: &Value, out: &mut Vec<Candidate>, warn: &mut Vec<String>) {
    match v {
        Value::Array(items) => {
            for item in items {
                from_json(item, out, warn);
            }
        }
        Value::Object(map) => {
            let before = out.len();
            for key in ["mcpServers", "mcp_servers", "servers", "mcp"] {
                if let Some(Value::Object(entries)) = map.get(key) {
                    for (k, entry) in entries {
                        from_entry(Some(k), entry, out, warn);
                    }
                }
                if let Some(Value::Array(entries)) = map.get(key) {
                    for entry in entries {
                        from_entry(entry.get("id").or_else(|| entry.get("name")).and_then(|n| n.as_str()), entry, out, warn);
                    }
                }
            }
            if out.len() == before && from_mod_config(v, out) {
                return;
            }
            if out.len() == before {
                from_entry(map.get("id").or_else(|| map.get("name")).and_then(|n| n.as_str()), v, out, warn);
            }
        }
        _ => {}
    }
}

/// `claude mcp add hub --transport http https://host/mcp --header "K: V"`
fn from_cli(text: &str, out: &mut Vec<Candidate>) -> bool {
    if !text.contains("mcp add") {
        return false;
    }
    let toks: Vec<String> = shell_split(text);
    let mut url = None;
    let mut name: Option<String> = None;
    let mut headers = HashMap::new();
    let mut i = 0;
    while i < toks.len() {
        let t = &toks[i];
        if t == "add" {
            // the token after `add` is the server name unless it's a flag/url
            if let Some(next) = toks.get(i + 1) {
                if !next.starts_with('-') && normalize_url(next).map(|u| u.contains("://")) != Some(true) {
                    name = Some(next.clone());
                }
            }
        } else if t == "--header" || t == "-H" {
            if let Some(h) = toks.get(i + 1) {
                if let Some((k, v)) = h.split_once(':') {
                    headers.insert(k.trim().to_string(), v.trim().to_string());
                }
                i += 1;
            }
        } else if t.starts_with("http://") || t.starts_with("https://") {
            url = normalize_url(t);
        }
        i += 1;
    }
    match url {
        Some(u) => {
            out.push(candidate(u, name.as_deref(), name.as_deref(), headers, "", "claude cli"));
            true
        }
        None => false,
    }
}

/// Minimal shell-ish tokenizer: splits on whitespace, honours quotes.
fn shell_split(s: &str) -> Vec<String> {
    let mut out = Vec::new();
    let mut cur = String::new();
    let mut quote: Option<char> = None;
    for c in s.chars() {
        match quote {
            Some(q) if c == q => quote = None,
            Some(_) => cur.push(c),
            None if c == '"' || c == '\'' => quote = Some(c),
            None if c.is_whitespace() => {
                if !cur.is_empty() {
                    out.push(std::mem::take(&mut cur));
                }
            }
            None => cur.push(c),
        }
    }
    if !cur.is_empty() {
        out.push(cur);
    }
    out
}

fn dedupe(mut list: Vec<Candidate>) -> Vec<Candidate> {
    let mut seen = std::collections::HashSet::new();
    list.retain(|c| seen.insert(c.url.clone()));
    // ids must stay unique within one intake or the second would shadow the first
    let mut used = std::collections::HashSet::new();
    for c in list.iter_mut() {
        let base = c.id.clone();
        let mut n = 2;
        while !used.insert(c.id.clone()) {
            c.id = clean_id(&format!("{base}-{n}"));
            n += 1;
        }
    }
    list
}

/// Parse anything a human (or a QR code) can hand us into candidate servers.
/// `depth` guards CID→content→CID chains.
pub async fn parse(input: &str, depth: u8) -> Intake {
    let text = input.trim();
    let mut r = Intake { kind: "text".into(), ..Default::default() };
    if text.is_empty() {
        r.warnings.push("nothing to parse".into());
        return r;
    }

    // 1. A CID (or ipfs:// url) — fetch it and parse whatever comes back.
    if depth == 0 && looks_like_cid(text) {
        match fetch_cid(text).await {
            Ok((body, from)) => {
                let mut inner = Box::pin(parse(&body, depth + 1)).await;
                inner.kind = "cid".into();
                inner.resolved = body.chars().take(2000).collect();
                inner.source = from;
                if inner.candidates.is_empty() {
                    inner.warnings.push("the CID resolved but held no MCP endpoint".into());
                }
                return inner;
            }
            Err(e) => {
                r.kind = "cid".into();
                r.warnings.push(e);
                return r;
            }
        }
    }

    // 2. JSON — a client config, a server object, a list, or a mod config.json.
    let json_ish = text.starts_with('{') || text.starts_with('[');
    if json_ish {
        match serde_json::from_str::<Value>(text) {
            Ok(v) => {
                r.kind = "json".into();
                from_json(&v, &mut r.candidates, &mut r.warnings);
                if r.candidates.is_empty() && r.warnings.is_empty() {
                    r.warnings.push("valid JSON, but no `url` field anywhere in it".into());
                }
                r.candidates = dedupe(std::mem::take(&mut r.candidates));
                return r;
            }
            Err(e) => {
                r.kind = "json".into();
                r.warnings.push(format!("looks like JSON but won't parse: {e}"));
                return r;
            }
        }
    }

    // 3. A CLI one-liner.
    if from_cli(text, &mut r.candidates) {
        r.kind = "cli".into();
        r.candidates = dedupe(std::mem::take(&mut r.candidates));
        return r;
    }

    // 4. Plain text: every URL in it is a candidate (QR payloads land here too).
    let mut urls: Vec<Candidate> = Vec::new();
    for tok in text.split_whitespace() {
        // strip a leading label like `url=` or `mcp:`
        let tok = tok.rsplit_once('=').map(|(_, v)| v).unwrap_or(tok);
        if let Some(u) = normalize_url(tok) {
            if u.contains("://") && u.len() > 10 {
                urls.push(candidate(u, None, None, HashMap::new(), "", "url"));
            }
        }
    }
    if urls.is_empty() {
        r.warnings.push("no URL, CID or JSON config found in that input".into());
        return r;
    }
    r.kind = "url".into();
    r.candidates = dedupe(urls);
    r
}
