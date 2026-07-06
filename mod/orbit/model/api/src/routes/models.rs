use crate::{extract_key, AppState};
use axum::{
    extract::{Query, State},
    http::{HeaderMap, StatusCode},
    Json,
};
use serde::{Deserialize, Serialize};
use std::sync::Arc;

#[derive(Deserialize)]
pub struct ListParams { pub provider: String }

#[derive(Serialize)]
pub struct Model { pub id: String, pub context_length: Option<u64> }

#[derive(Serialize)]
pub struct ListResponse { pub provider: String, pub count: usize, pub models: Vec<Model> }

pub async fn list(
    State(s): State<Arc<AppState>>,
    headers: HeaderMap,
    Query(p): Query<ListParams>,
) -> Result<Json<ListResponse>, (StatusCode, String)> {
    let cfg = s.providers.get(p.provider.as_str())
        .ok_or((StatusCode::BAD_REQUEST, format!("unknown provider '{}'", p.provider)))?;
    let key = extract_key(&headers)?;

    let url = format!("{}/models", cfg.url.trim_end_matches('/'));
    let resp = s.http.get(&url)
        .bearer_auth(&key)
        .send().await
        .map_err(|e| (StatusCode::BAD_GATEWAY, format!("{}/models failed: {e}", p.provider)))?;
    let status = resp.status();
    if !status.is_success() {
        let body = resp.text().await.unwrap_or_default();
        return Err((status, format!("{}: {}", p.provider, body.chars().take(400).collect::<String>())));
    }
    let v: serde_json::Value = resp.json().await
        .map_err(|e| (StatusCode::BAD_GATEWAY, format!("decode: {e}")))?;
    let arr = v.get("data").or_else(|| v.get("models")).cloned().unwrap_or(serde_json::Value::Array(vec![]));

    let mut models: Vec<Model> = vec![];
    if let serde_json::Value::Array(items) = arr {
        for m in items {
            if let Some(id) = m.as_str() {
                models.push(Model { id: id.to_string(), context_length: None });
            } else if let Some(obj) = m.as_object() {
                let id = obj.get("id").or_else(|| obj.get("name"))
                    .and_then(|v| v.as_str()).unwrap_or("").to_string();
                if id.is_empty() { continue; }
                let ctx = obj.get("context_length").or_else(|| obj.get("max_model_len"))
                    .and_then(|v| v.as_u64());
                models.push(Model { id, context_length: ctx });
            }
        }
    }
    Ok(Json(ListResponse { provider: p.provider, count: models.len(), models }))
}
