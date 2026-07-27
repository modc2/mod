use crate::actions::{self, OrderRequest, OrderSide, TimeInForce};
use crate::live_engine::{EngineConfig, TraderEntry};
use crate::sign_user;
use crate::store::{Follow, Index, IndexLeg};
use crate::AppState;
use axum::{
    extract::{Path, Query, State},
    http::StatusCode,
    routing::{delete, get, post},
    Json, Router,
};
use axum::Extension;
use serde::Deserialize;
use serde_json::{json, Value};

/// Session probe for the frontend: the guard already verified the Bearer
/// token, so this just echoes who the token says you are.
async fn auth_me(ext: Option<Extension<crate::auth::AuthedUser>>) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    match ext {
        Some(Extension(u)) => Ok(Json(json!({"ok": true, "address": u.0, "auth": "mod-protocol"}))),
        None => Err((StatusCode::UNAUTHORIZED, Json(json!({"ok": false, "error": "unauthorized"})))),
    }
}

pub fn router() -> Router<AppState> {
    Router::new()
        .route("/", get(info))
        .route("/health", get(health))
        .route("/status", get(status))

        // ── auth (mod protocol-auth; gated, so reaching it proves the token) ──
        .route("/auth/me", get(auth_me))

        // ── public market data ──
        .route("/market/meta", get(meta))
        .route("/mids", get(mids))
        .route("/orderbook/:coin", get(orderbook))
        .route("/candles/:coin", get(candles))

        // ── account / wallet ──
        .route("/user/:addr/state", get(user_state))
        .route("/user/:addr/fills", get(user_fills))
        .route("/user/:addr/pnl", get(user_pnl))
        .route("/user/:addr/orders", get(user_orders))
        .route("/user/:addr/funding", get(user_funding))

        // ── trader analytics ──
        .route("/leaderboard", get(leaderboard))
        .route("/traders/top", get(top_traders))
        .route("/trader/:addr/analyze", get(analyze_trader))
        .route("/scan/progress", get(scan_progress))

        // ── copy-trade ──
        .route("/follows", get(list_follows).post(create_follow))
        .route("/follows/:id", delete(delete_follow).patch(update_follow))
        .route("/follows/:id/pause", post(pause_follow))
        .route("/follows/:id/resume", post(resume_follow))
        .route("/signals", get(list_signals))
        .route("/signals/:id/ack", post(ack_signal))

        // ── indexes ──
        .route("/indexes", get(list_indexes).post(create_index))
        .route("/indexes/:id", get(get_index).patch(update_index).delete(delete_index))
        .route("/indexes/:id/perf", get(index_perf))
        .route("/indexes/auto", post(auto_index_preview))

        // ── vaults ──
        .route("/vaults", get(vaults))
        .route("/vaults/:addr", get(vault_details))
        .route("/vaults/:addr/perf", get(vault_perf))
        .route("/indexes/:id/vault/intent", post(vault_intent))

        // ── backend signer / agent wallet ──
        .route("/signer/address", post(signer_address))
        .route("/signer/approve_agent", post(approve_agent_intent))
        .route("/agent/status", get(agent_status))

        // ── browser-wallet (master EOA) signing support ──
        //
        // The backend agent key may sign L1 actions (orders, vaultTransfer…)
        // but Hyperliquid only accepts *master-wallet* signatures on the
        // user-signed action class (withdraw3, usdClassTransfer, approveAgent).
        // These endpoints build canonical intents (action + EIP-712 typedData
        // for eth_signTypedData_v4); the browser signs and posts the result
        // back through /exchange/relay.
        .route("/intent/withdraw", post(intent_withdraw))
        .route("/intent/usd_class_transfer", post(intent_usd_class_transfer))
        .route("/exchange/relay", post(exchange_relay))
        .route("/wallet/config", get(wallet_config))

        // ── cross-chain deposit (Ethereum / Base / Polygon → Arbitrum → HL) ──
        .route("/deposit/chains", get(crate::deposit::deposit_chains))
        .route("/deposit/balances", get(crate::deposit::deposit_balances))
        .route("/deposit/quote", post(crate::deposit::deposit_quote))
        .route("/deposit/status", get(crate::deposit::deposit_status))

        // ── trading actions (signed by backend agent) ──
        .route("/trade", post(trade))
        .route("/cancel", post(cancel))
        .route("/cancel_by_cloid", post(cancel_by_cloid))
        .route("/modify", post(modify))
        .route("/leverage", post(set_leverage))
        .route("/isolated_margin", post(update_isolated_margin))
        .route("/schedule_cancel", post(schedule_cancel))

        // ── transfers / bridging ──
        .route("/usd_class_transfer", post(usd_class_transfer))
        .route("/vault_transfer", post(vault_transfer))
        .route("/withdraw", post(withdraw_route))
        .route("/usd_send", post(usd_send_route))
        .route("/spot_send", post(spot_send_route))
        .route("/create_vault", post(create_vault_route))
        .route("/set_referrer", post(set_referrer_route))

        // ── generic signed action passthrough (anything else) ──
        .route("/action", post(action_route))

        // ── live copy-trade engine ──
        .route("/live/start", post(live_start))
        .route("/live/stop", post(live_stop))
        .route("/live/status", get(live_status))

        // ── generic mod-protocol passthrough ──
        .route("/forward", post(forward))
}

// Helper to convert anyhow::Error into a 500 with JSON body.
fn err500<E: std::fmt::Display>(e: E) -> (StatusCode, Json<Value>) {
    (StatusCode::INTERNAL_SERVER_ERROR, Json(json!({"error": e.to_string()})))
}

// ── health / status ──

async fn health() -> Json<Value> { Json(json!({"status": "ok"})) }

/// mod protocol null call: `GET /` returns module info without auth.
async fn info(State(s): State<AppState>) -> Json<Value> {
    Json(json!({
        "name": "hyperliquid",
        "version": env!("CARGO_PKG_VERSION"),
        "description": "Hyperliquid full-stack — backend agent signing, all L1+user actions, copy-trade live engine, indexes, vaults",
        "protocol": "mod",
        "auth": "mod protocol-auth Bearer token (personal_sign) — public reads open, user-scoped routes gated",
        "testnet": s.hl.testnet,
        "urls": { "app": "/hyperliquid", "api": "/api/hyperliquid" },
        "endpoints": {
            "public": ["/health", "/status", "/mids", "/market/meta", "/orderbook/:coin", "/candles/:coin", "/leaderboard", "/traders/top", "/trader/:addr/analyze", "/user/:addr/*", "/vaults", "/indexes"],
            "gated": ["/follows", "/signals", "/signer/*", "/trade", "/live/*", "/intent/*", "/exchange/relay", "/action"],
        },
    }))
}

