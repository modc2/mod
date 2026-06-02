//! High-level Hyperliquid action builders + signed POST to /exchange.
//!
//! This is the "thing actually trades" layer. The signer module knows how to
//! produce a digest; this module knows how to:
//!   - resolve a coin name to its perp asset index from /info meta,
//!   - round price/size to the lot/tick conventions HL enforces,
//!   - build the canonical L1 action JSON (order/cancel/modify/leverage/vault/…),
//!   - sign it with the backend signer keyed by the user's EOA, and
//!   - POST it to /exchange in the {action, nonce, signature, vaultAddress?}
//!     envelope HL expects.
//!
//! All sizing math runs in f64 → string at HL's required decimals. The hashed
//! payload sees the string representation, so as long as we format once and
//! reuse the same string for the digest and the body, there's no drift.

use anyhow::{anyhow, Context, Result};
use parking_lot::RwLock;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::sync::Arc;
use std::time::{Duration, Instant};

use crate::hl::Client;
use crate::sign_l1::{l1_digest, signature_to_rsv};
use crate::sign_user;
use crate::signer::SignerStore;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AssetSpec {
    pub coin: String,
    pub asset: u32,
    pub sz_decimals: u32,
    pub max_leverage: u32,
    /// `true` for spot universe assets (offset by 10_000 in the asset id).
    pub is_spot: bool,
}

/// Cached perp+spot universe. Refreshes opportunistically.
pub struct MetaCache {
    hl: Arc<Client>,
    inner: RwLock<Option<(Instant, HashMap<String, AssetSpec>)>>,
}

impl MetaCache {
    pub fn new(hl: Arc<Client>) -> Self { Self { hl, inner: RwLock::new(None) } }

    pub async fn get(&self, coin: &str) -> Result<AssetSpec> {
        let key = coin.to_ascii_uppercase();
        if let Some((t, m)) = &*self.inner.read() {
            if t.elapsed() < Duration::from_secs(120) {
                if let Some(s) = m.get(&key) { return Ok(s.clone()); }
            }
        }
        self.refresh().await?;
        let g = self.inner.read();
        g.as_ref()
            .and_then(|(_, m)| m.get(&key).cloned())
            .ok_or_else(|| anyhow!("unknown coin: {coin}"))
    }

    pub async fn refresh(&self) -> Result<()> {
        let perp = self.hl.meta_and_ctxs().await.context("fetch meta")?;
        let mut map: HashMap<String, AssetSpec> = HashMap::new();
        if let Some(universe) = perp.get(0).and_then(|v| v.get("universe")).and_then(|u| u.as_array()) {
            for (i, a) in universe.iter().enumerate() {
                let name = a.get("name").and_then(|x| x.as_str()).unwrap_or("").to_string();
                if name.is_empty() { continue; }
                map.insert(name.to_ascii_uppercase(), AssetSpec {
                    coin: name,
                    asset: i as u32,
                    sz_decimals: a.get("szDecimals").and_then(|x| x.as_u64()).unwrap_or(0) as u32,
                    max_leverage: a.get("maxLeverage").and_then(|x| x.as_u64()).unwrap_or(0) as u32,
                    is_spot: false,
                });
            }
        }
        // Spot universe — asset ids offset by 10_000 in the /exchange API.
        if let Ok(spot) = self.hl.info(json!({"type":"spotMetaAndAssetCtxs"})).await {
            if let Some(universe) = spot.get(0).and_then(|v| v.get("universe")).and_then(|u| u.as_array()) {
                for (i, a) in universe.iter().enumerate() {
                    let name = a.get("name").and_then(|x| x.as_str()).unwrap_or("").to_string();
                    if name.is_empty() { continue; }
                    map.insert(name.to_ascii_uppercase(), AssetSpec {
                        coin: name,
                        asset: 10_000 + i as u32,
                        sz_decimals: a.get("szDecimals").and_then(|x| x.as_u64()).unwrap_or(0) as u32,
                        max_leverage: 0,
                        is_spot: true,
                    });
                }
            }
        }
        *self.inner.write() = Some((Instant::now(), map));
        Ok(())
    }
}

// ─── Number formatting (must match what HL expects to hash) ─────────────────

/// Format a size to `sz_decimals` decimals with no trailing zeros. HL rejects
/// sizes outside that precision because the orderbook is sz_decimals-quantized.
pub fn fmt_size(sz: f64, sz_decimals: u32) -> String {
    let s = format!("{:.*}", sz_decimals as usize, sz);
    trim_trailing_zeros(&s)
}

