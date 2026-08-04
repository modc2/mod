//! Task vault — optional password encryption of one wallet's own task ledger.
//!
//! The jobs ledger is a public code trail by design: `/jobs`, `/jobs/:id`,
//! `/jobs/:id/stream` and `/tasks/:cid` answer without auth, and finished
//! tasks are published to the shared blob store as CIDs. Anyone who'd rather
//! not publish what they ask for can turn on a vault. From then on their
//! prompts, task output and errors are stored — and published — as ciphertext,
//! and only their own signed-in session, with the password entered, ever sees
//! them in the clear.
//!
//! Keys:
//!   password ──PBKDF2──▶ KEK ──unwraps──▶ DEK (32 random bytes, per wallet)
//!                                          └─ seals every prompt/output chunk
//!
//! The record (`~/.mod/build-fork/vault/<address>.json`, 0600) holds the salt and
//! the WRAPPED data key — never the password, never the bare key. Unlocking
//! puts the data key in process memory only. Signing out drops it, and so does
//! any api restart: that is the deal, re-enter the password each session. Lose
//! the password and the ciphertext stays ciphertext — there is no back door,
//! by construction (the server has nothing to recover it from).
//!
//! Rotating re-wraps the SAME data key under a new password, so changing your
//! password costs one small write instead of re-encrypting the whole ledger.
//!
//! Sealed: prompt, output, error — the content. Clear: id, status, model,
//! timestamps, work_dir — what the console schedules and routes on, and what
//! makes the ledger a ledger. Files a task writes are NOT sealed; the agent
//! has to read and write them as itself. This encrypts the record of the work,
//! not the work.

use crate::jobs::{ClaudeJob, ClaudeJobManager};
use axum::{extract::State, http::HeaderMap, http::StatusCode, response::IntoResponse, Json};
use base64::Engine;
use serde::{Deserialize, Serialize};
use serde_json::json;
use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::{Arc, OnceLock, RwLock};

/// Prefix of one sealed chunk inside a stored field. Sealing is line-oriented
/// so `output = output || ?` still appends in SQL — each chunk is a
/// self-contained base64 line, and a field can hold a mix of plaintext (written
/// before the vault existed) and sealed lines.
const TAG: &str = "BLDENC1:";

/// What a reader without the key gets in place of sealed content.
pub const LOCKED_NOTE: &str = "🔒 sealed by its owner's task vault";

/// Salt length for the password → KEK stretch (privacy::encrypt reads it).
const SALT_LEN: usize = 16;

/// Short enough passwords aren't worth the false sense of safety.
const MIN_PASSWORD: usize = 8;

// ── Record ───────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct VaultRecord {
    pub address: String,
    pub enabled: bool,
    /// Hex, 16 bytes — PBKDF2 salt, fixed per wallet.
    pub salt: String,
    /// Hex of the wrapped data key: `privacy::encrypt(password, salt, dek)`.
    /// Unwrapping it IS the password check — a wrong password fails the MAC.
    pub dek: String,
    pub created: u64,
    pub updated: u64,
    #[serde(default)]
    pub rotations: u32,
}

fn now_ts() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

fn dir() -> Option<PathBuf> {
    Some(crate::auth::private_dir()?.join("vault"))
}

/// One file per wallet, keyed by lowercased address. Never trust an address as
/// a path component: 0x-hex only (plus the no-auth `local` identity).
fn record_path(address: &str) -> Option<PathBuf> {
    let addr = address.trim().to_lowercase();
    let hexish = addr.chars().all(|c| c.is_ascii_hexdigit() || c == 'x');
    if addr.is_empty() || (!hexish && addr != "local") {
        return None;
    }
    Some(dir()?.join(format!("{addr}.json")))
}

pub fn record(address: &str) -> Option<VaultRecord> {
    let raw = std::fs::read_to_string(record_path(address)?).ok()?;
    serde_json::from_str(&raw).ok()
}

