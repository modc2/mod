//! The index itself: every MCP server this box has ever heard of, in memory,
//! flushed to disk when it changes.
//!
//! Two ids can describe one server (the official registry calls it
//! `io.github.foo/bar`, GitHub calls it `foo/bar`), so the endpoint URL is a
//! second key: an incoming row whose URL is already known merges into the row
//! that owns it instead of forking a duplicate.

use crate::store::{self, canon_url, Entry, SourceReport};
use crate::upstream::ProbeOut;
use serde_json::{json, Value};
use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;
use tokio::sync::RwLock;

/// Rolling telemetry for the scraper — what the console's ticker reads.
#[derive(Default)]
pub struct ScanStats {
    pub probes: u64,
    pub batches: u64,
    pub last_batch_at: u64,
    pub last_batch_size: usize,
    pub last_crawl_at: u64,
    pub last_crawl_ms: u64,
    pub hunts: u64,
    pub hunt_hits: u64,
    /// Newest first, capped — the "just found" feed.
    pub recent: Vec<Value>,
}

impl ScanStats {
    pub fn note(&mut self, v: Value) {
        self.recent.insert(0, v);
        self.recent.truncate(40);
    }
}

pub struct Index {
    pub entries: RwLock<HashMap<String, Entry>>,
    /// canonical url → entry id
    pub by_url: RwLock<HashMap<String, String>>,
    pub reports: RwLock<Vec<SourceReport>>,
    pub scan: RwLock<ScanStats>,
    pub started_at: u64,
    dirty: AtomicBool,
    pub crawling: AtomicBool,
    pub saves: AtomicU64,
}

impl Index {
    pub fn load() -> Arc<Self> {
        let file = store::load_catalog();
        let mut entries = HashMap::new();
        let mut by_url = HashMap::new();
        for mut e in file.entries {
            e.rebuild_hay();
            if e.probeable() {
                by_url.insert(canon_url(&e.url), e.id.clone());
            }
            entries.insert(e.id.clone(), e);
        }
        Arc::new(Self {
            entries: RwLock::new(entries),
            by_url: RwLock::new(by_url),
            reports: RwLock::new(store::load_reports()),
            scan: RwLock::new(ScanStats::default()),
            started_at: store::now(),
            dirty: AtomicBool::new(false),
            crawling: AtomicBool::new(false),
            saves: AtomicU64::new(0),
        })
    }

    pub fn touch(&self) {
        self.dirty.store(true, Ordering::Relaxed);
    }

    /// Write the catalogue out if anything changed since the last write. The
    /// file is megabytes, so this is called on a timer, not per row.
    pub async fn flush(&self) -> bool {
        if !self.dirty.swap(false, Ordering::Relaxed) {
            return false;
        }
        let entries: Vec<Entry> = self.entries.read().await.values().cloned().collect();
        store::save_catalog(&store::CatalogFile { entries, saved_at: store::now() });
        store::save_reports(&self.reports.read().await.clone());
        self.saves.fetch_add(1, Ordering::Relaxed);
        true
    }

    pub async fn len(&self) -> usize {
        self.entries.read().await.len()
    }

    pub async fn get(&self, id: &str) -> Option<Entry> {
        self.entries.read().await.get(id).cloned()
    }

    /// Fold a directory's listing into the index. Returns (new, updated).
    pub async fn merge(&self, incoming: Vec<Entry>) -> (u64, u64) {
        let mut entries = self.entries.write().await;
        let mut by_url = self.by_url.write().await;
        let (mut fresh, mut updated) = (0u64, 0u64);
        let ts = store::now();
        for mut inc in incoming {
            // An entry whose endpoint is already indexed is the same server.
            let key = if inc.probeable() {
                by_url.get(&canon_url(&inc.url)).cloned().unwrap_or_else(|| inc.id.clone())
            } else {
                inc.id.clone()
            };
            match entries.get_mut(&key) {
                Some(old) => {
                    let mut changed = false;
                    for src in inc.sources.drain(..) {
                        if !old.sources.contains(&src) {
                            old.sources.push(src);
                            changed = true;
                        }
                    }
                    // A directory that finally publishes an endpoint for a
                    // server we only knew as a package resets it to unprobed.
                    if old.url.is_empty() && inc.probeable() {
                        old.url = inc.url.clone();
                        old.transport = inc.transport.clone();
                        old.status = String::new();
                        old.checked_at = 0;
                        old.fails = 0;
                        by_url.insert(canon_url(&old.url), old.id.clone());
                        changed = true;
                    }
                    for (field, val) in [
                        (&mut old.title, &inc.title),
                        (&mut old.description, &inc.description),
                        (&mut old.homepage, &inc.homepage),
                        (&mut old.repository, &inc.repository),
                        (&mut old.auth_hint, &inc.auth_hint),
                    ] {
                        if field.is_empty() && !val.is_empty() {
                            *field = val.clone();
                            changed = true;
                        }
                    }
                    for p in inc.packages.drain(..) {
                        if !old.packages.contains(&p) {
                            old.packages.push(p);
                            changed = true;
                        }
                    }
                    old.last_seen = ts;
                    if changed {
                        old.rebuild_hay();
                        updated += 1;
                    }
                }
                None => {
                    inc.id = key.clone();
                    inc.first_seen = ts;
                    inc.last_seen = ts;
                    inc.rebuild_hay();
                    if inc.probeable() {
                        by_url.insert(canon_url(&inc.url), inc.id.clone());
                    }
                    entries.insert(key, inc);
                    fresh += 1;
                }
            }
        }
        drop(entries);
        drop(by_url);
        self.touch();
        (fresh, updated)
    }

