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
        // Long-running copy engine — see live_engine.rs. One session per
        // (eoa, strategyId): a wallet can fund and run several strats at
        // once, each budgeting against its own capital allocation.
        //   POST /live/start    — body: EngineConfig. Spawns a tokio task
        //                         keyed by eoa+strategyId. Persists config to
        //                         disk so the session survives API restarts.
        //   POST /live/stop     — body: {eoa, strategyId?}. Aborts the task and
        //                         deletes the persisted config. Without
        //                         strategyId, stops ALL of the wallet's
        //                         sessions (the old single-session behavior).
        //   GET  /live/status   — query: eoa, strategyId?. Current EngineState
        //                         JSON; falls back to the last persisted
        //                         snapshot when stopped. Without strategyId
        //                         it answers for any one running session.
        //   GET  /live/sessions — query: eoa. EVERY session this wallet has
        //                         (running or persisted), so the console can
        //                         show all funded strats side by side.
        .route("/live/start", post(live_start))
        .route("/live/stop", post(live_stop))
        .route("/live/status", get(live_status))
        .route("/live/sessions", get(live_sessions))
        // GET /live/bankroll — query: addresses=0xa,0xb. Each watched
        // trader's balance sheet (positions mark value + free USDC), the
        // denominator of proportional copy sizing. Served from the engine's
        // shared ~10m cache so the backtest sizes trades with the exact
        // number the live engine will use.
        .route("/live/bankroll", get(live_bankroll))
        // POST /live/execution — body: {eoa, strategyId?, autoExecute}. Flips
        // real order placement on/off for one session (default off = DRY RUN).
        .route("/live/execution", post(live_execution))
        // POST /liquidate — body: {eoa}. Sell EVERY position the account's
        // deposit wallet holds, at the marketable price. Places real orders
        // regardless of the session's autoExecute flag. The scheduled
        // "flatten every N hours" task (POLYMARKET_LIQUIDATE_EVERY_HOURS)
        // calls the same path; this endpoint is the manual "sell now" trigger.
        .route("/liquidate", post(liquidate_handler))
        // Background trader-data sync schedule — see sync.rs. Every 5 min by
        // default; the owner can change the cadence, pause it, or force a
        // run now. Owner-only comes free: the access gate (access.rs) wraps
        // every route below /health.
        //   GET  /sync/status — cadence + last/next run + last error
        //   POST /sync/config — body: {enabled?, intervalSecs|intervalMinutes|
        //                       intervalHours}. Persists to sync.json and
        //                       re-schedules the sleeping task immediately.
        //   POST /sync/run    — run a cycle now (bypasses the freshness skip)
        .route("/sync/status", get(sync_status))
        .route("/sync/config", get(sync_status).post(sync_config))
        .route("/sync/run", post(sync_run))
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
        // Send wrapped V2 collateral back out as USDC.e in one tx:
        // approves Offramp + calls Offramp.unwrap(USDC.e, dest, amount)
        // through the wallet's Batch flow. Used for "send my trading
        // balance to my MetaMask" without a separate unwrap step.
        .route("/deposit-wallet/unwrap-and-send", post(deposit_wallet_unwrap_send))
        // Redeem RESOLVED positions (winning outcome tokens → USDC). A SELL
        // can't cash these out — once a market settles it has no order book,
        // so SELL ALL bounces with "invalid token id" / "not enough balance".
        // redeemPositions against the ConditionalTokens contract is the only
        // way to recover settled winnings; this batches every resolved
        // condition through the deposit wallet, gaslessly.
        .route("/redeem", post(redeem_handler))
        // User-uploaded strats (mod.py / mod.rs). Storage only — execution
        // is a follow-up; the endpoints are here so the framework + UI
        // upload flow are ready ahead of the runtime hookup.
        .route("/user-strats", get(user_strats_list).post(user_strats_upload))
        // Community gallery of public strats (shared by other traders).
        .route("/user-strats/public", get(user_strats_public))
        // Import a strat shared by CID (content-addressable; localfs default).
        // Single static segment — declared before the `/:id/:kind` catch-all.
        .route("/user-strats/import", post(user_strats_import))
        // Template route MUST come before the `/:id/:kind` catch-all
        // below — axum matches in declaration order and otherwise this
        // resolves as `id=template, kind=<name>` and 400s on `kind`.
        .route("/user-strats/template/:name", get(user_strats_template))
        // Sharing actions: flip a strat public/private; fork someone's strat
        // into your own. Static second segments win over the `:kind` route.
        .route("/user-strats/:id/publish", post(user_strats_publish))
        .route("/user-strats/:id/fork", post(user_strats_fork))
        // Share a strat → CID (content-addressable; localfs default).
        .route("/user-strats/:id/share", post(user_strats_share))
        .route("/user-strats/:id/:kind", get(user_strats_read).delete(user_strats_delete))
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
    Json(body): Json<serde_json::Value>,
) -> impl IntoResponse {
    // Did the caller explicitly send `autoExecute`? A bare config re-post /
    // reconfigure from the UI omits it, and serde would then default it to
    // false — silently reverting a live session to DRY RUN. Detect presence
    // here so an omitted flag inherits the session's current mode instead.
    let explicit_auto = body.get("autoExecute").is_some();
    let mut cfg: crate::live_engine::EngineConfig = match serde_json::from_value(body) {
        Ok(c) => c,
        Err(e) => return (
            StatusCode::BAD_REQUEST,
            Json(json!({"error": format!("invalid config: {}", e)})),
        ).into_response(),
    };
    if !explicit_auto {
        if let Some(prev) = state.engines.current_auto_execute(&cfg.eoa, &cfg.strategy_id) {
            cfg.auto_execute = prev;
        }
    }
    state.engines.start(cfg);
    Json(json!({"ok": true})).into_response()
}

