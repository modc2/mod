use axum::Router;
use std::sync::Arc;
use tokio::sync::RwLock;
use tower_http::cors::CorsLayer;

mod config;
mod routes;
mod store;
mod types;

pub type AppState = Arc<SharedState>;

pub struct SharedState {
    pub store: RwLock<store::Store>,
    pub config: config::Config,
    pub http: reqwest::Client,
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "multisig=info,tower_http=info".into()),
        )
        .init();

    let config = config::Config::load()?;
    let host = config.host.clone();
    let port = config.port;
    let store = store::Store::load(&config.data_dir)?;

    let state: AppState = Arc::new(SharedState {
        store: RwLock::new(store),
        config,
        http: reqwest::Client::new(),
    });

    let app = Router::new()
        .merge(routes::common::router())
        .merge(routes::evm::router())
        .merge(routes::substrate::router())
        .merge(routes::solana::router())
        .layer(CorsLayer::permissive())
        .with_state(state);

    let addr = format!("{host}:{port}");
    tracing::info!("Multisig API listening on {addr}");
    let listener = tokio::net::TcpListener::bind(&addr).await?;
    axum::serve(listener, app).await?;

    Ok(())
}
