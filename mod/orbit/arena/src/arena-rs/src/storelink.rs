//! The bridge to the store module — every module here is also an object there.
//!
//! The arena keeps bytes under their SHA-256 in its own blob store, and that
//! is the id everything else refers to. This file makes each of those blobs an
//! object in the fleet's **store** module as well: the bytes are uploaded as a
//! public object (the store hashes them again into an IPFS CID), and the CID
//! is kept beside the id in the registry. So every game and player has two
//! hashes that agree about the same bytes — the arena's SHA-256 and the
//! store's CID — and a page in the store where anyone can read it back.
//!
//! What this file deliberately does not do:
//!
//!   * hold a credential. The store wants a mod-protocol token; this asks the
//!     box's own key for one (`python3 -I -c "import mod…"`) the same way the
//!     CLI would, keeps it in memory, and mints a new one when the store says
//!     it has expired. `ARENA_STORE_TOKEN` or `~/.mod/arena/store_token`
//!     override that when the box is not the signer you want.
//!   * make a request the arena depends on. A push runs in the background
//!     after an upload and again at startup for anything unpushed; a store
//!     that is asleep or absent leaves `cid` empty, never a module unstored.
//!   * trust the copy. `store_sync verify=true` reads each object back and
//!     hashes it; a CID whose bytes no longer hash to the id is reported.
//!
//! `ARENA_STORE_URL=off` turns the bridge off — the tests run that way, so a
//! test upload never lands in the real store.

use crate::{blobs, store};
use reqwest::header::{AUTHORIZATION, CONTENT_TYPE};
use serde_json::{json, Value};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Mutex, OnceLock};
use std::time::{Duration, Instant};

const DEFAULT_URL: &str = "http://127.0.0.1:9000/api/store";
const DEFAULT_PUBLIC: &str = "/api/store";
const DEFAULT_PAGE: &str = "/store";
/// A token is time-bounded by the store (7 days by default); this is well inside.
const TOKEN_TTL: Duration = Duration::from_secs(6 * 3600);
/// Chosen so it never appears in a wasm binary or a class by accident.
const BOUNDARY: &str = "----arena-storelink-7f3c1a9e2b";

static BOOTED: AtomicBool = AtomicBool::new(false);

/// Where the store answers, with the activator in front so a sleeping store
/// is woken rather than missed. `off`, `0` or empty disables the bridge.
pub fn url() -> Option<String> {
    match std::env::var("ARENA_STORE_URL") {
        Ok(v) => {
            let v = v.trim().trim_end_matches('/').to_string();
            if v.is_empty() || v == "off" || v == "0" || v == "false" {
                None
            } else {
                Some(v)
            }
        }
        Err(_) => Some(DEFAULT_URL.into()),
    }
}

pub fn enabled() -> bool {
    url().is_some()
}

/// The store API as a browser reaches it — relative, so it works behind the
/// fleet router without the console knowing the host.
fn public_api() -> String {
    std::env::var("ARENA_STORE_PUBLIC")
        .ok()
        .map(|v| v.trim_end_matches('/').to_string())
        .unwrap_or_else(|| DEFAULT_PUBLIC.into())
}

/// The store's own page for an object: info, preview, QR, who may read it.
pub fn page(cid: &str) -> String {
    let base = std::env::var("ARENA_STORE_PAGE")
        .ok()
        .map(|v| v.trim_end_matches('/').to_string())
        .unwrap_or_else(|| DEFAULT_PAGE.into());
    format!("{base}/o/{cid}")
}

/// The bytes, straight from the store.
pub fn get_url(cid: &str) -> String {
    format!("{}/get?cid={cid}", public_api())
}

/// What a module card says about its store copy. Empty `cid` → `null`, so a
/// reader can tell "not pushed yet" from "pushed" without parsing a string.
pub fn card(cid: &str) -> Value {
    if cid.is_empty() {
        return Value::Null;
    }
    json!({ "cid": cid, "get": get_url(cid), "page": page(cid) })
}

/// Bytes the store has not got, or a source it has not got beside them.
pub fn needs_push(m: &store::WasmModule) -> bool {
    m.cid.is_empty() || (!m.src.is_empty() && m.src_cid.is_empty())
}

