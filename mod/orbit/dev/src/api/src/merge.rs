//! CID-native merge requests — fork → propose → review → agentic merge.
//!
//! A merge request is fundamentally a pair of snapshot CIDs in the shared
//! blob store: `base_cid` (the upstream tree the fork started from) and
//! `head_cid` (the contributor's proposed tree). Everything else — title,
//! comments, revisions, status — is review metadata kept as one JSON file
//! per MR under `~/.mod/claude/merge_requests/`. Because both sides are
//! content-addressed, an MR can be opened from a live fork on this orbit
//! OR from raw CIDs minted anywhere the blob store is shared.
//!
//! The merge itself is not a dumb file copy: approving an MR submits an
//! agent job whose prompt is a three-way merge brief (BASE vs HEAD vs the
//! LIVE tree, with the conflict set precomputed from manifests). The agent
//! reconciles diverged files semantically; the existing job-completion
//! auto-snapshot then mints the merged version CID.

use crate::snapshots::{Manifest, Store};
use serde::{Deserialize, Serialize};
use std::path::PathBuf;

// ── Records ──────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MrComment {
    pub author: String,
    pub body: String,
    /// "comment" | "approve" | "request_changes" | "system"
    #[serde(default)]
    pub action: Option<String>,
    pub timestamp: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MrRevision {
    pub head_cid: String,
    #[serde(default)]
    pub message: String,
    pub timestamp: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MergeRequest {
    pub id: String,
    pub module: String,
    pub author: String,
    pub title: String,
    #[serde(default)]
    pub description: String,
    /// Upstream snapshot the fork started from.
    pub base_cid: String,
    /// The proposed tree (latest revision).
    pub head_cid: String,
    #[serde(default)]
    pub revisions: Vec<MrRevision>,
    /// open | changes_requested | approved | merging | merged | merge_failed | closed
    pub status: String,
    #[serde(default)]
    pub comments: Vec<MrComment>,
    pub created_at: u64,
    pub updated_at: u64,
    /// The agent job performing/performed the merge.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub merge_job_id: Option<String>,
    /// Post-merge snapshot CID (from the job's auto-snapshot version record).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub merged_cid: Option<String>,
    /// Server-side fork working copy, when the MR came from an on-orbit fork.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub fork_path: Option<String>,
}

impl MergeRequest {
    /// States a merge may start from / the head may be updated in.
    pub fn is_actionable(&self) -> bool {
        matches!(self.status.as_str(), "open" | "changes_requested" | "approved")
    }
}

/// Marker dropped inside a fork working copy so `open`/`update` can find the
/// base CID without the author restating it. Dotfiles are skipped by the
/// snapshot walker, so it never leaks into head trees.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ForkMeta {
    pub module: String,
    pub base_cid: String,
    pub author: String,
    pub forked_at: u64,
}

pub const FORK_META_FILE: &str = ".mr-fork.json";

// ── Store (one JSON file per MR) ─────────────────────────────────────

pub fn mr_dir() -> PathBuf {
    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string());
    PathBuf::from(home)
        .join(".mod")
        .join("dev")
        .join("merge_requests")
}

/// Staging area the merge job reads BASE/HEAD trees from.
pub fn staging_dir(id: &str) -> PathBuf {
    mr_dir().join("staging").join(id)
}

fn is_valid_mr_id(id: &str) -> bool {
    id.len() > 3
        && id.len() <= 40
        && id.starts_with("mr_")
        && id[3..].chars().all(|c| c.is_ascii_alphanumeric())
}

pub fn new_mr_id() -> String {
    format!("mr_{}", &uuid::Uuid::new_v4().simple().to_string()[..12])
}

pub fn load_mr(id: &str) -> Option<MergeRequest> {
    if !is_valid_mr_id(id) {
        return None;
    }
    let path = mr_dir().join(format!("{id}.json"));
    std::fs::read_to_string(path)
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
}

pub fn save_mr(mr: &MergeRequest) -> Result<(), String> {
    if !is_valid_mr_id(&mr.id) {
        return Err(format!("invalid mr id: {}", mr.id));
    }
    let dir = mr_dir();
    std::fs::create_dir_all(&dir).map_err(|e| format!("mkdir merge_requests: {e}"))?;
    let json = serde_json::to_vec_pretty(mr).map_err(|e| format!("mr serialize: {e}"))?;
    std::fs::write(dir.join(format!("{}.json", mr.id)), json)
        .map_err(|e| format!("mr write: {e}"))
}

/// All MRs, newest first; optionally filtered to one module.
pub fn list_mrs(module: Option<&str>) -> Vec<MergeRequest> {
    let mut out: Vec<MergeRequest> = Vec::new();
    let Ok(rd) = std::fs::read_dir(mr_dir()) else {
        return out;
    };
    for entry in rd.flatten() {
        let name = entry.file_name().to_string_lossy().to_string();
        if !name.starts_with("mr_") || !name.ends_with(".json") {
            continue;
        }
        if let Ok(s) = std::fs::read_to_string(entry.path()) {
            if let Ok(mr) = serde_json::from_str::<MergeRequest>(&s) {
                if module.map(|m| m == mr.module).unwrap_or(true) {
                    out.push(mr);
                }
            }
        }
    }
    out.sort_by(|a, b| b.created_at.cmp(&a.created_at));
    out
}

