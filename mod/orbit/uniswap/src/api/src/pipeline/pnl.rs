use std::collections::HashMap;

use crate::models::swap::Swap;

/// An open position in a pool's token0, carried in token units with its cost
/// in USD.
///
/// The previous version tracked "size" in USD and "cost" in USD, incrementing
/// both by the same figure on every buy. Average cost was therefore identically
/// 1.0, realized PnL was `sold - 1.0 * sold`, and every trader on every chain
/// scored exactly $0.00 profit. Cost basis only means something if the position
/// is measured in the thing being held.
#[derive(Default, Clone)]
struct Position {
    /// token0 units held
    qty: f64,
    /// USD paid for those units
    cost: f64,
}

/// One realized sale.
pub struct Realization {
    pub pool_id: String,
    pub timestamp: i64,
    pub pnl: f64,
}

/// Walk a trader's swaps in time order, matching sells against FIFO cost basis.
///
/// Positions are built from the trader's whole history, including swaps before
/// the window, so a sale inside the window is priced against what it actually
/// cost. Only realizations inside the window are reported.
fn walk(swaps: &[Swap], cutoff: i64) -> Vec<Realization> {
    let mut sorted: Vec<&Swap> = swaps.iter().collect();
    sorted.sort_by_key(|s| (s.timestamp, s.block, s.id.clone()));

    let mut positions: HashMap<&str, Position> = HashMap::new();
    let mut out = Vec::new();

    for swap in sorted {
        // Skip swaps we could not price — folding a $0 leg into cost basis
        // would corrupt every later sale in that pool.
        if !swap.amount_usd.is_finite() || swap.amount_usd <= 0.0 {
            continue;
        }
        let qty = swap.amount0.abs();
        if !qty.is_finite() || qty <= 0.0 {
            continue;
        }

        let pos = positions.entry(swap.pool_id.as_str()).or_default();

        if swap.is_buy_token0 {
            pos.qty += qty;
            pos.cost += swap.amount_usd;
            continue;
        }

        // Selling token0. Only the part matched by an existing position can be
        // realized; the rest is a position opened outside the sample.
        if pos.qty <= 0.0 {
            continue;
        }
        let sold = qty.min(pos.qty);
        let unit_price = swap.amount_usd / qty;
        let unit_cost = pos.cost / pos.qty;
        let realized = (unit_price - unit_cost) * sold;

        pos.qty -= sold;
        pos.cost -= unit_cost * sold;
        if pos.qty <= 1e-12 {
            pos.qty = 0.0;
            pos.cost = 0.0;
        }

        if swap.timestamp >= cutoff && realized.is_finite() {
            out.push(Realization {
                pool_id: swap.pool_id.clone(),
                timestamp: swap.timestamp,
                pnl: realized,
            });
        }
    }

    out
}

/// Compute realized PnL, win rate and per-pool PnL over the window.
///
/// Win rate is the share of closing trades that made money. Counting profitable
/// *pools* instead (the old behaviour) rates a trader who won once and lost
/// forty times in the same pool at 100%.
pub fn compute_fifo_pnl(swaps: &[Swap], cutoff: i64) -> (f64, f64, HashMap<String, f64>) {
    let realizations = walk(swaps, cutoff);

    let mut pool_pnl: HashMap<String, f64> = HashMap::new();
    let mut total = 0.0;
    let mut wins = 0usize;

    for r in &realizations {
        total += r.pnl;
        *pool_pnl.entry(r.pool_id.clone()).or_insert(0.0) += r.pnl;
        if r.pnl > 0.0 {
            wins += 1;
        }
    }

    let win_rate = if realizations.is_empty() {
        0.0
    } else {
        wins as f64 / realizations.len() as f64 * 100.0
    };

    (total, win_rate, pool_pnl)
}

/// Cumulative realized-PnL curve over the window, in `buckets` steps.
pub fn compute_pnl_curve(swaps: &[Swap], cutoff: i64, buckets: usize) -> Vec<f64> {
    let buckets = buckets.max(1);
    let now = chrono::Utc::now().timestamp();
    let window = now - cutoff;
    if window <= 0 {
        return vec![0.0; buckets];
    }

    let bucket_size = window as f64 / buckets as f64;
    let mut curve = vec![0.0; buckets];

    for r in walk(swaps, cutoff) {
        let offset = (r.timestamp - cutoff).max(0);
        let bucket = ((offset as f64 / bucket_size) as usize).min(buckets - 1);
        curve[bucket] += r.pnl;
    }

    for i in 1..curve.len() {
        curve[i] += curve[i - 1];
    }

    curve
}