#[derive(Deserialize)]
struct LiveEoaBody {
    eoa: String,
    /// Which funded strat to stop. Omitted ⇒ every session this wallet runs.
    #[serde(rename = "strategyId", default)]
    strategy_id: Option<String>,
}

async fn live_stop(
    State(state): State<AppState>,
    Json(body): Json<LiveEoaBody>,
) -> impl IntoResponse {
    let stopped = state.engines.stop(&body.eoa, body.strategy_id.as_deref());
    Json(json!({"ok": stopped})).into_response()
}

#[derive(Deserialize)]
struct LiveStatusQuery {
    eoa: String,
    #[serde(rename = "strategyId", default)]
    strategy_id: Option<String>,
}

/// One session's `{running, config, state}` envelope — running engine first,
/// else the last snapshot persisted to disk.
fn session_envelope(
    state: &AppState,
    eoa: &str,
    strategy_id: Option<&str>,
) -> serde_json::Value {
    match state.engines.status_of(eoa, strategy_id) {
        Some(s) => json!({
            "running": true,
            "config": state.engines.config_of(eoa, strategy_id),
            "state": s,
        }),
        // Stopped engine: serve the last persisted snapshot so the per-strat
        // ledger / open positions don't vanish from the UI between sessions.
        None => match state.engines.persisted_snapshot(eoa, strategy_id) {
            Some((cfg, st)) => json!({"running": false, "config": cfg, "state": st}),
            None => json!({"running": false}),
        },
    }
}

async fn live_status(
    State(state): State<AppState>,
    Query(q): Query<LiveStatusQuery>,
) -> impl IntoResponse {
    Json(session_envelope(&state, &q.eoa, q.strategy_id.as_deref())).into_response()
}

#[derive(Deserialize)]
struct LiveSessionsQuery {
    eoa: String,
}