/// Startup is over; uploads from here on push themselves.
pub fn set_booted() {
    BOOTED.store(true, Ordering::Relaxed);
}

/// Called by `put_module` after the registry write. Spawns and returns: the
/// upload that triggered this is answered by the arena, not by the store.
pub fn push_later(id: String) {
    if !enabled() || !BOOTED.load(Ordering::Relaxed) {
        return;
    }
    if let Ok(h) = tokio::runtime::Handle::try_current() {
        h.spawn(async move {
            if let Err(e) = push(&id).await {
                eprintln!("storelink: {id}: {e}");
            }
        });
    }
}

/// Called by `delete_module`. The arena's copy is gone; the store's goes too,
/// best-effort — a copy nobody can reach from here is only quota.
pub fn forget_later(cid: String) {
    if cid.is_empty() || !enabled() {
        return;
    }
    if let Ok(h) = tokio::runtime::Handle::try_current() {
        h.spawn(async move {
            if let Err(e) = forget(&cid).await {
                eprintln!("storelink: rm {cid}: {e}");
            }
        });
    }
}

// ── the client ────────────────────────────────────────────────────────────

fn client() -> &'static reqwest::Client {
    static C: OnceLock<reqwest::Client> = OnceLock::new();
    C.get_or_init(|| {
        reqwest::Client::builder()
            // Long enough for the activator to wake a sleeping store.
            .timeout(Duration::from_secs(60))
            .build()
            .unwrap_or_else(|_| reqwest::Client::new())
    })
}

struct Minted {
    value: String,
    at: Instant,
}

fn cache() -> &'static Mutex<Option<Minted>> {
    static T: OnceLock<Mutex<Option<Minted>>> = OnceLock::new();
    T.get_or_init(|| Mutex::new(None))
}

/// The module directory — where `python3 -I -c "import mod"` has to run from,
/// because the protocol refuses to load from a directory that is not a
/// project, and the arena's own `src/` shadows the package with `mod.py`.
fn module_dir() -> std::path::PathBuf {
    std::path::PathBuf::from(concat!(env!("CARGO_MANIFEST_DIR"), "/../.."))
}

fn mint_token() -> Result<String, String> {
    let out = std::process::Command::new("python3")
        .args(["-I", "-c", "import mod as m; print(m.mod('auth')().token({'mod': 'arena'}))"])
        .current_dir(module_dir())
        .output()
        .map_err(|e| format!("could not run python3 to mint a store token: {e}"))?;
    if !out.status.success() {
        let err = String::from_utf8_lossy(&out.stderr);
        return Err(format!(
            "minting a store token failed: {}",
            err.lines().last().unwrap_or("").trim()
        ));
    }
    let tok = String::from_utf8_lossy(&out.stdout).trim().to_string();
    if tok.is_empty() {
        return Err("minting a store token gave nothing back".into());
    }
    Ok(tok)
}

/// A mod-protocol token signed by this box's own key, for calls the arena
/// makes as itself — seating a fleet module that will only answer a signed-in
/// caller, say. The store's own overrides do not apply here: this is the box's
/// identity, not a store credential.
pub async fn protocol_token() -> Result<String, String> {
    static T: OnceLock<Mutex<Option<Minted>>> = OnceLock::new();
    let cell = T.get_or_init(|| Mutex::new(None));
    {
        let guard = cell.lock().unwrap_or_else(|e| e.into_inner());
        if let Some(m) = guard.as_ref() {
            if m.at.elapsed() < TOKEN_TTL {
                return Ok(m.value.clone());
            }
        }
    }
    let tok = tokio::task::spawn_blocking(mint_token)
        .await
        .map_err(|e| format!("the mint task failed: {e}"))??;
    *cell.lock().unwrap_or_else(|e| e.into_inner()) =
        Some(Minted { value: tok.clone(), at: Instant::now() });
    Ok(tok)
}

