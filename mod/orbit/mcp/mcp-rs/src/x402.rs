//! x402 — MCP servers that sit behind pay-per-request.
//!
//! Two jobs, both delegated to the x402 module (`orbit/x402`, loopback):
//!
//!   scan   — its index lists every paid resource that exists; the ones that
//!            live under an MCP endpoint (an `mcp.*` host, a `/mcp` path
//!            segment, or a listing typed `mcp`) are folded into one row per
//!            server, with the paid tools and the cheapest price.
//!   relay  — a server flagged `pay: "x402"` that answers HTTP 402 is not an
//!            error: the request is replayed through `x402_call`, which pays
//!            within the node's own policy (house wallet, trust tier, daily
//!            budget) and returns the paid reply. When the node may not pay,
//!            the error says what it costs, so the caller can pay with its own
//!            wallet through x402_quote.
//!
//! The hub never holds a key. Whether anything is spent is x402's decision.

use crate::state::AppState;
use crate::store::{clean_id, now, ServerEntry};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::{BTreeMap, HashMap};
use std::sync::{Arc, Mutex};
use std::time::Duration;

pub fn base() -> String {
    std::env::var("X402_URL")
        .map(|u| u.trim_end_matches('/').to_string())
        .unwrap_or_else(|_| "http://127.0.0.1:51110".into())
}

fn client(secs: u64) -> reqwest::Client {
    reqwest::Client::builder()
        .timeout(Duration::from_secs(secs))
        .user_agent("mcp-hub")
        .build()
        .expect("reqwest client")
}

/// One x402 tool, loopback. Errors come back as the tool's own message.
async fn tool(name: &str, args: Value, secs: u64) -> Result<Value, String> {
    let body = json!({ "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": { "name": name, "arguments": args } });
    let r = client(secs)
        .post(format!("{}/mcp", base()))
        .json(&body)
        .send()
        .await
        .map_err(|e| format!("x402 module unreachable at {}: {e}", base()))?;
    let v: Value = r.json().await.map_err(|e| format!("x402 module sent bad JSON: {e}"))?;
    if let Some(e) = v.get("error") {
        return Err(format!("x402: {}", e["message"].as_str().unwrap_or("rpc error")));
    }
    let res = &v["result"];
    if res["isError"].as_bool() == Some(true) {
        return Err(format!("x402: {}", res["content"][0]["text"].as_str().unwrap_or("refused")));
    }
    Ok(res["structuredContent"].clone())
}

fn offer_line(o: &Value) -> String {
    let price = o["price_usd"].as_f64().map(|p| format!("${p}")).unwrap_or_else(|| "?".into());
    let net = o["network"].as_str().unwrap_or("?");
    format!("{price} on {net}")
}

/// Replay one JSON-RPC POST through x402_call. Ok = the paid reply as
/// (body, content-type), ready for the usual reply parser.
pub async fn relay(
    url: &str,
    body: &Value,
    headers: &HashMap<String, String>,
    session: Option<&str>,
    protocol: &str,
) -> Result<(String, String), String> {
    let mut h = serde_json::Map::new();
    h.insert("Content-Type".into(), json!("application/json"));
    h.insert("Accept".into(), json!("application/json, text/event-stream"));
    h.insert("MCP-Protocol-Version".into(), json!(protocol));
    if let Some(s) = session {
        h.insert("Mcp-Session-Id".into(), json!(s));
    }
    for (k, v) in headers {
        h.insert(k.clone(), json!(v));
    }
    let out = tool(
        "x402_call",
        json!({ "url": url, "method": "POST", "body": body, "headers": h, "pay": "auto", "max_chars": 400000 }),
        120,
    )
    .await?;
    if out["payment_required"].as_bool() == Some(true) {
        let offers: Vec<String> = out["offers"].as_array().map(|a| a.iter().map(offer_line).collect()).unwrap_or_default();
        return Err(format!(
            "x402 payment required ({}) — {}. Pay with your own wallet: x402_quote url={url}",
            offers.join(" | "),
            out["note"].as_str().unwrap_or("not paid")
        ));
    }
    let status = out["status"].as_u64().unwrap_or(0);
    let ctype = out["content_type"].as_str().unwrap_or("").to_string();
    let text = match out.get("json") {
        Some(j) => serde_json::to_string(j).unwrap_or_default(),
        None => out["text"].as_str().unwrap_or("").to_string(),
    };
    if status >= 400 {
        let why = out["payment_error"].as_str().unwrap_or("");
        return Err(format!("HTTP {status}: {why} {}", text.chars().take(200).collect::<String>()).trim().to_string());
    }
    let ctype = if out.get("json").is_some() { "application/json".to_string() } else { ctype };
    Ok((text, ctype))
}