/// Every session this wallet has — the multi-strat view. The console reads
/// this to render each funded strat's own engine state, ledger and positions
/// instead of assuming one live strat per wallet.
async fn live_sessions(
    State(state): State<AppState>,
    Query(q): Query<LiveSessionsQuery>,
) -> impl IntoResponse {
    let sessions: Vec<serde_json::Value> = state
        .engines
        .session_ids(&q.eoa)
        .into_iter()
        .map(|sid| {
            let mut env = session_envelope(&state, &q.eoa, Some(&sid));
            env["strategyId"] = json!(sid);
            env
        })
        .collect();
    Json(json!({"sessions": sessions})).into_response()
}

#[derive(Deserialize)]
struct LiveBankrollQuery {
    /// Comma-separated trader addresses.
    addresses: String,
}

/// Each trader's bankroll — the denominator proportional copy sizing divides
/// by. The console's backtest calls this so its preview sizes trades with the
/// same number the live engine uses; without it the preview would fall back to
/// the volume model and predict sizes live would never place.
async fn live_bankroll(
    State(state): State<AppState>,
    Query(q): Query<LiveBankrollQuery>,
) -> impl IntoResponse {
    let addrs: Vec<String> = q
        .addresses
        .split(',')
        .map(|s| s.trim().to_string())
        .filter(|s| s.starts_with("0x") && s.len() == 42)
        .take(64)
        .collect();
    let mut out = serde_json::Map::new();
    for a in addrs {
        // Engine cache: repeated console loads cost nothing.
        if let Some(bankroll) = state.engines.bankroll_of(&a).await {
            out.insert(a.to_lowercase(), json!(bankroll));
        }
    }
    Json(json!({"bankrolls": out})).into_response()
}

#[derive(Deserialize)]
struct LiveExecutionBody {
    eoa: String,
    #[serde(rename = "strategyId", default)]
    strategy_id: Option<String>,
    #[serde(rename = "autoExecute")]
    auto_execute: bool,
}

/// Toggle autonomous order placement for a live session. With `autoExecute`
/// false (the default a session starts in) the engine runs DRY RUN: it logs
/// every mirror it would place but sends nothing to the CLOB. Flipping this to
/// true makes it place real orders via the backend signer.
async fn live_execution(
    State(state): State<AppState>,
    Json(body): Json<LiveExecutionBody>,
) -> impl IntoResponse {
    match state.engines.set_auto_execute(
        &body.eoa,
        body.strategy_id.as_deref(),
        body.auto_execute,
    ) {
        Some(v) => Json(json!({"ok": true, "autoExecute": v})).into_response(),
        None => (
            StatusCode::NOT_FOUND,
            Json(json!({"ok": false, "error": "no live session for eoa"})),
        )
            .into_response(),
    }
}

#[derive(Deserialize)]
struct LiquidateBody {
    eoa: String,
    /// Only sell positions whose best bid is at or above this price
    /// (e.g. 0.99 = harvest near-certain winners into free capital).
    /// Omitted = flatten everything.
    #[serde(rename = "minPrice", default)]
    min_price: Option<f64>,
}

/// Flatten an account: sell every held position at the marketable price.
async fn liquidate_handler(
    State(state): State<AppState>,
    Json(body): Json<LiquidateBody>,
) -> impl IntoResponse {
    match state.engines.liquidate_all(&body.eoa, body.min_price).await {
        Ok(result) => Json(json!({"ok": true, "result": result})).into_response(),
        Err(e) => (
            StatusCode::BAD_REQUEST,
            Json(json!({"ok": false, "error": e.to_string()})),
        )
            .into_response(),
    }
}

// ─── Background sync schedule ───────────────────────────────────────────

/// Cadence + last/next run of the background trader-data sync. Everything
/// the console's SYNC panel renders.
async fn sync_status(State(state): State<AppState>) -> impl IntoResponse {
    Json(state.sync.status_json())
}