/// Format a price for HL: max 5 significant figures AND max
/// (MAX_DECIMALS - sz_decimals) decimal places (MAX_DECIMALS is 6 for perps,
/// 8 for spot). Integer prices (e.g. BTC at 95000) are allowed regardless of
/// the sig-fig rule, which is why we check the integer guard first.
pub fn fmt_price(px: f64, sz_decimals: u32, is_spot: bool) -> String {
    if px <= 0.0 || !px.is_finite() {
        return "0".to_string();
    }
    let max_decimals: u32 = if is_spot { 8 } else { 6 };
    let max_dec_for_asset: u32 = max_decimals.saturating_sub(sz_decimals);
    // Round to 5 sig figs first.
    let mantissa_decimals = if px >= 1.0 {
        let int_digits = px.log10().floor() as i32 + 1;
        (5 - int_digits).max(0) as u32
    } else {
        // For px < 1, leading zeros after decimal don't count as sig figs.
        // We can let the decimal-cap handle precision.
        max_dec_for_asset
    };
    let dec = mantissa_decimals.min(max_dec_for_asset);
    let s = format!("{:.*}", dec as usize, px);
    trim_trailing_zeros(&s)
}

fn trim_trailing_zeros(s: &str) -> String {
    if !s.contains('.') { return s.to_string(); }
    let trimmed = s.trim_end_matches('0');
    let trimmed = trimmed.trim_end_matches('.');
    if trimmed.is_empty() || trimmed == "-" { "0".to_string() } else { trimmed.to_string() }
}

// ─── Action shape ───────────────────────────────────────────────────────────

#[derive(Debug, Clone, Copy)]
pub enum OrderSide { Buy, Sell }

#[derive(Debug, Clone)]
pub struct OrderRequest {
    pub coin: String,
    pub side: OrderSide,
    /// Limit price. For market orders set `tif = "Ioc"` and use a slippage-padded
    /// price (e.g. mid * 1.05 for buys, mid * 0.95 for sells).
    pub price: f64,
    pub size: f64,
    pub reduce_only: bool,
    pub tif: TimeInForce,
    pub cloid: Option<String>,
}

#[derive(Debug, Clone, Copy)]
pub enum TimeInForce { Gtc, Ioc, Alo }

impl TimeInForce {
    fn as_str(self) -> &'static str {
        match self { Self::Gtc => "Gtc", Self::Ioc => "Ioc", Self::Alo => "Alo" }
    }
}

/// Build the L1 `order` action for a single order. Multiple orders can be
/// batched by calling this with extra OrderRequest entries — for now we
/// expose the single-order variant since copy-trade mirrors one fill at a
/// time anyway.
pub fn build_order_action(req: &OrderRequest, spec: &AssetSpec) -> Result<Value> {
    if req.size <= 0.0 { return Err(anyhow!("size must be > 0")); }
    if req.price <= 0.0 { return Err(anyhow!("price must be > 0")); }
    let px_str = fmt_price(req.price, spec.sz_decimals, spec.is_spot);
    let sz_str = fmt_size(req.size, spec.sz_decimals);
    let mut order = json!({
        "a": spec.asset,
        "b": matches!(req.side, OrderSide::Buy),
        "p": px_str,
        "s": sz_str,
        "r": req.reduce_only,
        "t": { "limit": { "tif": req.tif.as_str() } },
    });
    if let Some(c) = &req.cloid {
        order.as_object_mut().unwrap().insert("c".to_string(), Value::String(c.clone()));
    }
    Ok(json!({
        "type": "order",
        "orders": [order],
        "grouping": "na",
    }))
}

#[derive(Debug, Clone)]
pub struct CancelRequest { pub coin: String, pub oid: u64 }

pub fn build_cancel_action(reqs: &[CancelRequest], meta: &HashMap<String, AssetSpec>) -> Result<Value> {
    let mut cancels: Vec<Value> = Vec::with_capacity(reqs.len());
    for r in reqs {
        let s = meta.get(&r.coin.to_ascii_uppercase())
            .ok_or_else(|| anyhow!("unknown coin: {}", r.coin))?;
        cancels.push(json!({ "a": s.asset, "o": r.oid }));
    }
    Ok(json!({ "type": "cancel", "cancels": cancels }))
}

#[derive(Debug, Clone)]
pub struct CancelByCloidRequest { pub coin: String, pub cloid: String }

pub fn build_cancel_by_cloid_action(reqs: &[CancelByCloidRequest], meta: &HashMap<String, AssetSpec>) -> Result<Value> {
    let mut cancels: Vec<Value> = Vec::with_capacity(reqs.len());
    for r in reqs {
        let s = meta.get(&r.coin.to_ascii_uppercase())
            .ok_or_else(|| anyhow!("unknown coin: {}", r.coin))?;
        cancels.push(json!({ "asset": s.asset, "cloid": r.cloid }));
    }
    Ok(json!({ "type": "cancelByCloid", "cancels": cancels }))
}