async fn status(State(s): State<AppState>) -> Json<Value> {
    Json(json!({
        "ok": true,
        "testnet": s.hl.testnet,
        "indexes": s.store.list_indexes().len(),
        "follows": s.store.list_follows(None).len(),
    }))
}

// ── market data ──

async fn meta(State(s): State<AppState>) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    s.hl.meta_and_ctxs().await.map(Json).map_err(err500)
}
async fn mids(State(s): State<AppState>) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    s.hl.all_mids().await.map(Json).map_err(err500)
}
async fn orderbook(State(s): State<AppState>, Path(coin): Path<String>) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    s.hl.l2_book(&coin).await.map(Json).map_err(err500)
}

#[derive(Deserialize)]
struct CandleQuery { interval: Option<String>, hours: Option<i64> }
async fn candles(State(s): State<AppState>, Path(coin): Path<String>, Query(q): Query<CandleQuery>)
    -> Result<Json<Value>, (StatusCode, Json<Value>)>
{
    let interval = q.interval.unwrap_or_else(|| "1h".into());
    let hours = q.hours.unwrap_or(24);
    let now = chrono::Utc::now().timestamp_millis();
    s.hl.candles(&coin, &interval, now - hours * 3_600_000, now)
        .await.map(Json).map_err(err500)
}

// ── user / wallet ──

async fn user_state(State(s): State<AppState>, Path(a): Path<String>) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    s.hl.user_state(&a).await.map(Json).map_err(err500)
}
async fn user_fills(State(s): State<AppState>, Path(a): Path<String>) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    s.hl.user_fills(&a).await.map(Json).map_err(err500)
}
async fn user_pnl(State(s): State<AppState>, Path(a): Path<String>) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    s.hl.user_pnl(&a).await.map(Json).map_err(err500)
}
async fn user_orders(State(s): State<AppState>, Path(a): Path<String>) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    s.hl.open_orders(&a).await.map(Json).map_err(err500)
}
async fn user_funding(State(s): State<AppState>, Path(a): Path<String>) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    s.hl.user_funding(&a).await.map(Json).map_err(err500)
}

// ── traders ──

async fn leaderboard(State(s): State<AppState>) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    s.hl.leaderboard().await.map(Json).map_err(err500)
}

#[derive(Deserialize)]
struct TopQ {
    days: Option<u32>,
    min_per_day: Option<f64>,
    pool: Option<usize>,
    seed: Option<String>,           // comma-separated extra wallets
}
async fn top_traders(State(s): State<AppState>, Query(q): Query<TopQ>)
    -> Result<Json<Value>, (StatusCode, Json<Value>)>
{
    let days = q.days.unwrap_or(7).clamp(1, 90);
    let min_per_day = q.min_per_day.unwrap_or(1.0).max(0.0);
    let pool = q.pool.unwrap_or(150).clamp(1, 1500);
    let seed: Vec<String> = q.seed.unwrap_or_default()
        .split(',').filter(|x| !x.is_empty())
        .map(|x| x.trim().to_lowercase()).collect();

    // Fast path: the background refresher keeps boards for the standard
    // windows; serve those directly instead of re-running the scan per
    // request. Seeded requests need custom rows, so they fall through.
    if seed.is_empty() {
        if let Some(entry) = s.boards.get(days) {
            if entry.pool >= pool {
                let mut traders = entry.traders;
                traders.truncate(pool);
                return Ok(Json(json!({
                    "days": days, "min_per_day": min_per_day, "pool": pool,
                    "updated_at": entry.updated_at,
                    "traders": traders,
                })));
            }
        }
    }

    let traders = crate::traders::top_traders_with_progress(
        s.hl.clone(), days, min_per_day, pool, seed.clone(),
        Some(s.progress.clone()),
    ).await.map_err(err500)?;
    if seed.is_empty() {
        s.boards.put(days, pool, traders.clone());
    }
    Ok(Json(json!({
        "days": days, "min_per_day": min_per_day, "pool": pool,
        "updated_at": chrono::Utc::now().timestamp_millis(),
        "traders": traders,
    })))
}

async fn scan_progress(State(s): State<AppState>) -> Json<Value> {
    Json(json!(s.progress.snapshot()))
}

#[derive(Deserialize)]
struct AnalyzeQ { days: Option<u32> }
async fn analyze_trader(State(s): State<AppState>, Path(a): Path<String>, Query(q): Query<AnalyzeQ>)
    -> Result<Json<Value>, (StatusCode, Json<Value>)>
{
    let days = q.days.unwrap_or(7).clamp(1, 90);
    crate::traders::analyze(s.hl.clone(), &a, days).await.map(Json).map_err(err500)
}

// ── follows / copy ──

#[derive(Deserialize)]
struct FollowFilter { follower: Option<String> }
async fn list_follows(State(s): State<AppState>, Query(q): Query<FollowFilter>) -> Json<Value> {
    Json(json!({"follows": s.store.list_follows(q.follower.as_deref())}))
}

#[derive(Deserialize)]
struct CreateFollow {
    follower: String,
    leader: String,
    size_pct: Option<f64>,
    max_per_trade_usd: Option<f64>,
    coins_allow: Option<Vec<String>>,
    coins_deny: Option<Vec<String>>,
    vault_address: Option<String>,
}
async fn create_follow(State(s): State<AppState>, Json(b): Json<CreateFollow>)
    -> Result<Json<Value>, (StatusCode, Json<Value>)>
{
    let f = Follow {
        id: String::new(),
        follower: b.follower.to_lowercase(),
        leader: b.leader.to_lowercase(),
        size_pct: b.size_pct.unwrap_or(10.0).clamp(0.0, 100.0),
        max_per_trade_usd: b.max_per_trade_usd.unwrap_or(0.0).max(0.0),
        coins_allow: b.coins_allow.unwrap_or_default(),
        coins_deny: b.coins_deny.unwrap_or_default(),
        created_ms: 0, last_seen_tid: 0, paused: false,
        vault_address: b.vault_address,
    };
    s.store.upsert_follow(f).map(|x| Json(json!(x))).map_err(err500)
}