#[derive(Deserialize)]
struct SyncConfigRequest {
    enabled: Option<bool>,
    /// Cadence in seconds. `intervalMinutes` / `intervalHours` are accepted as
    /// conveniences so CLI callers don't have to do the arithmetic.
    #[serde(rename = "intervalSecs")]
    interval_secs: Option<u64>,
    #[serde(rename = "intervalMinutes")]
    interval_minutes: Option<f64>,
    #[serde(rename = "intervalHours")]
    interval_hours: Option<f64>,
}

async fn sync_config(
    State(state): State<AppState>,
    Json(req): Json<SyncConfigRequest>,
) -> impl IntoResponse {
    let secs = req
        .interval_secs
        .or_else(|| req.interval_minutes.map(|m| (m * 60.0).round() as u64))
        .or_else(|| req.interval_hours.map(|h| (h * 3600.0).round() as u64));
    match state.sync.update(req.enabled, secs) {
        Ok(()) => Json(state.sync.status_json()).into_response(),
        Err(e) => (
            StatusCode::BAD_REQUEST,
            Json(json!({"ok": false, "error": e})),
        )
            .into_response(),
    }
}

/// Run a cycle now. Returns immediately — the scheduler task picks the
/// request up, so a manual run can never overlap a scheduled one. Poll
/// `/sync/status` for progress.
async fn sync_run(State(state): State<AppState>) -> impl IntoResponse {
    state.sync.trigger_now();
    Json(state.sync.status_json())
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
    // Capture the side before `req` is moved into place_order so the error
    // message can name the right asset (BUY needs USDC, SELL needs shares).
    let side = req.args.side;
    match crate::order_place::place_order(&state.http, &state.signer_store, req).await {
        Ok(resp) => Json(resp).into_response(),
        Err(e) => {
            // `place_order` surfaces an upstream CLOB rejection as
            // "CLOB /order HTTP <status>: <body>". A client-side rejection
            // (e.g. 400 "not enough balance") must NOT be flattened into a 502:
            // doing so makes proxies (Cloudflare) serve an opaque HTML error
            // page and hides the real, actionable reason from the UI. Forward
            // the true status + a clean message instead.
            let (code, error) = classify_place_error(&e.to_string(), side);
            (code, Json(json!({ "error": error }))).into_response()
        }
    }
}

