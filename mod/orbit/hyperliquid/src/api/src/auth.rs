//! mod **protocol-auth** — MetaMask sign-in tokens, verified statelessly.
//!
//! The browser builds a time-bounded bearer token (same scheme as
//! `mod/core/server/auth` and the venice/polymarket gateways):
//!
//!   1. `sigData = JSON.stringify({ data, time })`   (compact, no spaces)
//!   2. `signature = personal_sign(sigData)`         (EIP-191, MetaMask)
//!   3. `token = base64url(JSON.stringify({ data, time, key, signature }))`
//!
//! sent as `Authorization: Bearer <token>`. Verification reconstructs
//! `sigData` byte-for-byte, recovers the signer from the signature, and
//! requires it to equal the embedded `key` within `max_age` seconds. No
//! server-side session table — identity IS the recovered address.
//!
//! The guard is deny-by-default: public market/analytics reads are
//! allowlisted, everything user-scoped (follows, signals, signer, trade
//! actions, live engine) needs a token, and any `eoa`/`follower`/`owner`
//! the request names must equal the token's address — the API never again
//! trusts a client-supplied address on its own.
//!
//! `HYPERLIQUID_ACCESS_OPEN=1` disables the guard (local dev / CI only).

use std::sync::Arc;

use axum::body::Body;
use axum::extract::{Request, State};
use axum::http::{Method, StatusCode};
use axum::middleware::Next;
use axum::response::{IntoResponse, Response};
use axum::Json;
use base64::Engine;
use k256::ecdsa::{RecoveryId, Signature, VerifyingKey};
use serde_json::{json, Value};

use crate::signer::{address_from_pubkey, keccak256};
use crate::AppState;

/// Recovered signer address of the authenticated request, inserted as a
/// request extension so handlers can read who is calling.
#[derive(Clone)]
pub struct AuthedUser(pub String);

pub struct AuthCfg {
    pub max_age: u64,
    pub open: bool,
}

impl AuthCfg {
    pub fn from_env() -> Arc<Self> {
        // 7 days mirrors the venice/store gateways.
        let max_age = std::env::var("HYPERLIQUID_SESSION_TTL")
            .ok()
            .and_then(|s| s.parse().ok())
            .unwrap_or(86_400 * 7);
        let open = std::env::var("HYPERLIQUID_ACCESS_OPEN").ok().as_deref() == Some("1");
        if open {
            tracing::warn!("auth guard DISABLED via HYPERLIQUID_ACCESS_OPEN=1 — dev/CI only");
        } else {
            tracing::info!("auth guard: mod protocol-auth, session ttl {}s", max_age);
        }
        Arc::new(Self { max_age, open })
    }
}

// ─── Token verification (mirrors mod/core/server/auth) ──────────────────

/// Why a token failed. The distinction is the whole point: an **expired**
/// session is one signature away from working again — the console re-mints
/// and retries silently — while a malformed one is a bug worth showing.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum TokenError {
    Expired(String),
    Invalid(String),
}

impl std::fmt::Display for TokenError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            TokenError::Expired(m) | TokenError::Invalid(m) => f.write_str(m),
        }
    }
}

// `?` on the plain-string error paths below stays terse.
impl From<String> for TokenError {
    fn from(s: String) -> Self { TokenError::Invalid(s) }
}
impl From<&str> for TokenError {
    fn from(s: &str) -> Self { TokenError::Invalid(s.to_string()) }
}

/// Human duration for token-age copy — "3 days", not "268241s".
fn human_secs(s: f64) -> String {
    let s = s.abs() as i64;
    match s {
        0..=90 => format!("{s}s"),
        91..=5400 => format!("{}m", s / 60),
        5401..=172_800 => format!("{}h", s / 3600),
        _ => format!("{}d", s / 86_400),
    }
}

