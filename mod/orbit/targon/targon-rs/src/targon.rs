//! Upstream client for the Targon Hub API (api.targon.com, `/tha/v2`).
//!
//! Targon is Bittensor subnet 4: miners supply the GPUs, the Hub API rents
//! them out as workloads (rentals + confidential VMs), volumes and templates.
//! Everything here is a thin, uniform wrapper — the tool table in tools.rs is
//! what gives each endpoint a name and a schema.

use serde_json::{json, Value};
use std::sync::OnceLock;

pub const SSH_HOST: &str = "ssh.deployments.targon.com";

fn client() -> &'static reqwest::Client {
    static CLIENT: OnceLock<reqwest::Client> = OnceLock::new();
    CLIENT.get_or_init(|| {
        reqwest::Client::builder()
            .timeout(std::time::Duration::from_secs(180))
            .build()
            .expect("reqwest client")
    })
}

pub fn base_url() -> String {
    std::env::var("TARGON_BASE_URL").unwrap_or_else(|_| "https://api.targon.com".into())
}

/// Key precedence: explicit (per-request) > env > ~/.mod/targon/api_key.
pub fn resolve_key(explicit: Option<&str>) -> String {
    if let Some(k) = explicit {
        if !k.trim().is_empty() {
            return k.trim().to_string();
        }
    }
    if let Ok(k) = std::env::var("TARGON_API_KEY") {
        if !k.trim().is_empty() {
            return k.trim().to_string();
        }
    }
    if let Ok(home) = std::env::var("HOME") {
        if let Ok(k) = std::fs::read_to_string(format!("{home}/.mod/targon/api_key")) {
            if !k.trim().is_empty() {
                return k.trim().to_string();
            }
        }
    }
    String::new()
}

#[derive(Debug)]
pub struct ApiError {
    pub status: u16,
    pub message: String,
}

impl std::fmt::Display for ApiError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "upstream {}: {}", self.status, self.message)
    }
}

/// One request against the Hub API. Bodies and responses are JSON except for
/// logs/exec, which stream `text/plain` — those come back as `{"output": ...}`.
pub async fn request(
    method: &str,
    path: &str,
    key: &str,
    query: &[(String, String)],
    body: Option<&Value>,
) -> Result<Value, ApiError> {
    let m = reqwest::Method::from_bytes(method.as_bytes()).unwrap_or(reqwest::Method::GET);
    let mut req = client().request(m, format!("{}{}", base_url(), path));
    if !key.is_empty() {
        req = req.bearer_auth(key);
    }
    if !query.is_empty() {
        req = req.query(query);
    }
    if let Some(b) = body {
        req = req.json(b);
    }
    let resp = req.send().await.map_err(|e| ApiError {
        status: 502,
        message: e.to_string(),
    })?;
    let status = resp.status().as_u16();
    let text = resp.text().await.unwrap_or_default();
    let value = match serde_json::from_str::<Value>(&text) {
        Ok(v) => v,
        Err(_) if text.trim().is_empty() => json!({ "ok": true, "status": status }),
        Err(_) => json!({ "output": text }),
    };
    if status >= 400 {
        return Err(ApiError {
            status,
            message: value.to_string(),
        });
    }
    Ok(value)
}

/// `ssh <workload_uid>@ssh.deployments.targon.com` — the rental login line.
pub fn ssh_command(uid: &str) -> String {
    format!("ssh {uid}@{SSH_HOST}")
}