/// Map a `place_order` error string to an HTTP status + human message.
/// Recognizes the "CLOB /order HTTP <status>: <body>" shape and forwards the
/// upstream status for 4xx (client) errors; anything else stays a 502.
fn classify_place_error(msg: &str, side: crate::order_place::OrderSide) -> (StatusCode, String) {
    // Insufficient on-chain balance/allowance is the single most common cause —
    // give it a crisp, actionable message. CLOB returns the SAME "not enough
    // balance" string for both sides, but the cause is opposite: a BUY is short
    // USDC collateral, a SELL is short the outcome token (shares) or its CTF
    // allowance. Naming USDC on a SELL sent users to fund a wallet that was
    // never the problem.
    if msg.contains("not enough balance") || msg.contains("balance is not enough") {
        let detail = match side {
            crate::order_place::OrderSide::Buy =>
                "the trading wallet has no USDC. Fund the backend deposit wallet \
                 with USDC on Polygon (and approve the CLOB allowance) before buying.",
            crate::order_place::OrderSide::Sell =>
                "the trading wallet does not hold enough of this outcome's shares \
                 (or the CTF allowance isn't set). Confirm the position is held by \
                 the V2 deposit wallet that signs orders — selling a position held \
                 by a different wallet always fails here.",
        };
        return (
            StatusCode::PAYMENT_REQUIRED, // 402 — funding/holdings required
            format!("Insufficient balance/allowance: {}", detail),
        );
    }
    // Generic upstream status forwarding: "... HTTP <code>: <body>".
    if let Some(idx) = msg.find("HTTP ") {
        let tail = &msg[idx + 5..];
        let digits: String = tail.chars().take_while(|c| c.is_ascii_digit()).collect();
        if let Ok(code) = digits.parse::<u16>() {
            if (400..500).contains(&code) {
                let status = StatusCode::from_u16(code).unwrap_or(StatusCode::BAD_REQUEST);
                return (status, format!("Order rejected by venue: {}", msg));
            }
        }
    }
    (StatusCode::BAD_GATEWAY, format!("place order: {}", msg))
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
    // Free-text market-topic query — finer than `cat`, never matches address.
    let mq = q.market_query.as_deref().unwrap_or("").trim().to_string();
    let has_mq = !mq.is_empty();
    let has_query = search_lower.is_some() || !cat.is_empty() || has_mq;

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
                    let mq_ok = !has_mq || crate::categories::market_matches_query(&m.title, &mq);
                    search_ok && cat_ok && mq_ok
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
                    // Buy-accuracy over ONLY the matching markets — same
                    // definition as the pipeline: bought positions that
                    // ended up winning (saturated to $1) over decided
                    // positions, capped at 100.
                    let total_wins: u32 = matching.iter().map(|m| m.wins).sum();
                    let total_decided: u32 = matching.iter().map(|m| m.decided).sum();
                    t.win_rate = if total_decided > 0 {
                        (total_wins as f64 / total_decided as f64 * 100.0).round().min(100.0)
                    } else { -1.0 };
                    // Sharpe scoped to the matching markets' closed-trade
                    // returns — same query-scoped recompute the other stats
                    // get, via the ONE `stats_from_returns` formula.
                    let scoped_returns: Vec<f64> = matching.iter()
                        .flat_map(|m| m.returns.iter().copied())
                        .collect();
                    t.sharpe = crate::live_engine::stats_from_returns(&scoped_returns).sharpe;
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
                let mq_ok = !has_mq || crate::categories::trader_matches_query(&t.market_titles, &mq);
                search_ok && cat_ok && mq_ok
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
    let mq_sort = mq.clone();
    traders.sort_by(|a, b| {
        // Topic-query match count is the most specific signal — rank by it
        // first so the traders heaviest in the queried topic lead.
        if !mq_sort.is_empty() {
            let a_q = crate::categories::query_match_count(&a.market_titles, &mq_sort);
            let b_q = crate::categories::query_match_count(&b.market_titles, &mq_sort);
            if a_q != b_q {
                return b_q.cmp(&a_q);
            }
        }
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
            // Default SCORE metric — lets server pagination order the whole
            // set by Sharpe instead of the old pnl proxy.
            "sharpe" => a.sharpe.partial_cmp(&b.sharpe),
            // Missing timestamp (pre-lastTradeTs disk cache) sinks to the
            // bottom on desc — unknown recency must not outrank known.
            "last" => Some(a.last_trade_ts.unwrap_or(0).cmp(&b.last_trade_ts.unwrap_or(0))),
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
    let (deployed, usdc_balance, raw_usdce, native_usdc) = tokio::join!(
        crate::relayer::is_deposit_wallet_deployed(&state.http, &wallet),
        crate::relayer::usdc_balance(&state.http, &wallet),
        crate::relayer::raw_usdce_balance(&state.http, &wallet),
        crate::relayer::native_usdc_balance(&state.http, &wallet),
    );

    // A failed on-chain read must NOT masquerade as a zero balance — that
    // was the phantom "$0.00 / deposit now" bug. Emit `null` for any field
    // whose RPC read errored so the frontend can show "unavailable" and the
    // copy engine can keep its last-known balance instead of self-gating.
    let usdc_opt = usdc_balance.ok();
    let balance_unavailable = usdc_opt.is_none();
    Json(json!({
        "eoa": q.eoa.to_lowercase(),
        "backendSigner": backend_addr,
        "depositWallet": wallet,
        "deployed": deployed.unwrap_or(false),
        // Stringified base-units (1e6 = $1) — avoids JS Number precision
        // loss on large balances. Frontend divides by 1e6 for display.
        // usdcBalance is the WRAPPED trading collateral (what Polymarket
        // counts). rawUsdceBalance is un-wrapped USDC.e sitting in the
        // wallet (deposited but not yet tradable — needs /wrap).
        // nativeUsdcBalance is Circle-native USDC, which the Onramp can't
        // wrap at all; shown so funds are never invisible.
        // `null` = read failed (unknown), distinct from "0" = confirmed empty.
        "usdcBalance": usdc_opt,
        "rawUsdceBalance": raw_usdce.ok(),
        "nativeUsdcBalance": native_usdc.ok(),
        // True when the primary balance read failed — consumers should treat
        // the balance as unknown, not zero.
        "balanceUnavailable": balance_unavailable,
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

#[derive(Deserialize)]
struct RedeemRequest {
    eoa: String,
    /// Wrap the redeemed USDC.e into V2 trading collateral afterward so the
    /// proceeds show as spendable balance (and can be withdrawn). Defaults
    /// to `true`; set `false` to leave raw USDC.e in the wallet.
    #[serde(rename = "wrapAfter", default = "default_wrap_after")]
    wrap_after: bool,
}
fn default_wrap_after() -> bool { true }

/// Redeem every resolved position the deposit wallet holds, converting
/// winning outcome tokens to USDC in one gasless relayer Batch. This is the
/// cash-out path for SETTLED markets, which a SELL can't touch.
async fn redeem_handler(
    State(state): State<AppState>,
    Json(req): Json<RedeemRequest>,
) -> impl IntoResponse {
    match crate::relayer::redeem_resolved_positions(
        &state.http,
        &state.signer_store,
        &req.eoa,
        req.wrap_after,
    )
    .await
    {
        Ok(r) => Json(r).into_response(),
        Err(e) => (
            StatusCode::BAD_GATEWAY,
            Json(json!({"error": format!("redeem: {}", e)})),
        )
            .into_response(),
    }
}

#[derive(Deserialize)]
struct UnwrapSendRequest {
    eoa: String,
    destination: String,
    /// V2 collateral amount in base units (1e6 = $1).
    #[serde(rename = "amountBaseUnits")]
    amount_base_units: String,
}

async fn deposit_wallet_unwrap_send(
    State(state): State<AppState>,
    Json(req): Json<UnwrapSendRequest>,
) -> impl IntoResponse {
    match crate::relayer::unwrap_and_send(
        &state.http,
        &state.signer_store,
        &req.eoa,
        &req.destination,
        &req.amount_base_units,
    ).await {
        Ok(resp) => Json(resp).into_response(),
        Err(e) => (
            StatusCode::BAD_GATEWAY,
            Json(json!({"error": format!("unwrap-and-send: {}", e)})),
        ).into_response(),
    }
}

// ─── User-uploaded strats (mod.py / mod.rs) ─────────────────────────────

#[derive(Deserialize)]
struct UploadStratBody {
    id: String,
    /// "py" or "rs". Determines the file extension; future runtimes will
    /// dispatch by this field.
    kind: crate::user_strats::StratKind,
    /// Raw source text. UTF-8, ≤ 256 KiB — validated by the store.
    content: String,
    // ── sharing metadata (all optional; legacy editor omits them) ──
    owner: Option<String>,
    title: Option<String>,
    description: Option<String>,
    public: Option<bool>,
}

/// `?owner=0x..` scopes the listing to one trader (their strats + legacy
/// unclaimed uploads). Omitted → list everything (back-compat).
#[derive(Deserialize)]
struct OwnerQuery {
    owner: Option<String>,
}

async fn user_strats_list(
    State(state): State<AppState>,
    Query(q): Query<OwnerQuery>,
) -> impl IntoResponse {
    match state.user_strats.list(q.owner.as_deref()) {
        Ok(items) => Json(json!({"strats": items})).into_response(),
        Err(e) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({"error": format!("list: {}", e)})),
        ).into_response(),
    }
}

/// Community gallery — every public strat. `?owner=` (the viewer) tags
/// which entries are the viewer's own.
async fn user_strats_public(
    State(state): State<AppState>,
    Query(q): Query<OwnerQuery>,
) -> impl IntoResponse {
    match state.user_strats.list_public(q.owner.as_deref()) {
        Ok(items) => Json(json!({"strats": items})).into_response(),
        Err(e) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({"error": format!("public: {}", e)})),
        ).into_response(),
    }
}

async fn user_strats_upload(
    State(state): State<AppState>,
    Json(req): Json<UploadStratBody>,
) -> impl IntoResponse {
    let opts = crate::user_strats::UploadMeta {
        owner: req.owner,
        title: req.title,
        description: req.description,
        public: req.public,
    };
    match state.user_strats.upload(&req.id, req.kind, &req.content, &opts) {
        Ok(entry) => Json(entry).into_response(),
        Err(e) => (
            StatusCode::BAD_REQUEST,
            Json(json!({"error": format!("upload: {}", e)})),
        ).into_response(),
    }
}

#[derive(Deserialize)]
struct PublishBody {
    owner: String,
    public: bool,
}

/// Flip a strat's public visibility. Owner-gated.
async fn user_strats_publish(
    State(state): State<AppState>,
    axum::extract::Path(id): axum::extract::Path<String>,
    Json(req): Json<PublishBody>,
) -> impl IntoResponse {
    match state.user_strats.set_public(&id, &req.owner, req.public) {
        Ok(entry) => Json(entry).into_response(),
        Err(e) => (
            StatusCode::BAD_REQUEST,
            Json(json!({"error": format!("publish: {}", e)})),
        ).into_response(),
    }
}

#[derive(Deserialize)]
struct ForkBody {
    /// New id for the forked copy (owned by `owner`).
    #[serde(rename = "newId")]
    new_id: String,
    owner: String,
}

/// Fork an existing strat (`:id`) into `newId` owned by the caller.
async fn user_strats_fork(
    State(state): State<AppState>,
    axum::extract::Path(id): axum::extract::Path<String>,
    Json(req): Json<ForkBody>,
) -> impl IntoResponse {
    match state.user_strats.fork(&id, &req.new_id, &req.owner) {
        Ok(entry) => Json(entry).into_response(),
        Err(e) => (
            StatusCode::BAD_REQUEST,
            Json(json!({"error": format!("fork: {}", e)})),
        ).into_response(),
    }
}

#[derive(Deserialize)]
struct ShareBody {
    /// Viewer EOA — required to share a *private* strat (must be the owner);
    /// optional for public strats, which anyone may share.
    #[serde(default)]
    owner: Option<String>,
}

/// Share a strat: bundle its source + metadata, store the bytes in the
/// content-addressable backend (localfs by default), and return the CID.
/// The CID is the portable share link — see `share.rs` for how the backend
/// is swapped for other systems.
async fn user_strats_share(
    State(state): State<AppState>,
    axum::extract::Path(id): axum::extract::Path<String>,
    Json(req): Json<ShareBody>,
) -> impl IntoResponse {
    let bundle = match state.user_strats.bundle(&id, req.owner.as_deref()) {
        Ok(b) => b,
        Err(e) => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"error": format!("share: {}", e)})),
            ).into_response()
        }
    };
    let bytes = match serde_json::to_vec(&bundle) {
        Ok(b) => b,
        Err(e) => {
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"error": format!("serialize bundle: {}", e)})),
            ).into_response()
        }
    };
    match state.share.put_and_pin(&state.http, bytes).await {
        Ok(cid) => Json(json!({
            "ok": true,
            "cid": cid,
            "id": id,
            "backend": state.share.label(),
        })).into_response(),
        Err(e) => (
            StatusCode::BAD_GATEWAY,
            Json(json!({"error": format!("share store unavailable: {}", e)})),
        ).into_response(),
    }
}