/// Verify a protocol-auth bearer token. On success returns the signer's
/// lowercase 0x address; on failure a typed error safe for a 401 body.
pub fn verify_token(token: &str, max_age_secs: u64) -> Result<String, TokenError> {
    let raw = b64url_decode(token.trim()).map_err(|e| format!("bad token encoding: {e}"))?;
    let envelope: Value =
        serde_json::from_slice(&raw).map_err(|e| format!("bad token json: {e}"))?;

    let data = envelope.get("data").cloned().unwrap_or(Value::Object(Default::default()));
    let time = envelope.get("time").and_then(|v| v.as_str()).ok_or("token missing time")?;
    let key = envelope.get("key").and_then(|v| v.as_str()).ok_or("token missing key")?;
    let signature = envelope
        .get("signature")
        .and_then(|v| v.as_str())
        .ok_or("token missing signature")?;

    if !key.starts_with("0x") || key.len() != 42 {
        return Err("token key is not an ethereum address".into());
    }

    let t: f64 = time.parse().map_err(|_| "token time not a number".to_string())?;
    let now = chrono::Utc::now().timestamp() as f64;
    let age = now - t;
    if age.abs() > max_age_secs as f64 {
        return Err(TokenError::Expired(format!(
            "sign-in is {} old, and sessions last {}",
            human_secs(age),
            human_secs(max_age_secs as f64),
        )));
    }

    // Reconstruct the exact signed string: {"data":<data>,"time":"<time>"}.
    // preserve_order (already on for HL action signing) keeps `data`'s keys
    // in the order they were signed in.
    let data_str = serde_json::to_string(&data).map_err(|e| e.to_string())?;
    let time_str = serde_json::to_string(time).map_err(|e| e.to_string())?; // adds quotes
    let sig_data = format!("{{\"data\":{data_str},\"time\":{time_str}}}");

    // Two accepted signing modes, mirroring the Python verifier:
    // EIP-191 personal_sign (browser wallets) and plain keccak256(message)
    // (in-process mod keys).
    let msg = sig_data.as_bytes();
    let eip191_prehash = {
        let prefix = format!("\x19Ethereum Signed Message:\n{}", msg.len());
        let mut buf = Vec::with_capacity(prefix.len() + msg.len());
        buf.extend_from_slice(prefix.as_bytes());
        buf.extend_from_slice(msg);
        keccak256(&buf)
    };
    let plain_prehash = keccak256(msg);

    for prehash in [eip191_prehash, plain_prehash] {
        if let Ok(addr) = recover_address(&prehash, signature) {
            if addr.eq_ignore_ascii_case(key) {
                return Ok(key.to_lowercase());
            }
        }
    }
    Err(TokenError::Invalid("signature does not match token key".into()))
}

fn recover_address(prehash: &[u8; 32], signature_hex: &str) -> Result<String, String> {
    let sig_hex = signature_hex.strip_prefix("0x").unwrap_or(signature_hex);
    let sig_bytes = hex::decode(sig_hex).map_err(|_| "signature not hex".to_string())?;
    if sig_bytes.len() != 65 {
        return Err(format!("signature must be 65 bytes, got {}", sig_bytes.len()));
    }
    let rec_raw = sig_bytes[64];
    let rec_id = if rec_raw >= 27 { rec_raw - 27 } else { rec_raw };
    let recovery_id = RecoveryId::from_byte(rec_id).ok_or("bad recovery id")?;
    let signature = Signature::from_slice(&sig_bytes[..64]).map_err(|e| format!("bad sig: {e}"))?;
    let vk = VerifyingKey::recover_from_prehash(prehash, &signature, recovery_id)
        .map_err(|e| format!("recover failed: {e}"))?;
    Ok(address_from_pubkey(&vk))
}

/// Unpadded (or padded) base64url, matching the JS client's stripped `=`.
fn b64url_decode(s: &str) -> Result<Vec<u8>, String> {
    base64::engine::general_purpose::URL_SAFE_NO_PAD
        .decode(s.trim_end_matches('='))
        .map_err(|e| e.to_string())
}

// ─── Route classification ───────────────────────────────────────────────

/// Pure computations that happen to need a request body. `/indexes/auto`
/// ranks the **public** trader board and hands back proposed basket legs —
/// it reads nothing private and stores nothing. Gating it meant the strat
/// builder demanded a signature before the user had built anything worth
/// saving, and its 401 tore down the session on the way out. A preview is a
/// read; it is priced like one.
fn is_public_post(path: &str) -> bool {
    matches!(path, "/indexes/auto")
}

/// Public reads: market data, analytics, and index browsing — everything a
/// signed-out visitor may see. All writes and all user-scoped reads are gated.
pub fn is_public(method: &Method, path: &str) -> bool {
    if *method == Method::POST {
        return is_public_post(path);
    }
    if *method != Method::GET {
        return false;
    }
    matches!(path, "/" | "/health" | "/status" | "/mids" | "/leaderboard" | "/scan/progress"
        | "/wallet/config" | "/indexes" | "/vaults" | "/market/meta"
        // MCP discovery: the tool schema is the module's public fn surface.
        | "/mcp/schema"
        // Agent readiness (is a model key configured, how many tools). Asking
        // — POST /ask — spends model credits and stays token-gated.
        | "/ask/status"
        // deposit GETs are on-chain-public data (chain registry, balances,
        // bridge status) — watch-mode users see them without signing in.
        | "/deposit/chains" | "/deposit/balances" | "/deposit/status"
        // Sizing arithmetic over a leader's PUBLIC portfolio: "what would
        // $250 in this trader buy me?" is a read, and asking it before
        // connecting a wallet is the whole point.
        | "/invest/preview")
        || ["/orderbook/", "/candles/", "/user/", "/traders/", "/trader/", "/vaults/", "/indexes/"]
            .iter()
            .any(|p| path.starts_with(p))
}

