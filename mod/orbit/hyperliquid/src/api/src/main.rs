mod auth;
mod deposit;
mod hl;
mod traders;
mod vaults;
mod copytrade;
mod indexes;
mod store;
mod routes;
mod signer;
mod sign_l1;
mod sign_user;
mod actions;
mod live_engine;

use axum::Router;
use std::net::SocketAddr;
use std::sync::Arc;
use tower_http::cors::{Any, CorsLayer};
use tracing_subscriber::{layer::SubscriberExt, util::SubscriberInitExt};

#[derive(Clone)]
pub struct AppState {
    pub hl: Arc<hl::Client>,
    pub http: reqwest::Client,
    pub store: Arc<store::Store>,
    pub copy: Arc<copytrade::Engine>,
    pub progress: Arc<traders::ProgressTracker>,
    pub boards: Arc<traders::BoardCache>,
    pub signer: Arc<signer::SignerStore>,
    pub meta: Arc<actions::MetaCache>,
    pub live: Arc<live_engine::EngineRegistry>,
    pub auth: Arc<auth::AuthCfg>,
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::registry()
        .with(tracing_subscriber::EnvFilter::try_from_default_env()
            .unwrap_or_else(|_| "hyperliquid_api=info,tower_http=info".into()))
        .with(tracing_subscriber::fmt::layer())
        .init();

    let testnet = std::env::var("HYPERLIQUID_TESTNET")
        .map(|v| v.eq_ignore_ascii_case("true"))
        .unwrap_or(false);
    let port: u16 = std::env::var("PORT").ok()
        .and_then(|s| s.parse().ok()).unwrap_or(8919);

    let data_dir = std::env::var("HYPERLIQUID_DATA_DIR")
        .unwrap_or_else(|_| {
            let home = std::env::var("HOME").unwrap_or_else(|_| ".".into());
            format!("{home}/.hyperliquid")
        });
    std::fs::create_dir_all(&data_dir).ok();

    let hl = Arc::new(hl::Client::new(testnet));
    let http = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(30))
        .build()
        .expect("http client");
    let store = Arc::new(store::Store::load(&data_dir)?);
    let copy = Arc::new(copytrade::Engine::new(hl.clone(), store.clone()));
    let progress = Arc::new(traders::ProgressTracker::default());
    let boards = Arc::new(traders::BoardCache::load(&data_dir));
    let signer = Arc::new(signer::SignerStore::new());
    let meta = Arc::new(actions::MetaCache::new(hl.clone()));
    let live = Arc::new(live_engine::EngineRegistry::new(hl.clone(), signer.clone(), meta.clone()));

    // Resume any live sessions that were active before this restart.
    live.resume_persisted();

    // Existing background loops.
    let copy_bg = copy.clone();
    tokio::spawn(async move { copy_bg.run().await });

    // Board refresher: computes each standard window and publishes the result
    // into BoardCache (memory + disk). /traders/top serves from that cache, so
    // request latency is decoupled from HL's 429-throttled scan entirely.
    // Pool 600 = the widest UI option; enrichment is capped at ENRICH_CAP
    // inside top_traders, so a wide pool costs no extra /info calls.
    let prewarm_hl = hl.clone();
    let prewarm_progress = progress.clone();
    let prewarm_boards = boards.clone();
    tokio::spawn(async move {
        const PREWARM_POOL: usize = 600;
        loop {
            for days in [1u32, 7, 30] {
                let started = std::time::Instant::now();
                let r = traders::top_traders_with_progress(
                    prewarm_hl.clone(), days, 0.5, PREWARM_POOL, vec![],
                    Some(prewarm_progress.clone()),
                ).await;
                match r {
                    Ok(t) => {
                        tracing::info!(
                            "board refresh days={}: {} traders in {:?}",
                            days, t.len(), started.elapsed()
                        );
                        prewarm_boards.put(days, PREWARM_POOL, t);
                    }
                    Err(e) => tracing::warn!("board refresh days={days} failed: {e}"),
                }
            }
            tokio::time::sleep(std::time::Duration::from_secs(120)).await;
        }
    });

    let state = AppState {
        hl, http, store, copy, progress, boards, signer, meta, live,
        auth: auth::AuthCfg::from_env(),
    };

    let cors = CorsLayer::new()
        .allow_origin(Any)
        .allow_methods(Any)
        .allow_headers(Any);

    let app = Router::new()
        .merge(routes::router())
        .with_state(state.clone())
        // Sign-in guard sits inside CORS so preflights are answered openly
        // while every real request is authenticated.
        .layer(axum::middleware::from_fn_with_state(state, auth::guard))
        .layer(cors)
        .layer(tower_http::trace::TraceLayer::new_for_http());

    let addr: SocketAddr = ([0, 0, 0, 0], port).into();
    tracing::info!("hyperliquid-api listening on {addr} (testnet={testnet})");
    let listener = tokio::net::TcpListener::bind(addr).await?;
    axum::serve(listener, app).await?;
    Ok(())
}
