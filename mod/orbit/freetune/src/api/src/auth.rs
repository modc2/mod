//! Mod-protocol token auth — mirrors the module's `auth.py` token: a base64url
//! JSON `{data, time, key, signature}` where `signature` is a 65-byte
//! recoverable ECDSA sig over the compact `{"data":..,"time":..}` payload. We
//! accept two signing modes (mirroring auth.py's `verify()`): EIP-191
//! `personal_sign` — what MetaMask and other browser wallets produce — and
//! plain `keccak256(message)` — what an in-process mod key (`eth_keys`
//! `sign_msg`) produces. We recover the signer address from whichever mode
//! matches and check it equals `key` + the token is fresh.
//!
//! This is an OPT-IN owner gate: an owner address (off-tree
//! `~/.mod/freetune/owner.json`, or FREETUNE_OWNER env override) is required
//! to call gated endpoints via a valid token (header `token: <base64url>`).
//! Neither set = open (the default, so local/dev use is frictionless).
//!
//! Authorized = the configured owner OR any address holding BlocTime
//! on-chain — same rule as `claude.is_owner()` (orbit/claude/src/mod.py).
//! BlocTime status is checked via the `chain` module's HTTP API
//! (`GET /bloctime/owner`), short-TTL cached, and degrades to "not a holder"
//! on any RPC/HTTP failure so auth never blocks on chain issues.

use base64::Engine;
use k256::ecdsa::{RecoveryId, Signature, VerifyingKey};
use serde_json::Value;
use sha3::{Digest, Keccak256};
use std::time::{Duration, Instant};

const MAX_AGE_SECS: f64 = 3600.0;
const BLOCTIME_TTL: Duration = Duration::from_secs(60);

static BLOCTIME_CACHE: std::sync::OnceLock<dashmap::DashMap<String, (bool, Instant)>> =
    std::sync::OnceLock::new();

fn bloctime_cache() -> &'static dashmap::DashMap<String, (bool, Instant)> {
    BLOCTIME_CACHE.get_or_init(dashmap::DashMap::new)
}

fn chain_api_url() -> String {
    std::env::var("CHAIN_API_URL").unwrap_or_else(|_| "http://localhost:8800".to_string())
}

fn chain_network() -> String {
    std::env::var("FREETUNE_CHAIN_NETWORK").unwrap_or_else(|_| "testnet".to_string())
}

/// True if `addr` holds BlocTime (staked balance > 0) on-chain, per the
/// `chain` module's Registry/BlocTime contracts. Cached for `BLOCTIME_TTL`;
/// any failure to reach chain (RPC down, API down, bad response) is treated
/// as "not a holder", never as an auth error.
pub async fn is_bloctime_holder(addr: &str) -> bool {
    let addr = addr.to_lowercase();
    if let Some(entry) = bloctime_cache().get(&addr) {
        let (holder, at) = *entry;
        if at.elapsed() < BLOCTIME_TTL {
            return holder;
        }
    }
    let holder = check_bloctime_owner(&addr).await.unwrap_or(false);
    bloctime_cache().insert(addr, (holder, Instant::now()));
    holder
}

async fn check_bloctime_owner(addr: &str) -> anyhow::Result<bool> {
    let url = format!("{}/bloctime/owner", chain_api_url());
    let resp = reqwest::Client::new()
        .get(&url)
        .query(&[("address", addr), ("network", &chain_network())])
        .timeout(Duration::from_secs(5))
        .send()
        .await?
        .error_for_status()?;
    let body: Value = resp.json().await?;
    Ok(body.get("is_owner").and_then(|v| v.as_bool()).unwrap_or(false))
}

