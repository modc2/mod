use std::collections::BTreeMap;
use std::time::{SystemTime, UNIX_EPOCH};

use axum::{
    extract::{Path, Query, State},
    http::StatusCode,
    response::{IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use serde_json::{json, Value};

use crate::bridges::{self, BridgeQuoteIn, BridgeQuoteOut, RouteQuote, SOURCE_ASSETS};
use crate::config::{self, looks_like_ss58};
use crate::keys;
use crate::types::*;
use crate::AppState;

pub fn router() -> Router<AppState> {
    Router::new()
        .route("/", get(info))
        .route("/info", get(info))
        .route("/health", get(health))
        .route("/status", get(status))
        .route("/sources", get(sources))
        .route("/rates", get(rates))
        .route("/quote", post(quote))
        .route("/deposit_address", get(deposit_address))
        .route("/swap", post(swap))
        .route("/order/:id", get(order_get))
        .route("/orders", get(orders_list))
        .route("/order/:id/confirm", post(order_confirm))
        .route("/order/:id/mark_paid", post(order_mark_paid))
        .route("/order/:id/cancel", post(order_cancel))
        .route("/order/:id/settle_prediction", post(order_settle_prediction))
        .route("/bridges", get(bridges_catalog))
        .route("/bridges/assets", get(bridges_assets))
        .route("/bridges/quote", post(bridges_quote))
}

const PREDICTION_WINDOW_SECS: i64 = 86_400;

/// TAO/USDT spot from the rates view. Both prices are USD-quoted so the pair
/// is just `tao_usd / tether_usd` (USDT depegs only matter in extreme cases,
/// but we account for them for free here).
fn tao_usdt_price(view: &RatesView) -> Option<f64> {
    let tao = view.usd.get("bittensor").copied()?;
    let usdt = view.usd.get("tether").copied().filter(|p| *p > 0.0)?;
    if tao <= 0.0 { return None; }
    Some(tao / usdt)
}

// ── helpers ────────────────────────────────────────────────────────

#[derive(Debug)]
struct ApiError {
    status: StatusCode,
    body: Value,
}

impl ApiError {
    fn bad(msg: impl Into<String>) -> Self {
        ApiError { status: StatusCode::BAD_REQUEST, body: json!({ "detail": msg.into() }) }
    }
    fn not_found(msg: impl Into<String>) -> Self {
        ApiError { status: StatusCode::NOT_FOUND, body: json!({ "detail": msg.into() }) }
    }
    fn forbidden(msg: impl Into<String>) -> Self {
        ApiError { status: StatusCode::FORBIDDEN, body: json!({ "detail": msg.into() }) }
    }
    fn server(msg: impl Into<String>) -> Self {
        ApiError { status: StatusCode::INTERNAL_SERVER_ERROR, body: json!({ "detail": msg.into() }) }
    }
}

impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        (self.status, Json(self.body)).into_response()
    }
}

impl From<anyhow::Error> for ApiError {
    fn from(e: anyhow::Error) -> Self { ApiError::server(e.to_string()) }
}

fn now() -> i64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs() as i64).unwrap_or(0)
}

fn deposit_for(state: &AppState, sym: &str) -> Result<DepositAddress, ApiError> {
    let cfg = state.config.sources.get(sym)
        .ok_or_else(|| ApiError::bad(format!("unsupported source token '{sym}'")))?;
    let addr = std::env::var(&cfg.deposit_env).unwrap_or_default();
    let addr = if addr.is_empty() {
        // Fall back to a locally generated key persisted under the store dir.
        // The private key sits in `{store_dir}/keys/{eth,sol}.json` (mode 0600);
        // operators wanting a different address should set `cfg.deposit_env`.
        match cfg.chain.as_str() {
            "ethereum" => keys::eth_address(&state.config.store_dir)
                .map_err(|e| ApiError::server(format!("eth keygen failed: {e}")))?,
            "solana" => keys::sol_address(&state.config.store_dir)
                .map_err(|e| ApiError::server(format!("sol keygen failed: {e}")))?,
            other => return Err(ApiError::bad(format!(
                "no deposit fallback for chain '{other}' (set {} in operator env)",
                cfg.deposit_env
            ))),
        }
    } else {
        addr
    };
    Ok(DepositAddress { from: sym.to_string(), chain: cfg.chain.clone(), deposit_address: addr })
}

