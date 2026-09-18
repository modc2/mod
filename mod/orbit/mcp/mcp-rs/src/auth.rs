//! Identity for the hub — the very same wallet sessions the build module issues.
//!
//! The hub deliberately does NOT run its own sign-in. `orbit/build` is the
//! identity issuer on this host: it does the wallet challenge/verify across
//! three curves, shows the Terms of Use, and keeps the owner / co-owner /
//! whitelist / invite bookkeeping. It mints an HMAC bearer token
//! (`address:timestamp:hmac`) signed with ~/.mod/build/server.secret.
//!
//! Every one of those tokens validates *here*, because this module reads the
//! same secret and the same owner/whitelist files. Sign in once at /build and
//! the hub already knows who you are — one identity, two consoles. Nothing in
//! this file can mint a token; it only verifies what build minted, so the hub
//! is a relying party and never a second source of truth.
//!
//! Three tiers come out of it:
//!   • owner / editor — may edit the registry AND execute tools
//!   • viewer         — signed in, may browse the registry and tool schemas
//!   • anonymous      — reads only
//!
//! API keys (see `keys.rs`) are the non-browser path: a long-lived Bearer for
//! an MCP client, good for tool calls, never for registry edits.

use crate::keys;
use crate::store;
use axum::http::HeaderMap;
use hmac::{Hmac, Mac};
use serde_json::Value;
use sha2::Sha256;
use std::path::PathBuf;

type HmacSha256 = Hmac<Sha256>;

/// Marker build signs into tokens minted by redeeming a hand-off QR:
/// `address:timestamp:ho:hmac`. Validates like any other token.
const HANDOFF_MARK: &str = "ho";
/// Walletless guest identities from invite redemption. They outlive the 24h
/// window but die with their grant.
const GUEST_PREFIX: &str = "guest_";
const TOKEN_TTL: i64 = 86_400;

/// Which module issues the identities the hub trusts. Always `build` in
/// practice — the env var exists so a fork can point at its own issuer.
pub fn issuer() -> String {
    std::env::var("MCP_AUTH_MODULE").unwrap_or_else(|_| "build".into())
}

fn home() -> PathBuf {
    PathBuf::from(std::env::var("HOME").unwrap_or_else(|_| "/root".into()))
}

/// The issuer's private dir — ~/.mod/build by default.
fn issuer_dir() -> PathBuf {
    home().join(".mod").join(issuer())
}

fn mod_root() -> PathBuf {
    PathBuf::from(std::env::var("MOD_ROOT").unwrap_or_else(|_| "/root/mod/mod".into()))
}

/// The issuer's token-signing secret. Absent → the issuer has never run, so
/// there is no identity to borrow and the hub falls back to its own gate.
fn shared_secret() -> Option<Vec<u8>> {
    std::fs::read(issuer_dir().join("server.secret"))
        .ok()
        .filter(|b| !b.is_empty())
}

/// True when wallet sign-in is actually usable here: the issuer's secret
/// exists and someone owns the host. Without both, gating writes on identity
/// would lock everyone out, so the legacy secret/open behaviour stands.
pub fn available() -> bool {
    shared_secret().is_some() && !owner_addresses().is_empty()
}

/// Canonical form of an address, matching the issuer's `keys::normalize_addr`:
/// EVM hex is case-insensitive so it lowercases, base58 (Solana, SS58) carries
/// the key in its case so it is left exactly as the wallet gave it.
pub fn normalize_addr(address: &str) -> String {
    let a = address.trim();
    if let Some(hex) = a.strip_prefix("0x").or_else(|| a.strip_prefix("0X")) {
        if hex.len() == 40 && hex.chars().all(|c| c.is_ascii_hexdigit()) {
            return a.to_lowercase();
        }
        return a.to_lowercase();
    }
    const B58: &str = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz";
    if (32..=50).contains(&a.len()) && a.chars().all(|c| B58.contains(c)) {
        return a.to_string();
    }
    a.to_lowercase()
}

fn hmac_hex(secret: &[u8], payload: &str) -> String {
    let mut mac = HmacSha256::new_from_slice(secret).expect("hmac accepts any key length");
    mac.update(payload.as_bytes());
    hex::encode(mac.finalize().into_bytes())
}

/// Length-independent, byte-blind equality — this runs on every request an
/// attacker can make, so it must not leak how much of a guessed tag was right.
pub fn ct_eq(a: &[u8], b: &[u8]) -> bool {
    a.len() == b.len() && a.iter().zip(b).fold(0u8, |acc, (x, y)| acc | (x ^ y)) == 0
}

fn now() -> i64 {
    store::now() as i64
}

