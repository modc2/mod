use std::net::SocketAddr;
use std::path::PathBuf;
use std::sync::Arc;

use axum::Router;
use tower_http::cors::{Any, CorsLayer};
use tower_http::trace::TraceLayer;
use tracing_subscriber::EnvFilter;

use sshville_api::{routes, store, AppState};

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "sshville_api=info,tower_http=info".into()),
        )
        .init();

    let port: u16 = std::env::var("SSHVILLE_PORT")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(50180);

    let store_path: PathBuf = std::env::var("SSHVILLE_STORE_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|_| {
            let home = std::env::var("HOME").unwrap_or_else(|_| ".".into());
            PathBuf::from(home).join(".sshville")
        });
    std::fs::create_dir_all(&store_path).ok();

    let state = AppState {
        store: Arc::new(store::Store::open(store_path.join("connections.json"))?),
        challenge: std::env::var("SSHVILLE_CHALLENGE")
            .unwrap_or_else(|_| "sshville-auth-v1".to_string()),
    };

    let cors = CorsLayer::new()
        .allow_origin(Any)
        .allow_methods(Any)
        .allow_headers(Any);

    let app = Router::new()
        .nest("/", routes::router())
        .nest("/api/sshville", routes::router())
        .with_state(state)
        .layer(cors)
        .layer(TraceLayer::new_for_http());

    let addr = SocketAddr::from(([0, 0, 0, 0], port));
    tracing::info!("sshville listening on {addr}");
    let listener = tokio::net::TcpListener::bind(addr).await?;
    axum::serve(listener, app).await?;
    Ok(())
}
