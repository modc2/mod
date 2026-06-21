//! Thin proxy to the Venice AI OpenAI-compatible API.
//!
//! - `list_models` is public (no key) — same as the Python mod's `model2info`,
//!   which fetches `/models` unauthenticated.
//! - `chat` forwards a chat-completion request using whichever API key the
//!   caller resolved to (their BYOK key, or our backend key after payment),
//!   and streams the upstream response straight back to the client so SSE
//!   `stream: true` works end to end.

use axum::body::Body;
use axum::http::{header, StatusCode};
use axum::response::{IntoResponse, Response};
use serde_json::Value;

const VENICE_BASE: &str = "https://api.venice.ai/api/v1";

pub async fn list_models(http: &reqwest::Client) -> Response {
    let url = format!("{VENICE_BASE}/models");
    match http.get(&url).send().await {
        Ok(resp) => {
            let status = StatusCode::from_u16(resp.status().as_u16())
                .unwrap_or(StatusCode::BAD_GATEWAY);
            match resp.text().await {
                Ok(text) => {
                    let body: Value = serde_json::from_str(&text)
                        .unwrap_or_else(|_| serde_json::json!({ "raw": text }));
                    (status, axum::Json(body)).into_response()
                }
                Err(e) => (
                    StatusCode::BAD_GATEWAY,
                    axum::Json(serde_json::json!({ "error": format!("read models: {e}") })),
                )
                    .into_response(),
            }
        }
        Err(e) => (
            StatusCode::BAD_GATEWAY,
            axum::Json(serde_json::json!({ "error": format!("venice models: {e}") })),
        )
            .into_response(),
    }
}

/// Forward a chat-completion request and stream the upstream response back.
/// `api_key` is the resolved Venice key (BYOK or backend). `body` is the
/// OpenAI-style request payload the client sent.
pub async fn chat(http: &reqwest::Client, api_key: &str, body: Value) -> Response {
    let url = format!("{VENICE_BASE}/chat/completions");
    let upstream = http
        .post(&url)
        .bearer_auth(api_key)
        .json(&body)
        .send()
        .await;

    match upstream {
        Ok(resp) => {
            let status = StatusCode::from_u16(resp.status().as_u16())
                .unwrap_or(StatusCode::BAD_GATEWAY);
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
        Err(e) => (
            StatusCode::BAD_GATEWAY,
            axum::Json(serde_json::json!({ "error": format!("venice chat: {e}") })),
        )
            .into_response(),
    }
}