pub fn build_modify_action(oid: u64, req: &OrderRequest, spec: &AssetSpec) -> Result<Value> {
    let px_str = fmt_price(req.price, spec.sz_decimals, spec.is_spot);
    let sz_str = fmt_size(req.size, spec.sz_decimals);
    Ok(json!({
        "type": "modify",
        "oid": oid,
        "order": {
            "a": spec.asset,
            "b": matches!(req.side, OrderSide::Buy),
            "p": px_str,
            "s": sz_str,
            "r": req.reduce_only,
            "t": { "limit": { "tif": req.tif.as_str() } },
        },
    }))
}

pub fn build_update_leverage_action(asset: u32, is_cross: bool, leverage: u32) -> Value {
    json!({
        "type": "updateLeverage",
        "asset": asset,
        "isCross": is_cross,
        "leverage": leverage,
    })
}

pub fn build_update_isolated_margin_action(asset: u32, is_buy: bool, ntli_usd: i64) -> Value {
    // `ntli` is the signed USDC amount in microUSDC (×1e6). Positive adds
    // margin, negative removes it.
    json!({
        "type": "updateIsolatedMargin",
        "asset": asset,
        "isBuy": is_buy,
        "ntli": ntli_usd,
    })
}

/// `vaultTransfer` — deposit or withdraw USDC against a vault you own.
/// `is_deposit=true` moves USD from the caller into the vault; `false` moves
/// it out.
pub fn build_vault_transfer_action(vault: &str, is_deposit: bool, usd_micro: u64) -> Value {
    json!({
        "type": "vaultTransfer",
        "vaultAddress": vault,
        "isDeposit": is_deposit,
        "usd": usd_micro,
    })
}

pub fn build_create_vault_action(name: &str, description: &str, initial_usd_micro: u64, nonce: u64) -> Value {
    json!({
        "type": "createVault",
        "name": name,
        "description": description,
        "initialUsd": initial_usd_micro,
        "nonce": nonce,
    })
}

pub fn build_set_referrer_action(code: &str) -> Value {
    json!({ "type": "setReferrer", "code": code })
}

pub fn build_schedule_cancel_action(time_ms: Option<u64>) -> Value {
    match time_ms {
        Some(t) => json!({ "type": "scheduleCancel", "time": t }),
        None    => json!({ "type": "scheduleCancel" }),
    }
}

// ─── Sign + POST /exchange ──────────────────────────────────────────────────

/// Sign an L1 action with the backend agent key for `eoa` and POST it to
/// `/exchange`. `vault_address` is set when the agent is acting via a vault
/// (vault traders, not vault transfers — those use `None` and put the vault
/// address in the action payload itself).
pub async fn post_l1_action(
    http: &reqwest::Client,
    hl: &Client,
    signer_store: &SignerStore,
    eoa: &str,
    action: Value,
    nonce: u64,
    vault_address: Option<&str>,
) -> Result<Value> {
    let is_mainnet = !hl.testnet;
    let digest = l1_digest(&action, nonce, vault_address, is_mainnet)?;
    let sig = signer_store.sign_digest(eoa, &digest)?;
    let mut body = json!({
        "action": action,
        "nonce": nonce,
        "signature": signature_to_rsv(&sig),
    });
    if let Some(v) = vault_address {
        body.as_object_mut().unwrap().insert("vaultAddress".to_string(), Value::String(v.to_string()));
    }
    let r = http.post(&hl.exchange_url).json(&body).send().await.context("post /exchange")?;
    let status = r.status();
    let text = r.text().await.unwrap_or_default();
    let json: Value = serde_json::from_str(&text).unwrap_or_else(|_| json!({"raw": text}));
    tracing::info!(%status, response = %json, "POST /exchange (L1) response");
    if !status.is_success() { return Err(anyhow!("/exchange HTTP {status}: {json}")); }
    // HL returns {status: "ok", response: ...} or {status: "err", response: "..."}.
    if json.get("status").and_then(|s| s.as_str()) == Some("err") {
        return Err(anyhow!("/exchange err: {}", json.get("response").map(|x| x.to_string()).unwrap_or_default()));
    }
    Ok(json)
}

/// Sign a user-signed action with the backend agent key for `eoa` and POST
/// it to `/exchange`. The action payload itself carries `hyperliquidChain`
/// and `signatureChainId` — we just sign over the precomputed digest and
/// wrap.
pub async fn post_user_action(
    http: &reqwest::Client,
    hl: &Client,
    signer_store: &SignerStore,
    eoa: &str,
    action: Value,
    digest: [u8; 32],
    nonce: u64,
) -> Result<Value> {
    let sig = signer_store.sign_digest(eoa, &digest)?;
    let body = json!({
        "action": action,
        "nonce": nonce,
        "signature": sign_user::signature_to_rsv(&sig),
    });
    let r = http.post(&hl.exchange_url).json(&body).send().await.context("post /exchange")?;
    let status = r.status();
    let text = r.text().await.unwrap_or_default();
    let json: Value = serde_json::from_str(&text).unwrap_or_else(|_| json!({"raw": text}));
    tracing::info!(%status, response = %json, "POST /exchange (user) response");
    if !status.is_success() { return Err(anyhow!("/exchange HTTP {status}: {json}")); }
    if json.get("status").and_then(|s| s.as_str()) == Some("err") {
        return Err(anyhow!("/exchange err: {}", json.get("response").map(|x| x.to_string()).unwrap_or_default()));
    }
    Ok(json)
}

