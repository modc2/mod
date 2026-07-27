//! Upstream chutes.ai HTTP client.

use serde_json::{json, Value};
use std::sync::OnceLock;

pub const DEFAULT_MODEL: &str = "unsloth/Llama-3.3-70B-Instruct";

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
    std::env::var("CHUTES_BASE_URL").unwrap_or_else(|_| "https://api.chutes.ai".into())
}

pub fn default_model() -> String {
    std::env::var("CHUTES_DEFAULT_MODEL").unwrap_or_else(|_| DEFAULT_MODEL.into())
}

/// Key precedence: explicit (per-request) > env > ~/.mod/chutes/api_key.
pub fn resolve_key(explicit: Option<&str>) -> String {
    if let Some(k) = explicit {
        if !k.trim().is_empty() {
            return k.trim().to_string();
        }
    }
    if let Ok(k) = std::env::var("CHUTES_API_KEY") {
        if !k.trim().is_empty() {
            return k.trim().to_string();
        }
    }
    if let Ok(home) = std::env::var("HOME") {
        if let Ok(k) = std::fs::read_to_string(format!("{home}/.mod/chutes/api_key")) {
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

async fn request(
    method: reqwest::Method,
    path: &str,
    key: &str,
    body: Option<&Value>,
    query: Option<&[(&str, String)]>,
) -> Result<Value, ApiError> {
    let url = format!("{}{}", base_url(), path);
    let mut req = client().request(method, &url);
    if !key.is_empty() {
        req = req.bearer_auth(key);
    }
    if let Some(b) = body {
        req = req.json(b);
    }
    if let Some(q) = query {
        req = req.query(q);
    }
    let resp = req.send().await.map_err(|e| ApiError {
        status: 502,
        message: e.to_string(),
    })?;
    let status = resp.status().as_u16();
    let text = resp.text().await.unwrap_or_default();
    let value = serde_json::from_str::<Value>(&text).unwrap_or(json!({ "raw": text }));
    if status >= 400 {
        return Err(ApiError {
            status,
            message: value.to_string(),
        });
    }
    Ok(value)
}

pub async fn chat(key: &str, body: &Value) -> Result<Value, ApiError> {
    request(reqwest::Method::POST, "/v1/chat/completions", key, Some(body), None).await
}

/// Raw streaming response for SSE pass-through (stream: true must be set by caller).
pub async fn chat_stream_raw(key: &str, body: &Value) -> Result<reqwest::Response, ApiError> {
    let url = format!("{}/v1/chat/completions", base_url());
    let resp = client()
        .post(&url)
        .bearer_auth(key)
        .json(body)
        .send()
        .await
        .map_err(|e| ApiError { status: 502, message: e.to_string() })?;
    if resp.status().as_u16() >= 400 {
        let status = resp.status().as_u16();
        let text = resp.text().await.unwrap_or_default();
        return Err(ApiError { status, message: text });
    }
    Ok(resp)
}

pub async fn generate_image(key: &str, body: &Value) -> Result<Value, ApiError> {
    request(reqwest::Method::POST, "/v1/images/generations", key, Some(body), None).await
}

pub async fn list_chutes(key: &str, page: u64, limit: u64) -> Result<Value, ApiError> {
    request(
        reqwest::Method::GET,
        "/chutes/",
        key,
        None,
        Some(&[("page", page.to_string()), ("limit", limit.to_string())]),
    )
    .await
}

pub async fn get_chute(key: &str, id: &str) -> Result<Value, ApiError> {
    request(reqwest::Method::GET, &format!("/chutes/{id}"), key, None, None).await
}

pub async fn deploy_chute(key: &str, config: &Value) -> Result<Value, ApiError> {
    request(reqwest::Method::POST, "/chutes/", key, Some(config), None).await
}

pub async fn delete_chute(key: &str, id: &str) -> Result<Value, ApiError> {
    request(reqwest::Method::DELETE, &format!("/chutes/{id}"), key, None, None).await
}

pub async fn warmup(key: &str, id: &str) -> Result<Value, ApiError> {
    request(reqwest::Method::GET, &format!("/chutes/warmup/{id}"), key, None, None).await
}

pub async fn utilization(key: &str) -> Result<Value, ApiError> {
    request(reqwest::Method::GET, "/chutes/utilization", key, None, None).await
}
