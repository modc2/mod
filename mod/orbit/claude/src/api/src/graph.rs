//! Graph views over the change substrate.
//!
//! Two read-only endpoints, both derived from data that already exists — the
//! per-module version logs (`~/.mod/claude/versions/*.json`), the snapshot
//! manifests in the blob store, and each module's config.json. Nothing new is
//! persisted here; this is a lens, not a store.
//!
//! - `GET /modules/:name/ontology?cid=…[&base=…]` — what one version CHANGED,
//!   as a diff of two snapshot manifests. The console draws it as a tree
//!   (directories fanning out to the files they hold), so a version reads as
//!   the shape of its change rather than a line in a list.
//!
//! - `GET /graph/world` — the whole fleet as one graph: every module a node
//!   carrying its change history, every edge either a declared dependency or
//!   a fork. Fork lineage is recovered two ways: the message a fork record
//!   writes ("forked from X@cid"), and — more reliably — shared snapshot CIDs.
//!   Two modules whose histories contain the same tree CID are the same code;
//!   whoever had it first is the parent. Content addressing means lineage
//!   survives renames, copies and messages nobody wrote.

use crate::merge::{diff_manifests, load_manifest};
use crate::snapshots::{default_store, is_module_dir, read_versions, versions_dir, VersionRecord};
use axum::{
    extract::{Path, Query},
    http::StatusCode,
    response::IntoResponse,
    Json,
};
use serde::Deserialize;
use serde_json::json;
use std::collections::{BTreeMap, HashMap, HashSet};
use std::sync::{Mutex, OnceLock};

/// A first snapshot legitimately "adds" every file in the tree. Cap what we
/// ship so the ontology of a 6k-file module is still a response, not a stall —
/// the summary counts stay exact either way.
const MAX_CHANGES: usize = 4000;

// ── Change ontology (one version) ────────────────────────────────────

#[derive(Deserialize)]
pub struct OntologyQuery {
    pub cid: String,
    /// Diff against this tree instead of the version's own parent.
    pub base: Option<String>,
}

pub async fn module_ontology(
    Path(name): Path<String>,
    Query(q): Query<OntologyQuery>,
) -> impl IntoResponse {
    let history = read_versions(&name);
    let idx = history.iter().position(|v| v.cid == q.cid);
    let record = idx.map(|i| history[i].clone());

    // Base precedence: explicit query → the record's own parent → whatever
    // version preceded it in the log. A first snapshot has no base at all,
    // and every file in it counts as added.
    let base_cid: Option<String> = q.base.clone().or_else(|| {
        record.as_ref().and_then(|r| r.parent.clone()).or_else(|| {
            idx.and_then(|i| i.checked_sub(1)).map(|p| history[p].cid.clone())
        })
    });

    let store = default_store();
    let head = match load_manifest(&store, &q.cid) {
        Ok(m) => m,
        Err(e) => {
            return (StatusCode::BAD_REQUEST, Json(json!({ "error": e }))).into_response();
        }
    };
    // A base whose blob is gone (pruned, or minted on another orbit) degrades
    // to "no base" rather than failing the whole view.
    let (base, base_missing) = match &base_cid {
        Some(c) => match load_manifest(&store, c) {
            Ok(m) => (Some(m), false),
            Err(_) => (None, true),
        },
        None => (None, false),
    };

    let empty = crate::snapshots::Manifest { version: 1, files: Vec::new() };
    let changes = diff_manifests(base.as_ref().unwrap_or(&empty), &head);

    // Head sizes, so the console can weight a node by how much moved.
    let head_size: HashMap<&str, u64> =
        head.files.iter().map(|f| (f.path.as_str(), f.size)).collect();
    let base_size: HashMap<&str, u64> = base
        .as_ref()
        .map(|m| m.files.iter().map(|f| (f.path.as_str(), f.size)).collect())
        .unwrap_or_default();

    let mut added = 0usize;
    let mut modified = 0usize;
    let mut deleted = 0usize;
    for c in &changes {
        match c.status.as_str() {
            "added" => added += 1,
            "modified" => modified += 1,
            _ => deleted += 1,
        }
    }

    let items: Vec<serde_json::Value> = changes
        .iter()
        .take(MAX_CHANGES)
        .map(|c| {
            let size = head_size
                .get(c.path.as_str())
                .or_else(|| base_size.get(c.path.as_str()))
                .copied()
                .unwrap_or(0);
            json!({
                "path": c.path,
                "status": c.status,
                "size": size,
                "base_cid": c.base_cid,
                "head_cid": c.head_cid,
            })
        })
        .collect();

    Json(json!({
        "module": name,
        "cid": q.cid,
        "base": base_cid,
        "base_missing": base_missing,
        "message": record.as_ref().map(|r| r.message.clone()),
        "action": record.as_ref().and_then(|r| r.action.clone()),
        "author": record.as_ref().map(|r| r.author.clone()),
        "timestamp": record.as_ref().map(|r| r.timestamp),
        "file_count": head.files.len(),
        "summary": {
            "added": added,
            "modified": modified,
            "deleted": deleted,
            "total": changes.len(),
        },
        "truncated": changes.len() > MAX_CHANGES,
        "changes": items,
    }))
    .into_response()
}

