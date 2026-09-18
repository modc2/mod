//! The free default: the `liquidai` module running on this box.
//!
//! A model player that named no `base` used to mean OpenRouter — a key, and a
//! bill for every move of every friendly game. The liquidai module runs LFM
//! weights here, so when it is serving it is the better default in the one way
//! an arena cares about: a match costs nothing and needs nobody's credentials.
//! Anyone who wants Opus still says so — `config.base` and `config.model` win,
//! and when nothing local answers the fallback is OpenRouter exactly as before.
//!
//! Auth: liquidai gates `/v1` behind a session token, and mints one for a shell
//! on this box out of `~/.mod/liquidai/server.secret` (0600) — reading that file
//! IS the proof of being the operator, which is what `m liquidai/…` relies on
//! (`api/auth.py` `mint_local`). So the arena signs its own calls instead of
//! holding a key for the module next door. The payload below has to be
//! byte-identical to the one liquidai signs, keys sorted and no spaces, or the
//! HMAC will not match.

use serde_json::Value;
use sha2::{Digest, Sha256};
use std::sync::{Mutex, OnceLock};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

/// What a model player runs when nothing says otherwise and nothing is resident
/// yet — the same LFM the `agent` module defaults to, small enough to answer a
/// board in seconds on a CPU.
pub const DEFAULT_MODEL: &str = "LiquidAI/LFM2.5-1.2B-Instruct";

const HOST: &str = "http://127.0.0.1:50460";
/// A probe in front of every move would cost more than the default saves.
const PROBE_TTL: Duration = Duration::from_secs(30);
const TOKEN_TTL: u64 = 3600;

/// Where liquidai is; `LIQUIDAI_URL` moves it.
pub fn host() -> String {
    std::env::var("LIQUIDAI_URL")
        .ok()
        .map(|s| s.trim().trim_end_matches('/').to_string())
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| HOST.to_string())
}

/// Its OpenAI-compatible face — the one `model` players speak.
pub fn base() -> String {
    format!("{}/v1", host())
}

/// Does this base URL point at liquidai? Used to pick the key provider, and to
/// know that an unset `config.model` can be filled from what it is serving.
pub fn is_local(base: &str) -> bool {
    let b = base.to_lowercase();
    let h = host().to_lowercase();
    let port = h.rsplit(':').next().unwrap_or("").to_string();
    b.contains("liquidai")
        || b.starts_with(&h)
        || (port.chars().all(|c| c.is_ascii_digit()) && !port.is_empty() && b.contains(&format!(":{port}")))
}

/// The model a local liquidai is ready to serve, or None if it isn't serving.
///
/// "Ready" means the server runtime — the cloud runtime is a relay to Liquid on
/// somebody's key, which is the thing this default exists to avoid. Whatever is
/// already resident answers in seconds; anything else pays for a load first, so
/// the resident model is preferred over the constant when there is one.
pub async fn serving(client: &reqwest::Client) -> Option<String> {
    static CACHE: OnceLock<Mutex<Option<(Instant, Option<String>)>>> = OnceLock::new();
    let cache = CACHE.get_or_init(|| Mutex::new(None));
    if let Ok(seen) = cache.lock() {
        if let Some((at, model)) = seen.as_ref() {
            if at.elapsed() < PROBE_TTL {
                return model.clone();
            }
        }
    }
    let found = probe(client).await;
    if let Ok(mut seen) = cache.lock() {
        *seen = Some((Instant::now(), found.clone()));
    }
    found
}

async fn probe(client: &reqwest::Client) -> Option<String> {
    let health: Value = client
        .get(format!("{}/health", host()))
        .timeout(Duration::from_secs(3))
        .send()
        .await
        .ok()?
        .json()
        .await
        .ok()?;
    if health.get("ok").and_then(|v| v.as_bool()) != Some(true)
        || health.get("server_runtime").and_then(|v| v.as_bool()) != Some(true)
    {
        return None;
    }
    Some(resident_or_default(&health))
}

/// The resident model, unless it can't hold a conversation — an embedding or
/// audio model is loaded for something else and would 400 on a chat turn.
fn resident_or_default(health: &Value) -> String {
    let repo = health.pointer("/resident/repo").and_then(|v| v.as_str());
    let modality = health
        .pointer("/resident/modality")
        .and_then(|v| v.as_str())
        .unwrap_or("");
    match repo {
        Some(r) if !r.is_empty() && !matches!(modality, "embed" | "audio") => r.to_string(),
        _ => DEFAULT_MODEL.to_string(),
    }
}

