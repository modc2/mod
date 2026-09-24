//! The trades view: the simple front door. Selected traders, the trades they
//! are proposing, and one score function that ranks each trade by its
//! potential-ROI. The trader board and the tape both come from the fleet's
//! copytensor module (local, same box) — this file only joins and scores.
//!
//! The score function is the point, so it is one pure fn, printed verbatim on
//! the page and unit-tested here. A trade scores high when the trader behind
//! it has a high ROI over the chosen window and the trade is a large share of
//! that trader's own book (conviction). 50 is neutral; the output is 0..100.

use axum::{extract::Query, response::IntoResponse, Json};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::sync::OnceLock;
use std::time::Duration;

pub const TRADES_HTML: &str = include_str!("trades.html");

/// The formula, exactly as `score()` computes it — shown on the page.
pub const SCORE_EXPR: &str =
    "score = clamp(0, 100, 50 + 35·tanh(roi / 40) + 15·tanh(4·conviction))";
pub const SCORE_NOTE: &str = "roi = the trader's return % over the window · \
conviction = this trade's τ value as a share of the trader's whole book. \
A trade scores high when a trader who has been right lately moves big.";

/// Potential-ROI score for one trade. Pure; clamped to 0..100.
pub fn score(roi_pct: f64, conviction: f64) -> f64 {
    let roi = if roi_pct.is_finite() { roi_pct } else { 0.0 };
    let conv = if conviction.is_finite() { conviction.max(0.0) } else { 0.0 };
    let s = 50.0 + 35.0 * (roi / 40.0).tanh() + 15.0 * (4.0 * conv).tanh();
    s.clamp(0.0, 100.0)
}

fn client() -> &'static reqwest::Client {
    static C: OnceLock<reqwest::Client> = OnceLock::new();
    C.get_or_init(|| {
        reqwest::Client::builder()
            .timeout(Duration::from_secs(20))
            .build()
            .unwrap_or_else(|_| reqwest::Client::new())
    })
}

/// Where the trader board and the tape live. Copytensor runs on this box;
/// ARENA_COPYTENSOR_URL overrides.
fn source() -> String {
    std::env::var("ARENA_COPYTENSOR_URL")
        .unwrap_or_else(|_| "http://127.0.0.1:50150".into())
}

async fn fetch(path: &str) -> Result<Value, String> {
    let url = format!("{}{}", source(), path);
    let resp = client()
        .get(&url)
        .send()
        .await
        .map_err(|e| format!("copytensor unreachable at {}: {e}", source()))?;
    if !resp.status().is_success() {
        return Err(format!("copytensor answered {} on {path}", resp.status()));
    }
    resp.json::<Value>().await.map_err(|e| format!("bad json from {path}: {e}"))
}

