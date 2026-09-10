//! Hub types.
//!
//! Anything that lists MCP servers is a hub, and there are three kinds:
//!
//!   mod        — a mod-protocol hub: this software. It answers GET /hub with
//!                a manifest, GET /servers with its registry, and POST /mcp
//!                with every tool it aggregates. This deployment is one; any
//!                other deployment is a peer you can browse and connect —
//!                connecting nests it, so its tools arrive as peer__server__tool.
//!   index      — an internet-wide crawler with probe status per server
//!                (orbit/mcpscan). Browsable by query, connectable as a whole.
//!   directory  — a public registry: the official one, Smithery, Glama,
//!                PulseMCP, Docker's catalog. Browsable; rows connect one by one.
//!
//! The list here is the union of the built-ins, whatever index the fleet is
//! running, and the peers the owner added by URL (~/.mod/mcp/hubs.json). Each
//! is probed for what it holds — how many servers, how many answer, how many
//! tools — so "where else could I connect to" is one call.

use crate::state::AppState;
use crate::store::{self, PeerHub};
use serde::Serialize;
use serde_json::{json, Value};
use std::sync::Arc;
use std::time::{Duration, Instant};

/// How this hub introduces itself to another one.
pub const MANIFEST_TYPE: &str = "mod-hub";
pub const MANIFEST_PROTOCOL: &str = "mod/mcp-hub/1";
/// How long a probed hubs view is served before it is re-probed on demand.
pub const CACHE_SECS: u64 = 600;

#[derive(Clone, Debug, Default, Serialize)]
pub struct Hub {
    pub id: String,
    /// mod | index | directory
    pub kind: String,
    pub name: String,
    pub description: String,
    /// API base for a mod/index hub, the directory's API for a directory.
    pub url: String,
    #[serde(skip_serializing_if = "String::is_empty")]
    pub homepage: String,
    /// The hub's own MCP endpoint, when it has one — a mod or index hub can be
    /// registered as a single upstream server.
    #[serde(skip_serializing_if = "String::is_empty")]
    pub mcp: String,
    /// Which `registry=` value on /catalog searches it.
    #[serde(skip_serializing_if = "String::is_empty")]
    pub registry: String,
    /// builtin | fleet | user
    pub source: String,
    /// True for the hub answering the request.
    #[serde(rename = "self")]
    pub is_self: bool,
    pub needs_key: bool,
    #[serde(skip_serializing_if = "String::is_empty")]
    pub key_name: String,
    /// Reachable and usable from here right now.
    pub ready: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub servers: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub live: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tools: Option<u64>,
    #[serde(skip_serializing_if = "String::is_empty")]
    pub version: String,
    pub latency_ms: u64,
    pub checked_at: u64,
    #[serde(skip_serializing_if = "String::is_empty")]
    pub error: String,
    #[serde(skip_serializing_if = "String::is_empty")]
    pub note: String,
}

fn client() -> reqwest::Client {
    reqwest::Client::builder()
        .timeout(Duration::from_secs(15))
        .user_agent("mcp-hub")
        .build()
        .expect("reqwest client")
}

async fn get_json(url: &str, headers: &std::collections::HashMap<String, String>) -> Result<Value, String> {
    let mut req = client().get(url).header("Accept", "application/json");
    for (k, v) in headers {
        req = req.header(k.as_str(), v.as_str());
    }
    let r = req.send().await.map_err(|e| e.to_string())?;
    let status = r.status();
    let body = r.text().await.map_err(|e| e.to_string())?;
    if !status.is_success() {
        let snippet: String = body.trim().chars().take(140).collect();
        return Err(format!("HTTP {status}: {snippet}"));
    }
    serde_json::from_str(&body).map_err(|e| format!("bad JSON: {e}"))
}

