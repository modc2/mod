//! The ƒ SCORE MARKET's community shelf — published score functions.
//!
//! A score function is a tiny named source string (expr / JS / Python) that
//! ranks and filters the trader board CLIENT-side. This store only holds and
//! lists the text: it never executes anything (the browser compiles through
//! the same editor path a hand-typed score uses), so publishing is
//! storage+metadata exactly like `user_strats.rs` — and deliberately shaped
//! after it, including share-by-CID through the same [`crate::share::ShareStore`]
//! so a listing is portable across deploys.
//!
//! Each published fn lives at `<DATA_DIR>/polymarket-score-fns/<id>.json` as
//! one self-contained record — no meta.json split, because the source is
//! capped small (the client caps saved formulas at 4000 chars; we mirror it).
//!
//! Trust model matches the rest of the API: `owner` is the connected EOA
//! passed by the client, enforced without a signature — single-tenant-per-
//! deploy tool behind the access gate.

use std::fs;
use std::path::PathBuf;
use std::time::{SystemTime, UNIX_EPOCH};

use anyhow::{anyhow, Context, Result};
use axum::extract::{Path, Query, State};
use axum::http::StatusCode;
use axum::response::IntoResponse;
use axum::routing::{get, post};
use axum::{Json, Router};
use serde::{Deserialize, Serialize};
use serde_json::json;

use crate::AppState;

/// Mirrors the client-side saved-formula cap in `scoreFormula.ts`.
const SOURCE_MAX: usize = 4000;
const NAME_MAX: usize = 40;
const DESC_MAX: usize = 500;
const TAG_MAX: usize = 24;
const TAGS_MAX: usize = 8;

/// Bundle format discriminator — importers reject blobs that aren't shared
/// score fns (a CID can point at anything).
pub const BUNDLE_FORMAT: &str = "polymarket.scorefn";
pub const BUNDLE_VERSION: u32 = 1;

/// One published score function — the whole record, persisted as-is.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ScoreFnRec {
    pub id: String,
    /// Lowercased EOA of the publisher; enforced on overwrite/delete.
    #[serde(default)]
    pub owner: String,
    pub name: String,
    #[serde(default)]
    pub description: String,
    #[serde(default)]
    pub tags: Vec<String>,
    pub source: String,
    /// Original author EOA — provenance carried through import; the importer
    /// becomes the `owner`.
    #[serde(default)]
    pub author: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub forked_from: Option<String>,
    #[serde(default)]
    pub created_at: u64,
    #[serde(default)]
    pub updated_at: u64,
}

/// What the list endpoint returns — the record plus the viewer-relative
/// `mine` flag.
#[derive(Debug, Clone, Serialize)]
pub struct ScoreFnEntry {
    #[serde(flatten)]
    pub rec: ScoreFnRec,
    pub mine: bool,
}

/// The self-describing, content-addressable share bundle (same idea as
/// `StratBundle`): plain JSON + format/version header, so the CID resolves
/// identically on any IPFS-compatible store.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ScoreFnBundle {
    pub format: String,
    pub version: u32,
    pub id: String,
    pub name: String,
    #[serde(default)]
    pub description: String,
    #[serde(default)]
    pub tags: Vec<String>,
    pub source: String,
    #[serde(default)]
    pub author: String,
    #[serde(default)]
    pub created_at: u64,
}

#[derive(Clone)]
pub struct ScoreFnStore {
    root: PathBuf,
}

fn now_secs() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0)
}

fn normalize_owner(owner: Option<&str>) -> String {
    owner.map(|o| o.trim().to_lowercase()).unwrap_or_default()
}

/// "STEADY ROI!" → "steady-roi" — the id a published name gets.
fn slugify(name: &str) -> String {
    let mut out = String::new();
    for c in name.chars() {
        if c.is_ascii_alphanumeric() {
            out.push(c.to_ascii_lowercase());
        } else if (c == ' ' || c == '-' || c == '_') && !out.ends_with('-') && !out.is_empty() {
            out.push('-');
        }
        if out.len() >= 48 {
            break;
        }
    }
    out.trim_matches('-').to_string()
}

fn validate_id(id: &str) -> Result<()> {
    let ok = !id.is_empty()
        && id.len() <= 64
        && id.chars().all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_')
        && id.chars().next().map(|c| c.is_ascii_alphanumeric()).unwrap_or(false);
    if ok { Ok(()) } else { Err(anyhow!("bad score-fn id '{}'", id)) }
}

