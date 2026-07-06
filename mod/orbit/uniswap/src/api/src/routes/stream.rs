use axum::{
    body::Body,
    extract::{Query, State},
    http::Response,
    routing::get,
    Router,
};
use futures_util::StreamExt;
use serde::Deserialize;
use std::convert::Infallible;
use std::sync::Arc;
use tokio::sync::mpsc;
use tokio_stream::wrappers::ReceiverStream;

use crate::models::chain::Chain;
use crate::pipeline::{self, PipelineEvent};
use crate::state::AppState;

#[derive(Deserialize)]
pub struct StreamParams {
    chain: Option<String>,
    days: Option<u32>,
    pool: Option<u32>,
    min_swaps: Option<u32>,
}

pub fn router() -> Router<Arc<AppState>> {
    Router::new().route("/traders/stream", get(stream_traders))
}

async fn stream_traders(
    State(state): State<Arc<AppState>>,
    Query(params): Query<StreamParams>,
) -> Response<Body> {
    let chain_str = params.chain.unwrap_or_else(|| "base".to_string());
    let chain = match Chain::from_str(&chain_str) {
        Some(c) => c,
        None => {
            let err = serde_json::json!({"type": "error", "message": "Invalid chain"});
            return Response::builder()
                .header("Content-Type", "application/x-ndjson")
                .body(Body::from(format!("{}\n", err)))
                .unwrap();
        }
    };

    let days = params.days.unwrap_or(7);
    let pool = params.pool.unwrap_or(2000);
    let min_swaps = params.min_swaps.unwrap_or(5);

    let (tx, rx) = mpsc::channel::<PipelineEvent>(100);

    // Spawn pipeline in background
    tokio::spawn(async move {
        pipeline::run_pipeline(state, chain, days, pool, min_swaps, tx).await;
    });

    // Convert channel to NDJSON stream
    let stream = ReceiverStream::new(rx).map(|event| {
        let json = serde_json::to_string(&event).unwrap_or_else(|_| "{}".to_string());
        Ok::<_, Infallible>(format!("{json}\n"))
    });

    Response::builder()
        .header("Content-Type", "application/x-ndjson")
        .header("Cache-Control", "no-store")
        .header("X-Content-Type-Options", "nosniff")
        .body(Body::from_stream(stream))
        .unwrap()
}
