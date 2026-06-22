//! Content-addressed module snapshots.
//!
//! Storage-agnostic via the `Store` enum. The default is `LocalFs` — blobs land
//! in `~/.mod/claude/blobs/`. Adding ipfs/bitstore/dstore later is a new enum
//! variant + match arm; nothing else moves. The CID is the address of the
//! content; the storage layer is irrelevant to callers.
//!
//! Tree layout per snapshot:
//!   - each file's bytes → blob at CID = sha256(bytes)
//!   - manifest = JSON list of (relative_path, file_cid, mode) → blob at
//!     CID = sha256(manifest_json). This manifest CID is the "tree CID"
//!     the rest of the system calls a "version".

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::path::{Path, PathBuf};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ManifestEntry {
    pub path: String,
    pub cid: String,
    pub size: u64,
    pub mode: u32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Manifest {
    pub version: u32,
    pub files: Vec<ManifestEntry>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct VersionRecord {
    pub cid: String,
    pub message: String,
    pub author: String,
    pub timestamp: u64,
    pub parent: Option<String>,
    /// The mod-protocol api registry CID — `None` if api module unreachable
    /// at the time of the change (we degrade gracefully). When present, every
    /// change in claude's local log is also a node in the global registry chain.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub registry_cid: Option<String>,
    /// The previous registry CID (for git-like linked-list traversal).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub registry_prev: Option<String>,
    /// What kind of action created this record: snapshot, restore, fork, auto-snapshot.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub action: Option<String>,
}

/// Storage backend. Add a variant per backend (ipfs, bitstore, dstore…).
pub enum Store {
    LocalFs { blobs_dir: PathBuf },
}

impl Store {
    pub fn name(&self) -> &'static str {
        match self {
            Store::LocalFs { .. } => "localfs",
        }
    }

    pub fn put(&self, bytes: &[u8]) -> Result<String, String> {
        match self {
            Store::LocalFs { blobs_dir } => {
                let cid = sha256_hex(bytes);
                let path = blobs_dir.join(&cid);
                if !path.exists() {
                    std::fs::write(&path, bytes).map_err(|e| format!("blob write failed: {e}"))?;
                }
                Ok(cid)
            }
        }
    }

    pub fn get(&self, cid: &str) -> Result<Vec<u8>, String> {
        if !is_valid_cid(cid) {
            return Err(format!("invalid cid: {cid}"));
        }
        match self {
            Store::LocalFs { blobs_dir } => std::fs::read(blobs_dir.join(cid))
                .map_err(|e| format!("blob read failed: {e}")),
        }
    }

    #[allow(dead_code)]
    pub fn has(&self, cid: &str) -> bool {
        if !is_valid_cid(cid) {
            return false;
        }
        match self {
            Store::LocalFs { blobs_dir } => blobs_dir.join(cid).exists(),
        }
    }
}

pub fn default_store() -> Store {
    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string());
    let blobs = PathBuf::from(home).join(".mod").join("claude").join("blobs");
    std::fs::create_dir_all(&blobs).ok();
    Store::LocalFs { blobs_dir: blobs }
}

fn sha256_hex(bytes: &[u8]) -> String {
    let mut h = Sha256::new();
    h.update(bytes);
    hex::encode(h.finalize())
}

fn is_valid_cid(s: &str) -> bool {
    s.len() == 64 && s.chars().all(|c| c.is_ascii_hexdigit())
}

fn should_skip_dir(name: &str) -> bool {
    matches!(
        name,
        "node_modules"
            | "target"
            | "target-docker"
            | "vendor"
            | "__pycache__"
            | ".git"
            | ".next"
            | "dist"
            | "build"
            | ".venv"
            | "venv"
            | "blobs"
    ) || name.starts_with('.')
}

fn walk_files(
    root: &Path,
    dir: &Path,
    out: &mut Vec<ManifestEntry>,
    store: &Store,
) -> Result<(), String> {
    let entries =
        std::fs::read_dir(dir).map_err(|e| format!("read_dir {}: {e}", dir.display()))?;
    for entry in entries.flatten() {
        let path = entry.path();
        let file_name = entry.file_name();
        let name = file_name.to_string_lossy();
        let ft = entry.file_type().map_err(|e| format!("file_type: {e}"))?;
        if ft.is_dir() {
            if should_skip_dir(&name) {
                continue;
            }
            walk_files(root, &path, out, store)?;
        } else if ft.is_file() {
            // Skip noisy dotfiles like .DS_Store
            if name.starts_with('.') {
                continue;
            }
            let rel = path
                .strip_prefix(root)
                .map_err(|e| format!("strip_prefix: {e}"))?
                .to_string_lossy()
                .replace('\\', "/");
            let bytes = std::fs::read(&path).map_err(|e| format!("read {rel}: {e}"))?;
            let cid = store.put(&bytes)?;
            #[cfg(unix)]
            let mode = {
                use std::os::unix::fs::PermissionsExt;
                entry
                    .metadata()
                    .map(|m| m.permissions().mode())
                    .unwrap_or(0o644)
            };
            #[cfg(not(unix))]
            let mode = 0o644;
            out.push(ManifestEntry {
                path: rel,
                cid,
                size: bytes.len() as u64,
                mode,
            });
        }
    }
    Ok(())
}

