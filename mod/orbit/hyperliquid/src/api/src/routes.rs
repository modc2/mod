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

/// 400 with a sentence, not a struct dump.
fn bad_request(msg: impl Into<String>) -> (StatusCode, Json<Value>) {
    (StatusCode::BAD_REQUEST, Json(json!({"error": msg.into()})))
}

/// Resolve whose row this is.
///
/// The auth guard has already recovered the caller's address from their token
/// and pinned any address the body names to it, so an explicit field can only
/// ever agree — which makes it redundant. Prefer the body when present (open
/// mode and server-to-server callers still pass it), fall back to the token,
/// and only fail when neither exists.
fn caller_address(
    from_body: Option<&str>,
    user: Option<Extension<crate::auth::AuthedUser>>,
    field: &str,
) -> Result<String, (StatusCode, Json<Value>)> {
    from_body
        .map(str::trim)
        .filter(|a| !a.is_empty())
        .map(|a| a.to_lowercase())
        .or_else(|| user.map(|Extension(u)| u.0))
        .ok_or_else(|| bad_request(format!(
            "`{field}` is required — sign in, or pass the address explicitly"
        )))
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
        .route("/trader/:addr/curve", get(trader_curve))
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

        // ── MCP tool server (JSON-RPC 2.0) ──
        // Streamable HTTP on /mcp, plus the older HTTP+SSE pair on
        // /sse + /messages so clients that only speak that still connect.
        .route("/mcp", post(mcp_post).get(mcp_get))
        .route("/mcp/schema", get(mcp_schema))
        .route("/sse", get(mcp_sse))
        .route("/messages", post(mcp_messages))

        // ── agent: answers questions / runs tasks through that MCP server ──
        .route("/ask", post(crate::agent::ask))
        .route("/ask/status", get(crate::agent::ask_status))

        // ── generic mod-protocol passthrough ──
        .route("/forward", post(forward))

        // ── the investment book (invest_routes.rs): one verb over vaults,
        //    traders and strat baskets alike ──
        .merge(crate::invest_routes::router())
}

// ── MCP ──

/// Streamable HTTP: one JSON-RPC message — or a batch of them — per POST.
/// Tool calls re-enter this server over loopback carrying the caller's
/// Authorization header (see mcp.rs).
async fn mcp_post(
    State(s): State<AppState>,
    headers: axum::http::HeaderMap,
    body: axum::body::Bytes,
) -> axum::response::Response {
    use axum::response::IntoResponse;
    let payload: Value = match serde_json::from_slice(&body) {
        Ok(v) => v,
        Err(e) => {
            let err = crate::mcp::rpc_error(Value::Null, -32700, &format!("parse error: {e}"));
            return (StatusCode::BAD_REQUEST, Json(err)).into_response();
        }
    };
    let auth = header_str(&headers, axum::http::header::AUTHORIZATION);
    match crate::mcp::handle_payload(&s.self_url, &payload, auth).await {
        // The spec lets us answer with either JSON or a stream. Prefer JSON,
        // but a client that accepts *only* text/event-stream gets its
        // response as a one-event stream rather than a 406.
        Some(resp) if wants_only_sse(&headers) => sse_once(resp).into_response(),
        Some(resp) => Json(resp).into_response(),
        // Notifications carry no id and get no body — 202 per the spec.
        None => StatusCode::ACCEPTED.into_response(),
    }
}

fn header_str(headers: &axum::http::HeaderMap, name: axum::http::HeaderName) -> Option<&str> {
    headers.get(name).and_then(|h| h.to_str().ok())
}

/// True when the client asked for an event stream and would not take JSON.
fn wants_only_sse(headers: &axum::http::HeaderMap) -> bool {
    let accept = header_str(headers, axum::http::header::ACCEPT).unwrap_or_default();
    accept.contains("text/event-stream")
        && !accept.contains("application/json")
        && !accept.contains("*/*")
}