async fn build_quote(state: &AppState, sym: &str, amount: f64) -> Result<Quote, ApiError> {
    if amount <= 0.0 {
        return Err(ApiError::bad("amount must be positive"));
    }
    if !state.config.sources.contains_key(sym) {
        return Err(ApiError::bad(format!("unsupported source token '{sym}'")));
    }

    let r = state.rates.get(&state.http, false).await
        .map_err(|e| ApiError::bad(format!("rate unavailable: {e}")))?;

    let rate = r.pairs.get(&format!("{sym}_to_tao")).copied().unwrap_or(0.0);
    if rate <= 0.0 {
        return Err(ApiError::bad("rate unavailable"));
    }

    let gross = amount * rate;
    let fee_bps = state.config.fee_bps;
    let fee = gross * fee_bps as f64 / 10_000.0;
    Ok(Quote {
        from: sym.into(),
        amount_in: amount,
        rate,
        gross_tao: gross,
        fee_bps,
        fee_tao: fee,
        tao_out: gross - fee,
        rates_ts: r.ts,
        rates_stale: r.stale,
    })
}

// ── handlers ───────────────────────────────────────────────────────

async fn sources(State(s): State<AppState>) -> Json<Value> {
    Json(json!(s.config.sources))
}

async fn info(State(s): State<AppState>) -> Json<Value> {
    Json(json!({
        "name": "taox",
        "module": "taox",
        "version": env!("CARGO_PKG_VERSION"),
        "description": "Convert ETH and SOL into native TAO. Rust (axum) API + Next.js frontend.",
        "fns": ["info", "health", "status", "quote", "swap", "order",
                "orders", "deposit_address", "rates", "settle_prediction",
                "bridges", "bridge_assets", "bridge_quote"],
        "prediction_window_secs": PREDICTION_WINDOW_SECS,
        "sources": s.config.sources.keys().cloned().collect::<Vec<_>>(),
        "destination": s.config.destination.chain.clone(),
        "fee_bps": s.config.fee_bps,
    }))
}

async fn health(State(s): State<AppState>) -> Json<Health> {
    Json(Health {
        status: "ok",
        module: "taox",
        sources: s.config.sources.keys().cloned().collect(),
        destination: s.config.destination.chain.clone(),
    })
}

async fn status(State(s): State<AppState>) -> Json<Status> {
    let mut by_state: BTreeMap<String, usize> = BTreeMap::new();
    let orders = s.orders.list();
    for o in &orders {
        *by_state.entry(o.state.clone()).or_insert(0) += 1;
    }
    Json(Status {
        orders: orders.len(),
        by_state,
        fee_bps: s.config.fee_bps,
        sources_supported: s.config.sources.keys().cloned().collect(),
    })
}

async fn rates(
    State(s): State<AppState>,
    Query(q): Query<RatesQuery>,
) -> Result<Json<RatesView>, ApiError> {
    let v = s.rates.get(&s.http, q.refresh).await
        .map_err(|e| ApiError::server(e.to_string()))?;
    Ok(Json(v))
}

async fn quote(
    State(s): State<AppState>,
    Json(body): Json<QuoteIn>,
) -> Result<Json<Quote>, ApiError> {
    let sym = body.from_token.to_lowercase();
    let q = build_quote(&s, &sym, body.amount).await?;
    Ok(Json(q))
}

async fn deposit_address(
    State(s): State<AppState>,
    Query(q): Query<DepositQuery>,
) -> Result<Json<DepositAddress>, ApiError> {
    let sym = q.from_token.to_lowercase();
    Ok(Json(deposit_for(&s, &sym)?))
}

