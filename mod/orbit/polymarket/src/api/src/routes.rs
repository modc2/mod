use axum::body::Body;
use axum::extract::{Query, State};
use axum::http::StatusCode;
use axum::response::IntoResponse;
use axum::routing::{get, post};
use axum::{Json, Router};
use serde::Deserialize;
use serde_json::{json, Value};
use tokio_stream::StreamExt;

use crate::types::{ActiveTradersQuery, StreamEvent};
use crate::proxy;
use crate::AppState;

pub fn router() -> Router<AppState> {
    Router::new()
        .route("/health", get(health))
        .route("/active-traders", get(active_traders))
        // Backend signer endpoints — see signer.rs.
        // /signer/sign-order is the ONLY signing endpoint exposed. The
        // generic /signer/sign that took an arbitrary digest was removed
        // for safety: a malicious caller could otherwise hand the backend
        // a digest of a USDC.transfer execTransaction and get a valid
        // signature for it. sign-order reconstructs the digest server-side
        // from a typed Polymarket Order struct, so the backend never signs
        // anything that isn't structurally a Polymarket CLOB order.
        .route("/signer/address", get(signer_address))
        .route("/signer/sign-order", post(signer_sign_order))
        // Mint a CLOB API key bound to the per-user BACKEND signer EOA.
        // One-shot per user: backend signs ClobAuth with its own key,
        // POSTs to Polymarket /auth/api-key with POLY_ADDRESS=backend.
        // The resulting key lets the backend place orders without the
        // user's wallet — Safe-owner authorization gates the maker
        // address on Polymarket's side.
        .route("/signer/mint-clob-creds", post(mint_clob_creds))
        // Place an order on Polymarket CLOB end-to-end: backend builds the
        // Order struct, signs it with the per-EOA stored key, HMAC-auths
        // the L2 call, POSTs to clob.polymarket.com/order. Returns CLOB's
        // raw response so the caller can read order id / status / fills.
        .route("/order/place", post(order_place_handler))
        // Long-running copy engine — see live_engine.rs.
        //   POST /live/start  — body: EngineConfig. Spawns a tokio task
        //                       keyed by `eoa`. Persists config to disk so
        //                       the session survives API restarts.
        //   POST /live/stop   — body: {eoa}. Aborts the task, deletes the
        //                       persisted config (next boot won't resume).
        //   GET  /live/status — query: eoa. Returns the current EngineState
        //                       JSON. 404 when no engine is running for eoa.
        .route("/live/start", post(live_start))
        .route("/live/stop", post(live_stop))
        .route("/live/status", get(live_status))
        // Recycle the api process — container runs with
        // restart: unless-stopped so Docker auto-respawns it. Useful when
        // an engine task is wedged or after a deploy; persisted live
        // configs in /tmp/polymarket-live-engine survive the restart and
        // resume_persisted re-spawns the tasks on boot.
        .route("/admin/restart", post(admin_restart))
        // Deposit wallet (V2 POLY_1271) management — see relayer.rs +
        // deposit_wallet.rs. The frontend WalletPanel uses these to
        // surface the wallet address and balance to the user, and to
        // initiate withdrawals back to a destination address.
        .route("/deposit-wallet/info", get(deposit_wallet_info))
        .route("/deposit-wallet/withdraw", post(deposit_wallet_withdraw))
        // Wrap raw USDC.e in the deposit wallet into V2 trading collateral
        // via Polymarket's CollateralOnramp. The frontend WalletPanel
        // auto-fires this after a successful MetaMask deposit so users
        // never see "balance: 0" despite their USDC being in the wallet.
        .route("/deposit-wallet/wrap", post(deposit_wallet_wrap))
        // Encrypted strat storage
        .merge(crate::strats::router())
        // CLOB L1 auth proxy (derive/create api keys)
        .merge(crate::auth::router())
        // Proxy: all other endpoints go through the cache proxy
        .fallback(get(proxy::proxy_handler).post(proxy::proxy_handler))
}