// ── World graph (the whole fleet) ────────────────────────────────────

/// Every module that has a version log, keyed by the name the log is filed
/// under (`portal__0xab__foo.json` → `portal/0xab/foo`).
fn logged_modules() -> Vec<(String, Vec<VersionRecord>)> {
    let Ok(rd) = std::fs::read_dir(versions_dir()) else {
        return Vec::new();
    };
    let mut out = Vec::new();
    for entry in rd.flatten() {
        let file = entry.file_name().to_string_lossy().to_string();
        let Some(stem) = file.strip_suffix(".json") else { continue };
        let name = stem.replace("__", "/");
        let records = read_versions(&name);
        if !records.is_empty() {
            out.push((name, records));
        }
    }
    out
}

/// Declared dependencies. `deps` is this console's field; `dependencies` is
/// what the mod protocol's own configs use — both mean the same edge, and a
/// graph that honoured only one of them drew a nearly empty fleet.
pub fn declared_deps(config: &serde_json::Value) -> Vec<String> {
    for key in ["deps", "dependencies"] {
        if let Some(arr) = config.get(key).and_then(|v| v.as_array()) {
            let list: Vec<String> = arr
                .iter()
                .filter_map(|v| v.as_str().map(String::from))
                .collect();
            if !list.is_empty() {
                return list;
            }
        }
    }
    Vec::new()
}

/// A module's config.json, checked in the three places one is allowed to live
/// (`*/src` IS the module, so a config there names the parent).
fn read_config(dir: &std::path::Path) -> Option<serde_json::Value> {
    for p in [
        dir.join("config.json"),
        dir.join("src").join("config.json"),
    ] {
        if let Ok(text) = std::fs::read_to_string(&p) {
            if let Ok(v) = serde_json::from_str::<serde_json::Value>(&text) {
                return Some(v);
            }
        }
    }
    None
}

struct Scanned {
    category: String,
    path: String,
    description: Option<String>,
    version: Option<String>,
    deps: Vec<String>,
}

/// Light scan of the orbit/ and core/ trees — config.json only. Deliberately
/// no mtime walk: the world graph is drawn from history, and walking every
/// module's tree would turn a click into a multi-second stall.
fn scan_tree() -> BTreeMap<String, Scanned> {
    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string());
    let mut out = BTreeMap::new();
    for category in ["orbit", "core"] {
        let root = std::path::PathBuf::from(format!("{home}/mod/mod/{category}"));
        let Ok(rd) = std::fs::read_dir(&root) else { continue };
        for entry in rd.flatten() {
            let path = entry.path();
            if !path.is_dir() || !is_module_dir(&path) {
                continue;
            }
            let name = entry.file_name().to_string_lossy().to_string();
            if name.starts_with('.') || name.starts_with('_') {
                continue;
            }
            let config = read_config(&path);
            out.insert(
                name,
                Scanned {
                    category: category.to_string(),
                    path: path.to_string_lossy().to_string(),
                    description: config
                        .as_ref()
                        .and_then(|c| c.get("description"))
                        .and_then(|v| v.as_str())
                        .map(String::from),
                    version: config
                        .as_ref()
                        .and_then(|c| c.get("version"))
                        .and_then(|v| v.as_str())
                        .map(String::from),
                    deps: config.as_ref().map(declared_deps).unwrap_or_default(),
                },
            );
        }
    }
    out
}

/// "forked from agent@04e6c8f7" / "copied from agent" → "agent".
fn parent_from_message(message: &str) -> Option<String> {
    let rest = message
        .strip_prefix("forked from ")
        .or_else(|| message.strip_prefix("copied from "))?;
    let name = rest.split('@').next()?.trim();
    (!name.is_empty()).then(|| name.to_string())
}

/// Below this many files a tree is too small for shared-blob overlap to mean
/// anything — three identical scaffold files are a coincidence, not descent.
const MIN_LINEAGE_FILES: usize = 8;
/// Share this fraction of your first tree's file blobs with someone who had
/// them first, and you are their fork.
const LINEAGE_COVERAGE: f64 = 0.5;

