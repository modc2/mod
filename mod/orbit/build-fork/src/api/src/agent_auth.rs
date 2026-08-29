//! Per-user agent credentials — "connect your own session".
//!
//! The AGENT selector in the console offers three backends — claude and codex
//! (local CLIs) and orbit/agent (HTTP) — and each needs its own sign-in. By
//! default a job burns whatever credentials the SERVER has (root's
//! subscription file / env key). These endpoints let every signed-in wallet
//! connect its OWN instead, so jobs you submit run on YOUR account:
//!   claude — the sk-ant-oat… token from `claude setup-token` (or sk-ant-api…)
//!   codex  — an OpenAI sk-… key, or a pasted ~/.codex/auth.json (ChatGPT plan)
//!   agent  — a mod protocol-auth token, minted for you by the CONNECT button
//!
//! The ✦ credential is the odd one: it is a SIGNATURE over a timestamp, not a
//! secret, so it ages out (24h) where the other two last until revoked. This
//! module therefore drops a stale one instead of sending it — and offers the
//! server's own freshly-signed identity as the trusted-job fallback, the same
//! role root's claude subscription plays for ⬡ jobs.
//!
//! A claude slot can also be filled WITHOUT a paste, by the "Log in with
//! Claude" button: the app runs the OAuth PKCE flow against claude.ai and
//! hands the result here, so the slot then carries a refresh token and an
//! expiry beside the access token. Those sessions are short-lived by design
//! (~8h), so `resolve_token` renews them from the refresh token on the way
//! into a job — a connected session should not quietly stop working
//! overnight the way a bare pasted access token would.
//!
//! Secrets are per-user state, so they live off-tree (0600), one file per
//! wallet:  ~/.mod/build-fork/agent_auth/<address>.json
//!   { "claude": { "token": "sk-ant-…", "connected_at": 1690000000 }, … }
//!
//! Tracked in the app's Account sidebar → AGENT tab.

use axum::{extract::Path as AxPath, http::HeaderMap, http::StatusCode, response::IntoResponse, Json};
use base64::Engine;
use serde::Deserialize;
use serde_json::json;
use std::path::PathBuf;

/// The agent roster — mirrors AGENT_MODS in the app. `wired` says whether
/// the jobs runner actually uses the credential today; unwired slots still
/// store tokens so connecting is a one-time act, not a wait-for-launch.
const AGENTS: &[(&str, bool)] = &[("claude", true), ("codex", true), ("agent", true)];

fn agent_wired(agent: &str) -> Option<bool> {
    AGENTS.iter().find(|(a, _)| *a == agent).map(|(_, w)| *w)
}

fn auth_dir() -> Option<PathBuf> {
    Some(crate::auth::private_dir()?.join("agent_auth"))
}

/// One file per wallet, keyed by lowercased address so MetaMask's mixed-case
/// checksum spelling and the token's lowercase form hit the same file.
fn user_path(address: &str) -> Option<PathBuf> {
    let addr = address.trim().to_lowercase();
    // Addresses are 0x-hex from recover_eth_address ("local" is the no-auth
    // dev identity), but never trust a path component: reject anything else.
    let hexish = addr.chars().all(|c| c.is_ascii_hexdigit() || c == 'x');
    if addr.is_empty() || (!hexish && addr != "local") {
        return None;
    }
    Some(auth_dir()?.join(format!("{addr}.json")))
}

fn read_user(address: &str) -> serde_json::Map<String, serde_json::Value> {
    user_path(address)
        .and_then(|p| std::fs::read_to_string(p).ok())
        .and_then(|s| serde_json::from_str::<serde_json::Value>(&s).ok())
        .and_then(|v| v.as_object().cloned())
        .unwrap_or_default()
}