// ─── Convenience wrappers ───────────────────────────────────────────────────

/// Place a (limit or marketable) order. For market behavior, set
/// `tif = Ioc` and price the request with slippage padding.
pub async fn place_order(
    http: &reqwest::Client,
    hl: &Client,
    signer_store: &SignerStore,
    meta: &MetaCache,
    eoa: &str,
    req: OrderRequest,
    vault_address: Option<&str>,
) -> Result<Value> {
    let spec = meta.get(&req.coin).await?;
    let action = build_order_action(&req, &spec)?;
    let nonce = chrono::Utc::now().timestamp_millis() as u64;
    post_l1_action(http, hl, signer_store, eoa, action, nonce, vault_address).await
}

/// Place a market order by picking a slippage-padded limit price from the
/// current mid. `slippage_bps` is one-sided; for a buy the price is
/// `mid * (1 + slippage_bps/10000)`.
pub async fn place_market_order(
    http: &reqwest::Client,
    hl: &Client,
    signer_store: &SignerStore,
    meta: &MetaCache,
    eoa: &str,
    coin: &str,
    side: OrderSide,
    size: f64,
    slippage_bps: u32,
    reduce_only: bool,
    vault_address: Option<&str>,
) -> Result<Value> {
    let mids = hl.all_mids().await?;
    let mid = mids.get(coin).and_then(|v| v.as_str())
        .and_then(|s| s.parse::<f64>().ok())
        .ok_or_else(|| anyhow!("no mid for {coin}"))?;
    let slip = slippage_bps as f64 / 10_000.0;
    let px = match side {
        OrderSide::Buy  => mid * (1.0 + slip),
        OrderSide::Sell => mid * (1.0 - slip).max(0.0),
    };
    let req = OrderRequest {
        coin: coin.to_string(),
        side, price: px, size,
        reduce_only,
        tif: TimeInForce::Ioc,
        cloid: None,
    };
    place_order(http, hl, signer_store, meta, eoa, req, vault_address).await
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn size_format_trims_zeros() {
        assert_eq!(fmt_size(1.5, 4), "1.5");
        assert_eq!(fmt_size(1.5000, 4), "1.5");
        assert_eq!(fmt_size(0.1234, 3), "0.123");
        assert_eq!(fmt_size(10.0, 0), "10");
    }

    #[test]
    fn price_format_respects_sig_figs() {
        // BTC at $95,432.10 — 5 sig figs → "95432"
        assert_eq!(fmt_price(95432.10, 5, false), "95432");
        // ETH at $3045.78 — 5 sig figs → "3045.8"
        assert_eq!(fmt_price(3045.78, 4, false), "3045.8");
        // Small price like 0.05123 — 5-sig-fig rule doesn't trigger; cap by
        // max_decimals_for_asset = 6 - sz_decimals.
        assert_eq!(fmt_price(0.05123, 0, false), "0.05123");
    }

    #[test]
    fn order_action_shape_is_canonical() {
        let spec = AssetSpec { coin: "BTC".into(), asset: 0, sz_decimals: 5, max_leverage: 50, is_spot: false };
        let req = OrderRequest {
            coin: "BTC".into(),
            side: OrderSide::Buy,
            price: 50000.0,
            size: 0.001,
            reduce_only: false,
            tif: TimeInForce::Gtc,
            cloid: None,
        };
        let a = build_order_action(&req, &spec).unwrap();
        // First key in the outer map must be "type", and orders[0] must have
        // "a","b","p","s","r","t" in that order — both invariants matter for
        // the hash to match the python SDK's output.
        let outer = a.as_object().unwrap();
        let keys: Vec<&String> = outer.keys().collect();
        assert_eq!(keys, vec!["type", "orders", "grouping"]);
        let inner = a["orders"][0].as_object().unwrap();
        let ikeys: Vec<&String> = inner.keys().collect();
        assert_eq!(ikeys, vec!["a", "b", "p", "s", "r", "t"]);
        assert_eq!(a["orders"][0]["b"], json!(true));
        assert_eq!(a["orders"][0]["p"], json!("50000"));
        assert_eq!(a["orders"][0]["s"], json!("0.001"));
        assert_eq!(a["grouping"], json!("na"));
    }
}
