use crate::models::swap::Swap;
use crate::models::trader::MevIndicators;

/// Detect MEV bot behavior from swap patterns
/// Returns (is_mev_bot, indicators)
pub fn detect_mev(swaps: &[Swap], active_days: u32) -> (bool, MevIndicators) {
    if swaps.is_empty() || active_days == 0 {
        return (false, MevIndicators::default());
    }

    let mut sorted = swaps.to_vec();
    sorted.sort_by_key(|s| s.timestamp);

    let avg_swaps_per_day = swaps.len() as f64 / active_days.max(1) as f64;

    // Min interval between consecutive swaps
    let mut min_interval: u64 = u64::MAX;
    for window in sorted.windows(2) {
        let diff = (window[1].timestamp - window[0].timestamp).unsigned_abs();
        if diff < min_interval && diff > 0 {
            min_interval = diff;
        }
    }
    if min_interval == u64::MAX {
        min_interval = 0;
    }

    // Sandwich detection: same pool, buy then sell within 60 seconds
    let mut sandwich_count: u32 = 0;
    for i in 0..sorted.len().saturating_sub(1) {
        for j in (i + 1)..sorted.len().min(i + 5) {
            if sorted[j].timestamp - sorted[i].timestamp > 60 {
                break;
            }
            if sorted[i].pool_id == sorted[j].pool_id
                && sorted[i].is_buy_token0 != sorted[j].is_buy_token0
            {
                sandwich_count += 1;
                break;
            }
        }
    }

    // Arbitrage detection: multiple pool swaps within same second (circular)
    let mut arb_count: u32 = 0;
    let mut i = 0;
    while i < sorted.len() {
        let ts = sorted[i].timestamp;
        let mut same_ts: Vec<&Swap> = vec![&sorted[i]];
        let mut j = i + 1;
        while j < sorted.len() && sorted[j].timestamp == ts {
            same_ts.push(&sorted[j]);
            j += 1;
        }
        if same_ts.len() >= 2 {
            // Multiple pools in same timestamp = likely arb
            let unique_pools: std::collections::HashSet<&str> =
                same_ts.iter().map(|s| s.pool_id.as_str()).collect();
            if unique_pools.len() >= 2 {
                arb_count += 1;
            }
        }
        i = j;
    }

    // High volume pool ratio (bots tend to only trade in high-liquidity pools)
    // Approximate: if >80% of volume is in top 2 pools, flag it
    let mut pool_volumes: std::collections::HashMap<&str, f64> = std::collections::HashMap::new();
    for s in &sorted {
        *pool_volumes.entry(s.pool_id.as_str()).or_insert(0.0) += s.amount_usd;
    }
    let total_vol: f64 = pool_volumes.values().sum();
    let mut vols: Vec<f64> = pool_volumes.values().copied().collect();
    vols.sort_by(|a, b| b.partial_cmp(a).unwrap());
    let top2_vol: f64 = vols.iter().take(2).sum();
    let high_volume_pool_ratio = if total_vol > 0.0 {
        top2_vol / total_vol
    } else {
        0.0
    };

    let indicators = MevIndicators {
        sandwich_count,
        arb_count,
        avg_swaps_per_day,
        min_swap_interval_sec: min_interval,
        high_volume_pool_ratio,
    };

    // Classification heuristics
    let is_mev = avg_swaps_per_day > 50.0
        || min_interval < 3
        || sandwich_count > 5
        || arb_count > 3;

    (is_mev, indicators)
}