/// One JSON-RPC response, wrapped as a single-event SSE body.
fn sse_once(resp: Value) -> impl axum::response::IntoResponse {
    use axum::response::sse::{Event, Sse};
    let stream = futures::stream::once(async move {
        Ok::<_, std::convert::Infallible>(Event::default().event("message").data(resp.to_string()))
    });
    Sse::new(stream)
}

/// GET /mcp — this server keeps no server-initiated stream on the Streamable
/// HTTP endpoint, which the spec answers with 405. Clients that want a stream
/// use the HTTP+SSE transport at /sse instead.
async fn mcp_get() -> (StatusCode, Json<Value>) {
    (StatusCode::METHOD_NOT_ALLOWED, Json(json!({
        "error": "POST JSON-RPC 2.0 messages to this endpoint",
        "schema": "/mcp/schema",
        "sse_transport": "/sse",
    })))
}

/// HTTP+SSE transport, the half a client opens first. The stream's first
/// event names where to POST messages — as a *relative* URL, so it resolves
/// correctly whether this API is reached directly or behind the gateway's
/// `/api/hyperliquid` prefix. Responses to those POSTs arrive here.
async fn mcp_sse(headers: axum::http::HeaderMap) -> axum::response::Response {
    use axum::response::sse::{Event, KeepAlive, Sse};
    use axum::response::IntoResponse;

    let session = uuid::Uuid::new_v4().to_string();
    let auth = header_str(&headers, axum::http::header::AUTHORIZATION).map(str::to_string);
    let rx = crate::mcp::open_session(session.clone(), auth);

    // Dropping the stream (client hangs up) drops this guard, which is what
    // reaps the session — no sweeper needed.
    struct Reap(String);
    impl Drop for Reap {
        fn drop(&mut self) {
            crate::mcp::close_session(&self.0);
        }
    }

    let endpoint = format!("messages?sessionId={session}");
    let head = futures::stream::once(async move {
        Ok::<_, std::convert::Infallible>(Event::default().event("endpoint").data(endpoint))
    });
    let reap = Reap(session);
    let body = futures::stream::unfold((rx, reap), |(mut rx, reap)| async move {
        let msg = rx.recv().await?;
        Some((
            Ok::<_, std::convert::Infallible>(Event::default().event("message").data(msg)),
            (rx, reap),
        ))
    });

    Sse::new(futures::StreamExt::chain(head, body))
        .keep_alive(KeepAlive::default())
        .into_response()
}

#[derive(Deserialize)]
struct SessionQuery {
    /// Clients send `sessionId` (the name that transport's spec uses); the
    /// snake_case alias is there for hand-written callers.
    #[serde(rename = "sessionId", alias = "session_id")]
    session_id: Option<String>,
}

/// HTTP+SSE transport, the half a client POSTs to. The response goes out on
/// the matching SSE stream; this returns 202, as that transport specifies.
async fn mcp_messages(
    State(s): State<AppState>,
    Query(q): Query<SessionQuery>,
    headers: axum::http::HeaderMap,
    body: axum::body::Bytes,
) -> axum::response::Response {
    use axum::response::IntoResponse;
    let Some(session) = q.session_id else {
        return (StatusCode::BAD_REQUEST, Json(json!({
            "error": "missing sessionId — open GET /sse first and use the endpoint it sends",
        }))).into_response();
    };
    if !crate::mcp::session_exists(&session) {
        return (StatusCode::NOT_FOUND, Json(json!({
            "error": "unknown or closed session — reopen GET /sse",
        }))).into_response();
    }
    let payload: Value = match serde_json::from_slice(&body) {
        Ok(v) => v,
        Err(e) => {
            let err = crate::mcp::rpc_error(Value::Null, -32700, &format!("parse error: {e}"));
            return (StatusCode::BAD_REQUEST, Json(err)).into_response();
        }
    };
    // Credentials on the POST win; otherwise fall back to whatever the stream
    // was opened with, since some clients only authenticate the GET.
    let auth = header_str(&headers, axum::http::header::AUTHORIZATION)
        .map(str::to_string)
        .or_else(|| crate::mcp::session_authorization(&session));

    if let Some(resp) = crate::mcp::handle_payload(&s.self_url, &payload, auth.as_deref()).await {
        if !crate::mcp::session_send(&session, &resp) {
            return (StatusCode::GONE, Json(json!({
                "error": "the SSE stream for this session is closed",
            }))).into_response();
        }
    }
    StatusCode::ACCEPTED.into_response()
}

