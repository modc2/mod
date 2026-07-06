use std::net::SocketAddr;
use std::sync::Arc;

use axum::Router;
use tower_http::cors::{Any, CorsLayer};
use tower_http::trace::TraceLayer;
use tracing_subscriber::EnvFilter;

use taox_api::{AppState, Config, OrderStore, RatesCache};

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "taox_api=info,tower_http=info".into()),
        )
        .init();

    let config = Arc::new(Config::load()?);

    let port: u16 = std::env::var("TAOX_PORT")
        .ok()
        .and_then(|s| s.parse().ok())
        .or_else(|| std::env::var("PORT").ok().and_then(|s| s.parse().ok()))
        .unwrap_or(config.port);

    let http = reqwest::Client::builder()
        .user_agent("taox/0.1")
        .timeout(std::time::Duration::from_secs(15))
        .build()?;

    let store = Arc::new(OrderStore::open(&config.store_dir)?);
    let rates = Arc::new(RatesCache::new(config.clone(), &config.store_dir));

    let state = AppState {
        config: config.clone(),
        http,
        orders: store,
        rates,
    };

    let cors = CorsLayer::new()
        .allow_origin(Any)
        .allow_methods(Any)
        .allow_headers(Any);

    let app = Router::new()
        .merge(taox_api::router())
        .with_state(state)
        .layer(cors)
        .layer(TraceLayer::new_for_http());

    let addr: SocketAddr = ([0, 0, 0, 0], port).into();
    tracing::info!("taox-api listening on {}", addr);
    let listener = tokio::net::TcpListener::bind(addr).await?;
    axum::serve(listener, app).await?;
    Ok(())
}
