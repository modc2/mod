use std::collections::HashMap;

use crate::models::swap::Swap;

/// FIFO cost-basis position tracking per pool
#[derive(Default)]
struct Position {
    /// Amount of token0 held
    size: f64,
    /// Total USD cost of acquiring that token0
    cost: f64,
}

/// Compute realized PnL using FIFO cost-basis method
/// Returns (total_pnl, win_rate, per_pool_pnl)
pub fn compute_fifo_pnl(
    swaps: &[Swap],
    cutoff: i64,
) -> (f64, f64, HashMap<String, f64>) {
    let mut sorted = swaps.to_vec();
    sorted.sort_by_key(|s| s.timestamp);

    // Track position per pool
    let mut positions: HashMap<String, Position> = HashMap::new();
    let mut pool_pnl: HashMap<String, f64> = HashMap::new();
    let mut total_pnl = 0.0;

    for swap in &sorted {
        let pos = positions.entry(swap.pool_id.clone()).or_default();
        let in_window = swap.timestamp >= cutoff;

        if swap.is_buy_token0 {
            // Buying token0: increase position
            pos.size += swap.amount_usd;
            pos.cost += swap.amount_usd;
        } else {
            // Selling token0: realize PnL
            if pos.size > 0.0 {
                let avg_cost = pos.cost / pos.size;
                let sold_amount = swap.amount_usd.min(pos.size);
                // For Uniswap, we approximate PnL as the difference between
                // sell value and cost basis proportional to the position sold
                let cost_of_sold = avg_cost * (sold_amount / pos.size) * pos.size;
                let realized = sold_amount - cost_of_sold;

                if in_window {
                    total_pnl += realized;
                    *pool_pnl.entry(swap.pool_id.clone()).or_insert(0.0) += realized;
                }

                pos.size -= sold_amount;
                pos.cost -= cost_of_sold;
                if pos.size < 0.001 {
                    pos.size = 0.0;
                    pos.cost = 0.0;
                }
            }
        }
    }

    // Win rate: % of pools with positive PnL
    let pools_with_activity = pool_pnl.len();
    let pools_profitable = pool_pnl.values().filter(|&&v| v > 0.0).count();
    let win_rate = if pools_with_activity > 0 {
        (pools_profitable as f64 / pools_with_activity as f64) * 100.0
    } else {
        0.0
    };

    (total_pnl, win_rate, pool_pnl)
}

/// Compute a PnL curve with N buckets over the time window
pub fn compute_pnl_curve(swaps: &[Swap], cutoff: i64, buckets: usize) -> Vec<f64> {
    let now = chrono::Utc::now().timestamp();
    let window = now - cutoff;
    if window <= 0 {
        return vec![0.0; buckets];
    }

    let bucket_size = window as f64 / buckets as f64;
    let mut curve = vec![0.0; buckets];

    let mut sorted = swaps.to_vec();
    sorted.sort_by_key(|s| s.timestamp);

    let mut positions: HashMap<String, Position> = HashMap::new();

    for swap in &sorted {
        if swap.timestamp < cutoff {
            // Pre-window: just build positions
            let pos = positions.entry(swap.pool_id.clone()).or_default();
            if swap.is_buy_token0 {
                pos.size += swap.amount_usd;
                pos.cost += swap.amount_usd;
            } else if pos.size > 0.0 {
                let sold = swap.amount_usd.min(pos.size);
                let cost_ratio = sold / pos.size;
                pos.cost -= pos.cost * cost_ratio;
                pos.size -= sold;
            }
            continue;
        }

        let pos = positions.entry(swap.pool_id.clone()).or_default();
        let offset = swap.timestamp - cutoff;
        let bucket = ((offset as f64 / bucket_size) as usize).min(buckets - 1);

        if swap.is_buy_token0 {
            pos.size += swap.amount_usd;
            pos.cost += swap.amount_usd;
        } else if pos.size > 0.0 {
            let avg_cost = pos.cost / pos.size;
            let sold = swap.amount_usd.min(pos.size);
            let cost_of_sold = avg_cost * (sold / pos.size) * pos.size;
            let realized = sold - cost_of_sold;
            curve[bucket] += realized;

            pos.size -= sold;
            pos.cost -= cost_of_sold;
            if pos.size < 0.001 {
                pos.size = 0.0;
                pos.cost = 0.0;
            }
        }
    }

    // Make cumulative
    for i in 1..curve.len() {
        curve[i] += curve[i - 1];
    }

    curve
}
