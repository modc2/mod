//! chutes.ai — the one upstream. Base URL, key resolution, the default-model
//! list, and the normalizer from a chute record into the model row the console
//! and the MCP `models` tool return. Keys are resolved per request and never
//! logged.

use serde_json::{json, Value};
use std::sync::{Mutex, OnceLock};
use std::time::{Duration, Instant};

pub const ID: &str = "chutes";
pub const LABEL: &str = "CHUTES";
/// Console accent (8-bit palette).
pub const COLOR: &str = "#3ddc97";
pub const BASE: &str = "https://api.chutes.ai";
pub const ENV_BASE: &str = "CHUTES_BASE_URL";
pub const ENV_KEY: &str = "CHUTES_API_KEY";
pub const ENV_MODEL: &str = "CHUTES_DEFAULT_MODEL";
/// Off-chain key directory under ~/.mod/ (per the secrets-off-chain rule).
pub const KEY_DIR: &str = "chutes";
/// Verified live in the catalog; the old Llama default was delisted.
pub const DEFAULT_MODEL: &str = "Qwen/Qwen3-32B-TEE";
pub const CHAT_PATH: &str = "/v1/chat/completions";
pub const IMAGES_PATH: &str = "/v1/images/generations";
pub const SIGNUP: &str = "https://chutes.ai";

pub fn base_url() -> String {
    std::env::var(ENV_BASE).ok().filter(|v| !v.trim().is_empty()).unwrap_or_else(|| BASE.into())
}

/// Box-local defaults — deployment state, so it lives off-tree next to the
/// key rather than in the committed config.json:
///
/// ```json
/// { "models": ["Qwen/Qwen3-32B-TEE", "some/stand-in"] }
/// ```
///
/// `models` takes a string or a list. The older `{"models": {"chutes": …}}`
/// shape is still read.
fn box_defaults() -> Value {
    let home = match std::env::var("HOME") {
        Ok(h) => h,
        Err(_) => return Value::Null,
    };
    std::fs::read_to_string(format!("{home}/.mod/chutes/defaults.json"))
        .ok()
        .and_then(|raw| serde_json::from_str::<Value>(&raw).ok())
        .unwrap_or(Value::Null)
}

/// The default model, plus any stand-ins to fall back to when it can't answer
/// (a chute that's cold, delisted, or out of capacity moves the call to the
/// next one on the list).
pub fn default_models() -> Vec<String> {
    if let Some(m) = std::env::var(ENV_MODEL).ok().filter(|v| !v.trim().is_empty()) {
        return vec![m.trim().to_string()];
    }
    let configured = box_defaults().get("models").cloned();
    let configured = match configured {
        Some(Value::Object(o)) => o.get(ID).cloned(),
        other => other,
    };
    let listed: Vec<String> = match configured {
        Some(Value::String(m)) => vec![m],
        Some(Value::Array(ms)) => ms.iter().filter_map(|m| m.as_str()).map(String::from).collect(),
        _ => Vec::new(),
    };
    let mut out: Vec<String> =
        listed.into_iter().map(|m| m.trim().to_string()).filter(|m| !m.is_empty()).collect();
    if out.is_empty() {
        out.push(DEFAULT_MODEL.into());
    }
    out
}

pub fn default_model() -> String {
    default_models().remove(0)
}

/// Every place a server-side key can live, in precedence order — one list so
/// `resolve_key` and `key_source` can never disagree about whether the box is
/// usable. Returns (key, where-it-came-from).
///
/// The files are the shapes the sibling key-holding mods actually write:
/// `~/.mod/chutes/api_key` (raw), `~/.mod/chutes/key.json` {"key": …},
/// `~/.mod/model/chutes/apikeys.json` ["sk-…", …] (model mod).
fn lookup_key() -> Option<(String, &'static str)> {
    if let Ok(k) = std::env::var(ENV_KEY) {
        if !k.trim().is_empty() {
            return Some((k.trim().to_string(), "env"));
        }
    }
    let home = std::env::var("HOME").ok()?;
    let raw = |path: String| std::fs::read_to_string(path).ok();

    if let Some(k) = raw(format!("{home}/.mod/{KEY_DIR}/api_key")) {
        if !k.trim().is_empty() {
            return Some((k.trim().to_string(), "api_key"));
        }
    }
    if let Some(v) = raw(format!("{home}/.mod/{KEY_DIR}/key.json")).and_then(|r| serde_json::from_str::<Value>(&r).ok()) {
        for field in ["key", "api_key"] {
            if let Some(k) = v.get(field).and_then(|k| k.as_str()).map(str::trim).filter(|k| !k.is_empty()) {
                return Some((k.to_string(), "key.json"));
            }
        }
    }
    if let Some(v) =
        raw(format!("{home}/.mod/model/{KEY_DIR}/apikeys.json")).and_then(|r| serde_json::from_str::<Value>(&r).ok())
    {
        if let Some(k) = v
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(|k| k.as_str())
            .map(str::trim)
            .find(|k| !k.is_empty())
        {
            return Some((k.to_string(), "apikeys.json"));
        }
    }
    None
}