// ── scanning the x402 index ──────────────────────────────────────────

/// An MCP server as the x402 index sees it: one root, its paid tools.
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct Root {
    pub root: String,
    pub host: String,
    pub name: String,
    pub description: String,
    pub tools: usize,
    pub min_price: Option<f64>,
    pub max_price: Option<f64>,
    pub networks: Vec<String>,
    pub calls_30d: u64,
}

impl Root {
    /// Where to speak MCP: the root itself when it already ends in an mcp
    /// segment, else `<root>/mcp`.
    pub fn candidates(&self) -> Vec<String> {
        let r = self.root.trim_end_matches('/');
        let last = r.rsplit('/').next().unwrap_or("");
        if is_mcp_segment(last) {
            vec![r.to_string()]
        } else {
            vec![r.to_string(), format!("{r}/mcp")]
        }
    }
}

/// `mcp`, `mcp-x402`, `mcp_server`, `mcp-http`, `mcp-sse`, `mcp-v2` …
fn is_mcp_segment(s: &str) -> bool {
    let s = s.to_lowercase();
    let Some(rest) = s.strip_prefix("mcp") else { return false };
    if rest.is_empty() {
        return true;
    }
    let Some(tail) = rest.strip_prefix(['-', '_']) else { return false };
    // The whole remainder, not a prefix: `mcp-server-audit` is a REST route
    // that happens to start with the words, not an MCP endpoint.
    matches!(tail, "x402" | "server" | "http" | "sse")
        || (tail.starts_with('v') && tail.len() > 1 && tail[1..].chars().all(|c| c.is_ascii_digit()))
}

/// The MCP endpoint a paid resource belongs to — the same rule the x402
/// index uses for kind=mcp, so the two never disagree about what a server is.
fn mcp_root(url: &str, kind: &str) -> Option<String> {
    let (scheme, rest) = url.split_once("://")?;
    let (host, path) = rest.split_once('/').map(|(h, p)| (h, format!("/{p}"))).unwrap_or((rest, String::new()));
    let path = path.split(['?', '#']).next().unwrap_or("");
    let segs: Vec<&str> = path.split('/').filter(|s| !s.is_empty()).collect();
    for (i, s) in segs.iter().enumerate() {
        if is_mcp_segment(s) {
            return Some(format!("{scheme}://{host}/{}", segs[..=i].join("/")));
        }
    }
    if kind == "mcp" {
        return Some(url.to_string());
    }
    host.to_lowercase().starts_with("mcp.").then(|| format!("{scheme}://{host}"))
}

pub async fn scan() -> Result<Vec<Root>, String> {
    let mut roots: BTreeMap<String, Root> = BTreeMap::new();
    let mut offset = 0u64;
    loop {
        let page = tool("x402_search", json!({ "query": "mcp", "limit": 500, "offset": offset }), 60).await?;
        let items = page["items"].as_array().cloned().unwrap_or_default();
        for s in &items {
            let Some(url) = s["url"].as_str() else { continue };
            let Some(root) = mcp_root(url, s["type"].as_str().unwrap_or("")) else { continue };
            let r = roots.entry(root.clone()).or_insert_with(|| Root {
                host: s["host"].as_str().unwrap_or("").to_string(),
                root,
                ..Default::default()
            });
            r.tools += 1;
            r.calls_30d += s["calls_30d"].as_u64().unwrap_or(0);
            if let Some(p) = s["price_usd"].as_f64() {
                r.min_price = Some(r.min_price.map_or(p, |m| m.min(p)));
                r.max_price = Some(r.max_price.map_or(p, |m| m.max(p)));
            }
            for n in s["networks"].as_array().into_iter().flatten().filter_map(|n| n.as_str()) {
                if !r.networks.iter().any(|x| x == n) {
                    r.networks.push(n.to_string());
                }
            }
            if r.name.is_empty() {
                r.name = s["name"].as_str().unwrap_or("").to_string();
            }
            if r.description.is_empty() {
                r.description = s["description"].as_str().unwrap_or("").chars().take(300).collect();
            }
        }
        offset += 500;
        if items.is_empty() || offset >= page["total"].as_u64().unwrap_or(0) || offset >= 5000 {
            break;
        }
    }
    Ok(roots.into_values().collect())
}

// ── enabling ─────────────────────────────────────────────────────────

/// What a probe of one root found. `endpoint` is the URL that answered.
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct Outcome {
    pub root: String,
    pub endpoint: String,
    /// enabled | already | gated | key | down
    pub verdict: String,
    pub detail: String,
    pub tools: usize,
    pub checked_at: u64,
}

fn cache_path() -> std::path::PathBuf {
    crate::store::hub_dir().join("x402.json")
}

