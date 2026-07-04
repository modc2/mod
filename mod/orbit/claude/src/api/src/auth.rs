//! MetaMask signature authentication — challenge/verify + bearer token middleware

use axum::{
    extract::{Query, Request, State},
    http::{header, StatusCode},
    middleware::Next,
    response::{IntoResponse, Response},
    Json,
};
use hmac::{Hmac, Mac};
use k256::ecdsa::{RecoveryId, Signature, VerifyingKey};
use serde::{Deserialize, Serialize};
use sha2::Sha256;
use sha3::Digest;
use std::collections::HashMap;
use std::sync::Arc;
use tokio::sync::RwLock;

type HmacSha256 = Hmac<Sha256>;

use std::sync::OnceLock;

/// Server secret for HMAC token signing (generated at startup)
static SERVER_SECRET: OnceLock<[u8; 32]> = OnceLock::new();

pub fn init_secret() {
    use rand::RngCore;
    let mut secret = [0u8; 32];
    rand::thread_rng().fill_bytes(&mut secret);
    SERVER_SECRET.set(secret).ok();
}

fn get_secret() -> &'static [u8; 32] {
    SERVER_SECRET.get().expect("Server secret not initialized")
}

/// Pending challenges: address → nonce message
pub type ChallengeStore = Arc<RwLock<HashMap<String, String>>>;

pub fn new_challenge_store() -> ChallengeStore {
    Arc::new(RwLock::new(HashMap::new()))
}

// ── Endpoints ────────────────────────────────────────────────────────

#[derive(Deserialize)]
pub struct ChallengeQuery {
    pub address: String,
}

#[derive(Serialize)]
pub struct ChallengeResponse {
    pub message: String,
}

pub async fn challenge(
    State(store): State<ChallengeStore>,
    Query(q): Query<ChallengeQuery>,
) -> impl IntoResponse {
    let addr = q.address.to_lowercase();
    let nonce = hex::encode(rand::random::<[u8; 16]>());
    let message = format!(
        "Sign this message to authenticate with Claude Jobs.\n\nAddress: {}\nNonce: {}",
        addr, nonce
    );

    let mut challenges = store.write().await;
    challenges.insert(addr, message.clone());

    Json(ChallengeResponse { message })
}

#[derive(Deserialize)]
pub struct VerifyRequest {
    pub address: String,
    pub signature: String,
    pub message: String,
    /// Optional QR edit-grant id — lets a non-whitelisted address sign in for
    /// the grant's window (and registers their time-boxed edit access).
    #[serde(default)]
    pub grant: Option<String>,
    /// Optional second-factor key for the grant above.
    #[serde(default)]
    pub grant_key: Option<String>,
}

#[derive(Serialize)]
pub struct VerifyResponse {
    pub token: String,
    pub address: String,
}

pub async fn verify(
    State(store): State<ChallengeStore>,
    Json(req): Json<VerifyRequest>,
) -> Result<Json<VerifyResponse>, (StatusCode, Json<serde_json::Value>)> {
    let addr = req.address.to_lowercase();

    // Check challenge exists
    {
        let challenges = store.read().await;
        match challenges.get(&addr) {
            Some(expected) if *expected == req.message => {}
            _ => {
                return Err((
                    StatusCode::BAD_REQUEST,
                    Json(serde_json::json!({ "error": "Invalid or expired challenge" })),
                ));
            }
        }
    }

    // Recover signer from signature
    let recovered = recover_eth_address(&req.message, &req.signature).map_err(|e| {
        (
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({ "error": format!("Signature verification failed: {}", e) })),
        )
    })?;

    if recovered.to_lowercase() != addr {
        return Err((
            StatusCode::UNAUTHORIZED,
            Json(serde_json::json!({ "error": "Signer does not match address" })),
        ));
    }

    // Remove used challenge
    {
        let mut challenges = store.write().await;
        challenges.remove(&addr);
    }

    // Sign-in gate: owner → always; whitelisted addresses → allowed; else delegate
    // to optional `gate_command` (owner-defined executable that returns 0 to allow).
    // If no owner is set yet, this user claims ownership.
    let existing_owner = get_owner_address();
    let is_new_owner = match existing_owner {
        Some(owner) => {
            if owner != addr
                && !read_whitelist().iter().any(|w| w == &addr)
                && !run_gate_command(&addr)
            {
                // Last chance: a QR edit-grant. Redeeming one lets this address
                // in for the grant's window and records its time-boxed access.
                match req.grant.as_deref() {
                    Some(gid) => {
                        match redeem_grant(gid, req.grant_key.as_deref(), &addr) {
                            Ok(exp) => {
                                println!("✓ Grant redeemed: {} via {} (until {})", addr, gid, exp);
                            }
                            Err(e) => {
                                eprintln!("✗ Grant redemption failed for {}: {}", addr, e);
                                return Err((
                                    StatusCode::FORBIDDEN,
                                    Json(serde_json::json!({ "error": e })),
                                ));
                            }
                        }
                    }
                    None => {
                        // Echo the verified signer + configured owner so a wrong active
                        // MetaMask account is obvious. Log it too — this path was silent.
                        eprintln!(
                            "✗ Sign-in denied: signer {} is not owner ({}), not whitelisted, no gate matched",
                            addr, owner
                        );
                        return Err((
                            StatusCode::FORBIDDEN,
                            Json(serde_json::json!({
                                "error": format!(
                                    "Sign-in not permitted: signed-in address {} is not the owner ({}), not whitelisted, and no gate matched. Check which account is active in your wallet.",
                                    addr, owner
                                )
                            })),
                        ));
                    }
                }
            }
            false
        }
        None => {
            let owner_path = dirs::home_dir()
                .unwrap_or_else(|| std::path::PathBuf::from("."))
                .join(".mod")
                .join("claude")
                .join("owner.json");
            if let Some(parent) = owner_path.parent() {
                std::fs::create_dir_all(parent).ok();
            }
            let owner_data = serde_json::json!({ "owner": addr });
            match serde_json::to_string_pretty(&owner_data) {
                Ok(json_str) => {
                    std::fs::write(&owner_path, json_str).ok();
                    true
                }
                Err(_) => false,
            }
        }
    };

    let token = mint_token(&addr);

    if is_new_owner {
        println!("✓ First user authenticated - set as owner: {}", addr);
    }

    Ok(Json(VerifyResponse {
        token,
        address: addr,
    }))
}