#[derive(Deserialize)]
struct ImportBody {
    /// CID of a previously-shared strat bundle.
    cid: String,
    /// EOA that will own the imported copy.
    #[serde(default)]
    owner: Option<String>,
    /// Optional preferred id for the imported copy (auto-suffixed on clash).
    #[serde(default)]
    id: Option<String>,
}

/// Import a strat by CID: fetch the bundle from the share backend, validate
/// it, and write it as a new strat owned by the caller (private, lineage
/// tracked back to the original).
async fn user_strats_import(
    State(state): State<AppState>,
    Json(req): Json<ImportBody>,
) -> impl IntoResponse {
    let bytes = match state.share.get(&state.http, req.cid.trim()).await {
        Ok(b) => b,
        Err(e) => {
            return (
                StatusCode::BAD_GATEWAY,
                Json(json!({"error": format!("fetch {}: {}", req.cid, e)})),
            ).into_response()
        }
    };
    let bundle: crate::user_strats::StratBundle = match serde_json::from_slice(&bytes) {
        Ok(b) => b,
        Err(_) => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"error": format!("blob at {} is not a polymarket strat bundle", req.cid)})),
            ).into_response()
        }
    };
    let owner = req.owner.unwrap_or_default();
    match state
        .user_strats
        .import_bundle(&bundle, &owner, req.id.as_deref())
    {
        Ok(entry) => Json(entry).into_response(),
        Err(e) => (
            StatusCode::BAD_REQUEST,
            Json(json!({"error": format!("import: {}", e)})),
        ).into_response(),
    }
}

