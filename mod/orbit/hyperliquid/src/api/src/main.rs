mod agent;
mod auth;
mod deposit;
mod hl;
mod curve;
mod stats;
mod traders;
mod vaults;
mod copytrade;
mod indexes;
mod store;
mod mcp;
mod routes;
mod signer;
mod sign_l1;
mod sign_user;
mod actions;
mod live_engine;
mod invest;
mod invest_engine;
mod invest_routes;

use axum::Router;
use std::net::SocketAddr;
use std::sync::Arc;
use tower_http::cors::{Any, CorsLayer};
use tracing_subscriber::{layer::SubscriberExt, util::SubscriberInitExt};

/// Widest board the refresher keeps (the UI's largest pool option).
pub const PREWARM_POOL: usize = traders::ALL;
/// How far down each window's ROI board the background deepener keeps the
/// trader index warm, and how many wallets it scrapes per cycle per window.
/// Sized to stay well under HL's /info budget alongside the board refresh.
pub const DEEPEN: [(u32, usize); 3] = [(7, 400), (1, 200), (30, 200)];
pub const DEEPEN_BATCH: usize = 15;
/// How many of each ROI board's top wallets get their PnL curve fetched in
/// the background.
///
/// The board draws a curve on every card, so the first screenful is the one
/// that decides whether the page feels instant or feels like it is loading.
/// One `portfolio` call per wallet covers *every* window at once (the payload
/// carries day / week / month / allTime together, and `hl::Client` caches it
/// whole), so this is one screenful's worth of calls per board refresh, not
/// one per window per card.
pub const CURVE_PREWARM: usize = 36;

#[derive(Clone)]
pub struct AppState {
    pub hl: Arc<hl::Client>,
    pub http: reqwest::Client,
    pub store: Arc<store::Store>,
    pub copy: Arc<copytrade::Engine>,
    pub progress: Arc<traders::ProgressTracker>,
    pub boards: Arc<traders::BoardCache>,
    pub index: Arc<traders::TraderIndex>,
    /// Board scans running in the background (see `ScanJobs`).
    pub scans: Arc<traders::ScanJobs>,
    pub signer: Arc<signer::SignerStore>,
    pub meta: Arc<actions::MetaCache>,
    pub live: Arc<live_engine::EngineRegistry>,
    /// The investment book: every position a wallet has funded.
    pub invest: Arc<invest::InvestStore>,
    /// The reconciler that keeps trader sleeves aligned with their leader.
    pub engine: Arc<invest_engine::InvestEngine>,
    pub auth: Arc<auth::AuthCfg>,
    /// Where this server can reach itself — MCP tool calls loop back through
    /// the REST surface so the auth guard stays the single authority.
    pub self_url: Arc<String>,
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let port: u16 = std::env::var("PORT").ok()
        .and_then(|s| s.parse().ok()).unwrap_or(8919);
    let self_url = std::env::var("HL_SELF_URL")
        .unwrap_or_else(|_| format!("http://127.0.0.1:{port}"));

    // `--stdio` runs only the MCP stdio transport, proxying to a running API
    // (HL_API_URL / HL_SELF_URL). Checked before tracing is installed: stdout
    // belongs to the JSON-RPC stream in this mode, so logs must stay off it.
    if std::env::args().any(|a| a == "--stdio") {
        let base = std::env::var("HL_API_URL").unwrap_or(self_url);
        mcp::run_stdio(base).await;
        return Ok(());
    }

    tracing_subscriber::registry()
        .with(tracing_subscriber::EnvFilter::try_from_default_env()
            .unwrap_or_else(|_| "hyperliquid_api=info,tower_http=info".into()))
        .with(tracing_subscriber::fmt::layer())
        .init();

    let testnet = std::env::var("HYPERLIQUID_TESTNET")
        .map(|v| v.eq_ignore_ascii_case("true"))
        .unwrap_or(false);

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
    let index = Arc::new(traders::TraderIndex::load(&data_dir));
    tracing::info!("trader index: {} entries loaded", index.len());
    let signer = Arc::new(signer::SignerStore::new());
    let meta = Arc::new(actions::MetaCache::new(hl.clone()));
    let live = Arc::new(live_engine::EngineRegistry::new(hl.clone(), signer.clone(), meta.clone()));

    // The investment book and its reconciler. There is no start/stop here:
    // the book is the instruction, so a funded position resumes tracking on
    // its own after any restart.
    let invest_store = Arc::new(invest::InvestStore::load(&data_dir));
    tracing::info!("invest book: {} positions loaded", invest_store.count());
    let engine = Arc::new(invest_engine::InvestEngine::new(
        hl.clone(), http.clone(), signer.clone(), meta.clone(), invest_store.clone()));

