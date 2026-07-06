use std::net::SocketAddr;
use std::sync::Arc;

use axum::http::{header, Method};
use axum::Router;
use tower_http::cors::{Any, CorsLayer};
use tower_http::trace::TraceLayer;
use tracing_subscriber::EnvFilter;

use venice_api::keystore::KeyStore;
use venice_api::media::MediaStore;
use venice_api::x402::X402Config;
use venice_api::AppState;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "venice_api=info,tower_http=info".into()),
        )
        .init();

    let port: u16 = std::env::var("PORT")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(50880);

    let http = reqwest::Client::builder()
        .pool_max_idle_per_host(32)
        .timeout(std::time::Duration::from_secs(600)) // long-running streamed completions
        .build()?;

    // Backend key funds the paid (non-BYOK) path. Without it, only BYOK works.
    let backend_key = std::env::var("VENICE_API_KEY")
        .ok()
        .filter(|s| !s.trim().is_empty());

    let x402 = Arc::new(X402Config::from_env());
    // Token freshness window (seconds). 7 days mirrors the store gateway.
    let max_age: u64 = std::env::var("VENICE_SESSION_TTL")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(86_400 * 7);

    if backend_key.is_some() && x402.enabled {
        tracing::info!(
            "paid path ENABLED: {} {} on {} → receiver {}",
            x402.price_display, "USDC", x402.network, x402.receiver
        );
    } else if backend_key.is_some() {
        tracing::info!("backend key present but x402 receiver unset → paid path DISABLED (BYOK only)");
    } else {
        tracing::info!("no VENICE_API_KEY → paid path DISABLED (BYOK only)");
    }

    let state = AppState {
        http,
        keys: Arc::new(KeyStore::new()),
        media: Arc::new(MediaStore::new()),
        x402,
        backend_key,
        max_age,
    };

    // CORS: allow the browser to send Authorization + X-PAYMENT and read the
    // settlement receipt back out of X-PAYMENT-RESPONSE.
    let cors = CorsLayer::new()
        .allow_origin(Any)
        .allow_methods([Method::GET, Method::POST, Method::DELETE, Method::OPTIONS])
        .allow_headers([
            header::CONTENT_TYPE,
            header::AUTHORIZATION,
            header::HeaderName::from_static("x-payment"),
        ])
        .expose_headers([header::HeaderName::from_static("x-payment-response")]);

    let app = Router::new()
        .merge(venice_api::router())
        .with_state(state)
        .layer(cors)
        .layer(TraceLayer::new_for_http());

    let addr: SocketAddr = ([0, 0, 0, 0], port).into();
    tracing::info!("venice-api listening on {}", addr);
    let listener = tokio::net::TcpListener::bind(addr).await?;
    axum::serve(listener, app).await?;
    Ok(())
}