/// Mint a bearer token for an authenticated identity: address:timestamp:hmac.
pub fn mint_token(address: &str) -> String {
    let timestamp = chrono::Utc::now().timestamp();
    let payload = format!("{}:{}", address, timestamp);
    let mut mac = HmacSha256::new_from_slice(get_secret()).unwrap();
    mac.update(payload.as_bytes());
    let sig = hex::encode(mac.finalize().into_bytes());
    format!("{}:{}", payload, sig)
}

// ── Middleware ────────────────────────────────────────────────────────

pub async fn auth_middleware(req: Request, next: Next) -> Response {
    // Try Bearer token first (Claude API native token)
    let auth_header = req
        .headers()
        .get(header::AUTHORIZATION)
        .and_then(|v| v.to_str().ok())
        .unwrap_or("");

    if auth_header.starts_with("Bearer ") {
        let token = &auth_header[7..];
        return match validate_token(token) {
            Ok(_addr) => next.run(req).await,
            Err(e) => (
                StatusCode::UNAUTHORIZED,
                Json(serde_json::json!({ "error": e })),
            )
                .into_response(),
        };
    }

    // Try core app token in "token" header (Base64URL JSON with EIP-191 signature)
    let core_token = req
        .headers()
        .get("token")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("");

    if !core_token.is_empty() {
        return match validate_core_token(core_token) {
            Ok(_addr) => next.run(req).await,
            Err(e) => (
                StatusCode::UNAUTHORIZED,
                Json(serde_json::json!({ "error": format!("Core token: {}", e) })),
            )
                .into_response(),
        };
    }

    (
        StatusCode::UNAUTHORIZED,
        Json(serde_json::json!({ "error": "Missing authentication — provide Bearer token or core app token" })),
    )
        .into_response()
}

pub fn validate_token(token: &str) -> Result<String, String> {
    // Format: address:timestamp:hmac
    let parts: Vec<&str> = token.splitn(3, ':').collect();
    if parts.len() != 3 {
        return Err("Invalid token format".to_string());
    }

    let address = parts[0];
    let timestamp: i64 = parts[1]
        .parse()
        .map_err(|_| "Invalid timestamp".to_string())?;
    let provided_sig = parts[2];

    // Check expiry (24 hours). Guest tokens (walletless QR redemption) are
    // exempt here — they live and die with their grant, checked below.
    let is_guest = address.starts_with(GUEST_PREFIX);
    let now = chrono::Utc::now().timestamp();
    if !is_guest && now - timestamp > 86400 {
        return Err("Token expired".to_string());
    }

    // Verify HMAC
    let payload = format!("{}:{}", address, timestamp);
    let mut mac = HmacSha256::new_from_slice(get_secret()).unwrap();
    mac.update(payload.as_bytes());
    let expected = hex::encode(mac.finalize().into_bytes());

    if expected != provided_sig {
        return Err("Invalid token signature".to_string());
    }

    // A guest identity only means anything while its grant redemption is live,
    // so access ends the moment the grant expires or is revoked.
    if is_guest && !grant_active(address) {
        return Err("Guest access expired".to_string());
    }

    Ok(address.to_string())
}