    /// Record what a probe found against one entry.
    pub async fn apply_probe(&self, id: &str, p: &ProbeOut) {
        let mut entries = self.entries.write().await;
        let Some(e) = entries.get_mut(id) else { return };
        let was = e.status.clone();
        e.status = p.status.clone();
        e.checked_at = p.checked_at;
        e.latency_ms = p.latency_ms;
        e.error = p.error.clone();
        e.attempts = e.attempts.saturating_add(1);
        e.fails = if p.status == "live" { 0 } else { e.fails.saturating_add(1) };
        if p.status == "live" {
            e.protocol_version = p.protocol_version.clone();
            e.server_info = p.server_info.clone();
            e.tools = p.tools.clone();
        } else if !p.protocol_version.is_empty() {
            e.protocol_version = p.protocol_version.clone();
        }
        e.rebuild_hay();
        let row = json!({
            "at": p.checked_at, "id": e.id, "name": e.name, "url": e.url,
            "status": e.status, "tools": e.tools.len(), "latency_ms": e.latency_ms,
            "changed": was != e.status,
        });
        drop(entries);
        if p.status == "live" || was != p.status {
            self.scan.write().await.note(row);
        }
        self.touch();
    }

    /// Search. Empty `q` means "everything, best first".
    pub async fn search(&self, p: &SearchParams) -> (usize, Vec<Value>) {
        let q = p.q.trim().to_lowercase();
        let terms: Vec<&str> = q.split_whitespace().collect();
        let entries = self.entries.read().await;
        let mut hits: Vec<(i64, &Entry)> = Vec::new();
        for e in entries.values() {
            if !p.status.is_empty() && status_of(e) != p.status {
                continue;
            }
            if !p.source.is_empty() && !e.sources.iter().any(|s| s == &p.source) {
                continue;
            }
            if p.live_only && status_of(e) != "live" {
                continue;
            }
            if p.with_url && !e.probeable() {
                continue;
            }
            if !terms.is_empty() && !terms.iter().all(|t| e.hay.contains(t)) {
                continue;
            }
            hits.push((score(e, &terms), e));
        }
        let total = hits.len();
        match p.sort.as_str() {
            "tools" => hits.sort_by(|a, b| b.1.tools.len().cmp(&a.1.tools.len())),
            "recent" => hits.sort_by(|a, b| b.1.first_seen.cmp(&a.1.first_seen)),
            "fast" => hits.sort_by(|a, b| {
                rank_status(b.1).cmp(&rank_status(a.1)).then(a.1.latency_ms.cmp(&b.1.latency_ms))
            }),
            "name" => hits.sort_by(|a, b| a.1.name.cmp(&b.1.name)),
            _ => hits.sort_by(|a, b| b.0.cmp(&a.0).then(a.1.name.cmp(&b.1.name))),
        }
        let rows = hits
            .into_iter()
            .skip(p.offset)
            .take(p.limit)
            .map(|(_, e)| e.row(p.with_tools))
            .collect();
        (total, rows)
    }

