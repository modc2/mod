/// Compute composite score for a trader
/// Higher = better trader (penalizes MEV bots)
pub fn compute_score(
    volume: f64,
    pnl: f64,
    win_rate: f64,
    swap_count: u32,
    is_mev: bool,
) -> f64 {
    if volume == 0.0 {
        return 0.0;
    }

    // Normalized components (0-100 scale each)
    let volume_score = (volume.ln().max(0.0) / 15.0) * 100.0; // ln($3.2M) ≈ 15
    let pnl_score = if pnl > 0.0 {
        (pnl / volume * 100.0).min(100.0) // % return on volume, capped at 100%
    } else {
        (pnl / volume * 50.0).max(-50.0) // Negative but less penalty
    };
    let winrate_score = win_rate; // Already 0-100
    let activity_score = ((swap_count as f64).ln().max(0.0) / 7.0) * 100.0; // ln(1000) ≈ 7

    // Weighted composite
    let raw = volume_score * 0.25 + pnl_score * 0.35 + winrate_score * 0.25 + activity_score * 0.15;

    // Penalize MEV bots
    if is_mev {
        raw * 0.3
    } else {
        raw
    }
}