/// A token for the store: the environment, then a file beside the registry,
/// then one the box's own key signs. `fresh` throws the cached one away —
/// what the store's 401 asks for.
async fn token(fresh: bool) -> Result<String, String> {
    if let Ok(t) = std::env::var("ARENA_STORE_TOKEN") {
        if !t.trim().is_empty() {
            return Ok(t.trim().to_string());
        }
    }
    if let Ok(t) = std::fs::read_to_string(blobs::state_dir().join("store_token")) {
        if !t.trim().is_empty() {
            return Ok(t.trim().to_string());
        }
    }
    if !fresh {
        let guard = cache().lock().unwrap_or_else(|e| e.into_inner());
        if let Some(m) = guard.as_ref() {
            if m.at.elapsed() < TOKEN_TTL {
                return Ok(m.value.clone());
            }
        }
    }
    let tok = tokio::task::spawn_blocking(mint_token)
        .await
        .map_err(|e| format!("the mint task failed: {e}"))??;
    *cache().lock().unwrap_or_else(|e| e.into_inner()) =
        Some(Minted { value: tok.clone(), at: Instant::now() });
    Ok(tok)
}

/// One multipart/form-data body, by hand — the store takes a file and three
/// fields, which is not enough to earn a dependency.
pub fn multipart(fields: &[(&str, &str)], filename: &str, bytes: &[u8]) -> Vec<u8> {
    let mut body = Vec::with_capacity(bytes.len() + 512);
    for (name, value) in fields {
        body.extend_from_slice(
            format!("--{BOUNDARY}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n")
                .as_bytes(),
        );
    }
    let safe = filename.replace('"', "");
    body.extend_from_slice(
        format!(
            "--{BOUNDARY}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{safe}\"\r\n\
             Content-Type: application/octet-stream\r\n\r\n"
        )
        .as_bytes(),
    );
    body.extend_from_slice(bytes);
    body.extend_from_slice(format!("\r\n--{BOUNDARY}--\r\n").as_bytes());
    body
}

fn cid_of(reply: &Value) -> Option<String> {
    reply
        .get("results")
        .and_then(|r| r.as_object())
        .and_then(|o| o.values().find_map(|v| v.get("cid").and_then(|c| c.as_str())))
        .map(String::from)
}

/// Upload bytes as a public object. Returns the CID the store minted.
async fn upload(filename: &str, key: &str, bytes: &[u8]) -> Result<String, String> {
    let base = url().ok_or("the store bridge is off (ARENA_STORE_URL)")?;
    let mut tok = token(false).await?;
    for attempt in 0..2 {
        let body = multipart(
            &[("backend", "localfs"), ("public", "true"), ("key", key)],
            filename,
            bytes,
        );
        let r = client()
            .post(format!("{base}/put"))
            .header(AUTHORIZATION, format!("Bearer {tok}"))
            .header(CONTENT_TYPE, format!("multipart/form-data; boundary={BOUNDARY}"))
            .body(body)
            .send()
            .await
            .map_err(|e| format!("the store did not answer: {e}"))?;
        let status = r.status();
        let text = r.text().await.unwrap_or_default();
        if status.as_u16() == 401 && attempt == 0 {
            tok = token(true).await?;
            continue;
        }
        if !status.is_success() {
            let detail = serde_json::from_str::<Value>(&text)
                .ok()
                .and_then(|v| v.get("detail").or(v.get("error")).map(|d| d.to_string()))
                .unwrap_or_else(|| text.chars().take(200).collect());
            return Err(format!("the store said {status}: {detail}"));
        }
        let v: Value = serde_json::from_str(&text)
            .map_err(|_| format!("the store answered with something other than JSON: {}", text.chars().take(120).collect::<String>()))?;
        return cid_of(&v).ok_or_else(|| format!("the store stored it but named no cid: {v}"));
    }
    Err("the store refused the token twice".into())
}

async fn forget(cid: &str) -> Result<(), String> {
    let base = url().ok_or("the store bridge is off")?;
    let tok = token(false).await?;
    let r = client()
        .delete(format!("{base}/rm?cid={cid}"))
        .header(AUTHORIZATION, format!("Bearer {tok}"))
        .send()
        .await
        .map_err(|e| format!("the store did not answer: {e}"))?;
    if r.status().is_success() || r.status().as_u16() == 404 {
        Ok(())
    } else {
        Err(format!("the store said {}", r.status()))
    }
}

