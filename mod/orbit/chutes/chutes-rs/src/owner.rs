//! Setting the box's chutes key from the console — and who may.
//!
//! The server-side key (`~/.mod/chutes/api_key`) is what the MCP server, the
//! stdio transport, the `m` CLI and every browser without its own key share,
//! so writing it is the deployment owner's call and nobody else's. The door
//! is the fleet's mod-protocol token: `base64url({data, time, key, signature})`
//! where `signature` is an EIP-191 `personal_sign` over exactly
//! `JSON.stringify({data, time})` — the same envelope `m.mod('auth')().token()`
//! mints on the box and a MetaMask wallet mints in the browser. It is
//! verified here natively (k256 + keccak) so a request never waits on Python.
//!
//! The owner is, in order: `CHUTES_OWNER`, `~/.mod/chutes/owner.json`
//! (`{"owner": "0x…"}` — private, off-tree), config.json `owner` (the public
//! declaration), else the box's own key (`m.key().address`, asked once and
//! cached) — which is what the `m` CLI signs with, so on a fresh box
//! `m chutes/token` already opens the door.

use k256::ecdsa::{RecoveryId, Signature, VerifyingKey};
use serde_json::{json, Value};
use sha3::{Digest, Keccak256};
use std::path::PathBuf;
use std::sync::OnceLock;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

pub const ENV_OWNER: &str = "CHUTES_OWNER";
pub const ENV_TOKEN_MAX_AGE: &str = "CHUTES_TOKEN_MAX_AGE";
const DEFAULT_TOKEN_MAX_AGE: f64 = 7.0 * 86_400.0;
/// Clock skew we forgive on a token stamped in the future.
const FUTURE_SLACK: f64 = 300.0;

fn home() -> Option<PathBuf> {
    std::env::var("HOME").ok().filter(|h| !h.is_empty()).map(PathBuf::from)
}

fn state_dir() -> Option<PathBuf> {
    home().map(|h| h.join(".mod").join(crate::chutes::KEY_DIR))
}

/// `~/.mod/chutes/api_key` — the file `POST /key` writes and `lookup_key` reads.
pub fn key_path() -> Option<PathBuf> {
    state_dir().map(|d| d.join("api_key"))
}

pub fn token_max_age() -> f64 {
    std::env::var(ENV_TOKEN_MAX_AGE).ok().and_then(|v| v.parse().ok()).unwrap_or(DEFAULT_TOKEN_MAX_AGE)
}

// ── the token ────────────────────────────────────────────────────────────────

fn b64url_decode(s: &str) -> Result<Vec<u8>, String> {
    use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
    URL_SAFE_NO_PAD.decode(s.trim().trim_end_matches('=')).map_err(|e| format!("token is not base64url: {e}"))
}

/// Public key → lowercase 0x address.
fn address_of(key: &VerifyingKey) -> String {
    let point = key.to_encoded_point(false);
    let mut h = Keccak256::new();
    h.update(&point.as_bytes()[1..]);
    let addr = h.finalize();
    format!("0x{}", hex::encode(&addr[12..]))
}

/// Every address a 65-byte signature over `message` can recover to, under the
/// two hashes the fleet signs with:
///
/// * EIP-191 `personal_sign` — `keccak("\x19Ethereum Signed Message:\n" + len + msg)`,
///   what a browser wallet produces (v = 27/28);
/// * bare `keccak(msg)` — what the core Key's `eth_keys.sign_msg` produces for
///   a token minted on the box by `m.mod('auth')().token()` (v = 0/1).
///
/// The auth mod's own `ecdsa_verify` accepts either, so this does too.
fn recover_eth_addresses(message: &str, signature: &str) -> Result<Vec<String>, String> {
    let sig = hex::decode(signature.trim().strip_prefix("0x").unwrap_or(signature)).map_err(|e| format!("signature is not hex: {e}"))?;
    if sig.len() != 65 {
        return Err(format!("signature must be 65 bytes, got {}", sig.len()));
    }
    let mut parity = match sig[64] {
        27 | 0 => false,
        28 | 1 => true,
        v => return Err(format!("bad recovery id {v}")),
    };
    let mut signature = Signature::from_slice(&sig[..64]).map_err(|e| format!("bad signature: {e}"))?;
    // A high-s signature normalizes to its mirror, which flips the y parity.
    if let Some(low) = signature.normalize_s() {
        signature = low;
        parity = !parity;
    }
    let rid = RecoveryId::new(parity, false);

    let prefixed = {
        let mut h = Keccak256::new();
        h.update(format!("\x19Ethereum Signed Message:\n{}", message.len()).as_bytes());
        h.update(message.as_bytes());
        h.finalize()
    };
    let bare = Keccak256::digest(message.as_bytes());

    let mut out = Vec::with_capacity(2);
    for hash in [prefixed, bare] {
        if let Ok(key) = VerifyingKey::recover_from_prehash(&hash, &signature, rid) {
            out.push(address_of(&key));
        }
    }
    if out.is_empty() {
        return Err("signature does not recover to any key".into());
    }
    Ok(out)
}