fn save(rec: &VaultRecord) -> Result<(), String> {
    let path = record_path(&rec.address).ok_or("bad address")?;
    let d = path.parent().ok_or("bad vault dir")?.to_path_buf();
    std::fs::create_dir_all(&d).map_err(|e| format!("mkdir vault: {e}"))?;
    let json = serde_json::to_vec_pretty(rec).map_err(|e| format!("record serialize: {e}"))?;
    std::fs::write(&path, json).map_err(|e| format!("record write: {e}"))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(&d, std::fs::Permissions::from_mode(0o700));
        let _ = std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600));
    }
    Ok(())
}

fn remove_record(address: &str) -> Result<(), String> {
    let path = record_path(address).ok_or("bad address")?;
    match std::fs::remove_file(&path) {
        Ok(()) => Ok(()),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(e) => Err(format!("remove: {e}")),
    }
}

/// True when this wallet's ledger is sealed (or should be from now on).
pub fn is_enabled(address: &str) -> bool {
    !address.is_empty() && record(address).map(|r| r.enabled).unwrap_or(false)
}

// ── Unlocked keys (memory only) ──────────────────────────────────────

#[derive(Default)]
struct Keys {
    /// address → data key, put here by /vault/unlock, dropped by /vault/lock.
    by_addr: HashMap<String, [u8; 32]>,
    /// job id → data key. A task that was submitted unlocked keeps sealing its
    /// output to the end even if the person signs out while it runs — the key
    /// it started with lives exactly as long as the job.
    by_job: HashMap<String, [u8; 32]>,
}

fn keys() -> &'static RwLock<Keys> {
    static KEYS: OnceLock<RwLock<Keys>> = OnceLock::new();
    KEYS.get_or_init(|| RwLock::new(Keys::default()))
}

fn read_keys() -> std::sync::RwLockReadGuard<'static, Keys> {
    keys().read().unwrap_or_else(|e| e.into_inner())
}

fn write_keys() -> std::sync::RwLockWriteGuard<'static, Keys> {
    keys().write().unwrap_or_else(|e| e.into_inner())
}

pub fn session_key(address: &str) -> Option<[u8; 32]> {
    if address.is_empty() {
        return None;
    }
    read_keys().by_addr.get(&address.to_lowercase()).copied()
}

pub fn is_unlocked(address: &str) -> bool {
    session_key(address).is_some()
}

/// Give a job its own handle on the submitter's key.
pub fn bind_job(job_id: &str, address: &str) {
    if let Some(key) = session_key(address) {
        write_keys().by_job.insert(job_id.to_string(), key);
    }
}

/// Drop a finished job's key handle (called from the runners' terminal tail).
pub fn release_job(job_id: &str) {
    write_keys().by_job.remove(job_id);
}

pub fn job_key(job_id: &str) -> Option<[u8; 32]> {
    read_keys().by_job.get(job_id).copied()
}

/// Unwrap the data key with `password` and hold it for this address.
pub fn unlock(address: &str, password: &str) -> Result<(), String> {
    let rec = record(address).ok_or("no vault for this wallet")?;
    let blob = hex::decode(&rec.dek).map_err(|e| format!("bad vault record: {e}"))?;
    let dek = crate::privacy::decrypt(password, &blob).map_err(|_| "wrong password".to_string())?;
    let key: [u8; 32] = dek.try_into().map_err(|_| "bad vault record: key length")?;
    write_keys().by_addr.insert(address.to_lowercase(), key);
    Ok(())
}

/// Forget this address's key. Every sign-out calls this; so does an api
/// restart, implicitly, because the map only ever lived in memory.
pub fn lock(address: &str) {
    write_keys().by_addr.remove(&address.to_lowercase());
}

// ── Sealing text fields ──────────────────────────────────────────────

fn b64() -> base64::engine::general_purpose::GeneralPurpose {
    base64::engine::general_purpose::STANDARD_NO_PAD
}