/// Validate a core app token (Base64URL-encoded JSON with EIP-191 signature).
///
/// Token format (after Base64URL decode + JSON parse):
///   { "data": "", "time": "1712345678", "key": "0x...", "signature": "0x...", "dataHash": "..." }
///
/// The signature is EIP-191 personal_sign over the string:
///   {"data":"<data>","time":"<time>"}
pub fn validate_core_token(token: &str) -> Result<String, String> {
    use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};

    // Base64URL decode
    let decoded = URL_SAFE_NO_PAD
        .decode(token)
        .map_err(|e| format!("Base64 decode failed: {}", e))?;

    let json_str = String::from_utf8(decoded)
        .map_err(|e| format!("UTF-8 decode failed: {}", e))?;

    let parsed: serde_json::Value = serde_json::from_str(&json_str)
        .map_err(|e| format!("JSON parse failed: {}", e))?;

    let data = parsed.get("data").and_then(|v| v.as_str()).unwrap_or("");
    let time_str = parsed.get("time").and_then(|v| v.as_str())
        .ok_or("Missing 'time' field")?;
    let key = parsed.get("key").and_then(|v| v.as_str())
        .ok_or("Missing 'key' field")?;
    let signature = parsed.get("signature").and_then(|v| v.as_str())
        .ok_or("Missing 'signature' field")?;

    // Check staleness (1 hour max)
    let token_time: i64 = time_str.parse()
        .map_err(|_| "Invalid timestamp".to_string())?;
    let now = chrono::Utc::now().timestamp();
    if (now - token_time).abs() > 3600 {
        return Err("Core token expired".to_string());
    }

    // Reconstruct the signed message: {"data":"<data>","time":"<time>"}
    let sign_message = format!("{{\"data\":{},\"time\":{}}}",
        serde_json::to_string(data).unwrap_or_else(|_| format!("\"{}\"", data)),
        serde_json::to_string(time_str).unwrap_or_else(|_| format!("\"{}\"", time_str)),
    );

    // Verify EIP-191 signature (MetaMask personal_sign)
    let recovered = recover_eth_address(&sign_message, signature)
        .map_err(|e| format!("Signature verification failed: {}", e))?;

    if recovered.to_lowercase() != key.to_lowercase() {
        return Err(format!(
            "Address mismatch: recovered {} but token says {}",
            recovered, key
        ));
    }

    Ok(key.to_lowercase())
}

/// Extract address from a bearer token in the Authorization header
pub fn extract_address_from_header(auth_header: &str) -> Result<String, String> {
    if !auth_header.starts_with("Bearer ") {
        return Err("Missing bearer token".to_string());
    }
    validate_token(&auth_header[7..])
}

/// Extract address from request headers — tries Authorization (Bearer) first, then core app token header
pub fn extract_address_from_headers(headers: &axum::http::HeaderMap) -> Result<String, String> {
    // Try Bearer token first
    let auth_header = headers
        .get(axum::http::header::AUTHORIZATION)
        .and_then(|v| v.to_str().ok())
        .unwrap_or("");
    if let Ok(addr) = extract_address_from_header(auth_header) {
        return Ok(addr);
    }

    // Try core app token header
    let core_token = headers
        .get("token")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("");
    if !core_token.is_empty() {
        return validate_core_token(core_token);
    }

    Err("No valid auth token found".to_string())
}

/// Read the owner address — config.json "owner" field takes priority, then ~/.mod/claude/owner.json
pub fn get_owner_address() -> Option<String> {
    // Priority 1: config.json "owner" field (live-editable)
    if let Some(owner) = read_config_owner() {
        return Some(owner);
    }

    // Priority 2: owner.json
    let owner_path = dirs::home_dir()?
        .join(".mod")
        .join("claude")
        .join("owner.json");

    let content = std::fs::read_to_string(&owner_path).ok()?;
    let data: serde_json::Value = serde_json::from_str(&content).ok()?;
    data.get("owner").and_then(|v| v.as_str()).map(|s| s.to_lowercase())
}