/// Verify a build-issued bearer token and return the address it names.
pub fn validate_token(token: &str) -> Result<String, String> {
    let secret = shared_secret().ok_or("no identity issuer on this host")?;
    let parts: Vec<&str> = token.split(':').collect();
    let (address, ts, marked, sig) = match parts.as_slice() {
        [a, t, s] => (*a, *t, false, *s),
        [a, t, m, s] if *m == HANDOFF_MARK => (*a, *t, true, *s),
        _ => return Err("not a session token".into()),
    };
    let issued: i64 = ts.parse().map_err(|_| "bad timestamp".to_string())?;

    let is_guest = address.starts_with(GUEST_PREFIX);
    if !is_guest && now() - issued > TOKEN_TTL {
        return Err("session expired — sign in again".into());
    }

    let payload = if marked {
        format!("{address}:{issued}:{HANDOFF_MARK}")
    } else {
        format!("{address}:{issued}")
    };
    if !ct_eq(hmac_hex(&secret, &payload).as_bytes(), sig.as_bytes()) {
        return Err("invalid session signature".into());
    }
    if is_guest && !grant_active(address) {
        return Err("guest access expired".into());
    }
    Ok(address.to_string())
}

// ── who owns this host ───────────────────────────────────────────────

fn read_json(path: PathBuf) -> Option<Value> {
    serde_json::from_str(&std::fs::read_to_string(path).ok()?).ok()
}

/// Every address that counts as the owner: the issuer's config.json `owner`,
/// then owner.json, then the co-owner list. Same precedence build uses.
pub fn owner_addresses() -> Vec<String> {
    let mut all: Vec<String> = Vec::new();
    let mut push = |a: String| {
        let a = normalize_addr(&a);
        if !a.is_empty() && !all.contains(&a) {
            all.push(a);
        }
    };

    let cfg = mod_root().join("orbit").join(issuer()).join("config.json");
    if let Some(owner) = read_json(cfg)
        .and_then(|c| c.get("owner").and_then(|v| v.as_str()).map(str::to_string))
    {
        push(owner);
    }
    if let Some(owner) = read_json(issuer_dir().join("owner.json"))
        .and_then(|c| c.get("owner").and_then(|v| v.as_str()).map(str::to_string))
    {
        push(owner);
    }
    for a in json_addresses(issuer_dir().join("owners.json")) {
        push(a);
    }
    all
}

/// Read `["0x..", ..]` or `{"addresses": [..]}` — both shapes the issuer writes.
fn json_addresses(path: PathBuf) -> Vec<String> {
    let Some(v) = read_json(path) else { return Vec::new() };
    let arr = v
        .as_array()
        .cloned()
        .or_else(|| v.get("addresses").and_then(|a| a.as_array()).cloned())
        .unwrap_or_default();
    arr.iter()
        .filter_map(|item| match item {
            Value::String(s) => Some(s.clone()),
            Value::Object(o) => o.get("address").and_then(|a| a.as_str()).map(str::to_string),
            _ => None,
        })
        .map(|a| normalize_addr(&a))
        .collect()
}

fn whitelisted(addr: &str) -> bool {
    json_addresses(issuer_dir().join("whitelist.json")).iter().any(|w| w == addr)
}

/// True when the address holds an unexpired redemption of a live invite.
fn grant_active(address: &str) -> bool {
    let addr = normalize_addr(address);
    let Some(file) = read_json(issuer_dir().join("grants.json")) else { return false };
    let n = now();
    let live: Vec<&str> = file
        .get("grants")
        .and_then(|g| g.as_array())
        .map(|a| {
            a.iter()
                .filter(|g| {
                    !g.get("revoked").and_then(|r| r.as_bool()).unwrap_or(false)
                        && g.get("exp").and_then(|e| e.as_i64()).unwrap_or(0) > n
                })
                .filter_map(|g| g.get("id").and_then(|i| i.as_str()))
                .collect()
        })
        .unwrap_or_default();
    file.get("redemptions")
        .and_then(|r| r.as_array())
        .map(|a| {
            a.iter().any(|r| {
                r.get("address").and_then(|v| v.as_str()).map(normalize_addr).as_deref() == Some(&addr)
                    && r.get("exp").and_then(|e| e.as_i64()).unwrap_or(0) > n
                    && r.get("grant")
                        .and_then(|g| g.as_str())
                        .map(|g| live.contains(&g))
                        .unwrap_or(false)
            })
        })
        .unwrap_or(false)
}

/// The tier an address sits in — the same three names build uses.
pub fn role_of(address: &str) -> &'static str {
    let addr = normalize_addr(address);
    if addr.is_empty() {
        return "viewer";
    }
    if owner_addresses().iter().any(|o| o == &addr) {
        "owner"
    } else if whitelisted(&addr) || grant_active(&addr) {
        "editor"
    } else {
        "viewer"
    }
}

// ── the caller ───────────────────────────────────────────────────────

