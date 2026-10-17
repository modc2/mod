//! The venue adapter — the only file that knows __VENUE__ exists.
//! Replace these bodies with real calls; keep the return shapes:
//!   markets      → { markets: [{ symbol, price, volume24h }] }
//!   top_traders  → { traders: [{ address, pnl, volume, trades, winRate }] }
//!   analyze_trader → { address, pnl, volume, trades, fills: [...] }

use serde_json::{json, Value};

pub const VENUE: &str = "__VENUE__";

pub async fn markets(limit: usize) -> Result<Value, String> {
    Ok(json!({ "markets": [], "limit": limit, "note": "venue adapter not wired yet" }))
}

pub async fn top_traders(days: u32, limit: usize, sort: &str) -> Result<Value, String> {
    Ok(json!({ "traders": [], "days": days, "limit": limit, "sort": sort,
               "note": "venue adapter not wired yet" }))
}

pub async fn analyze_trader(address: &str, days: u32) -> Result<Value, String> {
    Ok(json!({ "address": address.to_lowercase(), "days": days, "fills": [],
               "note": "venue adapter not wired yet" }))
}
