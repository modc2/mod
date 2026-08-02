//! Upstream X API v2 client (api.x.com).
//!
//! Two auth rails: an app-only **Bearer** token for reads, and **OAuth 1.0a**
//! user context for anything that acts as an account (post, like, follow,
//! /2/users/me). One keyless path survives without credentials — the
//! syndication CDN renders a single public post — so `get_post` still works on
//! a fresh install.

use base64::Engine;
use hmac::{Hmac, Mac};
use percent_encoding::{utf8_percent_encode, AsciiSet, NON_ALPHANUMERIC};
use serde_json::{json, Value};
use sha1::Sha1;
use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Mutex, OnceLock};

/// RFC 3986 unreserved set — the OAuth 1.0a percent-encoding rule (§3.6).
const UNRESERVED: &AsciiSet = &NON_ALPHANUMERIC
    .remove(b'-')
    .remove(b'.')
    .remove(b'_')
    .remove(b'~');

pub const TWEET_FIELDS: &str =
    "created_at,public_metrics,author_id,conversation_id,lang,referenced_tweets,entities";
pub const USER_FIELDS: &str =
    "created_at,description,public_metrics,verified,profile_image_url,location,url";

fn pct(s: &str) -> String {
    utf8_percent_encode(s, UNRESERVED).to_string()
}

fn client() -> &'static reqwest::Client {
    static CLIENT: OnceLock<reqwest::Client> = OnceLock::new();
    CLIENT.get_or_init(|| {
        reqwest::Client::builder()
            .timeout(std::time::Duration::from_secs(60))
            .user_agent("mod-x/0.2 (+https://github.com/mod)")
            .build()
            .expect("reqwest client")
    })
}

pub fn base_url() -> String {
    std::env::var("X_BASE_URL").unwrap_or_else(|_| "https://api.x.com".into())
}

// ── credentials ──────────────────────────────────────────────────

#[derive(Clone, Default, Debug)]
pub struct Creds {
    pub bearer: String,
    pub consumer_key: String,
    pub consumer_secret: String,
    pub access_token: String,
    pub access_secret: String,
}

impl Creds {
    /// All four OAuth 1.0a legs present → we can act as the account.
    pub fn has_user(&self) -> bool {
        !self.consumer_key.is_empty()
            && !self.consumer_secret.is_empty()
            && !self.access_token.is_empty()
            && !self.access_secret.is_empty()
    }

    pub fn has_any(&self) -> bool {
        !self.bearer.is_empty() || self.has_user()
    }
}

fn env_var(keys: &[&str]) -> String {
    for k in keys {
        if let Ok(v) = std::env::var(k) {
            if !v.trim().is_empty() {
                return v.trim().to_string();
            }
        }
    }
    String::new()
}

fn creds_file() -> Value {
    let home = match std::env::var("HOME") {
        Ok(h) => h,
        Err(_) => return json!({}),
    };
    std::fs::read_to_string(format!("{home}/.mod/x/credentials.json"))
        .ok()
        .and_then(|s| serde_json::from_str::<Value>(&s).ok())
        .unwrap_or_else(|| json!({}))
}

fn from_file(f: &Value, keys: &[&str]) -> String {
    for k in keys {
        if let Some(v) = f.get(*k).and_then(|v| v.as_str()) {
            if !v.trim().is_empty() {
                return v.trim().to_string();
            }
        }
    }
    String::new()
}

/// Precedence per field: explicit (request header / tool arg) → env →
/// `~/.mod/x/credentials.json` (off-tree, never committed).
pub fn resolve(explicit_bearer: Option<&str>) -> Creds {
    let f = creds_file();
    let mut c = Creds {
        bearer: env_var(&["X_BEARER_TOKEN", "TWITTER_BEARER_TOKEN"]),
        consumer_key: env_var(&["X_API_KEY", "X_CONSUMER_KEY", "TWITTER_API_KEY"]),
        consumer_secret: env_var(&["X_API_SECRET", "X_CONSUMER_SECRET", "TWITTER_API_SECRET"]),
        access_token: env_var(&["X_ACCESS_TOKEN", "TWITTER_ACCESS_TOKEN"]),
        access_secret: env_var(&["X_ACCESS_SECRET", "X_ACCESS_TOKEN_SECRET"]),
    };
    if c.bearer.is_empty() {
        c.bearer = from_file(&f, &["bearer_token", "bearer"]);
    }
    if c.consumer_key.is_empty() {
        c.consumer_key = from_file(&f, &["api_key", "consumer_key"]);
    }
    if c.consumer_secret.is_empty() {
        c.consumer_secret = from_file(&f, &["api_secret", "consumer_secret"]);
    }
    if c.access_token.is_empty() {
        c.access_token = from_file(&f, &["access_token"]);
    }
    if c.access_secret.is_empty() {
        c.access_secret = from_file(&f, &["access_token_secret", "access_secret"]);
    }
    if let Some(b) = explicit_bearer {
        if !b.trim().is_empty() {
            c.bearer = b.trim().to_string();
        }
    }
    c
}