/// Seal `text` into one `BLDENC1:<base64>` line.
pub fn seal_text(key: &[u8; 32], text: &str) -> String {
    format!("{TAG}{}\n", b64().encode(crate::privacy::seal(key, text.as_bytes())))
}

/// True when any line of `stored` is a sealed chunk.
pub fn is_sealed(stored: &str) -> bool {
    stored.starts_with(TAG) || stored.contains(&format!("\n{TAG}"))
}

/// Sealed chunks → plaintext; anything else passes through untouched, so a
/// field written part before / part after the vault was switched on still
/// reads end to end.
pub fn open_text(key: &[u8; 32], stored: &str) -> String {
    if !is_sealed(stored) {
        return stored.to_string();
    }
    let mut out = String::with_capacity(stored.len());
    for line in stored.split_inclusive('\n') {
        let body = line.strip_suffix('\n').unwrap_or(line);
        match body.strip_prefix(TAG) {
            Some(payload) => match b64()
                .decode(payload.trim())
                .map_err(|e| e.to_string())
                .and_then(|blob| crate::privacy::open(key, &blob))
            {
                Ok(bytes) => out.push_str(&String::from_utf8_lossy(&bytes)),
                Err(_) => out.push_str("⟪unreadable chunk⟫"),
            },
            None => out.push_str(line),
        }
    }
    out
}

/// What a reader without the key sees: the plaintext parts (if any) plus one
/// note where the sealed content is — not a wall of repeated lock icons.
pub fn mask(stored: &str) -> String {
    if !is_sealed(stored) {
        return stored.to_string();
    }
    let clear: String = stored
        .split_inclusive('\n')
        .filter(|l| !l.strip_suffix('\n').unwrap_or(l).starts_with(TAG))
        .collect();
    if clear.trim().is_empty() {
        LOCKED_NOTE.to_string()
    } else {
        format!("{clear}{LOCKED_NOTE}\n")
    }
}

/// Seal a value a job is about to write, when that job is running under a
/// vault. Plain passthrough otherwise — an unvaulted ledger is byte-for-byte
/// what it always was.
pub fn seal_for_job(job_id: &str, text: &str) -> String {
    match job_key(job_id) {
        Some(key) if !text.is_empty() => seal_text(&key, text),
        _ => text.to_string(),
    }
}

// ── Reading jobs back ────────────────────────────────────────────────

/// True when any of this task's content is sealed.
pub fn is_job_sealed(job: &ClaudeJob) -> bool {
    is_sealed(&job.prompt)
        || is_sealed(&job.output)
        || job.error.as_deref().map(is_sealed).unwrap_or(false)
}

/// Decrypt a job's fields for `caller`, or mask them. The key is only ever
/// applied for the job's own author with a live unlocked session — the owner
/// browsing the ledger, or any anonymous reader, gets the masked form.
pub fn unmask_job(job: &mut ClaudeJob, caller: &str) {
    if !is_job_sealed(job) {
        return;
    }
    job.encrypted = true;
    let key = (!job.user_address.is_empty()
        && caller.to_lowercase() == job.user_address.to_lowercase())
    .then(|| session_key(&job.user_address))
    .flatten();
    match key {
        Some(k) => {
            job.prompt = open_text(&k, &job.prompt);
            job.output = open_text(&k, &job.output);
            job.error = job.error.as_deref().map(|e| open_text(&k, e));
        }
        None => {
            job.locked = true;
            job.prompt = mask(&job.prompt);
            job.output = mask(&job.output);
            job.error = job.error.as_deref().map(mask);
        }
    }
}