fn clean_tags(tags: &[String]) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    for t in tags {
        let t: String = t.trim().to_lowercase().chars().take(TAG_MAX).collect();
        if !t.is_empty() && !out.contains(&t) {
            out.push(t);
        }
        if out.len() >= TAGS_MAX {
            break;
        }
    }
    out
}

impl ScoreFnStore {
    /// Same data-root convention as [`crate::user_strats::UserStratStore`]:
    /// `POLYMARKET_DATA_DIR` in prod, `/tmp` for dev/tests.
    pub fn new() -> Self {
        let data_dir = std::env::var("POLYMARKET_DATA_DIR")
            .ok()
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from("/tmp"));
        let root = data_dir.join("polymarket-score-fns");
        let _ = fs::create_dir_all(&root);
        Self { root }
    }

    fn path(&self, id: &str) -> Result<PathBuf> {
        validate_id(id)?;
        Ok(self.root.join(format!("{}.json", id)))
    }

    fn load(&self, id: &str) -> Result<ScoreFnRec> {
        let path = self.path(id)?;
        let raw = fs::read_to_string(&path).with_context(|| format!("score fn '{}' not found", id))?;
        serde_json::from_str(&raw).context("parse score fn record")
    }

    fn save(&self, rec: &ScoreFnRec) -> Result<()> {
        let path = self.path(&rec.id)?;
        let json = serde_json::to_string_pretty(rec).context("serialize score fn")?;
        fs::write(&path, json).context("write score fn")
    }

    /// Publish (create or owner-overwrite). The id comes from the name so
    /// re-publishing "STEADY ROI 2" updates in place instead of piling up.
    pub fn publish(
        &self,
        owner: &str,
        name: &str,
        description: &str,
        tags: &[String],
        source: &str,
        forked_from: Option<String>,
    ) -> Result<ScoreFnRec> {
        let owner = normalize_owner(Some(owner));
        if owner.is_empty() {
            return Err(anyhow!("publishing needs a connected wallet (owner)"));
        }
        let name: String = name.trim().chars().take(NAME_MAX).collect();
        if name.is_empty() {
            return Err(anyhow!("a score fn needs a name"));
        }
        let source = source.trim();
        if source.is_empty() {
            return Err(anyhow!("nothing to publish — the score box is empty"));
        }
        if source.len() > SOURCE_MAX {
            return Err(anyhow!("score source too large ({} chars; max {})", source.len(), SOURCE_MAX));
        }
        let id = slugify(&name);
        validate_id(&id).map_err(|_| anyhow!("name '{}' doesn't reduce to a usable id", name))?;

        let existing = self.load(&id).ok();
        if let Some(prev) = &existing {
            if !prev.owner.is_empty() && prev.owner != owner {
                return Err(anyhow!("'{}' is already published by someone else — rename yours", name));
            }
        }
        let created_at = existing.as_ref().map(|p| p.created_at).filter(|t| *t > 0).unwrap_or_else(now_secs);
        let rec = ScoreFnRec {
            id,
            owner: owner.clone(),
            name,
            description: description.trim().chars().take(DESC_MAX).collect(),
            tags: clean_tags(tags),
            source: source.to_string(),
            author: existing.map(|p| p.author).filter(|a| !a.is_empty()).unwrap_or(owner),
            forked_from,
            created_at,
            updated_at: now_secs(),
        };
        self.save(&rec)?;
        Ok(rec)
    }

    /// Every listing, newest first, tagged `mine` relative to the viewer.
    pub fn list(&self, viewer: Option<&str>) -> Result<Vec<ScoreFnEntry>> {
        let viewer = normalize_owner(viewer);
        let mut out = Vec::new();
        for entry in fs::read_dir(&self.root).context("read score-fns dir")? {
            let Ok(entry) = entry else { continue };
            let path = entry.path();
            if path.extension().and_then(|e| e.to_str()) != Some("json") {
                continue;
            }
            let Ok(raw) = fs::read_to_string(&path) else { continue };
            let Ok(rec) = serde_json::from_str::<ScoreFnRec>(&raw) else { continue };
            let mine = !viewer.is_empty() && rec.owner == viewer;
            out.push(ScoreFnEntry { rec, mine });
        }
        out.sort_by(|a, b| b.rec.updated_at.cmp(&a.rec.updated_at));
        Ok(out)
    }

    pub fn delete(&self, id: &str, owner: &str) -> Result<()> {
        let rec = self.load(id)?;
        let owner = normalize_owner(Some(owner));
        if rec.owner != owner {
            return Err(anyhow!("only the publisher can delete '{}'", id));
        }
        fs::remove_file(self.path(id)?).context("delete score fn")
    }

    pub fn bundle(&self, id: &str) -> Result<ScoreFnBundle> {
        let rec = self.load(id)?;
        Ok(ScoreFnBundle {
            format: BUNDLE_FORMAT.to_string(),
            version: BUNDLE_VERSION,
            id: rec.id,
            name: rec.name,
            description: rec.description,
            tags: rec.tags,
            source: rec.source,
            author: rec.author,
            created_at: rec.created_at,
        })
    }

    /// Import a bundle fetched by CID as the caller's own listing (provenance
    /// kept in `author`/`forked_from`). Id clashes suffix `-2`, `-3`, …
    pub fn import_bundle(&self, bundle: &ScoreFnBundle, owner: &str) -> Result<ScoreFnRec> {
        if bundle.format != BUNDLE_FORMAT {
            return Err(anyhow!("that CID is not a shared score fn (format '{}')", bundle.format));
        }
        if bundle.version > BUNDLE_VERSION {
            return Err(anyhow!("bundle version {} is newer than this deploy understands", bundle.version));
        }
        let owner_norm = normalize_owner(Some(owner));
        if owner_norm.is_empty() {
            return Err(anyhow!("importing needs a connected wallet (owner)"));
        }
        let mut name = bundle.name.clone();
        // Walk to a free (or self-owned) name so imports never clobber a
        // stranger's listing.
        for n in 2..10 {
            let id = slugify(&name);
            match self.load(&id) {
                Ok(prev) if prev.owner != owner_norm => {
                    name = format!("{} {}", bundle.name, n);
                }
                _ => break,
            }
        }
        let mut rec = self.publish(
            owner,
            &name,
            &bundle.description,
            &bundle.tags,
            &bundle.source,
            Some(bundle.id.clone()),
        )?;
        if !bundle.author.is_empty() {
            rec.author = normalize_owner(Some(&bundle.author));
            self.save(&rec)?;
        }
        Ok(rec)
    }
}