// ─── Signer endpoints ────────────────────────────────────────────────────

#[derive(Deserialize)]
struct SignerAddressQuery {
    eoa: String,
}

async fn signer_address(
    State(state): State<AppState>,
    Query(q): Query<SignerAddressQuery>,
) -> impl IntoResponse {
    match state.signer_store.signer_address(&q.eoa) {
        Ok(addr) => Json(json!({"eoa": q.eoa.to_lowercase(), "signer": addr})).into_response(),
        Err(e) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({"error": format!("signer address: {}", e)})),
        )
            .into_response(),
    }
}

#[derive(Deserialize)]
struct SignOrderRequest {
    /// EOA that owns the proxy / Safe that this backend signer is registered
    /// against. Used to look up which stored key to sign with.
    eoa: String,
    /// Fully structured Polymarket order. Server reconstructs the EIP-712
    /// digest from these fields against Polymarket's known CTFExchange
    /// domain (hard-coded). Caller can't influence the digest beyond these
    /// fields, so they can't trick the backend into signing arbitrary calls.
    order: crate::order_signing::OrderInput,
}

// ─── Live engine endpoints ──────────────────────────────────────────────

async fn live_start(
    State(state): State<AppState>,
    Json(cfg): Json<crate::live_engine::EngineConfig>,
) -> impl IntoResponse {
    state.engines.start(cfg);
    Json(json!({"ok": true})).into_response()
}

#[derive(Deserialize)]
struct LiveEoaBody {
    eoa: String,
}

async fn live_stop(
    State(state): State<AppState>,
    Json(body): Json<LiveEoaBody>,
) -> impl IntoResponse {
    let stopped = state.engines.stop(&body.eoa);
    Json(json!({"ok": stopped})).into_response()
}

#[derive(Deserialize)]
struct LiveStatusQuery {
    eoa: String,
}

async fn live_status(
    State(state): State<AppState>,
    Query(q): Query<LiveStatusQuery>,
) -> impl IntoResponse {
    match state.engines.status_of(&q.eoa) {
        Some(s) => Json(json!({
            "running": true,
            "config": state.engines.config_of(&q.eoa),
            "state": s,
        }))
            .into_response(),
        None => Json(json!({"running": false})).into_response(),
    }
}

// ─── Admin ──────────────────────────────────────────────────────────────

async fn admin_restart() -> impl IntoResponse {
    // Reply first, then exit. The 200 lands before the process dies so the
    // caller sees a clean "ok" instead of a connection reset. Docker's
    // restart policy brings the container back within a couple of seconds.
    tokio::spawn(async {
        tokio::time::sleep(std::time::Duration::from_millis(150)).await;
        // exit(0) is graceful enough for our needs — tokio drops in-flight
        // tasks, but the live engine's state was already persisted to disk
        // after the most recent cycle, so resume_persisted picks up where
        // we left off on the next boot.
        std::process::exit(0);
    });
    Json(json!({"ok": true, "restarting": true}))
}

// ─── Backend-owned CLOB API key ─────────────────────────────────────────

#[derive(Deserialize)]
struct MintCredsRequest {
    /// User EOA the per-user backend key is keyed on. Looks up the
    /// backend address from `signer_store` and mints a CLOB API key
    /// against that backend address.
    eoa: String,
}

