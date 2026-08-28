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

/// Server secret for HMAC token signing. Persisted at ~/.mod/build-fork/server.secret
/// so bearer tokens survive API restarts — this server restarts itself after
/// self-edit jobs, and a fresh random secret would 401 every signed-in browser.
static SERVER_SECRET: OnceLock<[u8; 32]> = OnceLock::new();

fn secret_path() -> std::path::PathBuf {
    dirs::home_dir()
        .unwrap_or_else(|| std::path::PathBuf::from("."))
        .join(".mod")
        .join("build-fork")
        .join("server.secret")
}

/// Tighten ~/.mod/build-fork to 0700. Everything private this module holds lives
/// there — the HMAC secret, the whitelist, live QR grant ids (which ARE
/// capabilities), the sudo replay store — and several of those files are
/// written 0644 by serde helpers. Locking the directory keeps other local
/// users out regardless of the mode on any one file inside it.
pub fn harden_private_dir() {
    #[cfg(unix)]
    if let Some(dir) = private_dir() {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::create_dir_all(&dir);
        let _ = std::fs::set_permissions(&dir, std::fs::Permissions::from_mode(0o700));
    }
}

pub fn init_secret() {
    harden_private_dir();
    let path = secret_path();
    if let Ok(bytes) = std::fs::read(&path) {
        if bytes.len() == 32 {
            let mut secret = [0u8; 32];
            secret.copy_from_slice(&bytes);
            SERVER_SECRET.set(secret).ok();
            return;
        }
    }
    use rand::RngCore;
    let mut secret = [0u8; 32];
    rand::thread_rng().fill_bytes(&mut secret);
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).ok();
    }
    if std::fs::write(&path, secret).is_ok() {
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600)).ok();
        }
    }
    SERVER_SECRET.set(secret).ok();
}

fn get_secret() -> &'static [u8; 32] {
    SERVER_SECRET.get().expect("Server secret not initialized")
}

/// Pending challenges: address → (nonce message, issued-at unix seconds).
/// Unauthenticated callers mint these, so entries expire and the map is capped
/// — an un-pruned store is both a memory sink and an indefinitely-valid nonce.
pub type ChallengeStore = Arc<RwLock<HashMap<String, (String, i64)>>>;

/// How long a signed-in-with nonce stays good. Long enough to open a wallet,
/// short enough that a captured challenge is worthless by the time it leaks.
pub const CHALLENGE_TTL: i64 = 300;
/// Hard cap on outstanding challenges (one per address).
const MAX_CHALLENGES: usize = 512;

pub fn new_challenge_store() -> ChallengeStore {
    Arc::new(RwLock::new(HashMap::new()))
}

// ── Terms of Use ─────────────────────────────────────────────────────
//
// Every non-owner wallet must sign the current terms once before it can
// sign in. The terms are embedded in the challenge message itself, so the
// wallet signature covers them; acceptance is recorded per-address in
// ~/.mod/build-fork/terms_accepted.json and re-required when the version bumps.

pub const TERMS_VERSION: u32 = 1;
pub const TERMS_MARKER: &str = "Build Jobs Terms of Use (v1)";
pub const TERMS_TEXT: &str = "\
1. This service runs coding tasks on the operator's infrastructure. Your \
prompts, task output, and files a task touches may be visible to the \
operator, and tasks appear in a world-readable job ledger.
2. You will not use the service for unlawful, abusive, or malicious \
activity, or to access data or systems you are not authorized to access.
3. Access is granted at the operator's discretion and may be limited, \
suspended, or revoked at any time without notice.
4. You are responsible for your wallet, its keys, and everything submitted \
from your address.
5. The service is provided \"as is\", without warranty of any kind. To the \
maximum extent permitted by law, the operator is not liable for any damages \
or losses arising from its use.";

fn terms_accepted_path() -> std::path::PathBuf {
    dirs::home_dir()
        .unwrap_or_else(|| std::path::PathBuf::from("."))
        .join(".mod")
        .join("build-fork")
        .join("terms_accepted.json")
}

fn read_terms_accepted() -> HashMap<String, serde_json::Value> {
    std::fs::read_to_string(terms_accepted_path())
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or_default()
}