/// Recover fork lineage from the blob store for modules that never said where
/// they came from. A fork's first snapshot is, by definition, someone else's
/// tree with a few files changed — so the file CIDs in it are overwhelmingly
/// CIDs another module already had. Whoever held them first is the parent.
///
/// This is what makes the world graph show the forks that actually happened
/// (claude → build, dev, codex) rather than only the ones performed through
/// the /fork endpoint, which is the minority of them.
fn inferred_lineage(logs: &[(String, Vec<VersionRecord>)]) -> HashMap<String, (String, f64)> {
    let store = default_store();
    // file CID → earliest (module, timestamp) that held it. Built from every
    // manifest we can load; a parent is only credited for blobs it had first.
    let mut holder: HashMap<String, Vec<(usize, u64)>> = HashMap::new();
    let mut first_manifest: Vec<Option<Vec<String>>> = Vec::with_capacity(logs.len());
    for (i, (_, records)) in logs.iter().enumerate() {
        let mut own_first: Option<Vec<String>> = None;
        for r in records.iter() {
            // A pruned manifest just drops out — `own_first` then means the
            // earliest tree we can still read, which is the best available
            // stand-in for where the module started.
            let Ok(m) = load_manifest(&store, &r.cid) else { continue };
            let cids: Vec<String> = m.files.iter().map(|f| f.cid.clone()).collect();
            for cid in &cids {
                let slot = holder.entry(cid.clone()).or_default();
                match slot.iter_mut().find(|(mi, _)| *mi == i) {
                    Some(e) => e.1 = e.1.min(r.timestamp),
                    None => slot.push((i, r.timestamp)),
                }
            }
            if own_first.is_none() {
                own_first = Some(cids);
            }
        }
        first_manifest.push(own_first);
    }

    let mut out = HashMap::new();
    for (i, (name, records)) in logs.iter().enumerate() {
        let Some(files) = first_manifest[i].as_ref() else { continue };
        if files.len() < MIN_LINEAGE_FILES {
            continue;
        }
        let born = records.first().map(|r| r.timestamp).unwrap_or(0);
        let mut shared: HashMap<usize, usize> = HashMap::new();
        for cid in files {
            for (mi, ts) in holder.get(cid).into_iter().flatten() {
                if *mi != i && *ts <= born {
                    *shared.entry(*mi).or_default() += 1;
                }
            }
        }
        if let Some((&best, &count)) = shared.iter().max_by_key(|(_, c)| **c) {
            let coverage = count as f64 / files.len() as f64;
            if coverage >= LINEAGE_COVERAGE {
                out.insert(name.clone(), (logs[best].0.clone(), coverage));
            }
        }
    }
    out
}

/// Reading every manifest in the store to recover lineage costs real I/O, and
/// the hub repolls the graph on a timer — serve a recent build instead.
const CACHE_TTL_SECS: u64 = 30;

fn cache() -> &'static Mutex<Option<(u64, serde_json::Value)>> {
    static C: OnceLock<Mutex<Option<(u64, serde_json::Value)>>> = OnceLock::new();
    C.get_or_init(|| Mutex::new(None))
}

pub async fn world_graph() -> impl IntoResponse {
    let now = unix_now();
    if let Some((built, value)) = cache().lock().unwrap_or_else(|e| e.into_inner()).as_ref() {
        if now.saturating_sub(*built) < CACHE_TTL_SECS {
            return Json(value.clone()).into_response();
        }
    }
    let value = build_world(now);
    *cache().lock().unwrap_or_else(|e| e.into_inner()) = Some((now, value.clone()));
    Json(value).into_response()
}

fn unix_now() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