fn write_user(address: &str, map: &serde_json::Map<String, serde_json::Value>) -> Result<(), String> {
    let path = user_path(address).ok_or("bad address")?;
    if map.is_empty() {
        match std::fs::remove_file(&path) {
            Ok(()) => return Ok(()),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(()),
            Err(e) => return Err(format!("remove: {e}")),
        }
    }
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|e| format!("mkdir: {e}"))?;
    }
    let json = serde_json::to_string_pretty(&serde_json::Value::Object(map.clone()))
        .map_err(|e| format!("encode: {e}"))?;
    std::fs::write(&path, json).map_err(|e| format!("write: {e}"))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600));
    }
    Ok(())
}

/// The credential a job submitted by `address` should run with, if the user
/// connected one. Consulted by the jobs runner for wired agents.
///
/// An orbit/agent token is time-bounded (see `agent_token_expired`), so a
/// stale one is treated as absent — sending it would fail inside the agent
/// module with a misleading permission error instead of falling back here.
pub fn user_token(address: &str, agent: &str) -> Option<String> {
    if address.is_empty() {
        return None;
    }
    let token = read_user(address)
        .get(agent)?
        .get("token")?
        .as_str()
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(String::from)?;
    if agent == "agent" && agent_token_expired(&token) {
        return None;
    }
    Some(token)
}

/// The credential a job should run with, renewed first if it can be. Same
/// answer as `user_token` for everything except a claude session connected
/// through the OAuth button, which is refreshed here when it is at or near
/// its expiry — the browser that started the login is long gone by the time
/// the job runs, so this is the only place that renewal can happen.
///
/// A failed refresh falls through to the stored (stale) token rather than to
/// the server's credentials: the user asked for their account, and a 401 from
/// the CLI plus a RECONNECT badge in the AGENT tab is a truer story than a
/// job that silently bills someone else.
pub async fn resolve_token(address: &str, agent: &str) -> Option<String> {
    if agent == "claude" {
        if let Some(tok) = refresh_claude(address).await {
            return Some(tok);
        }
    }
    user_token(address, agent)
}

// ── claude: OAuth sessions ──────────────────────────────────────────────
//
// The "Log in with Claude" flow (app route /api/credentials/oauth) mints an
// access token that lasts hours, plus a refresh token that outlives it. Both
// land in the slot; these helpers keep the access token current. Endpoints
// are env-overridable for the same reason the app's copy is — they are
// undocumented and mid-rebrand.

/// Renew this far ahead of expiry rather than let a long run lose its
/// credential halfway through.
const CLAUDE_REFRESH_MARGIN: u64 = 600;

fn oauth_env(key: &str, default: &str) -> String {
    std::env::var(key)
        .ok()
        .map(|v| v.trim().to_string())
        .filter(|v| !v.is_empty())
        .unwrap_or_else(|| default.to_string())
}

/// Epoch seconds from a JSON number that may be seconds or JS milliseconds.
fn norm_epoch(v: f64) -> u64 {
    if v > 1e12 {
        (v / 1000.0) as u64
    } else {
        v as u64
    }
}

fn slot_expires_at(slot: &serde_json::Value) -> Option<u64> {
    slot.get("expires_at")
        .and_then(|v| v.as_f64())
        .map(norm_epoch)
        .filter(|t| *t > 0)
}

