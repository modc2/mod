// Index analytics — score an index's legs over an N-day window and
// produce a synthetic backtest by weight-summing each leg's daily PnL.

use crate::hl::{parse_fills, Client};
use crate::store::Index;
use crate::traders::TopTrader;
use futures::stream::{FuturesUnordered, StreamExt};
use serde::Serialize;
use serde_json::Value;
use std::collections::BTreeMap;
use std::sync::Arc;

#[derive(Debug, Serialize)]
pub struct LegPerf {
    pub address: String,
    pub weight: f64,
    pub volume: f64,
    pub pnl: f64,
    pub win_rate: f64,
    pub trades: usize,
}

#[derive(Debug, Serialize)]
pub struct IndexPerf {
    pub id: String,
    pub name: String,
    pub days: u32,
    pub total_pnl: f64,
    pub weighted_pnl: f64,
    pub legs: Vec<LegPerf>,
    pub daily: Vec<(i64, f64)>,        // (day_unix, weighted_pnl)
}

pub async fn perf(hl: Arc<Client>, idx: &Index, days_override: Option<u32>) -> anyhow::Result<IndexPerf> {
    let days = days_override.unwrap_or(idx.days_window.max(7));
    let cutoff_ms = chrono::Utc::now().timestamp_millis() - (days as i64) * 86_400_000;

    let mut tasks = FuturesUnordered::new();
    for leg in idx.legs.clone() {
        let hl = hl.clone();
        tasks.push(tokio::spawn(async move {
            let v = hl.user_fills_by_time(&leg.address, cutoff_ms)
                .await.unwrap_or(Value::Null);
            (leg, parse_fills(&v))
        }));
    }

    let mut legs_perf = Vec::new();
    let mut weighted_daily: BTreeMap<i64, f64> = BTreeMap::new();
    let mut total_pnl = 0.0f64;
    let mut weighted_pnl = 0.0f64;

    while let Some(res) = tasks.next().await {
        let Ok((leg, fills)) = res else { continue };
        let mut volume = 0.0f64;
        let mut pnl = 0.0f64;
        let mut wins = 0usize;
        let mut realised = 0usize;
        let mut daily: BTreeMap<i64, f64> = BTreeMap::new();
        for f in &fills {
            let px: f64 = f.px.parse().unwrap_or(0.0);
            let sz: f64 = f.sz.parse().unwrap_or(0.0);
            let cp: f64 = f.closed_pnl.parse().unwrap_or(0.0);
            let fee: f64 = f.fee.parse().unwrap_or(0.0);
            volume += px * sz;
            pnl += cp - fee;
            if cp != 0.0 {
                realised += 1;
                if cp > 0.0 { wins += 1; }
            }
            let day = f.time / 86_400_000;
            *daily.entry(day).or_insert(0.0) += cp - fee;
        }
        let win_rate = if realised == 0 { -1.0 } else { (wins as f64 / realised as f64) * 100.0 };
        for (d, p) in &daily {
            *weighted_daily.entry(*d).or_insert(0.0) += p * leg.weight;
        }
        total_pnl += pnl;
        weighted_pnl += pnl * leg.weight;
        legs_perf.push(LegPerf {
            address: leg.address.clone(),
            weight: leg.weight,
            volume, pnl, win_rate,
            trades: fills.len(),
        });
    }

    let daily: Vec<(i64, f64)> = weighted_daily.into_iter().collect();

    Ok(IndexPerf {
        id: idx.id.clone(),
        name: idx.name.clone(),
        days,
        total_pnl,
        weighted_pnl,
        legs: legs_perf,
        daily,
    })
}

// Build "auto" index from current top traders.
pub fn auto_legs(top: &[TopTrader], take: usize) -> Vec<crate::store::IndexLeg> {
    let take = take.max(1).min(top.len());
    let slice = &top[..take];
    let pos: Vec<&TopTrader> = slice.iter().filter(|t| t.pnl > 0.0).collect();
    let total: f64 = pos.iter().map(|t| t.pnl).sum();
    if total <= 0.0 || pos.is_empty() {
        return slice.iter().map(|t| crate::store::IndexLeg {
            address: t.address.clone(),
            weight: 1.0 / slice.len() as f64,
        }).collect();
    }
    pos.into_iter().map(|t| crate::store::IndexLeg {
        address: t.address.clone(),
        weight: t.pnl / total,
    }).collect()
}