#[derive(Deserialize)]
struct PatchFollow {
    size_pct: Option<f64>,
    max_per_trade_usd: Option<f64>,
    coins_allow: Option<Vec<String>>,
    coins_deny: Option<Vec<String>>,
    paused: Option<bool>,
    vault_address: Option<String>,
}
async fn update_follow(State(s): State<AppState>, Path(id): Path<String>, Json(p): Json<PatchFollow>)
    -> Result<Json<Value>, (StatusCode, Json<Value>)>
{
    let mut f = s.store.list_follows(None).into_iter().find(|x| x.id == id)
        .ok_or((StatusCode::NOT_FOUND, Json(json!({"error":"not found"}))))?;
    if let Some(v) = p.size_pct { f.size_pct = v.clamp(0.0, 100.0); }
    if let Some(v) = p.max_per_trade_usd { f.max_per_trade_usd = v.max(0.0); }
    if let Some(v) = p.coins_allow { f.coins_allow = v; }
    if let Some(v) = p.coins_deny { f.coins_deny = v; }
    if let Some(v) = p.paused { f.paused = v; }
    if let Some(v) = p.vault_address { f.vault_address = Some(v); }
    s.store.upsert_follow(f).map(|x| Json(json!(x))).map_err(err500)
}

async fn delete_follow(State(s): State<AppState>, Path(id): Path<String>)
    -> Result<Json<Value>, (StatusCode, Json<Value>)>
{
    let ok = s.store.delete_follow(&id).map_err(err500)?;
    Ok(Json(json!({"deleted": ok})))
}

async fn pause_follow(State(s): State<AppState>, Path(id): Path<String>)
    -> Result<Json<Value>, (StatusCode, Json<Value>)>
{
    let mut f = s.store.list_follows(None).into_iter().find(|x| x.id == id)
        .ok_or((StatusCode::NOT_FOUND, Json(json!({"error":"not found"}))))?;
    f.paused = true;
    s.store.upsert_follow(f).map(|x| Json(json!(x))).map_err(err500)
}
async fn resume_follow(State(s): State<AppState>, Path(id): Path<String>)
    -> Result<Json<Value>, (StatusCode, Json<Value>)>
{
    let mut f = s.store.list_follows(None).into_iter().find(|x| x.id == id)
        .ok_or((StatusCode::NOT_FOUND, Json(json!({"error":"not found"}))))?;
    f.paused = false;
    s.store.upsert_follow(f).map(|x| Json(json!(x))).map_err(err500)
}

#[derive(Deserialize)]
struct SignalQ { follower: Option<String>, limit: Option<usize> }
async fn list_signals(State(s): State<AppState>, Query(q): Query<SignalQ>) -> Json<Value> {
    let lim = q.limit.unwrap_or(100).clamp(1, 500);
    Json(json!({"signals": s.copy.recent_signals(q.follower.as_deref(), lim)}))
}

#[derive(Deserialize)]
struct AckBody { status: String }
async fn ack_signal(State(s): State<AppState>, Path(id): Path<String>, Json(b): Json<AckBody>) -> Json<Value> {
    s.copy.mark_signal(&id, &b.status);
    Json(json!({"ok": true}))
}

// ── indexes ──

async fn list_indexes(State(s): State<AppState>) -> Json<Value> {
    Json(json!({"indexes": s.store.list_indexes()}))
}

#[derive(Deserialize)]
struct CreateIndex {
    name: String,
    owner: String,
    description: Option<String>,
    legs: Vec<IndexLeg>,
    days_window: Option<u32>,
    max_leverage: Option<f64>,
    notional_pct: Option<f64>,
    vault_address: Option<String>,
}
async fn create_index(State(s): State<AppState>, Json(b): Json<CreateIndex>)
    -> Result<Json<Value>, (StatusCode, Json<Value>)>
{
    let idx = Index {
        id: String::new(),
        name: b.name,
        owner: b.owner.to_lowercase(),
        description: b.description.unwrap_or_default(),
        legs: b.legs,
        days_window: b.days_window.unwrap_or(7).clamp(1, 90),
        created_ms: 0,
        vault_address: b.vault_address,
        max_leverage: b.max_leverage.unwrap_or(0.0).max(0.0),
        notional_pct: b.notional_pct.unwrap_or(50.0).clamp(0.0, 100.0),
    };
    s.store.upsert_index(idx).map(|x| Json(json!(x))).map_err(err500)
}

async fn get_index(State(s): State<AppState>, Path(id): Path<String>)
    -> Result<Json<Value>, (StatusCode, Json<Value>)>
{
    s.store.get_index(&id)
        .map(|x| Json(json!(x)))
        .ok_or((StatusCode::NOT_FOUND, Json(json!({"error": "not found"}))))
}

#[derive(Deserialize)]
struct PatchIndex {
    name: Option<String>,
    description: Option<String>,
    legs: Option<Vec<IndexLeg>>,
    days_window: Option<u32>,
    max_leverage: Option<f64>,
    notional_pct: Option<f64>,
    vault_address: Option<String>,
}
async fn update_index(State(s): State<AppState>, Path(id): Path<String>, Json(p): Json<PatchIndex>)
    -> Result<Json<Value>, (StatusCode, Json<Value>)>
{
    let mut idx = s.store.get_index(&id)
        .ok_or((StatusCode::NOT_FOUND, Json(json!({"error":"not found"}))))?;
    if let Some(v) = p.name { idx.name = v; }
    if let Some(v) = p.description { idx.description = v; }
    if let Some(v) = p.legs { idx.legs = v; }
    if let Some(v) = p.days_window { idx.days_window = v.clamp(1, 90); }
    if let Some(v) = p.max_leverage { idx.max_leverage = v.max(0.0); }
    if let Some(v) = p.notional_pct { idx.notional_pct = v.clamp(0.0, 100.0); }
    if let Some(v) = p.vault_address { idx.vault_address = Some(v); }
    s.store.upsert_index(idx).map(|x| Json(json!(x))).map_err(err500)
}

async fn delete_index(State(s): State<AppState>, Path(id): Path<String>)
    -> Result<Json<Value>, (StatusCode, Json<Value>)>
{
    let ok = s.store.delete_index(&id).map_err(err500)?;
    Ok(Json(json!({"deleted": ok})))
}