/// True when this address needs no terms signature: the configured owner
/// (or the first user, who becomes owner) sets the terms rather than
/// agreeing to them, and already-recorded acceptances stay valid until
/// TERMS_VERSION bumps.
pub fn terms_satisfied(addr: &str) -> bool {
    match get_owner_address() {
        Some(owner) if owner != addr => {}
        _ => return true,
    }
    read_terms_accepted()
        .get(addr)
        .and_then(|v| v.get("version"))
        .and_then(|v| v.as_u64())
        .map(|v| v >= TERMS_VERSION as u64)
        .unwrap_or(false)
}

fn record_terms_acceptance(addr: &str) {
    let mut map = read_terms_accepted();
    map.insert(
        addr.to_string(),
        serde_json::json!({
            "version": TERMS_VERSION,
            "accepted_at": chrono::Utc::now().timestamp(),
        }),
    );
    let path = terms_accepted_path();
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).ok();
    }
    if let Ok(s) = serde_json::to_string_pretty(&map) {
        std::fs::write(&path, s).ok();
    }
}

#[derive(Deserialize)]
pub struct TermsQuery {
    #[serde(default)]
    pub address: Option<String>,
}

/// Public: the current terms text plus whether the given address still has
/// to sign them, so the app can show the agreement before requesting a
/// wallet signature.
pub async fn terms(Query(q): Query<TermsQuery>) -> impl IntoResponse {
    let required = match q.address.as_deref() {
        Some(a) if !a.is_empty() => !terms_satisfied(&a.to_lowercase()),
        _ => true,
    };
    Json(serde_json::json!({
        "version": TERMS_VERSION,
        "title": TERMS_MARKER,
        "text": TERMS_TEXT,
        "required": required,
    }))
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
    let mut message = format!(
        "Sign this message to authenticate with Build Jobs.\n\nAddress: {}\nNonce: {}",
        addr, nonce
    );
    // First sign-in from a non-owner wallet also signs the terms: embedding
    // them in the challenge makes the signature cover the exact text.
    if !terms_satisfied(&addr) {
        message.push_str(&format!(
            "\n\nBy signing you accept the {}:\n{}",
            TERMS_MARKER, TERMS_TEXT
        ));
    }

    let now = chrono::Utc::now().timestamp();
    let mut challenges = store.write().await;
    challenges.retain(|_, (_, issued)| now - *issued < CHALLENGE_TTL);
    // Still full of live challenges? Drop the oldest to make room rather than
    // letting a flood of addresses grow the map without bound.
    while challenges.len() >= MAX_CHALLENGES {
        let oldest = challenges
            .iter()
            .min_by_key(|(_, (_, issued))| *issued)
            .map(|(k, _)| k.clone());
        match oldest {
            Some(k) => {
                challenges.remove(&k);
            }
            None => break,
        }
    }
    challenges.insert(addr, (message.clone(), now));

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
    /// Tier this session lands in — "owner", "editor", or "viewer". Echoed so
    /// the console can dress itself read-only on the very first render instead
    /// of waiting for a /auth/role round-trip.
    pub role: &'static str,
}