/// Verify a token string, returning the recovered/claimed address on success.
pub fn verify_token(token: &str) -> anyhow::Result<String> {
    let raw = b64url_decode(token)?;
    let outer: Value = serde_json::from_slice(&raw)?;

    // Tokens may be nested ({"token": "<inner b64>"}); unwrap one level.
    let hdr: Value = if let Some(inner) = outer.get("token").and_then(|v| v.as_str()) {
        serde_json::from_slice(&b64url_decode(inner)?)?
    } else {
        outer
    };

    let key = hdr["key"].as_str().ok_or_else(|| anyhow::anyhow!("no key"))?;
    let sig = hdr["signature"].as_str().ok_or_else(|| anyhow::anyhow!("no signature"))?;
    let time_s = hdr["time"].as_str().ok_or_else(|| anyhow::anyhow!("no time"))?;

    // Freshness.
    let t: f64 = time_s.parse().map_err(|_| anyhow::anyhow!("bad time"))?;
    let now = chrono::Utc::now().timestamp() as f64;
    if (now - t).abs() > MAX_AGE_SECS {
        anyhow::bail!("stale token");
    }

    // Reconstruct the signed payload exactly as auth.py does: compact JSON of
    // {data, time} in that field order, no spaces.
    let sig_data = format!(
        "{{\"data\":{},\"time\":{}}}",
        serde_json::to_string(&hdr["data"])?,
        serde_json::to_string(&hdr["time"])?,
    );
    let msg = sig_data.as_bytes();

    let eip191_digest = {
        let prefix = format!("\x19Ethereum Signed Message:\n{}", msg.len());
        let mut buf = Vec::with_capacity(prefix.len() + msg.len());
        buf.extend_from_slice(prefix.as_bytes());
        buf.extend_from_slice(msg);
        keccak256(&buf)
    };
    let plain_digest = keccak256(msg);

    for digest in [eip191_digest, plain_digest] {
        if let Ok(addr) = recover_eth_address(&digest, sig) {
            if addr.eq_ignore_ascii_case(key) {
                return Ok(addr.to_lowercase());
            }
        }
    }
    anyhow::bail!("signature does not match key")
}

/// The configured owner address, if any: FREETUNE_OWNER env var takes
/// precedence, else the off-tree `~/.mod/freetune/owner.json` (kept out of
/// committed config.json, like the module's other private auth state).
pub fn owner_address() -> Option<String> {
    if let Ok(v) = std::env::var("FREETUNE_OWNER") {
        let v = v.trim();
        if !v.is_empty() {
            return Some(v.to_string());
        }
    }
    let path = format!("{}/owner.json", crate::state_dir());
    let data = std::fs::read_to_string(path).ok()?;
    let v: Value = serde_json::from_str(&data).ok()?;
    v.get("owner")
        .and_then(|x| x.as_str())
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
}

/// True if auth is required (an owner is configured) and the token is valid
/// for it. Returns Ok(()) when auth is disabled (no owner configured).
///
/// Authorized callers are the configured owner OR any address holding
/// BlocTime on-chain — the BlocTime check only runs once the address has
/// already failed the (cheap, local) owner comparison.
pub async fn require_owner(token: Option<&str>) -> anyhow::Result<()> {
    let Some(owner) = owner_address() else {
        return Ok(()); // gate disabled
    };
    let token = token.ok_or_else(|| anyhow::anyhow!("missing token"))?;
    let addr = verify_token(token)?;
    if addr.eq_ignore_ascii_case(&owner) {
        return Ok(());
    }
    if is_bloctime_holder(&addr).await {
        return Ok(());
    }
    anyhow::bail!("not owner and not a BlocTime holder")
}

fn recover_eth_address(digest: &[u8; 32], sig_hex: &str) -> anyhow::Result<String> {
    let bytes = hex::decode(sig_hex.trim_start_matches("0x"))?;
    if bytes.len() != 65 {
        anyhow::bail!("signature must be 65 bytes, got {}", bytes.len());
    }
    let mut v = bytes[64];
    if v >= 27 {
        v -= 27; // normalise legacy v
    }
    let rec_id = RecoveryId::from_byte(v).ok_or_else(|| anyhow::anyhow!("bad recovery id"))?;
    let sig = Signature::from_slice(&bytes[..64])?;

    let vk = VerifyingKey::recover_from_prehash(digest, &sig, rec_id)?;

    // Ethereum address = last 20 bytes of keccak256(uncompressed pubkey[1..]).
    let pub_pt = vk.to_encoded_point(false);
    let pub_bytes = pub_pt.as_bytes(); // 0x04 || X || Y
    let hash = Keccak256::digest(&pub_bytes[1..]);
    Ok(format!("0x{}", hex::encode(&hash[12..])))
}

fn keccak256(data: &[u8]) -> [u8; 32] {
    let mut h = Keccak256::new();
    h.update(data);
    let out = h.finalize();
    let mut o = [0u8; 32];
    o.copy_from_slice(&out);
    o
}

fn b64url_decode(s: &str) -> anyhow::Result<Vec<u8>> {
    // Tolerate missing padding (auth.py strips it).
    let mut s = s.trim().to_string();
    while s.len() % 4 != 0 {
        s.push('=');
    }
    Ok(base64::engine::general_purpose::URL_SAFE.decode(s.as_bytes())?)
}