/// Who is making this request, and what that buys them.
#[derive(Debug, Clone)]
pub enum Caller {
    /// No credential at all.
    Anon,
    /// A wallet session minted by the issuer.
    Wallet { address: String, role: &'static str },
    /// A hub API key — the MCP-client path. Calls only.
    Key { id: String, name: String },
    /// The legacy ~/.mod/mcp/server.secret bearer. Full power, for scripts.
    Root,
}

fn bearer(headers: &HeaderMap) -> Option<String> {
    headers
        .get("authorization")
        .and_then(|v| v.to_str().ok())
        .and_then(|v| v.strip_prefix("Bearer ").or_else(|| v.strip_prefix("bearer ")))
        .map(|t| t.trim().to_string())
        .filter(|t| !t.is_empty())
}

/// Resolve the caller from the request headers. The token forms are tried in
/// the order they can be told apart cheaply: hub secret → API key → wallet.
pub fn caller(headers: &HeaderMap) -> Caller {
    let Some(token) = bearer(headers) else { return Caller::Anon };
    if let Some(secret) = store::secret() {
        if ct_eq(token.as_bytes(), secret.as_bytes()) {
            return Caller::Root;
        }
    }
    if token.starts_with(keys::KEY_PREFIX) {
        if let Some(k) = keys::verify(&token) {
            return Caller::Key { id: k.id, name: k.name };
        }
        return Caller::Anon;
    }
    match validate_token(&token) {
        Ok(address) => {
            let role = role_of(&address);
            Caller::Wallet { address: normalize_addr(&address), role }
        }
        Err(_) => Caller::Anon,
    }
}

fn env_on(name: &str) -> bool {
    std::env::var(name).ok().as_deref() == Some("1")
}

/// Dev escape hatch: ACCESS_OPEN=1 turns every gate off.
pub fn open_mode() -> bool {
    env_on("ACCESS_OPEN")
}

/// Tool execution open to anyone (the pre-identity behaviour).
pub fn open_calls() -> bool {
    open_mode() || env_on("MCP_OPEN_CALLS")
}

/// When no credential source exists at all, gating would brick the hub —
/// fall back to the old open behaviour instead.
fn ungated() -> bool {
    open_mode() || (!available() && store::secret().is_none())
}

/// A request that did not arrive through the public gateway. Caddy stamps the
/// `X-Forwarded-*` family on everything it proxies, so their absence means the
/// caller is already on this host — where it can reach every aggregated
/// upstream directly, making a gate here theatre rather than security. Set
/// MCP_GATE_LOCAL=1 to demand a credential from local callers too.
pub fn local_request(headers: &HeaderMap) -> bool {
    if env_on("MCP_GATE_LOCAL") {
        return false;
    }
    !headers.keys().any(|k| k.as_str().starts_with("x-forwarded-"))
}

impl Caller {
    /// May edit the registry: add, remove, toggle a server.
    pub fn can_write(&self) -> bool {
        if ungated() {
            return true;
        }
        matches!(self, Caller::Root)
            || matches!(self, Caller::Wallet { role, .. } if *role == "owner" || *role == "editor")
    }

    /// May execute a tool through the hub. Viewers deliberately cannot: an
    /// aggregated tool can move money or post publicly on an upstream, so
    /// browsing the catalogue and running it are different privileges.
    pub fn can_call(&self) -> bool {
        open_calls() || ungated() || self.can_write() || matches!(self, Caller::Key { .. })
    }

    /// Owner-only powers — minting and revoking API keys.
    pub fn is_owner(&self) -> bool {
        if ungated() {
            return true;
        }
        matches!(self, Caller::Root) || matches!(self, Caller::Wallet { role, .. } if *role == "owner")
    }

    pub fn address(&self) -> Option<&str> {
        match self {
            Caller::Wallet { address, .. } => Some(address),
            _ => None,
        }
    }

    pub fn role(&self) -> &'static str {
        match self {
            Caller::Anon => "anonymous",
            Caller::Wallet { role, .. } => role,
            Caller::Key { .. } => "key",
            Caller::Root => "root",
        }
    }

    /// What the console shows about this session.
    pub fn describe(&self) -> Value {
        serde_json::json!({
            "authenticated": !matches!(self, Caller::Anon),
            "address": self.address(),
            "role": self.role(),
            "key": match self { Caller::Key { name, .. } => Some(name.clone()), _ => None },
            "can_write": self.can_write(),
            "can_call": self.can_call(),
            "is_owner": self.is_owner(),
        })
    }
}

/// Everything the console needs to render sign-in without guessing: who issues
/// identities, where its API lives, and which gates are currently armed.
pub fn config_json() -> Value {
    let issuer = issuer();
    serde_json::json!({
        "issuer": issuer,
        "issuer_api": std::env::var("MCP_AUTH_API").unwrap_or_else(|_| format!("/api/{issuer}")),
        // The issuer's app and the hub's app are served from one origin, so a
        // session stored under this key is already shared between them.
        "token_key": std::env::var("MCP_AUTH_TOKEN_KEY").unwrap_or_else(|_| "build_jobs_token".into()),
        "available": available(),
        "owners": owner_addresses().len(),
        "gates": {
            "writes": !ungated(),
            "calls": !(open_calls() || ungated()),
            "local_calls_open": !env_on("MCP_GATE_LOCAL"),
            "hub_secret": store::secret().is_some(),
        },
    })
}