pub async fn verify(
    State(store): State<ChallengeStore>,
    Json(req): Json<VerifyRequest>,
) -> Result<Json<VerifyResponse>, (StatusCode, Json<serde_json::Value>)> {
    let addr = req.address.to_lowercase();

    // Check the challenge exists AND is still inside its window.
    {
        let now = chrono::Utc::now().timestamp();
        let challenges = store.read().await;
        match challenges.get(&addr) {
            Some((expected, issued)) if *expected == req.message && now - *issued < CHALLENGE_TTL => {}
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
                    None if open_signin() => {
                        // Open door: any key may hold a session. Without owner,
                        // whitelist, or invite it's a VIEWER — read-only
                        // everywhere (enforced in `auth_middleware`).
                        println!("· Viewer sign-in: {} (read-only)", addr);
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
                                    "Sign-in closed: signed-in address {} is not the owner ({}), not whitelisted, and no gate matched. Check which account is active in your wallet.",
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
                .join("build-fork")
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

    // Terms gate: the challenge for a not-yet-accepted address embeds the
    // terms, so a valid signature over a message carrying the marker IS the
    // acceptance. A message without it means a stale/forged challenge —
    // send the client back through sign-in.
    if !is_new_owner && !terms_satisfied(&addr) {
        if req.message.contains(TERMS_MARKER) {
            record_terms_acceptance(&addr);
            println!("✓ Terms v{} accepted by {}", TERMS_VERSION, addr);
        } else {
            return Err((
                StatusCode::FORBIDDEN,
                Json(serde_json::json!({
                    "error": "Terms of Use acceptance required — refresh and sign in again",
                    "terms_required": true,
                })),
            ));
        }
    }

    let token = mint_token(&addr);

    if is_new_owner {
        println!("✓ First user authenticated - set as owner: {}", addr);
    }

    let role = role_of(&addr);

    Ok(Json(VerifyResponse {
        token,
        address: addr,
        role,
    }))
}

/// Mint a bearer token for an authenticated identity: address:timestamp:hmac.
pub fn mint_token(address: &str) -> String {
    mint_token_inner(address, false)
}

/// Marker signed into tokens minted by redeeming a handoff QR. A handed-off
/// session is a *copy* of the owner's access, not proof of the wallet — the
/// marker lets /auth/handoff refuse it, so sign-in QRs can only ever
/// originate from a wallet-signed session (no QR-mints-QR chains).
pub const HANDOFF_TOKEN_MARK: &str = "ho";

/// Mint a bearer token for a session opened via handoff redemption:
/// address:timestamp:ho:hmac. Validates like a normal token everywhere
/// except handoff minting.
pub fn mint_handoff_token(address: &str) -> String {
    mint_token_inner(address, true)
}

fn mint_token_inner(address: &str, via_handoff: bool) -> String {
    let timestamp = chrono::Utc::now().timestamp();
    let payload = if via_handoff {
        format!("{}:{}:{}", address, timestamp, HANDOFF_TOKEN_MARK)
    } else {
        format!("{}:{}", address, timestamp)
    };
    let mut mac = HmacSha256::new_from_slice(get_secret()).unwrap();
    mac.update(payload.as_bytes());
    let sig = hex::encode(mac.finalize().into_bytes());
    format!("{}:{}", payload, sig)
}

/// True when a token carries the handoff marker. Purely structural — pair
/// with validate_token (which verifies the marker is inside the HMAC'd
/// payload, so it can't be stripped or forged onto a foreign token).
pub fn token_is_handoff(token: &str) -> bool {
    let parts: Vec<&str> = token.split(':').collect();
    parts.len() == 4 && parts[2] == HANDOFF_TOKEN_MARK
}

/// True when the request authenticated with a handoff-minted bearer token.
pub fn headers_carry_handoff_token(headers: &axum::http::HeaderMap) -> bool {
    headers
        .get(axum::http::header::AUTHORIZATION)
        .and_then(|v| v.to_str().ok())
        .and_then(|h| h.strip_prefix("Bearer "))
        .map(token_is_handoff)
        .unwrap_or(false)
}

// ── Viewer tier (open sign-in, read-only) ────────────────────────────
//
// Any key may open a session. A signer who isn't the owner, whitelisted, or
// holding a live invite is a VIEWER: a real identity — their address rides
// every read, the console greets them by it — with no write surface at all.
// One choke point enforces it (`read_only_refusal` below, wired into the auth
// middleware) rather than a check per handler: if a request isn't a read, a
// viewer can't make it, so no new endpoint can forget the rule.
//
// The owner closes the door again with {"open": false} in ~/.mod/build-fork/gate.json
// (same file as `gate_command`), which restores the old whitelist-only sign-in.

pub fn open_signin() -> bool {
    let read = || -> Option<bool> {
        let content = std::fs::read_to_string(gate_path()?).ok()?;
        let data: serde_json::Value = serde_json::from_str(&content).ok()?;
        data.get("open")?.as_bool()
    };
    read().unwrap_or(true)
}

/// The tier `address` sits in: "owner" (every power), "editor" (trusted to
/// edit — whitelist or live invite), "viewer" (signed in, reads only).
pub fn role_of(address: &str) -> &'static str {
    if is_owner(address) {
        "owner"
    } else if is_trusted(address) {
        "editor"
    } else {
        "viewer"
    }
}

/// Refuse a write from a viewer. GET/HEAD (and OPTIONS, which the CORS layer
/// normally answers first) are reads; everything else creates or changes
/// something, so it needs edit trust.
fn read_only_refusal(method: &axum::http::Method, address: &str) -> Option<Response> {
    use axum::http::Method;
    if matches!(*method, Method::GET | Method::HEAD | Method::OPTIONS) {
        return None;
    }
    if is_trusted(address) {
        return None;
    }
    Some(
        (
            StatusCode::FORBIDDEN,
            Json(serde_json::json!({
                "error": "Read-only session — this key can browse the orbit but not edit or create anything. Ask the owner for edit access.",
                "read_only": true,
                "role": "viewer",
            })),
        )
            .into_response(),
    )
}

// ── Middleware ────────────────────────────────────────────────────────

pub async fn auth_middleware(req: Request, next: Next) -> Response {
    // Try Bearer token first (Build API native token)
    let auth_header = req
        .headers()
        .get(header::AUTHORIZATION)
        .and_then(|v| v.to_str().ok())
        .unwrap_or("");

    if auth_header.starts_with("Bearer ") {
        let token = &auth_header[7..];
        return match validate_token(token) {
            Ok(addr) => match read_only_refusal(req.method(), &addr) {
                Some(refusal) => refusal,
                None => next.run(req).await,
            },
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
            Ok(addr) => match read_only_refusal(req.method(), &addr) {
                Some(refusal) => refusal,
                None => next.run(req).await,
            },
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
    // Format: address:timestamp:hmac, or address:timestamp:ho:hmac for
    // handoff-minted sessions (the marker is part of the signed payload).
    let parts: Vec<&str> = token.split(':').collect();
    let (address, timestamp_str, marked, provided_sig) = match parts.as_slice() {
        [a, t, s] => (*a, *t, false, *s),
        [a, t, m, s] if *m == HANDOFF_TOKEN_MARK => (*a, *t, true, *s),
        _ => return Err("Invalid token format".to_string()),
    };

    let timestamp: i64 = timestamp_str
        .parse()
        .map_err(|_| "Invalid timestamp".to_string())?;

    // Check expiry (24 hours). Guest tokens (walletless QR redemption) are
    // exempt here — they live and die with their grant, checked below.
    let is_guest = address.starts_with(GUEST_PREFIX);
    let now = chrono::Utc::now().timestamp();
    if !is_guest && now - timestamp > 86400 {
        return Err("Token expired".to_string());
    }

    // Verify HMAC
    let payload = if marked {
        format!("{}:{}:{}", address, timestamp, HANDOFF_TOKEN_MARK)
    } else {
        format!("{}:{}", address, timestamp)
    };
    let mut mac = HmacSha256::new_from_slice(get_secret()).unwrap();
    mac.update(payload.as_bytes());
    let expected = hex::encode(mac.finalize().into_bytes());

    // Constant-time: a byte-at-a-time `!=` leaks how much of a guessed tag was
    // right, and this compare runs on every request an attacker can make.
    if !ct_eq(expected.as_bytes(), provided_sig.as_bytes()) {
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

/// Read the owner address — config.json "owner" field takes priority, then ~/.mod/build-fork/owner.json
pub fn get_owner_address() -> Option<String> {
    // Priority 1: config.json "owner" field (live-editable)
    if let Some(owner) = read_config_owner() {
        return Some(owner);
    }

    // Priority 2: owner.json
    let owner_path = dirs::home_dir()?
        .join(".mod")
        .join("build-fork")
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
            std::path::PathBuf::from(format!("{}/mod/mod/orbit/build-fork/config.json", home))
        });

    let content = std::fs::read_to_string(&config_path).ok()?;
    let data: serde_json::Value = serde_json::from_str(&content).ok()?;
    let owner = data.get("owner").and_then(|v| v.as_str())?.to_lowercase();
    if owner.is_empty() { None } else { Some(owner) }
}

/// Check if an address is the system owner — the configured primary owner OR
/// one of the co-owner wallets (~/.mod/build-fork/owners.json). A co-owner IS the
/// owner everywhere this is called: full edit surface over every module, and
/// sudo powers (their signatures verify because sudo checks `is_owner`). The
/// list is meant for the owner's *own other wallets*, so it lives off-tree
/// with the rest of the private auth state and adding to it is itself a
/// sudo-gated owner operation (see api.rs /owners).
pub fn is_owner(address: &str) -> bool {
    if address.is_empty() {
        return false;
    }
    let addr = address.to_lowercase();
    if owner_addresses().iter().any(|o| o == &addr) {
        return true;
    }
    // Sudo-whitelisted addresses pass every owner gate: sudo access is the
    // owner's full power surface, delegated — sudo sessions, whitelist edits,
    // process control, destructive module ops, host filesystem. The owner
    // hands it out (and takes it back) from the whitelist card.
    matches!(whitelist_access(&addr), Some(WhitelistAccess::Sudo))
}

/// Every address that counts as the owner: the configured primary owner
/// first, then co-owners in file order. Lowercased, deduped.
pub fn owner_addresses() -> Vec<String> {
    let mut all: Vec<String> = get_owner_address().into_iter().collect();
    for a in read_co_owners() {
        if !all.contains(&a) {
            all.push(a);
        }
    }
    all
}

pub fn owners_path() -> Option<std::path::PathBuf> {
    Some(private_dir()?.join("owners.json"))
}

/// Read the lowercased co-owner list from ~/.mod/build-fork/owners.json. Accepts
/// either `["0x..", ..]` or `{"addresses": ["0x..", ..]}` like the whitelist.
/// Returns [] if the file is absent or malformed — never errors.
pub fn read_co_owners() -> Vec<String> {
    let path = match owners_path() {
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
    let arr = parsed
        .as_array()
        .cloned()
        .or_else(|| parsed.get("addresses").and_then(|v| v.as_array()).cloned())
        .unwrap_or_default();
    arr.into_iter()
        .filter_map(|v| v.as_str().map(|s| s.to_lowercase()))
        .collect()
}

/// Persist the co-owner list back to ~/.mod/build-fork/owners.json.
pub fn write_co_owners(addresses: &[String]) -> Result<(), String> {
    let path = owners_path().ok_or("no home dir")?;
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|e| format!("mkdir: {}", e))?;
    }
    let json = serde_json::to_string_pretty(&addresses).map_err(|e| format!("encode: {}", e))?;
    std::fs::write(&path, json).map_err(|e| format!("write: {}", e))
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
// people. Everything lives off-repo in ~/.mod/build-fork/grants.json next to the
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
    /// Module scope: None = every module (the default); Some(list) confines
    /// the holder's edit powers to just those modules.
    #[serde(default)]
    pub modules: Option<Vec<String>>,
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
    /// Module scope stamped from the grant at redemption (None = every module),
    /// so access stays correctly scoped even if the grant row is later pruned.
    #[serde(default)]
    pub modules: Option<Vec<String>>,
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

/// Length-independent, byte-blind equality for secrets (tokens, key hashes).
fn ct_eq(a: &[u8], b: &[u8]) -> bool {
    a.len() == b.len() && a.iter().zip(b).fold(0u8, |acc, (x, y)| acc | (x ^ y)) == 0
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

/// Normalize a requested module scope: trim, drop empties, dedupe (order
/// preserved). An empty/absent list means "every module" and collapses to None.
fn normalize_modules(modules: Option<Vec<String>>) -> Option<Vec<String>> {
    let list: Vec<String> = modules?
        .into_iter()
        .map(|m| m.trim().to_string())
        .filter(|m| !m.is_empty())
        .fold(Vec::new(), |mut acc, m| {
            if !acc.contains(&m) {
                acc.push(m);
            }
            acc
        });
    if list.is_empty() { None } else { Some(list) }
}

/// Mint a new grant. `ttl` is clamped by the caller; `key` (if any) is hashed,
/// never stored in the clear. `modules` (if any) confines the holder's edit
/// powers to those modules — None means everything. Returns the created grant.
pub fn create_grant(
    ttl: i64,
    key: Option<&str>,
    label: Option<&str>,
    modules: Option<Vec<String>>,
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
        modules: normalize_modules(modules),
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
            Some(k) if ct_eq(sha256_hex(k).as_bytes(), expected.as_bytes()) => {}
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
        modules: grant.modules.clone(),
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

/// What a trusted address may edit. Owner and whitelisted editors always get
/// everything; grant holders get whatever scope their invite carried.
#[derive(Debug, Clone, PartialEq)]
pub enum EditScope {
    /// Every module — the owner, whitelisted editors, and unscoped grants.
    All,
    /// Only these modules (compartmentalized-risk invites).
    Modules(Vec<String>),
}

/// Resolve the edit scope for `address`: None = not trusted at all. A holder
/// of several active grants gets the union of their scopes; any unscoped
/// grant (or owner/whitelist status) widens to All.
pub fn edit_scope(address: &str) -> Option<EditScope> {
    if address.is_empty() {
        return None;
    }
    let addr = address.to_lowercase();
    if is_owner(&addr) || read_whitelist().iter().any(|w| w == &addr) {
        return Some(EditScope::All);
    }
    grant_edit_scope(&addr)
}

/// Edit scope conferred by live QR invites ALONE — grants, not owner or
/// whitelist status. This is what widens a guest past their peer workspace
/// into the real module tree (see `userspace::resolve_path`): the owner
/// minted the invite naming those modules, so the invite must actually buy
/// edit rights there. The whitelist deliberately does NOT widen anything —
/// whitelisted addresses stay peers for filesystem purposes.
pub fn grant_edit_scope(address: &str) -> Option<EditScope> {
    if address.is_empty() {
        return None;
    }
    let addr = address.to_lowercase();
    let now = chrono::Utc::now().timestamp();
    let file = read_grants();
    let live: std::collections::HashSet<&str> = file
        .grants
        .iter()
        .filter(|g| !g.revoked && g.exp > now)
        .map(|g| g.id.as_str())
        .collect();
    let mut modules: Vec<String> = Vec::new();
    let mut any = false;
    for r in file
        .redemptions
        .iter()
        .filter(|r| r.address == addr && r.exp > now && live.contains(r.grant.as_str()))
    {
        any = true;
        match &r.modules {
            None => return Some(EditScope::All),
            Some(list) => {
                for m in list {
                    if !modules.contains(m) {
                        modules.push(m.clone());
                    }
                }
            }
        }
    }
    if any { Some(EditScope::Modules(modules)) } else { None }
}

/// Module-aware trust check: true when `address` is trusted AND `module`
/// falls inside its edit scope. This is the gate for per-module editor
/// powers (MR verdicts, MR close); `is_trusted` stays the coarse gate.
pub fn can_edit_module(address: &str, module: &str) -> bool {
    match edit_scope(address) {
        Some(EditScope::All) => true,
        Some(EditScope::Modules(list)) => list.iter().any(|m| m == module),
        None => false,
    }
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

/// Default lifetime of a handoff QR. Short on purpose: the code is a
/// bearer capability for the minter's whole identity. The minter may pick
/// a different TTL, clamped to [HANDOFF_TTL_MIN, HANDOFF_TTL_MAX].
pub const HANDOFF_TTL: i64 = 300;
pub const HANDOFF_TTL_MIN: i64 = 60;
pub const HANDOFF_TTL_MAX: i64 = 86_400;

fn handoff_store() -> &'static std::sync::Mutex<HashMap<String, Handoff>> {
    HANDOFFS.get_or_init(|| std::sync::Mutex::new(HashMap::new()))
}

/// Mint a single-use handoff code for `address`. `ttl` is the requested
/// lifetime in seconds (None → HANDOFF_TTL), clamped so a caller can't mint
/// an effectively-immortal identity capability. Returns (code, expiry).
pub fn create_handoff(address: &str, ttl: Option<i64>) -> (String, i64) {
    let now = chrono::Utc::now().timestamp();
    let code = hex::encode(rand::random::<[u8; 16]>());
    let ttl = ttl.unwrap_or(HANDOFF_TTL).clamp(HANDOFF_TTL_MIN, HANDOFF_TTL_MAX);
    let exp = now + ttl;
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

/// Off-chain config dir (~/.mod/build-fork/) — holds owner.json, whitelist.json, gate.json.
/// Kept off-repo because the whitelist is private; mounted into the container as a volume.
pub fn private_dir() -> Option<std::path::PathBuf> {
    Some(dirs::home_dir()?.join(".mod").join("build-fork"))
}

pub fn whitelist_path() -> Option<std::path::PathBuf> {
    Some(private_dir()?.join("whitelist.json"))
}

fn gate_path() -> Option<std::path::PathBuf> {
    Some(private_dir()?.join("gate.json"))
}

// ── Whitelist entries (per-address access levels) ────────────────────
//
// Each whitelisted address carries an *access level*:
//   • Sudo          — everything the owner can do (passes every is_owner gate)
//   • All           — edit every module, but no owner-only powers (the default)
//   • Modules(list) — edit only the named modules
//
// On disk (~/.mod/build-fork/whitelist.json) an entry is
// `{"address": "0x..", "access": "sudo" | "all" | ["mod", ..]}`. Bare-string
// entries and the legacy `{"addresses": [..]}` wrapper still parse (as All)
// so a pre-existing file keeps working; the first write migrates the format.

#[derive(Debug, Clone, PartialEq)]
pub enum WhitelistAccess {
    Sudo,
    All,
    Modules(Vec<String>),
}

#[derive(Debug, Clone)]
pub struct WhitelistEntry {
    pub address: String,
    pub access: WhitelistAccess,
}

impl WhitelistAccess {
    /// Parse the wire/disk repr: `"sudo"` | `"all"` | `["mod", ..]`.
    /// Absent/null means All so old callers keep their old semantics.
    pub fn from_json(v: Option<&serde_json::Value>) -> Result<Self, String> {
        match v {
            None | Some(serde_json::Value::Null) => Ok(WhitelistAccess::All),
            Some(serde_json::Value::String(s)) => match s.trim().to_lowercase().as_str() {
                "sudo" => Ok(WhitelistAccess::Sudo),
                "all" | "" => Ok(WhitelistAccess::All),
                other => Err(format!(
                    "unknown access level '{}' — expected \"sudo\", \"all\", or a module list",
                    other
                )),
            },
            Some(serde_json::Value::Array(items)) => {
                let mods: Vec<String> = items
                    .iter()
                    .filter_map(|m| m.as_str().map(str::to_string))
                    .collect();
                match normalize_modules(Some(mods)) {
                    Some(list) => Ok(WhitelistAccess::Modules(list)),
                    None => Err("module list is empty — pass \"all\" for unscoped access".to_string()),
                }
            }
            Some(_) => Err("access must be \"sudo\", \"all\", or a module list".to_string()),
        }
    }

    pub fn to_json(&self) -> serde_json::Value {
        match self {
            WhitelistAccess::Sudo => serde_json::json!("sudo"),
            WhitelistAccess::All => serde_json::json!("all"),
            WhitelistAccess::Modules(list) => serde_json::json!(list),
        }
    }
}

/// Read whitelist entries from ~/.mod/build-fork/whitelist.json. Addresses are
/// lowercased and deduped (first entry wins). Returns [] if the file is
/// absent or malformed — never errors.
pub fn read_whitelist_entries() -> Vec<WhitelistEntry> {
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
    // Accept `[entry, ..]` or the legacy `{"addresses": ["0x..", ..]}` wrapper.
    let arr = parsed
        .as_array()
        .cloned()
        .or_else(|| parsed.get("addresses").and_then(|v| v.as_array()).cloned())
        .unwrap_or_default();
    let mut entries: Vec<WhitelistEntry> = Vec::new();
    for item in arr {
        let (address, access) = match &item {
            serde_json::Value::String(s) => (s.clone(), WhitelistAccess::All),
            serde_json::Value::Object(o) => {
                let addr = match o.get("address").and_then(|v| v.as_str()) {
                    Some(a) => a.to_string(),
                    None => continue,
                };
                let access = WhitelistAccess::from_json(o.get("access")).unwrap_or(WhitelistAccess::All);
                (addr, access)
            }
            _ => continue,
        };
        let address = address.to_lowercase();
        if !address.is_empty() && !entries.iter().any(|e| e.address == address) {
            entries.push(WhitelistEntry { address, access });
        }
    }
    entries
}

/// Every whitelisted address regardless of access level — the sign-in gate.
pub fn read_whitelist() -> Vec<String> {
    read_whitelist_entries().into_iter().map(|e| e.address).collect()
}

/// The access level for `address`, if it's whitelisted at all.
pub fn whitelist_access(address: &str) -> Option<WhitelistAccess> {
    let addr = address.to_lowercase();
    read_whitelist_entries()
        .into_iter()
        .find(|e| e.address == addr)
        .map(|e| e.access)
}

/// Persist whitelist entries back to ~/.mod/build-fork/whitelist.json (used by the
/// owner-only management endpoints in api.rs). Always writes the entry form.
pub fn write_whitelist_entries(entries: &[WhitelistEntry]) -> Result<(), String> {
    let path = whitelist_path().ok_or("no home dir")?;
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|e| format!("mkdir: {}", e))?;
    }
    let arr: Vec<serde_json::Value> = entries
        .iter()
        .map(|e| serde_json::json!({ "address": e.address, "access": e.access.to_json() }))
        .collect();
    let json = serde_json::to_string_pretty(&arr).map_err(|e| format!("encode: {}", e))?;
    std::fs::write(&path, json).map_err(|e| format!("write: {}", e))
}

/// Read the optional `gate_command` from ~/.mod/build-fork/gate.json — a shell command that the
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
        let message = "Hello Build Jobs";
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
    fn test_normalize_modules_scope() {
        // Absent / empty / all-blank scopes collapse to None (= everything).
        assert_eq!(normalize_modules(None), None);
        assert_eq!(normalize_modules(Some(vec![])), None);
        assert_eq!(normalize_modules(Some(vec!["  ".into(), "".into()])), None);
        // Trim + dedupe, order preserved.
        assert_eq!(
            normalize_modules(Some(vec![
                " store ".into(),
                "claude".into(),
                "store".into(),
            ])),
            Some(vec!["store".to_string(), "claude".to_string()])
        );
    }

    #[test]
    fn test_handoff_roundtrip_single_use() {
        let addr = "0xABCDef1234567890abcdef1234567890abcdef12";
        let (code, exp) = create_handoff(addr, None);
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
    fn test_handoff_token_marked_and_validates() {
        ensure_secret();
        let addr = "0xabcdef1234567890abcdef1234567890abcdef12";

        // Handoff-minted tokens carry the signed marker and still validate…
        let ho = mint_handoff_token(addr);
        assert!(token_is_handoff(&ho));
        assert_eq!(validate_token(&ho).expect("marked token invalid"), addr);

        // …wallet-minted ones don't…
        let wallet = mint_token(addr);
        assert!(!token_is_handoff(&wallet));
        assert_eq!(validate_token(&wallet).unwrap(), addr);

        // …and the marker can't be stripped (or forged in) without breaking
        // the HMAC, so a handed-off session can't launder itself into a
        // wallet-shaped token.
        let parts: Vec<&str> = ho.split(':').collect();
        let stripped = format!("{}:{}:{}", parts[0], parts[1], parts[3]);
        assert!(validate_token(&stripped).is_err());
        let wparts: Vec<&str> = wallet.split(':').collect();
        let forged = format!("{}:{}:{}:{}", wparts[0], wparts[1], HANDOFF_TOKEN_MARK, wparts[2]);
        assert!(validate_token(&forged).is_err());
    }

    #[test]
    fn test_handoff_ttl_clamped() {
        let addr = "0xabcdef1234567890abcdef1234567890abcdef12";
        let now = chrono::Utc::now().timestamp();

        // Requested TTL is honored within bounds…
        let (_, exp) = create_handoff(addr, Some(3600));
        assert!((exp - now - 3600).abs() <= 2);

        // …and clamped outside them (can't mint an immortal or instant code).
        let (_, exp) = create_handoff(addr, Some(999_999_999));
        assert!(exp - now <= HANDOFF_TTL_MAX + 2);
        let (_, exp) = create_handoff(addr, Some(0));
        assert!(exp - now >= HANDOFF_TTL_MIN - 2);
    }

    #[test]
    fn test_viewer_is_read_only() {
        // Reads the real $HOME's owner config — take the shared lock so a
        // sibling test's TempHome can't swap it out mid-assertion.
        let _lock = crate::home_test_lock();
        let stranger = "0x1111111111111111111111111111111111111111";
        assert_eq!(role_of(stranger), "viewer");
        // Reads pass, anything that could create or change state doesn't.
        assert!(read_only_refusal(&axum::http::Method::GET, stranger).is_none());
        assert!(read_only_refusal(&axum::http::Method::HEAD, stranger).is_none());
        assert!(read_only_refusal(&axum::http::Method::POST, stranger).is_some());
        assert!(read_only_refusal(&axum::http::Method::PUT, stranger).is_some());
        assert!(read_only_refusal(&axum::http::Method::DELETE, stranger).is_some());
        // Trusted keys are untouched by the gate.
        if let Some(owner) = get_owner_address() {
            assert_eq!(role_of(&owner), "owner");
            assert!(read_only_refusal(&axum::http::Method::POST, &owner).is_none());
        }
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