/// Read the "owner" field from config.json (re-read each call so live edits take effect)
fn read_config_owner() -> Option<String> {
    let config_path = std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(|d| d.to_path_buf()))
        .and_then(|d| {
            let mut dir = d.as_path();
            for _ in 0..5 {
                let candidate = dir.join("config.json");
                if candidate.exists() {
                    return Some(candidate);
                }
                dir = dir.parent()?;
            }
            None
        })
        .unwrap_or_else(|| {
            let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string());
            std::path::PathBuf::from(format!("{}/mod/mod/orbit/claude/config.json", home))
        });

    let content = std::fs::read_to_string(&config_path).ok()?;
    let data: serde_json::Value = serde_json::from_str(&content).ok()?;
    let owner = data.get("owner").and_then(|v| v.as_str())?.to_lowercase();
    if owner.is_empty() { None } else { Some(owner) }
}

/// Check if an address is the system owner
pub fn is_owner(address: &str) -> bool {
    match get_owner_address() {
        Some(owner) => address.to_lowercase() == owner,
        None => false,
    }
}

/// Check if an address is *trusted* to edit — the configured owner OR a
/// whitelisted address. Trusted callers get the owner's wider edit surface
/// (host filesystem access, unsandboxed jobs, core/ + orbit/ writes) because
/// everything in the orbit belongs to the host owner and the whitelist is the
/// owner's explicit delegation of edit rights.
///
/// Owner-only *powers* — managing the whitelist, changing the owner, killing
/// processes, process control, and destructive module ops (delete/rename/
/// restore) — stay gated on `is_owner`, NOT this. Only edit capability widens.
pub fn is_trusted(address: &str) -> bool {
    if address.is_empty() {
        return false;
    }
    if is_owner(address) {
        return true;
    }
    let addr = address.to_lowercase();
    if read_whitelist().iter().any(|w| w == &addr) {
        return true;
    }
    // Time-boxed QR grants: a holder who redeemed a still-valid edit grant gets
    // the same edit surface as a whitelisted editor, until the grant expires.
    grant_active(&addr)
}

// ── Time-boxed edit grants (QR hand-off) ─────────────────────────────
//
// The owner mints a *grant*: a short, random id that confers temporary
// edit access (default 1h) to whoever redeems it, optionally protected by
// a second-factor *key* the owner shares out of band. The id travels in a
// QR code; the key is supplied separately so a leaked QR alone is useless.
//
// Redemption is bound to the redeemer's signed-in address (during sign-in),
// so access cleaves an auditable trail and the grant can serve multiple
// people. Everything lives off-repo in ~/.mod/claude/grants.json next to the
// whitelist; the grant window is absolute (created → created+ttl), so the QR
// stops working — and every redeemer's access ends — at the same moment.

#[derive(Serialize, Deserialize, Clone)]
pub struct Grant {
    /// Random, URL-safe id carried by the QR code.
    pub id: String,
    /// Unix expiry — access (and redemption) ends here.
    pub exp: i64,
    /// Seconds of life the owner asked for (for display/echo).
    pub ttl: i64,
    /// sha256(key) hex if the owner set a second-factor key; absent otherwise.
    #[serde(default)]
    pub key_hash: Option<String>,
    /// Optional human label so the owner remembers what a grant was for.
    #[serde(default)]
    pub label: Option<String>,
    pub created: i64,
    #[serde(default)]
    pub revoked: bool,
}

#[derive(Serialize, Deserialize, Clone)]
pub struct Redemption {
    pub address: String,
    pub exp: i64,
    pub grant: String,
    pub redeemed: i64,
}

#[derive(Serialize, Deserialize, Default)]
pub struct GrantsFile {
    #[serde(default)]
    pub grants: Vec<Grant>,
    #[serde(default)]
    pub redemptions: Vec<Redemption>,
}

pub fn grants_path() -> Option<std::path::PathBuf> {
    Some(private_dir()?.join("grants.json"))
}

/// Read the grants file, tolerating a missing/corrupt file (returns empty).
pub fn read_grants() -> GrantsFile {
    let path = match grants_path() {
        Some(p) => p,
        None => return GrantsFile::default(),
    };
    std::fs::read_to_string(&path)
        .ok()
        .and_then(|c| serde_json::from_str(&c).ok())
        .unwrap_or_default()
}

pub fn write_grants(file: &GrantsFile) -> Result<(), String> {
    let path = grants_path().ok_or("no home dir")?;
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|e| format!("mkdir: {}", e))?;
    }
    let json = serde_json::to_string_pretty(file).map_err(|e| format!("encode: {}", e))?;
    std::fs::write(&path, json).map_err(|e| format!("write: {}", e))
}

fn sha256_hex(s: &str) -> String {
    let mut h = Sha256::new();
    h.update(s.as_bytes());
    hex::encode(h.finalize())
}

