use axum::Router;
use std::sync::Arc;
use tower_http::cors::{Any, CorsLayer};
use tracing_subscriber::EnvFilter;

mod cache;
mod config;
mod models;
mod pipeline;
mod routes;
mod state;

use state::AppState;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(EnvFilter::from_default_env().add_directive("info".parse()?))
        .init();

    let port = std::env::var("PORT")
        .unwrap_or_else(|_| "50088".to_string())
        .parse::<u16>()?;

    let state = Arc::new(AppState::new());

    // Spawn background warmup
    let warmup_state = state.clone();
    tokio::spawn(async move {
        pipeline::warmup::run(warmup_state).await;
    });

    let cors = CorsLayer::new()
        .allow_origin(Any)
        .allow_methods(Any)
        .allow_headers(Any);

    let app = Router::new()
        .merge(routes::health::router())
        .merge(routes::chains::router())
        .merge(routes::traders::router())
        .merge(routes::stream::router())
        .with_state(state)
        .layer(cors);

    let listener = tokio::net::TcpListener::bind(format!("0.0.0.0:{port}")).await?;
    tracing::info!("Uniswap trader API listening on port {port}");
    axum::serve(listener, app).await?;

    Ok(())
}