async fn swap(
    State(s): State<AppState>,
    Json(body): Json<SwapIn>,
) -> Result<Json<Order>, ApiError> {
    let sym = body.from_token.to_lowercase();

    if let Some(err) = config::validate_source_address(&s.config, &sym, &body.source_address) {
        return Err(ApiError::bad(err));
    }
    if !looks_like_ss58(&body.destination_ss58) {
        return Err(ApiError::bad("invalid destination ss58 address"));
    }

    let q = build_quote(&s, &sym, body.amount).await?;
    let deposit = deposit_for(&s, &sym)?;

    let id_full = uuid::Uuid::new_v4().simple().to_string();
    let id = id_full[..16].to_string();
    let ts = now();

    let prediction = if let Some(p) = body.prediction.as_ref() {
        let direction = p.direction.trim().to_lowercase();
        if direction != "up" && direction != "down" {
            return Err(ApiError::bad("prediction.direction must be 'up' or 'down'"));
        }
        let view = s.rates.get(&s.http, false).await
            .map_err(|e| ApiError::bad(format!("rates unavailable: {e}")))?;
        let ref_price = tao_usdt_price(&view)
            .ok_or_else(|| ApiError::bad("TAO/USDT reference price unavailable"))?;
        Some(Prediction {
            direction,
            reference_price: ref_price,
            fee_tao: q.fee_tao,
            opened_at: ts,
            settles_at: ts + PREDICTION_WINDOW_SECS,
            settled: false,
            outcome: None,
            settled_price: None,
            settled_at: None,
            delta_pct: None,
            user_payout_tao: None,
            owner_share_tao: None,
            payout_tx: None,
        })
    } else {
        None
    };

    let order = Order {
        id: id.clone(),
        created: ts,
        from: sym.clone(),
        amount_in: body.amount,
        source_address: body.source_address.clone(),
        destination_ss58: body.destination_ss58.clone(),
        deposit_address: deposit.deposit_address,
        quoted_rate: q.rate,
        quoted_tao_out: q.tao_out,
        fee_bps: q.fee_bps,
        slippage_bps: body.slippage_bps,
        state: "awaiting_deposit".into(),
        source_tx: None,
        delivery_tx: None,
        history: vec![HistoryEntry {
            ts, state: "awaiting_deposit".into(), tx: None, reason: None,
        }],
        prediction,
    };

    let saved = s.orders.insert(order)?;
    Ok(Json(saved))
}

async fn order_get(
    State(s): State<AppState>,
    Path(id): Path<String>,
) -> Result<Json<Order>, ApiError> {
    s.orders.get(&id).map(Json).ok_or_else(|| ApiError::not_found("order not found"))
}

async fn orders_list(
    State(s): State<AppState>,
    Query(q): Query<OrdersQuery>,
) -> Json<Vec<Order>> {
    let mut items = s.orders.list();
    if let Some(addr) = &q.source_address {
        let lc = addr.to_lowercase();
        items.retain(|o| o.source_address.to_lowercase() == lc);
    }
    items.sort_by(|a, b| b.created.cmp(&a.created));
    items.truncate(q.limit);
    Json(items)
}

async fn order_confirm(
    State(s): State<AppState>,
    Path(id): Path<String>,
    Json(body): Json<ConfirmIn>,
) -> Result<Json<Order>, ApiError> {
    let tx = body.source_tx.trim().to_string();
    let updated = s.orders.update(&id, |o| {
        if !matches!(o.state.as_str(), "awaiting_deposit" | "deposit_seen") {
            return Err(format!("cannot confirm in state '{}'", o.state));
        }
        o.source_tx = Some(tx.clone());
        o.state = "deposit_seen".into();
        o.history.push(HistoryEntry {
            ts: now(), state: "deposit_seen".into(),
            tx: Some(tx.clone()), reason: None,
        });
        Ok(())
    }).map_err(|e| ApiError::bad(e.to_string()))?;
    updated.map(Json).ok_or_else(|| ApiError::not_found("order not found"))
}

async fn order_mark_paid(
    State(s): State<AppState>,
    Path(id): Path<String>,
    Json(body): Json<MarkPaidIn>,
) -> Result<Json<Order>, ApiError> {
    let expected = std::env::var("TAOX_ADMIN_KEY").unwrap_or_default();
    if expected.is_empty() || body.admin_key != expected {
        return Err(ApiError::forbidden("admin auth required"));
    }
    let tx = body.delivery_tx.trim().to_string();
    let updated = s.orders.update(&id, |o| {
        o.delivery_tx = Some(tx.clone());
        o.state = "completed".into();
        o.history.push(HistoryEntry {
            ts: now(), state: "completed".into(),
            tx: Some(tx.clone()), reason: None,
        });
        Ok(())
    }).map_err(|e| ApiError::bad(e.to_string()))?;
    updated.map(Json).ok_or_else(|| ApiError::not_found("order not found"))
}