/// Same treatment for a task bundle pulled back out of the blob store by CID
/// (`GET /tasks/:cid`): the published bundle carries the sealed text, so a
/// replay QR still replays — for its author, unlocked, and for nobody else.
pub fn unmask_bundle(bundle: &mut serde_json::Value, caller: &str) {
    let author = bundle
        .get("user_address")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    let fields = ["prompt", "output", "error"];
    let sealed = fields.iter().any(|f| {
        bundle.get(*f).and_then(|v| v.as_str()).map(is_sealed).unwrap_or(false)
    });
    if !sealed {
        return;
    }
    let key = (!author.is_empty() && caller.to_lowercase() == author.to_lowercase())
        .then(|| session_key(&author))
        .flatten();
    for f in fields {
        let Some(text) = bundle.get(f).and_then(|v| v.as_str()).map(str::to_string) else {
            continue;
        };
        let next = match &key {
            Some(k) => open_text(k, &text),
            None => mask(&text),
        };
        bundle[f] = json!(next);
    }
    bundle["encrypted"] = json!(true);
    bundle["locked"] = json!(key.is_none());
}

// ── HTTP handlers ────────────────────────────────────────────────────

type AppState = Arc<ClaudeJobManager>;

fn caller(headers: &HeaderMap) -> Result<String, (StatusCode, Json<serde_json::Value>)> {
    match crate::auth::extract_address_from_headers(headers) {
        Ok(addr) if !addr.is_empty() => Ok(addr.to_lowercase()),
        _ => Err((
            StatusCode::UNAUTHORIZED,
            Json(json!({
                "error": "the task vault is per-wallet — sign in with a wallet first",
                "available": false
            })),
        )),
    }
}

fn check_password(password: &str) -> Result<(), (StatusCode, Json<serde_json::Value>)> {
    if password.chars().count() < MIN_PASSWORD {
        return Err((
            StatusCode::BAD_REQUEST,
            Json(json!({
                "error": format!("password must be at least {MIN_PASSWORD} characters")
            })),
        ));
    }
    Ok(())
}

fn status_json(mgr: &ClaudeJobManager, addr: &str) -> serde_json::Value {
    let rec = record(addr);
    let (total, sealed) = mgr.vault_counts(addr);
    json!({
        "available": true,
        "address": addr,
        "enabled": rec.as_ref().map(|r| r.enabled).unwrap_or(false),
        "unlocked": is_unlocked(addr),
        "created": rec.as_ref().map(|r| r.created),
        "updated": rec.as_ref().map(|r| r.updated),
        "rotations": rec.as_ref().map(|r| r.rotations).unwrap_or(0),
        "tasks": { "total": total, "sealed": sealed },
    })
}

#[derive(Deserialize)]
pub struct PasswordBody {
    #[serde(default)]
    pub password: String,
    /// Only used by /vault/rotate.
    #[serde(default)]
    pub new_password: String,
}

/// GET /vault — is there a vault, is it open, how much is sealed.
pub async fn status(headers: HeaderMap, State(mgr): State<AppState>) -> impl IntoResponse {
    match caller(&headers) {
        Ok(addr) => (StatusCode::OK, Json(status_json(&mgr, &addr))).into_response(),
        // Local mode / no wallet: report "no vault here" rather than an error,
        // so the console can just not offer the card.
        Err(_) => (
            StatusCode::OK,
            Json(json!({ "available": false, "enabled": false, "unlocked": false })),
        )
            .into_response(),
    }
}

