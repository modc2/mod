// Vault discovery — the deposit-side analogue of the top-traders board.
// Hyperliquid vaults let you deposit USDC and have the vault leader trade it
// (native copy-trading). We pull the full vault universe from the stats CDN,
// drop closed / dust / child sub-vaults, and rank what's left by APR so the UI
// can surface the best vaults to invest in.

use crate::hl::Client;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::sync::Arc;

// Dust floor: vaults below this TVL aren't meaningfully copyable and let a
// lucky micro-vault top an APR ranking. Mirrors the traders board's equity
// floor. ~$10k keeps the board to vaults with real money in them.
const MIN_TVL: f64 = 10_000.0;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Vault {
    pub address: String,
    pub name: String,
    pub leader: String,
    pub apr: f64,            // APR as percent (HL publishes a fraction; ×100 here)
    pub tvl: f64,            // total value locked, USD
    pub age_days: i64,       // since createTimeMillis
    /// Trailing-window APRs: what a deposit made at window start would have
    /// annualized to, in percent. `None` when the basis is too small to divide
    /// by honestly (near-empty vault) or the CDN series is missing.
    #[serde(default)]
    pub apr_24h: Option<f64>,
    #[serde(default)]
    pub apr_7d: Option<f64>,
}

fn f(v: &Value, k: &str) -> f64 {
    v.get(k).and_then(|x| x.as_str()).and_then(|s| s.parse::<f64>().ok()).unwrap_or(0.0)
}

/// PnL realised over one CDN window. `pnls` entries are `[name, [cumulative…]]`
/// samples across the window ("day" spans the last 24h, "week" the last 7d),
/// so the window's PnL is last − first.
fn window_pnl(entry: &Value, name: &str) -> Option<f64> {
    let series = entry.get("pnls")?.as_array()?.iter().find_map(|pair| {
        let p = pair.as_array()?;
        if p.first()?.as_str()? != name { return None; }
        p.get(1)?.as_array()
    })?;
    let first: f64 = series.first()?.as_str()?.parse().ok()?;
    let last: f64 = series.last()?.as_str()?.parse().ok()?;
    Some(last - first)
}

/// Deposit basis below which a window APR is noise: a vault that made $500 on
/// a $600 starting book annualizes to five digits and tells you nothing.
const MIN_APR_BASIS: f64 = 1_000.0;

/// "If you had deposited at window start": window PnL over starting TVL,
/// annualized to percent. Starting TVL is approximated as tvl_now − pnl
/// (per-window flows aren't in the CDN dump).
fn window_apr(tvl_now: f64, pnl: Option<f64>, periods_per_year: f64) -> Option<f64> {
    let pnl = pnl?;
    let basis = tvl_now - pnl;
    if basis < MIN_APR_BASIS { return None; }
    Some(pnl / basis * periods_per_year * 100.0)
}

/// Parse the stats-CDN vault dump, filter to investable vaults, rank by APR.
pub fn parse_vaults_ranked(v: &Value, min_tvl: f64, now_ms: i64) -> Vec<Vault> {
    let arr = match v.as_array() {
        Some(a) => a,
        None => return Vec::new(),
    };
    let mut out: Vec<Vault> = Vec::new();
    for e in arr {
        let apr = e.get("apr").and_then(|x| x.as_f64()).unwrap_or(0.0);
        let Some(s) = e.get("summary") else { continue };
        let addr = s.get("vaultAddress").and_then(|x| x.as_str()).unwrap_or("");
        if !addr.starts_with("0x") || addr.len() != 42 { continue; }
        if s.get("isClosed").and_then(|x| x.as_bool()).unwrap_or(false) { continue; }
        // Child sub-vaults can't be deposited into directly; only normal/parent.
        let rel = s.get("relationship").and_then(|r| r.get("type")).and_then(|x| x.as_str()).unwrap_or("normal");
        if rel == "child" { continue; }
        let tvl = f(s, "tvl");
        if tvl < min_tvl { continue; }
        let created = s.get("createTimeMillis").and_then(|x| x.as_i64()).unwrap_or(now_ms);
        out.push(Vault {
            address: addr.to_lowercase(),
            name: s.get("name").and_then(|x| x.as_str()).unwrap_or("").trim().to_string(),
            leader: s.get("leader").and_then(|x| x.as_str()).unwrap_or("").to_lowercase(),
            apr: apr * 100.0,
            tvl,
            age_days: ((now_ms - created).max(0)) / 86_400_000,
            apr_24h: window_apr(tvl, window_pnl(e, "day"), 365.0),
            apr_7d: window_apr(tvl, window_pnl(e, "week"), 365.0 / 7.0),
        });
    }
    out.sort_by(|a, b| b.apr.partial_cmp(&a.apr).unwrap_or(std::cmp::Ordering::Equal));
    out
}

pub async fn top_vaults(hl: Arc<Client>, min_tvl: Option<f64>, pool: usize) -> anyhow::Result<Vec<Vault>> {
    let raw = hl.vaults().await.unwrap_or(Value::Null);
    let now_ms = chrono::Utc::now().timestamp_millis();
    let mut v = parse_vaults_ranked(&raw, min_tvl.unwrap_or(MIN_TVL), now_ms);
    v.truncate(pool.max(1));
    Ok(v)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn entry(day: &[&str], week: &[&str], tvl: &str) -> Value {
        json!({
            "apr": 0.5,
            "pnls": [["day", day], ["week", week], ["month", []], ["allTime", []]],
            "summary": {
                "name": "t", "vaultAddress": "0x00043d4a2c258892172dc6ce5f62e2e3936ae0dc",
                "leader": "0x9ab020fd91d6909e3d0cbf6c078f8b8163836c06",
                "tvl": tvl, "isClosed": false,
                "relationship": {"type": "normal"}, "createTimeMillis": 0
            }
        })
    }

    #[test]
    fn window_apr_is_annualized_return_on_starting_tvl() {
        // TVL now 101k, made 1k over the day → basis 100k, +1% × 365.
        let v = parse_vaults_ranked(
            &json!([entry(&["0.0", "500.0", "1000.0"], &["0.0", "7000.0"], "101000")]),
            0.0, 0);
        assert_eq!(v.len(), 1);
        let apr24 = v[0].apr_24h.unwrap();
        assert!((apr24 - 365.0).abs() < 1e-6, "got {apr24}");
        // week: +7k on basis 94k, annualized ×(365/7)
        let apr7 = v[0].apr_7d.unwrap();
        let want = 7000.0 / 94000.0 * (365.0 / 7.0) * 100.0;
        assert!((apr7 - want).abs() < 1e-6, "got {apr7} want {want}");
    }

    #[test]
    fn window_apr_refuses_dust_basis_and_missing_series() {
        // Basis under $1k → None, even though the ratio would be huge.
        let v = parse_vaults_ranked(&json!([entry(&["0.0", "500.0"], &[], "1200")]), 0.0, 0);
        assert_eq!(v[0].apr_24h, None, "basis 700 must not annualize");
        assert_eq!(v[0].apr_7d, None, "empty series must not annualize");
    }
}