async fn order_settle_prediction(
    State(s): State<AppState>,
    Path(id): Path<String>,
    Json(body): Json<SettlePredictionIn>,
) -> Result<Json<Order>, ApiError> {
    let expected = std::env::var("TAOX_ADMIN_KEY").unwrap_or_default();
    if expected.is_empty() || body.admin_key != expected {
        return Err(ApiError::forbidden("admin auth required"));
    }

    let view = s.rates.get(&s.http, true).await
        .map_err(|e| ApiError::server(format!("rates unavailable: {e}")))?;
    let settled_price = tao_usdt_price(&view)
        .ok_or_else(|| ApiError::server("TAO/USDT settle price unavailable"))?;
    let settled_at = now();
    let force = body.force;
    let payout_tx = body.payout_tx.as_ref().map(|t| t.trim().to_string()).filter(|t| !t.is_empty());

    let updated = s.orders.update(&id, |o| {
        let p = o.prediction.as_mut()
            .ok_or_else(|| "order has no prediction".to_string())?;
        if p.settled {
            return Err("prediction already settled".into());
        }
        if !force && settled_at < p.settles_at {
            let secs = p.settles_at - settled_at;
            return Err(format!("not yet settleable; {secs}s remaining (or pass force=true)"));
        }

        let delta_pct = (settled_price - p.reference_price) / p.reference_price;
        let won = match p.direction.as_str() {
            "up" => delta_pct > 0.0,
            "down" => delta_pct < 0.0,
            _ => false,
        };

        p.settled = true;
        p.settled_at = Some(settled_at);
        p.settled_price = Some(settled_price);
        p.delta_pct = Some(delta_pct);
        if won {
            // Profit = fee scaled by the realized magnitude of the move.
            // User: full fee refund + half the profit. Owner: half the profit.
            let profit = p.fee_tao * delta_pct.abs();
            p.outcome = Some("win".into());
            p.user_payout_tao = Some(p.fee_tao + profit / 2.0);
            p.owner_share_tao = Some(profit / 2.0);
        } else {
            p.outcome = Some("lose".into());
            p.user_payout_tao = Some(0.0);
            p.owner_share_tao = Some(0.0);
        }
        if let Some(tx) = payout_tx.clone() {
            p.payout_tx = Some(tx);
        }

        let outcome = p.outcome.clone().unwrap_or_default();
        o.history.push(HistoryEntry {
            ts: settled_at,
            state: format!("prediction_{outcome}"),
            tx: payout_tx.clone(),
            reason: Some(format!(
                "ref={:.6} settled={:.6} delta={:.4}%",
                p.reference_price, settled_price, delta_pct * 100.0
            )),
        });
        Ok(())
    }).map_err(|e| ApiError::bad(e.to_string()))?;
    updated.map(Json).ok_or_else(|| ApiError::not_found("order not found"))
}

async fn order_cancel(
    State(s): State<AppState>,
    Path(id): Path<String>,
    Json(body): Json<CancelIn>,
) -> Result<Json<Order>, ApiError> {
    let updated = s.orders.update(&id, |o| {
        if matches!(o.state.as_str(), "completed" | "cancelled") {
            return Err(format!("cannot cancel in state '{}'", o.state));
        }
        o.state = "cancelled".into();
        o.history.push(HistoryEntry {
            ts: now(), state: "cancelled".into(),
            tx: None, reason: Some(body.reason.clone()),
        });
        Ok(())
    }).map_err(|e| ApiError::bad(e.to_string()))?;
    updated.map(Json).ok_or_else(|| ApiError::not_found("order not found"))
}

// ── bridge-in board ────────────────────────────────────────────────

/// Map a bridge source-asset key onto the config source symbol this module's
/// own desk uses, so the desk can be priced next to the outside routes.
/// Base has no entry — the desk only watches Ethereum and Solana deposits.
fn desk_symbol(key: &str) -> Option<&'static str> {
    Some(match key {
        "sol:SOL" => "sol",
        "sol:USDC" => "usdc_sol",
        "sol:USDT" => "usdt_sol",
        "eth:ETH" => "eth",
        "eth:USDC" => "usdc_eth",
        "eth:USDT" => "usdt_eth",
        _ => return None,
    })
}

/// CoinGecko id for a source symbol, used to build the mid-price benchmark.
fn coingecko_id_for(symbol: &str) -> &'static str {
    match symbol {
        "SOL" => "solana",
        "ETH" => "ethereum",
        "USDC" => "usd-coin",
        "USDT" => "tether",
        _ => "",
    }
}