/// Drop expired grants and redemptions, plus redemptions whose grant is gone or
/// revoked. Returns the pruned file (callers persist it).
fn prune_grants(mut file: GrantsFile, now: i64) -> GrantsFile {
    file.grants.retain(|g| !g.revoked && g.exp > now);
    let live: std::collections::HashSet<String> =
        file.grants.iter().map(|g| g.id.clone()).collect();
    file.redemptions
        .retain(|r| r.exp > now && live.contains(&r.grant));
    file
}

/// Mint a new grant. `ttl` is clamped by the caller; `key` (if any) is hashed,
/// never stored in the clear. Returns the created grant.
pub fn create_grant(
    ttl: i64,
    key: Option<&str>,
    label: Option<&str>,
) -> Result<Grant, String> {
    let now = chrono::Utc::now().timestamp();
    let id = hex::encode(rand::random::<[u8; 12]>());
    let grant = Grant {
        id,
        exp: now + ttl,
        ttl,
        key_hash: key.map(sha256_hex),
        label: label
            .map(|s| s.trim().to_string())
            .filter(|s| !s.is_empty()),
        created: now,
        revoked: false,
    };
    let mut file = prune_grants(read_grants(), now);
    file.grants.push(grant.clone());
    write_grants(&file)?;
    Ok(grant)
}

/// Active (unexpired, unrevoked) grants and live redemptions, for the owner UI.
pub fn list_grants() -> GrantsFile {
    let now = chrono::Utc::now().timestamp();
    let pruned = prune_grants(read_grants(), now);
    // Persist the prune so the file doesn't grow unbounded; ignore write errors.
    let _ = write_grants(&pruned);
    pruned
}

/// Revoke a grant by id (and cut every session it opened). Returns true if found.
pub fn revoke_grant(id: &str) -> bool {
    let now = chrono::Utc::now().timestamp();
    let mut file = prune_grants(read_grants(), now);
    let before = file.grants.len();
    file.grants.retain(|g| g.id != id);
    let removed = file.grants.len() != before;
    if removed {
        file.redemptions.retain(|r| r.grant != id);
        let _ = write_grants(&file);
    }
    removed
}

/// Redeem a grant for `address`. Validates existence, expiry, and the optional
/// key, then records (or refreshes) the redemption. Returns the access expiry.
pub fn redeem_grant(id: &str, key: Option<&str>, address: &str) -> Result<i64, String> {
    let now = chrono::Utc::now().timestamp();
    let mut file = prune_grants(read_grants(), now);
    let addr = address.to_lowercase();

    let grant = file
        .grants
        .iter()
        .find(|g| g.id == id)
        .cloned()
        .ok_or("Invalid or expired invite")?;

    if let Some(expected) = &grant.key_hash {
        let supplied = key.map(|k| k.trim()).filter(|k| !k.is_empty());
        match supplied {
            Some(k) if &sha256_hex(k) == expected => {}
            Some(_) => return Err("Wrong key for this invite".to_string()),
            None => return Err("This invite requires a key".to_string()),
        }
    }

    // Upsert the redemption (re-redeeming just refreshes it to the grant window).
    file.redemptions.retain(|r| !(r.address == addr && r.grant == id));
    file.redemptions.push(Redemption {
        address: addr,
        exp: grant.exp,
        grant: grant.id.clone(),
        redeemed: now,
    });
    write_grants(&file)?;
    Ok(grant.exp)
}

/// Prefix for walletless guest identities minted by QR redemption. Can never
/// collide with a real signer: Ethereum addresses always start with "0x".
pub const GUEST_PREFIX: &str = "guest_";

/// Walletless redemption: anyone holding the grant id (from the QR) trades it
/// for a fresh guest identity whose redemption — and therefore whose bearer
/// token — expires with the grant. Returns (guest address, access expiry).
pub fn redeem_grant_guest(id: &str, key: Option<&str>) -> Result<(String, i64), String> {
    let guest = format!("{}{}", GUEST_PREFIX, hex::encode(rand::random::<[u8; 4]>()));
    let exp = redeem_grant(id, key, &guest)?;
    Ok((guest, exp))
}

/// True if `address` holds an unexpired redemption of a live grant.
pub fn grant_active(address: &str) -> bool {
    let now = chrono::Utc::now().timestamp();
    let addr = address.to_lowercase();
    let file = read_grants();
    let live: std::collections::HashSet<&str> = file
        .grants
        .iter()
        .filter(|g| !g.revoked && g.exp > now)
        .map(|g| g.id.as_str())
        .collect();
    file.redemptions
        .iter()
        .any(|r| r.address == addr && r.exp > now && live.contains(r.grant.as_str()))
}

