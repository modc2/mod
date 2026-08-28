//! Autosnap — background CID minting for the module hub.
//!
//! Once a minute, scan the orbit/ and core/ trees and give every module that
//! has no CID in the mod-protocol registry (~/.mod/api/registry.json) one,
//! via the same snapshot + api/reg flow as POST /modules/:name/snapshot.
//! A fresh api/reg call takes ~15-20s, so each tick snaps at most a few
//! modules — a large backlog drains over successive ticks. Failures back
//! off per module (doubling, capped) so one broken module can't pin the
//! queue. Disable with BUILD_FORK_AUTOSNAP=0.

use std::collections::{HashMap, HashSet};
use std::path::PathBuf;
use std::sync::{Mutex, OnceLock};

use crate::snapshots::{
    append_version, default_store, dir_bytes, read_versions, snapshot_dir, VersionRecord,
};

const TICK_SECS: u64 = 60;
/// Max modules registered per tick — api/reg is ~15-20s per fresh snapshot.
const PER_TICK: usize = 3;
/// Snapshot reads every file body (node_modules/target/.next excluded); skip
/// anything still larger than this rather than churn the store.
const MAX_TREE_BYTES: u64 = 256 * 1024 * 1024;
/// First retry delay after a failure; doubles per consecutive failure.
const BACKOFF_BASE_SECS: u64 = 600;
const BACKOFF_MAX_SECS: u64 = 6 * 3600;

#[derive(Clone, Default)]
struct Status {
    last_tick: u64,
    pending: usize,
    snapped: u64,
    failed: u64,
    recent: Vec<serde_json::Value>,
}

fn status() -> &'static Mutex<Status> {
    static S: OnceLock<Mutex<Status>> = OnceLock::new();
    S.get_or_init(|| Mutex::new(Status::default()))
}

/// module name → (consecutive failures, unix ts of next allowed attempt)
fn backoff() -> &'static Mutex<HashMap<String, (u32, u64)>> {
    static B: OnceLock<Mutex<HashMap<String, (u32, u64)>>> = OnceLock::new();
    B.get_or_init(|| Mutex::new(HashMap::new()))
}

fn now_ts() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

fn enabled() -> bool {
    std::env::var("BUILD_FORK_AUTOSNAP").unwrap_or_default() != "0"
}

pub fn spawn() {
    if !enabled() {
        tracing::info!("autosnap disabled (BUILD_FORK_AUTOSNAP=0)");
        return;
    }
    tokio::spawn(async move {
        let mut iv = tokio::time::interval(std::time::Duration::from_secs(TICK_SECS));
        iv.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
        loop {
            iv.tick().await;
            tick().await;
        }
    });
}

/// Every module dir under the scan trees: (module name, dir path).
/// Same conventions as /modules — skip dot/underscore dirs, and count a dir
/// as a module if it carries a config.json OR a mod.py (src/mod.py counts:
/// */src IS */) OR only holds nested mods (e.g. archive/). Requiring a
/// config.json here used to leave those last two kinds permanently CID-less
/// even though the hub lists them. Name comes from config.json when present,
/// else the dir name. The tree roots themselves (orbit/, core/, mod) are
/// left to manual snapshots: they span every module and are too big to
/// auto-churn.
fn scan_modules() -> Vec<(String, PathBuf)> {
    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string());
    let mut out = Vec::new();
    for tree in ["orbit", "core"] {
        let dir = PathBuf::from(&home).join("mod").join("mod").join(tree);
        let Ok(rd) = std::fs::read_dir(&dir) else { continue };
        for entry in rd.flatten() {
            let path = entry.path();
            if !path.is_dir() {
                continue;
            }
            let dir_name = entry.file_name().to_string_lossy().to_string();
            if dir_name.starts_with('.') || dir_name.starts_with('_') {
                continue;
            }
            let has_config = path.join("config.json").is_file();
            let has_mod_py =
                path.join("mod.py").is_file() || path.join("src").join("mod.py").is_file();
            let has_nested_mod = || {
                std::fs::read_dir(&path)
                    .map(|rd| {
                        rd.flatten().any(|e| {
                            let p = e.path();
                            let n = e.file_name().to_string_lossy().to_string();
                            p.is_dir()
                                && !n.starts_with('.')
                                && !n.starts_with('_')
                                && (p.join("config.json").is_file() || p.join("mod.py").is_file())
                        })
                    })
                    .unwrap_or(false)
            };
            if !has_config && !has_mod_py && !has_nested_mod() {
                continue;
            }
            let name = std::fs::read_to_string(path.join("config.json"))
                .ok()
                .and_then(|raw| serde_json::from_str::<serde_json::Value>(&raw).ok())
                .and_then(|c| c.get("name").and_then(|v| v.as_str()).map(String::from))
                .unwrap_or_else(|| dir_name.clone());
            out.push((name, path));
        }
    }
    out
}