/// POST /vault/enable — mint the key, seal everything already in the ledger.
pub async fn enable(
    headers: HeaderMap,
    State(mgr): State<AppState>,
    Json(body): Json<PasswordBody>,
) -> impl IntoResponse {
    let addr = match caller(&headers) {
        Ok(a) => a,
        Err(e) => return e.into_response(),
    };
    if let Err(e) = check_password(&body.password) {
        return e.into_response();
    }
    if record(&addr).map(|r| r.enabled).unwrap_or(false) {
        return (
            StatusCode::CONFLICT,
            Json(json!({ "error": "this wallet already has a vault — unlock or rotate it" })),
        )
            .into_response();
    }

    // Re-enabling a vault that was disabled: keep the old salt so nothing
    // about the identity of the record changes, but mint a fresh data key —
    // disabling decrypted the ledger, so no old ciphertext depends on it.
    use rand::RngCore;
    let mut salt = [0u8; SALT_LEN];
    rand::rngs::OsRng.fill_bytes(&mut salt);
    let mut dek = [0u8; 32];
    rand::rngs::OsRng.fill_bytes(&mut dek);
    let wrapped = crate::privacy::encrypt(&body.password, &salt, &dek);

    let ts = now_ts();
    let rec = VaultRecord {
        address: addr.clone(),
        enabled: true,
        salt: hex::encode(salt),
        dek: hex::encode(&wrapped),
        created: record(&addr).map(|r| r.created).unwrap_or(ts),
        updated: ts,
        rotations: 0,
    };
    if let Err(e) = save(&rec) {
        return (StatusCode::INTERNAL_SERVER_ERROR, Json(json!({ "error": e }))).into_response();
    }
    write_keys().by_addr.insert(addr.clone(), dek);

    // Seal what's already there, and hand the key to anything still running so
    // a task submitted a minute ago finishes into the vault, not beside it.
    let sealed = mgr.seal_existing(&addr, &dek);
    for id in mgr.active_job_ids(&addr) {
        bind_job(&id, &addr);
    }
    (
        StatusCode::OK,
        Json(json!({ "ok": true, "sealed": sealed, "vault": status_json(&mgr, &addr) })),
    )
        .into_response()
}

/// POST /vault/unlock — the password prompt after every sign-in.
pub async fn unlock_handler(
    headers: HeaderMap,
    State(mgr): State<AppState>,
    Json(body): Json<PasswordBody>,
) -> impl IntoResponse {
    let addr = match caller(&headers) {
        Ok(a) => a,
        Err(e) => return e.into_response(),
    };
    match unlock(&addr, &body.password) {
        Ok(()) => (
            StatusCode::OK,
            Json(json!({ "ok": true, "vault": status_json(&mgr, &addr) })),
        )
            .into_response(),
        Err(e) if e == "wrong password" => {
            (StatusCode::UNAUTHORIZED, Json(json!({ "error": e }))).into_response()
        }
        Err(e) => (StatusCode::BAD_REQUEST, Json(json!({ "error": e }))).into_response(),
    }
}

/// POST /vault/lock — sign-out calls this; the key is gone until re-entered.
pub async fn lock_handler(headers: HeaderMap, State(mgr): State<AppState>) -> impl IntoResponse {
    let addr = match caller(&headers) {
        Ok(a) => a,
        Err(e) => return e.into_response(),
    };
    lock(&addr);
    (
        StatusCode::OK,
        Json(json!({ "ok": true, "vault": status_json(&mgr, &addr) })),
    )
        .into_response()
}

/// POST /vault/rotate — new password, same data key: one write, no re-encrypt.
pub async fn rotate(
    headers: HeaderMap,
    State(mgr): State<AppState>,
    Json(body): Json<PasswordBody>,
) -> impl IntoResponse {
    let addr = match caller(&headers) {
        Ok(a) => a,
        Err(e) => return e.into_response(),
    };
    if let Err(e) = check_password(&body.new_password) {
        return e.into_response();
    }
    let Some(mut rec) = record(&addr) else {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({ "error": "no vault for this wallet" })),
        )
            .into_response();
    };
    // The old password is proved by unwrapping, never by a stored copy.
    let blob = match hex::decode(&rec.dek) {
        Ok(b) => b,
        Err(e) => {
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({ "error": format!("bad vault record: {e}") })),
            )
                .into_response()
        }
    };
    let dek = match crate::privacy::decrypt(&body.password, &blob) {
        Ok(d) => d,
        Err(_) => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({ "error": "wrong current password" })),
            )
                .into_response()
        }
    };
    let mut salt = [0u8; SALT_LEN];
    use rand::RngCore;
    rand::rngs::OsRng.fill_bytes(&mut salt);
    rec.salt = hex::encode(salt);
    rec.dek = hex::encode(crate::privacy::encrypt(&body.new_password, &salt, &dek));
    rec.rotations += 1;
    rec.updated = now_ts();
    if let Err(e) = save(&rec) {
        return (StatusCode::INTERNAL_SERVER_ERROR, Json(json!({ "error": e }))).into_response();
    }
    // Rotating leaves you signed in and unlocked — the key didn't change.
    if let Ok(key) = <[u8; 32]>::try_from(dek) {
        write_keys().by_addr.insert(addr.clone(), key);
    }
    (
        StatusCode::OK,
        Json(json!({ "ok": true, "vault": status_json(&mgr, &addr) })),
    )
        .into_response()
}