async fn mint_clob_creds(
    State(state): State<AppState>,
    Json(req): Json<MintCredsRequest>,
) -> impl IntoResponse {
    // 1. Resolve backend signer address for this user.
    let backend_addr = match state.signer_store.signer_address(&req.eoa) {
        Ok(a) => a,
        Err(e) => {
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"error": format!("signer address: {}", e)})),
            )
                .into_response();
        }
    };

    // 2. Build the ClobAuth EIP-712 digest, sign with backend key.
    let timestamp = chrono::Utc::now().timestamp().to_string();
    let nonce: u64 = 0;
    let digest = match crate::clob_auth::clob_auth_digest(&backend_addr, &timestamp, nonce) {
        Ok(d) => d,
        Err(e) => {
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"error": format!("digest: {}", e)})),
            )
                .into_response();
        }
    };
    let sig = match state.signer_store.sign_digest(&req.eoa, &digest) {
        Ok(s) => s,
        Err(e) => {
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"error": format!("sign: {}", e)})),
            )
                .into_response();
        }
    };
    let sig_hex = format!("0x{}", hex::encode(sig));

    // 3. Hit Polymarket's /auth/api-key (POST) and /auth/derive-api-key
    //    (GET) using the backend address. Try derive first — if a key
    //    already exists for this backend EOA we just return it. Otherwise
    //    create a fresh one.
    const CLOB: &str = "https://clob.polymarket.com";
    let try_call = |method: reqwest::Method, path: &str| {
        let url = format!("{}{}", CLOB, path);
        let http = state.http.clone();
        let backend_addr = backend_addr.clone();
        let sig_hex = sig_hex.clone();
        let timestamp = timestamp.clone();
        async move {
            http.request(method, &url)
                .header("POLY_ADDRESS", &backend_addr)
                .header("POLY_SIGNATURE", &sig_hex)
                .header("POLY_TIMESTAMP", &timestamp)
                .header("POLY_NONCE", nonce.to_string())
                .send()
                .await
        }
    };

    let resp = match try_call(reqwest::Method::GET, "/auth/derive-api-key").await {
        Ok(r) if r.status().is_success() => r,
        _ => match try_call(reqwest::Method::POST, "/auth/api-key").await {
            Ok(r) => r,
            Err(e) => {
                return (
                    StatusCode::BAD_GATEWAY,
                    Json(json!({"error": format!("upstream: {}", e)})),
                )
                    .into_response();
            }
        },
    };

    let status = resp.status();
    let body_text = resp.text().await.unwrap_or_default();
    let body: Value =
        serde_json::from_str(&body_text).unwrap_or_else(|_| json!({"raw": body_text}));
    if !status.is_success() {
        tracing::warn!(
            "mint-clob-creds failed: backend={} status={} body={}",
            backend_addr, status, body
        );
        let code = StatusCode::from_u16(status.as_u16()).unwrap_or(StatusCode::BAD_GATEWAY);
        return (code, Json(body)).into_response();
    }

    // Augment the response with the backend address so the client knows
    // which signer to use for `POLY_ADDRESS` and `order.signer`.
    tracing::info!(
        "mint-clob-creds ok: backend={} body_len={}",
        backend_addr, body_text.len()
    );
    let mut merged = match body {
        Value::Object(m) => m,
        _ => serde_json::Map::new(),
    };
    merged.insert("signerAddress".to_string(), Value::String(backend_addr));
    Json(Value::Object(merged)).into_response()
}

async fn order_place_handler(
    State(state): State<AppState>,
    Json(req): Json<crate::order_place::PlaceOrderRequest>,
) -> impl IntoResponse {
    match crate::order_place::place_order(&state.http, &state.signer_store, req).await {
        Ok(resp) => Json(resp).into_response(),
        Err(e) => (
            StatusCode::BAD_GATEWAY,
            Json(json!({"error": format!("place order: {}", e)})),
        )
            .into_response(),
    }
}

async fn signer_sign_order(
    State(state): State<AppState>,
    Json(req): Json<SignOrderRequest>,
) -> impl IntoResponse {
    let digest = match crate::order_signing::order_digest(&req.order) {
        Ok(d) => d,
        Err(e) => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"error": format!("digest build: {}", e)})),
            )
                .into_response();
        }
    };
    match state.signer_store.sign_digest(&req.eoa, &digest) {
        Ok(sig) => {
            let sig_hex = format!("0x{}", hex::encode(sig));
            let digest_hex = format!("0x{}", hex::encode(digest));
            Json(json!({
                "signature": sig_hex,
                // Echo the digest so the caller can verify it matches what
                // they'd have computed locally before sending the order out.
                "digest": digest_hex,
            }))
                .into_response()
        }
        Err(e) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({"error": format!("sign: {}", e)})),
        )
            .into_response(),
    }
}