fn build_world(now: u64) -> serde_json::Value {
    let scanned = scan_tree();
    let logs = logged_modules();
    let history: HashMap<&str, &Vec<VersionRecord>> =
        logs.iter().map(|(n, r)| (n.as_str(), r)).collect();

    // Which module first held a given tree CID. A later module carrying the
    // same CID didn't coincidentally match — it IS that code.
    let mut first_seen: HashMap<&str, (&str, u64)> = HashMap::new();
    for (name, records) in &logs {
        for r in records.iter() {
            let e = first_seen
                .entry(r.cid.as_str())
                .or_insert((name.as_str(), r.timestamp));
            if r.timestamp < e.1 {
                *e = (name.as_str(), r.timestamp);
            }
        }
    }

    // Nodes: everything on disk, plus any module that has history but no
    // directory any more (deleted, renamed, or a portal fork) — dropping
    // those would silently cut branches out of the lineage.
    let mut names: HashSet<String> = scanned.keys().cloned().collect();
    for (name, _) in &logs {
        names.insert(name.clone());
    }

    let mut nodes = Vec::new();
    for name in &names {
        let records = history.get(name.as_str()).copied();
        let meta = scanned.get(name);
        let mut authors: HashSet<&str> = HashSet::new();
        let mut edits = 0usize;
        let mut snaps = 0usize;
        let mut restores = 0usize;
        if let Some(rs) = records {
            for r in rs.iter() {
                if !r.author.is_empty() {
                    authors.insert(r.author.as_str());
                }
                match r.action.as_deref().unwrap_or("snapshot") {
                    "edit" => edits += 1,
                    "restore" => restores += 1,
                    _ => snaps += 1,
                }
            }
        }
        nodes.push(json!({
            "name": name,
            "category": meta.map(|m| m.category.clone())
                .unwrap_or_else(|| if name.starts_with("portal/") { "portal".into() } else { "gone".into() }),
            "path": meta.map(|m| m.path.clone()),
            "description": meta.and_then(|m| m.description.clone()),
            "version": meta.and_then(|m| m.version.clone()),
            "exists": meta.is_some(),
            "changes": records.map(|r| r.len()).unwrap_or(0),
            "edits": edits,
            "snapshots": snaps,
            "restores": restores,
            "authors": authors.len(),
            "first_change": records.and_then(|r| r.first().map(|v| v.timestamp)),
            "last_change": records.and_then(|r| r.last().map(|v| v.timestamp)),
            "head_cid": records.and_then(|r| r.last().map(|v| v.cid.clone())),
        }));
    }
    nodes.sort_by(|a, b| a["name"].as_str().unwrap_or("").cmp(b["name"].as_str().unwrap_or("")));

    // Edges. Dependencies point child → dependency; forks point child →
    // origin. Both are deduped, and an edge is only drawn between two nodes
    // the graph actually holds.
    let mut seen_edges: HashSet<(String, String, &str)> = HashSet::new();
    let mut edges = Vec::new();
    let mut push = |from: &str, to: &str, kind: &'static str, via: &'static str, weight: Option<f64>| {
        if from == to || !names.contains(from) || !names.contains(to) {
            return;
        }
        if !seen_edges.insert((from.to_string(), to.to_string(), kind)) {
            return;
        }
        edges.push(json!({ "from": from, "to": to, "kind": kind, "via": via, "weight": weight }));
    };

    for (name, meta) in &scanned {
        for dep in &meta.deps {
            push(name, dep, "dep", "config", None);
        }
    }

    for (name, records) in &logs {
        // Stated lineage: what the fork wrote about itself.
        for r in records.iter() {
            if matches!(r.action.as_deref(), Some("fork") | Some("copy")) {
                if let Some(parent) = parent_from_message(&r.message) {
                    push(name, &parent, "fork", "record", Some(1.0));
                }
            }
        }
        // Exact lineage: this module's first tree CID was already somebody
        // else's tree. Only the first record counts — later shared CIDs are
        // merges and restores, not birth.
        if let Some(first) = records.first() {
            if let Some((origin, ts)) = first_seen.get(first.cid.as_str()) {
                if *origin != name.as_str() && *ts <= first.timestamp {
                    push(name, origin, "fork", "tree-cid", Some(1.0));
                }
            }
        }
    }

    // Recovered lineage: everything forked before anyone wrote it down.
    for (child, (parent, coverage)) in inferred_lineage(&logs) {
        push(&child, &parent, "fork", "blob-overlap", Some(coverage));
    }

    let dep_count = edges.iter().filter(|e| e["kind"] == "dep").count();
    json!({
        "generated_at": now,
        "nodes": nodes,
        "edges": edges,
        "stats": {
            "modules": names.len(),
            "with_history": logs.len(),
            "changes": logs.iter().map(|(_, r)| r.len()).sum::<usize>(),
            "dep_edges": dep_count,
            "fork_edges": edges.len() - dep_count,
        },
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_fork_lineage_from_message() {
        assert_eq!(parent_from_message("forked from agent@04e6c8f7"), Some("agent".into()));
        assert_eq!(parent_from_message("working snapshot"), None);
    }

    #[test]
    fn dependencies_alias_deps() {
        let a = json!({ "deps": ["chain", "store"] });
        let b = json!({ "dependencies": ["store"] });
        let c = json!({ "deps": [], "dependencies": ["store"] });
        assert_eq!(declared_deps(&a), vec!["chain", "store"]);
        assert_eq!(declared_deps(&b), vec!["store"]);
        // An empty `deps` must not shadow a populated `dependencies`.
        assert_eq!(declared_deps(&c), vec!["store"]);
    }
}
