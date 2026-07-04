//! User-uploaded strats (Python mod.py / Rust mod.rs) + sharing/forking.
//!
//! Stores files on the persistent data volume so they survive container
//! recreates. Each strat lives at:
//!
//!   <DATA_DIR>/polymarket-user-strats/<id>/mod.<py|rs>
//!   <DATA_DIR>/polymarket-user-strats/<id>/meta.json   (owner, title, …)
//!
//! Storage is layered against execution: this module only handles
//! upload/list/delete + the public/fork metadata. The Python `Strat`
//! interface in `src/strats/base.py` is what an uploaded `mod.py` is
//! expected to subclass — execution (loading + running uploaded code in
//! the engine loop) is a follow-up and is intentionally NOT wired up
//! here, so a malformed upload can't crash a running engine.
//!
//! Sharing model: every strat carries a `meta.json` recording its `owner`
//! (the connected EOA, lowercased), a human title/description, a `public`
//! flag, and `forked_from` lineage. A public strat shows up in every
//! trader's community gallery; forking copies the source into a new strat
//! owned by the forker with `forked_from` pointing back at the original.
//! Ownership is enforced server-side against the `owner` passed by the
//! client — the same trust model the rest of the API uses for the `eoa`
//! query param (no signature; this is a single-tenant-per-deploy tool).
//!
//! Why store as plain files instead of through the existing `StratStore`
//! (encrypted blob keyed by `token_id`): user-strat code is meant to be
//! shared/reviewed/edited, so per-user encryption gets in the way. The
//! existing StratStore stays for the *data-only* strats (trader weights,
//! capital, params) that already work.

use std::fs;
use std::path::PathBuf;
use std::time::{SystemTime, UNIX_EPOCH};

use anyhow::{anyhow, Context, Result};
use serde::{Deserialize, Serialize};

const STRAT_FILE_MAX_BYTES: usize = 256 * 1024; // 256 KiB; plenty for hand-written strats.
const TITLE_MAX: usize = 120;
const DESC_MAX: usize = 2000;

/// Bundle format discriminator — lets an importer reject blobs that aren't
/// shared strats (a CID can point at anything).
pub const BUNDLE_FORMAT: &str = "polymarket.strat";
/// Bundle schema version. Bump on breaking changes; importers reject newer.
pub const BUNDLE_VERSION: u32 = 1;

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

/// Per-strat metadata persisted alongside the source as `meta.json`.
/// All sharing/forking state lives here so the source files stay pure code.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
struct StratMeta {
    #[serde(default)]
    owner: String, // lowercased EOA; "" = legacy/unclaimed upload
    #[serde(default)]
    title: String,
    #[serde(default)]
    description: String,
    #[serde(default)]
    public: bool,
    #[serde(default, rename = "forkedFrom", skip_serializing_if = "Option::is_none")]
    forked_from: Option<String>,
    #[serde(default, rename = "createdAt")]
    created_at: u64,
    #[serde(default)]
    forks: u64,
}

/// Fields a client may set on upload. All optional so the legacy
/// `{id, kind, content}` upload (used by the source-code editor) keeps
/// working unchanged.
#[derive(Debug, Clone, Default, Deserialize)]
pub struct UploadMeta {
    pub owner: Option<String>,
    pub title: Option<String>,
    pub description: Option<String>,
    pub public: Option<bool>,
}

#[derive(Debug, Clone, Serialize)]
pub struct UserStratEntry {
    pub id: String,
    pub kind: StratKind,
    #[serde(rename = "updatedAt")]
    pub updated_at: u64,
    pub size: u64,
    // ── sharing metadata (flattened from meta.json) ──
    pub owner: String,
    pub title: String,
    pub description: String,
    pub public: bool,
    #[serde(rename = "forkedFrom", skip_serializing_if = "Option::is_none")]
    pub forked_from: Option<String>,
    #[serde(rename = "createdAt")]
    pub created_at: u64,
    pub forks: u64,
    /// True when `owner` matches the requesting EOA (or the strat is
    /// unclaimed). Set by list helpers; defaults false.
    pub mine: bool,
}