async fn health() -> Json<Value> {
    Json(json!({"status": "ok", "service": "polymarket-api"}))
}

async fn active_traders(
    State(state): State<AppState>,
    Query(q): Query<ActiveTradersQuery>,
) -> impl IntoResponse {
    let days = q.days.unwrap_or(7).clamp(1, 365);
    let min_per_day = q.min_per_day.unwrap_or(0.0).max(0.0);
    let pool = q.pool.unwrap_or(1000).clamp(50, 2000);
    let stream = q.stream.as_deref() == Some("1");
    let paged = q.paged.as_deref() == Some("1");
    let force = q.force.as_deref() == Some("1");
    let cache_key = format!("{}:{}:{}", days, min_per_day, pool);

    // Status probe
    if q.status.as_deref() == Some("1") {
        return Json(json!({"ok": true})).into_response();
    }

    // Check cache (memory + disk). `force=1` skips this so the SYNC button
    // can guarantee a fresh re-aggregation from Polymarket regardless of
    // how recent the cache is.
    if !force {
    if let Some((payload, source)) = state.pipeline.cache.get_or_disk(&cache_key) {
        if paged {
            let result = apply_pagination(&payload, &q, source);
            return Json(result).into_response();
        }
        if stream {
            let evt = StreamEvent::Result {
                source: source.to_string(),
                count: payload.count,
                candidate_pool: payload.candidate_pool,
                days_window: payload.days_window,
                min_trades_per_day: payload.min_trades_per_day,
                traders: payload.traders,
            };
            let body = format!("{}\n", serde_json::to_string(&evt).unwrap_or_default());
            return axum::response::Response::builder()
                .header("content-type", "application/x-ndjson")
                .header("cache-control", "no-store")
                .body(Body::from(body))
                .unwrap()
                .into_response();
        }
        return Json(json!({
            "count": payload.count,
            "candidatePool": payload.candidate_pool,
            "daysWindow": payload.days_window,
            "minTradesPerDay": payload.min_trades_per_day,
            "traders": payload.traders,
            "source": source,
            "syncedAt": payload.synced_at,
        })).into_response();
    }
    }

    // Paged but cold cache. force=1 skips this "cold" early return so the
    // request falls through to run_pipeline below and the client gets the
    // freshly-aggregated paged result instead of an empty COLD payload.
    if paged && !force {
        return Json(json!({
            "traders": [],
            "total": 0,
            "page": 0,
            "pageSize": 25,
            "cold": true,
            "source": null,
        })).into_response();
    }

    // Streaming response
    if stream {
        let pipeline = state.pipeline.clone();
        let (tx, rx) = tokio::sync::mpsc::channel::<Value>(100);

        tokio::spawn(async move {
            let result = pipeline.run_pipeline(days, min_per_day, pool, Some(tx.clone())).await;
            match result {
                Ok(payload) => {
                    // Don't poison the cache with empty results from upstream hiccups.
                    if payload.count > 0 {
                        pipeline.cache.set(&cache_key, payload.clone());
                    }
                    let evt = serde_json::json!({
                        "type": "result",
                        "source": "fresh",
                        "count": payload.count,
                        "candidatePool": payload.candidate_pool,
                        "daysWindow": payload.days_window,
                        "minTradesPerDay": payload.min_trades_per_day,
                        "traders": payload.traders,
                        "syncedAt": payload.synced_at,
                    });
                    tx.send(evt).await.ok();
                }
                Err(e) => {
                    tx.send(serde_json::json!({"type": "error", "message": e.to_string()})).await.ok();
                }
            }
        });

        let stream = tokio_stream::wrappers::ReceiverStream::new(rx)
            .map(|v| {
                let line = format!("{}\n", serde_json::to_string(&v).unwrap_or_default());
                Ok::<_, std::convert::Infallible>(line)
            });

        return axum::response::Response::builder()
            .header("content-type", "application/x-ndjson")
            .header("cache-control", "no-store")
            .body(Body::from_stream(stream))
            .unwrap()
            .into_response();
    }

    // Non-streaming cold miss (or force=1 refresh): run the pipeline now.
    match state.pipeline.run_pipeline(days, min_per_day, pool, None).await {
        Ok(payload) => {
            if payload.count > 0 {
                state.pipeline.cache.set(&cache_key, payload.clone());
            }
            // If the client wants paged shape (SYNC button hits force=1
            // with paged=1 to get a drop-in replacement for the normal
            // paged response), return the paginated slice.
            if paged {
                let result = apply_pagination(&payload, &q, "fresh");
                return Json(result).into_response();
            }
            Json(json!({
                "count": payload.count,
                "candidatePool": payload.candidate_pool,
                "daysWindow": payload.days_window,
                "minTradesPerDay": payload.min_trades_per_day,
                "traders": payload.traders,
                "source": "fresh",
                "syncedAt": payload.synced_at,
            })).into_response()
        }
        Err(e) => {
            (StatusCode::INTERNAL_SERVER_ERROR, Json(json!({"error": e.to_string()}))).into_response()
        }
    }
}

