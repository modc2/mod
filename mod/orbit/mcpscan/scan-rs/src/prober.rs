//! The constant scraper.
//!
//! Three loops, all running forever:
//!   crawl — re-read every public directory (slow clock, hours)
//!   probe — handshake with endpoints that are due, batch after batch, always
//!   hunt  — knock on `/mcp` and friends at the domain of a server nobody
//!           published an endpoint for, because plenty of them serve one anyway
//!
//! The probe loop is deliberately never idle: the index is tens of thousands
//! of endpoints and each has its own re-check clock (live servers every few
//! hours, dead ones with exponential backoff), so there is always something
//! due. Batches are small and concurrency is capped — this is a crawler, and
//! it behaves like one.

use crate::index::{status_of, Index};
use crate::store::{self, host_of, Entry, SourceReport};
use crate::{sources, upstream};
use std::collections::{HashMap, HashSet};
use std::sync::atomic::Ordering;
use std::sync::Arc;

fn envn(k: &str, default: u64) -> u64 {
    std::env::var(k).ok().and_then(|v| v.parse().ok()).unwrap_or(default)
}

/// Re-probe clocks, in seconds. A server that has been dead for a while is
/// asked less and less often, but never dropped: this index is a history of
/// what existed, not only of what is up right now.
fn due_in(e: &Entry) -> u64 {
    let base = match status_of(e) {
        "live" => envn("MCPSCAN_LIVE_EVERY", 6 * 3600),
        "auth" => envn("MCPSCAN_AUTH_EVERY", 12 * 3600),
        "error" => envn("MCPSCAN_ERROR_EVERY", 12 * 3600),
        _ => envn("MCPSCAN_DOWN_EVERY", 6 * 3600),
    };
    let backoff = base.saturating_mul(1u64 << e.fails.min(4));
    backoff.min(envn("MCPSCAN_MAX_EVERY", 14 * 24 * 3600))
}

fn is_due(e: &Entry, now: u64) -> bool {
    if !e.probeable() {
        return false;
    }
    if e.checked_at == 0 {
        return true;
    }
    now.saturating_sub(e.checked_at) >= due_in(e)
}

/// One batch of probes. Returns how many endpoints were checked.
pub async fn probe_cycle(index: &Arc<Index>, batch: usize) -> usize {
    let now = store::now();
    let timeout = envn("MCPSCAN_PROBE_TIMEOUT", 8);
    let mut queue: Vec<(String, String, u64)> = {
        let entries = index.entries.read().await;
        entries
            .values()
            .filter(|e| is_due(e, now))
            .map(|e| (e.id.clone(), e.url.clone(), e.checked_at))
            .collect()
    };
    if queue.is_empty() {
        return 0;
    }
    // Never-probed first, then whatever has been waiting longest.
    queue.sort_by_key(|(_, _, checked)| *checked);
    queue.truncate(batch);

    let no_headers: HashMap<String, String> = HashMap::new();
    let jobs = queue.iter().map(|(id, url, _)| {
        let headers = no_headers.clone();
        async move { (id.clone(), upstream::probe(url, &headers, timeout).await) }
    });
    let results = futures_util::future::join_all(jobs).await;
    let n = results.len();
    for (id, out) in results {
        index.apply_probe(&id, &out).await;
    }
    {
        let mut scan = index.scan.write().await;
        scan.probes += n as u64;
        scan.batches += 1;
        scan.last_batch_at = store::now();
        scan.last_batch_size = n;
    }
    n
}

/// Probe one endpoint on demand and fold the result into the index, adding the
/// row if this URL has never been seen. Returns the entry id.
pub async fn probe_url(
    index: &Arc<Index>,
    url: &str,
    headers: &HashMap<String, String>,
    source: &str,
) -> (String, upstream::ProbeOut) {
    let timeout = envn("MCPSCAN_PROBE_TIMEOUT", 12);
    let out = upstream::probe(url, headers, timeout).await;
    let name = out
        .server_info
        .get("name")
        .and_then(|n| n.as_str())
        .map(String::from)
        .unwrap_or_else(|| host_of(url));
    let entry = Entry {
        id: store::slug(&name),
        name,
        url: url.to_string(),
        transport: "streamable-http".into(),
        sources: vec![source.to_string()],
        ..Default::default()
    };
    index.merge(vec![entry]).await;
    let id = index
        .by_url
        .read()
        .await
        .get(&store::canon_url(url))
        .cloned()
        .unwrap_or_else(|| store::slug(url));
    index.apply_probe(&id, &out).await;
    (id, out)
}

// ── the hunt ──────────────────────────────────────────────────────────

/// Paths an MCP server is actually served at, in the order they are worth
/// trying. Every hit here is a server no directory knew how to reach.
const HUNT_PATHS: [&str; 6] = ["/mcp", "/sse", "/api/mcp", "/mcp/sse", "/v1/mcp", "/mcp/v1"];