/// The MCP tool surface plus its mod-protocol mapping (tool → fn → route).
async fn mcp_schema(State(s): State<AppState>) -> Json<Value> {
    Json(crate::mcp::schema_doc(s.hl.testnet))
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
        "description": "Hyperliquid full-stack — one-transaction cross-chain deposits from seven chains, backend agent signing, all L1+user actions, copy-trade live engine, indexes, vaults, MCP tool server",
        "protocol": "mod",
        "auth": "mod protocol-auth Bearer token (personal_sign) — public reads open, user-scoped routes gated",
        // A refusal is part of the API, so it is documented like the rest of
        // it. Every 401/403 carries `reason` (from this list), a `message`
        // written for a person, and `sign_in` — whether re-signing would fix
        // it. Clients branch on the code, show the message, and retry once
        // when sign_in is true, instead of reprinting a status number.
        "denials": {
            "no_token":          {"status": 401, "sign_in": true,  "means": "no Authorization header"},
            "expired_token":     {"status": 401, "sign_in": true,  "means": "past the session window"},
            "bad_token":         {"status": 401, "sign_in": true,  "means": "malformed, or signature does not recover to its key"},
            "wrong_wallet":      {"status": 403, "sign_in": false, "means": "request names an address the token does not sign for"},
            "not_owner":         {"status": 403, "sign_in": false, "means": "the row belongs to another wallet"},
            "unscoped_query":    {"status": 403, "sign_in": false, "means": "per-wallet list with no ?follower/?eoa scope"},
            "payload_too_large": {"status": 413, "sign_in": false, "means": "body over 1 MiB"},
        },
        "testnet": s.hl.testnet,
        "urls": { "app": "/hyperliquid", "api": "/api/hyperliquid" },
        "endpoints": {
            // `crate::auth::is_public` is the authority; mcp::tools() carries
            // the same flag per tool and a test holds the two in agreement.
            "public": ["/health", "/status", "/mids", "/market/meta", "/orderbook/:coin", "/candles/:coin", "/leaderboard", "/traders/top", "/trader/:addr/analyze", "/user/:addr/*", "/vaults", "/indexes", "/indexes/:id", "/indexes/:id/perf", "POST /indexes/auto", "/deposit/chains", "/deposit/balances", "/deposit/status", "/ask/status", "/wallet/config", "/mcp", "/mcp/schema"],
            "gated": ["/auth/me", "/follows", "/signals", "/signer/*", "/trade", "/live/*", "/intent/*", "/exchange/relay", "/action", "/deposit/quote", "POST /indexes", "PATCH|DELETE /indexes/:id"],
        },
        // The mod-protocol fn surface is also an MCP tool server; /mcp/schema
        // publishes the tool → fn → route mapping.
        "mcp": {
            "endpoint": "/mcp",
            "schema": "/mcp/schema",
            "transport": "streamable-http",
            "protocolVersion": crate::mcp::DEFAULT_PROTOCOL_VERSION,
            "tools": crate::mcp::tools().len(),
        },
    }))
}