// ── Session handoff (QR sign-in on another device) ──────────────────
//
// A signed-in browser mints a short-lived, SINGLE-USE code bound to its own
// identity. The code rides a QR; another device (typically the same person's
// phone) opens `?handoff=<code>` and trades it for a fresh bearer token as
// the SAME address — no wallet or signature needed on that device. This is
// distinct from grants: a grant invites *someone else* in (guest identity /
// time-boxed access), a handoff moves *your own* session across devices.
//
// Codes live in memory only: they expire in minutes and are consumed on
// first redemption, so a restart merely voids any un-scanned QR.

struct Handoff {
    address: String,
    exp: i64,
}

static HANDOFFS: OnceLock<std::sync::Mutex<HashMap<String, Handoff>>> = OnceLock::new();

/// How long a handoff QR stays scannable. Short on purpose: the code is a
/// bearer capability for the minter's whole identity.
pub const HANDOFF_TTL: i64 = 300;

fn handoff_store() -> &'static std::sync::Mutex<HashMap<String, Handoff>> {
    HANDOFFS.get_or_init(|| std::sync::Mutex::new(HashMap::new()))
}

/// Mint a single-use handoff code for `address`. Returns (code, expiry).
pub fn create_handoff(address: &str) -> (String, i64) {
    let now = chrono::Utc::now().timestamp();
    let code = hex::encode(rand::random::<[u8; 16]>());
    let exp = now + HANDOFF_TTL;
    let mut map = handoff_store().lock().unwrap();
    map.retain(|_, h| h.exp > now);
    map.insert(
        code.clone(),
        Handoff {
            address: address.to_lowercase(),
            exp,
        },
    );
    (code, exp)
}

/// Redeem (and consume) a handoff code. Returns the bound address.
pub fn redeem_handoff(code: &str) -> Result<String, String> {
    let now = chrono::Utc::now().timestamp();
    let mut map = handoff_store().lock().unwrap();
    map.retain(|_, h| h.exp > now);
    map.remove(code)
        .map(|h| h.address)
        .ok_or_else(|| "Invalid, expired, or already-used sign-in code".to_string())
}

/// Off-chain config dir (~/.mod/claude/) — holds owner.json, whitelist.json, gate.json.
/// Kept off-repo because the whitelist is private; mounted into the container as a volume.
pub fn private_dir() -> Option<std::path::PathBuf> {
    Some(dirs::home_dir()?.join(".mod").join("claude"))
}

pub fn whitelist_path() -> Option<std::path::PathBuf> {
    Some(private_dir()?.join("whitelist.json"))
}

fn gate_path() -> Option<std::path::PathBuf> {
    Some(private_dir()?.join("gate.json"))
}

/// Read the lowercased whitelist from ~/.mod/claude/whitelist.json (a JSON string array).
/// Returns [] if the file is absent or malformed — never errors.
pub fn read_whitelist() -> Vec<String> {
    let path = match whitelist_path() {
        Some(p) => p,
        None => return Vec::new(),
    };
    let content = match std::fs::read_to_string(&path) {
        Ok(c) => c,
        Err(_) => return Vec::new(),
    };
    let parsed: serde_json::Value = match serde_json::from_str(&content) {
        Ok(v) => v,
        Err(_) => return Vec::new(),
    };
    // Accept either `["0x..", ..]` or `{"addresses": ["0x..", ..]}`.
    let arr = parsed
        .as_array()
        .cloned()
        .or_else(|| parsed.get("addresses").and_then(|v| v.as_array()).cloned())
        .unwrap_or_default();
    arr.into_iter()
        .filter_map(|v| v.as_str().map(|s| s.to_lowercase()))
        .collect()
}

/// Persist the whitelist back to ~/.mod/claude/whitelist.json (used by the owner-only
/// management endpoints in api.rs). Returns an error string on failure.
pub fn write_whitelist(addresses: &[String]) -> Result<(), String> {
    let path = whitelist_path().ok_or("no home dir")?;
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|e| format!("mkdir: {}", e))?;
    }
    let json = serde_json::to_string_pretty(&addresses).map_err(|e| format!("encode: {}", e))?;
    std::fs::write(&path, json).map_err(|e| format!("write: {}", e))
}

/// Read the optional `gate_command` from ~/.mod/claude/gate.json — a shell command that the
/// owner defines to authorize sign-in based on arbitrary logic (token-gated, NFT-gated, etc).
/// File format: {"command": "..."}.
fn read_gate_command() -> Option<String> {
    let path = gate_path()?;
    let content = std::fs::read_to_string(&path).ok()?;
    let data: serde_json::Value = serde_json::from_str(&content).ok()?;
    let cmd = data.get("command").and_then(|v| v.as_str())?.trim().to_string();
    if cmd.is_empty() { None } else { Some(cmd) }
}

