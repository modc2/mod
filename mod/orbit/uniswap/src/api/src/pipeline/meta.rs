use futures_util::stream::{self, StreamExt};
use std::collections::HashMap;
use std::sync::Arc;

use crate::config;
use crate::models::chain::Chain;
use crate::pipeline::price::hex_to_f64;
use crate::pipeline::rpc;
use crate::state::AppState;

/// Everything a Swap log leaves out: which tokens the pool holds, how many
/// decimals each has, and what fee tier it charges.
///
/// A Uniswap V3 Swap event carries two raw int256 amounts and nothing else.
/// Without decimals those amounts are unitless integers, which is why the
/// pricing here used to be a guess ("if it looks small it's probably USDC")
/// and why every trader came back with empty token symbols.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct PoolMeta {
    pub pool: String,
    pub token0: String,
    pub token1: String,
    pub symbol0: String,
    pub symbol1: String,
    pub decimals0: u32,
    pub decimals1: u32,
    pub fee: u32,
    /// Quote-token balance held by the pool, the ranking proxy for depth.
    pub quote_balance: f64,
    pub quote_symbol: String,
}

impl PoolMeta {
    pub fn label(&self) -> String {
        format!("{}/{} {}bps", self.symbol0, self.symbol1, self.fee / 100)
    }
}

/// One ERC-20, resolved once and reused across every pool that holds it.
#[derive(Debug, Clone)]
pub struct TokenMeta {
    pub address: String,
    pub symbol: String,
    pub decimals: u32,
}

// Selectors
const SEL_DECIMALS: &str = "0x313ce567"; // decimals()
const SEL_SYMBOL: &str = "0x95d89b41"; // symbol()
const SEL_GET_POOL: &str = "0x1698ee82"; // getPool(address,address,uint24)
const SEL_BALANCE_OF: &str = "0x70a08231"; // balanceOf(address)

fn pad_address(addr: &str) -> String {
    format!("{:0>64}", addr.trim_start_matches("0x").to_lowercase())
}

fn pad_u32(v: u32) -> String {
    format!("{v:064x}")
}

fn decode_address(hex: &str) -> Option<String> {
    if hex.len() < 64 {
        return None;
    }
    Some(format!("0x{}", &hex[24..64].to_lowercase()))
}

fn decode_uint(hex: &str) -> Option<u64> {
    if hex.len() < 64 {
        return None;
    }
    u64::from_str_radix(&hex[0..64], 16).ok()
}

const ZERO_ADDRESS: &str = "0x0000000000000000000000000000000000000000";

/// Decode an ERC-20 `symbol()` return: either a dynamic string or, for tokens
/// that predate the standard settling, a raw bytes32.
fn decode_symbol(hex: &str) -> Option<String> {
    let bytes = hex::decode(hex).ok()?;

    // Dynamic string: offset word, length word, then the bytes.
    if bytes.len() >= 64 {
        let offset = u64::from_be_bytes(bytes[24..32].try_into().ok()?) as usize;
        if offset == 32 {
            let len = u64::from_be_bytes(bytes[56..64].try_into().ok()?) as usize;
            if len > 0 && len <= 64 && bytes.len() >= 64 + len {
                if let Ok(s) = std::str::from_utf8(&bytes[64..64 + len]) {
                    let s = s.trim().to_string();
                    if !s.is_empty() {
                        return Some(s);
                    }
                }
            }
        }
    }

    // bytes32: NUL-padded ASCII.
    if bytes.len() >= 32 {
        let s: String = bytes[0..32]
            .iter()
            .take_while(|&&b| b != 0)
            .map(|&b| b as char)
            .collect();
        let s = s.trim().to_string();
        if !s.is_empty() && s.chars().all(|c| c.is_ascii_graphic()) {
            return Some(s);
        }
    }

    None
}

/// Resolve one ERC-20's symbol and decimals, through the process cache.
///
/// Caching by token rather than by pool matters for correctness as well as
/// speed: resolving per pool meant the same WETH could come back named "WETH"
/// in one pool and "0x420000…" in the next, whenever a single eth_call among
/// dozens happened to be the one a public endpoint dropped.
async fn resolve_token(state: &Arc<AppState>, chain: &Chain, address: &str) -> TokenMeta {
    let address = address.to_lowercase();
    let key = format!("{}:{}", chain.name(), address);

    if let Some(t) = state.token_meta.get(&key) {
        return t.clone();
    }

    let decimals = rpc::eth_call(&state.http, chain, &address, SEL_DECIMALS)
        .await
        .ok()
        .and_then(|h| decode_uint(&h))
        .unwrap_or(18) as u32;

    let symbol = rpc::eth_call(&state.http, chain, &address, SEL_SYMBOL)
        .await
        .ok()
        .and_then(|h| decode_symbol(&h))
        .unwrap_or_else(|| format!("{}…", &address[..8]));

    let meta = TokenMeta {
        address: address.clone(),
        symbol,
        decimals,
    };
    state.token_meta.insert(key, meta.clone());
    meta
}

/// Ask the V3 factory for the pool holding a pair at a fee tier.
async fn get_pool(
    state: &Arc<AppState>,
    chain: &Chain,
    a: &str,
    b: &str,
    fee: u32,
) -> Option<String> {
    let data = format!(
        "{SEL_GET_POOL}{}{}{}",
        pad_address(a),
        pad_address(b),
        pad_u32(fee)
    );
    let hex = rpc::eth_call(&state.http, chain, config::factory_address(chain), &data)
        .await
        .ok()?;
    let pool = decode_address(&hex)?;
    (pool != ZERO_ADDRESS).then_some(pool)
}