#[derive(Deserialize)]
struct PerfQ { days: Option<u32> }
async fn index_perf(State(s): State<AppState>, Path(id): Path<String>, Query(q): Query<PerfQ>)
    -> Result<Json<Value>, (StatusCode, Json<Value>)>
{
    let idx = s.store.get_index(&id)
        .ok_or((StatusCode::NOT_FOUND, Json(json!({"error":"not found"}))))?;
    crate::indexes::perf(s.hl.clone(), &idx, q.days)
        .await.map(|p| Json(json!(p))).map_err(err500)
}

#[derive(Deserialize)]
struct AutoBody { days: Option<u32>, top: Option<usize>, min_per_day: Option<f64>, pool: Option<usize> }
async fn auto_index_preview(State(s): State<AppState>, Json(b): Json<AutoBody>)
    -> Result<Json<Value>, (StatusCode, Json<Value>)>
{
    let days = b.days.unwrap_or(7);
    let top = b.top.unwrap_or(10).max(1).min(50);
    let pool = b.pool.unwrap_or(150);
    let mpd = b.min_per_day.unwrap_or(1.0);
    let traders = crate::traders::top_traders(s.hl.clone(), days, mpd, pool, vec![])
        .await.map_err(err500)?;
    let legs = crate::indexes::auto_legs(&traders, top);
    Ok(Json(json!({
        "days": days, "top": top, "legs": legs,
        "candidates": traders.into_iter().take(top).collect::<Vec<_>>(),
    })))
}

// ── vaults ──

#[derive(Deserialize)]
struct VaultsQ { min_tvl: Option<f64>, pool: Option<usize> }
async fn vaults(State(s): State<AppState>, Query(q): Query<VaultsQ>)
    -> Result<Json<Value>, (StatusCode, Json<Value>)>
{
    let pool = q.pool.unwrap_or(300).clamp(1, 2000);
    let vaults = crate::vaults::top_vaults(s.hl.clone(), q.min_tvl, pool)
        .await.map_err(err500)?;
    Ok(Json(json!({ "vaults": vaults })))
}

#[derive(Deserialize)]
struct VaultDetailQ { user: Option<String> }
async fn vault_details(State(s): State<AppState>, Path(a): Path<String>, Query(q): Query<VaultDetailQ>)
    -> Result<Json<Value>, (StatusCode, Json<Value>)>
{
    s.hl.vault_details(&a, q.user.as_deref()).await.map(Json).map_err(err500)
}
async fn vault_perf(State(s): State<AppState>, Path(a): Path<String>) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    s.hl.vault_pnl(&a).await.map(Json).map_err(err500)
}

// Build a vault-creation intent for an index. The Hyperliquid vault
// creation tx must be signed by the index owner — we hand the caller
// the canonical action payload they need to sign and submit to
// /exchange themselves (or via the /forward passthrough below).
#[derive(Deserialize)]
struct VaultIntentBody {
    initial_usd: f64,
    nonce: Option<u64>,
}
async fn vault_intent(State(s): State<AppState>, Path(id): Path<String>, Json(b): Json<VaultIntentBody>)
    -> Result<Json<Value>, (StatusCode, Json<Value>)>
{
    let idx = s.store.get_index(&id)
        .ok_or((StatusCode::NOT_FOUND, Json(json!({"error":"not found"}))))?;
    let nonce = b.nonce.unwrap_or_else(|| chrono::Utc::now().timestamp_millis() as u64);
    let action = json!({
        "type": "createVault",
        "name": idx.name,
        "description": format!("Index '{}' — {} legs. Owner-funded only.",
                                idx.name, idx.legs.len()),
        "initialUsd": (b.initial_usd * 1e6) as u64,
        "nonce": nonce,
    });
    Ok(Json(json!({
        "action": action,
        "owner": idx.owner,
        "exchange_url": s.hl.exchange_url,
        "note": "Sign `action` with the owner key, POST to /forward with \
                 {fn:'exchange_post', payload:{action,nonce,signature}}.",
    })))
}

// ── /forward — generic mod-protocol passthrough ──
//
// fn:
//   "info_post"     → POST /info  with body.payload
//   "exchange_post" → POST /exchange with body.payload (caller-signed)
//   "top_traders"   → wraps top_traders helper
//   "list_indexes" / "list_follows" / "recent_signals"
#[derive(Deserialize)]
struct ForwardBody {
    #[serde(rename = "fn")]
    fnname: String,
    #[serde(default)]
    payload: Value,
}

async fn forward(State(s): State<AppState>, Json(b): Json<ForwardBody>)
    -> Result<Json<Value>, (StatusCode, Json<Value>)>
{
    let result = match b.fnname.as_str() {
        "info_post" => {
            let r = reqwest::Client::new().post(&s.hl.info_url)
                .json(&b.payload).send().await.map_err(err500)?;
            let v: Value = r.json().await.map_err(err500)?;
            v
        }
        "exchange_post" => {
            let r = reqwest::Client::new().post(&s.hl.exchange_url)
                .json(&b.payload).send().await.map_err(err500)?;
            let v: Value = r.json().await.map_err(err500)?;
            v
        }
        "top_traders" => {
            let days = b.payload.get("days").and_then(|x| x.as_u64()).unwrap_or(7) as u32;
            let mpd = b.payload.get("min_per_day").and_then(|x| x.as_f64()).unwrap_or(1.0);
            let pool = b.payload.get("pool").and_then(|x| x.as_u64()).unwrap_or(150) as usize;
            let traders = crate::traders::top_traders(s.hl.clone(), days, mpd, pool, vec![])
                .await.map_err(err500)?;
            json!({"traders": traders})
        }
        "list_indexes" => json!({"indexes": s.store.list_indexes()}),
        "list_follows" => json!({"follows": s.store.list_follows(None)}),
        "recent_signals" => {
            let lim = b.payload.get("limit").and_then(|x| x.as_u64()).unwrap_or(100) as usize;
            let f = b.payload.get("follower").and_then(|x| x.as_str()).map(|x| x.to_string());
            json!({"signals": s.copy.recent_signals(f.as_deref(), lim)})
        }
        other => {
            return Err((StatusCode::BAD_REQUEST,
                Json(json!({"error": format!("unknown fn: {other}")}))));
        }
    };
    Ok(Json(json!({"result": result})))
}

// ════════════════════════════════════════════════════════════════════════
//  Backend agent signer + trading actions
// ════════════════════════════════════════════════════════════════════════