/// Gated GET endpoints that list per-user data MUST be scoped to the caller —
/// an unscoped query would enumerate every user's trading config.
fn required_query_key(path: &str) -> Option<&'static str> {
    match path {
        "/follows" | "/signals" => Some("follower"),
        "/live/status" | "/agent/status" => Some("eoa"),
        "/invest" => Some("investor"),
        _ => None,
    }
}

/// Very small query-string scan (addresses are plain hex — no percent-decoding
/// concerns). Returns the value of `key` if present.
fn query_param<'a>(query: &'a str, key: &str) -> Option<&'a str> {
    query.split('&').find_map(|kv| {
        let (k, v) = kv.split_once('=')?;
        (k == key).then_some(v)
    })
}

// ─── Denials ────────────────────────────────────────────────────────────
//
// A gate that answers "401 unauthorized" and nothing else pushes its job onto
// whoever called it: the console can only reprint the status code, and the
// person reading it learns nothing about what to do next. Every refusal here
// carries a stable `reason` code, a sentence written for a human, and
// `sign_in` — whether a fresh signature would plausibly fix it. The web client
// re-mints and retries exactly once on `sign_in`, so an expired session costs
// one MetaMask prompt instead of a dead end.

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Denial {
    /// No Authorization header at all — the caller is signed out.
    NoToken,
    /// A token that isn't a token: bad base64/JSON, or a signature that
    /// doesn't recover to the address it claims.
    BadToken,
    /// A well-formed token past its freshness window.
    ExpiredToken,
    /// The request names an address that isn't the token's own.
    WrongWallet,
    /// The row exists and belongs to a different wallet.
    NotOwner,
    /// A user-scoped list query with no scope — would enumerate everyone.
    Unscoped,
    /// Body over the inspection limit.
    OversizeBody,
}

impl Denial {
    fn code(self) -> &'static str {
        match self {
            Denial::NoToken => "no_token",
            Denial::BadToken => "bad_token",
            Denial::ExpiredToken => "expired_token",
            Denial::WrongWallet => "wrong_wallet",
            Denial::NotOwner => "not_owner",
            Denial::Unscoped => "unscoped_query",
            Denial::OversizeBody => "payload_too_large",
        }
    }

    fn status(self) -> StatusCode {
        match self {
            Denial::NoToken | Denial::BadToken | Denial::ExpiredToken => StatusCode::UNAUTHORIZED,
            Denial::WrongWallet | Denial::NotOwner | Denial::Unscoped => StatusCode::FORBIDDEN,
            Denial::OversizeBody => StatusCode::PAYLOAD_TOO_LARGE,
        }
    }

    /// `true` when minting a fresh token could plausibly resolve this, which
    /// is the client's signal to re-sign and retry once instead of failing.
    fn sign_in(self) -> bool {
        matches!(self, Denial::NoToken | Denial::BadToken | Denial::ExpiredToken)
    }

    /// The short line the UI shows. Says what happened and what to do.
    fn message(self) -> &'static str {
        match self {
            Denial::NoToken => "Sign in with your wallet to do that.",
            Denial::BadToken => "That sign-in couldn't be verified. Sign in again.",
            Denial::ExpiredToken => "Your session expired. Sign in again to continue.",
            Denial::WrongWallet => "That request names a different wallet than the one you signed in with.",
            Denial::NotOwner => "That belongs to a different wallet.",
            Denial::Unscoped => "This list is per-wallet — scope it to your own address.",
            Denial::OversizeBody => "That request body is too large.",
        }
    }
}

/// Build the refusal. `detail` is the specific, developer-facing half;
/// `message` is the half a person reads.
fn deny(kind: Denial, detail: impl Into<String>) -> Response {
    (kind.status(), Json(json!({
        // `error` stays the coarse status word older clients match on.
        "error": if kind.status() == StatusCode::UNAUTHORIZED { "unauthorized" }
                 else if kind.status() == StatusCode::FORBIDDEN { "forbidden" }
                 else { "payload too large" },
        "reason": kind.code(),
        "message": kind.message(),
        "sign_in": kind.sign_in(),
        "gate": "hyperliquid-auth",
        "detail": detail.into(),
    })))
        .into_response()
}

