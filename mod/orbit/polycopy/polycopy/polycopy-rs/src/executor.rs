use std::sync::Arc;

use ethers::abi::{self, Token};
use ethers::prelude::*;
use ethers::types::{Address, Bytes, TransactionRequest, U256};
use tokio::sync::broadcast;
use tracing::{error, info, warn};

use crate::rpc::RpcPool;
use crate::types::{EngineConfig, SwapEvent};

pub struct TradeExecutor {
    rpc: Arc<RpcPool>,
    config: EngineConfig,
    /// chain_id -> proxy contract address
    proxy_addresses: dashmap::DashMap<u64, Address>,
    /// Wallet for signing transactions
    private_key: Option<String>,
}

impl TradeExecutor {
    pub fn new(rpc: Arc<RpcPool>, config: EngineConfig) -> Self {
        let private_key = config.private_key.clone();
        Self {
            rpc,
            config,
            proxy_addresses: dashmap::DashMap::new(),
            private_key,
        }
    }

    /// Set proxy contract address for a chain
    pub fn set_proxy(&self, chain_id: u64, address: &str) {
        if let Ok(addr) = address.parse::<Address>() {
            self.proxy_addresses.insert(chain_id, addr);
            info!("Chain {} proxy set to {:?}", chain_id, addr);
        }
    }

    /// Get a signer for a chain
    fn get_signer(
        &self,
        chain_id: u64,
    ) -> Option<SignerMiddleware<Provider<Http>, LocalWallet>> {
        let provider = self.rpc.get_provider(chain_id)?;
        let key = self.private_key.as_ref()?;
        let wallet: LocalWallet = key.parse().ok()?;
        let wallet = wallet.with_chain_id(chain_id);
        Some(SignerMiddleware::new(provider, wallet))
    }

    /// Build calldata for a V2 swap (swapExactTokensForTokens)
    fn encode_v2_swap(
        &self,
        token_in: Address,
        token_out: Address,
        amount_in: U256,
        min_amount_out: U256,
        recipient: Address,
    ) -> Bytes {
        let deadline = U256::from(
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap_or_default()
                .as_secs()
                + 300, // 5 min deadline
        );

        let path = vec![Token::Address(token_in), Token::Address(token_out)];
        let tokens = vec![
            Token::Uint(amount_in),
            Token::Uint(min_amount_out),
            Token::Array(path),
            Token::Address(recipient),
            Token::Uint(deadline),
        ];

        // swapExactTokensForTokens selector: 0x38ed1739
        let selector: [u8; 4] = [0x38, 0xed, 0x17, 0x39];
        let encoded = abi::encode(&tokens);
        let mut calldata = selector.to_vec();
        calldata.extend(encoded);
        Bytes::from(calldata)
    }

    /// Build calldata for a V3 swap (exactInputSingle)
    fn encode_v3_swap(
        &self,
        token_in: Address,
        token_out: Address,
        amount_in: U256,
        min_amount_out: U256,
        recipient: Address,
        fee: u32,
    ) -> Bytes {
        // ExactInputSingleParams struct
        let params = Token::Tuple(vec![
            Token::Address(token_in),
            Token::Address(token_out),
            Token::Uint(U256::from(fee)),
            Token::Address(recipient),
            Token::Uint(amount_in),
            Token::Uint(min_amount_out),
            Token::Uint(U256::zero()), // sqrtPriceLimitX96 = 0
        ]);

        // exactInputSingle selector: 0x04e45aaf
        let selector: [u8; 4] = [0x04, 0xe4, 0x5a, 0xaf];
        let encoded = abi::encode(&[params]);
        let mut calldata = selector.to_vec();
        calldata.extend(encoded);
        Bytes::from(calldata)
    }

    /// Build the TradeCall struct for the proxy contract
    fn encode_proxy_call(
        &self,
        router: Address,
        token_in: Address,
        token_out: Address,
        amount_in: U256,
        min_amount_out: U256,
        swap_calldata: Bytes,
    ) -> Bytes {
        // executeTrade((address,address,address,uint256,uint256,bytes))
        // selector: first 4 bytes of keccak256("executeTrade((address,address,address,uint256,uint256,bytes))")
        let trade_tuple = Token::Tuple(vec![
            Token::Address(router),
            Token::Address(token_in),
            Token::Address(token_out),
            Token::Uint(amount_in),
            Token::Uint(min_amount_out),
            Token::Bytes(swap_calldata.to_vec()),
        ]);

        // We use the function selector for executeTrade
        let selector = ethers::utils::id("executeTrade((address,address,address,uint256,uint256,bytes))");
        let encoded = abi::encode(&[trade_tuple]);
        let mut calldata = selector[..4].to_vec();
        calldata.extend(encoded);
        Bytes::from(calldata)
    }

