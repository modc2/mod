pub mod abi;
pub mod config;
pub mod executor;
pub mod monitor;
pub mod rpc;
pub mod scorer;
pub mod types;

use std::sync::Arc;

use dashmap::DashMap;
use ethers::types::Address;
use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;
use tokio::sync::broadcast;
use tracing::info;

use crate::config::default_chains;
use crate::executor::TradeExecutor;
use crate::monitor::SwapMonitor;
use crate::rpc::RpcPool;
use crate::scorer::TraderScorer;
use crate::types::EngineConfig;

#[pyclass]
pub struct PolycopyEngine {
    runtime: tokio::runtime::Runtime,
    rpc: Arc<RpcPool>,
    monitor: Option<Arc<SwapMonitor>>,
    scorer: Arc<TraderScorer>,
    executor: Arc<TradeExecutor>,
    config: EngineConfig,
    watched_wallets: Arc<DashMap<Address, String>>,
    event_tx: broadcast::Sender<types::SwapEvent>,
    running: Arc<std::sync::atomic::AtomicBool>,
}

#[pymethods]
impl PolycopyEngine {
    #[new]
    fn new(config_json: &str) -> PyResult<Self> {
        // Initialize tracing
        let _ = tracing_subscriber::fmt()
            .with_env_filter(
                tracing_subscriber::EnvFilter::from_default_env()
                    .add_directive("polycopy_rs=info".parse().unwrap()),
            )
            .try_init();

        let config: EngineConfig = if config_json.is_empty() || config_json == "{}" {
            let mut cfg = EngineConfig::default();
            cfg.chains = default_chains()
                .into_iter()
                .map(|c| types::ChainConfig {
                    chain_id: c.chain_id,
                    name: c.name,
                    enabled: c.enabled,
                    rpc_urls: c.rpc_urls,
                    routers: c.routers,
                    proxy_address: c.proxy_address,
                })
                .collect();
            cfg
        } else {
            serde_json::from_str(config_json)
                .map_err(|e| PyRuntimeError::new_err(format!("Invalid config: {}", e)))?
        };

        let runtime = tokio::runtime::Builder::new_multi_thread()
            .enable_all()
            .worker_threads(4)
            .build()
            .map_err(|e| PyRuntimeError::new_err(format!("Runtime error: {}", e)))?;

        // Initialize RPC pool
        let rpc = Arc::new(RpcPool::new());
        for chain in &config.chains {
            if chain.enabled {
                rpc.add_chain(chain.chain_id, &chain.rpc_urls);
            }
        }

        // Initialize watched wallets
        let watched_wallets: Arc<DashMap<Address, String>> = Arc::new(DashMap::new());
        for wallet in &config.wallets {
            if let Ok(addr) = wallet.parse::<Address>() {
                watched_wallets.insert(addr, wallet.clone());
            }
        }

        // Broadcast channel for swap events
        let (event_tx, _) = broadcast::channel(1024);

        // Scorer
        let scorer = Arc::new(TraderScorer::new());

        // Executor
        let executor = Arc::new(TradeExecutor::new(rpc.clone(), config.clone()));

        // Set proxy addresses from config
        for chain in &config.chains {
            if let Some(ref proxy) = chain.proxy_address {
                executor.set_proxy(chain.chain_id, proxy);
            }
        }

        info!("PolycopyEngine initialized ({} chains, {} wallets)",
            config.chains.iter().filter(|c| c.enabled).count(),
            config.wallets.len()
        );

        Ok(Self {
            runtime,
            rpc,
            monitor: None,
            scorer,
            executor,
            config,
            watched_wallets,
            event_tx,
            running: Arc::new(std::sync::atomic::AtomicBool::new(false)),
        })
    }

    /// Start the monitoring and trading engine
    fn start(&mut self) -> PyResult<()> {
        if self.running.load(std::sync::atomic::Ordering::Relaxed) {
            return Err(PyRuntimeError::new_err("Already running"));
        }
        self.running
            .store(true, std::sync::atomic::Ordering::Relaxed);

        // Create monitor
        let monitor = Arc::new(SwapMonitor::new(
            self.rpc.clone(),
            self.watched_wallets.clone(),
            self.event_tx.clone(),
        ));
        self.monitor = Some(monitor.clone());

        // Start health check loop
        let rpc_clone = self.rpc.clone();
        self.runtime.spawn(async move {
            rpc_clone.health_check_loop(30).await;
        });

        // Start monitor for each enabled chain
        let poll_interval = self.config.poll_interval_ms;
        for chain in &self.config.chains {
            if chain.enabled {
                let mon = monitor.clone();
                let chain_id = chain.chain_id;
                self.runtime.spawn(async move {
                    mon.poll_chain(chain_id, poll_interval).await;
                });
            }
        }

        // Start scorer listener
        let scorer = self.scorer.clone();
        let scorer_rx = self.event_tx.subscribe();
        self.runtime.spawn(async move {
            scorer.listen(scorer_rx).await;
        });

        // Start executor listener (only if private key is set)
        if self.config.private_key.is_some() {
            let executor = self.executor.clone();
            let executor_rx = self.event_tx.subscribe();
            self.runtime.spawn(async move {
                executor.listen(executor_rx).await;
            });
            info!("Auto-execution enabled");
        } else {
            info!("Monitor-only mode (no private key set)");
        }

        info!("PolycopyEngine started");
        Ok(())
    }