pub fn cached() -> Vec<Outcome> {
    std::fs::read_to_string(cache_path()).ok().and_then(|s| serde_json::from_str(&s).ok()).unwrap_or_default()
}

fn save(rows: &[Outcome]) {
    let _ = std::fs::create_dir_all(crate::store::hub_dir());
    let _ = std::fs::write(cache_path(), serde_json::to_string_pretty(rows).unwrap_or_default());
}

static RUN: Mutex<Option<Value>> = Mutex::new(None);

pub fn status() -> Value {
    let rows = cached();
    let mut by: BTreeMap<&str, usize> = BTreeMap::new();
    for r in &rows {
        *by.entry(r.verdict.as_str()).or_default() += 1;
    }
    json!({
        "x402": base(),
        "running": RUN.lock().ok().map(|g| g.as_ref().map(|v| v["running"] == true).unwrap_or(false)).unwrap_or(false),
        "last": RUN.lock().ok().and_then(|g| g.clone()),
        "known": rows.len(),
        "verdicts": by,
        "rows": rows,
    })
}

fn set_run(v: Value) {
    if let Ok(mut g) = RUN.lock() {
        *g = Some(v);
    }
}

/// Is this probe error a server that wants payment (and nothing else)?
fn wants_payment(err: &str) -> bool {
    err.contains("HTTP 402")
}

/// Probe every MCP server the x402 index knows and register the ones that
/// answer — or answer "402 pay me" and nothing else. A server that asks for an
/// account or key (401/403) needs more than x402 and is left alone. Probing
/// never pays: background probes do not spend money.
pub async fn enable(state: &Arc<AppState>) -> Result<Value, String> {
    set_run(json!({ "running": true, "started_at": now(), "phase": "scanning the x402 index" }));
    let roots = match scan().await {
        Ok(r) => r,
        Err(e) => {
            set_run(json!({ "running": false, "error": e }));
            return Err(e);
        }
    };
    let have: HashMap<String, String> = state
        .all_servers()
        .await
        .into_iter()
        .map(|s| (s.url.trim_end_matches('/').to_string(), s.id))
        .collect();
    let mut taken: std::collections::HashSet<String> = state.all_servers().await.into_iter().map(|s| s.id).collect();

    set_run(json!({ "running": true, "started_at": now(), "phase": format!("probing {} servers", roots.len()) }));
    let mut outcomes: Vec<Outcome> = Vec::new();
    let mut added: Vec<(ServerEntry, crate::store::Probe)> = Vec::new();

    for chunk in roots.chunks(16) {
        let jobs = chunk.iter().map(|root| {
            let have = &have;
            async move {
                let mut last = Outcome { root: root.root.clone(), verdict: "down".into(), checked_at: now(), ..Default::default() };
                for ep in root.candidates() {
                    if let Some(id) = have.get(ep.trim_end_matches('/')) {
                        return (Outcome { endpoint: ep, verdict: "already".into(), detail: format!("registered as `{id}`"), ..last }, None);
                    }
                    let entry = ServerEntry {
                        id: clean_id(&root.host),
                        name: root.host.clone(),
                        url: ep.clone(),
                        source: "user".into(),
                        pay: "x402".into(),
                        origin: "x402".into(),
                        note: paid_note(root),
                        added_at: now(),
                        ..Default::default()
                    };
                    let p = crate::upstream::probe_in(&entry, 15, false).await;
                    if p.ok {
                        let n = p.tools.len();
                        return (Outcome { endpoint: ep, verdict: "enabled".into(), tools: n, ..last }, Some((entry, p)));
                    }
                    if wants_payment(&p.error) {
                        return (
                            Outcome { endpoint: ep, verdict: "gated".into(), detail: "answers 402 before the handshake — paid per call".into(), ..last },
                            Some((entry, p)),
                        );
                    }
                    let key = p.error.contains("HTTP 401") || p.error.contains("HTTP 403");
                    if key || last.endpoint.is_empty() {
                        last.endpoint = ep;
                        last.verdict = if key { "key".into() } else { "down".into() };
                        last.detail = p.error.chars().take(160).collect();
                    }
                }
                (last, None)
            }
        });
        for (out, hit) in futures_util::future::join_all(jobs).await {
            if let Some((mut entry, probe)) = hit {
                let mut id = entry.id.clone();
                let mut n = 2;
                while taken.contains(&id) {
                    id = format!("{}-{n}", entry.id);
                    n += 1;
                }
                entry.id = id.clone();
                taken.insert(id);
                added.push((entry, probe));
            }
            outcomes.push(out);
        }
    }

    {
        let mut user = state.user.write().await;
        for (e, _) in &added {
            user.push(e.clone());
        }
    }
    for (e, p) in &added {
        state.disabled.write().await.remove(&e.id);
        let mut probes = state.probes.write().await;
        probes.insert(e.id.clone(), p.clone());
    }
    state.persist().await;
    crate::store::save_probes(&state.probes.read().await.clone());
    save(&outcomes);

    let count = |v: &str| outcomes.iter().filter(|o| o.verdict == v).count();
    let summary = json!({
        "running": false,
        "finished_at": now(),
        "scanned": roots.len(),
        "enabled": count("enabled"),
        "gated": count("gated"),
        "already": count("already"),
        "needs_key": count("key"),
        "down": count("down"),
        "added": added.iter().map(|(e, _)| e.id.clone()).collect::<Vec<_>>(),
    });
    set_run(summary.clone());
    Ok(summary)
}

