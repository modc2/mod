use std::collections::HashMap;

use crate::models::swap::Swap;
use crate::models::trader::{PoolStats, TokenStats, TraderCandidate};

/// Group swaps by sender and filter by minimum swap count
pub fn aggregate_traders(swaps: &[Swap], min_swaps: u32) -> Vec<TraderCandidate> {
    let mut map: HashMap<&str, (u32, f64)> = HashMap::new();

    for swap in swaps {
        let entry = map.entry(swap.sender.as_str()).or_insert((0, 0.0));
        entry.0 += 1;
        entry.1 += swap.amount_usd;
    }

    let mut candidates: Vec<TraderCandidate> = map
        .into_iter()
        .filter(|(_, (count, _))| *count >= min_swaps)
        .map(|(addr, (count, volume))| TraderCandidate {
            address: addr.to_string(),
            swap_count: count,
            total_volume_usd: volume,
        })
        .collect();

    // Sort by volume descending
    candidates.sort_by(|a, b| b.total_volume_usd.partial_cmp(&a.total_volume_usd).unwrap());
    candidates
}

/// Compute token statistics for a trader's swaps
pub fn compute_token_stats(swaps: &[&Swap]) -> (Vec<TokenStats>, f64) {
    let mut token_map: HashMap<String, (f64, f64, u32)> = HashMap::new(); // (volume, net_flow, count)

    for swap in swaps {
        // Token0 stats
        let entry = token_map
            .entry(swap.token0_symbol.clone())
            .or_insert((0.0, 0.0, 0));
        entry.0 += swap.amount_usd / 2.0; // Approximate half per token
        if swap.is_buy_token0 {
            entry.1 += swap.amount_usd / 2.0; // Inflow
        } else {
            entry.1 -= swap.amount_usd / 2.0; // Outflow
        }
        entry.2 += 1;

        // Token1 stats
        let entry = token_map
            .entry(swap.token1_symbol.clone())
            .or_insert((0.0, 0.0, 0));
        entry.0 += swap.amount_usd / 2.0;
        if !swap.is_buy_token0 {
            entry.1 += swap.amount_usd / 2.0;
        } else {
            entry.1 -= swap.amount_usd / 2.0;
        }
        entry.2 += 1;
    }

    let mut tokens: Vec<TokenStats> = token_map
        .into_iter()
        .map(|(symbol, (volume, net_flow, count))| TokenStats {
            symbol,
            volume_usd: volume,
            net_flow_usd: net_flow,
            swap_count: count,
        })
        .collect();

    tokens.sort_by(|a, b| b.volume_usd.partial_cmp(&a.volume_usd).unwrap());

    // Compute HHI (Herfindahl-Hirschman Index) for concentration
    let total_vol: f64 = tokens.iter().map(|t| t.volume_usd).sum();
    let hhi = if total_vol > 0.0 {
        tokens
            .iter()
            .map(|t| {
                let share = t.volume_usd / total_vol;
                share * share
            })
            .sum::<f64>()
    } else {
        1.0
    };

    (tokens.into_iter().take(10).collect(), hhi)
}

/// Compute pool statistics and diversity score
pub fn compute_pool_stats(
    swaps: &[&Swap],
    pool_pnls: &HashMap<String, f64>,
) -> (Vec<PoolStats>, f64) {
    let mut pool_map: HashMap<String, (String, String, u32, f64, u32)> = HashMap::new();

    for swap in swaps {
        let entry = pool_map
            .entry(swap.pool_id.clone())
            .or_insert((
                swap.token0_symbol.clone(),
                swap.token1_symbol.clone(),
                swap.fee_tier,
                0.0,
                0,
            ));
        entry.3 += swap.amount_usd;
        entry.4 += 1;
    }

    let mut pools: Vec<PoolStats> = pool_map
        .into_iter()
        .map(|(pool_id, (t0, t1, fee, vol, count))| {
            let pnl = pool_pnls.get(&pool_id).copied().unwrap_or(0.0);
            PoolStats {
                pool_id,
                token0: t0,
                token1: t1,
                fee_tier: fee,
                volume_usd: vol,
                swap_count: count,
                pnl_usd: pnl,
            }
        })
        .collect();

    pools.sort_by(|a, b| b.volume_usd.partial_cmp(&a.volume_usd).unwrap());

    // Pool diversity = inverse HHI (higher = more diverse)
    let total_vol: f64 = pools.iter().map(|p| p.volume_usd).sum();
    let hhi = if total_vol > 0.0 {
        pools
            .iter()
            .map(|p| {
                let share = p.volume_usd / total_vol;
                share * share
            })
            .sum::<f64>()
    } else {
        1.0
    };
    let diversity = 1.0 - hhi; // 0 = all in one pool, ~1 = evenly spread

    (pools.into_iter().take(20).collect(), diversity)
}