/// Invoke the owner-defined `gate_command` (if any). The address is passed as $1 and on
/// stdin as `{"address":"0x.."}`. Exit code 0 ⇒ allow, anything else ⇒ deny.
/// Returns false if no command is configured (deny by default).
pub fn run_gate_command(address: &str) -> bool {
    let cmd = match read_gate_command() {
        Some(c) => c,
        None => return false,
    };
    let payload = serde_json::json!({ "address": address }).to_string();
    let mut child = match std::process::Command::new("sh")
        .arg("-c")
        .arg(&cmd)
        .arg("--")
        .arg(address)
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .spawn()
    {
        Ok(c) => c,
        Err(e) => {
            eprintln!("gate_command spawn failed: {}", e);
            return false;
        }
    };
    if let Some(mut stdin) = child.stdin.take() {
        use std::io::Write;
        let _ = stdin.write_all(payload.as_bytes());
    }
    match child.wait() {
        Ok(status) => status.success(),
        Err(_) => false,
    }
}

// ── Ethereum Signature Recovery ──────────────────────────────────────

pub fn recover_eth_address(message: &str, signature: &str) -> Result<String, String> {
    // Strip 0x prefix
    let sig_hex = signature.strip_prefix("0x").unwrap_or(signature);
    let sig_bytes = hex::decode(sig_hex).map_err(|e| format!("Bad hex: {}", e))?;

    if sig_bytes.len() != 65 {
        return Err(format!("Signature must be 65 bytes, got {}", sig_bytes.len()));
    }

    // Split into r,s,v
    let r_s = &sig_bytes[..64];
    let v = sig_bytes[64];

    // MetaMask uses v = 27 or 28. RecoveryId::new(is_y_odd, is_x_reduced)
    let recovery_id = match v {
        27 | 0 => RecoveryId::new(false, false),
        28 | 1 => RecoveryId::new(true, false),
        _ => return Err(format!("Invalid recovery id: {}", v)),
    };

    // EIP-191 personal_sign hash: "\x19Ethereum Signed Message:\n" + len + message
    let prefix = format!("\x19Ethereum Signed Message:\n{}", message.len());
    let mut hasher = sha3::Keccak256::new();
    hasher.update(prefix.as_bytes());
    hasher.update(message.as_bytes());
    let hash = hasher.finalize();

    let signature =
        Signature::from_slice(r_s).map_err(|e| format!("Bad signature: {}", e))?;

    let recovered_key = VerifyingKey::recover_from_prehash(&hash, &signature, recovery_id)
        .map_err(|e| format!("Recovery failed: {}", e))?;

    // Public key → Ethereum address (keccak256 of uncompressed pubkey without 0x04 prefix)
    let pubkey_bytes = recovered_key.to_encoded_point(false);
    let pubkey_raw = &pubkey_bytes.as_bytes()[1..]; // skip 0x04

    let mut addr_hasher = sha3::Keccak256::new();
    addr_hasher.update(pubkey_raw);
    let addr_hash = addr_hasher.finalize();

    let address = format!("0x{}", hex::encode(&addr_hash[12..]));
    Ok(address)
}