/// Verify a mod-protocol token; returns the signer's lowercase address.
pub fn verify_token(token: &str) -> Result<String, String> {
    let raw = b64url_decode(token)?;
    let v: Value = serde_json::from_slice(&raw).map_err(|e| format!("token is not JSON: {e}"))?;
    let data = v.get("data").ok_or("token has no `data`")?;
    let time = v.get("time").ok_or("token has no `time`")?;
    let key = v.get("key").and_then(|k| k.as_str()).ok_or("token has no `key`")?;
    let signature = v.get("signature").and_then(|k| k.as_str()).ok_or("token has no `signature`")?;

    let stamped: f64 = match time {
        Value::String(s) => s.trim().parse().map_err(|_| "token `time` is not a number")?,
        Value::Number(n) => n.as_f64().ok_or("token `time` is not a number")?,
        _ => return Err("token `time` is not a number".into()),
    };
    let now = SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or(Duration::ZERO).as_secs_f64();
    let age = now - stamped;
    if age > token_max_age() {
        return Err(format!("token is stale ({:.0}s old, max {:.0}s) — sign in again", age, token_max_age()));
    }
    if age < -FUTURE_SLACK {
        return Err("token is stamped in the future".into());
    }

    // Byte-for-byte what the signer signed: compact JSON, `data` then `time`,
    // with `data` re-serialized in its own key order (serde_json keeps it).
    let message = format!("{{\"data\":{},\"time\":{}}}", data, time);
    let claimed = key.trim().to_lowercase();
    if !recover_eth_addresses(&message, signature)?.iter().any(|a| *a == claimed) {
        return Err("token signer does not match its `key`".into());
    }
    Ok(claimed)
}

// ── the owner ────────────────────────────────────────────────────────────────

/// The chutes module dir — the first ancestor of the binary whose
/// config.json says `"name": "chutes"`, else the conventional checkout path.
fn module_dir() -> Option<PathBuf> {
    let mut candidates: Vec<PathBuf> = Vec::new();
    if let Ok(exe) = std::env::current_exe() {
        let mut dir = exe.parent().map(|p| p.to_path_buf());
        for _ in 0..5 {
            let Some(d) = dir else { break };
            candidates.push(d.clone());
            dir = d.parent().map(|p| p.to_path_buf());
        }
    }
    if let Some(h) = home() {
        candidates.push(h.join("mod/mod/orbit/chutes"));
    }
    candidates.into_iter().find(|d| {
        std::fs::read_to_string(d.join("config.json"))
            .ok()
            .and_then(|r| serde_json::from_str::<Value>(&r).ok())
            .and_then(|v| v.get("name").and_then(|n| n.as_str()).map(|n| n == crate::chutes::ID))
            .unwrap_or(false)
    })
}

fn config_owner() -> Option<String> {
    let raw = std::fs::read_to_string(module_dir()?.join("config.json")).ok()?;
    let v: Value = serde_json::from_str(&raw).ok()?;
    v.get("owner").and_then(|o| o.as_str()).map(|s| s.trim().to_lowercase()).filter(|s| !s.is_empty())
}

/// The box's own key — what the `m` CLI signs with. One Python call, cached.
fn box_key_address() -> Option<String> {
    static ADDR: OnceLock<Option<String>> = OnceLock::new();
    ADDR.get_or_init(|| {
        let out = std::process::Command::new("python3")
            .args(["-I", "-c", "import mod as m; print(m.key().address)"])
            // `import mod` refuses to run with cwd=$HOME ("cannot sync in home
            // directory"); the module dir is fine (-I keeps cwd off sys.path,
            // so our own mod.py can't shadow the package), else /tmp.
            .current_dir(module_dir().unwrap_or_else(|| PathBuf::from("/tmp")))
            .stderr(std::process::Stdio::null())
            .output()
            .ok()?;
        String::from_utf8_lossy(&out.stdout)
            .lines()
            .map(str::trim)
            .filter(|l| l.len() == 42 && l.starts_with("0x") && l[2..].chars().all(|c| c.is_ascii_hexdigit()))
            .last()
            .map(|l| l.to_lowercase())
    })
    .clone()
}