/// The manifest this hub serves at GET /hub — what a peer reads to learn
/// what kind of hub it is talking to and what it holds.
pub async fn manifest(state: &Arc<AppState>, public_url: &str) -> Value {
    let rows = crate::hub::servers_view(state).await;
    let up = rows.iter().filter(|r| r["probe"]["ok"].as_bool().unwrap_or(false)).count();
    let tools: u64 = rows.iter().map(|r| r["probe"]["toolCount"].as_u64().unwrap_or(0)).sum();
    let peers: Vec<String> = state.peers.read().await.iter().map(|p| p.id.clone()).collect();
    json!({
        "type": MANIFEST_TYPE,
        "protocol": MANIFEST_PROTOCOL,
        "kind": "mod",
        "id": "mcp",
        "name": "MCP Hub",
        "version": crate::hub::SERVER_VERSION,
        "url": public_url,
        "mcp": format!("{public_url}/mcp"),
        "servers": rows.len(),
        "up": up,
        "tools": tools,
        "kinds": ["mod", "index", "directory"],
        "peers": peers,
        "browse": format!("{public_url}/servers"),
        "catalog": format!("{public_url}/catalog?q="),
        "connect": "POST /servers {url, id?, headers?} — probed before it is kept",
        "namespacing": "server__tool; aggregating this hub from another nests one level: hub__server__tool",
        "hubs": format!("{public_url}/hubs"),
    })
}

fn directory(id: &str, name: &str, url: &str, home: &str, desc: &str, key: &str) -> Hub {
    Hub {
        id: id.into(),
        kind: "directory".into(),
        name: name.into(),
        description: desc.into(),
        url: url.into(),
        homepage: home.into(),
        registry: id.into(),
        source: "builtin".into(),
        needs_key: !key.is_empty(),
        key_name: key.into(),
        ready: key.is_empty() || crate::web::key(&key.to_lowercase().replace("_api_key", "")).is_some(),
        ..Default::default()
    }
}

/// The hubs this software knows about without being told.
fn builtin(public_url: &str) -> Vec<Hub> {
    vec![
        Hub {
            id: "mod".into(),
            kind: "mod".into(),
            name: "this hub".into(),
            description: "The mod-protocol hub you are looking at: the local fleet, the servers registered here, and one MCP endpoint over all of them.".into(),
            url: public_url.into(),
            mcp: format!("{public_url}/mcp"),
            registry: "hub".into(),
            source: "builtin".into(),
            is_self: true,
            ready: true,
            ..Default::default()
        },
        directory(
            "official",
            "Official registry",
            "https://registry.modelcontextprotocol.io/v0/servers",
            "https://registry.modelcontextprotocol.io/",
            "The MCP project's own index. Keyless; every row with a remote endpoint is listed here.",
            "",
        ),
        directory(
            "smithery",
            "Smithery",
            "https://registry.smithery.ai/servers",
            "https://smithery.ai/",
            "The largest third-party directory. Listing is keyless; connecting a Smithery-hosted server needs a Smithery key.",
            "",
        ),
        directory(
            "glama",
            "Glama",
            "https://glama.ai/api/mcp/v1/servers",
            "https://glama.ai/mcp/servers",
            "Curated directory with quality scores. The API wants a Glama key; put it in ~/.mod/mcp/web.json as `glama`.",
            "GLAMA_API_KEY",
        ),
        directory(
            "pulsemcp",
            "PulseMCP",
            "https://api.pulsemcp.com/v0.1/servers",
            "https://www.pulsemcp.com/servers",
            "Directory with install counts and remotes. v0.1 needs an X-API-Key; put it in web.json as `pulsemcp`.",
            "PULSEMCP_API_KEY",
        ),
        directory(
            "docker",
            "Docker MCP catalog",
            "https://github.com/docker/mcp-registry/tree/main/servers",
            "https://hub.docker.com/mcp",
            "Docker's curated catalog of containerised servers. Browsed through the index, which mirrors it.",
            "",
        ),
    ]
}

