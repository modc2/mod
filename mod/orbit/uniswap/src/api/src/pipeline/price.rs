use std::collections::HashMap;
use std::sync::Arc;

use crate::config;
use crate::models::chain::Chain;
use crate::pipeline::meta::PoolMeta;
use crate::pipeline::rpc;
use crate::state::AppState;

/// Uniswap V3 `slot0()`
const SEL_SLOT0: &str = "0x3850c7bd";

/// Read a big-endian hex word as f64. Values here (sqrtPriceX96, token
/// amounts) routinely exceed u64, so accumulating nibble by nibble into a
/// float is the honest way to read them — f64 keeps 53 bits of mantissa,
/// which is far more precision than a USD figure needs, whereas truncating to
/// the low 64 bits (what this code used to do) silently wraps any amount over
/// ~18 ETH into a small number.
pub fn hex_to_f64(hex: &str) -> f64 {
    let mut v = 0.0f64;
    for c in hex.chars() {
        let d = match c.to_digit(16) {
            Some(d) => d as f64,
            None => continue,
        };
        v = v * 16.0 + d;
    }
    v
}

/// Read a 32-byte two's-complement int256 as f64, full width.
pub fn parse_int256(hex: &str) -> f64 {
    if hex.len() < 64 {
        return 0.0;
    }
    let hex = &hex[0..64];

    let first = hex.chars().next().and_then(|c| c.to_digit(16)).unwrap_or(0);
    if first < 8 {
        return hex_to_f64(hex);
    }

    // Negative: take the two's complement on the nibbles themselves. Doing it
    // in f64 instead (2^256 minus the unsigned reading) cancels catastrophically
    // — 2^256 - 1 is not representable, so every small negative amount would
    // come back as exactly zero.
    let mut nibbles: Vec<u32> = hex
        .chars()
        .map(|c| 15 - c.to_digit(16).unwrap_or(0))
        .collect();
    for n in nibbles.iter_mut().rev() {
        if *n == 15 {
            *n = 0;
        } else {
            *n += 1;
            break;
        }
    }

    let mut magnitude = 0.0f64;
    for n in nibbles {
        magnitude = magnitude * 16.0 + n as f64;
    }
    -magnitude
}

/// The chain's ETH price in USD, read from a stable/WETH pool's current price.
///
/// Taken from `slot0()` rather than from an external price API: the pool is
/// already the thing being measured, the reading is exact, and it adds no
/// dependency that can rate-limit or disappear. Cached per chain for a few
/// minutes — swap USD values do not need tick-level freshness.
pub async fn eth_price_usd(
    state: &Arc<AppState>,
    chain: &Chain,
    pools: &HashMap<String, PoolMeta>,
) -> Option<f64> {
    if let Some(p) = state.get_eth_price(chain.name()) {
        return Some(p);
    }

    // Prefer the tightest fee tier among stable/WETH pools — deepest liquidity,
    // least spread.
    let mut candidates: Vec<&PoolMeta> = pools
        .values()
        .filter(|m| {
            (config::is_stable(&m.symbol0) && config::is_eth_like(&m.symbol1))
                || (config::is_eth_like(&m.symbol0) && config::is_stable(&m.symbol1))
        })
        .collect();
    candidates.sort_by_key(|m| m.fee);

    for meta in candidates {
        let Ok(hex) = rpc::eth_call(&state.http, chain, &meta.pool, SEL_SLOT0).await else {
            continue;
        };
        if hex.len() < 64 {
            continue;
        }

        // price of token0 denominated in token1
        let sqrt_price = hex_to_f64(&hex[0..64]);
        if sqrt_price <= 0.0 {
            continue;
        }
        let ratio = (sqrt_price / 2f64.powi(96)).powi(2);
        let price0_in_1 = ratio * 10f64.powi(meta.decimals0 as i32)
            / 10f64.powi(meta.decimals1 as i32);

        let eth = if config::is_eth_like(&meta.symbol0) {
            price0_in_1
        } else if price0_in_1 > 0.0 {
            1.0 / price0_in_1
        } else {
            continue;
        };

        // Sanity band: a reading outside it means the pool was misread, not
        // that ETH moved.
        if eth.is_finite() && (50.0..1_000_000.0).contains(&eth) {
            tracing::info!(
                "{}: ETH = ${:.2} from {} ({})",
                chain.name(),
                eth,
                meta.label(),
                &meta.pool[..10]
            );
            state.set_eth_price(chain.name(), eth);
            return Some(eth);
        }
    }

    tracing::warn!("{}: no stable/WETH pool resolved — ETH-quoted pools will price as 0", chain.name());
    None
}

/// USD value of one swap, from the side of the pair whose price is known.
///
/// A Uniswap swap has two legs of equal value, so pricing either leg prices
/// the trade. Stablecoin legs are taken at $1; WETH legs at the pool price
/// read above. A pair with neither is left unpriced (0) rather than guessed.
pub fn swap_usd(meta: &PoolMeta, amount0: f64, amount1: f64, eth_price: Option<f64>) -> f64 {
    let a0 = amount0.abs();
    let a1 = amount1.abs();

    if config::is_stable(&meta.symbol0) {
        return a0;
    }
    if config::is_stable(&meta.symbol1) {
        return a1;
    }
    if let Some(eth) = eth_price {
        if config::is_eth_like(&meta.symbol0) {
            return a0 * eth;
        }
        if config::is_eth_like(&meta.symbol1) {
            return a1 * eth;
        }
    }
    0.0
}