/// The pool's balance of a token, decimal-adjusted.
async fn balance_of(
    state: &Arc<AppState>,
    chain: &Chain,
    token: &TokenMeta,
    holder: &str,
) -> Option<f64> {
    let data = format!("{SEL_BALANCE_OF}{}", pad_address(holder));
    let hex = rpc::eth_call(&state.http, chain, &token.address, &data)
        .await
        .ok()?;
    if hex.len() < 64 {
        return None;
    }
    Some(hex_to_f64(&hex[0..64]) / 10f64.powi(token.decimals as i32))
}

/// Discover the deepest Uniswap V3 pools on a chain and describe them fully.
///
/// Every pool address comes from the canonical factory, so it is a real V3
/// pool on this chain by construction, and its fee tier is the one the factory
/// was asked for rather than one decoded out of an unknown contract. Ranking
/// is by how much of the quote token each pool actually holds — a direct read
/// of depth, not an assumption about which pairs were busy the day the list
/// was written.
pub async fn resolve_chain_pools(
    state: &Arc<AppState>,
    chain: &Chain,
) -> HashMap<String, PoolMeta> {
    if let Some(cached) = state.chain_pools.get(chain.name()) {
        return cached.clone();
    }

    let token_addrs = config::tokens(chain);

    // Resolve the token registry first — every later step needs decimals.
    let addrs: Vec<String> = token_addrs.iter().map(|a| a.to_string()).collect();
    let tokens: Vec<TokenMeta> = stream::iter(addrs)
        .map(|a| {
            let state = state.clone();
            let chain = *chain;
            async move { resolve_token(&state, &chain, &a).await }
        })
        .buffer_unordered(config::META_CONCURRENCY)
        .collect()
        .await;

    let by_addr: HashMap<String, TokenMeta> = tokens
        .iter()
        .map(|t| (t.address.clone(), t.clone()))
        .collect();

    // Every unordered pair at every fee tier.
    let mut queries = Vec::new();
    for i in 0..tokens.len() {
        for j in (i + 1)..tokens.len() {
            for fee in config::FEE_TIERS {
                queries.push((tokens[i].address.clone(), tokens[j].address.clone(), *fee));
            }
        }
    }

    let found: Vec<Option<(String, String, String, u32)>> = stream::iter(queries)
        .map(|(a, b, fee)| {
            let state = state.clone();
            let chain = *chain;
            async move {
                get_pool(&state, &chain, &a, &b, fee)
                    .await
                    .map(|pool| (pool, a, b, fee))
            }
        })
        .buffer_unordered(config::META_CONCURRENCY)
        .collect()
        .await;

    // Rank by quote-token depth.
    let ranked: Vec<Option<PoolMeta>> = stream::iter(found.into_iter().flatten())
        .map(|(pool, a, b, fee)| {
            let state = state.clone();
            let chain = *chain;
            let by_addr = by_addr.clone();
            async move {
                let ta = by_addr.get(&a)?.clone();
                let tb = by_addr.get(&b)?.clone();

                // Uniswap orders a pool's tokens by address, always.
                let (t0, t1) = if ta.address < tb.address {
                    (ta, tb)
                } else {
                    (tb, ta)
                };

                // Quote side: the stablecoin if there is one, else ETH.
                let quote = if config::is_stable(&t1.symbol) || config::is_eth_like(&t1.symbol) {
                    t1.clone()
                } else {
                    t0.clone()
                };
                let quote_balance = balance_of(&state, &chain, &quote, &pool).await?;

                Some(PoolMeta {
                    pool: pool.to_lowercase(),
                    token0: t0.address,
                    token1: t1.address,
                    symbol0: t0.symbol,
                    symbol1: t1.symbol,
                    decimals0: t0.decimals,
                    decimals1: t1.decimals,
                    fee,
                    quote_balance,
                    quote_symbol: quote.symbol,
                })
            }
        })
        .buffer_unordered(config::META_CONCURRENCY)
        .collect()
        .await;

    let mut pools: Vec<PoolMeta> = ranked.into_iter().flatten().collect();

    // Depth is only comparable within a quote token, so weight ETH-quoted
    // balances into the same units before ranking against stable-quoted ones.
    let eth_hint = state.get_eth_price(chain.name()).unwrap_or(3000.0);
    pools.sort_by(|a, b| {
        let av = if config::is_eth_like(&a.quote_symbol) {
            a.quote_balance * eth_hint
        } else {
            a.quote_balance
        };
        let bv = if config::is_eth_like(&b.quote_symbol) {
            b.quote_balance * eth_hint
        } else {
            b.quote_balance
        };
        bv.total_cmp(&av)
    });

    let discovered = pools.len();
    pools.truncate(config::MAX_POOLS);

    let out: HashMap<String, PoolMeta> = pools
        .into_iter()
        .map(|m| {
            tracing::info!(
                "{}: pool {} = {} ({:.0} {} deep)",
                chain.name(),
                &m.pool[..10],
                m.label(),
                m.quote_balance,
                m.quote_symbol,
            );
            state.pool_meta.insert(m.pool.clone(), m.clone());
            (m.pool.clone(), m)
        })
        .collect();

    tracing::info!(
        "{}: discovered {} V3 pools across {} tokens, sampling the {} deepest",
        chain.name(),
        discovered,
        token_addrs.len(),
        out.len(),
    );

    state.chain_pools.insert(chain.name().to_string(), out.clone());
    out
}