/// The index the fleet is running, if any — mcpscan by id, or any aggregated
/// server whose MCP face is the `mcp_find`/`mcp_stats` pair.
async fn fleet_indexes(state: &Arc<AppState>) -> Vec<Hub> {
    let probes = state.probes.read().await.clone();
    let mut out = Vec::new();
    for s in state.all_servers().await {
        let looks_like_index = s.id == "mcpscan"
            || probes
                .get(&s.id)
                .map(|p| {
                    p.tools.iter().any(|t| t["name"].as_str() == Some("mcp_find"))
                        && p.tools.iter().any(|t| t["name"].as_str() == Some("mcp_stats"))
                })
                .unwrap_or(false);
        if !looks_like_index || s.url.is_empty() {
            continue;
        }
        let base = s.url.trim_end_matches('/').trim_end_matches("/mcp").to_string();
        out.push(Hub {
            id: s.id.clone(),
            kind: "index".into(),
            name: if s.id == "mcpscan" { "mcpscan — internet-wide index".into() } else { s.name.clone() },
            description: "Crawls every public directory and probes every endpoint on a loop, so each row carries a live/auth/down status and its real tool list.".into(),
            url: base,
            homepage: "https://modc2.com/mcpscan".into(),
            mcp: s.url.clone(),
            registry: s.id.clone(),
            // Discovered, not added by hand — even when its server row was —
            // so it is never offered for "forget"; that is for peers.
            source: "fleet".into(),
            ready: true,
            ..Default::default()
        });
    }
    out
}

fn peer_hub(p: &PeerHub) -> Hub {
    Hub {
        id: p.id.clone(),
        kind: if p.kind.is_empty() { "mod".into() } else { p.kind.clone() },
        name: if p.name.is_empty() { p.id.clone() } else { p.name.clone() },
        description: p.note.clone(),
        url: p.url.trim_end_matches('/').to_string(),
        mcp: format!("{}/mcp", p.url.trim_end_matches('/')),
        registry: p.id.clone(),
        source: "user".into(),
        needs_key: !p.headers.is_empty(),
        ready: true,
        ..Default::default()
    }
}

/// Every hub this one knows about, unprobed.
pub async fn known(state: &Arc<AppState>, public_url: &str) -> Vec<Hub> {
    let mut out = builtin(public_url);
    let indexes = fleet_indexes(state).await;
    let peers: Vec<Hub> = state.peers.read().await.iter().map(peer_hub).collect();
    // A peer added by hand outranks a built-in with the same id.
    for h in indexes.into_iter().chain(peers) {
        out.retain(|b| b.id != h.id);
        out.push(h);
    }
    out
}

/// Ask one hub what it holds. Each kind speaks differently; a hub that does
/// not answer keeps its row with the error on it — a directory being down
/// should not look like a directory being empty.
pub async fn probe(mut h: Hub, headers: &std::collections::HashMap<String, String>) -> Hub {
    let t0 = Instant::now();
    let res = fill(&mut h, headers).await;
    h.latency_ms = t0.elapsed().as_millis() as u64;
    h.checked_at = store::now();
    if let Err(e) = res {
        h.error = e;
        h.ready = false;
    }
    h
}

async fn fill(h: &mut Hub, headers: &std::collections::HashMap<String, String>) -> Result<(), String> {
    match h.kind.as_str() {
        "mod" if h.is_self => Ok(()),
        "mod" => match get_json(&format!("{}/hub", h.url), headers).await {
            Ok(v) if v["type"].as_str() == Some(MANIFEST_TYPE) => {
                h.servers = v["servers"].as_u64();
                h.live = v["up"].as_u64();
                h.tools = v["tools"].as_u64();
                h.version = v["version"].as_str().unwrap_or_default().to_string();
                if h.description.is_empty() {
                    h.description = format!("{} — a mod-protocol hub", v["name"].as_str().unwrap_or(&h.id));
                }
                if let Some(m) = v["mcp"].as_str() {
                    h.mcp = m.to_string();
                }
                Ok(())
            }
            Ok(_) => {
                // An older hub without the manifest still has /stats.
                let s = get_json(&format!("{}/stats", h.url), headers).await?;
                h.servers = s["servers"].as_u64();
                h.live = s["up"].as_u64();
                h.tools = s["tools"].as_u64();
                Ok(())
            }
            Err(e) => Err(e),
        },
        "index" => {
            let s = get_json(&format!("{}/stats", h.url), headers).await?;
            h.servers = s["servers"].as_u64();
            h.live = s["by_status"]["live"].as_u64();
            h.tools = s["tools"].as_u64();
            Ok(())
        }
        _ => match h.id.as_str() {
            "official" => {
                // The registry paginates by cursor and never says how many rows
                // there are; one page proves it answers, the index counts it.
                let v = get_json(&format!("{}?limit=1&version=latest", h.url), headers).await?;
                if v["servers"].as_array().map(|a| a.is_empty()).unwrap_or(true) {
                    Err("registry answered with no rows".into())
                } else {
                    Ok(())
                }
            }
            "smithery" => {
                let v = get_json(&format!("{}?pageSize=1", h.url), headers).await?;
                h.servers = v["pagination"]["totalCount"].as_u64();
                Ok(())
            }
            "glama" => match crate::web::key("glama") {
                Some(k) => {
                    let mut hd = headers.clone();
                    hd.insert("Authorization".into(), format!("Bearer {k}"));
                    let v = get_json(&format!("{}?first=1", h.url), &hd).await?;
                    h.servers = v["pageInfo"]["totalCount"].as_u64().or(v["totalCount"].as_u64());
                    Ok(())
                }
                None => Err("no key — set GLAMA_API_KEY or web.json `glama`".into()),
            },
            "pulsemcp" => match crate::web::key("pulsemcp") {
                Some(k) => {
                    let mut hd = headers.clone();
                    hd.insert("X-API-Key".into(), k);
                    let v = get_json(&format!("{}?count_per_page=1", h.url), &hd).await?;
                    h.servers = v["total_count"].as_u64();
                    Ok(())
                }
                None => Err("no key — set PULSEMCP_API_KEY or web.json `pulsemcp`".into()),
            },
            _ => Ok(()),
        },
    }
}