async fn status(State(s): State<AppState>) -> Json<Value> {
    Json(json!({
        "ok": true,
        "testnet": s.hl.testnet,
        "indexes": s.store.list_indexes().len(),
        "follows": s.store.list_follows(None).len(),
        "mcp_tools": crate::mcp::tools().len(),
        // Agents currently attached over the HTTP+SSE transport.
        "mcp_sse_sessions": crate::mcp::session_count(),
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
    pool: Option<String>,           // 1-1500, or `all` = every gated wallet on the leaderboard
    enrich: Option<usize>,          // fill stats for the top N by `rank` only (default 120, max 400)
    seed: Option<String>,           // comma-separated extra wallets
    rank: Option<String>,           // roi (default) | pnl | volume
    active: Option<String>,         // 24h (default) | window
    coins: Option<String>,          // comma-separated: only wallets that traded one of these
    sort: Option<String>,           // roi | pnl | volume | equity | sharpe | win_rate | trades (default = rank)
    // Score floors — see traders::ScoreFilter.
    min_roi: Option<f64>,
    min_pnl: Option<f64>,
    min_volume: Option<f64>,
    min_equity: Option<f64>,
    min_sharpe: Option<f64>,
    min_win: Option<f64>,
    min_trades: Option<usize>,
    with_stats: Option<bool>,
    wait: Option<u64>,              // seconds to hold the request open for a cold scan (default 45, max 90)
}

/// How long `/traders/top` will hold a connection open waiting for a cold
/// scan before answering `scanning: true`. Cloudflare cuts an origin request
/// at 100s and swaps in its own error page, so the ceiling stays under that.
const SCAN_WAIT_DEFAULT_S: u64 = 45;
const SCAN_WAIT_MAX_S: u64 = 90;

async fn top_traders(State(s): State<AppState>, Query(q): Query<TopQ>)
    -> Result<Json<Value>, (StatusCode, Json<Value>)>
{
    use crate::traders::{ScoreFilter, SortKey, ALL, ENRICH_CAP, ENRICH_MAX};
    let days = q.days.unwrap_or(7).clamp(1, 90);
    let min_per_day = q.min_per_day.unwrap_or(1.0).max(0.0);
    let bad = |what: &str, got: &str| (StatusCode::BAD_REQUEST, Json(json!({
        "error": format!("unknown {what} '{got}'"),
        "rank": ["roi", "pnl", "volume"], "active": ["24h", "window"],
        "pool": "1-1500 or all",
        "sort": ["roi", "pnl", "volume", "equity", "sharpe", "win_rate", "trades"],
    })));
    let pool_s = q.pool.unwrap_or_default();
    let pool = match pool_s.trim().to_ascii_lowercase().as_str() {
        "" => 150,
        "all" | "0" | "*" => ALL,
        n => n.parse::<usize>().map_err(|_| bad("pool", &pool_s))?.clamp(1, 1500),
    };
    let enrich = q.enrich.unwrap_or(ENRICH_CAP).min(ENRICH_MAX);
    let seed: Vec<String> = q.seed.unwrap_or_default()
        .split(',').filter(|x| !x.is_empty())
        .map(|x| x.trim().to_lowercase()).collect();
    let rank_s = q.rank.unwrap_or_default();
    let rank = crate::traders::Rank::parse(&rank_s).ok_or_else(|| bad("rank", &rank_s))?;
    let active_s = q.active.unwrap_or_default();
    let active = crate::traders::Active::parse(&active_s).ok_or_else(|| bad("active", &active_s))?;
    let sort_s = q.sort.unwrap_or_default();
    let sort = if sort_s.trim().is_empty() { None }
        else { Some(SortKey::parse(&sort_s).ok_or_else(|| bad("sort", &sort_s))?) };
    let filter = ScoreFilter {
        min_roi: q.min_roi, min_pnl: q.min_pnl, min_volume: q.min_volume, min_equity: q.min_equity,
        min_sharpe: q.min_sharpe, min_win: q.min_win, min_trades: q.min_trades,
        with_stats: q.with_stats.unwrap_or(false),
    };
    // One shape for both paths: filter, then order, then report what was
    // priced from the leaderboard vs actually measured from fills.
    let finish = |mut traders: Vec<crate::traders::TopTrader>, depth: usize, candidates: Option<usize>,
                  coins: Vec<String>, updated_at: i64, scanning: bool| {
        let priced = traders.len();
        filter.apply(&mut traders);
        if let Some(k) = sort { k.sort(&mut traders); }
        Json(json!({
            "days": days, "min_per_day": min_per_day,
            "pool": if pool == ALL { json!("all") } else { json!(pool) },
            "all": pool == ALL, "enrich": enrich,
            "rank": rank.as_str(), "active": active.as_str(),
            "sort": sort.map(|k| k.as_str()).unwrap_or(rank.as_str()),
            "filter": filter, "filtered": !filter.is_empty(),
            "coins": coins, "depth": depth, "candidates": candidates,
            "priced": priced, "matched": traders.len(),
            "enriched": crate::traders::enriched_count(&traders),
            "updated_at": updated_at,
            // A cold board outlives any proxy timeout, so the walk runs in the
            // background and this says so — rows here (if any) are the last
            // good board for the window, and /scan/progress tracks the rest.
            "scanning": scanning,
            "progress": if scanning { json!(s.progress.snapshot()) } else { Value::Null },
            "traders": traders,
        }))
    };
    let coins = crate::traders::wanted_coins(
        &q.coins.unwrap_or_default().split(',').map(|x| x.to_string()).collect::<Vec<_>>());
    // A coin nobody trades would send the scan through its whole walk budget
    // (hundreds of throttled fills fetches) to find nothing — reject unknown
    // names up front. Builder-dex ("xyz:GOLD") and spot-index ("@123") coins
    // aren't in the perp/spot universe by name, so they pass through.
    for c in &coins {
        if c.contains(':') || c.starts_with('@') { continue; }
        if s.meta.get(c).await.is_err() {
            return Err((StatusCode::BAD_REQUEST, Json(json!({
                "error": format!("unknown coin '{c}' — not in the Hyperliquid perp/spot universe"),
                "coins": coins,
            }))));
        }
    }

    // Fast path: the background refresher keeps boards for the standard
    // windows; serve those directly instead of re-running the scan per
    // request. Seeded and coin-filtered requests need their own walk of the
    // leaderboard, so they fall through.
    // A deeper enrichment than the refresher keeps warm is a real scan, so it
    // skips the cache too.
    if seed.is_empty() && coins.is_empty() && enrich <= ENRICH_CAP {
        if let Some(entry) = s.boards.get(days, rank, active) {
            if entry.covers(pool) {
                let mut traders = entry.traders;
                if pool != ALL { traders.truncate(pool); }
                let candidates = if entry.all { Some(entry.pool) } else { None };
                let depth = traders.len();
                return Ok(finish(traders, depth, candidates, vec![], entry.updated_at, false));
            }
        }
    }

    // Nothing cached covers this request, so it needs a real walk of the
    // leaderboard — minutes, on a cold index. Run it as a detached task and
    // wait on it for a bounded budget: fast scans (a warm index makes a repeat
    // ~instant) still answer inline, and a genuinely cold one answers
    // `scanning: true` with the last good board instead of being killed at a
    // gateway timeout and rendered as somebody else's error page.
    let key = format!("{days}|{}|{}|{}|{enrich}|{}|{}", pool, rank.as_str(), active.as_str(),
                      coins.join(","), seed.join(","));
    // A coin requirement or a seed can only be answered by the walk itself —
    // a plain cached board would show wallets that don't meet it.
    let fallback = if seed.is_empty() && coins.is_empty() {
        s.boards.get(days, rank, active)
    } else { None };
    let scanning = |entry: Option<crate::traders::BoardEntry>, coins: Vec<String>| {
        let (rows, updated_at, candidates) = match entry {
            Some(e) => {
                let mut t = e.traders;
                if pool != ALL { t.truncate(pool); }
                let c = if e.all { Some(e.pool) } else { None };
                (t, e.updated_at, c)
            }
            None => (vec![], 0, None),
        };
        let depth = rows.len();
        finish(rows, depth, candidates, coins, updated_at, true)
    };

    let Some(claim) = s.scans.claim(&key) else {
        // Someone else is already walking exactly this board; joining their
        // scan beats starting a second one against the same rate limit.
        return Ok(scanning(fallback, coins));
    };
    let (hl, index, progress) = (s.hl.clone(), s.index.clone(), s.progress.clone());
    let (boards, coins_req) = (s.boards.clone(), coins.clone());
    let (seed_t, coins_t) = (seed.clone(), coins.clone());
    let seeded = !seed.is_empty();
    let task = tokio::spawn(async move {
        let _claim = claim; // released when the scan ends, panic included
        let board = crate::traders::top_traders_with_progress(
            hl, index, days, pool, seed_t, Some(progress), rank, active, coins_t, enrich,
        ).await?;
        // Cache what we computed under the key the refresher will keep warm —
        // but never let a narrow live board replace a whole-leaderboard one
        // that already covers it (a deeper `enrich` request lands here on
        // purpose). Storing it here is also what a `scanning` caller comes
        // back for.
        let covered = boards.get(days, rank, active).map_or(false, |e| e.covers(pool));
        if !seeded && board.coins.is_empty() && (pool == ALL || !covered) {
            boards.put(days, rank, active, pool, board.traders.clone());
        }
        Ok::<_, anyhow::Error>(board)
    });
    let wait = std::time::Duration::from_secs(
        q.wait.unwrap_or(SCAN_WAIT_DEFAULT_S).min(SCAN_WAIT_MAX_S));
    match tokio::time::timeout(wait, task).await {
        // Dropping the JoinHandle on timeout detaches the task, it does not
        // cancel it — the walk finishes and lands in the board cache.
        Err(_) => Ok(scanning(fallback, coins_req)),
        Ok(Err(join)) => Err(err500(join)),
        Ok(Ok(Err(e))) => Err(err500(e)),
        Ok(Ok(Ok(board))) => Ok(finish(board.traders, board.depth, Some(board.candidates), board.coins,
                                       chrono::Utc::now().timestamp_millis(), false)),
    }
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

/// One wallet's PnL curve for one window — the shape behind the row's number.
///
/// Public, and deliberately infallible: the board hovers this per row, so an
/// upstream 429 comes back as `available: false` with a sentence rather than a
/// status code that would paint a working row as broken.
async fn trader_curve(State(s): State<AppState>, Path(a): Path<String>, Query(q): Query<AnalyzeQ>)
    -> Json<Value>
{
    let days = q.days.unwrap_or(7).clamp(1, 90);
    let c = crate::curve::trader_curve(s.hl.clone(), &a, days).await;
    Json(serde_json::to_value(c).unwrap_or_else(|_| json!({"available": false})))
}

// ── follows / copy ──

#[derive(Deserialize)]
struct FollowFilter { follower: Option<String> }
async fn list_follows(State(s): State<AppState>, Query(q): Query<FollowFilter>) -> Json<Value> {
    Json(json!({"follows": s.store.list_follows(q.follower.as_deref())}))
}

#[derive(Deserialize)]
struct CreateFollow {
    /// Optional — defaults to the signed-in wallet. See `CreateIndex::owner`.
    follower: Option<String>,
    leader: String,
    size_pct: Option<f64>,
    max_per_trade_usd: Option<f64>,
    coins_allow: Option<Vec<String>>,
    coins_deny: Option<Vec<String>>,
    vault_address: Option<String>,
}
/// A leader can be ANY Hyperliquid account — nothing requires it to be on a
/// board — but it must at least be a well-formed EVM address, and you can't
/// follow yourself (the engine would mirror its own mirrors).
pub fn validate_follow_pair(follower: &str, leader: &str) -> Result<(String, String), String> {
    let is_addr = |a: &str| a.len() == 42 && a.starts_with("0x")
        && a[2..].chars().all(|c| c.is_ascii_hexdigit());
    let follower = follower.trim().to_lowercase();
    let leader = leader.trim().to_lowercase();
    if !is_addr(&follower) { return Err("follower must be a 0x… address (40 hex chars)".into()); }
    if !is_addr(&leader) { return Err("leader must be a 0x… address (40 hex chars)".into()); }
    if follower == leader { return Err("leader and follower are the same wallet".into()); }
    Ok((follower, leader))
}

async fn create_follow(
    State(s): State<AppState>,
    user: Option<Extension<crate::auth::AuthedUser>>,
    Json(b): Json<CreateFollow>,
) -> Result<Json<Value>, (StatusCode, Json<Value>)>
{
    let me = caller_address(b.follower.as_deref(), user, "follower")?;
    let (follower, leader) = validate_follow_pair(&me, &b.leader)
        .map_err(bad_request)?;
    let f = Follow {
        id: String::new(),
        follower,
        leader,
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
    /// Optional: the guard has already proven who is calling, so the default
    /// owner is the signed-in wallet. Sending it is allowed (the guard pins it
    /// to the token) but never required — a client that had to plumb its own
    /// address through just to name itself was one stale render away from
    /// posting a mismatch.
    owner: Option<String>,
    description: Option<String>,
    legs: Vec<IndexLeg>,
    days_window: Option<u32>,
    max_leverage: Option<f64>,
    notional_pct: Option<f64>,
    vault_address: Option<String>,
}
async fn create_index(
    State(s): State<AppState>,
    user: Option<Extension<crate::auth::AuthedUser>>,
    Json(b): Json<CreateIndex>,
) -> Result<Json<Value>, (StatusCode, Json<Value>)>
{
    let owner = caller_address(b.owner.as_deref(), user, "owner")?;
    let name = b.name.trim().to_string();
    if name.is_empty() {
        return Err(bad_request("name is required"));
    }
    if b.legs.is_empty() {
        return Err(bad_request("a strat needs at least one trader leg"));
    }
    let idx = Index {
        id: String::new(),
        name,
        owner,
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
struct AutoBody { days: Option<u32>, top: Option<usize>, pool: Option<usize>, rank: Option<String>, coins: Option<Vec<String>> }
async fn auto_index_preview(State(s): State<AppState>, Json(b): Json<AutoBody>)
    -> Result<Json<Value>, (StatusCode, Json<Value>)>
{
    let days = b.days.unwrap_or(7);
    let top = b.top.unwrap_or(10).max(1).min(50);
    let pool = b.pool.unwrap_or(150);
    let rank = crate::traders::Rank::parse(b.rank.as_deref().unwrap_or("")).unwrap_or(crate::traders::Rank::Roi);
    let traders = crate::traders::top_traders_with_progress(
        s.hl.clone(), s.index.clone(), days, pool, vec![], None, rank, crate::traders::Active::Day,
        b.coins.unwrap_or_default(), crate::traders::ENRICH_CAP,
    ).await.map(|b| b.traders).map_err(err500)?;
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
            let pool = b.payload.get("pool").and_then(|x| x.as_u64()).unwrap_or(150) as usize;
            let coins: Vec<String> = b.payload.get("coins").and_then(|x| x.as_array())
                .map(|a| a.iter().filter_map(|c| c.as_str().map(String::from)).collect())
                .unwrap_or_default();
            let traders = crate::traders::top_traders(s.hl.clone(), s.index.clone(), days, pool, vec![], coins)
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

#[cfg(test)]
mod follow_tests {
    use super::validate_follow_pair;

    #[test]
    fn any_well_formed_leader_is_accepted() {
        let f = "0x1111111111111111111111111111111111111111";
        // a leader that's on no board at all — just a wallet
        let l = "0xABCDEFabcdef0123456789ABCDEFabcdef012345";
        let (a, b) = validate_follow_pair(f, l).unwrap();
        assert_eq!(a, f);
        assert_eq!(b, l.to_lowercase());
        // whitespace tolerated
        assert!(validate_follow_pair(&format!("  {f} "), l).is_ok());
    }

    #[test]
    fn malformed_or_self_follow_is_rejected() {
        let f = "0x1111111111111111111111111111111111111111";
        assert!(validate_follow_pair(f, "0x123").is_err());
        assert!(validate_follow_pair(f, "0xZZ11111111111111111111111111111111111111").is_err());
        assert!(validate_follow_pair(f, "").is_err());
        assert!(validate_follow_pair("nope", f).is_err());
        assert!(validate_follow_pair(f, &f.to_uppercase().replace("0X", "0x")).is_err(), "self-follow");
    }
}