impl Default for ScoreFnStore {
    fn default() -> Self {
        Self::new()
    }
}

// ─── Routes ──────────────────────────────────────────────────────────────

pub fn router() -> Router<AppState> {
    Router::new()
        // The community shelf: list is open, publish needs an owner.
        .route("/score-fns", get(score_fns_list).post(score_fns_publish))
        // Static segment before the `/:id/…` routes (axum matches in order).
        .route("/score-fns/import", post(score_fns_import))
        .route("/score-fns/:id/share", post(score_fns_share))
        .route("/score-fns/:id", axum::routing::delete(score_fns_delete))
}

#[derive(Deserialize)]
struct ViewerQuery {
    owner: Option<String>,
}

async fn score_fns_list(
    State(state): State<AppState>,
    Query(q): Query<ViewerQuery>,
) -> impl IntoResponse {
    match state.score_fns.list(q.owner.as_deref()) {
        Ok(fns) => Json(json!({ "fns": fns })).into_response(),
        Err(e) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({"error": format!("score-fns: {}", e)})),
        ).into_response(),
    }
}

#[derive(Deserialize)]
struct PublishFnBody {
    owner: String,
    name: String,
    #[serde(default)]
    description: String,
    #[serde(default)]
    tags: Vec<String>,
    source: String,
}

async fn score_fns_publish(
    State(state): State<AppState>,
    Json(req): Json<PublishFnBody>,
) -> impl IntoResponse {
    match state.score_fns.publish(&req.owner, &req.name, &req.description, &req.tags, &req.source, None) {
        Ok(rec) => Json(rec).into_response(),
        Err(e) => (
            StatusCode::BAD_REQUEST,
            Json(json!({"error": format!("publish: {}", e)})),
        ).into_response(),
    }
}

#[derive(Deserialize)]
struct OwnerOnlyQuery {
    owner: String,
}

async fn score_fns_delete(
    State(state): State<AppState>,
    Path(id): Path<String>,
    Query(q): Query<OwnerOnlyQuery>,
) -> impl IntoResponse {
    match state.score_fns.delete(&id, &q.owner) {
        Ok(()) => Json(json!({"ok": true})).into_response(),
        Err(e) => (
            StatusCode::BAD_REQUEST,
            Json(json!({"error": format!("delete: {}", e)})),
        ).into_response(),
    }
}

