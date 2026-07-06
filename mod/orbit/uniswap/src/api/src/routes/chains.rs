use axum::{extract::State, routing::get, Json, Router};
use serde_json::{json, Value};
use std::sync::Arc;

use crate::models::chain::Chain;
use crate::state::AppState;

pub fn router() -> Router<Arc<AppState>> {
    Router::new().route("/chains", get(list_chains))
}

async fn list_chains(State(_state): State<Arc<AppState>>) -> Json<Value> {
    let chains: Vec<Value> = Chain::all()
        .iter()
        .map(|c| {
            json!({
                "name": c.name(),
                "supported": true,
            })
        })
        .collect();

    Json(json!({ "chains": chains }))
}