/// Renew the caller's claude session if it carries a refresh token and is at
/// (or within the margin of) its expiry. Returns the token to use when this
/// slot is an OAuth session, None when it isn't one (leave it to `user_token`).
async fn refresh_claude(address: &str) -> Option<String> {
    if address.is_empty() {
        return None;
    }
    let stored = read_user(address);
    let slot = stored.get("claude")?;
    let token = slot.get("token")?.as_str()?.trim().to_string();
    let refresh = slot
        .get("refresh_token")
        .and_then(|t| t.as_str())
        .map(str::trim)
        .filter(|s| !s.is_empty())?
        .to_string();
    let expires_at = slot_expires_at(slot);
    // Still comfortably valid — nothing to do.
    if expires_at.map(|e| e > now_secs() + CLAUDE_REFRESH_MARGIN).unwrap_or(false) {
        return Some(token);
    }
    let body = json!({
        "grant_type": "refresh_token",
        "refresh_token": refresh,
        "client_id": oauth_env("CLAUDE_OAUTH_CLIENT_ID", "9d1c250a-e61b-44d9-88ed-5944d1962f5e"),
    });
    let url = oauth_env(
        "CLAUDE_OAUTH_TOKEN_URL",
        "https://console.anthropic.com/v1/oauth/token",
    );
    let resp = reqwest::Client::new()
        .post(&url)
        .header("Content-Type", "application/json")
        .header("Accept", "application/json")
        .json(&body)
        .timeout(std::time::Duration::from_secs(20))
        .send()
        .await
        .ok()?;
    if !resp.status().is_success() {
        // Expired/revoked refresh token: keep the stale access token in place
        // so the AGENT tab can still say "reconnect" instead of "never
        // connected", and let the job fail on its own 401.
        return Some(token);
    }
    let v: serde_json::Value = resp.json().await.ok()?;
    let access = v.get("access_token").and_then(|t| t.as_str())?.trim().to_string();
    if access.is_empty() {
        return Some(token);
    }
    let new_refresh = v
        .get("refresh_token")
        .and_then(|t| t.as_str())
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(String::from)
        .unwrap_or(refresh);
    let expires_in = v.get("expires_in").and_then(|t| t.as_f64()).unwrap_or(8.0 * 3600.0);
    // Re-read: a concurrent connect/disconnect may have landed since.
    let mut latest = read_user(address);
    let connected_at = latest
        .get("claude")
        .and_then(|s| s.get("connected_at"))
        .cloned()
        .unwrap_or(json!(now_secs()));
    latest.insert(
        "claude".into(),
        json!({
            "token": access,
            "refresh_token": new_refresh,
            "expires_at": now_secs() + expires_in as u64,
            "connected_at": connected_at,
            "source": "oauth",
        }),
    );
    let _ = write_user(address, &latest);
    latest
        .get("claude")
        .and_then(|s| s.get("token"))
        .and_then(|t| t.as_str())
        .map(String::from)
}

// ── orbit/agent: signed, expiring tokens ────────────────────────────────
//
// The ✦ backend does not take a bearer string. It takes a mod protocol-auth
// envelope — base64url of {data, time, key, signature} — and the core auth
// mod refuses one older than its max_age (86400s). A token connected once in
// the AGENT tab therefore dies a day later, and the run fails inside the
// agent module with "Permission denied: 'run' requires admin access", which
// reads like a missing grant and is really an expired signature. So we treat
// age as first-class on both sides: a stale user token is dropped here, and
// the server mints its own fresh (see `server_agent_token`).

/// The verifier's window. Kept in sync with core/server/auth `max_age`.
pub const AGENT_TOKEN_MAX_AGE: u64 = 86_400;

/// Refuse a token this close to the edge rather than watch a long run lose
/// its credential halfway through.
const AGENT_TOKEN_MARGIN: u64 = 3_600;

fn now_secs() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

/// When an orbit/agent token was signed, read out of its own envelope — the
/// only honest source, since the file's `connected_at` is when it was pasted.
/// `time` is stringified seconds ("1785697349.377556").
pub fn agent_token_issued_at(token: &str) -> Option<u64> {
    let bytes = base64::engine::general_purpose::URL_SAFE_NO_PAD
        .decode(token.trim())
        .ok()?;
    let v: serde_json::Value = serde_json::from_slice(&bytes).ok()?;
    let t = v.get("time")?;
    let secs = t
        .as_str()
        .and_then(|s| s.parse::<f64>().ok())
        .or_else(|| t.as_f64())?;
    Some(secs as u64)
}

/// True when the agent module would reject this token as stale. An envelope
/// we can't read at all counts as expired: it was never going to verify.
pub fn agent_token_expired(token: &str) -> bool {
    match agent_token_issued_at(token) {
        Some(issued) => now_secs().saturating_sub(issued) >= AGENT_TOKEN_MAX_AGE - AGENT_TOKEN_MARGIN,
        None => true,
    }
}