// ─── Guard middleware ───────────────────────────────────────────────────

pub async fn guard(State(state): State<AppState>, req: Request, next: Next) -> Response {
    if state.auth.open {
        return next.run(req).await;
    }
    // CORS preflights carry no Authorization header by spec.
    if req.method() == Method::OPTIONS {
        return next.run(req).await;
    }
    let path = req.uri().path().to_string();
    if is_public(req.method(), &path) {
        return next.run(req).await;
    }
    // MCP is a transport, not a privilege: `tools/list` and public tools must
    // work signed-out. Each tool call re-enters this guard as a loopback
    // request carrying the caller's own header, so gating happens there. All
    // three MCP entrances (streamable HTTP, and the HTTP+SSE pair) are the
    // same transport and get the same treatment.
    if matches!(path.as_str(), "/mcp" | "/sse" | "/messages") {
        return next.run(req).await;
    }

    let token = match req
        .headers()
        .get(axum::http::header::AUTHORIZATION)
        .and_then(|h| h.to_str().ok())
        .and_then(|h| h.strip_prefix("Bearer ").or_else(|| h.strip_prefix("bearer ")))
    {
        Some(t) => t,
        None => {
            return deny(
                Denial::NoToken,
                "send a mod protocol-auth token as `Authorization: Bearer <token>`",
            )
        }
    };
    let addr = match verify_token(token, state.auth.max_age) {
        Ok(a) => a,
        Err(TokenError::Expired(e)) => return deny(Denial::ExpiredToken, e),
        Err(TokenError::Invalid(e)) => return deny(Denial::BadToken, e),
    };

    // Query-string identity binding.
    let query = req.uri().query().unwrap_or("").to_string();
    for key in ["eoa", "follower", "owner", "investor"] {
        if let Some(v) = query_param(&query, key) {
            if !v.eq_ignore_ascii_case(&addr) {
                return deny(Denial::WrongWallet, format!(
                    "?{key}={v} is not the wallet this token signs for ({addr})"));
            }
        }
    }
    if req.method() == Method::GET {
        if let Some(key) = required_query_key(&path) {
            if query_param(&query, key).is_none() {
                return deny(Denial::Unscoped, format!(
                    "scope this query to your signed-in wallet with ?{key}={addr}"));
            }
        }
    }
    // Resource-ownership binding for id-routes: only the creator of a follow
    // or index may mutate it (token alone must not unlock other users' rows).
    let method = req.method().clone();
    if method != Method::GET {
        if let Some(rest) = path.strip_prefix("/follows/") {
            let id = rest.split('/').next().unwrap_or("");
            if let Some(f) = state.store.list_follows(None).into_iter().find(|f| f.id == id) {
                if !f.follower.eq_ignore_ascii_case(&addr) {
                    return deny(Denial::NotOwner, format!("follow {id} is owned by {}", f.follower));
                }
            }
        }
        if let Some(rest) = path.strip_prefix("/indexes/") {
            let id = rest.split('/').next().unwrap_or("");
            if id != "auto" {
                if let Some(idx) = state.store.get_index(id) {
                    if !idx.owner.eq_ignore_ascii_case(&addr) {
                        return deny(Denial::NotOwner, format!("strat {id} is owned by {}", idx.owner));
                    }
                }
            }
        }
    }

    // Body identity binding: any eoa/follower/owner named in a JSON body must
    // be the signed-in wallet. Buffer (≤1 MiB), inspect, and replay the bytes.
    let (mut parts, body) = req.into_parts();
    let bytes = match axum::body::to_bytes(body, 1 << 20).await {
        Ok(b) => b,
        Err(_) => return deny(Denial::OversizeBody, "request body exceeds 1 MiB"),
    };
    if !bytes.is_empty() {
        if let Ok(Value::Object(map)) = serde_json::from_slice::<Value>(&bytes) {
            for key in ["eoa", "follower", "owner", "investor"] {
                if let Some(v) = map.get(key).and_then(|v| v.as_str()) {
                    if !v.eq_ignore_ascii_case(&addr) {
                        return deny(Denial::WrongWallet, format!(
                            "body `{key}` is {v}, but this token signs for {addr}"));
                    }
                }
            }
        }
    }
    parts.extensions.insert(AuthedUser(addr));
    next.run(Request::from_parts(parts, Body::from(bytes))).await
}

#[cfg(test)]
mod tests {
    use super::*;
    use k256::ecdsa::signature::hazmat::PrehashSigner;
    use k256::ecdsa::SigningKey;

