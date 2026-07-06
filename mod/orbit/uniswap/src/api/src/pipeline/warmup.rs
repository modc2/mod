use std::sync::Arc;
use tokio::sync::mpsc;

use crate::config::WARMUP_COMBOS;
use crate::state::AppState;

/// Background warmup: pre-compute common chain/days combos
pub async fn run(state: Arc<AppState>) {
    // Initial delay to let server start
    tokio::time::sleep(std::time::Duration::from_secs(5)).await;

    loop {
        for &(chain, days) in WARMUP_COMBOS {
            let cache_key = AppState::cache_key(chain.name(), days, 2000);

            // Skip if already cached
            if state.get_cached(&cache_key).is_some() {
                continue;
            }

            tracing::info!("Warmup: scraping {} {}d", chain.name(), days);

            let (tx, mut rx) = mpsc::channel(100);
            let state_clone = state.clone();

            tokio::spawn(async move {
                super::run_pipeline(state_clone, chain, days, 2000, 5, tx).await;
            });

            // Drain the channel (we just want it cached)
            while rx.recv().await.is_some() {}

            // Small delay between warmup runs
            tokio::time::sleep(std::time::Duration::from_secs(2)).await;
        }

        // Re-run warmup every hour
        tokio::time::sleep(std::time::Duration::from_secs(3600)).await;
    }
}