/// Path to the python auth bridge, or None when it isn't installed.
fn agent_bridge_path() -> Option<String> {
    let bridge = std::env::var("BUILD_FORK_AGENT_BRIDGE").unwrap_or_else(|_| {
        let home = std::env::var("HOME").unwrap_or_else(|_| "/root".to_string());
        format!("{home}/mod/mod/orbit/build-fork/src/api/agent_bridge.py")
    });
    std::path::Path::new(&bridge).exists().then_some(bridge)
}

/// The server's own agent credential, minted fresh and cached for well under
/// the verifier's window. This is the ✦ equivalent of root's claude
/// subscription: what a TRUSTED job falls back to when its submitter hasn't
/// connected a session. Peer jobs never receive it — see the jobs runner.
///
/// Blocking (spawns python + imports the mod framework, ~1s cold), so call it
/// from a blocking context; the cache makes that once per half-day, not once
/// per job.
pub fn server_agent_token() -> Option<String> {
    use std::sync::Mutex;
    use std::sync::OnceLock;
    static CACHE: OnceLock<Mutex<Option<(String, u64)>>> = OnceLock::new();
    let cache = CACHE.get_or_init(|| Mutex::new(None));
    if let Ok(guard) = cache.lock() {
        if let Some((tok, issued)) = guard.as_ref() {
            if now_secs().saturating_sub(*issued) < AGENT_TOKEN_MAX_AGE / 2 {
                return Some(tok.clone());
            }
        }
    }
    let bridge = agent_bridge_path()?;
    let out = std::process::Command::new("python3")
        .arg(&bridge)
        .arg("mint")
        .arg("agent")
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::null())
        .output()
        .ok()?;
    let stdout = String::from_utf8_lossy(&out.stdout);
    let v = stdout
        .lines()
        .rev()
        .find_map(|l| serde_json::from_str::<serde_json::Value>(l.trim()).ok())?;
    let token = v.get("token")?.as_str()?.trim().to_string();
    if token.is_empty() {
        return None;
    }
    if let Ok(mut guard) = cache.lock() {
        *guard = Some((token.clone(), now_secs()));
    }
    Some(token)
}

/// Whether the server can sign for itself at all — what the AGENT tab shows
/// as the ✦ slot's fallback, without paying for a signature to find out.
fn server_agent_identity() -> bool {
    agent_bridge_path().is_some()
}

/// A codex credential is either an API key (rides CODEX_API_KEY) or a pasted
/// ~/.codex/auth.json from a ChatGPT-plan `codex login`. Returns the auth.json
/// text to stage in a job-private CODEX_HOME for the second kind, None for the
/// first — the jobs runner picks its path from that.
pub fn codex_auth_file(token: &str) -> Option<String> {
    let t = token.trim();
    if !t.starts_with('{') {
        return None;
    }
    let v: serde_json::Value = serde_json::from_str(t).ok()?;
    // Both shapes codex writes: OAuth "tokens" (ChatGPT plan) and the
    // API-key file `codex login --api-key` leaves behind.
    if v.get("tokens").is_none() && v.get("OPENAI_API_KEY").is_none() {
        return None;
    }
    Some(t.to_string())
}

/// "sk-ant-oat01-abcd…wxyz" → "sk-ant-oat…wxyz" — enough to recognize which
/// token is connected without ever echoing the secret back whole.
fn mask(token: &str) -> String {
    if token.len() <= 14 {
        return "····".to_string();
    }
    format!("{}…{}", &token[..10], &token[token.len() - 4..])
}