    pub async fn stats(&self) -> Value {
        let entries = self.entries.read().await;
        let mut by_status: HashMap<String, u64> = HashMap::new();
        let mut by_source: HashMap<String, u64> = HashMap::new();
        let (mut endpoints, mut tools, mut hunted, mut hunt_found) = (0u64, 0u64, 0u64, 0u64);
        for e in entries.values() {
            *by_status.entry(status_of(e).to_string()).or_default() += 1;
            for s in &e.sources {
                *by_source.entry(s.clone()).or_default() += 1;
            }
            if e.probeable() {
                endpoints += 1;
            }
            if e.hunted_at > 0 {
                hunted += 1;
            }
            // Found by knocking rather than by any directory — a persisted
            // count, so a restart doesn't reset the hunter's score to zero.
            if e.sources.iter().any(|s| s == "hunt") {
                hunt_found += 1;
            }
            tools += e.tools.len() as u64;
        }
        let total = entries.len();
        drop(entries);
        let scan = self.scan.read().await;
        // A restart doesn't undo the last crawl — fall back to what the source
        // reports remember, so the console doesn't claim "never".
        let last_crawl = if scan.last_crawl_at > 0 {
            scan.last_crawl_at
        } else {
            self.reports.read().await.iter().map(|r| r.ran_at).max().unwrap_or(0)
        };
        json!({
            "servers": total,
            "endpoints": endpoints,
            "tools": tools,
            "by_status": by_status,
            "by_source": by_source,
            "domains_hunted": hunted,
            "scraper": {
                "probes": scan.probes,
                "batches": scan.batches,
                "last_batch_at": scan.last_batch_at,
                "last_batch_size": scan.last_batch_size,
                "last_crawl_at": last_crawl,
                "last_crawl_ms": scan.last_crawl_ms,
                "crawling": self.crawling.load(Ordering::Relaxed),
                "hunts": scan.hunts,
                "hunt_hits": hunt_found.max(scan.hunt_hits),
            },
            "started_at": self.started_at,
            "saves": self.saves.load(Ordering::Relaxed),
        })
    }

    pub async fn recent(&self, n: usize) -> Vec<Value> {
        self.scan.read().await.recent.iter().take(n).cloned().collect()
    }

    pub async fn set_report(&self, r: SourceReport) {
        let mut reports = self.reports.write().await;
        reports.retain(|x| x.source != r.source);
        reports.push(r);
        reports.sort_by(|a, b| a.source.cmp(&b.source));
        self.touch();
    }
}

pub fn status_of(e: &Entry) -> &str {
    if e.status.is_empty() {
        "unknown"
    } else {
        &e.status
    }
}

fn rank_status(e: &Entry) -> i64 {
    match status_of(e) {
        "live" => 4,
        "auth" => 3,
        "unknown" => 2,
        "error" => 1,
        _ => 0,
    }
}

/// Relevance: where the words matched matters more than how often.
fn score(e: &Entry, terms: &[&str]) -> i64 {
    let mut s = rank_status(e) * 25 + (e.tools.len() as i64).min(40);
    if e.probeable() {
        s += 15;
    }
    s += (e.sources.len() as i64 - 1) * 10; // listed in more than one directory
    for t in terms {
        if e.name.to_lowercase().contains(t) {
            s += 120;
        }
        if e.title.to_lowercase().contains(t) {
            s += 60;
        }
        if e.tools.iter().any(|x| x.name.to_lowercase().contains(t)) {
            s += 80;
        }
        if e.description.to_lowercase().contains(t) {
            s += 25;
        }
        if e.url.to_lowercase().contains(t) {
            s += 15;
        }
    }
    s
}

#[derive(Debug, Clone)]
pub struct SearchParams {
    pub q: String,
    pub status: String,
    pub source: String,
    pub sort: String,
    pub limit: usize,
    pub offset: usize,
    pub live_only: bool,
    pub with_url: bool,
    pub with_tools: bool,
}

impl Default for SearchParams {
    fn default() -> Self {
        Self {
            q: String::new(),
            status: String::new(),
            source: String::new(),
            sort: "relevance".into(),
            limit: 50,
            offset: 0,
            live_only: false,
            with_url: false,
            with_tools: false,
        }
    }
}

impl SearchParams {
    /// Build from a REST query string map.
    pub fn from_query(q: &HashMap<String, String>) -> Self {
        let flag = |k: &str| matches!(q.get(k).map(String::as_str), Some("1") | Some("true"));
        Self {
            q: q.get("q").cloned().unwrap_or_default(),
            status: q.get("status").cloned().unwrap_or_default(),
            source: q.get("source").cloned().unwrap_or_default(),
            sort: q.get("sort").cloned().unwrap_or_else(|| "relevance".into()),
            limit: q.get("limit").and_then(|v| v.parse().ok()).unwrap_or(50).clamp(1, 500),
            offset: q.get("offset").and_then(|v| v.parse().ok()).unwrap_or(0),
            live_only: flag("live"),
            with_url: flag("endpoint") || flag("has_url"),
            with_tools: flag("tools"),
        }
    }
}