pub fn snapshot_dir(root: &Path, store: &Store) -> Result<(String, Manifest), String> {
    if !root.is_dir() {
        return Err(format!("not a directory: {}", root.display()));
    }
    let mut entries: Vec<ManifestEntry> = Vec::new();
    walk_files(root, root, &mut entries, store)?;
    entries.sort_by(|a, b| a.path.cmp(&b.path));
    let manifest = Manifest {
        version: 1,
        files: entries,
    };
    let manifest_json =
        serde_json::to_vec(&manifest).map_err(|e| format!("manifest serialize: {e}"))?;
    let tree_cid = store.put(&manifest_json)?;
    Ok((tree_cid, manifest))
}

pub fn restore_into(target: &Path, cid: &str, store: &Store) -> Result<usize, String> {
    if !is_valid_cid(cid) {
        return Err(format!("invalid cid: {cid}"));
    }
    let manifest_bytes = store.get(cid)?;
    let manifest: Manifest = serde_json::from_slice(&manifest_bytes)
        .map_err(|e| format!("manifest parse: {e}"))?;
    std::fs::create_dir_all(target).map_err(|e| format!("mkdir target: {e}"))?;
    let mut written = 0usize;
    let target_canon = std::fs::canonicalize(target).unwrap_or_else(|_| target.to_path_buf());
    for entry in &manifest.files {
        // Reject anything that could escape `target`: parent-dir traversal,
        // absolute paths (Path::join("/x") silently discards the base), and
        // Windows-style roots. Belt-and-suspenders against a crafted manifest.
        let rel = Path::new(&entry.path);
        let escapes = entry.path.contains("..")
            || rel.is_absolute()
            || rel
                .components()
                .any(|c| matches!(c, std::path::Component::ParentDir | std::path::Component::RootDir | std::path::Component::Prefix(_)));
        if escapes {
            return Err(format!("unsafe path in manifest: {}", entry.path));
        }
        let bytes = store.get(&entry.cid)?;
        let dst = target.join(&entry.path);
        // Final guard: the resolved parent must stay inside target.
        if let Some(parent) = dst.parent() {
            std::fs::create_dir_all(parent).map_err(|e| format!("mkdir {}: {e}", parent.display()))?;
            if let Ok(parent_canon) = std::fs::canonicalize(parent) {
                if !parent_canon.starts_with(&target_canon) {
                    return Err(format!("path escapes target: {}", entry.path));
                }
            }
        }
        std::fs::write(&dst, &bytes).map_err(|e| format!("write {}: {e}", dst.display()))?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let _ = std::fs::set_permissions(&dst, std::fs::Permissions::from_mode(entry.mode));
        }
        written += 1;
    }
    Ok(written)
}

// ── Versions log (per module, append-only JSON list) ─────────────────

pub fn versions_dir() -> PathBuf {
    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string());
    PathBuf::from(home).join(".mod").join("claude").join("versions")
}

fn versions_path(module: &str) -> PathBuf {
    let safe: String = module
        .chars()
        .filter(|c| c.is_ascii_alphanumeric() || matches!(c, '-' | '_' | '/'))
        .collect();
    let safe = safe.replace('/', "__");
    versions_dir().join(format!("{safe}.json"))
}

pub fn read_versions(module: &str) -> Vec<VersionRecord> {
    let path = versions_path(module);
    std::fs::read_to_string(&path)
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or_default()
}

pub fn append_version(module: &str, record: VersionRecord) -> Result<(), String> {
    let dir = versions_dir();
    std::fs::create_dir_all(&dir).map_err(|e| format!("mkdir versions: {e}"))?;
    let mut history = read_versions(module);
    history.push(record);
    let json =
        serde_json::to_vec_pretty(&history).map_err(|e| format!("versions serialize: {e}"))?;
    std::fs::write(versions_path(module), json).map_err(|e| format!("versions write: {e}"))?;
    Ok(())
}