/// Does the SERVER have credentials of its own for this agent? That's the
/// fallback a TRUSTED job uses when the submitting user hasn't connected a
/// session — shown in the AGENT tab so "it works, but on whose account?" has
/// an answer. Sandboxed peer jobs never inherit it, on any agent.
fn server_default(agent: &str) -> &'static str {
    let home = std::env::var("HOME").unwrap_or_default();
    let file_exists = |rel: &str| !home.is_empty() && std::path::Path::new(&format!("{home}/{rel}")).exists();
    let env_set = |k: &str| std::env::var(k).map(|v| !v.is_empty()).unwrap_or(false);
    match agent {
        "claude" => {
            if file_exists(".claude/.credentials.json") {
                "subscription"
            } else if env_set("ANTHROPIC_API_KEY") {
                "env-api-key"
            } else {
                "none"
            }
        }
        "codex" => {
            if file_exists(".codex/auth.json") {
                "subscription"
            } else if env_set("CODEX_API_KEY") || env_set("OPENAI_API_KEY") {
                "env-api-key"
            } else {
                "none"
            }
        }
        // The ✦ backend authenticates with a signature, not a stored secret:
        // the host key signs a fresh envelope per run. Whether that identity
        // is granted `run` on the agent module is that module's ACL to say.
        "agent" => {
            if server_agent_identity() {
                "module-key"
            } else {
                "none"
            }
        }
        _ => "none",
    }
}

/// True when the host itself can run codex — `codex login`'s auth.json or an
/// API key in the environment. The jobs runner preflights on this so a job
/// with no credential anywhere fails with an instruction instead of codex's
/// own seven-retry 401 transcript.
pub fn server_codex_default() -> bool {
    server_default("codex") != "none"
}

fn caller(headers: &HeaderMap) -> Result<String, (StatusCode, Json<serde_json::Value>)> {
    match crate::auth::extract_address_from_headers(headers) {
        Ok(a) => Ok(a),
        // BUILD_FORK_JOBS_LOCAL=1 runs with auth disabled — everything is one
        // "local" identity, same as the jobs runner sees.
        Err(_) if std::env::var("BUILD_FORK_JOBS_LOCAL").unwrap_or_default() == "1" => {
            Ok("local".to_string())
        }
        Err(e) => Err((StatusCode::UNAUTHORIZED, Json(json!({ "error": e })))),
    }
}

/// GET /agent/auth — the caller's connection state for every agent slot.
pub async fn status(headers: HeaderMap) -> impl IntoResponse {
    let address = match caller(&headers) {
        Ok(a) => a,
        Err(e) => return e.into_response(),
    };
    Json(snapshot(&address)).into_response()
}

fn snapshot(address: &str) -> serde_json::Value {
    let stored = read_user(address);
    let agents: serde_json::Map<String, serde_json::Value> = AGENTS
        .iter()
        .map(|(agent, wired)| {
            let slot = stored.get(*agent);
            let token = slot
                .and_then(|s| s.get("token"))
                .and_then(|t| t.as_str())
                .unwrap_or("");
            let session_kind = token.starts_with("sk-ant-oat")
                || codex_auth_file(token).is_some()
                || *agent == "agent";
            // Two kinds of credential carry an expiry: ✦ envelopes (signed,
            // age out) and claude OAuth sessions (short-lived access token +
            // refresh token). A pasted secret is valid until revoked.
            let claude_expires = (*agent == "claude")
                .then(|| slot.and_then(slot_expires_at))
                .flatten();
            // A session we can renew is not "expired" to the reader — the job
            // runner refreshes it on the way in (see `resolve_token`).
            let refreshable = *agent == "claude"
                && slot
                    .and_then(|s| s.get("refresh_token"))
                    .and_then(|t| t.as_str())
                    .map(|s| !s.trim().is_empty())
                    .unwrap_or(false);
            let expired = match *agent {
                "agent" => !token.is_empty() && agent_token_expired(token),
                "claude" => {
                    !token.is_empty()
                        && !refreshable
                        && claude_expires.map(|e| e <= now_secs()).unwrap_or(false)
                }
                _ => false,
            };
            let expires_at = if *agent == "agent" && !token.is_empty() {
                agent_token_issued_at(token).map(|t| t + AGENT_TOKEN_MAX_AGE)
            } else {
                claude_expires
            };
            // How the credential got here — "oauth" (Log in with Claude) or a
            // pasted secret. The console words the card from this.
            let source = slot
                .and_then(|s| s.get("source"))
                .and_then(|s| s.as_str())
                .map(String::from)
                .unwrap_or_else(|| if refreshable { "oauth".into() } else { "paste".into() });
            let entry = json!({
                "connected": !token.is_empty(),
                "hint": if token.is_empty() { serde_json::Value::Null } else { json!(mask(token)) },
                "kind": if token.is_empty() { serde_json::Value::Null } else if session_kind { json!("session") } else { json!("api-key") },
                "connected_at": slot.and_then(|s| s.get("connected_at")).cloned().unwrap_or(serde_json::Value::Null),
                "wired": wired,
                // A signed ✦ token the verifier would now refuse as stale.
                // Still "connected" — the console needs to tell "never
                // connected" (CONNECT) from "connected, aged out" (RECONNECT).
                "expired": expired,
                "expires_at": expires_at,
                // Only meaningful when connected: where the credential came
                // from, and whether it renews itself.
                "source": if token.is_empty() { serde_json::Value::Null } else { json!(source) },
                "refreshable": refreshable,
                // What a TRUSTED job on this agent falls back to when you
                // haven't connected: "subscription", "env-api-key",
                // "module-key" (✦ signs as the host) or "none".
                "server_default": server_default(agent),
            });
            (agent.to_string(), entry)
        })
        .collect();
    json!({
        "address": address,
        "agents": agents,
        // Kept for older clients — the claude slot's fallback.
        "server_default": server_default("claude"),
    })
}

