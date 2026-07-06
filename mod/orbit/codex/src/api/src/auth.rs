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
        "Sign this message to authenticate with Codex Jobs.\n\nAddress: {}\nNonce: {}",
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
                return Err((
                    StatusCode::FORBIDDEN,
                    Json(serde_json::json!({
                        "error": "Sign-in not permitted: address is not the owner, not whitelisted, and no gate matched"
                    })),
                ));
            }
            false
        }
        None => {
            let owner_path = dirs::home_dir()
                .unwrap_or_else(|| std::path::PathBuf::from("."))
                .join(".mod")
                .join("codex")
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

    // Generate bearer token: address:timestamp:hmac
    let timestamp = chrono::Utc::now().timestamp();
    let payload = format!("{}:{}", addr, timestamp);
    let mut mac = HmacSha256::new_from_slice(get_secret()).unwrap();
    mac.update(payload.as_bytes());
    let sig = hex::encode(mac.finalize().into_bytes());
    let token = format!("{}:{}", payload, sig);

    if is_new_owner {
        println!("✓ First user authenticated - set as owner: {}", addr);
    }

    Ok(Json(VerifyResponse {
        token,
        address: addr,
    }))
}

// ── Middleware ────────────────────────────────────────────────────────

pub async fn auth_middleware(req: Request, next: Next) -> Response {
    // Try Bearer token first (Codex API native token)
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

    // Check expiry (24 hours)
    let now = chrono::Utc::now().timestamp();
    if now - timestamp > 86400 {
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

/// Read the owner address — config.json "owner" field takes priority, then ~/.mod/codex/owner.json
pub fn get_owner_address() -> Option<String> {
    // Priority 1: config.json "owner" field (live-editable)
    if let Some(owner) = read_config_owner() {
        return Some(owner);
    }

    // Priority 2: owner.json
    let owner_path = dirs::home_dir()?
        .join(".mod")
        .join("codex")
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
            std::path::PathBuf::from(format!("{}/mod/mod/orbit/codex/config.json", home))
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

/// Off-chain config dir (~/.mod/codex/) — holds owner.json, whitelist.json, gate.json.
/// Kept off-repo because the whitelist is private; mounted into the container as a volume.
pub fn private_dir() -> Option<std::path::PathBuf> {
    Some(dirs::home_dir()?.join(".mod").join("codex"))
}

pub fn whitelist_path() -> Option<std::path::PathBuf> {
    Some(private_dir()?.join("whitelist.json"))
}

fn gate_path() -> Option<std::path::PathBuf> {
    Some(private_dir()?.join("gate.json"))
}

/// Read the lowercased whitelist from ~/.mod/codex/whitelist.json (a JSON string array).
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

/// Persist the whitelist back to ~/.mod/codex/whitelist.json (used by the owner-only
/// management endpoints in api.rs). Returns an error string on failure.
pub fn write_whitelist(addresses: &[String]) -> Result<(), String> {
    let path = whitelist_path().ok_or("no home dir")?;
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|e| format!("mkdir: {}", e))?;
    }
    let json = serde_json::to_string_pretty(&addresses).map_err(|e| format!("encode: {}", e))?;
    std::fs::write(&path, json).map_err(|e| format!("write: {}", e))
}

/// Read the optional `gate_command` from ~/.mod/codex/gate.json — a shell command that the
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

fn recover_eth_address(message: &str, signature: &str) -> Result<String, String> {
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
        let message = "Hello Codex Jobs";
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
