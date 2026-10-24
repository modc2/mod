use crate::AppState;
use axum::{extract::State, Json};
use serde::Serialize;
use std::sync::Arc;

#[derive(Serialize)]
pub struct ProviderInfo {
    pub id: String,
    pub label: String,
    pub url: String,
    pub default_model: String,
    pub key_hint: String,
    pub docs: String,
}

pub async fn list(State(s): State<Arc<AppState>>) -> Json<Vec<ProviderInfo>> {
    let mut out: Vec<_> = s.providers.iter().map(|(id, cfg)| ProviderInfo {
        id: id.to_string(),
        label: cfg.label.clone(),
        url: cfg.url.clone(),
        default_model: cfg.default_model.clone(),
        key_hint: cfg.key_hint.clone(),
        docs: cfg.docs.clone(),
    }).collect();
    // Stable order matters for the UI.
    out.sort_by(|a, b| a.id.cmp(&b.id));
    Json(out)
}