#[derive(Deserialize)]
pub struct ConnectRequest {
    token: String,
    /// OAuth sessions ("Log in with Claude") come with the two extra fields
    /// that let the runner renew them; a pasted secret has neither.
    #[serde(default)]
    refresh_token: Option<String>,
    #[serde(default)]
    expires_at: Option<f64>,
    #[serde(default)]
    source: Option<String>,
}

/// POST /agent/auth/:agent {token} — connect the caller's own session.
pub async fn connect(
    headers: HeaderMap,
    AxPath(agent): AxPath<String>,
    Json(req): Json<ConnectRequest>,
) -> impl IntoResponse {
    let address = match caller(&headers) {
        Ok(a) => a,
        Err(e) => return e.into_response(),
    };
    if agent_wired(&agent).is_none() {
        return (StatusCode::NOT_FOUND, Json(json!({ "error": format!("unknown agent '{agent}'") }))).into_response();
    }
    let token = req.token.trim();
    if token.is_empty() {
        return (StatusCode::BAD_REQUEST, Json(json!({ "error": "empty token" }))).into_response();
    }
    // Each agent's credential has a recognizable shape — catch pastes of the
    // wrong thing early rather than at the first failed job.
    if agent == "codex" && !token.starts_with("sk-") && codex_auth_file(token).is_none() {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({ "error": "Unrecognized token — paste an OpenAI sk-… API key, or the contents of ~/.codex/auth.json from `codex login`" })),
        ).into_response();
    }
    if agent == "claude" && !token.starts_with("sk-ant-") {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({ "error": "Unrecognized token — paste the sk-ant-oat… output of `claude setup-token` or an sk-ant-api… API key" })),
        ).into_response();
    }
    // orbit/agent uses fleet protocol-auth tokens: base64url of a signed
    // {data, time, key, signature} envelope. Reject pastes that can't be one.
    if agent == "agent" {
        let ok = base64::engine::general_purpose::URL_SAFE_NO_PAD
            .decode(token)
            .ok()
            .and_then(|b| serde_json::from_slice::<serde_json::Value>(&b).ok())
            .map(|v| v.get("signature").is_some() && v.get("key").is_some())
            .unwrap_or(false);
        if !ok {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({ "error": "Unrecognized token — expected a mod protocol-auth token (signed {data,time,key,signature} envelope); the console mints one when you press CONNECT" })),
            ).into_response();
        }
    }
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    let mut stored = read_user(&address);
    let mut entry = serde_json::Map::new();
    entry.insert("token".into(), json!(token));
    entry.insert("connected_at".into(), json!(now));
    if let Some(r) = req.refresh_token.as_ref().map(|s| s.trim()).filter(|s| !s.is_empty()) {
        entry.insert("refresh_token".into(), json!(r));
    }
    if let Some(e) = req.expires_at.filter(|v| *v > 0.0) {
        entry.insert("expires_at".into(), json!(norm_epoch(e)));
    }
    entry.insert(
        "source".into(),
        json!(match req.source.as_deref().map(str::trim) {
            Some(s) if !s.is_empty() => s,
            _ if req.refresh_token.is_some() => "oauth",
            _ => "paste",
        }),
    );
    stored.insert(agent, serde_json::Value::Object(entry));
    if let Err(e) = write_user(&address, &stored) {
        return (StatusCode::INTERNAL_SERVER_ERROR, Json(json!({ "error": e }))).into_response();
    }
    Json(snapshot(&address)).into_response()
}

