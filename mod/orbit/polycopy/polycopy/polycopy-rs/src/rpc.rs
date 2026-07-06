use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use dashmap::DashMap;
use ethers::providers::{Http, Middleware, Provider};
use tokio::time;
use tracing::{debug, info, warn};

struct ProviderEntry {
    url: String,
    provider: Provider<Http>,
    latency_ms: AtomicU64,
    error_count: AtomicU64,
    last_used: AtomicU64,
    is_healthy: AtomicBool,
    total_requests: AtomicU64,
}

pub struct RpcPool {
    /// chain_id -> list of provider entries
    providers: DashMap<u64, Vec<Arc<ProviderEntry>>>,
    /// Round-robin index per chain
    rr_index: DashMap<u64, AtomicU64>,
}

impl RpcPool {
    pub fn new() -> Self {
        Self {
            providers: DashMap::new(),
            rr_index: DashMap::new(),
        }
    }

    /// Add RPC endpoints for a chain
    pub fn add_chain(&self, chain_id: u64, urls: &[String]) {
        let mut entries = Vec::new();
        for url in urls {
            match Provider::<Http>::try_from(url.as_str()) {
                Ok(provider) => {
                    let provider = provider.interval(Duration::from_millis(500));
                    entries.push(Arc::new(ProviderEntry {
                        url: url.clone(),
                        provider,
                        latency_ms: AtomicU64::new(500), // default assumption
                        error_count: AtomicU64::new(0),
                        last_used: AtomicU64::new(0),
                        is_healthy: AtomicBool::new(true),
                        total_requests: AtomicU64::new(0),
                    }));
                }
                Err(e) => {
                    warn!("Failed to create provider for {}: {}", url, e);
                }
            }
        }
        info!("Chain {}: {} providers registered", chain_id, entries.len());
        self.providers.insert(chain_id, entries);
        self.rr_index.insert(chain_id, AtomicU64::new(0));
    }

    /// Get the best available provider for a chain.
    /// Uses weighted round-robin: prefers lower latency, skips unhealthy.
    pub fn get_provider(&self, chain_id: u64) -> Option<Provider<Http>> {
        let entries = self.providers.get(&chain_id)?;
        if entries.is_empty() {
            return None;
        }

        let idx_entry = self.rr_index.get(&chain_id)?;
        let len = entries.len();

        // Try up to len times to find a healthy provider
        for _ in 0..len {
            let idx = idx_entry.fetch_add(1, Ordering::Relaxed) as usize % len;
            let entry = &entries[idx];

            if !entry.is_healthy.load(Ordering::Relaxed) {
                continue;
            }

            let now = SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap_or_default()
                .as_secs();
            entry.last_used.store(now, Ordering::Relaxed);
            entry.total_requests.fetch_add(1, Ordering::Relaxed);

            return Some(entry.provider.clone());
        }

        // All unhealthy — return first one anyway as fallback
        warn!("Chain {}: all providers unhealthy, using fallback", chain_id);
        Some(entries[0].provider.clone())
    }

    /// Get provider URL (for logging)
    pub fn get_provider_url(&self, chain_id: u64) -> Option<String> {
        let entries = self.providers.get(&chain_id)?;
        let idx_entry = self.rr_index.get(&chain_id)?;
        let idx = (idx_entry.load(Ordering::Relaxed).wrapping_sub(1)) as usize % entries.len();
        Some(entries[idx].url.clone())
    }

    /// Report an error for the most recently used provider on a chain
    pub fn report_error(&self, chain_id: u64) {
        if let Some(entries) = self.providers.get(&chain_id) {
            let idx_entry = self.rr_index.get(&chain_id).unwrap();
            let idx = (idx_entry.load(Ordering::Relaxed).wrapping_sub(1)) as usize % entries.len();
            let entry = &entries[idx];
            let errs = entry.error_count.fetch_add(1, Ordering::Relaxed) + 1;
            if errs >= 5 {
                entry.is_healthy.store(false, Ordering::Relaxed);
                warn!(
                    "Chain {} provider {} marked unhealthy ({} errors)",
                    chain_id, entry.url, errs
                );
            }
        }
    }

    /// Report success + latency for the most recently used provider
    pub fn report_success(&self, chain_id: u64, latency_ms: u64) {
        if let Some(entries) = self.providers.get(&chain_id) {
            let idx_entry = self.rr_index.get(&chain_id).unwrap();
            let idx = (idx_entry.load(Ordering::Relaxed).wrapping_sub(1)) as usize % entries.len();
            let entry = &entries[idx];
            entry.error_count.store(0, Ordering::Relaxed);
            entry.is_healthy.store(true, Ordering::Relaxed);
            // Exponential moving average for latency
            let old = entry.latency_ms.load(Ordering::Relaxed);
            let new_latency = (old * 7 + latency_ms * 3) / 10;
            entry.latency_ms.store(new_latency, Ordering::Relaxed);
        }
    }

    /// Background health check: ping all providers every interval
    pub async fn health_check_loop(self: Arc<Self>, interval_secs: u64) {
        let mut interval = time::interval(Duration::from_secs(interval_secs));
        loop {
            interval.tick().await;
            for entry in self.providers.iter() {
                let chain_id = *entry.key();
                for provider_entry in entry.value().iter() {
                    let pe = provider_entry.clone();
                    let _pool = self.clone();
                    tokio::spawn(async move {
                        let start = Instant::now();
                        match pe.provider.get_block_number().await {
                            Ok(block) => {
                                let ms = start.elapsed().as_millis() as u64;
                                pe.latency_ms.store(ms, Ordering::Relaxed);
                                pe.error_count.store(0, Ordering::Relaxed);
                                let was_unhealthy = !pe.is_healthy.swap(true, Ordering::Relaxed);
                                if was_unhealthy {
                                    info!(
                                        "Chain {} provider {} recovered (block {})",
                                        chain_id, pe.url, block
                                    );
                                }
                                debug!(
                                    "Chain {} {} block={} latency={}ms",
                                    chain_id, pe.url, block, ms
                                );
                            }
                            Err(e) => {
                                let errs = pe.error_count.fetch_add(1, Ordering::Relaxed) + 1;
                                if errs >= 3 {
                                    pe.is_healthy.store(false, Ordering::Relaxed);
                                }
                                debug!(
                                    "Chain {} {} health check failed: {} (errors: {})",
                                    chain_id, pe.url, e, errs
                                );
                            }
                        }
                    });
                }
            }
        }
    }

    /// Get stats for all providers (for status reporting)
    pub fn stats(&self) -> Vec<(u64, Vec<ProviderStats>)> {
        let mut result = Vec::new();
        for entry in self.providers.iter() {
            let chain_id = *entry.key();
            let stats: Vec<ProviderStats> = entry
                .value()
                .iter()
                .map(|pe| ProviderStats {
                    url: pe.url.clone(),
                    latency_ms: pe.latency_ms.load(Ordering::Relaxed),
                    is_healthy: pe.is_healthy.load(Ordering::Relaxed),
                    error_count: pe.error_count.load(Ordering::Relaxed),
                    total_requests: pe.total_requests.load(Ordering::Relaxed),
                })
                .collect();
            result.push((chain_id, stats));
        }
        result
    }
}

#[derive(Debug, Clone, serde::Serialize)]
pub struct ProviderStats {
    pub url: String,
    pub latency_ms: u64,
    pub is_healthy: bool,
    pub error_count: u64,
    pub total_requests: u64,
}