/// Bundle → content-addressable store → CID, exactly like strat sharing.
async fn score_fns_share(
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> impl IntoResponse {
    let bundle = match state.score_fns.bundle(&id) {
        Ok(b) => b,
        Err(e) => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"error": format!("share: {}", e)})),
            ).into_response()
        }
    };
    let bytes = match serde_json::to_vec(&bundle) {
        Ok(b) => b,
        Err(e) => {
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"error": format!("serialize bundle: {}", e)})),
            ).into_response()
        }
    };
    match state.share.put_and_pin(&state.http, bytes).await {
        Ok(cid) => Json(json!({
            "ok": true,
            "cid": cid,
            "id": id,
            "backend": state.share.label(),
        })).into_response(),
        Err(e) => (
            StatusCode::BAD_GATEWAY,
            Json(json!({"error": format!("share store unavailable: {}", e)})),
        ).into_response(),
    }
}

#[derive(Deserialize)]
struct ImportFnBody {
    cid: String,
    owner: String,
}

async fn score_fns_import(
    State(state): State<AppState>,
    Json(req): Json<ImportFnBody>,
) -> impl IntoResponse {
    let bytes = match state.share.get(&state.http, req.cid.trim()).await {
        Ok(b) => b,
        Err(e) => {
            return (
                StatusCode::BAD_GATEWAY,
                Json(json!({"error": format!("fetch cid: {}", e)})),
            ).into_response()
        }
    };
    let bundle: ScoreFnBundle = match serde_json::from_slice(&bytes) {
        Ok(b) => b,
        Err(e) => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"error": format!("that CID is not a score-fn bundle: {}", e)})),
            ).into_response()
        }
    };
    match state.score_fns.import_bundle(&bundle, &req.owner) {
        Ok(rec) => Json(rec).into_response(),
        Err(e) => (
            StatusCode::BAD_REQUEST,
            Json(json!({"error": format!("import: {}", e)})),
        ).into_response(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn store() -> ScoreFnStore {
        use std::sync::atomic::{AtomicU32, Ordering};
        static SEQ: AtomicU32 = AtomicU32::new(0);
        let root = std::env::temp_dir().join(format!(
            "score-fns-test-{}-{}-{}",
            std::process::id(),
            now_secs(),
            SEQ.fetch_add(1, Ordering::Relaxed),
        ));
        let _ = fs::create_dir_all(&root);
        ScoreFnStore { root }
    }

    #[test]
    fn publish_list_delete_roundtrip() {
        let s = store();
        let rec = s
            .publish("0xAbC", "Steady ROI", "roi gated on consistency", &["roi".into(), "ROI".into()], "return 1", None)
            .unwrap();
        assert_eq!(rec.id, "steady-roi");
        assert_eq!(rec.owner, "0xabc");
        assert_eq!(rec.tags, vec!["roi"]); // deduped, lowercased
        let listed = s.list(Some("0xABC")).unwrap();
        assert_eq!(listed.len(), 1);
        assert!(listed[0].mine);
        // A different owner can't overwrite or delete.
        assert!(s.publish("0xdef", "Steady ROI", "", &[], "return 2", None).is_err());
        assert!(s.delete("steady-roi", "0xdef").is_err());
        s.delete("steady-roi", "0xabc").unwrap();
        assert!(s.list(None).unwrap().is_empty());
    }

    #[test]
    fn import_avoids_clobbering_strangers() {
        let s = store();
        s.publish("0xaaa", "Grinder", "", &[], "return 1", None).unwrap();
        let bundle = ScoreFnBundle {
            format: BUNDLE_FORMAT.into(),
            version: BUNDLE_VERSION,
            id: "grinder".into(),
            name: "Grinder".into(),
            description: "".into(),
            tags: vec![],
            source: "return 2".into(),
            author: "0xccc".into(),
            created_at: 1,
        };
        let rec = s.import_bundle(&bundle, "0xbbb").unwrap();
        assert_eq!(rec.id, "grinder-2");
        assert_eq!(rec.owner, "0xbbb");
        assert_eq!(rec.author, "0xccc"); // provenance kept
        assert_eq!(rec.forked_from.as_deref(), Some("grinder"));
        // Wrong format rejected.
        let mut bad = bundle.clone();
        bad.format = "polymarket.strat".into();
        assert!(s.import_bundle(&bad, "0xbbb").is_err());
    }
}
