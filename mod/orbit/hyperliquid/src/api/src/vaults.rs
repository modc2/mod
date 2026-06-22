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
}

fn f(v: &Value, k: &str) -> f64 {
    v.get(k).and_then(|x| x.as_str()).and_then(|s| s.parse::<f64>().ok()).unwrap_or(0.0)
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
