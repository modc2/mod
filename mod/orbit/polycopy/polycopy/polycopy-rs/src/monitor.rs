use std::sync::Arc;
use std::time::{Duration, Instant};

use dashmap::DashMap;
use ethers::prelude::*;
use ethers::types::{Address, Filter, Log, H256, U256};
use tokio::sync::broadcast;
use tokio::time;
use tracing::{debug, info, warn};

use crate::config::{SWAP_TOPIC_V2, SWAP_TOPIC_V3};
use crate::rpc::RpcPool;
use crate::types::SwapEvent;

pub struct SwapMonitor {
    rpc: Arc<RpcPool>,
    watched_wallets: Arc<DashMap<Address, String>>, // address -> label
    event_tx: broadcast::Sender<SwapEvent>,
    /// Last scanned block per chain
    last_block: DashMap<u64, u64>,
}

impl SwapMonitor {
    pub fn new(
        rpc: Arc<RpcPool>,
        watched_wallets: Arc<DashMap<Address, String>>,
        event_tx: broadcast::Sender<SwapEvent>,
    ) -> Self {
        Self {
            rpc,
            watched_wallets,
            event_tx,
            last_block: DashMap::new(),
        }
    }

    /// Add a wallet to watch
    pub fn watch(&self, address: Address, label: String) {
        info!("Watching wallet: {:?} ({})", address, label);
        self.watched_wallets.insert(address, label);
    }

    /// Remove a wallet
    pub fn unwatch(&self, address: &Address) {
        self.watched_wallets.remove(address);
        info!("Unwatched wallet: {:?}", address);
    }

    /// Poll loop for a single chain. Polls eth_getLogs for Swap events.
    pub async fn poll_chain(self: Arc<Self>, chain_id: u64, poll_interval_ms: u64) {
        let mut interval = time::interval(Duration::from_millis(poll_interval_ms));
        info!("Starting swap monitor for chain {}", chain_id);

        loop {
            interval.tick().await;

            let provider = match self.rpc.get_provider(chain_id) {
                Some(p) => p,
                None => {
                    warn!("No provider for chain {}", chain_id);
                    continue;
                }
            };

            // Get current block
            let start = Instant::now();
            let current_block = match provider.get_block_number().await {
                Ok(b) => b.as_u64(),
                Err(e) => {
                    self.rpc.report_error(chain_id);
                    debug!("Chain {} get_block_number failed: {}", chain_id, e);
                    continue;
                }
            };
            self.rpc
                .report_success(chain_id, start.elapsed().as_millis() as u64);

            // Determine scan range
            let from_block = self
                .last_block
                .get(&chain_id)
                .map(|v| *v + 1)
                .unwrap_or(current_block.saturating_sub(2));

            if from_block > current_block {
                continue;
            }

            // Cap range to 100 blocks max to avoid huge responses
            let to_block = current_block.min(from_block + 100);

            // Build filter for V2 and V3 swap events
            let v3_topic: H256 = SWAP_TOPIC_V3.parse().unwrap_or_default();
            let v2_topic: H256 = SWAP_TOPIC_V2.parse().unwrap_or_default();

            // Query V3 swaps
            let filter_v3 = Filter::new()
                .from_block(from_block)
                .to_block(to_block)
                .topic0(v3_topic);

            let start = Instant::now();
            match provider.get_logs(&filter_v3).await {
                Ok(logs) => {
                    self.rpc
                        .report_success(chain_id, start.elapsed().as_millis() as u64);
                    for log in logs {
                        self.process_v3_log(chain_id, &log).await;
                    }
                }
                Err(e) => {
                    self.rpc.report_error(chain_id);
                    debug!("Chain {} V3 getLogs failed: {}", chain_id, e);
                }
            }

            // Query V2 swaps
            let filter_v2 = Filter::new()
                .from_block(from_block)
                .to_block(to_block)
                .topic0(v2_topic);

            let start = Instant::now();
            match provider.get_logs(&filter_v2).await {
                Ok(logs) => {
                    self.rpc
                        .report_success(chain_id, start.elapsed().as_millis() as u64);
                    for log in logs {
                        self.process_v2_log(chain_id, &log).await;
                    }
                }
                Err(e) => {
                    self.rpc.report_error(chain_id);
                    debug!("Chain {} V2 getLogs failed: {}", chain_id, e);
                }
            }

            self.last_block.insert(chain_id, to_block);
        }
    }