/// Every route we know about, plus which source assets each one accepts.
async fn bridges_catalog() -> Json<Value> {
    Json(json!({
        "assets": bridges::SOURCE_ASSETS,
        "routes": bridges::ROUTES,
        "coverage": bridges::coverage(),
        "tao_solana_mint": bridges::TAO_SOLANA_MINT,
        "bittensor_evm": {
            "chain_id": 964,
            "chain_id_hex": "0x3c4",
            "rpc": "https://lite.chain.opentensor.ai",
            "explorer": "https://evm.taostats.io",
        },
        "note": "Routes delivering native ss58 TAO are the only ones that finish \
                 the job in one step; everything else needs a further hop, which \
                 is what `hops` and `delivers` record.",
    }))
}

async fn bridges_assets() -> Json<Value> {
    Json(json!(SOURCE_ASSETS))
}

/// Price every quotable route for one (asset, amount) side by side.
async fn bridges_quote(
    State(s): State<AppState>,
    Json(body): Json<BridgeQuoteIn>,
) -> Result<Json<BridgeQuoteOut>, ApiError> {
    if body.amount <= 0.0 {
        return Err(ApiError::bad("amount must be positive"));
    }
    let asset = bridges::source_asset(&body.asset).ok_or_else(|| {
        ApiError::bad(format!(
            "unknown source asset '{}' — expected one of: {}",
            body.asset,
            SOURCE_ASSETS.iter().map(|a| a.key).collect::<Vec<_>>().join(", ")
        ))
    })?;

    // Mid-price benchmark. Best-effort: if CoinGecko is unreachable the board
    // still renders, it just can't say what each route costs versus mid.
    let rates_view = s.rates.get(&s.http, false).await.ok();
    let (mid_tao, mid_stale) = match &rates_view {
        Some(r) => {
            let tao = r.usd.get("bittensor").copied().unwrap_or(0.0);
            let src = r.usd.get(coingecko_id_for(asset.symbol)).copied().unwrap_or(0.0);
            let mid = (tao > 0.0 && src > 0.0).then(|| body.amount * src / tao);
            (mid, Some(r.stale))
        }
        None => (None, None),
    };

    let mut routes = bridges::quote_all(&s.http, asset, body.amount, mid_tao).await;

    // Slot this deployment's own desk into the same board, priced the same
    // way it prices a real order.
    if let Some(sym) = desk_symbol(asset.key) {
        routes.push(desk_route_quote(&s, sym, body.amount, mid_tao).await);
    }

    bridges::sort_board(&mut routes);
    let best_native = bridges::best_native(&routes);

    Ok(Json(BridgeQuoteOut {
        asset: asset.key.to_string(),
        chain: asset.chain,
        symbol: asset.symbol,
        amount: body.amount,
        mid_tao,
        mid_stale,
        routes,
        best_native,
        manual: bridges::manual_routes(asset.key),
        ts: now(),
    }))
}

/// This module's own desk as one more row on the board.
async fn desk_route_quote(
    state: &AppState,
    sym: &str,
    amount: f64,
    mid_tao: Option<f64>,
) -> RouteQuote {
    let desk = bridges::ROUTES.iter().find(|r| r.id == "taox_desk").expect("taox_desk route");
    let mut q = RouteQuote {
        id: desk.id,
        name: desk.name,
        kind: desk.kind,
        custody: desk.custody,
        delivers: desk.delivers,
        delivers_label: desk.delivers_label,
        hops: desk.hops,
        eta: desk.eta,
        url: desk.url,
        status: "error",
        tao_out: None,
        rate: None,
        vs_mid_pct: None,
        min_in: None,
        max_in: None,
        price_impact_pct: None,
        // The desk prices off the CoinGecko mid minus a fixed fee rather than
        // off a book, so it structurally undercuts every desk quoting a real
        // spread. Flagged so it can't top the board on that advantage.
        indicative: true,
        detail: None,
    };
    match build_quote(state, sym, amount).await {
        Ok(quoted) => {
            q.status = "ok";
            q.tao_out = Some(quoted.tao_out);
            q.rate = Some(quoted.tao_out / amount);
            if let Some(mid) = mid_tao.filter(|m| *m > 0.0) {
                q.vs_mid_pct = Some((quoted.tao_out / mid - 1.0) * 100.0);
            }
            let mut note =
                format!("indicative: CoinGecko mid less {}bps, not a firm quote", quoted.fee_bps);
            if quoted.rates_stale {
                note.push_str(" (rates stale)");
            }
            q.detail = Some(note);
        }
        Err(e) => {
            q.detail = Some(
                e.body.get("detail").and_then(|v| v.as_str()).unwrap_or("quote failed").to_string(),
            );
        }
    }
    q
}