/// GET /trades?days=7&hours=48 — the whole page's data in one call: the
/// trader board (windowed ROI) joined onto the tape, every trade scored.
/// Selection is the browser's business: everything comes back, the page
/// filters to the ticked traders.
pub async fn data(Query(q): Query<HashMap<String, String>>) -> impl IntoResponse {
    let days: u32 = q.get("days").and_then(|v| v.parse().ok()).unwrap_or(7).clamp(1, 30);
    let hours: u32 = q.get("hours").and_then(|v| v.parse().ok()).unwrap_or(96).clamp(1, 168);

    // Deep board: the tape is wide and a trade only scores if its trader's
    // window ROI is known, so the join wants everyone copytensor ranks.
    let board = fetch(&format!("/leaderboard?days={days}&top=500")).await;
    let tape = fetch(&format!("/flows?hours={hours}&limit=300")).await;

    let (board, tape) = match (board, tape) {
        (Ok(b), Ok(t)) => (b, t),
        (Err(e), _) | (_, Err(e)) => {
            return Json(json!({ "error": e, "fn": { "expr": SCORE_EXPR, "note": SCORE_NOTE } }))
        }
    };

    // The board: keep real books (baseline established, ≥ 1 τ) — an emptied
    // dust wallet reads ±extreme % on nothing and would top every ranking.
    let mut traders: Vec<Value> = Vec::new();
    let mut by_ss58: HashMap<String, (f64, f64, String)> = HashMap::new();
    for row in board.as_array().cloned().unwrap_or_default() {
        let ss58 = row["ss58"].as_str().unwrap_or_default().to_string();
        let stake = row["total_stake_tao"].as_f64().unwrap_or(0.0);
        let roi = row["pnl_pct"].as_f64().unwrap_or(0.0);
        let baseline = row["baseline"].as_bool().unwrap_or(false);
        // A wallet emptied and refilled reads an astronomic % on nothing —
        // copytensor's own front page caps at 500%, same rule here.
        if ss58.is_empty() || !baseline || stake < 1.0 || !roi.is_finite() || roi.abs() > 500.0 {
            continue;
        }
        let label = row["label"]
            .as_str()
            .map(str::to_string)
            .unwrap_or_else(|| format!("{}…{}", &ss58[..6], &ss58[ss58.len() - 4..]));
        by_ss58.insert(ss58.clone(), (roi, stake, label.clone()));
        traders.push(json!({
            "ss58": ss58, "label": label, "roi": roi,
            "stake_tao": stake, "subnets": row["num_subnets"],
        }));
    }
    traders.sort_by(|a, b| {
        b["roi"].as_f64().partial_cmp(&a["roi"].as_f64()).unwrap_or(std::cmp::Ordering::Equal)
    });

    // The tape, joined and scored. Only trades from traders on the board —
    // a trade with no window ROI behind it has nothing to score.
    let mut trades: Vec<Value> = Vec::new();
    for f in tape["flows"].as_array().cloned().unwrap_or_default() {
        let ss58 = f["ss58"].as_str().unwrap_or_default();
        let Some((roi, stake, label)) = by_ss58.get(ss58) else { continue };
        let tao = f["tao_value"].as_f64().unwrap_or(0.0);
        let conviction = if *stake > 0.0 { tao / stake } else { 0.0 };
        let s = score(*roi, conviction);
        trades.push(json!({
            "ts": f["ts"], "ss58": ss58, "trader": label,
            "side": f["side"], "netuid": f["netuid"], "subnet": f["name"],
            "tao_value": tao, "price": f["price"],
            "roi": roi, "conviction": conviction, "score": s,
        }));
    }
    trades.sort_by(|a, b| {
        b["score"].as_f64().partial_cmp(&a["score"].as_f64()).unwrap_or(std::cmp::Ordering::Equal)
    });

    // The chips list stays legible: everyone who actually traded this
    // window, plus the top 30 by ROI — every trade's trader is pickable.
    let active: std::collections::HashSet<String> = trades
        .iter()
        .filter_map(|t| t["ss58"].as_str().map(str::to_string))
        .collect();
    let mut rank = 0usize;
    traders.retain(|t| {
        rank += 1;
        rank <= 30 || active.contains(t["ss58"].as_str().unwrap_or_default())
    });

    Json(json!({
        "fn": { "expr": SCORE_EXPR, "note": SCORE_NOTE },
        "window_days": days, "tape_hours": hours,
        "traders": traders, "trades": trades,
    }))
}

#[cfg(test)]
mod tests {
    use super::score;

    #[test]
    fn neutral_at_zero() {
        assert!((score(0.0, 0.0) - 50.0).abs() < 1e-9);
    }

    #[test]
    fn monotonic_in_roi_and_conviction() {
        assert!(score(40.0, 0.1) > score(10.0, 0.1));
        assert!(score(10.0, 0.5) > score(10.0, 0.05));
        assert!(score(-40.0, 0.1) < score(0.0, 0.1));
    }

    #[test]
    fn clamped_and_finite_on_garbage() {
        assert!(score(1e12, 1e12) <= 100.0);
        assert!(score(-1e12, 0.0) >= 0.0);
        let s = score(f64::NAN, f64::INFINITY);
        assert!(s.is_finite() && (0.0..=100.0).contains(&s));
        // a negative conviction (bad upstream data) never lowers a score
        assert!((score(0.0, -5.0) - 50.0).abs() < 1e-9);
    }
}