#[derive(Deserialize)]
struct EoaBody { eoa: String }

async fn signer_address(State(s): State<AppState>, Json(b): Json<EoaBody>)
    -> Result<Json<Value>, (StatusCode, Json<Value>)>
{
    let addr = s.signer.signer_address(&b.eoa).map_err(err500)?;
    Ok(Json(json!({ "eoa": b.eoa, "agentAddress": addr })))
}

/// Build the `approveAgent` action + digest the *user's* master wallet
/// needs to sign in their browser to authorize the backend agent. We don't
/// sign it ourselves — the user does. Returns the action JSON, the digest
/// (as hex), and the EIP-712 typed-data so the wallet can sign either way.
#[derive(Deserialize)]
struct ApproveAgentBody {
    eoa: String,
    #[serde(default)]
    agent_name: Option<String>,
}

async fn approve_agent_intent(State(s): State<AppState>, Json(b): Json<ApproveAgentBody>)
    -> Result<Json<Value>, (StatusCode, Json<Value>)>
{
    let agent_addr = s.signer.signer_address(&b.eoa).map_err(err500)?;
    let is_mainnet = !s.hl.testnet;
    let nonce = chrono::Utc::now().timestamp_millis() as u64;
    let (action, digest) = sign_user::build_approve_agent(
        &agent_addr, b.agent_name.as_deref(), nonce, is_mainnet,
    );
    let typed_data = sign_user::approve_agent_typed_data(
        &agent_addr, b.agent_name.as_deref(), nonce, is_mainnet,
    );
    Ok(Json(json!({
        "action": action,
        "nonce": nonce,
        "agentAddress": agent_addr,
        "digest": format!("0x{}", hex::encode(digest)),
        "typedData": typed_data,
        "exchange_url": s.hl.exchange_url,
        "note": "Sign `typedData` with eth_signTypedData_v4 (master EOA, on Arbitrum), then POST { action, nonce, signature: {r,s,v} } to /exchange/relay.",
    })))
}

/// Is the backend agent for this EOA already approved on Hyperliquid?
#[derive(Deserialize)]
struct AgentStatusQ { eoa: String }

async fn agent_status(State(s): State<AppState>, Query(q): Query<AgentStatusQ>)
    -> Result<Json<Value>, (StatusCode, Json<Value>)>
{
    let agent_addr = s.signer.signer_address(&q.eoa).map_err(err500)?;
    let agents = s.hl.extra_agents(&q.eoa).await.unwrap_or(Value::Null);
    let approved = agents.as_array().map(|arr| arr.iter().any(|a| {
        a.get("address").and_then(|x| x.as_str())
            .map(|x| x.eq_ignore_ascii_case(&agent_addr)).unwrap_or(false)
    })).unwrap_or(false);
    Ok(Json(json!({
        "eoa": q.eoa,
        "agentAddress": agent_addr,
        "approved": approved,
        "agents": agents,
    })))
}

// ── master-wallet intents + relay ──

#[derive(Deserialize)]
struct WithdrawIntentBody { destination: String, amount: String }

async fn intent_withdraw(State(s): State<AppState>, Json(b): Json<WithdrawIntentBody>)
    -> Result<Json<Value>, (StatusCode, Json<Value>)>
{
    let is_mainnet = !s.hl.testnet;
    let now = chrono::Utc::now().timestamp_millis() as u64;
    let (action, digest) = sign_user::build_withdraw3(&b.destination, &b.amount, now, is_mainnet);
    let typed_data = sign_user::withdraw3_typed_data(&b.destination, &b.amount, now, is_mainnet);
    Ok(Json(json!({
        "action": action,
        "nonce": now,
        "digest": format!("0x{}", hex::encode(digest)),
        "typedData": typed_data,
    })))
}

#[derive(Deserialize)]
struct UsdClassIntentBody { amount: String, to_perp: bool }

async fn intent_usd_class_transfer(State(s): State<AppState>, Json(b): Json<UsdClassIntentBody>)
    -> Result<Json<Value>, (StatusCode, Json<Value>)>
{
    let is_mainnet = !s.hl.testnet;
    let nonce = chrono::Utc::now().timestamp_millis() as u64;
    let (action, digest) = sign_user::build_usd_class_transfer(&b.amount, b.to_perp, nonce, is_mainnet);
    let typed_data = sign_user::usd_class_transfer_typed_data(&b.amount, b.to_perp, nonce, is_mainnet);
    Ok(Json(json!({
        "action": action,
        "nonce": nonce,
        "digest": format!("0x{}", hex::encode(digest)),
        "typedData": typed_data,
    })))
}

/// Relay a browser-signed action to Hyperliquid's /exchange. The body is the
/// exact envelope HL expects — we forward it untouched so signatures stay
/// valid, but surface HL-level "err" statuses as HTTP errors for the UI.
#[derive(Deserialize)]
struct RelayBody {
    action: Value,
    nonce: u64,
    signature: Value,
    #[serde(default, rename = "vaultAddress")]
    vault_address: Option<String>,
}

async fn exchange_relay(State(s): State<AppState>, Json(b): Json<RelayBody>)
    -> Result<Json<Value>, (StatusCode, Json<Value>)>
{
    let mut body = json!({
        "action": b.action,
        "nonce": b.nonce,
        "signature": b.signature,
    });
    if let Some(v) = &b.vault_address {
        body.as_object_mut().unwrap().insert("vaultAddress".into(), Value::String(v.clone()));
    }
    let r = s.http.post(&s.hl.exchange_url).json(&body).send().await.map_err(err500)?;
    let status = r.status();
    let text = r.text().await.unwrap_or_default();
    let v: Value = serde_json::from_str(&text).unwrap_or_else(|_| json!({"raw": text}));
    tracing::info!(%status, response = %v, "POST /exchange (relay) response");
    if !status.is_success() {
        return Err((StatusCode::BAD_GATEWAY, Json(json!({"error": format!("/exchange HTTP {status}"), "response": v}))));
    }
    if v.get("status").and_then(|x| x.as_str()) == Some("err") {
        return Err((StatusCode::BAD_REQUEST, Json(json!({
            "error": v.get("response").and_then(|x| x.as_str()).unwrap_or("exchange rejected action"),
            "response": v,
        }))));
    }
    Ok(Json(v))
}