/// Every known hub, probed concurrently, with the index's per-directory totals
/// filled in where a directory cannot count itself.
pub async fn probe_all(state: &Arc<AppState>, public_url: &str) -> Vec<Hub> {
    let peers = state.peers.read().await.clone();
    let hubs = known(state, public_url).await;
    let jobs = hubs.into_iter().map(|h| {
        let headers = peers
            .iter()
            .find(|p| p.id == h.id)
            .map(|p| p.headers.clone())
            .unwrap_or_default();
        async move { probe(h, &headers).await }
    });
    let mut hubs = futures_util::future::join_all(jobs).await;

    // Fill the self row from state and the directory totals from the index.
    let rows = crate::hub::servers_view(state).await;
    let up = rows.iter().filter(|r| r["probe"]["ok"].as_bool().unwrap_or(false)).count() as u64;
    let tools: u64 = rows.iter().map(|r| r["probe"]["toolCount"].as_u64().unwrap_or(0)).sum();
    for h in hubs.iter_mut().filter(|h| h.is_self) {
        h.servers = Some(rows.len() as u64);
        h.live = Some(up);
        h.tools = Some(tools);
        h.version = crate::hub::SERVER_VERSION.into();
        h.checked_at = store::now();
    }
    if let Some(index) = hubs.iter().find(|h| h.kind == "index" && h.ready).cloned() {
        if let Ok(v) = get_json(&format!("{}/sources", index.url), &Default::default()).await {
            for rep in v["reports"].as_array().cloned().unwrap_or_default() {
                let Some(src) = rep["source"].as_str() else { continue };
                let total = rep["total"].as_u64().or(rep["found"].as_u64()).filter(|n| *n > 0);
                if let Some(h) = hubs.iter_mut().find(|h| h.id == src && h.kind == "directory") {
                    if h.servers.is_none() && total.is_some() {
                        h.servers = total;
                        h.note = format!("counted by {}", index.id);
                    }
                    if h.id == "docker" || h.id == "github" {
                        h.registry = format!("{}:{}", index.id, src);
                        h.ready = rep["ok"].as_bool().unwrap_or(false);
                    }
                }
            }
        }
    }
    hubs.sort_by_key(|h| {
        (
            !h.is_self,
            match h.kind.as_str() {
                "mod" => 0,
                "index" => 1,
                _ => 2,
            },
            !h.ready,
            h.id.clone(),
        )
    });
    hubs
}

