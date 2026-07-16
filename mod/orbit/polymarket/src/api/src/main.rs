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
        // Public Polygon RPCs + Polymarket data-api now reject requests that
        // carry no User-Agent (reqwest sends none by default) with 401/403.
        // That surfaced as phantom $0 balances and "0 trades observed" — a
        // browser-like UA is all they want. Keep this or on-chain reads and
        // trader polling silently break again.
        .user_agent("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
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

    // Scheduled "flatten everything" — sell every held position on a fixed
    // cadence. The interval is a PARAMETER via POLYMARKET_LIQUIDATE_EVERY_HOURS
    // (default 6h; 0 disables). First run is one full period after boot so a
    // process restart never triggers an unexpected instant flatten. Each pass
    // iterates every persisted session and sells its deposit-wallet positions.
    let liq_engines = engines.clone();
    let liq_hours: f64 = std::env::var("POLYMARKET_LIQUIDATE_EVERY_HOURS")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(6.0);
    if liq_hours > 0.0 {
        let period = std::time::Duration::from_secs_f64(liq_hours * 3600.0);
        tracing::info!(hours = liq_hours, "scheduled liquidation enabled");
        tokio::spawn(async move {
            loop {
                tokio::time::sleep(period).await;
                for eoa in liq_engines.persisted_eoas() {
                    // Redeem settled winnings FIRST — a SELL can't cash out a
                    // resolved market (no order book), so without this the
                    // proceeds of every settled position would be stranded as
                    // un-sellable CTF tokens. Then flatten whatever live
                    // positions remain.
                    match liq_engines.redeem_all(&eoa).await {
                        Ok(r) => tracing::info!(
                            eoa = %eoa, conditions = r.conditions,
                            value = r.value_redeemed, skipped = r.skipped,
                            "scheduled redemption done",
                        ),
                        Err(e) => tracing::warn!(eoa = %eoa, error = %e, "scheduled redemption errored"),
                    }
                    match liq_engines.liquidate_all(&eoa).await {
                        Ok(r) => tracing::info!(
                            eoa = %eoa, positions = r.positions, placed = r.placed,
                            skipped = r.skipped, failed = r.failed,
                            "scheduled liquidation done",
                        ),
                        Err(e) => tracing::warn!(eoa = %eoa, error = %e, "scheduled liquidation errored"),
                    }
                }
            }
        });
    } else {
        tracing::info!("scheduled liquidation disabled (POLYMARKET_LIQUIDATE_EVERY_HOURS=0)");
    }

    let user_strats = Arc::new(polymarket_api::UserStratStore::new());
    let share = polymarket_api::ShareStore::from_env();
    tracing::info!(backend = %share.label(), "strat share store");
    let state = AppState {
        http: http.clone(),
        proxy_cache: proxy_cache.clone(),
        pipeline: pipeline.clone(),
        strat_store,
        signer_store,
        engines,
        user_strats,
        share,
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

    // Owner-only access gate + signed terms acceptance (access.rs). Applied
    // here (not inside polymarket_api::router()) so unit/integration tests
    // exercise routes without minting tokens; the deployed binary is what's
    // gated. Guard wraps EVERYTHING merged above it — the /access/* routes
    // exempt themselves inside the middleware.
    let access = polymarket_api::AccessStore::from_env();
    let app = Router::new()
        .merge(polymarket_api::router().with_state(state))
        .merge(polymarket_api::access::router(access.clone()))
        .layer(axum::middleware::from_fn_with_state(
            access.clone(),
            polymarket_api::access::guard,
        ))
        .layer(cors)
        .layer(TraceLayer::new_for_http());

    let addr: SocketAddr = ([0, 0, 0, 0], port).into();
    tracing::info!("polymarket-api listening on {}", addr);
    let listener = tokio::net::TcpListener::bind(addr).await?;
    axum::serve(listener, app).await?;
    Ok(())
}
