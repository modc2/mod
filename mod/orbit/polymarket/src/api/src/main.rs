use std::net::SocketAddr;
use std::sync::Arc;

use axum::Router;
use tower_http::compression::predicate::{NotForContentType, Predicate, SizeAbove};
use tower_http::compression::CompressionLayer;
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

    // 128 entries: memory is a hot tier only — persistent endpoints (trader
    // activity/positions/history) reload from disk on eviction, so a small
    // cap costs no upstream refetches, just keeps big JSON bodies off-heap.
    let proxy_cache = Arc::new(ProxyCache::new(128));
    let pipeline = Arc::new(PipelineState::new(http.clone()));

    let strat_store = Arc::new(StratStore::new());
    let signer_store = Arc::new(polymarket_api::SignerStore::new());
    let engines = Arc::new(polymarket_api::EngineRegistry::new(
        http.clone(),
        signer_store.clone(),
    ));
    // Resume any live sessions that were running before the previous restart.
    // resume_persisted scans the persist dir and re-spawns tokio tasks for
    // every <eoa>.config.json present. An explicit STOP KEEPS its file (so the
    // console can still read that strat's ledger) but marks it `stopped: true`,
    // and resume skips those.
    engines.resume_persisted();

    // Scheduled "flatten everything" — sell every held position on a fixed
    // cadence. OFF unless POLYMARKET_LIQUIDATE_EVERY_HOURS is set to a positive
    // number of hours: a pass sells the deposit wallet's ENTIRE on-chain book
    // at best bid, including positions the engine never bought, so it is opt-in
    // rather than a default a stock deployment applies to a real wallet.
    // First run is one full period after boot so a process restart never
    // triggers an unexpected instant flatten, and `persisted_eoas` limits each
    // pass to wallets with a running, auto_execute session.
    let liq_engines = engines.clone();
    let liq_hours: f64 = std::env::var("POLYMARKET_LIQUIDATE_EVERY_HOURS")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(0.0);
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
                    match liq_engines.liquidate_all(&eoa, None).await {
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
        tracing::info!("scheduled liquidation disabled (set POLYMARKET_LIQUIDATE_EVERY_HOURS to enable)");
    }

    let user_strats = Arc::new(polymarket_api::UserStratStore::new());
    let share = polymarket_api::ShareStore::from_env();
    tracing::info!(backend = %share.label(), "strat share store");
    let sync = polymarket_api::SyncSchedule::from_env();
    let copy_book = polymarket_api::CopyBookStore::from_env();
    let state = AppState {
        http: http.clone(),
        proxy_cache: proxy_cache.clone(),
        pipeline: pipeline.clone(),
        strat_store,
        signer_store,
        engines,
        user_strats,
        share,
        sync: sync.clone(),
        copy_book,
    };

    // Background warmup: traders pipeline. 5-MINUTE cadence by default — the
    // freshest schedule the sweep can absorb, and deliberately aggressive: a
    // full 1D/7D/14D/30D pass over ~6k traders takes 8–10 min, so in practice
    // cycles run back-to-back and the effective cadence is the cycle duration.
    // Expect steady data-api 429s at this setting; individual traders that get
    // rate-limited drop out of a window and reappear on the next pass. Raising
    // the interval (AUTO chip / `/sync/config`) buys a quieter upstream and a
    // scheduler that actually idles. Copy responsiveness never depended on
    // this: the live engine polls the user's tracked traders separately at 60s.
    // Paired with the 1h `AGG_TTL` in cache.rs, which is now an upper bound
    // rather than a match — warmed entries are replaced long before they
    // expire. Each cycle still only re-fetches windows past
    // `resync_after_secs`, so a restart loop can't multiply the load.
    //
    // The cadence is OWNER-SETTABLE (sync.rs): `wait_for_next_run` schedules
    // start-to-start off the persisted interval — never sleep-after-work, which
    // drifted to interval + cycle duration — and wakes early when the owner
    // changes the schedule or presses SYNC NOW. Each cycle is panic-guarded so
    // one bad upstream payload can't kill the task and silently stop syncs.
    let warmup_pipeline = pipeline.clone();
    let warmup_sync = sync.clone();
    tokio::spawn(async move {
        use futures::FutureExt;
        tokio::time::sleep(std::time::Duration::from_secs(5)).await;
        loop {
            let trigger = warmup_sync.wait_for_next_run().await;
            // A manual "sync now" bypasses the freshness skip entirely (0) —
            // the owner asked for fresh data, not for a no-op cycle.
            let min_age = match trigger {
                polymarket_api::sync::Trigger::Manual => 0,
                polymarket_api::sync::Trigger::Scheduled => warmup_sync.resync_after_secs(),
            };
            warmup_sync.mark_started(trigger);
            let cycle = warmup_pipeline.warmup_cycle(min_age);
            let panicked = std::panic::AssertUnwindSafe(cycle).catch_unwind().await.is_err();
            if panicked {
                tracing::error!(
                    interval_secs = warmup_sync.interval_secs(),
                    "warmup cycle panicked; retrying on the next scheduled sync",
                );
            }
            warmup_sync.mark_finished(panicked.then(|| "cycle panicked".to_string()));
            // Hand the cycle's burst heap back to the OS. Parsing thousands
            // of trader activity pages allocates GBs that glibc otherwise
            // keeps in its arenas forever — RSS sat pinned at the ~11GB
            // high-water mark while live data (all disk-backed) was <200MB.
            #[cfg(target_os = "linux")]
            unsafe { libc::malloc_trim(0); }
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
        // Wallet-signed copy-desk actions need BOTH the access store (owner +
        // nonce HMAC) and the app state (book + engines) — merged here where
        // both exist, and still inside the guard below.
        .merge(polymarket_api::copy_actions::router(access.clone(), state.clone()))
        .merge(polymarket_api::router().with_state(state))
        .merge(polymarket_api::access::router(access.clone()))
        .layer(axum::middleware::from_fn_with_state(
            access.clone(),
            polymarket_api::access::guard,
        ))
        .layer(cors)
        // Wire-size, not server time: the leaderboard is ~150KB of JSON and
        // /live/sessions ~1MB, and every byte of it was going out raw — over
        // anything but loopback that transfer IS the page load. gzip/br take
        // those to a few percent of the size.
        //
        // The predicate is the important part. Progress streams
        // (application/x-ndjson: a cold /active-traders?stream=1 emits one
        // event per pipeline stage over ~10 minutes) must NOT be buffered
        // into a compressor, or the console's progress bar arrives all at
        // once at the end. SizeAbove(512) skips the bodies where a
        // compression frame costs more than it saves — /health, /access/check
        // and the rest of the small probes.
        .layer(
            CompressionLayer::new().gzip(true).br(true).compress_when(
                SizeAbove::new(512)
                    .and(NotForContentType::new("application/x-ndjson"))
                    .and(NotForContentType::new("text/event-stream"))
                    .and(NotForContentType::GRPC)
                    .and(NotForContentType::IMAGES),
            ),
        )
        .layer(TraceLayer::new_for_http());

    let addr: SocketAddr = ([0, 0, 0, 0], port).into();
    tracing::info!("polymarket-api listening on {}", addr);
    let listener = tokio::net::TcpListener::bind(addr).await?;
    axum::serve(listener, app).await?;
    Ok(())
}
