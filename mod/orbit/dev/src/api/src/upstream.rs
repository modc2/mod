//! Generic proxy to any OpenAI-API-standard provider.
//!
//! Every provider exposes the same two endpoints under its `base_url`:
//!   GET  {base}/models             — model list
//!   POST {base}/chat/completions   — chat completion (supports `stream: true`)
//!
//! `chat` streams the upstream bytes straight back to the caller so SSE token
//! streaming is preserved end to end (no buffering).

use axum::body::Body;
use axum::http::{header, StatusCode};
use axum::response::{IntoResponse, Response};
use serde_json::Value;

use crate::providers::Provider;

/// GET {base}/models. `api_key` is optional — some providers serve the model
/// list publicly (venice), others require a key (openai). We send it when we
/// have one.
pub async fn list_models(http: &reqwest::Client, provider: &Provider, api_key: Option<&str>) -> Response {
    let url = format!("{}/models", provider.base_url);
    let mut req = http.get(&url);
    if let Some(k) = api_key {
        if !k.is_empty() {
            req = req.bearer_auth(k);
        }
    }
    match req.send().await {
        Ok(resp) => {
            let status =
                StatusCode::from_u16(resp.status().as_u16()).unwrap_or(StatusCode::BAD_GATEWAY);
            match resp.text().await {
                Ok(text) => {
                    let body: Value = serde_json::from_str(&text)
                        .unwrap_or_else(|_| serde_json::json!({ "raw": text }));
                    (status, axum::Json(body)).into_response()
                }
                Err(e) => bad_gateway(format!("read models: {e}")),
            }
        }
        Err(e) => bad_gateway(format!("{} models: {e}", provider.name)),
    }
}

/// POST {base}/chat/completions. Forwards the OpenAI-style `body` with the
/// resolved key and streams the upstream response back unbuffered.
pub async fn chat(http: &reqwest::Client, provider: &Provider, api_key: &str, body: Value) -> Response {
    let url = format!("{}/chat/completions", provider.base_url);
    let upstream = http.post(&url).bearer_auth(api_key).json(&body).send().await;

    match upstream {
        Ok(resp) => {
            let status =
                StatusCode::from_u16(resp.status().as_u16()).unwrap_or(StatusCode::BAD_GATEWAY);
            let content_type = resp
                .headers()
                .get(header::CONTENT_TYPE)
                .and_then(|v| v.to_str().ok())
                .unwrap_or("application/json")
                .to_string();
            // Pass the bytes through unbuffered so token streaming is preserved.
            let stream = resp.bytes_stream();
            Response::builder()
                .status(status)
                .header(header::CONTENT_TYPE, content_type)
                .header("cache-control", "no-cache")
                .body(Body::from_stream(stream))
                .unwrap_or_else(|_| {
                    (StatusCode::INTERNAL_SERVER_ERROR, "stream build error").into_response()
                })
        }
        Err(e) => bad_gateway(format!("{} chat: {e}", provider.name)),
    }
}

fn bad_gateway(msg: String) -> Response {
    (
        StatusCode::BAD_GATEWAY,
        axum::Json(serde_json::json!({ "error": msg })),
    )
        .into_response()
}