    fn make_token(key: &SigningKey, addr: &str, time: f64, eip191: bool) -> String {
        let data = json!({"scope": "hyperliquid"});
        let time_s = format!("{time}");
        let sig_data = format!(
            "{{\"data\":{},\"time\":{}}}",
            serde_json::to_string(&data).unwrap(),
            serde_json::to_string(&time_s).unwrap(),
        );
        let prehash = if eip191 {
            let prefix = format!("\x19Ethereum Signed Message:\n{}", sig_data.len());
            keccak256(&[prefix.as_bytes(), sig_data.as_bytes()].concat())
        } else {
            keccak256(sig_data.as_bytes())
        };
        let (sig, recid): (Signature, RecoveryId) = key.sign_prehash(&prehash).unwrap();
        let mut out = [0u8; 65];
        out[..64].copy_from_slice(&sig.to_bytes());
        out[64] = 27 + u8::from(recid);
        let envelope = json!({
            "data": data,
            "time": time_s,
            "key": addr,
            "signature": format!("0x{}", hex::encode(out)),
        });
        base64::engine::general_purpose::URL_SAFE_NO_PAD
            .encode(serde_json::to_vec(&envelope).unwrap())
    }

    #[test]
    fn accepts_both_signing_modes() {
        let key = SigningKey::random(&mut rand::rngs::OsRng);
        let addr = {
            let vk = key.verifying_key();
            address_from_pubkey(vk)
        };
        let now = chrono::Utc::now().timestamp() as f64;
        for eip191 in [true, false] {
            let token = make_token(&key, &addr, now, eip191);
            assert_eq!(verify_token(&token, 3600).unwrap(), addr.to_lowercase());
        }
    }

    #[test]
    fn a_preview_is_a_read() {
        // /indexes/auto only ranks the public board. Gating it made the strat
        // builder demand a signature before there was anything to save.
        assert!(is_public(&Method::POST, "/indexes/auto"));
        // …and nothing else about POST loosened up.
        assert!(!is_public(&Method::POST, "/indexes"));
        assert!(!is_public(&Method::POST, "/trade"));
        assert!(!is_public(&Method::POST, "/live/start"));
        assert!(!is_public(&Method::DELETE, "/indexes/auto"));
        assert!(!is_public(&Method::PATCH, "/indexes/abc"));
    }

    #[test]
    fn expiry_and_forgery_are_different_answers() {
        let key = SigningKey::random(&mut rand::rngs::OsRng);
        let addr = address_from_pubkey(key.verifying_key());
        let now = chrono::Utc::now().timestamp() as f64;

        // Stale → recoverable, the client re-signs.
        match verify_token(&make_token(&key, &addr, now - 7200.0, true), 3600) {
            Err(TokenError::Expired(_)) => {}
            other => panic!("stale token should be Expired, got {other:?}"),
        }
        // Wrong signer → not recoverable by re-signing the same claim.
        let other = "0x000000000000000000000000000000000000dead";
        match verify_token(&make_token(&key, other, now, true), 3600) {
            Err(TokenError::Invalid(_)) => {}
            other => panic!("mismatched key should be Invalid, got {other:?}"),
        }
        match verify_token("not-a-token", 3600) {
            Err(TokenError::Invalid(_)) => {}
            other => panic!("garbage should be Invalid, got {other:?}"),
        }
    }

    #[test]
    fn every_denial_says_what_to_do() {
        use Denial::*;
        for d in [NoToken, BadToken, ExpiredToken, WrongWallet, NotOwner, Unscoped, OversizeBody] {
            assert!(!d.code().is_empty());
            assert!(d.message().ends_with('.'), "{:?}: message should read as a sentence", d);
            // Only the 401 family is worth re-signing for; a 403 means the
            // right wallet is a different wallet, and re-signing loops.
            assert_eq!(d.sign_in(), d.status() == StatusCode::UNAUTHORIZED, "{d:?}");
        }
    }

    #[test]
    fn rejects_stale_and_mismatched() {
        let key = SigningKey::random(&mut rand::rngs::OsRng);
        let addr = address_from_pubkey(key.verifying_key());
        let now = chrono::Utc::now().timestamp() as f64;
        // Stale.
        assert!(verify_token(&make_token(&key, &addr, now - 7200.0, true), 3600).is_err());
        // key ≠ signer.
        let other = "0x000000000000000000000000000000000000dead";
        assert!(verify_token(&make_token(&key, other, now, true), 3600).is_err());
        // Garbage.
        assert!(verify_token("not-a-token", 3600).is_err());
    }
}