/// (owner address, where it was declared). Blocking on first call if it has
/// to ask Python for the box key — call from `spawn_blocking`.
pub fn owner() -> (Option<String>, &'static str) {
    if let Some(o) = std::env::var(ENV_OWNER).ok().map(|s| s.trim().to_lowercase()).filter(|s| !s.is_empty()) {
        return (Some(o), "env");
    }
    if let Some(v) = state_dir()
        .and_then(|d| std::fs::read_to_string(d.join("owner.json")).ok())
        .and_then(|r| serde_json::from_str::<Value>(&r).ok())
    {
        for field in ["owner", "address"] {
            if let Some(o) = v.get(field).and_then(|o| o.as_str()).map(|s| s.trim().to_lowercase()).filter(|s| !s.is_empty()) {
                return (Some(o), "owner.json");
            }
        }
    }
    if let Some(o) = config_owner() {
        return (Some(o), "config.json");
    }
    match box_key_address() {
        Some(o) => (Some(o), "box-key"),
        None => (None, "none"),
    }
}

// ── the key file ─────────────────────────────────────────────────────────────

fn sane_key(key: &str) -> Result<&str, String> {
    let key = key.trim();
    if key.is_empty() {
        return Err("key is empty".into());
    }
    if key.len() > 512 {
        return Err("key is too long to be a chutes key".into());
    }
    if !key.chars().all(|c| c.is_ascii_graphic()) {
        return Err("key has whitespace or non-ASCII in it — paste it again".into());
    }
    Ok(key)
}

/// Write `~/.mod/chutes/api_key` (dir 0700, file 0600).
pub fn write_key(key: &str) -> Result<PathBuf, String> {
    let key = sane_key(key)?;
    let path = key_path().ok_or("no $HOME — nowhere to keep a key")?;
    let dir = path.parent().ok_or("bad key path")?;
    std::fs::create_dir_all(dir).map_err(|e| format!("mkdir {}: {e}", dir.display()))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(dir, std::fs::Permissions::from_mode(0o700));
    }
    let tmp = dir.join(".api_key.tmp");
    {
        #[cfg(unix)]
        use std::os::unix::fs::OpenOptionsExt;
        let mut opts = std::fs::OpenOptions::new();
        opts.write(true).create(true).truncate(true);
        #[cfg(unix)]
        opts.mode(0o600);
        use std::io::Write;
        let mut f = opts.open(&tmp).map_err(|e| format!("open {}: {e}", tmp.display()))?;
        f.write_all(key.as_bytes()).and_then(|_| f.write_all(b"\n")).map_err(|e| format!("write: {e}"))?;
    }
    std::fs::rename(&tmp, &path).map_err(|e| format!("rename into place: {e}"))?;
    Ok(path)
}

/// Remove `~/.mod/chutes/api_key`. Ok(false) when there was none.
pub fn clear_key() -> Result<bool, String> {
    let path = key_path().ok_or("no $HOME")?;
    match std::fs::remove_file(&path) {
        Ok(()) => Ok(true),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(false),
        Err(e) => Err(format!("remove {}: {e}", path.display())),
    }
}

/// The `GET /key` body: where the key stands, who owns the box, who's asking.
pub fn report(you: Option<&str>, token_error: Option<&str>) -> Value {
    let (owner, owner_source) = owner();
    let is_owner = matches!((&owner, you), (Some(o), Some(y)) if o == y);
    let source = crate::chutes::key_source();
    // A saved file is shadowed by CHUTES_API_KEY in the environment.
    let shadowed_by = if source == "env" { Some("env") } else { None };
    json!({
        "key": source != "none",
        "key_source": source,
        "file": crate::chutes::describe()["key_file"],
        "file_present": key_path().map(|p| p.exists()).unwrap_or(false),
        "shadowed_by": shadowed_by,
        "owner": owner,
        "owner_source": owner_source,
        "you": you,
        "is_owner": is_owner,
        "can_write": is_owner,
        "token_error": token_error,
        "token_max_age": token_max_age(),
        "how": "POST /key {key} or DELETE /key with a mod-protocol token as Authorization: Bearer <token> (or x-mod-token), signed by the owner",
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rejects_garbage() {
        assert!(verify_token("not-a-token").is_err());
        assert!(verify_token("").is_err());
    }

    #[test]
    fn sane_keys() {
        assert!(sane_key("  cpk_abc.def  ").is_ok());
        assert!(sane_key("").is_err());
        assert!(sane_key("has space").is_err());
    }
}