/// Domains where knocking is pointless (code hosts, package registries, the
/// directories themselves) or rude (this box's own fleet).
fn skip_host(h: &str) -> bool {
    const BLOCK: [&str; 16] = [
        "github.com",
        "raw.githubusercontent.com",
        "gitlab.com",
        "bitbucket.org",
        "npmjs.com",
        "www.npmjs.com",
        "pypi.org",
        "hub.docker.com",
        "docker.com",
        "smithery.ai",
        "glama.ai",
        "pulsemcp.com",
        "modelcontextprotocol.io",
        "registry.modelcontextprotocol.io",
        "localhost",
        "127.0.0.1",
    ];
    if h.is_empty() || !h.contains('.') && h != "localhost" {
        return true;
    }
    BLOCK.iter().any(|b| h == *b || h.ends_with(&format!(".{b}")))
        || h.starts_with("192.168.")
        || h.starts_with("10.")
        || h.starts_with("172.16.")
}

/// A domain worth knocking on, from whatever the directories published.
fn hunt_domain(e: &Entry) -> Option<String> {
    for cand in [&e.homepage, &e.repository] {
        let h = host_of(cand);
        if !skip_host(&h) {
            return Some(h);
        }
    }
    None
}

/// Knock on `budget` domains. Returns the ids that turned out to serve MCP.
pub async fn hunt_cycle(index: &Arc<Index>, budget: usize) -> Vec<String> {
    let now = store::now();
    let cooldown = envn("MCPSCAN_HUNT_COOLDOWN", 30 * 24 * 3600);
    let timeout = envn("MCPSCAN_HUNT_TIMEOUT", 6);

    let (targets, hunted_hosts) = {
        let entries = index.entries.read().await;
        // One knock per domain, ever, until the cooldown expires — a hundred
        // repos under one org must not become a hundred requests.
        let mut recent: HashSet<String> = HashSet::new();
        for e in entries.values() {
            if e.hunted_at > 0 && now.saturating_sub(e.hunted_at) < cooldown {
                if let Some(d) = hunt_domain(e) {
                    recent.insert(d);
                }
            }
            if e.probeable() {
                recent.insert(host_of(&e.url));
            }
        }
        let mut picked: Vec<(String, String)> = Vec::new();
        let mut seen: HashSet<String> = HashSet::new();
        for e in entries.values() {
            if e.probeable() || picked.len() >= budget {
                continue;
            }
            let Some(domain) = hunt_domain(e) else { continue };
            if recent.contains(&domain) || !seen.insert(domain.clone()) {
                continue;
            }
            picked.push((e.id.clone(), domain));
        }
        (picked, recent)
    };
    let _ = hunted_hosts;
    if targets.is_empty() {
        return Vec::new();
    }

    let no_headers: HashMap<String, String> = HashMap::new();
    let jobs = targets.iter().map(|(id, domain)| {
        let headers = no_headers.clone();
        async move {
            for path in HUNT_PATHS {
                let url = format!("https://{domain}{path}");
                let out = upstream::probe(&url, &headers, timeout).await;
                if out.status == "live" || out.status == "auth" {
                    return (id.clone(), Some((url, out)));
                }
            }
            (id.clone(), None)
        }
    });
    let results = futures_util::future::join_all(jobs).await;

    let mut hits = Vec::new();
    {
        let mut entries = index.entries.write().await;
        let mut by_url = index.by_url.write().await;
        for (id, found) in results {
            let Some(e) = entries.get_mut(&id) else { continue };
            e.hunted_at = store::now();
            match found {
                Some((url, out)) => {
                    e.url = url.clone();
                    e.transport = "streamable-http".into();
                    e.status = out.status.clone();
                    e.checked_at = out.checked_at;
                    e.latency_ms = out.latency_ms;
                    e.error = out.error.clone();
                    e.attempts += 1;
                    if out.status == "live" {
                        e.protocol_version = out.protocol_version.clone();
                        e.server_info = out.server_info.clone();
                        e.tools = out.tools.clone();
                    }
                    if !e.sources.iter().any(|s| s == "hunt") {
                        e.sources.push("hunt".into());
                    }
                    e.rebuild_hay();
                    by_url.insert(store::canon_url(&url), id.clone());
                    hits.push((id.clone(), e.name.clone(), url, e.status.clone(), e.tools.len()));
                }
                None => {}
            }
        }
    }
    {
        let mut scan = index.scan.write().await;
        scan.hunts += targets.len() as u64;
        scan.hunt_hits += hits.len() as u64;
        for (id, name, url, status, tools) in &hits {
            scan.note(serde_json::json!({
                "at": store::now(), "id": id, "name": name, "url": url,
                "status": status, "tools": tools, "found_by": "hunt", "changed": true,
            }));
        }
    }
    index.touch();
    hits.into_iter().map(|(id, ..)| id).collect()
}