// ── OAuth 1.0a signing ───────────────────────────────────────────

type HmacSha1 = Hmac<Sha1>;

/// Nonce = clock nanos + a process counter. Uniqueness per (timestamp, token)
/// is all X requires, and this avoids pulling in an RNG for it.
fn nonce() -> String {
    static SEQ: AtomicU64 = AtomicU64::new(0);
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos() as u64)
        .unwrap_or(0);
    format!("{:x}{:x}", nanos, SEQ.fetch_add(1, Ordering::Relaxed))
}

fn unix_now() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

fn oauth1_header(method: &str, url: &str, query: &[(String, String)], c: &Creds) -> String {
    sign(method, url, query, c, &nonce(), unix_now())
}

/// Build the `Authorization: OAuth ...` header for a request.
/// Note: X API v2 sends JSON bodies, which are *not* part of the signature
/// base string — only the oauth_* params and the URL query are (RFC 5849 §3.4).
/// Nonce and timestamp are parameters so the signature is testable.
fn sign(
    method: &str,
    url: &str,
    query: &[(String, String)],
    c: &Creds,
    nonce: &str,
    timestamp: u64,
) -> String {
    let oauth: Vec<(&str, String)> = vec![
        ("oauth_consumer_key", c.consumer_key.clone()),
        ("oauth_nonce", nonce.to_string()),
        ("oauth_signature_method", "HMAC-SHA1".into()),
        ("oauth_timestamp", timestamp.to_string()),
        ("oauth_token", c.access_token.clone()),
        ("oauth_version", "1.0".into()),
    ];

    let mut params: Vec<(String, String)> = oauth
        .iter()
        .map(|(k, v)| (pct(k), pct(v)))
        .chain(query.iter().map(|(k, v)| (pct(k), pct(v))))
        .collect();
    params.sort();
    let param_string = params
        .iter()
        .map(|(k, v)| format!("{k}={v}"))
        .collect::<Vec<_>>()
        .join("&");

    let base = format!("{}&{}&{}", method, pct(url), pct(&param_string));
    let signing_key = format!("{}&{}", pct(&c.consumer_secret), pct(&c.access_secret));
    let mut mac = HmacSha1::new_from_slice(signing_key.as_bytes()).expect("hmac key");
    mac.update(base.as_bytes());
    let signature = base64::engine::general_purpose::STANDARD.encode(mac.finalize().into_bytes());

    let mut parts: Vec<String> = oauth
        .into_iter()
        .map(|(k, v)| format!("{}=\"{}\"", k, pct(&v)))
        .collect();
    parts.push(format!("oauth_signature=\"{}\"", pct(&signature)));
    parts.sort();
    format!("OAuth {}", parts.join(", "))
}

// ── requests ─────────────────────────────────────────────────────

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Auth {
    /// App-only bearer — reads.
    App,
    /// Acts as the logged-in account — writes and /2/users/me.
    User,
}

#[derive(Debug)]
pub struct ApiError {
    pub status: u16,
    pub message: String,
}

impl std::fmt::Display for ApiError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        if self.status == 0 {
            write!(f, "{}", self.message)
        } else {
            write!(f, "x api {}: {}", self.status, self.message)
        }
    }
}

fn err(status: u16, message: impl Into<String>) -> ApiError {
    ApiError { status, message: message.into() }
}

const NO_CREDS: &str = "no X credentials — set a bearer token (X_BEARER_TOKEN or \
    ~/.mod/x/credentials.json) via `m x/set_keys bearer_token=... persist=True`";
