//! One HTTP client for api.chutes.ai — chat, images, the catalog (paginated
//! and cached) and the control plane (deploy / warmup / utilization).
//! Everything goes through `request`.

use crate::chutes;
use serde_json::{json, Value};
use std::sync::OnceLock;

fn client() -> &'static reqwest::Client {
    static CLIENT: OnceLock<reqwest::Client> = OnceLock::new();
    CLIENT.get_or_init(|| {
        reqwest::Client::builder()
            .timeout(std::time::Duration::from_secs(180))
            .build()
            .expect("reqwest client")
    })
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
    let url = format!("{}{}", chutes::base_url(), path);
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
    let resp = req.send().await.map_err(|e| ApiError { status: 502, message: e.to_string() })?;
    let status = resp.status().as_u16();
    let text = resp.text().await.unwrap_or_default();
    let value = serde_json::from_str::<Value>(&text).unwrap_or(json!({ "raw": text }));
    if status >= 400 {
        return Err(ApiError { status, message: value.to_string() });
    }
    Ok(value)
}

pub async fn chat(key: &str, body: &Value) -> Result<Value, ApiError> {
    request(reqwest::Method::POST, chutes::CHAT_PATH, key, Some(body), None).await
}

/// Call `f` with each candidate model until one answers; returns the model that
/// did, or the last failure. Any error moves down the list: the list is longer
/// than one only when nobody named a model, and then every way a default can
/// fail — a chute out of capacity (429/503), an unfunded key (402), an id that
/// got delisted since someone wrote it down (400/404) — is a reason to try the
/// next one rather than hand the caller an error they didn't cause.
pub async fn try_models<T, F, Fut>(models: &[String], f: F) -> Result<(String, T), ApiError>
where
    F: Fn(&str) -> Fut,
    Fut: std::future::Future<Output = Result<T, ApiError>>,
{
    let mut last = ApiError { status: 400, message: "no model to call".into() };
    for model in models {
        match f(model).await {
            Ok(v) => return Ok((model.clone(), v)),
            Err(e) => last = e,
        }
    }
    Err(last)
}

/// Raw streaming response for SSE pass-through (caller sets `stream: true`).
pub async fn chat_stream_raw(key: &str, body: &Value) -> Result<reqwest::Response, ApiError> {
    let url = format!("{}{}", chutes::base_url(), chutes::CHAT_PATH);
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
    request(reqwest::Method::POST, chutes::IMAGES_PATH, key, Some(body), None).await
}

/// Full chute catalog, normalized and cached (10 min). The chute list *is* the
/// model list — paginated 200 at a time.
pub async fn models(key: &str, refresh: bool) -> Result<Vec<Value>, ApiError> {
    if !refresh {
        if let Some(hit) = chutes::cached() {
            return Ok(hit);
        }
    }
    let mut items: Vec<Value> = Vec::new();
    let mut page = 0u64;
    loop {
        let v = list_chutes(key, page, 200).await?;
        let batch = v.get("items").and_then(|i| i.as_array()).cloned().unwrap_or_default();
        let n = batch.len();
        items.extend(batch.iter().map(chutes::normalize_chute));
        let total = v.get("total").and_then(|t| t.as_u64()).unwrap_or(0);
        if n < 200 || items.len() as u64 >= total || page >= 9 {
            break;
        }
        page += 1;
    }
    chutes::put_cache(items.clone());
    Ok(items)
}

// ── control plane ───────────────────────────────────────────────────────────

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