// ── Tests ────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use k256::ecdsa::SigningKey;

    fn ensure_secret() {
        // OnceLock only sets once, safe to call multiple times
        let _ = SERVER_SECRET.set([42u8; 32]);
    }

    #[test]
    fn test_token_roundtrip() {
        ensure_secret();
        let addr = "0xabcdef1234567890abcdef1234567890abcdef12";
        let timestamp = chrono::Utc::now().timestamp();
        let payload = format!("{}:{}", addr, timestamp);

        let mut mac = HmacSha256::new_from_slice(get_secret()).unwrap();
        mac.update(payload.as_bytes());
        let sig = hex::encode(mac.finalize().into_bytes());
        let token = format!("{}:{}", payload, sig);

        let result = validate_token(&token);
        assert!(result.is_ok(), "Token validation failed: {:?}", result.err());
        assert_eq!(result.unwrap(), addr);
    }

    #[test]
    fn test_token_expired() {
        ensure_secret();
        let addr = "0xabcdef1234567890abcdef1234567890abcdef12";
        // 2 days ago
        let timestamp = chrono::Utc::now().timestamp() - 172800;
        let payload = format!("{}:{}", addr, timestamp);

        let mut mac = HmacSha256::new_from_slice(get_secret()).unwrap();
        mac.update(payload.as_bytes());
        let sig = hex::encode(mac.finalize().into_bytes());
        let token = format!("{}:{}", payload, sig);

        let result = validate_token(&token);
        assert!(result.is_err());
        assert_eq!(result.unwrap_err(), "Token expired");
    }

    #[test]
    fn test_token_tampered() {
        ensure_secret();
        let addr = "0xabcdef1234567890abcdef1234567890abcdef12";
        let timestamp = chrono::Utc::now().timestamp();
        let token = format!("{}:{}:badhmacsignature", addr, timestamp);

        let result = validate_token(&token);
        assert!(result.is_err());
        assert_eq!(result.unwrap_err(), "Invalid token signature");
    }

    #[test]
    fn test_token_bad_format() {
        ensure_secret();
        // No colons at all
        let result = validate_token("garbage");
        assert!(result.is_err());
        assert_eq!(result.unwrap_err(), "Invalid token format");

        // Only two parts (need 3: address:timestamp:hmac)
        let result2 = validate_token("only:two");
        assert!(result2.is_err());
        assert_eq!(result2.unwrap_err(), "Invalid token format");

        // Three parts but non-numeric timestamp
        let result3 = validate_token("addr:notanumber:sig");
        assert!(result3.is_err());
        assert_eq!(result3.unwrap_err(), "Invalid timestamp");
    }

    #[test]
    fn test_recover_eth_address_with_known_key() {
        // Generate a new secp256k1 key pair
        let signing_key = SigningKey::random(&mut rand::thread_rng());
        let verifying_key = signing_key.verifying_key();

        // Derive the expected Ethereum address from the public key
        let pubkey_bytes = verifying_key.to_encoded_point(false);
        let pubkey_raw = &pubkey_bytes.as_bytes()[1..]; // skip 0x04 prefix
        let mut addr_hasher = sha3::Keccak256::new();
        addr_hasher.update(pubkey_raw);
        let addr_hash = addr_hasher.finalize();
        let expected_address = format!("0x{}", hex::encode(&addr_hash[12..]));

        // Sign a message using EIP-191 personal_sign
        let message = "Hello Claude Jobs";
        let prefix = format!("\x19Ethereum Signed Message:\n{}", message.len());
        let mut hasher = sha3::Keccak256::new();
        hasher.update(prefix.as_bytes());
        hasher.update(message.as_bytes());
        let hash = hasher.finalize();

        let (sig, recid) = signing_key
            .sign_prehash_recoverable(&hash)
            .expect("signing failed");

        // Build the 65-byte signature: r (32) + s (32) + v (1)
        let mut sig_bytes = Vec::with_capacity(65);
        sig_bytes.extend_from_slice(&sig.to_bytes());
        // v = 27 + recovery_id (0 or 1)
        let v: u8 = if recid.is_y_odd().into() { 28 } else { 27 };
        sig_bytes.push(v);

        let sig_hex = format!("0x{}", hex::encode(&sig_bytes));

        // Recover and verify
        let recovered = recover_eth_address(message, &sig_hex).expect("recovery failed");
        assert_eq!(
            recovered.to_lowercase(),
            expected_address.to_lowercase(),
            "Recovered address doesn't match expected"
        );
    }

    #[test]
    fn test_handoff_roundtrip_single_use() {
        let addr = "0xABCDef1234567890abcdef1234567890abcdef12";
        let (code, exp) = create_handoff(addr);
        assert!(exp > chrono::Utc::now().timestamp());

        // Redeems to the lowercased bound address…
        let redeemed = redeem_handoff(&code).expect("redeem failed");
        assert_eq!(redeemed, addr.to_lowercase());

        // …and only once.
        assert!(redeem_handoff(&code).is_err());
    }

    #[test]
    fn test_handoff_unknown_code() {
        assert!(redeem_handoff("nope").is_err());
    }

    #[test]
    fn test_recover_bad_signature_length() {
        let result = recover_eth_address("hello", "0xdeadbeef");
        assert!(result.is_err());
        assert!(result.unwrap_err().contains("65 bytes"));
    }

    #[test]
    fn test_recover_bad_hex() {
        let result = recover_eth_address("hello", "0xZZZZ");
        assert!(result.is_err());
        assert!(result.unwrap_err().contains("Bad hex"));
    }

    #[test]
    fn test_recover_invalid_v() {
        // 64 bytes of zeros + v=99 (invalid)
        let sig = format!("0x{}{:02x}", "00".repeat(64), 99u8);
        let result = recover_eth_address("hello", &sig);
        assert!(result.is_err());
        assert!(result.unwrap_err().contains("Invalid recovery id"));
    }
}