const NO_USER_CREDS: &str = "this action posts as an account and needs OAuth 1.0a user \
    credentials (api_key, api_secret, access_token, access_token_secret) — \
    `m x/set_keys api_key=... api_secret=... access_token=... access_token_secret=... persist=True`";

pub async fn request(
    method: reqwest::Method,
    path: &str,
    query: &[(String, String)],
    body: Option<&Value>,
    creds: &Creds,
    auth: Auth,
) -> Result<Value, ApiError> {
    let url = format!("{}{}", base_url(), path);
    let mut req = client().request(method.clone(), &url);

    // User context prefers OAuth 1.0a; a bearer only works here if it happens
    // to be an OAuth 2.0 *user* token, so we pass it through and let X judge.
    if auth == Auth::User && creds.has_user() {
        req = req.header(
            reqwest::header::AUTHORIZATION,
            oauth1_header(method.as_str(), &url, query, creds),
        );
    } else if !creds.bearer.is_empty() {
        req = req.bearer_auth(&creds.bearer);
    } else if auth == Auth::User {
        return Err(err(401, NO_USER_CREDS));
    } else {
        return Err(err(401, NO_CREDS));
    }

    if !query.is_empty() {
        req = req.query(query);
    }
    if let Some(b) = body {
        req = req.json(b);
    }

    let resp = req.send().await.map_err(|e| err(502, e.to_string()))?;
    let status = resp.status().as_u16();
    let text = resp.text().await.unwrap_or_default();
    let value: Value = serde_json::from_str(&text).unwrap_or_else(|_| json!({ "raw": text }));

    if !(200..300).contains(&status) {
        let detail = value
            .get("detail")
            .or_else(|| value.get("title"))
            .and_then(|d| d.as_str())
            .map(String::from)
            .unwrap_or_else(|| value.to_string());
        let hint = if status == 401 && auth == Auth::User && !creds.has_user() {
            format!(" — {NO_USER_CREDS}")
        } else {
            String::new()
        };
        return Err(err(status, format!("{detail}{hint}")));
    }
    Ok(value)
}

pub async fn get(
    path: &str,
    query: &[(String, String)],
    creds: &Creds,
    auth: Auth,
) -> Result<Value, ApiError> {
    request(reqwest::Method::GET, path, query, None, creds, auth).await
}

pub async fn post(path: &str, body: &Value, creds: &Creds) -> Result<Value, ApiError> {
    request(reqwest::Method::POST, path, &[], Some(body), creds, Auth::User).await
}

pub async fn delete(path: &str, creds: &Creds) -> Result<Value, ApiError> {
    request(reqwest::Method::DELETE, path, &[], None, creds, Auth::User).await
}

// ── identity helpers ─────────────────────────────────────────────

/// The authenticated account's id — stable per token, so cache it rather than
/// spending a /2/users/me round trip before every like/repost/follow.
pub async fn me_id(creds: &Creds) -> Result<String, ApiError> {
    static CACHE: OnceLock<Mutex<HashMap<String, String>>> = OnceLock::new();
    let cache = CACHE.get_or_init(|| Mutex::new(HashMap::new()));
    let key = if creds.access_token.is_empty() {
        creds.bearer.clone()
    } else {
        creds.access_token.clone()
    };
    if let Some(id) = cache.lock().ok().and_then(|c| c.get(&key).cloned()) {
        return Ok(id);
    }
    let me = get("/2/users/me", &[], creds, Auth::User).await?;
    let id = me
        .pointer("/data/id")
        .and_then(|v| v.as_str())
        .ok_or_else(|| err(502, "users/me returned no id"))?
        .to_string();
    if let Ok(mut c) = cache.lock() {
        c.insert(key, id.clone());
    }
    Ok(id)
}

/// Accept a numeric id or an @handle anywhere a user is named.
pub async fn user_id(who: &str, creds: &Creds) -> Result<String, ApiError> {
    let who = who.trim().trim_start_matches('@');
    if !who.is_empty() && who.chars().all(|c| c.is_ascii_digit()) {
        return Ok(who.to_string());
    }
    let u = get(&format!("/2/users/by/username/{who}"), &[], creds, Auth::App).await?;
    u.pointer("/data/id")
        .and_then(|v| v.as_str())
        .map(String::from)
        .ok_or_else(|| err(404, format!("no such user: @{who}")))
}

// ── keyless fallback ─────────────────────────────────────────────