// ── Diff (manifest vs manifest) ──────────────────────────────────────

#[derive(Debug, Clone, Serialize)]
pub struct FileChange {
    pub path: String,
    /// added | modified | deleted
    pub status: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub base_cid: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub head_cid: Option<String>,
}

fn manifest_map(m: &Manifest) -> std::collections::BTreeMap<&str, &str> {
    m.files
        .iter()
        .map(|f| (f.path.as_str(), f.cid.as_str()))
        .collect()
}

/// Files that differ base → head, in stable path order.
pub fn diff_manifests(base: &Manifest, head: &Manifest) -> Vec<FileChange> {
    let b = manifest_map(base);
    let h = manifest_map(head);
    let mut out = Vec::new();
    for (path, hcid) in &h {
        match b.get(path) {
            None => out.push(FileChange {
                path: path.to_string(),
                status: "added".into(),
                base_cid: None,
                head_cid: Some(hcid.to_string()),
            }),
            Some(bcid) if bcid != hcid => out.push(FileChange {
                path: path.to_string(),
                status: "modified".into(),
                base_cid: Some(bcid.to_string()),
                head_cid: Some(hcid.to_string()),
            }),
            _ => {}
        }
    }
    for (path, bcid) in &b {
        if !h.contains_key(path) {
            out.push(FileChange {
                path: path.to_string(),
                status: "deleted".into(),
                base_cid: Some(bcid.to_string()),
                head_cid: None,
            });
        }
    }
    out.sort_by(|a, b| a.path.cmp(&b.path));
    out
}

/// Paths in `changes` whose live version has also drifted from base —
/// the set the merge agent must reconcile rather than fast-apply.
pub fn conflict_paths(changes: &[FileChange], base: &Manifest, live: &Manifest) -> Vec<String> {
    let b = manifest_map(base);
    let l = manifest_map(live);
    changes
        .iter()
        .filter(|c| {
            let live_cid = l.get(c.path.as_str());
            let base_cid = b.get(c.path.as_str());
            // Drifted = present-with-different-cid, or existence changed.
            live_cid != base_cid
        })
        .map(|c| c.path.clone())
        .collect()
}

/// Load a manifest blob from the store and parse it — validates that a CID
/// actually addresses a snapshot tree, not arbitrary bytes.
pub fn load_manifest(store: &Store, cid: &str) -> Result<Manifest, String> {
    let bytes = store.get(cid)?;
    serde_json::from_slice(&bytes).map_err(|e| format!("cid {cid} is not a snapshot manifest: {e}"))
}

// ── The agentic merge brief ──────────────────────────────────────────

pub fn build_merge_prompt(
    mr: &MergeRequest,
    module_root: &std::path::Path,
    staging: &std::path::Path,
    changes: &[FileChange],
    conflicts: &[String],
) -> String {
    let mut change_lines = String::new();
    for c in changes.iter().take(200) {
        change_lines.push_str(&format!("  [{}] {}\n", c.status, c.path));
    }
    if changes.len() > 200 {
        change_lines.push_str(&format!("  … and {} more\n", changes.len() - 200));
    }
    let conflict_block = if conflicts.is_empty() {
        "  none — the live tree still matches BASE for every MR-changed file; a clean apply is expected.".to_string()
    } else {
        conflicts
            .iter()
            .take(100)
            .map(|p| format!("  {p}"))
            .collect::<Vec<_>>()
            .join("\n")
    };
    let mut comment_block = String::new();
    for c in mr.comments.iter().rev().take(20).rev() {
        let tag = c.action.as_deref().unwrap_or("comment");
        comment_block.push_str(&format!("  [{}] {}: {}\n", tag, c.author, c.body));
    }
    if comment_block.is_empty() {
        comment_block = "  (no review comments)\n".to_string();
    }
    format!(
        "You are performing an AGENTIC MERGE of merge request {id} into the live module '{module}'.\n\
        Your working directory IS the live module: {root}\n\
        \n\
        MR: \"{title}\" — proposed by {author}\n\
        Description: {desc}\n\
        \n\
        Three trees are on disk:\n\
        - BASE (upstream snapshot the fork started from): {staging}/base\n\
        - HEAD (the contributor's proposed tree):          {staging}/head\n\
        - LIVE (current upstream): your working directory\n\
        \n\
        Files changed by this MR (BASE → HEAD):\n{changes}\
        \n\
        Files that ALSO changed upstream since BASE (conflicts to reconcile):\n{conflicts}\n\
        \n\
        Review discussion (context for the contributor's intent):\n{comments}\
        \n\
        Merge procedure:\n\
        1. For each MR-changed file: if the LIVE file still matches BASE, apply the HEAD version verbatim \
        (copy it over; create parent dirs for added files; delete deleted files).\n\
        2. For each conflict file: perform a semantic three-way merge — read BASE, HEAD and LIVE, keep the \
        upstream changes AND incorporate the MR's intent. Never blindly overwrite a diverged file, and never \
        delete a file the upstream has since modified without merging its changes.\n\
        3. Stay strictly inside the live module directory. Do not touch other modules, do not restart any \
        services or processes, and do not run `git` commands.\n\
        4. Verify what you merged with quick, targeted checks (`npx tsc --noEmit` for a TS app, `cargo check` \
        for Rust, `python -m py_compile` for Python — only for the parts you touched) and fix anything the \
        merge broke.\n\
        5. End your final message with one line: MERGE RESULT: ok|partial|failed — <one-sentence summary>.\n\
        If the MR is fundamentally incompatible with the current upstream, change nothing further, restore any \
        partial edits to their LIVE state, and report MERGE RESULT: failed with the reason.",
        id = mr.id,
        module = mr.module,
        root = module_root.display(),
        title = mr.title,
        author = mr.author,
        desc = if mr.description.is_empty() { "(none)" } else { &mr.description },
        staging = staging.display(),
        changes = change_lines,
        conflicts = conflict_block,
        comments = comment_block,
    )
}

