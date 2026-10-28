//! The venue adapter — dYdX v4 over its public indexer (no key, no auth).
//! The only file in the API that knows dYdX exists; main.rs is the
//! tradekit/1 skeleton shared with every scaffolded venue.
//!
//!   markets        → { markets: [{ symbol, price, volume24h, openInterest, maxLeverage }] }
//!   top_traders    → { traders: [{ address, pnl, equity, volume, trades }] }
//!   analyze_trader → { address, pnl, equity, volume, trades, positions, fills }
//!
//! dYdX publishes no trader leaderboard, so top_traders ranks a WATCHLIST:
//! $DYDX_WATCHLIST (comma-separated) plus ~/.mod/dydx/watchlist.json.

use serde_json::{json, Value};
use std::time::Duration;

pub const VENUE: &str = "dYdX";

fn indexer() -> String {
    std::env::var("DYDX_INDEXER").unwrap_or_else(|_| "https://indexer.dydx.trade/v4".into())
}

async fn get(path: &str) -> Result<Value, String> {
    let url = format!("{}{}", indexer(), path);
    let r = reqwest::Client::new()
        .get(&url)
        .timeout(Duration::from_secs(20))
        .send()
        .await
        .map_err(|e| format!("indexer: {e}"))?;
    let status = r.status();
    let body: Value = r.json().await.map_err(|e| format!("indexer body: {e}"))?;
    if !status.is_success() {
        let msg = body["errors"][0]["msg"].as_str().unwrap_or("request failed");
        return Err(format!("indexer {status}: {msg}"));
    }
    Ok(body)
}

fn f(v: &Value) -> f64 {
    v.as_str().and_then(|s| s.parse().ok()).or_else(|| v.as_f64()).unwrap_or(0.0)
}

pub async fn markets(limit: usize) -> Result<Value, String> {
    let body = get("/perpetualMarkets").await?;
    let mut rows: Vec<Value> = body["markets"]
        .as_object()
        .map(|m| m.values().filter(|x| x["status"] == "ACTIVE").map(|x| {
            let imf = f(&x["initialMarginFraction"]);
            json!({
                "symbol": x["ticker"], "price": f(&x["oraclePrice"]),
                "volume24h": f(&x["volume24H"]), "openInterest": f(&x["openInterest"]),
                "maxLeverage": if imf > 0.0 { (1.0 / imf).round() } else { 0.0 },
            })
        }).collect())
        .unwrap_or_default();
    rows.sort_by(|a, b| f(&b["volume24h"]).partial_cmp(&f(&a["volume24h"])).unwrap_or(std::cmp::Ordering::Equal));
    rows.truncate(limit.max(1));
    Ok(json!({ "markets": rows }))
}

fn watchlist() -> Vec<String> {
    let mut out: Vec<String> = std::env::var("DYDX_WATCHLIST").unwrap_or_default()
        .split(',').map(|s| s.trim().to_string()).filter(|s| s.starts_with("dydx1")).collect();
    if let Ok(home) = std::env::var("HOME") {
        let p = format!("{home}/.mod/dydx/watchlist.json");
        if let Ok(txt) = std::fs::read_to_string(p) {
            if let Ok(Value::Array(a)) = serde_json::from_str::<Value>(&txt) {
                out.extend(a.iter().filter_map(|v| v.as_str().map(String::from)));
            }
        }
    }
    out.sort();
    out.dedup();
    out
}

pub async fn top_traders(days: u32, limit: usize, sort: &str) -> Result<Value, String> {
    let list = watchlist();
    let mut rows = Vec::new();
    let mut errors = Vec::new();
    for addr in &list {
        match analyze_trader(addr, days).await {
            Ok(mut v) => {
                if let Some(o) = v.as_object_mut() {
                    o.remove("fills");
                    o.remove("positions");
                }
                rows.push(v)
            }
            Err(e) => errors.push(json!({ "address": addr, "error": e })),
        }
    }
    let key = match sort { "volume" | "equity" | "trades" => sort, _ => "pnl" };
    rows.sort_by(|a, b| f(&b[key]).partial_cmp(&f(&a[key])).unwrap_or(std::cmp::Ordering::Equal));
    rows.truncate(limit.max(1));
    Ok(json!({
        "traders": rows, "days": days, "sort": key, "watched": list.len(), "errors": errors,
        "note": if list.is_empty() {
            "dYdX publishes no leaderboard — add addresses with `m dydx/watch address=dydx1…`"
        } else { "ranked over the watchlist (dYdX publishes no leaderboard)" },
    }))
}

pub async fn analyze_trader(address: &str, days: u32) -> Result<Value, String> {
    if !address.starts_with("dydx1") {
        return Err("address must be a dydx1… bech32 address".into());
    }
    let acct = get(&format!("/addresses/{address}/subaccountNumber/0")).await?;
    let sub = &acct["subaccount"];
    let hours = (days.max(1) * 24).min(1000);
    let hist = get(&format!("/historical-pnl?address={address}&subaccountNumber=0&limit={hours}")).await?;
    // newest first; window pnl = latest totalPnl − oldest point in window
    let pts = hist["historicalPnl"].as_array().cloned().unwrap_or_default();
    let pnl = match (pts.first(), pts.last()) {
        (Some(new), Some(old)) => f(&new["totalPnl"]) - f(&old["totalPnl"]),
        _ => 0.0,
    };
    let fills_body = get(&format!("/fills?address={address}&subaccountNumber=0&limit=100")).await?;
    let cutoff = chrono_cutoff(days);
    let fills: Vec<Value> = fills_body["fills"].as_array().cloned().unwrap_or_default()
        .into_iter().filter(|x| x["createdAt"].as_str().map_or(false, |t| t >= cutoff.as_str())).collect();
    let volume: f64 = fills.iter().map(|x| f(&x["size"]) * f(&x["price"])).sum();
    let positions: Vec<Value> = sub["openPerpetualPositions"].as_object()
        .map(|m| m.values().map(|p| json!({
            "symbol": p["market"], "side": p["side"], "size": f(&p["size"]),
            "entryPrice": f(&p["entryPrice"]), "unrealizedPnl": f(&p["unrealizedPnl"]),
        })).collect())
        .unwrap_or_default();
    Ok(json!({
        "address": address, "days": days, "pnl": pnl, "equity": f(&sub["equity"]),
        "volume": volume, "trades": fills.len(), "positions": positions,
        "fills": fills.iter().take(25).map(|x| json!({
            "ts": x["createdAt"], "symbol": x["market"], "side": x["side"],
            "size": f(&x["size"]), "price": f(&x["price"]), "fee": f(&x["fee"]),
        })).collect::<Vec<_>>(),
        "fillsCapped": fills_body["fills"].as_array().map_or(0, |a| a.len()) >= 100,
    }))
}

/// ISO-8601 timestamp `days` ago — string-comparable with the indexer's
/// createdAt (same format, UTC, ms precision).
fn chrono_cutoff(days: u32) -> String {
    let secs = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs()).unwrap_or(0).saturating_sub(days as u64 * 86_400);
    // civil-from-days (Howard Hinnant), no chrono dependency
    let z = (secs / 86_400) as i64 + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = yoe + era * 400 + if m <= 2 { 1 } else { 0 };
    let s = secs % 86_400;
    format!("{y:04}-{m:02}-{d:02}T{:02}:{:02}:{:02}.000Z", s / 3600, (s / 60) % 60, s % 60)
}