    /// Stop the engine
    fn stop(&mut self) -> PyResult<()> {
        self.running
            .store(false, std::sync::atomic::Ordering::Relaxed);
        self.monitor = None;
        info!("PolycopyEngine stopped");
        Ok(())
    }

    /// Add a wallet to watch
    fn add_wallet(&self, address: &str, label: Option<&str>) -> PyResult<()> {
        let addr: Address = address
            .parse()
            .map_err(|_| PyRuntimeError::new_err("Invalid address"))?;
        let label = label.unwrap_or(address).to_string();
        self.watched_wallets.insert(addr, label.clone());
        if let Some(ref monitor) = self.monitor {
            monitor.watch(addr, label);
        }
        Ok(())
    }

    /// Remove a wallet from watch list
    fn remove_wallet(&self, address: &str) -> PyResult<()> {
        let addr: Address = address
            .parse()
            .map_err(|_| PyRuntimeError::new_err("Invalid address"))?;
        self.watched_wallets.remove(&addr);
        if let Some(ref monitor) = self.monitor {
            monitor.unwatch(&addr);
        }
        Ok(())
    }

    /// Get all watched wallets
    fn get_wallets(&self) -> PyResult<String> {
        let wallets: Vec<(String, String)> = self
            .watched_wallets
            .iter()
            .map(|entry| (format!("{:?}", entry.key()), entry.value().clone()))
            .collect();
        serde_json::to_string(&wallets)
            .map_err(|e| PyRuntimeError::new_err(format!("Serialize error: {}", e)))
    }

    /// Get trader scores as JSON
    fn get_scores(&self) -> PyResult<String> {
        let scores = self.scorer.get_scores();
        serde_json::to_string(&scores)
            .map_err(|e| PyRuntimeError::new_err(format!("Serialize error: {}", e)))
    }

    /// Get recent trades as JSON
    fn get_trades(&self, limit: usize) -> PyResult<String> {
        let trades = self.scorer.get_all_recent_trades(limit);
        serde_json::to_string(&trades)
            .map_err(|e| PyRuntimeError::new_err(format!("Serialize error: {}", e)))
    }

    /// Set proxy contract address for a chain
    fn set_proxy_address(&self, chain_id: u64, address: &str) -> PyResult<()> {
        self.executor.set_proxy(chain_id, address);
        Ok(())
    }

    /// Manually execute a copy trade
    fn execute_trade(&self, trade_json: &str) -> PyResult<String> {
        let event: types::SwapEvent = serde_json::from_str(trade_json)
            .map_err(|e| PyRuntimeError::new_err(format!("Invalid trade: {}", e)))?;

        let executor = self.executor.clone();
        let result = self
            .runtime
            .block_on(async move { executor.copy_trade(&event).await });

        match result {
            Ok(hash) => Ok(hash),
            Err(e) => Err(PyRuntimeError::new_err(e)),
        }
    }

    /// Pause trading on a chain
    fn pause(&self, chain_id: u64) -> PyResult<String> {
        let executor = self.executor.clone();
        let result = self
            .runtime
            .block_on(async move { executor.pause_chain(chain_id).await });
        match result {
            Ok(hash) => Ok(hash),
            Err(e) => Err(PyRuntimeError::new_err(e)),
        }
    }

    /// Unpause trading on a chain
    fn unpause(&self, chain_id: u64) -> PyResult<String> {
        let executor = self.executor.clone();
        let result = self
            .runtime
            .block_on(async move { executor.unpause_chain(chain_id).await });
        match result {
            Ok(hash) => Ok(hash),
            Err(e) => Err(PyRuntimeError::new_err(e)),
        }
    }

    /// Get RPC pool stats as JSON
    fn get_rpc_stats(&self) -> PyResult<String> {
        let stats = self.rpc.stats();
        serde_json::to_string(&stats)
            .map_err(|e| PyRuntimeError::new_err(format!("Serialize error: {}", e)))
    }

    /// Check if engine is running
    fn is_running(&self) -> bool {
        self.running.load(std::sync::atomic::Ordering::Relaxed)
    }
}

/// Python module definition
#[pymodule]
fn polycopy_rs(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<PolycopyEngine>()?;
    Ok(())
}