    /// Process a Uniswap V3 Swap log
    async fn process_v3_log(&self, chain_id: u64, log: &Log) {
        // V3 Swap(address sender, address recipient, int256 amount0, int256 amount1, uint160 sqrtPriceX96, uint128 liquidity, int24 tick)
        // Topics: [event_sig, sender, recipient]
        if log.topics.len() < 3 {
            return;
        }

        let sender = Address::from(log.topics[1]);
        let recipient = Address::from(log.topics[2]);

        // Check if sender or recipient is a watched wallet
        let trader = if self.watched_wallets.contains_key(&sender) {
            sender
        } else if self.watched_wallets.contains_key(&recipient) {
            recipient
        } else {
            // Not watching either party — check transaction sender
            if let Some(tx_hash) = log.transaction_hash {
                if let Some(provider) = self.rpc.get_provider(chain_id) {
                    if let Ok(Some(tx)) = provider.get_transaction(tx_hash).await {
                        if self.watched_wallets.contains_key(&tx.from) {
                            tx.from
                        } else {
                            return;
                        }
                    } else {
                        return;
                    }
                } else {
                    return;
                }
            } else {
                return;
            }
        };

        // Decode data: int256 amount0, int256 amount1, uint160 sqrtPriceX96, uint128 liquidity, int24 tick
        if log.data.len() < 160 {
            return;
        }

        let amount0 = I256::from_raw(U256::from_big_endian(&log.data[0..32]));
        let amount1 = I256::from_raw(U256::from_big_endian(&log.data[32..64]));

        // Determine token_in/out based on sign (negative = outgoing from pool = received by trader)
        let (amount_in, amount_out) = if amount0.is_positive() {
            // token0 went in, token1 came out
            (
                amount0.into_raw().to_string(),
                amount1.into_raw().to_string(),
            )
        } else {
            // token1 went in, token0 came out
            (
                amount1.into_raw().to_string(),
                amount0.into_raw().to_string(),
            )
        };

        let pool = format!("{:?}", log.address);
        let tx_hash = log
            .transaction_hash
            .map(|h| format!("{:?}", h))
            .unwrap_or_default();
        let block = log.block_number.map(|b| b.as_u64()).unwrap_or(0);

        let event = SwapEvent {
            chain_id,
            tx_hash,
            block_number: block,
            timestamp: 0, // filled by caller if needed
            trader: format!("{:?}", trader),
            router: pool.clone(), // pool address for V3
            token_in: String::new(),  // would need pool token0/token1 lookup
            token_out: String::new(),
            amount_in,
            amount_out,
            pool,
            dex: "uniswap_v3".into(),
        };

        let _ = self.event_tx.send(event);
    }

    /// Process a Uniswap V2 Swap log
    async fn process_v2_log(&self, chain_id: u64, log: &Log) {
        // V2 Swap(address sender, uint amount0In, uint amount0Out, uint amount1In, uint amount1Out, address to)
        // Topics: [event_sig, sender, to]
        if log.topics.len() < 3 {
            return;
        }

        let sender = Address::from(log.topics[1]);
        let to = Address::from(log.topics[2]);

        let trader = if self.watched_wallets.contains_key(&sender) {
            sender
        } else if self.watched_wallets.contains_key(&to) {
            to
        } else {
            // Check tx sender
            if let Some(tx_hash) = log.transaction_hash {
                if let Some(provider) = self.rpc.get_provider(chain_id) {
                    if let Ok(Some(tx)) = provider.get_transaction(tx_hash).await {
                        if self.watched_wallets.contains_key(&tx.from) {
                            tx.from
                        } else {
                            return;
                        }
                    } else {
                        return;
                    }
                } else {
                    return;
                }
            } else {
                return;
            }
        };

        if log.data.len() < 128 {
            return;
        }

        let amount0_in = U256::from_big_endian(&log.data[0..32]);
        let amount0_out = U256::from_big_endian(&log.data[32..64]);
        let amount1_in = U256::from_big_endian(&log.data[64..96]);
        let amount1_out = U256::from_big_endian(&log.data[96..128]);

        let (amount_in, amount_out) = if !amount0_in.is_zero() {
            (amount0_in.to_string(), amount1_out.to_string())
        } else {
            (amount1_in.to_string(), amount0_out.to_string())
        };

        let pool = format!("{:?}", log.address);
        let tx_hash = log
            .transaction_hash
            .map(|h| format!("{:?}", h))
            .unwrap_or_default();
        let block = log.block_number.map(|b| b.as_u64()).unwrap_or(0);

        let event = SwapEvent {
            chain_id,
            tx_hash,
            block_number: block,
            timestamp: 0,
            trader: format!("{:?}", trader),
            router: pool.clone(),
            token_in: String::new(),
            token_out: String::new(),
            amount_in,
            amount_out,
            pool,
            dex: "uniswap_v2".into(),
        };

        let _ = self.event_tx.send(event);
    }
}