/// Chain constants the frontend needs to drive MetaMask: which chain to be
/// on for user-signed actions, and where USDC bridge deposits go.
async fn wallet_config(State(s): State<AppState>) -> Json<Value> {
    let testnet = s.hl.testnet;
    let (chain_id, chain_name, usdc, bridge, rpc, explorer) = if testnet {
        (421614u64, "Arbitrum Sepolia",
         "0x1baAbB04529D43a73232B713C0FE471f7c7334d5",
         "0x08cfc1B6b2dCF36A1480b99353A354AA8AC56f89",
         "https://sepolia-rollup.arbitrum.io/rpc",
         "https://sepolia.arbiscan.io")
    } else {
        (42161u64, "Arbitrum One",
         "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
         "0x2Df1c51E09aECF9cacB7bc98cB1742757f163dF7",
         "https://arb1.arbitrum.io/rpc",
         "https://arbiscan.io")
    };
    Json(json!({
        "testnet": testnet,
        "chainId": chain_id,
        "chainIdHex": format!("0x{:x}", chain_id),
        "chainName": chain_name,
        "usdcAddress": usdc,
        "bridgeAddress": bridge,
        "rpcUrl": rpc,
        "explorerUrl": explorer,
        "minDepositUsd": 5.0,
        "withdrawalFeeUsd": 1.0,
    }))
}

#[derive(Deserialize)]
struct TradeBody {
    eoa: String,
    coin: String,
    is_buy: bool,
    /// Order size in base units (e.g. ETH amount, not USD).
    size: f64,
    /// Optional limit price. Omit for market orders (slippage-padded IOC).
    #[serde(default)]
    price: Option<f64>,
    /// Time-in-force: "Gtc" | "Ioc" | "Alo". Defaults to Gtc for limits, Ioc for market.
    #[serde(default)]
    tif: Option<String>,
    #[serde(default)]
    reduce_only: bool,
    #[serde(default)]
    slippage_bps: Option<u32>,
    #[serde(default)]
    cloid: Option<String>,
    #[serde(default)]
    vault_address: Option<String>,
}

async fn trade(State(s): State<AppState>, Json(b): Json<TradeBody>)
    -> Result<Json<Value>, (StatusCode, Json<Value>)>
{
    let side = if b.is_buy { OrderSide::Buy } else { OrderSide::Sell };
    let result = match b.price {
        Some(px) => {
            let tif = match b.tif.as_deref() {
                Some("Ioc") => TimeInForce::Ioc,
                Some("Alo") => TimeInForce::Alo,
                _ => TimeInForce::Gtc,
            };
            let req = OrderRequest {
                coin: b.coin.clone(), side,
                price: px, size: b.size,
                reduce_only: b.reduce_only, tif,
                cloid: b.cloid.clone(),
            };
            actions::place_order(&s.http, &s.hl, &s.signer, &s.meta, &b.eoa, req, b.vault_address.as_deref()).await
        }
        None => {
            actions::place_market_order(
                &s.http, &s.hl, &s.signer, &s.meta, &b.eoa, &b.coin, side, b.size,
                b.slippage_bps.unwrap_or(100), b.reduce_only,
                b.vault_address.as_deref(),
            ).await
        }
    };
    result.map(Json).map_err(err500)
}

#[derive(Deserialize)]
struct CancelEntry { coin: String, oid: u64 }
#[derive(Deserialize)]
struct CancelBody {
    eoa: String,
    cancels: Vec<CancelEntry>,
    #[serde(default)] vault_address: Option<String>,
}

async fn cancel(State(s): State<AppState>, Json(b): Json<CancelBody>)
    -> Result<Json<Value>, (StatusCode, Json<Value>)>
{
    // Build a coin→spec lookup just for the assets referenced.
    let mut meta_map = std::collections::HashMap::new();
    for c in &b.cancels {
        let spec = s.meta.get(&c.coin).await.map_err(err500)?;
        meta_map.insert(c.coin.to_ascii_uppercase(), spec);
    }
    let reqs: Vec<actions::CancelRequest> = b.cancels.iter()
        .map(|c| actions::CancelRequest { coin: c.coin.clone(), oid: c.oid }).collect();
    let action = actions::build_cancel_action(&reqs, &meta_map).map_err(err500)?;
    let nonce = chrono::Utc::now().timestamp_millis() as u64;
    actions::post_l1_action(&s.http, &s.hl, &s.signer, &b.eoa, action, nonce, b.vault_address.as_deref())
        .await.map(Json).map_err(err500)
}

#[derive(Deserialize)]
struct CancelByCloidEntry { coin: String, cloid: String }
#[derive(Deserialize)]
struct CancelByCloidBody {
    eoa: String,
    cancels: Vec<CancelByCloidEntry>,
    #[serde(default)] vault_address: Option<String>,
}

async fn cancel_by_cloid(State(s): State<AppState>, Json(b): Json<CancelByCloidBody>)
    -> Result<Json<Value>, (StatusCode, Json<Value>)>
{
    let mut meta_map = std::collections::HashMap::new();
    for c in &b.cancels {
        let spec = s.meta.get(&c.coin).await.map_err(err500)?;
        meta_map.insert(c.coin.to_ascii_uppercase(), spec);
    }
    let reqs: Vec<actions::CancelByCloidRequest> = b.cancels.iter()
        .map(|c| actions::CancelByCloidRequest { coin: c.coin.clone(), cloid: c.cloid.clone() }).collect();
    let action = actions::build_cancel_by_cloid_action(&reqs, &meta_map).map_err(err500)?;
    let nonce = chrono::Utc::now().timestamp_millis() as u64;
    actions::post_l1_action(&s.http, &s.hl, &s.signer, &b.eoa, action, nonce, b.vault_address.as_deref())
        .await.map(Json).map_err(err500)
}

#[derive(Deserialize)]
struct ModifyBody {
    eoa: String,
    oid: u64,
    coin: String,
    is_buy: bool,
    price: f64,
    size: f64,
    #[serde(default)] reduce_only: bool,
    #[serde(default)] tif: Option<String>,
    #[serde(default)] vault_address: Option<String>,
}