#[derive(Deserialize)]
struct UserStratParams {
    id: String,
    kind: crate::user_strats::StratKind,
}

async fn user_strats_read(
    State(state): State<AppState>,
    axum::extract::Path(p): axum::extract::Path<UserStratParams>,
) -> impl IntoResponse {
    match state.user_strats.read(&p.id, p.kind) {
        Ok(content) => Json(json!({"id": p.id, "kind": p.kind, "content": content})).into_response(),
        Err(e) => (
            StatusCode::NOT_FOUND,
            Json(json!({"error": format!("read: {}", e)})),
        ).into_response(),
    }
}

async fn user_strats_delete(
    State(state): State<AppState>,
    axum::extract::Path(p): axum::extract::Path<UserStratParams>,
    Query(q): Query<OwnerQuery>,
) -> impl IntoResponse {
    match state.user_strats.delete(&p.id, q.owner.as_deref()) {
        Ok(()) => Json(json!({"ok": true})).into_response(),
        Err(e) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({"error": format!("delete: {}", e)})),
        ).into_response(),
    }
}

async fn user_strats_template(
    axum::extract::Path(name): axum::extract::Path<String>,
) -> impl IntoResponse {
    // Embed the reference files at compile time so the template route
    // works regardless of where the binary runs (container has the
    // sources at /app/src/strats but production-friendlier to bundle
    // them in). Keys must be the *exact* set we want exposed; a typo
    // shouldn't expose anything else from the FS.
    // Paths are relative to *this file* (src/api/src/routes.rs) so
    // they resolve identically whether the api builds in-place (dev)
    // or in the Docker `api-builder` stage (which copies the strats
    // into `/build/strats/`, sibling of the api crate root).
    let body: Option<&'static str> = match name.as_str() {
        "base" => Some(include_str!("../../strats/base/mod.py")),
        "copytrader" => Some(include_str!("../../strats/copytrader/mod.py")),
        "example_ev_strat" => Some(include_str!("../../strats/example_ev_strat/mod.py")),
        _ => None,
    };
    match body {
        Some(content) => Json(json!({
            "name": name,
            "kind": "py",
            "content": content,
        })).into_response(),
        None => (
            StatusCode::NOT_FOUND,
            Json(json!({"error": format!("no template named '{}'", name)})),
        ).into_response(),
    }
}