fn paid_note(r: &Root) -> String {
    let price = match (r.min_price, r.max_price) {
        (Some(a), Some(b)) if (a - b).abs() > f64::EPSILON => format!("${a}–${b}"),
        (Some(a), _) => format!("${a}"),
        _ => "priced per call".into(),
    };
    format!("x402 · {} paid tool{} · {price} · {}", r.tools, if r.tools == 1 { "" } else { "s" }, r.networks.join(", "))
}

/// Catalog rows for the x402 registry — every MCP server the index knows,
/// carrying the verdict of the last probe when there was one.
pub async fn listings(q: &str, limit: usize) -> Result<Vec<crate::catalog::Listing>, String> {
    let known: HashMap<String, Outcome> = cached().into_iter().map(|o| (o.root.clone(), o)).collect();
    let mut rows: Vec<Root> = scan().await?;
    rows.sort_by(|a, b| b.calls_30d.cmp(&a.calls_30d));
    let q = q.to_lowercase();
    Ok(rows
        .into_iter()
        .filter(|r| q.is_empty() || q.split_whitespace().all(|t| format!("{} {} {}", r.host, r.name, r.description).to_lowercase().contains(t)))
        .take(limit)
        .map(|r| {
            let o = known.get(&r.root);
            let endpoint = o.filter(|o| !o.endpoint.is_empty()).map(|o| o.endpoint.clone()).unwrap_or_else(|| r.candidates().pop().unwrap_or_default());
            let verdict = o.map(|o| o.verdict.as_str()).unwrap_or("");
            crate::catalog::Listing {
                id: clean_id(&r.host),
                name: r.host.clone(),
                description: if r.description.is_empty() { r.name.clone() } else { r.description.clone() },
                url: endpoint,
                registry: "x402".into(),
                status: match verdict {
                    "enabled" | "already" => "live".into(),
                    "gated" => "paid".into(),
                    "key" => "auth".into(),
                    "down" => "down".into(),
                    _ => String::new(),
                },
                tools: o.map(|o| o.tools as u64).filter(|n| *n > 0),
                needs_key: verdict == "key",
                pay: "x402".into(),
                price_usd: r.min_price,
                note: paid_note(&r),
                uses: Some(r.calls_30d),
                ..Default::default()
            }
        })
        .collect())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn mcp_segments() {
        for s in ["mcp", "MCP", "mcp-x402", "mcp_server", "mcp-v2", "mcp-http"] {
            assert!(is_mcp_segment(s), "{s}");
        }
        for s in ["mcpfoo", "pde-mcp", "mcp-other", "mcp-server-audit", "x"] {
            assert!(!is_mcp_segment(s), "{s}");
        }
    }

    #[test]
    fn roots() {
        assert_eq!(mcp_root("https://mcp.newstex.com/x402/search", "").as_deref(), Some("https://mcp.newstex.com"));
        assert_eq!(mcp_root("https://api.remix.live/mcp/x402-http/compile", "").as_deref(), Some("https://api.remix.live/mcp"));
        assert_eq!(mcp_root("https://example.com/a/b", ""), None);
        assert_eq!(mcp_root("https://example.com/a/b", "mcp").as_deref(), Some("https://example.com/a/b"));
    }

    #[test]
    fn endpoints_try_the_root_then_slash_mcp() {
        let r = Root { root: "https://mcp.newstex.com".into(), ..Default::default() };
        assert_eq!(r.candidates(), vec!["https://mcp.newstex.com", "https://mcp.newstex.com/mcp"]);
        let r = Root { root: "https://api.remix.live/mcp".into(), ..Default::default() };
        assert_eq!(r.candidates(), vec!["https://api.remix.live/mcp"]);
    }

    #[test]
    fn payment_wanted_only_on_402() {
        assert!(wants_payment("HTTP 402 Payment Required: {}"));
        assert!(!wants_payment("HTTP 401 Unauthorized: Seller login required"));
    }
}
