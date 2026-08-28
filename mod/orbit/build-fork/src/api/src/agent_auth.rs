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
pub fn user_token(address: &str, agent: &str) -> Option<String> {
    if address.is_empty() {
        return None;
    }
    read_user(address)
        .get(agent)?
        .get("token")?
        .as_str()
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(String::from)
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
/// fallback a job uses when the submitting user hasn't connected a session —
/// shown in the AGENT tab so "it works, but on whose account?" has an answer.
/// orbit/agent has no server-side identity at all: every ✦ job runs on the
/// submitter's own protocol-auth token.
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
        _ => "none",
    }
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
            let entry = json!({
                "connected": !token.is_empty(),
                "hint": if token.is_empty() { serde_json::Value::Null } else { json!(mask(token)) },
                "kind": if token.is_empty() { serde_json::Value::Null } else if session_kind { json!("session") } else { json!("api-key") },
                "connected_at": slot.and_then(|s| s.get("connected_at")).cloned().unwrap_or(serde_json::Value::Null),
                "wired": wired,
                // What a job on THIS agent falls back to when you haven't
                // connected: "subscription", "env-api-key" or "none".
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
    stored.insert(agent, json!({ "token": token, "connected_at": now }));
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
