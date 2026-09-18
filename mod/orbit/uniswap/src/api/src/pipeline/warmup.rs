use std::sync::Arc;
use tokio::sync::mpsc;

use crate::config::WARMUP_COMBOS;
use crate::state::AppState;

/// Background warmup: pre-compute common chain/days combos.
///
/// Off unless `UNISWAP_WARMUP=1`. Warming all nine combinations fires several
/// hundred requests at free public RPC endpoints the moment the process
/// starts, which on a fleet that sleeps and wakes modules on demand means
/// every wake spends its first minutes rate-limiting itself out of the
/// endpoints the request that woke it is about to need.
pub async fn run(state: Arc<AppState>) {
    let enabled = std::env::var("UNISWAP_WARMUP")
        .map(|v| v == "1" || v.eq_ignore_ascii_case("true"))
        .unwrap_or(false);

    if !enabled {
        tracing::info!("warmup disabled (set UNISWAP_WARMUP=1 to pre-scrape on boot)");
        return;
    }

    tokio::time::sleep(std::time::Duration::from_secs(5)).await;

    loop {
        for &(chain, days) in WARMUP_COMBOS {
            let cache_key = AppState::cache_key(chain.name(), days, 2000);
            if state.get_cached(&cache_key).is_some() {
                continue;
            }

            tracing::info!("Warmup: scraping {} {}d", chain.name(), days);

            let (tx, mut rx) = mpsc::channel(100);
            let state_clone = state.clone();
            tokio::spawn(async move {
                super::run_pipeline(state_clone, chain, days, 2000, 5, tx).await;
            });

            while rx.recv().await.is_some() {}
            tokio::time::sleep(std::time::Duration::from_secs(2)).await;
        }

        tokio::time::sleep(std::time::Duration::from_secs(3600)).await;
    }
}