/// A liquidai session token minted from this box's own secret, or None when
/// there is no secret to read (liquidai not installed, or another user's home).
pub fn token() -> Option<String> {
    let path = std::env::var("LIQUIDAI_SECRET").unwrap_or_else(|_| {
        let home = std::env::var("HOME").unwrap_or_else(|_| "/root".into());
        format!("{home}/.mod/liquidai/server.secret")
    });
    let secret = std::fs::read_to_string(path).ok()?;
    let secret = secret.trim();
    if secret.is_empty() {
        return None;
    }
    let now = SystemTime::now().duration_since(UNIX_EPOCH).ok()?.as_secs();
    Some(mint(secret.as_bytes(), now + TOKEN_TTL))
}

/// liquidai's `mint_local`, in Rust: `{"a","exp","k","root"}` compact and
/// sorted, base64url without padding, HMAC-SHA256 of that body.
fn mint(secret: &[u8], exp: u64) -> String {
    let payload = format!("{{\"a\":\"cli@localhost\",\"exp\":{exp},\"k\":\"cli\",\"root\":1}}");
    let body = b64url(payload.as_bytes());
    let sig = hmac_sha256(secret, body.as_bytes());
    format!("{body}.{}", b64url(&sig))
}

fn b64url(bytes: &[u8]) -> String {
    const ALPHABET: &[u8] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
    let mut out = String::with_capacity(bytes.len().div_ceil(3) * 4);
    for chunk in bytes.chunks(3) {
        let b = [chunk[0], *chunk.get(1).unwrap_or(&0), *chunk.get(2).unwrap_or(&0)];
        let n = ((b[0] as u32) << 16) | ((b[1] as u32) << 8) | b[2] as u32;
        out.push(ALPHABET[(n >> 18 & 63) as usize] as char);
        out.push(ALPHABET[(n >> 12 & 63) as usize] as char);
        if chunk.len() > 1 {
            out.push(ALPHABET[(n >> 6 & 63) as usize] as char);
        }
        if chunk.len() > 2 {
            out.push(ALPHABET[(n & 63) as usize] as char);
        }
    }
    out
}

fn hmac_sha256(key: &[u8], msg: &[u8]) -> [u8; 32] {
    let mut block = [0u8; 64];
    if key.len() > 64 {
        block[..32].copy_from_slice(&Sha256::digest(key));
    } else {
        block[..key.len()].copy_from_slice(key);
    }
    let mut inner_key = [0x36u8; 64];
    let mut outer_key = [0x5cu8; 64];
    for i in 0..64 {
        inner_key[i] ^= block[i];
        outer_key[i] ^= block[i];
    }
    let mut inner = Sha256::new();
    inner.update(inner_key);
    inner.update(msg);
    let inner = inner.finalize();
    let mut outer = Sha256::new();
    outer.update(outer_key);
    outer.update(inner);
    outer.finalize().into()
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn base64url_has_no_padding_and_no_plus_or_slash() {
        assert_eq!(b64url(b""), "");
        assert_eq!(b64url(b"f"), "Zg");
        assert_eq!(b64url(b"fo"), "Zm8");
        assert_eq!(b64url(b"foo"), "Zm9v");
        assert_eq!(b64url(b"foob"), "Zm9vYg");
        assert_eq!(b64url(&[0xfb, 0xff, 0xbe]), "-_--");
    }

    #[test]
    fn hmac_matches_rfc4231() {
        // RFC 4231 case 1
        let mac = hmac_sha256(&[0x0b; 20], b"Hi There");
        let hex: String = mac.iter().map(|b| format!("{b:02x}")).collect();
        assert_eq!(
            hex,
            "b0344c61d8db38535ca8afceaf0bf12b881dc200c9833da726e9376c2e32cff7"
        );
    }

    #[test]
    fn mints_the_token_liquidai_would_have_minted() {
        // Fixture from liquidai's own auth.mint_local with secret=b"topsecret".
        assert_eq!(
            mint(b"topsecret", 1787868280),
            "eyJhIjoiY2xpQGxvY2FsaG9zdCIsImV4cCI6MTc4Nzg2ODI4MCwiayI6ImNsaSIsInJvb3QiOjF9\
             .qP474EKoqgo1U68keLnvsBdXOE8xhP-UYOGQw3HccvA"
        );
    }

    #[test]
    fn knows_its_own_base() {
        assert!(is_local("http://127.0.0.1:50460/v1"));
        assert!(is_local("https://modc2.com/liquidai/v1"));
        assert!(!is_local("https://openrouter.ai/api/v1"));
        assert!(!is_local("https://api.venice.ai/api/v1"));
    }

    #[test]
    fn prefers_the_resident_model_unless_it_cannot_chat() {
        let text = json!({"resident": {"repo": "LiquidAI/LFM2.5-2.6B", "modality": "text"}});
        assert_eq!(resident_or_default(&text), "LiquidAI/LFM2.5-2.6B");
        let embed = json!({"resident": {"repo": "LiquidAI/LFM2-ColBERT", "modality": "embed"}});
        assert_eq!(resident_or_default(&embed), DEFAULT_MODEL);
        assert_eq!(resident_or_default(&json!({})), DEFAULT_MODEL);
    }
}