/// Lowercased module keys that already have a CID under ANY owner in
/// ~/.mod/api/registry.json (same file + both shapes /modules reads).
fn registered_names() -> HashSet<String> {
    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string());
    let raw = std::fs::read_to_string(format!("{home}/.mod/api/registry.json")).unwrap_or_default();
    let Ok(data) = serde_json::from_str::<serde_json::Value>(&raw) else {
        return HashSet::new();
    };
    let map = data
        .get("data")
        .and_then(|d| d.as_object())
        .or_else(|| data.as_object());
    let mut out = HashSet::new();
    if let Some(map) = map {
        for owner_mods in map.values() {
            if let Some(mods) = owner_mods.as_object() {
                out.extend(mods.keys().map(|k| k.to_lowercase()));
            }
        }
    }
    out
}

async fn tick() {
    let registered = registered_names();
    let now = now_ts();
    let mut queue: Vec<(String, PathBuf)> = scan_modules()
        .into_iter()
        .filter(|(name, path)| {
            let dir = path
                .file_name()
                .map(|n| n.to_string_lossy().to_lowercase())
                .unwrap_or_default();
            !registered.contains(&name.to_lowercase()) && !registered.contains(&dir)
        })
        // Private modules never auto-register — the api/reg push would
        // publish the plaintext tree (mod_protocol_register also refuses,
        // but skipping here keeps them out of the pending queue entirely).
        .filter(|(name, _)| !crate::privacy::is_private(name))
        .collect();
    queue.sort_by(|a, b| a.0.cmp(&b.0));
    let eligible: Vec<(String, PathBuf)> = {
        let b = backoff().lock().unwrap_or_else(|e| e.into_inner());
        queue
            .into_iter()
            .filter(|(name, _)| b.get(name).map(|&(_, next)| next <= now).unwrap_or(true))
            .collect()
    };
    {
        let mut s = status().lock().unwrap_or_else(|e| e.into_inner());
        s.last_tick = now;
        s.pending = eligible.len();
    }
    for (name, root) in eligible.into_iter().take(PER_TICK) {
        let outcome = snap_one(&name, &root).await;
        let mut s = status().lock().unwrap_or_else(|e| e.into_inner());
        let mut b = backoff().lock().unwrap_or_else(|e| e.into_inner());
        match outcome {
            Ok(cid) => {
                tracing::info!("autosnap: {name} → {cid}");
                b.remove(&name);
                s.snapped += 1;
                s.pending = s.pending.saturating_sub(1);
                s.recent.push(serde_json::json!({ "module": name, "cid": cid, "ts": now_ts() }));
            }
            Err(e) => {
                tracing::warn!("autosnap: {name} failed: {e}");
                let fails = b.get(&name).map(|&(f, _)| f).unwrap_or(0) + 1;
                let delay =
                    (BACKOFF_BASE_SECS << (fails - 1).min(6)).min(BACKOFF_MAX_SECS);
                b.insert(name.clone(), (fails, now_ts() + delay));
                s.failed += 1;
                s.recent.push(serde_json::json!({ "module": name, "error": e, "ts": now_ts() }));
            }
        }
        let excess = s.recent.len().saturating_sub(20);
        if excess > 0 {
            s.recent.drain(..excess);
        }
    }
}

/// One module: local snapshot (versions log + restore point), then the
/// api/reg push — the registry CID is what makes the hub card show a cid.
async fn snap_one(name: &str, root: &PathBuf) -> Result<String, String> {
    let size = dir_bytes(root);
    if size > MAX_TREE_BYTES {
        return Err(format!("tree too large ({size} bytes)"));
    }
    let snap_root = root.clone();
    let (local_cid, _manifest) =
        tokio::task::spawn_blocking(move || snapshot_dir(&snap_root, &default_store()))
            .await
            .map_err(|e| format!("snapshot task: {e}"))??;
    let reg = crate::api::mod_protocol_register(name, "autosnap: initial cid").await?;
    let history = read_versions(name);
    // Idempotent: content unchanged since the last logged version → no new
    // record, so retries never spam the versions log.
    if history.last().map(|v| v.cid.as_str()) != Some(local_cid.as_str()) {
        let record = VersionRecord {
            cid: local_cid.clone(),
            message: "autosnap: initial cid".to_string(),
            author: "autosnap".to_string(),
            timestamp: now_ts(),
            parent: history.last().map(|v| v.cid.clone()),
            registry_cid: reg.cid.clone(),
            registry_prev: reg.prev.clone(),
            action: Some("auto-snapshot".to_string()),
            job_id: None,
            ..Default::default()
        };
        append_version(name, record)?;
    }
    Ok(reg.cid.unwrap_or(local_cid))
}

/// GET /autosnap/status — public, like /modules: how the loop is doing.
pub async fn status_handler() -> axum::Json<serde_json::Value> {
    let s = status().lock().unwrap_or_else(|e| e.into_inner()).clone();
    axum::Json(serde_json::json!({
        "enabled": enabled(),
        "tick_secs": TICK_SECS,
        "per_tick": PER_TICK,
        "last_tick": s.last_tick,
        "pending": s.pending,
        "snapped": s.snapped,
        "failed": s.failed,
        "recent": s.recent,
    }))
}
