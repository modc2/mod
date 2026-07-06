use axum::{http::HeaderValue, Router};
use bridge_api::{config, routes, state::AppState};
use std::{net::SocketAddr, sync::Arc};
use tower_http::{cors::CorsLayer, limit::RequestBodyLimitLayer, trace::TraceLayer};

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "bridge_api=info,tower_http=info".into()),
        )
        .init();

    let cfg = config::Config::load()?;
    let port = cfg.port;
    let cors = build_cors(&cfg);

    let state: AppState = Arc::new(bridge_api::state::SharedState::new(cfg)?);

    let app = Router::new()
        .merge(routes::router())
        // Hard cap on request body — claim/commit payloads are tiny (~500 bytes max).
        .layer(RequestBodyLimitLayer::new(8 * 1024))
        .layer(TraceLayer::new_for_http())
        .layer(cors)
        .with_state(state);

    let addr: SocketAddr = format!("0.0.0.0:{port}").parse()?;
    tracing::info!("Bridge API (Rust) listening on {addr}");
    let listener = tokio::net::TcpListener::bind(addr).await?;
    axum::serve(
        listener,
        app.into_make_service_with_connect_info::<SocketAddr>(),
    )
    .await?;

    Ok(())
}

fn build_cors(cfg: &config::Config) -> CorsLayer {
    use axum::http::Method;
    let origins: Vec<HeaderValue> = cfg
        .cors_origins
        .iter()
        .filter_map(|o| HeaderValue::from_str(o).ok())
        .collect();

    CorsLayer::new()
        .allow_origin(origins)
        .allow_methods([Method::GET, Method::POST, Method::OPTIONS])
        .allow_headers([axum::http::header::CONTENT_TYPE])
        .allow_credentials(true)
}