async fn modify(State(s): State<AppState>, Json(b): Json<ModifyBody>)
    -> Result<Json<Value>, (StatusCode, Json<Value>)>
{
    let spec = s.meta.get(&b.coin).await.map_err(err500)?;
    let tif = match b.tif.as_deref() {
        Some("Ioc") => TimeInForce::Ioc,
        Some("Alo") => TimeInForce::Alo,
        _ => TimeInForce::Gtc,
    };
    let req = OrderRequest {
        coin: b.coin.clone(),
        side: if b.is_buy { OrderSide::Buy } else { OrderSide::Sell },
        price: b.price, size: b.size, reduce_only: b.reduce_only, tif, cloid: None,
    };
    let action = actions::build_modify_action(b.oid, &req, &spec).map_err(err500)?;
    let nonce = chrono::Utc::now().timestamp_millis() as u64;
    actions::post_l1_action(&s.http, &s.hl, &s.signer, &b.eoa, action, nonce, b.vault_address.as_deref())
        .await.map(Json).map_err(err500)
}

#[derive(Deserialize)]
struct LeverageBody {
    eoa: String,
    coin: String,
    leverage: u32,
    #[serde(default = "default_true_v")] is_cross: bool,
    #[serde(default)] vault_address: Option<String>,
}
fn default_true_v() -> bool { true }

async fn set_leverage(State(s): State<AppState>, Json(b): Json<LeverageBody>)
    -> Result<Json<Value>, (StatusCode, Json<Value>)>
{
    let spec = s.meta.get(&b.coin).await.map_err(err500)?;
    let action = actions::build_update_leverage_action(spec.asset, b.is_cross, b.leverage);
    let nonce = chrono::Utc::now().timestamp_millis() as u64;
    actions::post_l1_action(&s.http, &s.hl, &s.signer, &b.eoa, action, nonce, b.vault_address.as_deref())
        .await.map(Json).map_err(err500)
}

#[derive(Deserialize)]
struct IsolatedMarginBody {
    eoa: String,
    coin: String,
    is_buy: bool,
    /// USDC amount to add (positive) or remove (negative). Will be converted to ntli (microUSDC).
    amount_usd: f64,
    #[serde(default)] vault_address: Option<String>,
}

async fn update_isolated_margin(State(s): State<AppState>, Json(b): Json<IsolatedMarginBody>)
    -> Result<Json<Value>, (StatusCode, Json<Value>)>
{
    let spec = s.meta.get(&b.coin).await.map_err(err500)?;
    let ntli = (b.amount_usd * 1_000_000.0).round() as i64;
    let action = actions::build_update_isolated_margin_action(spec.asset, b.is_buy, ntli);
    let nonce = chrono::Utc::now().timestamp_millis() as u64;
    actions::post_l1_action(&s.http, &s.hl, &s.signer, &b.eoa, action, nonce, b.vault_address.as_deref())
        .await.map(Json).map_err(err500)
}

#[derive(Deserialize)]
struct ScheduleCancelBody {
    eoa: String,
    #[serde(default)] time_ms: Option<u64>,
    #[serde(default)] vault_address: Option<String>,
}

async fn schedule_cancel(State(s): State<AppState>, Json(b): Json<ScheduleCancelBody>)
    -> Result<Json<Value>, (StatusCode, Json<Value>)>
{
    let action = actions::build_schedule_cancel_action(b.time_ms);
    let nonce = chrono::Utc::now().timestamp_millis() as u64;
    actions::post_l1_action(&s.http, &s.hl, &s.signer, &b.eoa, action, nonce, b.vault_address.as_deref())
        .await.map(Json).map_err(err500)
}

#[derive(Deserialize)]
struct VaultTransferBody {
    eoa: String,
    /// The vault address to deposit into / withdraw from.
    vault: String,
    is_deposit: bool,
    /// USDC amount.
    amount_usd: f64,
}

async fn vault_transfer(State(s): State<AppState>, Json(b): Json<VaultTransferBody>)
    -> Result<Json<Value>, (StatusCode, Json<Value>)>
{
    let usd_micro = (b.amount_usd * 1_000_000.0).round() as u64;
    let action = actions::build_vault_transfer_action(&b.vault, b.is_deposit, usd_micro);
    let nonce = chrono::Utc::now().timestamp_millis() as u64;
    // NB: vaultAddress is part of the action itself for vaultTransfer — the
    // envelope-level vaultAddress is for *trading as* a vault, not transfers.
    let res = actions::post_l1_action(&s.http, &s.hl, &s.signer, &b.eoa, action, nonce, None)
        .await.map(Json).map_err(err500)?;
    // Drop cached vaultDetails for this vault so the page reload right after
    // a deposit/withdraw shows the new followerState instead of a stale copy.
    s.hl.cache_evict_prefix(&format!("vaultDetails:{}", b.vault.to_lowercase()));
    Ok(res)
}

#[derive(Deserialize)]
struct UsdClassTransferBody {
    eoa: String,
    /// USDC amount, as a string (matches HL convention; e.g. "10.5").
    amount: String,
    to_perp: bool,
}

async fn usd_class_transfer(State(s): State<AppState>, Json(b): Json<UsdClassTransferBody>)
    -> Result<Json<Value>, (StatusCode, Json<Value>)>
{
    let is_mainnet = !s.hl.testnet;
    let nonce = chrono::Utc::now().timestamp_millis() as u64;
    let (action, digest) = sign_user::build_usd_class_transfer(&b.amount, b.to_perp, nonce, is_mainnet);
    actions::post_user_action(&s.http, &s.hl, &s.signer, &b.eoa, action, digest, nonce)
        .await.map(Json).map_err(err500)
}

#[derive(Deserialize)]
struct WithdrawBody {
    eoa: String,
    destination: String,
    amount: String,
}

async fn withdraw_route(State(s): State<AppState>, Json(b): Json<WithdrawBody>)
    -> Result<Json<Value>, (StatusCode, Json<Value>)>
{
    let is_mainnet = !s.hl.testnet;
    let now = chrono::Utc::now().timestamp_millis() as u64;
    let (action, digest) = sign_user::build_withdraw3(&b.destination, &b.amount, now, is_mainnet);
    actions::post_user_action(&s.http, &s.hl, &s.signer, &b.eoa, action, digest, now)
        .await.map(Json).map_err(err500)
}

#[derive(Deserialize)]
struct UsdSendBody {
    eoa: String,
    destination: String,
    amount: String,
}