// ── the crawl ─────────────────────────────────────────────────────────

/// Read the directories. `only` runs a single source; None runs them all,
/// concurrently, and merges each one's rows as it lands.
pub async fn crawl(index: &Arc<Index>, only: Option<&str>) -> Vec<SourceReport> {
    index.crawling.store(true, Ordering::Relaxed);
    let started = std::time::Instant::now();
    let list: Vec<String> = match only {
        Some(s) => vec![s.to_string()],
        None => sources::ALL.iter().map(|s| s.to_string()).collect(),
    };
    // Each source lands on its own: rows from the fast directories are
    // searchable while a slow one is still paging, and one stuck directory
    // can't hide the other five.
    let jobs = list.into_iter().map(|source| {
        let ix = index.clone();
        tokio::spawn(async move {
            let t = std::time::Instant::now();
            let res = sources::run(&source).await;
            let mut r = SourceReport {
                source: source.clone(),
                ms: t.elapsed().as_millis() as u64,
                ran_at: store::now(),
                ..Default::default()
            };
            match res {
                Ok((rows, total)) => {
                    r.ok = true;
                    r.found = rows.len() as u64;
                    r.total = total;
                    let (fresh, updated) = ix.merge(rows).await;
                    r.new = fresh;
                    r.updated = updated;
                    println!(
                        "crawl/{source}: {} rows{}, {} new, {} updated in {}ms",
                        r.found,
                        if r.total > r.found { format!(" of {} listed", r.total) } else { String::new() },
                        r.new,
                        r.updated,
                        r.ms
                    );
                }
                Err(e) => {
                    // `needs:KEY` is a configuration fact, not a failure.
                    if let Some(key) = e.strip_prefix("needs:") {
                        r.needs = key.to_string();
                        r.error = format!("set {key} to include this directory");
                    } else {
                        r.error = store::clip(&e, 300);
                        println!("crawl/{source}: {}", r.error);
                    }
                }
            }
            ix.set_report(r.clone()).await;
            r
        })
    });
    let reports: Vec<SourceReport> =
        futures_util::future::join_all(jobs).await.into_iter().flatten().collect();
    {
        let mut scan = index.scan.write().await;
        scan.last_crawl_at = store::now();
        scan.last_crawl_ms = started.elapsed().as_millis() as u64;
    }
    index.crawling.store(false, Ordering::Relaxed);
    index.flush().await;
    reports
}

/// Spawn the three loops. This is what makes the module a scraper rather than
/// a search box over someone else's list.
pub fn spawn_loops(index: Arc<Index>) {
    // crawl
    {
        let ix = index.clone();
        tokio::spawn(async move {
            let every = envn("MCPSCAN_CRAWL_SECS", 6 * 3600);
            let last = ix.reports.read().await.iter().map(|r| r.ran_at).max().unwrap_or(0);
            let age = store::now().saturating_sub(last);
            if age < every {
                tokio::time::sleep(std::time::Duration::from_secs(every - age)).await;
            }
            loop {
                let reports = crawl(&ix, None).await;
                let found: u64 = reports.iter().map(|r| r.found).sum();
                let fresh: u64 = reports.iter().map(|r| r.new).sum();
                println!("crawl: {found} rows from {} sources, {fresh} new", reports.len());
                tokio::time::sleep(std::time::Duration::from_secs(every)).await;
            }
        });
    }
    // probe — the loop that never stops
    {
        let ix = index.clone();
        tokio::spawn(async move {
            let batch = envn("MCPSCAN_BATCH", 64) as usize;
            let gap = envn("MCPSCAN_PROBE_GAP", 2);
            let idle = envn("MCPSCAN_IDLE_GAP", 60);
            loop {
                let n = probe_cycle(&ix, batch).await;
                tokio::time::sleep(std::time::Duration::from_secs(if n == 0 { idle } else { gap }))
                    .await;
            }
        });
    }
    // hunt
    {
        let ix = index.clone();
        tokio::spawn(async move {
            let every = envn("MCPSCAN_HUNT_SECS", 120);
            let budget = envn("MCPSCAN_HUNT_BATCH", 8) as usize;
            loop {
                tokio::time::sleep(std::time::Duration::from_secs(every)).await;
                let hits = hunt_cycle(&ix, budget).await;
                if !hits.is_empty() {
                    println!("hunt: found MCP at {} undeclared domain(s)", hits.len());
                }
            }
        });
    }
    // flush
    {
        let ix = index.clone();
        tokio::spawn(async move {
            let every = envn("MCPSCAN_FLUSH_SECS", 45);
            loop {
                tokio::time::sleep(std::time::Duration::from_secs(every)).await;
                ix.flush().await;
            }
        });
    }
}