pub fn now_ts() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::snapshots::ManifestEntry;

    fn mf(entries: &[(&str, &str)]) -> Manifest {
        Manifest {
            version: 1,
            files: entries
                .iter()
                .map(|(p, c)| ManifestEntry {
                    path: p.to_string(),
                    cid: c.to_string(),
                    size: 1,
                    mode: 0o644,
                })
                .collect(),
        }
    }

    #[test]
    fn diff_detects_add_modify_delete() {
        let base = mf(&[("a.rs", "1"), ("b.rs", "2"), ("c.rs", "3")]);
        let head = mf(&[("a.rs", "1"), ("b.rs", "9"), ("d.rs", "4")]);
        let d = diff_manifests(&base, &head);
        let by: Vec<(String, String)> = d.iter().map(|c| (c.path.clone(), c.status.clone())).collect();
        assert_eq!(
            by,
            vec![
                ("b.rs".to_string(), "modified".to_string()),
                ("c.rs".to_string(), "deleted".to_string()),
                ("d.rs".to_string(), "added".to_string()),
            ]
        );
    }

    #[test]
    fn conflicts_only_where_live_drifted() {
        let base = mf(&[("a.rs", "1"), ("b.rs", "2")]);
        let head = mf(&[("a.rs", "5"), ("b.rs", "6")]);
        // live: a.rs unchanged from base, b.rs drifted upstream
        let live = mf(&[("a.rs", "1"), ("b.rs", "7")]);
        let changes = diff_manifests(&base, &head);
        let conflicts = conflict_paths(&changes, &base, &live);
        assert_eq!(conflicts, vec!["b.rs".to_string()]);
    }

    #[test]
    fn conflict_when_live_deleted_an_mr_file() {
        let base = mf(&[("a.rs", "1")]);
        let head = mf(&[("a.rs", "2")]);
        let live = mf(&[]); // upstream deleted a.rs
        let changes = diff_manifests(&base, &head);
        let conflicts = conflict_paths(&changes, &base, &live);
        assert_eq!(conflicts, vec!["a.rs".to_string()]);
    }

    #[test]
    fn mr_ids_validate() {
        assert!(is_valid_mr_id(&new_mr_id()));
        assert!(!is_valid_mr_id("mr_../../etc"));
        assert!(!is_valid_mr_id("nope"));
        assert!(!is_valid_mr_id("mr_"));
        // "mr_" followed by nothing alnum-invalid
        assert!(!is_valid_mr_id("mr_abc/def"));
    }

    #[test]
    fn save_load_roundtrip() {
        // Mutating HOME races parallel tests (process-wide env), so write a
        // uniquely-named record to the real store and remove it after.
        let module = format!("mrtest-{}", std::process::id());
        let mr = MergeRequest {
            id: new_mr_id(),
            module: module.clone(),
            author: "0xabc".into(),
            title: "test".into(),
            description: String::new(),
            base_cid: "b".repeat(64),
            head_cid: "h".repeat(64),
            revisions: vec![],
            status: "open".into(),
            comments: vec![],
            created_at: 1,
            updated_at: 1,
            merge_job_id: None,
            merged_cid: None,
            fork_path: None,
        };
        save_mr(&mr).unwrap();
        let loaded = load_mr(&mr.id).unwrap();
        assert_eq!(loaded.module, module);
        assert!(list_mrs(Some(&module)).iter().any(|m| m.id == mr.id));
        let _ = std::fs::remove_file(mr_dir().join(format!("{}.json", mr.id)));
    }

    #[test]
    fn mr_empty_suffix_invalid() {
        assert!(!is_valid_mr_id("mr_"));
    }
}
