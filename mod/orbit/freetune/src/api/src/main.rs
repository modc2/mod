use std::net::SocketAddr;
use std::sync::Arc;

use tower_http::cors::{Any, CorsLayer};
use tower_http::trace::TraceLayer;
use tracing_subscriber::EnvFilter;

use freetune_api::{models, AppState};
use freetune_api::infer::InferPool;
use freetune_api::jobs::JobManager;
use freetune_api::metrics::Metrics;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "freetune_api=info,tower_http=info".into()),
        )
        .init();

    let port: u16 = std::env::var("PORT")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(50210);

    let trainer_dir = freetune_api::trainer_dir();
    std::fs::create_dir_all(freetune_api::state_dir()).ok();
    tracing::info!(trainer_dir = %trainer_dir, state_dir = %freetune_api::state_dir(), "freetune boot");

    let metrics = Metrics::new();
    // Background CPU/RAM sampler — every 2s into a rolling buffer.
    let m = metrics.clone();
    tokio::spawn(async move {
        let mut tick = tokio::time::interval(std::time::Duration::from_secs(2));
        loop {
            tick.tick().await;
            m.sample();
        }
    });

    let state = AppState {
        jobs: Arc::new(JobManager::new(trainer_dir.clone())),
        infer: Arc::new(InferPool::new(trainer_dir.clone())),
        metrics,
        models: Arc::new(models::load(&trainer_dir)),
        trainer_dir,
    };

    let cors = CorsLayer::new()
        .allow_origin(Any)
        .allow_methods(Any)
        .allow_headers(Any);

    let app = freetune_api::router()
        .with_state(state)
        .layer(cors)
        .layer(TraceLayer::new_for_http());

    let addr: SocketAddr = ([0, 0, 0, 0], port).into();
    tracing::info!("freetune-api listening on {}", addr);
    let listener = tokio::net::TcpListener::bind(addr).await?;
    axum::serve(listener, app).await?;
    Ok(())
}
