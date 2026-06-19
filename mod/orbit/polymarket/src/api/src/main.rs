use std::net::SocketAddr;
use std::sync::Arc;

use axum::Router;
use tower_http::cors::{Any, CorsLayer};
use tower_http::trace::TraceLayer;
use tracing_subscriber::EnvFilter;

use polymarket_api::{AppState, ProxyCache, PipelineState, StratStore};

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "polymarket_api=info,tower_http=info".into()),
        )
        .init();

    let port: u16 = std::env::var("PORT")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(50091);

    let http = reqwest::Client::builder()
        .pool_max_idle_per_host(64)
        .timeout(std::time::Duration::from_secs(30))
        .build()?;

    let proxy_cache = Arc::new(ProxyCache::new(500));
    let pipeline = Arc::new(PipelineState::new(http.clone()));

    let strat_store = Arc::new(StratStore::new());
    let signer_store = Arc::new(polymarket_api::SignerStore::new());
    let engines = Arc::new(polymarket_api::EngineRegistry::new(
        http.clone(),
        signer_store.clone(),
    ));
    // Resume any live sessions that were running before the previous restart.
    // resume_persisted scans the persist dir and re-spawns tokio tasks for
    // every <eoa>.config.json present (explicit STOP deletes those files).
    engines.resume_persisted();

    let user_strats = Arc::new(polymarket_api::UserStratStore::new());
    let state = AppState {
        http: http.clone(),
        proxy_cache: proxy_cache.clone(),
        pipeline: pipeline.clone(),
        strat_store,
        signer_store,
        engines,
        user_strats,
    };

    // Background warmup: traders pipeline. HOURLY cadence — re-warming the
    // 1D/7D/14D/30D leaderboards (~6k traders) every minute was the dominant
    // source of data-api rate-limiting (429s). The leaderboard is slow-moving
    // (30d Sharpe barely shifts minute-to-minute), so a ~1h refresh is plenty;
    // the live engine polls the user's tracked traders separately at 60s, so
    // copy responsiveness is unaffected. Paired with the 1h `AGG_TTL` in
    // cache.rs so warmed entries stay valid between cycles instead of expiring
    // after 60s and triggering on-demand refetches. Each cycle still only
    // re-fetches expired entries, so steady-state load stays proportional to
    // the candidate pool size (default 2000 traders).
    let warmup_pipeline = pipeline.clone();
    tokio::spawn(async move {
        tokio::time::sleep(std::time::Duration::from_secs(5)).await;
        loop {
            warmup_pipeline.warmup_cycle().await;
            tokio::time::sleep(std::time::Duration::from_secs(3600)).await;
        }
    });

    // Background warmup: pre-cache active markets so first page load is instant
    let warmup_http = http.clone();
    let warmup_cache = proxy_cache.clone();
    tokio::spawn(async move {
        use std::time::Duration;
        tokio::time::sleep(Duration::from_secs(2)).await;
        loop {
            let today = chrono::Utc::now().format("%Y-%m-%dT00:00:00.000Z").to_string();
            let warmup_queries = vec![
                format!("endpoint=markets&_limit=100&active=true&closed=false&order=volume&ascending=false&end_date_min={}", today),
                format!("endpoint=markets&_limit=100&active=true&closed=false&order=liquidity&ascending=false&end_date_min={}", today),
                format!("endpoint=markets&_limit=100&active=true&closed=false&order=end_date_min&ascending=false&end_date_min={}", today),
            ];
            for qs in &warmup_queries {
                let cache_key = format!("proxy:{}", qs);
                // Skip if already cached and fresh
                if let Some((_, true)) = warmup_cache.get(&cache_key, "markets") {
                    continue;
                }
                let url = format!("https://gamma-api.polymarket.com/markets?{}", qs.replace("endpoint=markets&", ""));
                match warmup_http.get(&url).header("accept", "application/json").send().await {
                    Ok(resp) if resp.status().is_success() => {
                        if let Ok(data) = resp.json::<serde_json::Value>().await {
                            let ttl = crate::ProxyCache::ttl_for_endpoint("markets");
                            warmup_cache.set(cache_key, data, ttl, "markets");
                            tracing::info!("warmed market cache");
                        }
                    }
                    _ => {}
                }
            }
            tokio::time::sleep(Duration::from_secs(80)).await; // refresh before 90s TTL
        }
    });

    let cors = CorsLayer::new()
        .allow_origin(Any)
        .allow_methods(Any)
        .allow_headers(Any);

    let app = Router::new()
        .merge(polymarket_api::router())
        .with_state(state)
        .layer(cors)
        .layer(TraceLayer::new_for_http());

    let addr: SocketAddr = ([0, 0, 0, 0], port).into();
    tracing::info!("polymarket-api listening on {}", addr);
    let listener = tokio::net::TcpListener::bind(addr).await?;
    axum::serve(listener, app).await?;
    Ok(())
}