    /// Execute a copy trade based on a detected swap event
    pub async fn copy_trade(&self, event: &SwapEvent) -> Result<String, String> {
        let chain_id = event.chain_id;

        let proxy_addr = self
            .proxy_addresses
            .get(&chain_id)
            .map(|v| *v)
            .ok_or_else(|| format!("No proxy contract for chain {}", chain_id))?;

        let signer = self
            .get_signer(chain_id)
            .ok_or("No signer configured (missing private key)")?;

        // Parse amounts and scale position
        let original_amount: U256 = event
            .amount_in
            .parse()
            .map_err(|_| "Invalid amount_in")?;

        // Scale by position_pct
        let scaled_amount = original_amount * U256::from((self.config.position_pct * 100.0) as u64)
            / U256::from(10000u64);

        if scaled_amount.is_zero() {
            return Err("Scaled amount is zero".into());
        }

        // Calculate minAmountOut with slippage
        let original_out: U256 = event
            .amount_out
            .parse()
            .unwrap_or(U256::zero());

        let scaled_out = if !original_out.is_zero() && !original_amount.is_zero() {
            original_out * scaled_amount / original_amount
        } else {
            U256::zero()
        };

        let slippage_factor = U256::from(10000u64 - self.config.slippage_bps);
        let min_amount_out = scaled_out * slippage_factor / U256::from(10000u64);

        // Parse addresses
        let token_in: Address = if event.token_in.is_empty() {
            Address::zero()
        } else {
            event.token_in.parse().map_err(|_| "Invalid token_in")?
        };
        let token_out: Address = if event.token_out.is_empty() {
            Address::zero()
        } else {
            event.token_out.parse().map_err(|_| "Invalid token_out")?
        };
        let router: Address = event.router.parse().map_err(|_| "Invalid router")?;

        // Build swap calldata based on DEX type
        let swap_calldata = if event.dex == "uniswap_v3" {
            self.encode_v3_swap(token_in, token_out, scaled_amount, min_amount_out, proxy_addr, 3000)
        } else {
            self.encode_v2_swap(token_in, token_out, scaled_amount, min_amount_out, proxy_addr)
        };

        // Build proxy call
        let proxy_calldata = self.encode_proxy_call(
            router,
            token_in,
            token_out,
            scaled_amount,
            min_amount_out,
            swap_calldata,
        );

        // Estimate gas
        let tx = TransactionRequest::new()
            .to(proxy_addr)
            .data(proxy_calldata.clone())
            .from(signer.address());

        let gas_estimate = signer
            .estimate_gas(&tx.clone().into(), None)
            .await
            .map_err(|e| format!("Gas estimation failed: {}", e))?;

        // Add 30% gas buffer
        let gas_limit = gas_estimate * 130 / 100;

        let tx = tx.gas(gas_limit);

        info!(
            "Executing copy trade on chain {}: {} -> {}, amount_in={}, min_out={}",
            chain_id,
            event.token_in,
            event.token_out,
            scaled_amount,
            min_amount_out
        );

        // Send transaction
        let pending = signer
            .send_transaction(tx, None)
            .await
            .map_err(|e| format!("Send tx failed: {}", e))?;

        let tx_hash = format!("{:?}", pending.tx_hash());
        info!("Trade submitted: {}", tx_hash);

        // Wait for receipt
        match pending.await {
            Ok(Some(receipt)) => {
                if receipt.status == Some(U64::from(1)) {
                    info!("Trade confirmed: {} (gas used: {:?})", tx_hash, receipt.gas_used);
                    Ok(tx_hash)
                } else {
                    Err(format!("Trade reverted: {}", tx_hash))
                }
            }
            Ok(None) => Err(format!("No receipt for: {}", tx_hash)),
            Err(e) => Err(format!("Receipt error: {}", e)),
        }
    }

    /// Pause trading on a chain
    pub async fn pause_chain(&self, chain_id: u64) -> Result<String, String> {
        let proxy_addr = self
            .proxy_addresses
            .get(&chain_id)
            .map(|v| *v)
            .ok_or("No proxy")?;

        let signer = self.get_signer(chain_id).ok_or("No signer")?;

        // pause() selector
        let selector = ethers::utils::id("pause()");
        let tx = TransactionRequest::new()
            .to(proxy_addr)
            .data(Bytes::from(selector[..4].to_vec()))
            .from(signer.address());

        let pending = signer
            .send_transaction(tx, None)
            .await
            .map_err(|e| format!("Pause failed: {}", e))?;

        Ok(format!("{:?}", pending.tx_hash()))
    }

    /// Unpause trading on a chain
    pub async fn unpause_chain(&self, chain_id: u64) -> Result<String, String> {
        let proxy_addr = self
            .proxy_addresses
            .get(&chain_id)
            .map(|v| *v)
            .ok_or("No proxy")?;

        let signer = self.get_signer(chain_id).ok_or("No signer")?;

        let selector = ethers::utils::id("unpause()");
        let tx = TransactionRequest::new()
            .to(proxy_addr)
            .data(Bytes::from(selector[..4].to_vec()))
            .from(signer.address());

        let pending = signer
            .send_transaction(tx, None)
            .await
            .map_err(|e| format!("Unpause failed: {}", e))?;

        Ok(format!("{:?}", pending.tx_hash()))
    }

    /// Listen for swap events and auto-execute copy trades
    pub async fn listen(self: Arc<Self>, mut rx: broadcast::Receiver<SwapEvent>) {
        info!("Trade executor listening for swap events");
        loop {
            match rx.recv().await {
                Ok(event) => {
                    let executor = self.clone();
                    tokio::spawn(async move {
                        match executor.copy_trade(&event).await {
                            Ok(hash) => {
                                info!("Copy trade success: {} (copied from {})", hash, event.trader);
                            }
                            Err(e) => {
                                error!("Copy trade failed for {}: {}", event.trader, e);
                            }
                        }
                    });
                }
                Err(broadcast::error::RecvError::Lagged(n)) => {
                    warn!("Executor lagged {} events", n);
                }
                Err(broadcast::error::RecvError::Closed) => {
                    info!("Executor channel closed");
                    break;
                }
            }
        }
    }
}