/// Read an object back and say whether its bytes still hash to `id`.
async fn verify(cid: &str, id: &str) -> Result<bool, String> {
    let base = url().ok_or("the store bridge is off")?;
    let r = client()
        .get(format!("{base}/get?cid={cid}"))
        .send()
        .await
        .map_err(|e| format!("the store did not answer: {e}"))?;
    if !r.status().is_success() {
        return Err(format!("the store said {} for {cid}", r.status()));
    }
    let bytes = r.bytes().await.map_err(|e| format!("reading {cid}: {e}"))?;
    Ok(blobs::hash(&bytes) == id)
}

fn filename(name: &str, lang: &str) -> String {
    let stem: String = name
        .chars()
        .map(|c| if c.is_ascii_alphanumeric() || c == '-' || c == '_' { c } else { '-' })
        .collect();
    let stem = if stem.is_empty() { "module".to_string() } else { stem };
    let ext = match lang {
        "python" => "py",
        "rust" => "rs",
        _ => "wasm",
    };
    format!("{stem}.{ext}")
}

// ── the operations ────────────────────────────────────────────────────────

/// Push one module: its bytes, and its readable source if it carries one
/// separately (a wasm example with the Rust it was built from). Each lands
/// under the arena's own key for it, `arena/<sha256>`, so the store's index
/// and the arena's registry name the same thing.
pub async fn push(id: &str) -> Result<Value, String> {
    let m = store::read(|s| s.modules.get(id).cloned())
        .ok_or_else(|| format!("no module `{id}`"))?;
    let bytes = blobs::get(&m.id)?;
    let cid = upload(&filename(&m.name, m.lang()), &format!("arena/{}", m.id), &bytes).await?;

    let mut src_cid = String::new();
    if !m.src.is_empty() {
        if let Ok(text) = blobs::get(&m.src) {
            src_cid = upload(&filename(&m.name, "rust"), &format!("arena/{}", m.src), &text)
                .await
                .unwrap_or_default();
        }
    }

    store::write(|s| {
        if let Some(x) = s.modules.get_mut(id) {
            x.cid = cid.clone();
            if !src_cid.is_empty() {
                x.src_cid = src_cid.clone();
            }
            x.stored = store::now();
        }
    });
    Ok(json!({
        "id": m.id, "name": m.name, "size": bytes.len(),
        "cid": cid, "page": page(&cid),
        "src_cid": if src_cid.is_empty() { Value::Null } else { json!(src_cid) },
    }))
}

/// Push everything that has no CID yet (or everything, with `force`), and
/// optionally read each copy back and check it against the id.
pub async fn sync(args: &Value) -> Value {
    let force = args.get("force").and_then(|v| v.as_bool()).unwrap_or(false);
    let check = args.get("verify").and_then(|v| v.as_bool()).unwrap_or(false);
    if !enabled() {
        return json!({ "enabled": false, "pushed": 0,
                       "note": "the store bridge is off — set ARENA_STORE_URL to turn it on" });
    }
    let mods = store::read(|s| {
        s.module_list()
            .into_iter()
            .map(|m| (m.id.clone(), m.name.clone(), needs_push(m)))
            .collect::<Vec<_>>()
    });
    let mut pushed = Vec::new();
    let mut failed = Vec::new();
    let mut verified = 0usize;
    let mut mismatched = Vec::new();
    for (id, name, wanted) in &mods {
        if force || *wanted {
            match push(id).await {
                Ok(v) => pushed.push(v),
                Err(e) => {
                    failed.push(json!({ "id": id, "name": name, "error": e }));
                    // One failure is usually every failure (store down, token
                    // refused); stop hammering it.
                    if failed.len() >= 3 {
                        break;
                    }
                    continue;
                }
            }
        }
        if check {
            let cid = store::read(|s| s.modules.get(id).map(|m| m.cid.clone()).unwrap_or_default());
            if cid.is_empty() {
                continue;
            }
            match verify(&cid, id).await {
                Ok(true) => verified += 1,
                Ok(false) => mismatched.push(json!({ "id": id, "name": name, "cid": cid })),
                Err(e) => failed.push(json!({ "id": id, "name": name, "cid": cid, "error": e })),
            }
        }
    }
    let stored = store::read(|s| s.modules.values().filter(|m| !m.cid.is_empty()).count());
    json!({
        "enabled": true, "store": url(),
        "modules": mods.len(), "stored": stored, "missing": mods.len() - stored,
        "pushed": pushed.len(), "modules_pushed": pushed,
        "failed": failed,
        "verified": if check { json!(verified) } else { Value::Null },
        "mismatched": mismatched,
    })
}