    // Resume any live sessions that were active before this restart.
    live.resume_persisted();

    let engine_bg = engine.clone();
    tokio::spawn(async move { engine_bg.run().await });

    // Existing background loops.
    let copy_bg = copy.clone();
    tokio::spawn(async move { copy_bg.run().await });

    // Board refresher: computes each standard window and publishes the result
    // into BoardCache (memory + disk). /traders/top serves from that cache, so
    // request latency is decoupled from HL's 429-throttled scan entirely.
    // Pool = the whole gated leaderboard (every wallet the CDN prices, ~5k
    // for 24h-active); enrichment is capped at ENRICH_CAP inside top_traders,
    // so the wide pool costs no extra /info calls — only the top slice by
    // rank is ever measured from fills.
    let prewarm_hl = hl.clone();
    let prewarm_progress = progress.clone();
    let prewarm_boards = boards.clone();
    let prewarm_index = index.clone();
    tokio::spawn(async move {
        loop {
            // Standard boards first (ROI + PnL for each window, 24h-active),
            // then anything else a visitor has asked for since — those keys
            // live in the cache, so they stay fresh instead of going stale.
            let mut todo: Vec<(u32, traders::Rank, traders::Active)> = Vec::new();
            for days in [1u32, 7, 30] {
                for rank in [traders::Rank::Roi, traders::Rank::Pnl] {
                    todo.push((days, rank, traders::Active::Day));
                }
            }
            for k in prewarm_boards.keys() {
                if !todo.contains(&k) { todo.push(k); }
            }
            for (days, rank, active) in todo {
                let started = std::time::Instant::now();
                let r = traders::top_traders_with_progress(
                    prewarm_hl.clone(), prewarm_index.clone(), days, PREWARM_POOL, vec![],
                    Some(prewarm_progress.clone()), rank, active, vec![], traders::ENRICH_CAP,
                ).await;
                match r {
                    Ok(b) => {
                        tracing::info!(
                            "board refresh days={} rank={} active={}: {} traders in {:?}",
                            days, rank.as_str(), active.as_str(), b.traders.len(), started.elapsed()
                        );
                        // Warm the curves the cards on the first screen will
                        // ask for. Only the ROI board, because that is the
                        // board the page opens on, and the portfolio payload
                        // it caches serves the PnL board's overlap for free.
                        let heads: Vec<String> = if rank == traders::Rank::Roi {
                            b.traders.iter().take(CURVE_PREWARM).map(|t| t.address.clone()).collect()
                        } else { Vec::new() };
                        prewarm_boards.put(days, rank, active, PREWARM_POOL, b.traders);
                        if !heads.is_empty() {
                            let warmed = curve::trader_curves(prewarm_hl.clone(), &heads, days).await;
                            let ok = warmed.iter().filter(|c| c.available).count();
                            tracing::info!("curve prewarm days={days}: {ok}/{} available", warmed.len());
                        }
                    }
                    Err(e) => tracing::warn!("board refresh days={days} rank={} failed: {e}", rank.as_str()),
                }
            }
            // Deepen the trader index past the boards' enrichment cap, a few
            // wallets per cycle, so a coin-filtered scan ("top 50 who trade
            // ZEC") finds its answers in memory instead of walking 600
            // wallets through HL's 429s while a visitor waits.
            for (days, depth) in DEEPEN {
                let ranked = traders::ranked_addrs(
                    &prewarm_hl, days, traders::Rank::Roi, traders::Active::Day).await;
                let top: Vec<String> = ranked.into_iter().take(depth).collect();
                let now = chrono::Utc::now().timestamp_millis();
                let stale = prewarm_index.stale(days, &top, now);
                let batch: Vec<String> = stale.into_iter().take(DEEPEN_BATCH).collect();
                if batch.is_empty() { continue; }
                let started = std::time::Instant::now();
                let n = prewarm_index.enrich(&prewarm_hl, days, &batch, None, 0).await;
                tracing::info!("index deepen days={days}: {n} wallets in {:?} ({} indexed)",
                    started.elapsed(), prewarm_index.len());
            }
            tokio::time::sleep(std::time::Duration::from_secs(120)).await;
        }
    });

    let state = AppState {
        hl, http, store, copy, progress, boards, index, signer, meta, live,
        invest: invest_store, engine,
        scans: traders::ScanJobs::new(),
        auth: auth::AuthCfg::from_env(),
        self_url: Arc::new(self_url),
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