async fn usd_send_route(State(s): State<AppState>, Json(b): Json<UsdSendBody>)
    -> Result<Json<Value>, (StatusCode, Json<Value>)>
{
    let is_mainnet = !s.hl.testnet;
    let now = chrono::Utc::now().timestamp_millis() as u64;
    let (action, digest) = sign_user::build_usd_send(&b.destination, &b.amount, now, is_mainnet);
    actions::post_user_action(&s.http, &s.hl, &s.signer, &b.eoa, action, digest, now)
        .await.map(Json).map_err(err500)
}

#[derive(Deserialize)]
struct SpotSendBody {
    eoa: String,
    destination: String,
    token: String,
    amount: String,
}

async fn spot_send_route(State(s): State<AppState>, Json(b): Json<SpotSendBody>)
    -> Result<Json<Value>, (StatusCode, Json<Value>)>
{
    let is_mainnet = !s.hl.testnet;
    let now = chrono::Utc::now().timestamp_millis() as u64;
    let (action, digest) = sign_user::build_spot_send(&b.destination, &b.token, &b.amount, now, is_mainnet);
    actions::post_user_action(&s.http, &s.hl, &s.signer, &b.eoa, action, digest, now)
        .await.map(Json).map_err(err500)
}

#[derive(Deserialize)]
struct CreateVaultBody {
    eoa: String,
    name: String,
    #[serde(default)] description: String,
    initial_usd: f64,
}

async fn create_vault_route(State(s): State<AppState>, Json(b): Json<CreateVaultBody>)
    -> Result<Json<Value>, (StatusCode, Json<Value>)>
{
    let nonce = chrono::Utc::now().timestamp_millis() as u64;
    let initial_micro = (b.initial_usd * 1_000_000.0).round() as u64;
    let action = actions::build_create_vault_action(&b.name, &b.description, initial_micro, nonce);
    actions::post_l1_action(&s.http, &s.hl, &s.signer, &b.eoa, action, nonce, None)
        .await.map(Json).map_err(err500)
}

#[derive(Deserialize)]
struct SetReferrerBody { eoa: String, code: String }

async fn set_referrer_route(State(s): State<AppState>, Json(b): Json<SetReferrerBody>)
    -> Result<Json<Value>, (StatusCode, Json<Value>)>
{
    let action = actions::build_set_referrer_action(&b.code);
    let nonce = chrono::Utc::now().timestamp_millis() as u64;
    actions::post_l1_action(&s.http, &s.hl, &s.signer, &b.eoa, action, nonce, None)
        .await.map(Json).map_err(err500)
}

/// Generic signed action passthrough. Caller supplies the raw L1 action JSON
/// (must use insertion-ordered keys per HL's encoding); we sign with the
/// backend agent for `eoa` and POST to /exchange. Useful for action types
/// we haven't wrapped explicitly (e.g. `setReferrer`, `approveBuilderFee`,
/// `subAccountTransfer`, etc.).
#[derive(Deserialize)]
struct GenericActionBody {
    eoa: String,
    action: Value,
    #[serde(default)] vault_address: Option<String>,
    #[serde(default)] nonce: Option<u64>,
}

async fn action_route(State(s): State<AppState>, Json(b): Json<GenericActionBody>)
    -> Result<Json<Value>, (StatusCode, Json<Value>)>
{
    let nonce = b.nonce.unwrap_or_else(|| chrono::Utc::now().timestamp_millis() as u64);
    actions::post_l1_action(&s.http, &s.hl, &s.signer, &b.eoa, b.action, nonce, b.vault_address.as_deref())
        .await.map(Json).map_err(err500)
}

// ════════════════════════════════════════════════════════════════════════
//  Live copy-trade engine
// ════════════════════════════════════════════════════════════════════════

#[derive(Deserialize)]
struct LiveStartBody {
    eoa: String,
    #[serde(default)] strategy_id: Option<String>,
    traders: Vec<TraderInput>,
    #[serde(default)] capital: Option<f64>,
    #[serde(default)] interval_ms: Option<u64>,
    #[serde(default)] min_order_size_usd: Option<f64>,
    #[serde(default)] max_slippage_bps: Option<u32>,
    #[serde(default)] size_pct: Option<f64>,
    #[serde(default)] max_per_trade_usd: Option<f64>,
    #[serde(default)] coins_allow: Option<Vec<String>>,
    #[serde(default)] coins_deny: Option<Vec<String>>,
    #[serde(default)] vault_address: Option<String>,
}

#[derive(Deserialize)]
struct TraderInput {
    address: String,
    #[serde(default = "one")] weight: f64,
    #[serde(default = "yes")] enabled: bool,
}
fn one() -> f64 { 1.0 }
fn yes() -> bool { true }

async fn live_start(State(s): State<AppState>, Json(b): Json<LiveStartBody>) -> Json<Value> {
    let cfg = EngineConfig {
        eoa: b.eoa.to_lowercase(),
        strategy_id: b.strategy_id.unwrap_or_default(),
        traders: b.traders.into_iter().map(|t| TraderEntry {
            address: t.address.to_lowercase(),
            weight: t.weight.max(0.0),
            enabled: t.enabled,
        }).collect(),
        capital: b.capital.unwrap_or(0.0),
        interval_ms: b.interval_ms.unwrap_or(15_000).max(2_000),
        min_order_size_usd: b.min_order_size_usd.unwrap_or(10.0).max(0.0),
        max_slippage_bps: b.max_slippage_bps.unwrap_or(100).min(5000),
        size_pct: b.size_pct.unwrap_or(10.0).clamp(0.0, 1000.0),
        max_per_trade_usd: b.max_per_trade_usd.unwrap_or(0.0).max(0.0),
        coins_allow: b.coins_allow.unwrap_or_default(),
        coins_deny: b.coins_deny.unwrap_or_default(),
        vault_address: b.vault_address,
    };
    s.live.start(cfg.clone());
    Json(json!({ "ok": true, "eoa": cfg.eoa, "status": "running" }))
}

async fn live_stop(State(s): State<AppState>, Json(b): Json<EoaBody>) -> Json<Value> {
    let stopped = s.live.stop(&b.eoa);
    Json(json!({ "ok": true, "eoa": b.eoa, "wasRunning": stopped }))
}

#[derive(Deserialize)]
struct LiveStatusQ { eoa: String }

async fn live_status(State(s): State<AppState>, Query(q): Query<LiveStatusQ>) -> Json<Value> {
    let cfg = s.live.config_of(&q.eoa);
    let st = s.live.status_of(&q.eoa);
    Json(json!({ "eoa": q.eoa, "config": cfg, "state": st }))
}
