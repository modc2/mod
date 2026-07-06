use crate::{extract_key, AppState};
use axum::{
    extract::State,
    http::{HeaderMap, StatusCode},
    response::{sse::{Event, KeepAlive}, IntoResponse, Sse},
    Json,
};
use bytes::Bytes;
use futures_util::{Stream, StreamExt};
use serde::{Deserialize, Serialize};
use std::{convert::Infallible, sync::Arc};

#[derive(Deserialize, Serialize, Clone)]
pub struct ChatMessage { pub role: String, pub content: String }

#[derive(Deserialize)]
pub struct ChatRequest {
    pub provider: String,
    pub model: Option<String>,
    pub messages: Vec<ChatMessage>,
    #[serde(default = "default_temp")] pub temperature: f32,
    #[serde(default = "default_max")] pub max_tokens: u32,
    #[serde(default = "default_stream")] pub stream: bool,
}
fn default_temp() -> f32 { 1.0 }
fn default_max() -> u32 { 4096 }
fn default_stream() -> bool { true }

#[derive(Serialize)]
pub struct OneShotResponse { pub provider: String, pub model: String, pub content: String }

pub async fn chat(
    State(s): State<Arc<AppState>>,
    headers: HeaderMap,
    Json(req): Json<ChatRequest>,
) -> Result<axum::response::Response, (StatusCode, String)> {
    let cfg = s.providers.get(req.provider.as_str())
        .ok_or((StatusCode::BAD_REQUEST, format!("unknown provider '{}'", req.provider)))?;
    let key = extract_key(&headers)?;
    let model = req.model.clone().unwrap_or_else(|| cfg.default_model.to_string());

    // Build upstream request: OpenAI-compatible /chat/completions.
    let url = format!("{}/chat/completions", cfg.url.trim_end_matches('/'));
    let upstream_body = serde_json::json!({
        "model": model,
        "messages": req.messages,
        "temperature": req.temperature,
        "max_tokens": req.max_tokens,
        "stream": req.stream,
    });
    // Targon and a few others reject unknown fields; nothing extra here, so we're safe.

    if !req.stream {
        let resp = s.http.post(&url).bearer_auth(&key)
            .json(&upstream_body).send().await
            .map_err(|e| (StatusCode::BAD_GATEWAY, format!("upstream: {e}")))?;
        let status = resp.status();
        let body: serde_json::Value = resp.json().await
            .map_err(|e| (StatusCode::BAD_GATEWAY, format!("decode: {e}")))?;
        if !status.is_success() {
            return Err((status, body.to_string()));
        }
        let content = body.pointer("/choices/0/message/content")
            .and_then(|v| v.as_str()).unwrap_or("").to_string();
        return Ok(Json(OneShotResponse { provider: req.provider, model, content }).into_response());
    }

    // Streaming: open the upstream SSE and re-emit `{delta}` / `{done}` frames.
    let upstream = s.http.post(&url).bearer_auth(&key)
        .json(&upstream_body).send().await
        .map_err(|e| (StatusCode::BAD_GATEWAY, format!("upstream: {e}")))?;
    let status = upstream.status();
    if !status.is_success() {
        let body = upstream.text().await.unwrap_or_default();
        return Err((status, body.chars().take(400).collect::<String>()));
    }

    let provider = req.provider.clone();
    let model_for_done = model.clone();
    let byte_stream = upstream.bytes_stream();

    let sse_stream = parse_openai_sse(byte_stream)
        .map(move |item| -> Result<Event, Infallible> {
            match item {
                StreamItem::Delta(d) => Ok(Event::default().data(serde_json::json!({"delta": d}).to_string())),
                StreamItem::Done => Ok(Event::default().data(serde_json::json!({
                    "done": true,
                    "provider": provider,
                    "model": model_for_done,
                }).to_string())),
                StreamItem::Error(e) => Ok(Event::default().data(serde_json::json!({"error": e}).to_string())),
            }
        });

    Ok(Sse::new(sse_stream).keep_alive(KeepAlive::default()).into_response())
}

enum StreamItem { Delta(String), Done, Error(String) }

/// Convert an upstream `chat/completions` byte stream (OpenAI-format SSE) into
/// our internal `{delta}` / `{done}` / `{error}` items.
fn parse_openai_sse<S>(s: S) -> impl Stream<Item = StreamItem>
where
    S: Stream<Item = Result<Bytes, reqwest::Error>> + Send + 'static,
{
    use futures_util::stream::unfold;

    struct Acc<St> { src: St, buf: String, finished: bool }
    let acc = Acc { src: Box::pin(s), buf: String::new(), finished: false };

    unfold(acc, |mut acc| async move {
        if acc.finished { return None; }
        loop {
            // Try to emit completed frame from buffer first.
            if let Some(idx) = acc.buf.find("\n\n") {
                let frame: String = acc.buf.drain(..idx + 2).collect();
                let frame = frame.trim_end_matches('\n');
                let mut data: Option<&str> = None;
                for line in frame.split('\n') {
                    if let Some(rest) = line.strip_prefix("data:") {
                        data = Some(rest.trim_start());
                    }
                }
                let Some(d) = data else { continue; };
                if d == "[DONE]" {
                    acc.finished = true;
                    return Some((StreamItem::Done, acc));
                }
                let v: serde_json::Value = match serde_json::from_str(d) {
                    Ok(v) => v,
                    Err(_) => continue,
                };
                if let Some(content) = v.pointer("/choices/0/delta/content").and_then(|x| x.as_str()) {
                    if !content.is_empty() {
                        return Some((StreamItem::Delta(content.to_string()), acc));
                    }
                }
                // Some providers include reasoning or empty deltas; just keep looping.
                continue;
            }
            // Need more bytes.
            match acc.src.next().await {
                Some(Ok(bytes)) => {
                    if let Ok(s) = std::str::from_utf8(&bytes) {
                        acc.buf.push_str(s);
                    }
                }
                Some(Err(e)) => {
                    acc.finished = true;
                    return Some((StreamItem::Error(format!("upstream stream: {e}")), acc));
                }
                None => {
                    acc.finished = true;
                    // Flush a tail frame if buffered.
                    return Some((StreamItem::Done, acc));
                }
            }
        }
    })
}