/// Public embed data for one post, no credentials required. The syndication
/// CDN wants a `token` derived from the id; any value in the expected shape is
/// accepted, so we derive one deterministically.
pub async fn syndication_post(id: &str) -> Result<Value, ApiError> {
    let token = syndication_token(id);
    let url = format!(
        "https://cdn.syndication.twimg.com/tweet-result?id={id}&lang=en&token={token}"
    );
    let resp = client()
        .get(&url)
        .send()
        .await
        .map_err(|e| err(502, e.to_string()))?;
    let status = resp.status().as_u16();
    let text = resp.text().await.unwrap_or_default();
    if !(200..300).contains(&status) || text.trim().is_empty() {
        return Err(err(status, format!("post {id} not available without credentials")));
    }
    let v: Value = serde_json::from_str(&text).map_err(|e| err(502, e.to_string()))?;
    Ok(json!({
        "data": {
            "id": v.get("id_str").cloned().unwrap_or_else(|| json!(id)),
            "text": v.get("text").cloned().unwrap_or(Value::Null),
            "created_at": v.get("created_at").cloned().unwrap_or(Value::Null),
            "lang": v.get("lang").cloned().unwrap_or(Value::Null),
            "author": v.get("user").cloned().unwrap_or(Value::Null),
            "public_metrics": {
                "like_count": v.get("favorite_count").cloned().unwrap_or(Value::Null),
                "reply_count": v.get("conversation_count").cloned().unwrap_or(Value::Null)
            }
        },
        "source": "syndication (keyless — limited fields)"
    }))
}

/// The CDN's token: base-36 of (id / 1e15) * pi, with punctuation stripped.
fn syndication_token(id: &str) -> String {
    let n: f64 = id.parse::<f64>().unwrap_or(0.0);
    let t = ((n / 1e15) * std::f64::consts::PI).to_string();
    // JS does `.toString(36)`; a base-36 render of the fractional part.
    let mut v = t.replace(['.', '-'], "");
    let digits: String = v.chars().filter(|c| c.is_ascii_digit()).collect();
    v = digits
        .chars()
        .map(|c| std::char::from_digit(c.to_digit(10).unwrap_or(0), 36).unwrap_or('0'))
        .collect();
    if v.is_empty() {
        "a".into()
    } else {
        v
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// X's own documented OAuth 1.0a example ("Creating a signature"). If this
    /// signature matches, our base string, encoding and HMAC-SHA1 are right —
    /// the one part of the write path that can't be checked without live keys.
    #[test]
    fn oauth1_matches_documented_signature() {
        let c = Creds {
            bearer: String::new(),
            consumer_key: "xvz1evFS4wEEPTGEFPHBog".into(),
            consumer_secret: "kAcSOqF21Fu85e7zjz7ZN2U4ZRhfV3WpwPAoE3Z7kBw".into(),
            access_token: "370773112-GmHxMAgYyLbNEtIKZeRNFsMKPR9EyMZeS9weJAEb".into(),
            access_secret: "LswwdoUaIvS8ltyTt5jkRh4J50vUPVVHtR2YPi5kE".into(),
        };
        let query = vec![
            ("status".to_string(), "Hello Ladies + Gentlemen, a signed OAuth request!".to_string()),
            ("include_entities".to_string(), "true".to_string()),
        ];
        let header = sign(
            "POST",
            "https://api.twitter.com/1.1/statuses/update.json",
            &query,
            &c,
            "kYjzVBB8Y0ZFabxSWbWovY3uYSQ2pTgmZeNu2VS4cg",
            1318622958,
        );
        assert!(
            header.contains(&format!("oauth_signature=\"{}\"", pct("hCtSmYh+iHYCEqBWrE7C7hYmtUk="))),
            "unexpected signature in: {header}"
        );
    }

    #[test]
    fn percent_encoding_follows_rfc3986() {
        assert_eq!(pct("Ladies + Gentlemen"), "Ladies%20%2B%20Gentlemen");
        assert_eq!(pct("-._~"), "-._~");
        assert_eq!(pct("a/b?c=d"), "a%2Fb%3Fc%3Dd");
    }

    #[test]
    fn explicit_bearer_beats_env_and_file() {
        let c = resolve(Some("  tok-123  "));
        assert_eq!(c.bearer, "tok-123");
    }
}