fn apply_pagination(payload: &crate::types::AggPayload, q: &ActiveTradersQuery, source: &str) -> Value {
    let sort = q.sort.as_deref().unwrap_or("pnl");
    let order = q.order.as_deref().unwrap_or("desc");
    let page = q.page.unwrap_or(0);
    let page_size = q.page_size.unwrap_or(25).clamp(1, 100);

    let mut traders = payload.traders.clone();

    let search_lower = q.search.as_ref().map(|s| s.to_lowercase());
    let cat = q.category.as_deref().unwrap_or("").to_lowercase();
    let has_query = search_lower.is_some() || !cat.is_empty();

    // When a search or category filter is active and per-market metrics are
    // available, recompute each trader's aggregate stats from ONLY the
    // matching markets. This lets users see e.g. a trader's crypto-specific
    // P&L rather than their overall numbers.
    if has_query {
        traders.retain_mut(|t| {
            if let Some(ref mm) = t.market_metrics {
                // Filter to markets matching the query
                let matching: Vec<_> = mm.iter().filter(|m| {
                    let title_lower = m.title.to_lowercase();
                    let search_ok = search_lower.as_ref().map_or(true, |s| {
                        t.address.contains(s.as_str()) || title_lower.contains(s.as_str())
                    });
                    let cat_ok = cat.is_empty() || crate::categories::title_in_category(&m.title, &cat);
                    search_ok && cat_ok
                }).collect();

                if matching.is_empty() && !search_lower.as_ref().map_or(false, |s| t.address.contains(s.as_str())) {
                    return false; // no matching markets → drop trader
                }

                if !matching.is_empty() {
                    // Recompute aggregate stats from matching markets
                    t.volume = matching.iter().map(|m| m.volume).sum();
                    t.buy_volume = matching.iter().map(|m| m.buy_volume).sum();
                    t.sell_volume = matching.iter().map(|m| m.sell_volume).sum();
                    t.pnl = matching.iter().map(|m| m.pnl).sum();
                    t.recent_trades = matching.iter().map(|m| m.trades).sum();
                    t.market_titles = matching.iter().map(|m| m.title.clone()).collect();
                    let total_wins: u32 = matching.iter().map(|m| m.wins).sum();
                    let total_sells: u32 = matching.iter().map(|m| m.sells).sum();
                    t.win_rate = if total_sells > 0 {
                        (total_wins as f64 / total_sells as f64 * 100.0).round()
                    } else { -1.0 };
                    t.pnl_curve = None; // curve reflects all trades, clear for consistency
                }
                true
            } else {
                // No per-market data (loaded from disk cache) — fall back to
                // the original title-based filtering without recomputation.
                let search_ok = search_lower.as_ref().map_or(true, |s| {
                    t.address.contains(s.as_str())
                        || t.market_titles.iter().any(|m| m.to_lowercase().contains(s.as_str()))
                });
                let cat_ok = cat.is_empty() || crate::categories::trader_in_category(&t.market_titles, &cat);
                search_ok && cat_ok
            }
        });
    }

    // Numeric filters (applied on the recomputed stats when query is active)
    if let Some(min_vol) = q.min_volume {
        if min_vol > 0.0 {
            traders.retain(|t| t.volume >= min_vol);
        }
    }
    if let Some(min_pnl) = q.min_pnl {
        traders.retain(|t| t.pnl >= min_pnl);
    }
    if let Some(min_t) = q.min_trades {
        if min_t > 0 {
            traders.retain(|t| t.recent_trades >= min_t);
        }
    }
    if let Some(min_bv) = q.min_buy_volume {
        if min_bv > 0.0 {
            traders.retain(|t| t.buy_volume >= min_bv);
        }
    }
    if let Some(min_sv) = q.min_sell_volume {
        if min_sv > 0.0 {
            traders.retain(|t| t.sell_volume >= min_sv);
        }
    }

    // Sort. With a category selected, rank first by how many of the
    // trader's market titles fall in the category — so the leaderboard
    // surfaces traders heavily in the vibe before falling back to the
    // primary metric (P&L / volume / etc.) as a tiebreaker.
    let dir: f64 = if order == "asc" { 1.0 } else { -1.0 };
    let cat_sort = cat.clone();
    traders.sort_by(|a, b| {
        if !cat_sort.is_empty() {
            let a_match = crate::categories::title_match_count(&a.market_titles, &cat_sort);
            let b_match = crate::categories::title_match_count(&b.market_titles, &cat_sort);
            if a_match != b_match {
                return b_match.cmp(&a_match);
            }
        }
        let cmp = match sort {
            "volume" => a.volume.partial_cmp(&b.volume),
            "positions" => a.positions.partial_cmp(&b.positions),
            "winRate" => a.win_rate.partial_cmp(&b.win_rate),
            _ => a.pnl.partial_cmp(&b.pnl),
        };
        let c = cmp.unwrap_or(std::cmp::Ordering::Equal);
        if dir < 0.0 { c.reverse() } else { c }
    });

    let total = traders.len();
    let start = (page * page_size) as usize;
    // Strip market_metrics before serialization (release memory)
    let sliced: Vec<_> = traders.into_iter().skip(start).take(page_size as usize)
        .map(|mut t| { t.market_metrics = None; t })
        .collect();

    json!({
        "traders": sliced,
        "total": total,
        "page": page,
        "pageSize": page_size,
        "count": payload.count,
        "candidatePool": payload.candidate_pool,
        "daysWindow": payload.days_window,
        "minTradesPerDay": payload.min_trades_per_day,
        "source": source,
        // Wall-clock unix-seconds when the underlying data was last refreshed.
        // Distinct from "when the client hit the cache" — drives the LIVE
        // staleness label so users see real source age, not cache hit age.
        "syncedAt": payload.synced_at,
    })
}

