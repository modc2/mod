//! User-uploaded strats (Python mod.py / Rust mod.rs).
//!
//! Stores files on the persistent data volume so they survive container
//! recreates. Each strat lives at:
//!
//!   <DATA_DIR>/polymarket-user-strats/<id>/mod.<py|rs>
//!
//! Storage is layered against execution: this module only handles
//! upload/list/delete. The Python `Strat` interface in `src/strats/base.py`
//! is what an uploaded `mod.py` is expected to subclass — execution
//! (loading + running uploaded code in the engine loop) is a follow-up
//! and is intentionally NOT wired up here, so a malformed upload can't
//! crash a running engine.
//!
//! Why store as plain files instead of through the existing `StratStore`
//! (encrypted blob keyed by `token_id`): user-strat code is meant to be
//! shared/reviewed/edited, so per-user encryption gets in the way. The
//! existing StratStore stays for the *data-only* strats (trader weights,
//! capital, params) that already work.

use std::fs;
use std::path::PathBuf;

use anyhow::{anyhow, Context, Result};
use serde::{Deserialize, Serialize};

const STRAT_FILE_MAX_BYTES: usize = 256 * 1024; // 256 KiB; plenty for hand-written strats.

/// Kind of source file. Determines the extension and (eventually) the
/// runtime that will execute the strat.
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum StratKind {
    Py,
    Rs,
}

impl StratKind {
    fn extension(self) -> &'static str {
        match self {
            StratKind::Py => "py",
            StratKind::Rs => "rs",
        }
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct UserStratEntry {
    pub id: String,
    pub kind: StratKind,
    /// Raw bytes of the file. JSON-safe because we keep mod.py / mod.rs
    /// to printable ASCII + UTF-8 (validated on upload).
    #[serde(rename = "updatedAt")]
    pub updated_at: u64,
    pub size: u64,
}

#[derive(Clone)]
pub struct UserStratStore {
    root: PathBuf,
}

impl UserStratStore {
    /// Resolve the data root from `POLYMARKET_DATA_DIR` (set by the prod
    /// compose file) or fall back to `/tmp` so dev / unit tests work.
    pub fn new() -> Self {
        let data_dir = std::env::var("POLYMARKET_DATA_DIR")
            .ok()
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from("/tmp"));
        let root = data_dir.join("polymarket-user-strats");
        // Best-effort create; surfaced as a route error on first use if it
        // truly can't be created (read-only fs etc.).
        let _ = fs::create_dir_all(&root);
        Self { root }
    }

    fn strat_dir(&self, id: &str) -> Result<PathBuf> {
        validate_id(id)?;
        Ok(self.root.join(id))
    }

    fn strat_path(&self, id: &str, kind: StratKind) -> Result<PathBuf> {
        Ok(self.strat_dir(id)?.join(format!("mod.{}", kind.extension())))
    }

    /// Write or overwrite a user strat. Validates ID format + content
    /// size + UTF-8. Idempotent: re-uploads bump `updated_at`.
    pub fn upload(&self, id: &str, kind: StratKind, content: &str) -> Result<UserStratEntry> {
        if content.len() > STRAT_FILE_MAX_BYTES {
            return Err(anyhow!(
                "strat file too large ({} bytes; max {})",
                content.len(),
                STRAT_FILE_MAX_BYTES
            ));
        }
        let dir = self.strat_dir(id)?;
        fs::create_dir_all(&dir).context("create strat dir")?;
        let path = self.strat_path(id, kind)?;
        fs::write(&path, content).context("write strat file")?;
        self.entry_for(id, kind)
    }

    pub fn list(&self) -> Result<Vec<UserStratEntry>> {
        let mut out = Vec::new();
        let read = match fs::read_dir(&self.root) {
            Ok(r) => r,
            Err(_) => return Ok(out),
        };
        for entry in read.flatten() {
            let id = entry.file_name().to_string_lossy().into_owned();
            if validate_id(&id).is_err() {
                continue;
            }
            for kind in [StratKind::Py, StratKind::Rs] {
                if let Ok(e) = self.entry_for(&id, kind) {
                    out.push(e);
                }
            }
        }
        out.sort_by(|a, b| b.updated_at.cmp(&a.updated_at));
        Ok(out)
    }

    pub fn read(&self, id: &str, kind: StratKind) -> Result<String> {
        let path = self.strat_path(id, kind)?;
        fs::read_to_string(&path).context("read strat file")
    }

    pub fn delete(&self, id: &str) -> Result<()> {
        let dir = self.strat_dir(id)?;
        if dir.exists() {
            fs::remove_dir_all(&dir).context("delete strat dir")?;
        }
        Ok(())
    }

    fn entry_for(&self, id: &str, kind: StratKind) -> Result<UserStratEntry> {
        let path = self.strat_path(id, kind)?;
        let meta = fs::metadata(&path).context("strat file metadata")?;
        let updated_at = meta
            .modified()
            .ok()
            .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
            .map(|d| d.as_secs())
            .unwrap_or(0);
        Ok(UserStratEntry {
            id: id.to_string(),
            kind,
            updated_at,
            size: meta.len(),
        })
    }
}

/// IDs flow into a filesystem path, so reject anything that could escape
/// the strats directory or trip OS path quirks (slashes, dots, NUL).
fn validate_id(id: &str) -> Result<()> {
    if id.is_empty() || id.len() > 64 {
        return Err(anyhow!("id length must be 1–64"));
    }
    if !id
        .chars()
        .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_')
    {
        return Err(anyhow!("id may only contain [a-zA-Z0-9_-]"));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn id_validation_rejects_traversal() {
        assert!(validate_id("../foo").is_err());
        assert!(validate_id("foo/bar").is_err());
        assert!(validate_id("foo.bar").is_err());
        assert!(validate_id("").is_err());
        assert!(validate_id("ok_123-strat").is_ok());
    }
}