/// Where things stand: the store, the address pushes are recorded under, and
/// how much of the registry has a CID.
pub async fn status() -> Value {
    let (modules, stored) = store::read(|s| {
        (s.modules.len(), s.modules.values().filter(|m| !m.cid.is_empty()).count())
    });
    let mut v = json!({
        "enabled": enabled(), "store": url(),
        "public_api": public_api(), "page": page("<cid>"),
        "modules": modules, "stored": stored, "missing": modules - stored,
        "key": format!("arena/<sha256>"),
    });
    if let Some(base) = url() {
        let me = async {
            let tok = token(false).await.ok()?;
            let r = client()
                .get(format!("{base}/me"))
                .header(AUTHORIZATION, format!("Bearer {tok}"))
                .timeout(Duration::from_secs(20))
                .send()
                .await
                .ok()?;
            r.json::<Value>().await.ok()
        }
        .await;
        match me {
            Some(me) => {
                v["address"] = me.get("address").cloned().unwrap_or(Value::Null);
                v["authorized"] = me.get("authorized").cloned().unwrap_or(Value::Null);
                v["quota"] = me.get("quota").cloned().unwrap_or(Value::Null);
                v["terms"] = me.get("terms").cloned().unwrap_or(Value::Null);
                v["reachable"] = json!(true);
            }
            None => {
                v["reachable"] = json!(false);
            }
        }
    }
    v
}

/// The startup pass: a moment after boot, push whatever has no CID. Runs in
/// the background so a store that is asleep costs the arena nothing.
pub fn backfill_later() {
    set_booted();
    if !enabled() {
        return;
    }
    if let Ok(h) = tokio::runtime::Handle::try_current() {
        h.spawn(async {
            tokio::time::sleep(Duration::from_secs(3)).await;
            let r = sync(&json!({})).await;
            println!(
                "storelink: {} of {} module(s) have a store cid ({} pushed, {} failed)",
                r["stored"], r["modules"], r["pushed"],
                r["failed"].as_array().map(|a| a.len()).unwrap_or(0)
            );
        });
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn multipart_carries_the_fields_and_the_file() {
        let body = multipart(&[("backend", "localfs"), ("public", "true")], "ttt.wasm", b"\0asm\x01");
        let text = String::from_utf8_lossy(&body);
        assert!(text.contains("name=\"backend\"\r\n\r\nlocalfs"));
        assert!(text.contains("name=\"public\"\r\n\r\ntrue"));
        assert!(text.contains("filename=\"ttt.wasm\""));
        assert!(body.windows(5).any(|w| w == b"\0asm\x01"));
        assert!(text.ends_with(&format!("--{BOUNDARY}--\r\n")));
    }

    #[test]
    fn filenames_are_safe_and_typed() {
        assert_eq!(filename("nim-rs", "rust"), "nim-rs.rs");
        assert_eq!(filename("con nect/4", "python"), "con-nect-4.py");
        assert_eq!(filename("", "wasm"), "module.wasm");
    }

    #[test]
    fn the_cid_is_read_off_whichever_backend_answered() {
        let v = json!({ "results": { "localfs": { "cid": "QmX", "size": 3 } } });
        assert_eq!(cid_of(&v).as_deref(), Some("QmX"));
        assert!(cid_of(&json!({ "results": {} })).is_none());
    }

    #[test]
    fn an_unpushed_module_has_no_store_card() {
        assert!(card("").is_null());
        let c = card("QmX");
        assert_eq!(c["cid"], "QmX");
        assert!(c["page"].as_str().unwrap().ends_with("/o/QmX"));
        assert!(c["get"].as_str().unwrap().ends_with("cid=QmX"));
    }
}