// ─── Deposit wallet endpoints ───────────────────────────────────────────

#[derive(Deserialize)]
struct DepositWalletInfoQuery {
    eoa: String,
}

/// Returns the V2 deposit-wallet address derived for `eoa`'s per-user
/// backend signer + the wallet's deployment state + its current USDC.e
/// balance on Polygon. Drives the WalletPanel UI.
async fn deposit_wallet_info(
    State(state): State<AppState>,
    Query(q): Query<DepositWalletInfoQuery>,
) -> impl IntoResponse {
    let backend_addr = match state.signer_store.signer_address(&q.eoa) {
        Ok(a) => a,
        Err(e) => return (
            StatusCode::BAD_REQUEST,
            Json(json!({"error": format!("signer: {}", e)})),
        ).into_response(),
    };
    let wallet = match crate::deposit_wallet::derive_deposit_wallet(&backend_addr) {
        Ok(w) => w,
        Err(e) => return (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({"error": format!("derive: {}", e)})),
        ).into_response(),
    };
    // Best-effort on-chain reads. Frontend polls so a transient RPC
    // failure self-heals on the next tick.
    let deployed = crate::relayer::is_deposit_wallet_deployed(&state.http, &wallet)
        .await
        .unwrap_or(false);
    let usdc_balance = crate::relayer::usdc_balance(&state.http, &wallet)
        .await
        .unwrap_or_else(|_| "0".to_string());

    Json(json!({
        "eoa": q.eoa.to_lowercase(),
        "backendSigner": backend_addr,
        "depositWallet": wallet,
        "deployed": deployed,
        // Stringified base-units (1e6 = $1) — avoids JS Number precision
        // loss on large balances. Frontend divides by 1e6 for display.
        "usdcBalance": usdc_balance,
    })).into_response()
}