/// Key precedence: explicit (per-request) > everything `lookup_key` knows.
pub fn resolve_key(explicit: Option<&str>) -> String {
    if let Some(k) = explicit {
        if !k.trim().is_empty() {
            return k.trim().to_string();
        }
    }
    lookup_key().map(|(k, _)| k).unwrap_or_default()
}

/// Where a resolved key came from — surfaced by `status`, never the key itself.
pub fn key_source() -> &'static str {
    lookup_key().map(|(_, src)| src).unwrap_or("none")
}

pub fn describe() -> Value {
    json!({
        "id": ID,
        "label": LABEL,
        "color": COLOR,
        "base_url": base_url(),
        "default_model": default_model(),
        "default_models": default_models(),
        "images": true,
        "key": key_source() != "none",
        "key_source": key_source(),
        "key_env": ENV_KEY,
        "key_file": format!("~/.mod/{KEY_DIR}/api_key"),
        "signup": SIGNUP,
    })
}

// ── normalized model records ────────────────────────────────────────────────
// {id, name, chute_id, context, in_price, out_price, kind, tags, invocations}
// where prices are USD per million tokens.

fn f(v: Option<&Value>) -> Option<f64> {
    match v? {
        Value::Number(n) => n.as_f64(),
        Value::String(s) => s.parse::<f64>().ok(),
        _ => None,
    }
}

/// A chute record → model row. `standard_template` tells chat from diffusion.
pub fn normalize_chute(m: &Value) -> Value {
    let per_m = |k: &str| {
        m.get("current_estimated_price")
            .and_then(|p| p.get("per_million_tokens"))
            .and_then(|p| p.get(k))
            .and_then(|p| f(p.get("usd")))
            .unwrap_or(0.0)
    };
    let template = m.get("standard_template").and_then(|v| v.as_str()).unwrap_or("");
    let kind = match template {
        "vllm" | "sglang" => "chat",
        "diffusion" => "image",
        "tei" => "embedding",
        _ => "custom",
    };
    let mut tags: Vec<String> = Vec::new();
    if !template.is_empty() {
        tags.push(template.into());
    }
    if m.get("tee").and_then(|v| v.as_bool()).unwrap_or(false) {
        tags.push("tee".into());
    }
    if m.get("hot").and_then(|v| v.as_bool()).unwrap_or(false) {
        tags.push("hot".into());
    }
    json!({
        "id": m.get("name").and_then(|v| v.as_str()).unwrap_or(""),
        "name": m.get("name").and_then(|v| v.as_str()).unwrap_or(""),
        "chute_id": m.get("chute_id").and_then(|v| v.as_str()).unwrap_or(""),
        // The chute catalog doesn't publish a context window.
        "context": 0,
        "in_price": per_m("input"),
        "out_price": per_m("output"),
        "kind": kind,
        "tags": tags,
        "invocations": m.get("invocation_count").and_then(|v| v.as_u64()).unwrap_or(0),
        "description": m.get("tagline").and_then(|v| v.as_str()).unwrap_or("").chars().take(240).collect::<String>(),
    })
}

// ── catalog cache ───────────────────────────────────────────────────────────
// The chutes catalog is 3 paginated round-trips; nobody wants that per
// keystroke in the model browser.

const TTL: Duration = Duration::from_secs(600);

type Cache = Mutex<Option<(Instant, Vec<Value>)>>;

fn cache() -> &'static Cache {
    static C: OnceLock<Cache> = OnceLock::new();
    C.get_or_init(|| Mutex::new(None))
}

pub fn cached() -> Option<Vec<Value>> {
    let c = cache().lock().ok()?;
    let (at, models) = c.as_ref()?;
    (at.elapsed() < TTL).then(|| models.clone())
}

pub fn put_cache(models: Vec<Value>) {
    if let Ok(mut c) = cache().lock() {
        *c = Some((Instant::now(), models));
    }
}