/// A self-describing, content-addressable strat bundle. Its serialized bytes
/// are what gets stored in the share backend (localfs by default) and hashed
/// to the CID people pass around. Plain JSON + a `format`/`version` header so
/// the same CID re-pinned to any IPFS-compatible store resolves to the same
/// strat — the share link is portable across systems, not tied to one deploy.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StratBundle {
    /// Always [`BUNDLE_FORMAT`]; importers reject anything else.
    pub format: String,
    pub version: u32,
    pub id: String,
    pub kind: StratKind,
    #[serde(default)]
    pub title: String,
    #[serde(default)]
    pub description: String,
    pub source: String,
    #[serde(default, rename = "forkedFrom", skip_serializing_if = "Option::is_none")]
    pub forked_from: Option<String>,
    /// Original author EOA — provenance only; the importer becomes the owner.
    #[serde(default)]
    pub author: String,
    #[serde(default, rename = "createdAt")]
    pub created_at: u64,
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

    fn meta_path(&self, id: &str) -> Result<PathBuf> {
        Ok(self.strat_dir(id)?.join("meta.json"))
    }

    fn load_meta(&self, id: &str) -> StratMeta {
        let path = match self.meta_path(id) {
            Ok(p) => p,
            Err(_) => return StratMeta::default(),
        };
        fs::read_to_string(&path)
            .ok()
            .and_then(|s| serde_json::from_str(&s).ok())
            .unwrap_or_default()
    }

    fn save_meta(&self, id: &str, meta: &StratMeta) -> Result<()> {
        let path = self.meta_path(id)?;
        let json = serde_json::to_string_pretty(meta).context("serialize meta")?;
        fs::write(&path, json).context("write meta.json")
    }

    fn has_source(&self, id: &str) -> bool {
        [StratKind::Py, StratKind::Rs]
            .iter()
            .any(|k| self.strat_path(id, *k).map(|p| p.exists()).unwrap_or(false))
    }

    /// Write or overwrite a user strat. Validates ID format + content size
    /// + UTF-8. Merges sharing metadata: created_at/owner/forked_from/forks
    /// are preserved across re-uploads; title/description/public update when
    /// provided. Rejects overwriting a strat owned by a *different* EOA.
    pub fn upload(
        &self,
        id: &str,
        kind: StratKind,
        content: &str,
        opts: &UploadMeta,
    ) -> Result<UserStratEntry> {
        if content.len() > STRAT_FILE_MAX_BYTES {
            return Err(anyhow!(
                "strat file too large ({} bytes; max {})",
                content.len(),
                STRAT_FILE_MAX_BYTES
            ));
        }
        let caller = normalize_owner(opts.owner.as_deref());
        let mut meta = self.load_meta(id);
        // Guard against clobbering someone else's published strat. Empty
        // owner (legacy editor / unclaimed) is always writable.
        if !meta.owner.is_empty() && !caller.is_empty() && meta.owner != caller {
            return Err(anyhow!(
                "strat '{}' is owned by {} — fork it instead of overwriting",
                id,
                short_addr(&meta.owner)
            ));
        }

        let dir = self.strat_dir(id)?;
        fs::create_dir_all(&dir).context("create strat dir")?;
        let path = self.strat_path(id, kind)?;
        fs::write(&path, content).context("write strat file")?;

        // Merge metadata.
        if meta.created_at == 0 {
            meta.created_at = now_secs();
        }
        if meta.owner.is_empty() {
            meta.owner = caller;
        }
        if let Some(t) = opts.title.as_ref() {
            meta.title = t.trim().chars().take(TITLE_MAX).collect();
        }
        if meta.title.is_empty() {
            meta.title = id.to_string();
        }
        if let Some(d) = opts.description.as_ref() {
            meta.description = d.trim().chars().take(DESC_MAX).collect();
        }
        if let Some(p) = opts.public {
            meta.public = p;
        }
        self.save_meta(id, &meta)?;
        self.entry_for(id, kind, &caller_for_mine(opts.owner.as_deref()))
    }

    /// List strats for the strat-management UI. When `owner` is provided,
    /// returns that owner's strats plus any unclaimed (empty-owner) legacy
    /// uploads. When omitted, returns everything (back-compat for the
    /// source-code editor / legacy callers).
    pub fn list(&self, owner: Option<&str>) -> Result<Vec<UserStratEntry>> {
        let want = owner.map(|o| normalize_owner(Some(o)));
        let mut out = self.collect(|meta| match &want {
            Some(w) => &meta.owner == w || meta.owner.is_empty(),
            None => true,
        })?;
        if let Some(w) = &want {
            for e in out.iter_mut() {
                e.mine = e.owner.is_empty() || &e.owner == w;
            }
        }
        out.sort_by(|a, b| b.updated_at.cmp(&a.updated_at));
        Ok(out)
    }

    /// Public community gallery: every strat flagged `public`. `viewer` (if
    /// given) marks which entries belong to the requester so the UI can hide
    /// the fork button on its own strats.
    pub fn list_public(&self, viewer: Option<&str>) -> Result<Vec<UserStratEntry>> {
        let me = viewer.map(|v| normalize_owner(Some(v)));
        let mut out = self.collect(|meta| meta.public)?;
        if let Some(me) = &me {
            for e in out.iter_mut() {
                e.mine = &e.owner == me;
            }
        }
        // Most-forked first, then most-recent — surfaces proven strats.
        out.sort_by(|a, b| {
            b.forks
                .cmp(&a.forks)
                .then(b.updated_at.cmp(&a.updated_at))
        });
        Ok(out)
    }

    /// Toggle a strat's public visibility. Owner-gated.
    pub fn set_public(&self, id: &str, owner: &str, public: bool) -> Result<UserStratEntry> {
        validate_id(id)?;
        if !self.has_source(id) {
            return Err(anyhow!("no strat '{}'", id));
        }
        let caller = normalize_owner(Some(owner));
        let mut meta = self.load_meta(id);
        self.assert_owner(&meta, &caller, id)?;
        if meta.owner.is_empty() {
            meta.owner = caller.clone(); // claim on first publish
        }
        if meta.created_at == 0 {
            meta.created_at = now_secs();
        }
        meta.public = public;
        self.save_meta(id, &meta)?;
        let kind = self.primary_kind(id)?;
        self.entry_for(id, kind, &Some(caller))
    }

    /// Fork `src_id` into a new strat `new_id` owned by `new_owner`. The
    /// source must be public or already owned by the forker. Copies every
    /// source kind present, stamps `forked_from`, and bumps the source's
    /// fork counter.
    pub fn fork(&self, src_id: &str, new_id: &str, new_owner: &str) -> Result<UserStratEntry> {
        validate_id(src_id)?;
        validate_id(new_id)?;
        if src_id == new_id {
            return Err(anyhow!("fork needs a new id"));
        }
        if !self.has_source(src_id) {
            return Err(anyhow!("no strat '{}' to fork", src_id));
        }
        if self.has_source(new_id) || self.meta_path(new_id)?.exists() {
            return Err(anyhow!("id '{}' already exists — pick another", new_id));
        }
        let forker = normalize_owner(Some(new_owner));
        let mut src_meta = self.load_meta(src_id);
        if !src_meta.public && !src_meta.owner.is_empty() && src_meta.owner != forker {
            return Err(anyhow!("strat '{}' is private", src_id));
        }

        // Copy every source file present.
        let new_dir = self.strat_dir(new_id)?;
        fs::create_dir_all(&new_dir).context("create fork dir")?;
        let mut copied_kind: Option<StratKind> = None;
        for kind in [StratKind::Py, StratKind::Rs] {
            let from = self.strat_path(src_id, kind)?;
            if from.exists() {
                let content = fs::read_to_string(&from).context("read source for fork")?;
                fs::write(self.strat_path(new_id, kind)?, content).context("write fork file")?;
                copied_kind.get_or_insert(kind);
            }
        }
        let kind = copied_kind.ok_or_else(|| anyhow!("source has no code"))?;

        let base_title = if src_meta.title.is_empty() {
            src_id.to_string()
        } else {
            src_meta.title.clone()
        };
        let new_meta = StratMeta {
            owner: forker.clone(),
            title: format!("{} (fork)", base_title).chars().take(TITLE_MAX).collect(),
            description: src_meta.description.clone(),
            public: false, // forks start private; owner publishes when ready
            forked_from: Some(src_id.to_string()),
            created_at: now_secs(),
            forks: 0,
        };
        self.save_meta(new_id, &new_meta)?;

        // Bump the source fork counter (best-effort; don't fail the fork).
        src_meta.forks = src_meta.forks.saturating_add(1);
        let _ = self.save_meta(src_id, &src_meta);

        self.entry_for(new_id, kind, &Some(forker))
    }

    pub fn read(&self, id: &str, kind: StratKind) -> Result<String> {
        let path = self.strat_path(id, kind)?;
        fs::read_to_string(&path).context("read strat file")
    }

    /// Delete a strat. When `owner` is supplied it must match (or the strat
    /// must be unclaimed); `None` skips the check for legacy/admin callers.
    pub fn delete(&self, id: &str, owner: Option<&str>) -> Result<()> {
        let dir = self.strat_dir(id)?;
        if let Some(o) = owner {
            let caller = normalize_owner(Some(o));
            let meta = self.load_meta(id);
            self.assert_owner(&meta, &caller, id)?;
        }
        if dir.exists() {
            fs::remove_dir_all(&dir).context("delete strat dir")?;
        }
        Ok(())
    }

    // ── content-addressable sharing ──

    /// Build a shareable bundle for `id`. Readable when the strat is public
    /// or owned by `viewer` (`None` = unrestricted, for legacy/admin callers).
    /// Bundles the strat's primary source (`mod.py` preferred over `mod.rs`).
    pub fn bundle(&self, id: &str, viewer: Option<&str>) -> Result<StratBundle> {
        validate_id(id)?;
        let kind = self.primary_kind(id)?;
        let meta = self.load_meta(id);
        // Access gate: don't let anyone mint a share link for a private strat
        // they don't own. Public strats are shareable by anyone.
        if !meta.public {
            let v = normalize_owner(viewer);
            if !meta.owner.is_empty() && (v.is_empty() || v != meta.owner) {
                return Err(anyhow!(
                    "strat '{}' is private — only its owner can share it",
                    id
                ));
            }
        }
        let source = self.read(id, kind)?;
        Ok(StratBundle {
            format: BUNDLE_FORMAT.to_string(),
            version: BUNDLE_VERSION,
            id: id.to_string(),
            kind,
            title: if meta.title.is_empty() {
                id.to_string()
            } else {
                meta.title.clone()
            },
            description: meta.description.clone(),
            source,
            forked_from: meta.forked_from.clone(),
            author: meta.owner.clone(),
            created_at: meta.created_at,
        })
    }

    /// Import a bundle (fetched by CID from the share backend) as a new strat
    /// owned by `new_owner`. Mirrors `fork()`: the copy starts private and
    /// records `forked_from` lineage back to the bundle's original id. The new
    /// id is `want_id` if given and free, else the bundle id, else a numeric
    /// suffix — so re-importing the same CID never clobbers an existing strat.
    pub fn import_bundle(
        &self,
        bundle: &StratBundle,
        new_owner: &str,
        want_id: Option<&str>,
    ) -> Result<UserStratEntry> {
        if bundle.format != BUNDLE_FORMAT {
            return Err(anyhow!(
                "not a polymarket strat bundle (format='{}')",
                bundle.format
            ));
        }
        if bundle.version > BUNDLE_VERSION {
            return Err(anyhow!(
                "bundle is version {} but this build only understands up to {}",
                bundle.version,
                BUNDLE_VERSION
            ));
        }
        if bundle.source.is_empty() {
            return Err(anyhow!("bundle has no source code"));
        }
        if bundle.source.len() > STRAT_FILE_MAX_BYTES {
            return Err(anyhow!(
                "bundle source too large ({} bytes; max {})",
                bundle.source.len(),
                STRAT_FILE_MAX_BYTES
            ));
        }
        let owner = normalize_owner(Some(new_owner));
        let id = self.unique_id(want_id.unwrap_or(&bundle.id))?;

        let dir = self.strat_dir(&id)?;
        fs::create_dir_all(&dir).context("create import dir")?;
        fs::write(self.strat_path(&id, bundle.kind)?, &bundle.source)
            .context("write imported strat")?;

        let base_title = if bundle.title.trim().is_empty() {
            bundle.id.clone()
        } else {
            bundle.title.clone()
        };
        let meta = StratMeta {
            owner: owner.clone(),
            title: base_title.chars().take(TITLE_MAX).collect(),
            description: bundle.description.chars().take(DESC_MAX).collect(),
            public: false, // imports start private; owner publishes when ready
            forked_from: Some(bundle.id.clone()),
            created_at: now_secs(),
            forks: 0,
        };
        self.save_meta(&id, &meta)?;
        self.entry_for(&id, bundle.kind, &caller_for_mine(Some(&owner)))
    }

    /// Pick a free id derived from `base`: sanitize to the allowed charset,
    /// then append `-1`, `-2`, … until nothing exists there.
    fn unique_id(&self, base: &str) -> Result<String> {
        let cleaned: String = base
            .chars()
            .filter(|c| c.is_ascii_alphanumeric() || *c == '-' || *c == '_')
            .take(58) // leave headroom for a `-N` suffix within the 64 cap
            .collect();
        let cleaned = if cleaned.is_empty() {
            "strat".to_string()
        } else {
            cleaned
        };
        if !self.exists(&cleaned) {
            validate_id(&cleaned)?;
            return Ok(cleaned);
        }
        for n in 1..=9999 {
            let cand = format!("{}-{}", cleaned, n);
            if !self.exists(&cand) {
                validate_id(&cand)?;
                return Ok(cand);
            }
        }
        Err(anyhow!("could not find a free id for '{}'", cleaned))
    }

    /// True if any source file or metadata already lives under `id`.
    fn exists(&self, id: &str) -> bool {
        self.has_source(id) || self.meta_path(id).map(|p| p.exists()).unwrap_or(false)
    }

    // ── helpers ──

    fn assert_owner(&self, meta: &StratMeta, caller: &str, id: &str) -> Result<()> {
        if meta.owner.is_empty() || caller.is_empty() || meta.owner == *caller {
            return Ok(());
        }
        Err(anyhow!(
            "strat '{}' belongs to {}",
            id,
            short_addr(&meta.owner)
        ))
    }

    fn primary_kind(&self, id: &str) -> Result<StratKind> {
        for kind in [StratKind::Py, StratKind::Rs] {
            if self.strat_path(id, kind)?.exists() {
                return Ok(kind);
            }
        }
        Err(anyhow!("no source for '{}'", id))
    }

    /// Walk the strat root and emit one entry per (id, kind) whose meta
    /// passes `keep`.
    fn collect(&self, keep: impl Fn(&StratMeta) -> bool) -> Result<Vec<UserStratEntry>> {
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
            let meta = self.load_meta(&id);
            if !keep(&meta) {
                continue;
            }
            for kind in [StratKind::Py, StratKind::Rs] {
                if let Ok(e) = self.entry_for(&id, kind, &None) {
                    out.push(e);
                }
            }
        }
        Ok(out)
    }

    fn entry_for(
        &self,
        id: &str,
        kind: StratKind,
        viewer: &Option<String>,
    ) -> Result<UserStratEntry> {
        let path = self.strat_path(id, kind)?;
        let fmeta = fs::metadata(&path).context("strat file metadata")?;
        let updated_at = fmeta
            .modified()
            .ok()
            .and_then(|t| t.duration_since(UNIX_EPOCH).ok())
            .map(|d| d.as_secs())
            .unwrap_or(0);
        let meta = self.load_meta(id);
        let mine = viewer
            .as_ref()
            .map(|v| meta.owner.is_empty() || &meta.owner == v)
            .unwrap_or(false);
        Ok(UserStratEntry {
            id: id.to_string(),
            kind,
            updated_at,
            size: fmeta.len(),
            owner: meta.owner.clone(),
            title: if meta.title.is_empty() {
                id.to_string()
            } else {
                meta.title.clone()
            },
            description: meta.description,
            public: meta.public,
            forked_from: meta.forked_from,
            created_at: meta.created_at,
            forks: meta.forks,
            mine,
        })
    }
}