#[derive(Deserialize)]
struct DepositWalletWithdrawRequest {
    eoa: String,
    /// 0x-prefixed 20-byte destination address. The contract has no
    /// recovery for sends to dead addresses, so we validate length on
    /// the backend before signing anything.
    destination: String,
    /// USDC.e amount as a base-units integer string (1e6 = $1). Sent
    /// stringified to dodge JS Number precision issues.
    #[serde(rename = "amountBaseUnits")]
    amount_base_units: String,
}

/// Signs a Batch typed-data containing a single `USDC.transfer(dest,
/// amount)` call via the user's deposit wallet, then submits to
/// Polymarket's relayer. Gasless from the user's perspective.
async fn deposit_wallet_withdraw(
    State(state): State<AppState>,
    Json(req): Json<DepositWalletWithdrawRequest>,
) -> impl IntoResponse {
    match crate::relayer::withdraw_usdc(
        &state.http,
        &state.signer_store,
        &req.eoa,
        &req.destination,
        &req.amount_base_units,
    ).await {
        Ok(resp) => Json(resp).into_response(),
        Err(e) => (
            StatusCode::BAD_GATEWAY,
            Json(json!({"error": format!("withdraw: {}", e)})),
        ).into_response(),
    }
}

#[derive(Deserialize)]
struct DepositWalletWrapRequest {
    eoa: String,
    /// Optional explicit amount (USDC.e base-units). When omitted, the
    /// backend reads the wallet's full USDC.e balance and wraps all of
    /// it — the common case after a deposit.
    #[serde(rename = "amountBaseUnits", default)]
    amount_base_units: Option<String>,
}

/// Submits a 2-call Batch (`USDC.e.approve(Onramp, MAX)` +
/// `Onramp.wrap(USDC.e, wallet, amount)`) through the deposit wallet,
/// converting raw USDC.e in the wallet into Polymarket V2 trading
/// collateral. Gasless. Returns once the relayer confirms the tx.
async fn deposit_wallet_wrap(
    State(state): State<AppState>,
    Json(req): Json<DepositWalletWrapRequest>,
) -> impl IntoResponse {
    match crate::relayer::wrap_usdce_to_collateral(
        &state.http,
        &state.signer_store,
        &req.eoa,
        req.amount_base_units.as_deref(),
    ).await {
        Ok(resp) => Json(resp).into_response(),
        Err(e) => (
            StatusCode::BAD_GATEWAY,
            Json(json!({"error": format!("wrap: {}", e)})),
        ).into_response(),
    }
}