/// POST /vault/disable — decrypt the ledger back to plaintext and drop the
/// record. Needs the password: the server can't do it without the key either.
pub async fn disable(
    headers: HeaderMap,
    State(mgr): State<AppState>,
    Json(body): Json<PasswordBody>,
) -> impl IntoResponse {
    let addr = match caller(&headers) {
        Ok(a) => a,
        Err(e) => return e.into_response(),
    };
    let Some(rec) = record(&addr) else {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({ "error": "no vault for this wallet" })),
        )
            .into_response();
    };
    let key = match hex::decode(&rec.dek)
        .map_err(|e| e.to_string())
        .and_then(|blob| crate::privacy::decrypt(&body.password, &blob))
        .and_then(|d| <[u8; 32]>::try_from(d).map_err(|_| "bad key length".to_string()))
    {
        Ok(k) => k,
        Err(_) => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({ "error": "wrong password" })),
            )
                .into_response()
        }
    };
    let opened = mgr.unseal_existing(&addr, &key);
    lock(&addr);
    if let Err(e) = remove_record(&addr) {
        return (StatusCode::INTERNAL_SERVER_ERROR, Json(json!({ "error": e }))).into_response();
    }
    (
        StatusCode::OK,
        Json(json!({ "ok": true, "opened": opened, "vault": status_json(&mgr, &addr) })),
    )
        .into_response()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn key() -> [u8; 32] {
        [4u8; 32]
    }

    #[test]
    fn seal_open_roundtrip() {
        let k = key();
        let sealed = seal_text(&k, "write me a parser");
        assert!(is_sealed(&sealed));
        assert!(!sealed.contains("parser"));
        assert_eq!(open_text(&k, &sealed), "write me a parser");
    }

    #[test]
    fn appended_chunks_concatenate() {
        // Exactly what SQL `output = output || ?` produces.
        let k = key();
        let stored = format!("{}{}", seal_text(&k, "line one\n"), seal_text(&k, "line two\n"));
        assert_eq!(open_text(&k, &stored), "line one\nline two\n");
    }

    #[test]
    fn plaintext_written_before_the_vault_still_reads() {
        let k = key();
        let stored = format!("old plaintext\n{}", seal_text(&k, "new secret\n"));
        assert_eq!(open_text(&k, &stored), "old plaintext\nnew secret\n");
        // And a reader without the key keeps the old part, loses the new.
        let masked = mask(&stored);
        assert!(masked.contains("old plaintext"));
        assert!(!masked.contains("new secret"));
        assert!(masked.contains(LOCKED_NOTE));
    }

    #[test]
    fn wrong_key_never_yields_plaintext() {
        let stored = seal_text(&key(), "top secret");
        let out = open_text(&[5u8; 32], &stored);
        assert!(!out.contains("top secret"));
        assert!(out.contains("unreadable"));
    }

    #[test]
    fn unsealed_text_passes_through_untouched() {
        let plain = "just a normal prompt\nwith lines\n";
        assert!(!is_sealed(plain));
        assert_eq!(open_text(&key(), plain), plain);
        assert_eq!(mask(plain), plain);
    }
}