/// Cached view: re-probed at most every `max_age` seconds.
pub async fn view(state: &Arc<AppState>, public_url: &str, max_age: u64) -> Value {
    let now = store::now();
    let fresh = {
        let cache = state.hubs_cache.read().await;
        cache.as_ref().filter(|(at, _)| now.saturating_sub(*at) < max_age).map(|(_, h)| h.clone())
    };
    let hubs = match fresh {
        Some(h) => h,
        None => {
            let h = probe_all(state, public_url).await;
            *state.hubs_cache.write().await = Some((now, h.clone()));
            h
        }
    };
    let mut kinds = serde_json::Map::new();
    for h in &hubs {
        let e = kinds.entry(h.kind.clone()).or_insert(json!(0));
        *e = json!(e.as_u64().unwrap_or(0) + 1);
    }
    json!({
        "self": hubs.iter().find(|h| h.is_self).map(|h| h.id.clone()),
        "count": hubs.len(),
        "ready": hubs.iter().filter(|h| h.ready).count(),
        "kinds": kinds,
        "hubs": hubs,
        "checked_at": state.hubs_cache.read().await.as_ref().map(|(at, _)| *at).unwrap_or(now),
        "kind_docs": {
            "mod": "a mod-protocol hub like this one — browse its servers, or connect the whole hub and get its tools as hub__server__tool",
            "index": "an internet-wide crawl with a probe status per server — search it, or connect it and search from your client",
            "directory": "a public registry — search it, connect rows one at a time",
        },
    })
}

/// Work out what kind of hub answers at `url`, for POST /hubs. A mod hub is
/// recognised by its manifest, an index by its stats shape; anything that
/// only speaks MCP is not a hub — register it as a server instead.
pub async fn identify(url: &str, headers: &std::collections::HashMap<String, String>) -> Result<Hub, String> {
    let base = url.trim().trim_end_matches('/').trim_end_matches("/mcp").to_string();
    if !base.starts_with("http://") && !base.starts_with("https://") {
        return Err("`url` must be http(s)".into());
    }
    if let Ok(v) = get_json(&format!("{base}/hub"), headers).await {
        if v["type"].as_str() == Some(MANIFEST_TYPE) {
            let mut h = Hub {
                id: store::clean_id(v["id"].as_str().unwrap_or("hub")),
                kind: "mod".into(),
                name: v["name"].as_str().unwrap_or("mod hub").to_string(),
                url: base.clone(),
                mcp: v["mcp"].as_str().unwrap_or(&format!("{base}/mcp")).to_string(),
                source: "user".into(),
                ready: true,
                ..Default::default()
            };
            h.servers = v["servers"].as_u64();
            h.live = v["up"].as_u64();
            h.tools = v["tools"].as_u64();
            h.version = v["version"].as_str().unwrap_or_default().to_string();
            return Ok(h);
        }
    }
    let s = get_json(&format!("{base}/stats"), headers)
        .await
        .map_err(|e| format!("no hub at {base}: /hub and /stats both failed ({e})"))?;
    if s["by_status"].is_object() {
        return Ok(Hub {
            id: "index".into(),
            kind: "index".into(),
            name: "index".into(),
            url: base.clone(),
            mcp: format!("{base}/mcp"),
            source: "user".into(),
            ready: true,
            servers: s["servers"].as_u64(),
            live: s["by_status"]["live"].as_u64(),
            tools: s["tools"].as_u64(),
            ..Default::default()
        });
    }
    if s["servers"].is_number() && s["by_source"].is_object() {
        return Ok(Hub {
            id: "hub".into(),
            kind: "mod".into(),
            name: "mod hub".into(),
            url: base.clone(),
            mcp: format!("{base}/mcp"),
            source: "user".into(),
            ready: true,
            servers: s["servers"].as_u64(),
            live: s["up"].as_u64(),
            tools: s["tools"].as_u64(),
            ..Default::default()
        });
    }
    Err(format!("{base} answers /stats but not like a hub — if it speaks MCP, add it as a server"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn builtins_are_complete() {
        let hubs = builtin("http://localhost:50360");
        assert!(hubs.iter().filter(|h| h.is_self).count() == 1);
        for h in &hubs {
            assert!(!h.url.is_empty(), "{} has no url", h.id);
            assert!(matches!(h.kind.as_str(), "mod" | "index" | "directory"), "{} kind", h.id);
        }
        let glama = hubs.iter().find(|h| h.id == "glama").unwrap();
        assert!(glama.needs_key);
    }
}