/// DELETE /agent/auth/:agent — forget the caller's credential for one agent.
pub async fn disconnect(headers: HeaderMap, AxPath(agent): AxPath<String>) -> impl IntoResponse {
    let address = match caller(&headers) {
        Ok(a) => a,
        Err(e) => return e.into_response(),
    };
    if agent_wired(&agent).is_none() {
        return (StatusCode::NOT_FOUND, Json(json!({ "error": format!("unknown agent '{agent}'") }))).into_response();
    }
    let mut stored = read_user(&address);
    stored.remove(&agent);
    if let Err(e) = write_user(&address, &stored) {
        return (StatusCode::INTERNAL_SERVER_ERROR, Json(json!({ "error": e }))).into_response();
    }
    Json(snapshot(&address)).into_response()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_mask_never_echoes_whole_token() {
        let t = "sk-ant-oat01-abcdefghijklmnop";
        let m = mask(t);
        assert!(!m.contains("abcdefghijklm"));
        assert!(m.starts_with("sk-ant-oat"));
        assert!(m.ends_with("mnop"));
        assert_eq!(mask("short"), "····");
    }

    #[test]
    fn test_codex_credential_shapes() {
        // API keys ride the env var, not a staged file.
        assert!(codex_auth_file("sk-proj-abc123").is_none());
        assert!(codex_auth_file("{\"nope\": 1}").is_none());
        assert!(codex_auth_file("not json at all").is_none());
        // Both shapes `codex login` writes get staged as auth.json.
        assert!(codex_auth_file("{\"tokens\":{\"access_token\":\"x\"}}").is_some());
        assert!(codex_auth_file(" {\"OPENAI_API_KEY\":\"sk-x\"} ").is_some());
    }

    /// Build a ✦ envelope signed `age` seconds ago.
    fn agent_envelope(age: u64) -> String {
        let t = now_secs().saturating_sub(age);
        let body = json!({
            "data": {"scope": "agent"},
            "time": format!("{t}.0"),
            "key": "0xd779eb61ced815570f74ab15a52ee8378a66996f",
            "signature": "0xdeadbeef",
        });
        base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(body.to_string())
    }

    #[test]
    fn test_agent_token_expiry_reads_the_envelope() {
        // Fresh: usable. Aged past the margin: refused before it ever reaches
        // the agent module (which would answer with a misleading 403).
        assert!(!agent_token_expired(&agent_envelope(60)));
        assert!(!agent_token_expired(&agent_envelope(AGENT_TOKEN_MAX_AGE - AGENT_TOKEN_MARGIN - 60)));
        assert!(agent_token_expired(&agent_envelope(AGENT_TOKEN_MAX_AGE - AGENT_TOKEN_MARGIN + 60)));
        assert!(agent_token_expired(&agent_envelope(8 * 86_400)));
        // Unreadable envelopes were never going to verify either.
        assert!(agent_token_expired("not-base64-at-all!!"));
        assert!(agent_token_expired(""));
    }

    #[test]
    fn test_stale_agent_token_reads_as_disconnected() {
        let addr = "0xabc123abc123abc123abc123abc123abc123abc2";
        let mut map = serde_json::Map::new();
        map.insert("agent".into(), json!({ "token": agent_envelope(8 * 86_400), "connected_at": 1 }));
        // Expired ⇒ the runner sees nothing and falls back to the server.
        write_user(addr, &map).unwrap();
        assert_eq!(user_token(addr, "agent"), None);
        // …but the console still sees it, flagged, so it can say RECONNECT
        // rather than pretending it was never connected.
        let snap = snapshot(addr);
        assert_eq!(snap["agents"]["agent"]["connected"], json!(true));
        assert_eq!(snap["agents"]["agent"]["expired"], json!(true));

        map.insert("agent".into(), json!({ "token": agent_envelope(120), "connected_at": 1 }));
        write_user(addr, &map).unwrap();
        assert!(user_token(addr, "agent").is_some());
        assert_eq!(snapshot(addr)["agents"]["agent"]["expired"], json!(false));

        // An sk-… secret never expires on age — only ✦ tokens do.
        let mut m2 = serde_json::Map::new();
        m2.insert("claude".into(), json!({ "token": "sk-ant-oat01-test", "connected_at": 1 }));
        write_user(addr, &m2).unwrap();
        assert!(user_token(addr, "claude").is_some());
        assert_eq!(snapshot(addr)["agents"]["claude"]["expired"], json!(false));

        write_user(addr, &serde_json::Map::new()).unwrap();
    }

    #[test]
    fn test_claude_oauth_session_reads_as_renewable() {
        let addr = "0xabc123abc123abc123abc123abc123abc123abc3";
        let mut map = serde_json::Map::new();
        // An OAuth session past its expiry is still "connected" and NOT
        // expired — the runner refreshes it on the way into the job.
        map.insert(
            "claude".into(),
            json!({
                "token": "sk-ant-oat01-test",
                "refresh_token": "rt-test",
                "expires_at": now_secs() - 60,
                "connected_at": 1,
                "source": "oauth",
            }),
        );
        write_user(addr, &map).unwrap();
        let snap = snapshot(addr);
        assert_eq!(snap["agents"]["claude"]["connected"], json!(true));
        assert_eq!(snap["agents"]["claude"]["expired"], json!(false));
        assert_eq!(snap["agents"]["claude"]["refreshable"], json!(true));
        assert_eq!(snap["agents"]["claude"]["source"], json!("oauth"));

        // …but one with no refresh token can only be reconnected by hand.
        map.insert(
            "claude".into(),
            json!({ "token": "sk-ant-oat01-test", "expires_at": now_secs() - 60, "connected_at": 1 }),
        );
        write_user(addr, &map).unwrap();
        let snap = snapshot(addr);
        assert_eq!(snap["agents"]["claude"]["expired"], json!(true));
        assert_eq!(snap["agents"]["claude"]["source"], json!("paste"));

        // JS millisecond expiries normalize to seconds.
        assert_eq!(norm_epoch(1_785_697_349_000.0), 1_785_697_349);
        assert_eq!(norm_epoch(1_785_697_349.0), 1_785_697_349);

        write_user(addr, &serde_json::Map::new()).unwrap();
    }

    #[test]
    fn test_user_path_rejects_traversal() {
        assert!(user_path("../../etc/passwd").is_none());
        assert!(user_path("").is_none());
        assert!(user_path("0xD779eB61CEd815570F74AB15a52eE8378a66996f").is_some());
    }

    #[test]
    fn test_roundtrip_and_token_lookup() {
        // HOME is the test runner's — write under a throwaway address and
        // clean up after.
        let addr = "0xabc123abc123abc123abc123abc123abc123abc1";
        let mut map = serde_json::Map::new();
        map.insert("claude".into(), json!({ "token": "sk-ant-oat01-test", "connected_at": 1 }));
        write_user(addr, &map).unwrap();
        assert_eq!(user_token(addr, "claude").as_deref(), Some("sk-ant-oat01-test"));
        // Checksummed spelling hits the same file.
        assert_eq!(user_token(&addr.to_uppercase().replace("0X", "0x"), "claude").as_deref(), Some("sk-ant-oat01-test"));
        assert_eq!(user_token(addr, "codex"), None);
        write_user(addr, &serde_json::Map::new()).unwrap();
        assert_eq!(user_token(addr, "claude"), None);
    }
}