fn now_secs() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

/// Lowercase + trim an EOA-ish owner string; empty/whitespace → "".
fn normalize_owner(owner: Option<&str>) -> String {
    owner.map(|o| o.trim().to_lowercase()).unwrap_or_default()
}

fn caller_for_mine(owner: Option<&str>) -> Option<String> {
    let n = normalize_owner(owner);
    if n.is_empty() {
        None
    } else {
        Some(n)
    }
}

/// `0x1234…cdef` for human-readable ownership errors.
fn short_addr(addr: &str) -> String {
    if addr.len() <= 12 {
        return addr.to_string();
    }
    format!("{}…{}", &addr[..6], &addr[addr.len() - 4..])
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

    fn tmp_store() -> (UserStratStore, PathBuf) {
        let base = std::env::temp_dir().join(format!("ustrat-test-{}", now_secs_nanos()));
        let store = UserStratStore {
            root: base.clone(),
        };
        fs::create_dir_all(&base).unwrap();
        (store, base)
    }

    // now_secs() is whole-seconds; tests need unique dirs, so derive a
    // nanosecond suffix without touching production code paths.
    fn now_secs_nanos() -> u128 {
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_nanos())
            .unwrap_or(0)
    }

    #[test]
    fn id_validation_rejects_traversal() {
        assert!(validate_id("../foo").is_err());
        assert!(validate_id("foo/bar").is_err());
        assert!(validate_id("foo.bar").is_err());
        assert!(validate_id("").is_err());
        assert!(validate_id("ok_123-strat").is_ok());
    }

    #[test]
    fn upload_records_owner_and_blocks_foreign_overwrite() {
        let (s, dir) = tmp_store();
        let alice = UploadMeta {
            owner: Some("0xAAAA".into()),
            title: Some("Alice EV".into()),
            ..Default::default()
        };
        let e = s.upload("a1", StratKind::Py, "print(1)", &alice).unwrap();
        assert_eq!(e.owner, "0xaaaa");
        assert_eq!(e.title, "Alice EV");
        assert!(!e.public);

        // Bob can't overwrite Alice's strat.
        let bob = UploadMeta {
            owner: Some("0xBBBB".into()),
            ..Default::default()
        };
        assert!(s.upload("a1", StratKind::Py, "print(2)", &bob).is_err());
        let _ = fs::remove_dir_all(dir);
    }

    #[test]
    fn publish_then_visible_in_gallery() {
        let (s, dir) = tmp_store();
        let alice = UploadMeta {
            owner: Some("0xaaaa".into()),
            ..Default::default()
        };
        s.upload("pub1", StratKind::Py, "x=1", &alice).unwrap();
        assert!(s.list_public(None).unwrap().is_empty());
        s.set_public("pub1", "0xaaaa", true).unwrap();
        let gallery = s.list_public(Some("0xbbbb")).unwrap();
        assert_eq!(gallery.len(), 1);
        assert!(!gallery[0].mine);
        // non-owner cannot toggle
        assert!(s.set_public("pub1", "0xbbbb", false).is_err());
        let _ = fs::remove_dir_all(dir);
    }

    #[test]
    fn fork_copies_and_counts() {
        let (s, dir) = tmp_store();
        let alice = UploadMeta {
            owner: Some("0xaaaa".into()),
            public: Some(true),
            ..Default::default()
        };
        s.upload("src", StratKind::Py, "EDGE=0.05", &alice).unwrap();
        let f = s.fork("src", "mine1", "0xbbbb").unwrap();
        assert_eq!(f.owner, "0xbbbb");
        assert_eq!(f.forked_from.as_deref(), Some("src"));
        assert!(!f.public);
        assert_eq!(s.read("mine1", StratKind::Py).unwrap(), "EDGE=0.05");
        // source fork counter bumped
        let src = s
            .list(Some("0xaaaa"))
            .unwrap()
            .into_iter()
            .find(|e| e.id == "src")
            .unwrap();
        assert_eq!(src.forks, 1);
        // can't fork onto an existing id
        assert!(s.fork("src", "mine1", "0xcccc").is_err());
        let _ = fs::remove_dir_all(dir);
    }

    #[test]
    fn cannot_fork_private_strat_of_another() {
        let (s, dir) = tmp_store();
        let alice = UploadMeta {
            owner: Some("0xaaaa".into()),
            ..Default::default()
        };
        s.upload("priv", StratKind::Py, "x=1", &alice).unwrap();
        assert!(s.fork("priv", "copy", "0xbbbb").is_err());
        // owner can fork their own private strat
        assert!(s.fork("priv", "copy", "0xaaaa").is_ok());
        let _ = fs::remove_dir_all(dir);
    }

    #[test]
    fn bundle_roundtrips_into_a_new_owner() {
        let (s, dir) = tmp_store();
        let alice = UploadMeta {
            owner: Some("0xaaaa".into()),
            title: Some("Alice EV".into()),
            description: Some("captures EV edge".into()),
            public: Some(true),
        };
        s.upload("ev", StratKind::Py, "EDGE=0.05", &alice).unwrap();

        // Share → bundle carries the source + provenance.
        let b = s.bundle("ev", Some("0xbbbb")).unwrap(); // public ⇒ anyone can share
        assert_eq!(b.format, BUNDLE_FORMAT);
        assert_eq!(b.source, "EDGE=0.05");
        assert_eq!(b.author, "0xaaaa");

        // Import into Bob's store as a new, private, owned strat.
        let e = s.import_bundle(&b, "0xBBBB", None).unwrap();
        assert_eq!(e.owner, "0xbbbb");
        assert!(!e.public);
        assert_eq!(e.forked_from.as_deref(), Some("ev"));
        // id collided with the existing "ev" → suffixed.
        assert_eq!(e.id, "ev-1");
        assert_eq!(s.read("ev-1", StratKind::Py).unwrap(), "EDGE=0.05");
        let _ = fs::remove_dir_all(dir);
    }

    #[test]
    fn bundle_blocks_private_strat_of_another() {
        let (s, dir) = tmp_store();
        let alice = UploadMeta {
            owner: Some("0xaaaa".into()),
            ..Default::default()
        };
        s.upload("secret", StratKind::Py, "x=1", &alice).unwrap();
        // Stranger can't mint a share link for Alice's private strat.
        assert!(s.bundle("secret", Some("0xbbbb")).is_err());
        // Owner can.
        assert!(s.bundle("secret", Some("0xaaaa")).is_ok());
        let _ = fs::remove_dir_all(dir);
    }

    #[test]
    fn import_rejects_foreign_blob() {
        let (s, dir) = tmp_store();
        let junk = StratBundle {
            format: "not-a-strat".into(),
            version: 1,
            id: "x".into(),
            kind: StratKind::Py,
            title: String::new(),
            description: String::new(),
            source: "print(1)".into(),
            forked_from: None,
            author: String::new(),
            created_at: 0,
        };
        assert!(s.import_bundle(&junk, "0xbbbb", None).is_err());
        let _ = fs::remove_dir_all(dir);
    }
}
