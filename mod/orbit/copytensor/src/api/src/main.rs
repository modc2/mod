//! copytensor-api — Bittensor dTAO copy trading REST API
//!
//! Axum server providing endpoints for:
//! - Querying subnet token positions and prices
//! - Calculating N-day PnL for any account
//! - Leaderboard of top performers
//! - Copy trading (auto stake/unstake to mirror a target)
//!
//! All data sourced directly from the Bittensor subtensor chain.
//! No third-party APIs.

mod chain;
mod db;
mod models;

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Instant;

use anyhow::Result;
use axum::extract::{Json, Path, Query, State};
use axum::http::StatusCode;
use axum::response::IntoResponse;
use axum::routing::{delete, get, post};
use axum::Router;
use serde_json::json;
use tokio::sync::RwLock;
use tokio::task::JoinHandle;
use tower_http::cors::{Any, CorsLayer};
use tracing::{error, info, warn};

use chain::SubtensorClient;
use db::Database;
use models::*;

// ── app state ───────────────────────────────────────────────────

struct AppState {
    client: SubtensorClient,
    db: Database,
    config: RwLock<serde_json::Value>,
    config_path: PathBuf,
    wallet_signer: RwLock<Option<subxt_signer::sr25519::Keypair>>,
    wallet_ss58: RwLock<Option<String>>,
    copy_tasks: RwLock<HashMap<String, JoinHandle<()>>>,
    last_tx_time: RwLock<Instant>,
    daily_spent: RwLock<f64>,
    daily_reset: RwLock<Instant>,
    // Per-block caches. Keyed by chain block height so every new block
    // triggers a refresh; within a block, all reads are served from memory.
    // Tuple = (block_height, payload, fetched_at). `fetched_at` lets us
    // serve a stale value if the chain temporarily errors instead of 500ing.
    subnets_cache: RwLock<Option<(u64, Vec<SubnetInfo>, Instant)>>,
    tao_price_cache: RwLock<Option<(u64, f64, Instant)>>,
}

type S = Arc<AppState>;

fn err(status: StatusCode, msg: impl ToString) -> (StatusCode, Json<serde_json::Value>) {
    (status, Json(json!({ "error": msg.to_string() })))
}

// ── main ────────────────────────────────────────────────────────

#[tokio::main]
async fn main() -> Result<()> {
    tracing_subscriber::fmt::init();

    let port: u16 = std::env::var("PORT")
        .unwrap_or_else(|_| "50150".into())
        .parse()?;

    // Load config
    let config_path = find_config_path();
    let config: serde_json::Value = if config_path.exists() {
        let text = std::fs::read_to_string(&config_path)?;
        serde_json::from_str(&text)?
    } else {
        json!({})
    };

    let network = config["network"].as_str().unwrap_or("finney");
    let endpoint = config["subtensor_endpoint"].as_str();

    info!("connecting to subtensor ({network})...");
    let client = SubtensorClient::connect(network, endpoint).await?;
    info!("connected to subtensor");

    // Open database
    let db_path = config_path
        .parent()
        .unwrap_or(std::path::Path::new("."))
        .join("src")
        .join("data")
        .join("copytensor.db");
    let db = Database::open(&db_path)?;
    info!("database opened at {}", db_path.display());

    // Load watched accounts from config
    if let Some(accounts) = config["watched_accounts"].as_array() {
        for acct in accounts {
            if let Some(ss58) = acct.as_str() {
                let _ = db.add_account(ss58, None);
            }
        }
    }

    let state = Arc::new(AppState {
        client,
        db,
        config: RwLock::new(config),
        config_path,
        wallet_signer: RwLock::new(None),
        wallet_ss58: RwLock::new(None),
        copy_tasks: RwLock::new(HashMap::new()),
        last_tx_time: RwLock::new(Instant::now()),
        daily_spent: RwLock::new(0.0),
        daily_reset: RwLock::new(Instant::now()),
        subnets_cache: RwLock::new(None),
        tao_price_cache: RwLock::new(None),
    });

    // Background block-driven cache refresher. Polls the chain head every
    // ~6s (one Bittensor block); on each new block, refreshes the subnet
    // snapshot and the TAO/USD price in parallel. Request handlers read
    // straight from the cache so they never block on RPC.
    spawn_cache_refresher(state.clone());

    // Resume active copy loops
    {
        let copies = state.db.list_copies().unwrap_or_default();
        for copy in copies.iter().filter(|c| c.status == "active") {
            let config_json: serde_json::Value = serde_json::from_str(&copy.config_json).unwrap_or_default();
            let interval = config_json["poll_interval_sec"].as_u64().unwrap_or(300);
            spawn_copy_loop(state.clone(), copy.id.clone(), copy.target_ss58.clone(), interval);
        }
    }

    let cors = CorsLayer::new()
        .allow_origin(Any)
        .allow_methods(Any)
        .allow_headers(Any);

    let app = Router::new()
        // health
        .route("/health", get(health))
        .route("/status", get(status))
        // subnets
        .route("/subnets", get(list_subnets))
        // price (TAO/USD, cached server-side)
        .route("/tao_price", get(tao_price))
        // accounts
        .route("/account/{ss58}", get(get_account))
        .route("/account/{ss58}/pnl", get(get_account_pnl))
        .route("/account/{ss58}/history", get(get_account_history))
        // trader details
        .route("/trader/{ss58}", get(get_trader))
        // leaderboard
        .route("/leaderboard", get(leaderboard))
        // watchlist
        .route("/watch", post(watch))
        .route("/watch/{ss58}", delete(unwatch))
        .route("/watches", get(list_watches))
        // copy trading
        .route("/copy", post(create_copy))
        .route("/copies", get(list_copies))
        .route("/copy/{id}", get(get_copy))
        .route("/copy/{id}/pause", post(pause_copy))
        .route("/copy/{id}/resume", post(resume_copy))
        .route("/copy/{id}/sync", post(sync_copy))
        .route("/copy/{id}", delete(delete_copy_route))
        // trades
        .route("/trades", get(list_trades))
        // wallet
        .route("/wallet/set", post(set_wallet))
        .route("/wallet/balance", get(wallet_balance))
        // config
        .route("/config", get(get_config))
        .route("/config", post(set_config))
        // snapshots
        .route("/snapshots/now", post(snapshot_now))
        .with_state(state)
        .layer(cors);

    let addr = format!("0.0.0.0:{port}");
    info!("copytensor-api listening on {addr}");
    let listener = tokio::net::TcpListener::bind(&addr).await?;
    axum::serve(listener, app).await?;
    Ok(())
}

fn find_config_path() -> PathBuf {
    // Walk up from binary to find config.json
    if let Ok(exe) = std::env::current_exe() {
        let mut dir = exe.parent().map(|p| p.to_path_buf());
        for _ in 0..5 {
            if let Some(ref d) = dir {
                let cfg = d.join("config.json");
                if cfg.exists() {
                    return cfg;
                }
                dir = d.parent().map(|p| p.to_path_buf());
            }
        }
    }
    // Fallback: current directory
    let cwd = std::env::current_dir().unwrap_or_default();
    cwd.join("config.json")
}

fn save_config(path: &PathBuf, config: &serde_json::Value) {
    if let Ok(text) = serde_json::to_string_pretty(config) {
        let _ = std::fs::write(path, text);
    }
}

// ── handlers ────────────────────────────────────────────────────

async fn health(State(s): State<S>) -> impl IntoResponse {
    Json(s.client.health().await)
}

async fn status(State(s): State<S>) -> impl IntoResponse {
    let h = s.client.health().await;
    let accounts = s.db.account_count().unwrap_or(0);
    let copies = s.db.list_copies().unwrap_or_default();
    let active = copies.iter().filter(|c| c.status == "active").count();
    let wallet_set = s.wallet_ss58.read().await.is_some();

    Json(StatusResponse {
        running: h.connected,
        network: h.network,
        block_height: h.block,
        tracked_accounts: accounts,
        active_copies: active,
        wallet_set,
    })
}

/// GET /tao_price → { usd, age_sec, block, stale }
/// Pure cache read — populated by the background refresher.
async fn tao_price(State(s): State<S>) -> Json<serde_json::Value> {
    if let Some((block, usd, at)) = s.tao_price_cache.read().await.as_ref() {
        let age = at.elapsed().as_secs();
        return Json(json!({
            "usd": usd,
            "age_sec": age,
            "block": block,
            "stale": age > 120,
        }));
    }
    Json(json!({ "error": "tao price not yet cached — refresher warming up" }))
}

/// Background task: on each new chain block, refresh the subnet snapshot
/// and the TAO/USD price. Handlers serve directly from the cache so they
/// never wait on chain or network I/O.
async fn refresh_caches(state: &S, http: &reqwest::Client, last_block: u64) -> u64 {
    let block = match state.client.get_block().await {
        Ok(b) => b,
        Err(e) => {
            warn!("refresher: get_block failed: {e}");
            return last_block;
        }
    };
    if block == last_block {
        return last_block;
    }
    // Fetch subnet snapshot + TAO/USD price concurrently.
    let subnets_fut = async { state.client.get_all_subnet_info().await.ok() };
    let price_fut = async {
        let r = http
            .get("https://api.coingecko.com/api/v3/simple/price?ids=bittensor&vs_currencies=usd")
            .timeout(std::time::Duration::from_secs(5))
            .send()
            .await
            .ok()?;
        r.json::<serde_json::Value>().await.ok()?
            ["bittensor"]["usd"].as_f64()
    };
    let (subnets, price) = tokio::join!(subnets_fut, price_fut);
    if let Some(info) = subnets {
        let n = info.len();
        *state.subnets_cache.write().await = Some((block, info, Instant::now()));
        info!("refreshed subnets cache: {n} subnets @ block {block}");
    }
    if let Some(p) = price {
        *state.tao_price_cache.write().await = Some((block, p, Instant::now()));
        info!("refreshed TAO price: ${p:.2} @ block {block}");
    }
    block
}

fn spawn_cache_refresher(state: S) {
    tokio::spawn(async move {
        let http = reqwest::Client::new();
        // First refresh immediately, then one per Bittensor block (~12s).
        let mut last_block = refresh_caches(&state, &http, 0).await;
        loop {
            tokio::time::sleep(tokio::time::Duration::from_secs(6)).await;
            last_block = refresh_caches(&state, &http, last_block).await;
        }
    });
}

async fn list_subnets(State(s): State<S>) -> Result<Json<Vec<SubnetInfo>>, (StatusCode, Json<serde_json::Value>)> {
    // Pure cache read — refresher keeps it warm per block.
    if let Some((_, info, _)) = s.subnets_cache.read().await.as_ref() {
        return Ok(Json(info.clone()));
    }
    // Cold start: cache hasn't been populated yet. Fall back to a one-shot
    // fetch so the very first request after boot still works.
    let info = s
        .client
        .get_all_subnet_info()
        .await
        .map_err(|e| err(StatusCode::INTERNAL_SERVER_ERROR, e))?;
    let block = s.client.get_block().await.unwrap_or(0);
    *s.subnets_cache.write().await = Some((block, info.clone(), Instant::now()));
    Ok(Json(info))
}

async fn get_account(
    State(s): State<S>,
    Path(ss58): Path<String>,
    Query(q): Query<AccountQuery>,
) -> Result<Json<AccountResponse>, (StatusCode, Json<serde_json::Value>)> {
    let days = q.days.unwrap_or(7);
    let positions = s
        .client
        .get_stake_for_coldkey(&ss58, None)
        .await
        .map_err(|e| err(StatusCode::INTERNAL_SERVER_ERROR, e))?;

    // Get subnet names
    let subnet_names = get_subnet_names(&s.client).await;
    let total = if positions.total_value_tao > 0.001 {
        positions.total_value_tao
    } else {
        0.001
    };

    let allocations: Vec<Allocation> = positions
        .positions
        .iter()
        .map(|p| Allocation {
            netuid: p.netuid,
            subnet_name: subnet_names
                .get(&p.netuid)
                .cloned()
                .unwrap_or_else(|| format!("SN{}", p.netuid)),
            hotkey: p.hotkey.clone(),
            alpha_amount: p.alpha_amount,
            alpha_price_tao: p.alpha_price_tao,
            value_tao: p.value_tao,
            pct_of_total: p.value_tao / total * 100.0,
        })
        .collect();

    // Calculate PnL
    let (pnl_tao, pnl_pct) = calculate_pnl(&s.client, &s.db, &ss58, days)
        .await
        .unwrap_or((0.0, 0.0));

    Ok(Json(AccountResponse {
        ss58,
        total_stake_tao: positions.total_value_tao,
        allocations,
        pnl_tao,
        pnl_pct,
        days,
    }))
}

async fn get_account_pnl(
    State(s): State<S>,
    Path(ss58): Path<String>,
    Query(q): Query<AccountQuery>,
) -> Result<Json<PnlResponse>, (StatusCode, Json<serde_json::Value>)> {
    let days = q.days.unwrap_or(7);
    let pnl = calculate_pnl_detailed(&s.client, &s.db, &ss58, days)
        .await
        .map_err(|e| err(StatusCode::INTERNAL_SERVER_ERROR, e))?;
    Ok(Json(pnl))
}

async fn get_account_history(
    State(s): State<S>,
    Path(ss58): Path<String>,
    Query(q): Query<TradesQuery>,
) -> impl IntoResponse {
    let limit = q.limit.unwrap_or(50);
    let snapshots = s.db.get_snapshots(&ss58, limit).unwrap_or_default();
    let result: Vec<serde_json::Value> = snapshots
        .into_iter()
        .map(|snap| {
            json!({
                "block": snap.block,
                "timestamp": snap.timestamp,
                "total_value_tao": snap.total_value_tao,
                "allocations": serde_json::from_str::<serde_json::Value>(&snap.allocations_json).unwrap_or_default()
            })
        })
        .collect();
    Json(json!({ "snapshots": result }))
}

async fn leaderboard(
    State(s): State<S>,
    Query(q): Query<LeaderboardQuery>,
) -> Result<Json<Vec<LeaderboardEntry>>, (StatusCode, Json<serde_json::Value>)> {
    let days = q.days.unwrap_or(7);
    let top = q.top.unwrap_or(50);
    let min_subnets = q.min_subnets.unwrap_or(0);

    let accounts = s
        .db
        .list_accounts()
        .map_err(|e| err(StatusCode::INTERNAL_SERVER_ERROR, e))?;

    let mut entries = Vec::new();
    for acct in &accounts {
        match calculate_pnl_detailed(&s.client, &s.db, &acct.ss58, days).await {
            Ok(pnl) => {
                let by_subnet = &pnl.by_subnet;
                if by_subnet.len() < min_subnets {
                    continue;
                }
                let top_sn = by_subnet
                    .iter()
                    .max_by(|a, b| a.pnl_tao.partial_cmp(&b.pnl_tao).unwrap_or(std::cmp::Ordering::Equal));

                entries.push(LeaderboardEntry {
                    ss58: acct.ss58.clone(),
                    label: acct.label.clone(),
                    total_stake_tao: pnl.end_value_tao,
                    pnl_tao: pnl.pnl_tao,
                    pnl_pct: pnl.pnl_pct,
                    num_subnets: by_subnet.len(),
                    top_subnet: top_sn.map(|s| s.netuid),
                    top_subnet_pnl: top_sn.map(|s| s.pnl_tao).unwrap_or(0.0),
                });
            }
            Err(e) => {
                warn!("leaderboard skip {}: {e}", &acct.ss58[..8.min(acct.ss58.len())]);
            }
        }
    }

    entries.sort_by(|a, b| b.pnl_tao.partial_cmp(&a.pnl_tao).unwrap_or(std::cmp::Ordering::Equal));
    entries.truncate(top);

    Ok(Json(entries))
}

async fn watch(
    State(s): State<S>,
    Json(req): Json<WatchRequest>,
) -> Result<Json<WatchResponse>, (StatusCode, Json<serde_json::Value>)> {
    s.db
        .add_account(&req.ss58, req.label.as_deref())
        .map_err(|e| err(StatusCode::INTERNAL_SERVER_ERROR, e))?;

    // Persist to config
    {
        let mut config = s.config.write().await;
        let watched = config["watched_accounts"]
            .as_array_mut()
            .cloned()
            .unwrap_or_default();
        if !watched.iter().any(|v| v.as_str() == Some(&req.ss58)) {
            let mut w = watched;
            w.push(json!(req.ss58));
            config["watched_accounts"] = json!(w);
            save_config(&s.config_path, &config);
        }
    }

    // Take initial snapshot
    take_snapshot(&s.client, &s.db, &req.ss58).await;

    let total = s.db.account_count().unwrap_or(0);
    Ok(Json(WatchResponse {
        watched: req.ss58,
        total,
    }))
}

async fn unwatch(
    State(s): State<S>,
    Path(ss58): Path<String>,
) -> Result<Json<UnwatchResponse>, (StatusCode, Json<serde_json::Value>)> {
    s.db
        .remove_account(&ss58)
        .map_err(|e| err(StatusCode::INTERNAL_SERVER_ERROR, e))?;

    {
        let mut config = s.config.write().await;
        if let Some(arr) = config["watched_accounts"].as_array_mut() {
            arr.retain(|v| v.as_str() != Some(&ss58));
            save_config(&s.config_path, &config);
        }
    }

    let total = s.db.account_count().unwrap_or(0);
    Ok(Json(UnwatchResponse {
        unwatched: ss58,
        total,
    }))
}

async fn list_watches(State(s): State<S>) -> impl IntoResponse {
    let accounts = s.db.list_accounts().unwrap_or_default();
    let result: Vec<AccountWatch> = accounts
        .into_iter()
        .map(|a| AccountWatch {
            ss58: a.ss58,
            label: a.label,
            added_at: a.added_at,
        })
        .collect();
    Json(json!({ "accounts": result }))
}

async fn create_copy(
    State(s): State<S>,
    Json(req): Json<CopyRequest>,
) -> Result<Json<CopyInfo>, (StatusCode, Json<serde_json::Value>)> {
    if s.wallet_ss58.read().await.is_none() {
        return Err(err(StatusCode::BAD_REQUEST, "wallet not set — POST /wallet/set first"));
    }

    let config = s.config.read().await;
    let copy_config = json!({
        "our_hotkey": req.our_hotkey,
        "max_tao_per_tx": req.max_tao_per_tx.unwrap_or_else(|| config["max_tao_per_tx"].as_f64().unwrap_or(10.0)),
        "daily_limit_tao": req.daily_limit_tao.unwrap_or_else(|| config["daily_limit_tao"].as_f64().unwrap_or(100.0)),
        "min_balance_tao": req.min_balance_tao.unwrap_or_else(|| config["min_balance_tao"].as_f64().unwrap_or(1.0)),
        "subnet_allowlist": req.subnet_allowlist,
        "subnet_denylist": req.subnet_denylist.unwrap_or_else(|| {
            config["subnet_denylist"].as_array().map(|a| a.iter().filter_map(|v| v.as_u64().map(|n| n as u16)).collect()).unwrap_or_default()
        }),
        "rebalance_threshold_pct": req.rebalance_threshold_pct.unwrap_or_else(|| config["rebalance_threshold_pct"].as_f64().unwrap_or(5.0)),
        "poll_interval_sec": req.poll_interval_sec.unwrap_or_else(|| config["poll_interval_sec"].as_u64().unwrap_or(300)),
    });
    drop(config);

    let poll_interval = copy_config["poll_interval_sec"].as_u64().unwrap_or(300);
    let target_ss58 = req.target_ss58.clone();

    let copy_id = s
        .db
        .insert_copy(&req.target_ss58, &copy_config, req.label.as_deref())
        .map_err(|e| err(StatusCode::INTERNAL_SERVER_ERROR, e))?;

    // Spawn background polling loop
    spawn_copy_loop(s.clone(), copy_id.clone(), target_ss58, poll_interval);

    let copy = s
        .db
        .get_copy(&copy_id)
        .map_err(|e| err(StatusCode::INTERNAL_SERVER_ERROR, e))?
        .ok_or_else(|| err(StatusCode::INTERNAL_SERVER_ERROR, "copy not found after insert"))?;

    let info = copy_to_info_enriched(copy, &s).await;
    Ok(Json(info))
}

async fn list_copies(State(s): State<S>) -> impl IntoResponse {
    let copies = s.db.list_copies().unwrap_or_default();
    let mut infos = Vec::new();
    for copy in copies {
        infos.push(copy_to_info_enriched(copy, &s).await);
    }
    Json(infos)
}

async fn get_copy(
    State(s): State<S>,
    Path(id): Path<String>,
) -> Result<Json<CopyInfo>, (StatusCode, Json<serde_json::Value>)> {
    let copy = s
        .db
        .get_copy(&id)
        .map_err(|e| err(StatusCode::INTERNAL_SERVER_ERROR, e))?
        .ok_or_else(|| err(StatusCode::NOT_FOUND, "copy not found"))?;
    let info = copy_to_info_enriched(copy, &s).await;
    Ok(Json(info))
}

async fn pause_copy(
    State(s): State<S>,
    Path(id): Path<String>,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    s.db
        .update_copy_status(&id, "paused")
        .map_err(|e| err(StatusCode::INTERNAL_SERVER_ERROR, e))?;
    // Cancel the background polling loop
    if let Some(handle) = s.copy_tasks.write().await.remove(&id) {
        handle.abort();
    }
    Ok(Json(json!({ "id": id, "status": "paused" })))
}

async fn resume_copy(
    State(s): State<S>,
    Path(id): Path<String>,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    s.db
        .update_copy_status(&id, "active")
        .map_err(|e| err(StatusCode::INTERNAL_SERVER_ERROR, e))?;
    // Restart the background polling loop
    let copy = s.db.get_copy(&id).ok().flatten();
    if let Some(copy) = copy {
        let config: serde_json::Value = serde_json::from_str(&copy.config_json).unwrap_or_default();
        let interval = config["poll_interval_sec"].as_u64().unwrap_or(300);
        spawn_copy_loop(s.clone(), id.clone(), copy.target_ss58, interval);
    }
    Ok(Json(json!({ "id": id, "status": "active" })))
}

async fn sync_copy(
    State(s): State<S>,
    Path(id): Path<String>,
) -> Result<Json<SyncResult>, (StatusCode, Json<serde_json::Value>)> {
    let copy = s
        .db
        .get_copy(&id)
        .map_err(|e| err(StatusCode::INTERNAL_SERVER_ERROR, e))?
        .ok_or_else(|| err(StatusCode::NOT_FOUND, "copy not found"))?;

    let signer = s.wallet_signer.read().await;
    let signer = signer
        .as_ref()
        .ok_or_else(|| err(StatusCode::BAD_REQUEST, "wallet not set"))?;

    let our_ss58 = s.wallet_ss58.read().await.clone().unwrap_or_default();
    let config: serde_json::Value =
        serde_json::from_str(&copy.config_json).unwrap_or_default();
    let our_hotkey = config["our_hotkey"]
        .as_str()
        .ok_or_else(|| err(StatusCode::BAD_REQUEST, "no our_hotkey in copy config"))?
        .to_string();
    let threshold = config["rebalance_threshold_pct"].as_f64().unwrap_or(5.0);
    let max_per_tx = config["max_tao_per_tx"].as_f64().unwrap_or(10.0);
    let daily_limit = config["daily_limit_tao"].as_f64().unwrap_or(100.0);
    let min_balance = config["min_balance_tao"].as_f64().unwrap_or(1.0);

    // Get positions
    let target = s
        .client
        .get_stake_for_coldkey(&copy.target_ss58, None)
        .await
        .map_err(|e| err(StatusCode::INTERNAL_SERVER_ERROR, e))?;
    let ours = s
        .client
        .get_stake_for_coldkey(&our_ss58, None)
        .await
        .map_err(|e| err(StatusCode::INTERNAL_SERVER_ERROR, e))?;
    let balance = s
        .client
        .get_balance(&our_ss58)
        .await
        .map_err(|e| err(StatusCode::INTERNAL_SERVER_ERROR, e))?;

    // Compute deltas
    let deltas = compute_deltas(&target, &ours, threshold);
    let max_subnets = config["max_subnets"].as_u64().unwrap_or(20) as usize;
    let denylist: Vec<u16> = config["subnet_denylist"].as_array()
        .map(|a| a.iter().filter_map(|v| v.as_u64().map(|n| n as u16)).collect())
        .unwrap_or_default();
    let allowlist: Option<Vec<u16>> = config["subnet_allowlist"].as_array()
        .map(|a| a.iter().filter_map(|v| v.as_u64().map(|n| n as u16)).collect());
    let deltas = apply_safety(deltas, max_per_tx, daily_limit, min_balance, balance,
        allowlist.as_deref(), &denylist, max_subnets);

    let block = s.client.get_block().await.unwrap_or(0);
    let now = chrono::Utc::now().to_rfc3339();
    let mut results = Vec::new();

    // Execute unstakes first, then stakes
    for d in deltas.iter().filter(|d| d.action == "unstake") {
        let amount_rao = (d.amount_tao * 1e9) as u64;
        let r = s
            .client
            .unstake(signer, &our_hotkey, d.netuid, amount_rao)
            .await;
        let (status, error) = match r {
            Ok(_) => ("confirmed", None),
            Err(e) => ("failed", Some(e.to_string())),
        };
        let _ = s.db.insert_trade(&id, Some(block), &now, "unstake", d.netuid, d.amount_tao, status);
        results.push(TradeExecResult {
            action: "unstake".into(),
            netuid: d.netuid,
            amount_tao: d.amount_tao,
            status: status.into(),
            error,
        });
    }

    for d in deltas.iter().filter(|d| d.action == "stake") {
        let amount_rao = (d.amount_tao * 1e9) as u64;
        let r = s
            .client
            .stake(signer, &our_hotkey, d.netuid, amount_rao)
            .await;
        let (status, error) = match r {
            Ok(_) => ("confirmed", None),
            Err(e) => ("failed", Some(e.to_string())),
        };
        let _ = s.db.insert_trade(&id, Some(block), &now, "stake", d.netuid, d.amount_tao, status);
        results.push(TradeExecResult {
            action: "stake".into(),
            netuid: d.netuid,
            amount_tao: d.amount_tao,
            status: status.into(),
            error,
        });
    }

    let _ = s.db.update_copy_sync_block(&id, block);

    Ok(Json(SyncResult {
        synced: true,
        trades: results,
    }))
}

async fn delete_copy_route(
    State(s): State<S>,
    Path(id): Path<String>,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    // Cancel the background polling loop
    if let Some(handle) = s.copy_tasks.write().await.remove(&id) {
        handle.abort();
    }
    s.db
        .delete_copy(&id)
        .map_err(|e| err(StatusCode::INTERNAL_SERVER_ERROR, e))?;
    Ok(Json(json!({ "deleted": true, "id": id })))
}

async fn list_trades(
    State(s): State<S>,
    Query(q): Query<TradesQuery>,
) -> impl IntoResponse {
    let trades = s
        .db
        .get_trades(q.copy_id.as_deref(), q.limit.unwrap_or(50))
        .unwrap_or_default();
    let infos: Vec<TradeInfo> = trades
        .into_iter()
        .map(|t| TradeInfo {
            id: t.id,
            copy_id: t.copy_id,
            block: t.block,
            timestamp: t.timestamp,
            action: t.action,
            netuid: t.netuid,
            amount_tao: t.amount_tao,
            tx_hash: t.tx_hash,
            status: t.status,
            error: t.error,
        })
        .collect();
    Json(infos)
}

async fn set_wallet(
    State(s): State<S>,
    Json(req): Json<WalletSetRequest>,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    let keypair = if let Some(ref mnemonic) = req.mnemonic {
        let parsed: subxt_signer::bip39::Mnemonic = mnemonic
            .parse()
            .map_err(|e| err(StatusCode::BAD_REQUEST, format!("invalid mnemonic: {e}")))?;
        subxt_signer::sr25519::Keypair::from_phrase(&parsed, None)
            .map_err(|e| err(StatusCode::BAD_REQUEST, format!("keypair from mnemonic: {e}")))?
    } else if let Some(ref seed) = req.seed_hex {
        let bytes = hex::decode(seed.trim_start_matches("0x"))
            .map_err(|e| err(StatusCode::BAD_REQUEST, format!("invalid hex seed: {e}")))?;
        let seed_arr: [u8; 32] = bytes
            .try_into()
            .map_err(|_| err(StatusCode::BAD_REQUEST, "seed must be 32 bytes"))?;
        subxt_signer::sr25519::Keypair::from_secret_key(seed_arr)
            .map_err(|e| err(StatusCode::BAD_REQUEST, format!("keypair from seed: {e}")))?
    } else {
        return Err(err(StatusCode::BAD_REQUEST, "provide mnemonic or seed_hex"));
    };

    let account_id = keypair.public_key().to_account_id();
    let ss58 = account_id.to_string();

    *s.wallet_signer.write().await = Some(keypair);
    *s.wallet_ss58.write().await = Some(ss58.clone());

    info!("wallet set: {ss58}");
    Ok(Json(json!({ "wallet_set": true, "ss58": ss58 })))
}

async fn wallet_balance(
    State(s): State<S>,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    let ss58 = s
        .wallet_ss58
        .read()
        .await
        .clone()
        .ok_or_else(|| err(StatusCode::BAD_REQUEST, "wallet not set"))?;
    let balance = s
        .client
        .get_balance(&ss58)
        .await
        .map_err(|e| err(StatusCode::INTERNAL_SERVER_ERROR, e))?;
    Ok(Json(json!({ "ss58": ss58, "balance_tao": balance })))
}

async fn get_config(State(s): State<S>) -> impl IntoResponse {
    let config = s.config.read().await;
    let mut safe = config.clone();
    if let Some(obj) = safe.as_object_mut() {
        obj.remove("private_key");
        obj.remove("mnemonic");
        obj.remove("seed_hex");
    }
    Json(safe)
}

async fn set_config(
    State(s): State<S>,
    Json(req): Json<ConfigSetRequest>,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    if ["private_key", "mnemonic", "seed_hex"].contains(&req.key.as_str()) {
        return Err(err(StatusCode::BAD_REQUEST, "cannot set secrets via config endpoint"));
    }
    let mut config = s.config.write().await;
    config[&req.key] = req.value.clone();
    save_config(&s.config_path, &config);
    Ok(Json(json!({ "set": req.key, "value": req.value })))
}

async fn snapshot_now(
    State(s): State<S>,
) -> impl IntoResponse {
    let accounts = s.db.list_accounts().unwrap_or_default();
    let mut count = 0;
    for acct in &accounts {
        if take_snapshot(&s.client, &s.db, &acct.ss58).await {
            count += 1;
        }
    }
    Json(json!({ "snapshots": count, "total_accounts": accounts.len() }))
}

// ── helper functions ────────────────────────────────────────────

async fn get_subnet_names(client: &SubtensorClient) -> HashMap<u16, String> {
    let mut names = HashMap::new();
    if let Ok(infos) = client.get_all_subnet_info().await {
        for info in infos {
            names.insert(info.netuid, info.name);
        }
    }
    names
}

async fn take_snapshot(client: &SubtensorClient, db: &Database, ss58: &str) -> bool {
    match client.get_stake_for_coldkey(ss58, None).await {
        Ok(positions) => {
            let now = chrono::Utc::now().to_rfc3339();
            let allocations: Vec<serde_json::Value> = positions
                .positions
                .iter()
                .map(|p| {
                    json!({
                        "netuid": p.netuid,
                        "hotkey": p.hotkey,
                        "alpha": p.alpha_amount,
                        "price_tao": p.alpha_price_tao,
                        "value_tao": p.value_tao,
                    })
                })
                .collect();
            let _ = db.insert_snapshot(
                ss58,
                positions.block,
                &now,
                positions.total_value_tao,
                &json!(allocations),
            );
            true
        }
        Err(e) => {
            error!("snapshot failed for {}: {e}", &ss58[..8.min(ss58.len())]);
            false
        }
    }
}

async fn calculate_pnl(
    client: &SubtensorClient,
    db: &Database,
    ss58: &str,
    days: u32,
) -> Result<(f64, f64)> {
    let pnl = calculate_pnl_detailed(client, db, ss58, days).await?;
    Ok((pnl.pnl_tao, pnl.pnl_pct))
}

async fn calculate_pnl_detailed(
    client: &SubtensorClient,
    db: &Database,
    ss58: &str,
    days: u32,
) -> Result<PnlResponse> {
    let current_block = client.get_block().await?;
    let start_block = client.block_at_days_ago(current_block, days);

    // Get historical positions — prefer snapshot, fall back to chain
    let start_allocs = get_historical_positions(client, db, ss58, start_block).await;

    // Get current positions
    let current = client.get_stake_for_coldkey(ss58, None).await?;

    // Build subnet maps
    let mut start_by_sn: HashMap<u16, (f64, f64)> = HashMap::new(); // netuid -> (alpha, price)
    for alloc in &start_allocs {
        let netuid = alloc["netuid"].as_u64().unwrap_or(0) as u16;
        let alpha = alloc["alpha"].as_f64().unwrap_or(0.0);
        let price = alloc["price_tao"].as_f64().unwrap_or(0.0);
        let entry = start_by_sn.entry(netuid).or_insert((0.0, price));
        entry.0 += alpha;
    }

    let mut end_by_sn: HashMap<u16, (f64, f64)> = HashMap::new();
    for p in &current.positions {
        let entry = end_by_sn.entry(p.netuid).or_insert((0.0, p.alpha_price_tao));
        entry.0 += p.alpha_amount;
    }

    // Get subnet names
    let subnet_names = get_subnet_names(client).await;

    let mut all_netuids: Vec<u16> = start_by_sn
        .keys()
        .chain(end_by_sn.keys())
        .copied()
        .collect::<std::collections::HashSet<_>>()
        .into_iter()
        .collect();
    all_netuids.sort();

    let mut by_subnet = Vec::new();
    let mut total_start = 0.0;
    let mut total_end = 0.0;

    for netuid in all_netuids {
        let (alpha_start, price_start) = start_by_sn.get(&netuid).copied().unwrap_or((0.0, 0.0));
        let (alpha_end, price_end) = end_by_sn.get(&netuid).copied().unwrap_or((0.0, 0.0));

        let value_start = alpha_start * price_start;
        let value_end = alpha_end * price_end;
        let pnl = value_end - value_start;
        let pnl_pct = if value_start > 0.0 {
            pnl / value_start * 100.0
        } else if value_end > 0.0 {
            100.0
        } else {
            0.0
        };

        total_start += value_start;
        total_end += value_end;

        by_subnet.push(SubnetPnl {
            netuid,
            subnet_name: subnet_names
                .get(&netuid)
                .cloned()
                .unwrap_or_else(|| format!("SN{netuid}")),
            alpha_start,
            alpha_end,
            price_start_tao: price_start,
            price_end_tao: price_end,
            value_start_tao: value_start,
            value_end_tao: value_end,
            pnl_tao: pnl,
            pnl_pct,
        });
    }

    let total_pnl = total_end - total_start;
    let total_pnl_pct = if total_start > 0.0 {
        total_pnl / total_start * 100.0
    } else if total_end > 0.0 {
        100.0
    } else {
        0.0
    };

    Ok(PnlResponse {
        ss58: ss58.to_string(),
        days,
        block_start: start_block,
        block_end: current_block,
        start_value_tao: total_start,
        end_value_tao: total_end,
        pnl_tao: total_pnl,
        pnl_pct: total_pnl_pct,
        by_subnet,
    })
}

async fn get_historical_positions(
    client: &SubtensorClient,
    db: &Database,
    ss58: &str,
    target_block: u64,
) -> Vec<serde_json::Value> {
    const BLOCKS_PER_DAY: u64 = 7200;

    // Try local snapshot first
    if let Ok(Some(snap)) = db.get_nearest_snapshot(ss58, target_block) {
        if snap.block.abs_diff(target_block) < BLOCKS_PER_DAY {
            if let Ok(allocs) = serde_json::from_str::<Vec<serde_json::Value>>(&snap.allocations_json)
            {
                return allocs;
            }
        }
    }

    // Fall back to chain query at historical block
    match client.get_stake_for_coldkey(ss58, Some(target_block)).await {
        Ok(positions) => positions
            .positions
            .iter()
            .map(|p| {
                json!({
                    "netuid": p.netuid,
                    "hotkey": p.hotkey,
                    "alpha": p.alpha_amount,
                    "price_tao": p.alpha_price_tao,
                    "value_tao": p.value_tao,
                })
            })
            .collect(),
        Err(e) => {
            warn!("historical query failed for {ss58}: {e}");
            vec![]
        }
    }
}

fn compute_deltas(
    target: &AccountPositions,
    ours: &AccountPositions,
    threshold_pct: f64,
) -> Vec<Delta> {
    if target.total_value_tao <= 0.0 {
        return vec![];
    }

    // Target allocation percentages by subnet
    let mut target_alloc: HashMap<u16, f64> = HashMap::new();
    for p in &target.positions {
        *target_alloc.entry(p.netuid).or_insert(0.0) += p.value_tao;
    }
    for v in target_alloc.values_mut() {
        *v = *v / target.total_value_tao * 100.0;
    }

    // Our allocation percentages
    let our_total = if ours.total_value_tao > 0.001 {
        ours.total_value_tao
    } else {
        0.001
    };
    let mut our_alloc: HashMap<u16, f64> = HashMap::new();
    for p in &ours.positions {
        *our_alloc.entry(p.netuid).or_insert(0.0) += p.value_tao;
    }
    for v in our_alloc.values_mut() {
        *v = *v / our_total * 100.0;
    }

    let all_netuids: std::collections::HashSet<u16> = target_alloc
        .keys()
        .chain(our_alloc.keys())
        .copied()
        .collect();

    let mut deltas = Vec::new();
    for netuid in all_netuids {
        let target_pct = target_alloc.get(&netuid).copied().unwrap_or(0.0);
        let our_pct = our_alloc.get(&netuid).copied().unwrap_or(0.0);
        let diff = target_pct - our_pct;

        if diff.abs() < threshold_pct {
            continue;
        }

        let amount_tao = diff.abs() / 100.0 * our_total;
        if amount_tao < 0.001 {
            continue;
        }

        deltas.push(Delta {
            netuid,
            action: if diff > 0.0 { "stake" } else { "unstake" }.into(),
            amount_tao,
            pct_change: diff,
        });
    }

    deltas
}

fn apply_safety(
    deltas: Vec<Delta>,
    max_per_tx: f64,
    daily_limit: f64,
    min_balance: f64,
    balance: f64,
    subnet_allowlist: Option<&[u16]>,
    subnet_denylist: &[u16],
    max_subnets: usize,
) -> Vec<Delta> {
    let mut safe = Vec::new();
    let mut spent = 0.0;

    for mut d in deltas {
        // Subnet allowlist/denylist
        if let Some(allow) = subnet_allowlist {
            if !allow.contains(&d.netuid) { continue; }
        }
        if subnet_denylist.contains(&d.netuid) { continue; }

        // Cap per-transaction
        if d.amount_tao > max_per_tx {
            d.amount_tao = max_per_tx;
        }

        // Daily limit
        if spent + d.amount_tao > daily_limit {
            let remaining = daily_limit - spent;
            if remaining <= 0.0 { continue; }
            d.amount_tao = remaining;
        }

        // Min balance (for stakes only)
        if d.action == "stake" && balance - d.amount_tao < min_balance {
            let available = balance - min_balance;
            if available <= 0.0 { continue; }
            d.amount_tao = available;
        }

        spent += d.amount_tao;
        safe.push(d);

        // Max subnets cap
        if safe.len() >= max_subnets { break; }
    }

    safe
}

fn copy_to_info(row: db::CopyRow) -> CopyInfo {
    CopyInfo {
        id: row.id,
        target_ss58: row.target_ss58,
        label: row.label,
        status: row.status,
        config: serde_json::from_str(&row.config_json).unwrap_or_default(),
        last_sync_block: row.last_sync_block,
        created_at: row.created_at,
        updated_at: row.updated_at,
        target_info: None,
    }
}

async fn copy_to_info_enriched(row: db::CopyRow, state: &AppState) -> CopyInfo {
    let target_info = get_target_info(state, &row.target_ss58).await;
    CopyInfo {
        id: row.id,
        target_ss58: row.target_ss58,
        label: row.label,
        status: row.status,
        config: serde_json::from_str(&row.config_json).unwrap_or_default(),
        last_sync_block: row.last_sync_block,
        created_at: row.created_at,
        updated_at: row.updated_at,
        target_info,
    }
}

/// Fetch live target trader details (positions + PnL) for embedding in copy responses.
async fn get_target_info(state: &AppState, target_ss58: &str) -> Option<TargetTraderInfo> {
    let positions = state.client.get_stake_for_coldkey(target_ss58, None).await.ok()?;
    let subnet_names = get_subnet_names(&state.client).await;
    let total = if positions.total_value_tao > 0.001 { positions.total_value_tao } else { 0.001 };

    let mut allocations: Vec<Allocation> = positions.positions.iter().map(|p| Allocation {
        netuid: p.netuid,
        subnet_name: subnet_names.get(&p.netuid).cloned().unwrap_or_else(|| format!("SN{}", p.netuid)),
        hotkey: p.hotkey.clone(),
        alpha_amount: p.alpha_amount,
        alpha_price_tao: p.alpha_price_tao,
        value_tao: p.value_tao,
        pct_of_total: p.value_tao / total * 100.0,
    }).collect();
    allocations.sort_by(|a, b| b.value_tao.partial_cmp(&a.value_tao).unwrap_or(std::cmp::Ordering::Equal));

    let (pnl_tao, pnl_pct) = calculate_pnl(&state.client, &state.db, target_ss58, 7).await.unwrap_or((0.0, 0.0));

    // Get label from watchlist
    let label = state.db.list_accounts().ok()
        .and_then(|accts| accts.into_iter().find(|a| a.ss58 == target_ss58).and_then(|a| a.label));

    Some(TargetTraderInfo {
        ss58: target_ss58.to_string(),
        label,
        total_stake_tao: positions.total_value_tao,
        num_subnets: allocations.len(),
        pnl_tao,
        pnl_pct,
        pnl_days: 7,
        top_allocations: allocations.into_iter().take(10).collect(),
    })
}

/// GET /trader/{ss58} — full trader profile
async fn get_trader(
    State(s): State<S>,
    Path(ss58): Path<String>,
    Query(q): Query<AccountQuery>,
) -> Result<Json<TraderResponse>, (StatusCode, Json<serde_json::Value>)> {
    let days = q.days.unwrap_or(7);
    let positions = s.client.get_stake_for_coldkey(&ss58, None).await
        .map_err(|e| err(StatusCode::INTERNAL_SERVER_ERROR, e))?;

    let subnet_names = get_subnet_names(&s.client).await;
    let total = if positions.total_value_tao > 0.001 { positions.total_value_tao } else { 0.001 };

    let mut allocations: Vec<Allocation> = positions.positions.iter().map(|p| Allocation {
        netuid: p.netuid,
        subnet_name: subnet_names.get(&p.netuid).cloned().unwrap_or_else(|| format!("SN{}", p.netuid)),
        hotkey: p.hotkey.clone(),
        alpha_amount: p.alpha_amount,
        alpha_price_tao: p.alpha_price_tao,
        value_tao: p.value_tao,
        pct_of_total: p.value_tao / total * 100.0,
    }).collect();
    allocations.sort_by(|a, b| b.value_tao.partial_cmp(&a.value_tao).unwrap_or(std::cmp::Ordering::Equal));

    let pnl = match calculate_pnl_detailed(&s.client, &s.db, &ss58, days).await {
        Ok(p) => json!({
            "pnl_tao": p.pnl_tao,
            "pnl_pct": p.pnl_pct,
            "start_value_tao": p.start_value_tao,
            "end_value_tao": p.end_value_tao,
            "block_start": p.block_start,
            "block_end": p.block_end,
            "by_subnet": p.by_subnet,
        }),
        Err(e) => json!({ "error": e.to_string() }),
    };

    let label = s.db.list_accounts().ok()
        .and_then(|accts| accts.into_iter().find(|a| a.ss58 == ss58).and_then(|a| a.label));

    Ok(Json(TraderResponse {
        ss58,
        label,
        total_stake_tao: positions.total_value_tao,
        num_subnets: allocations.len(),
        days,
        pnl,
        allocations,
    }))
}

/// Spawn a background tokio task that periodically syncs a copy.
fn spawn_copy_loop(state: Arc<AppState>, copy_id: String, target_ss58: String, interval_sec: u64) {
    let s = state.clone();
    let id = copy_id.clone();
    let handle = tokio::spawn(async move {
        info!("copy loop started for {id} (target={}, interval={interval_sec}s)", &target_ss58[..8.min(target_ss58.len())]);
        loop {
            tokio::time::sleep(tokio::time::Duration::from_secs(interval_sec)).await;
            // Check if still active
            let copy = match s.db.get_copy(&id) {
                Ok(Some(c)) if c.status == "active" => c,
                _ => { info!("copy {id} no longer active, stopping loop"); break; }
            };
            // Need wallet
            let signer = s.wallet_signer.read().await;
            let Some(ref signer) = *signer else { continue; };
            let our_ss58 = s.wallet_ss58.read().await.clone().unwrap_or_default();
            let config: serde_json::Value = serde_json::from_str(&copy.config_json).unwrap_or_default();
            let our_hotkey = config["our_hotkey"].as_str().unwrap_or("").to_string();
            let threshold = config["rebalance_threshold_pct"].as_f64().unwrap_or(5.0);
            let max_per_tx = config["max_tao_per_tx"].as_f64().unwrap_or(10.0);
            let daily_limit = config["daily_limit_tao"].as_f64().unwrap_or(100.0);
            let min_balance = config["min_balance_tao"].as_f64().unwrap_or(1.0);
            let max_subnets = config["max_subnets"].as_u64().unwrap_or(20) as usize;

            let Ok(target) = s.client.get_stake_for_coldkey(&copy.target_ss58, None).await else { continue; };
            let Ok(ours) = s.client.get_stake_for_coldkey(&our_ss58, None).await else { continue; };
            let Ok(balance) = s.client.get_balance(&our_ss58).await else { continue; };

            let denylist: Vec<u16> = config["subnet_denylist"].as_array()
                .map(|a| a.iter().filter_map(|v| v.as_u64().map(|n| n as u16)).collect())
                .unwrap_or_default();
            let allowlist: Option<Vec<u16>> = config["subnet_allowlist"].as_array()
                .map(|a| a.iter().filter_map(|v| v.as_u64().map(|n| n as u16)).collect());

            let deltas = compute_deltas(&target, &ours, threshold);
            let deltas = apply_safety(deltas, max_per_tx, daily_limit, min_balance, balance,
                allowlist.as_deref(), &denylist, max_subnets);
            if deltas.is_empty() { continue; }

            let block = s.client.get_block().await.unwrap_or(0);
            let now = chrono::Utc::now().to_rfc3339();

            for d in &deltas {
                let amount_rao = (d.amount_tao * 1e9) as u64;
                let result = if d.action == "unstake" {
                    s.client.unstake(signer, &our_hotkey, d.netuid, amount_rao).await
                } else {
                    s.client.stake(signer, &our_hotkey, d.netuid, amount_rao).await
                };
                let status = if result.is_ok() { "confirmed" } else { "failed" };
                let _ = s.db.insert_trade(&id, Some(block), &now, &d.action, d.netuid, d.amount_tao, status);
            }
            let _ = s.db.update_copy_sync_block(&id, block);
            info!("copy {id}: synced {} trades", deltas.len());
        }
    });

    // Store handle
    let state2 = state.clone();
    tokio::spawn(async move {
        state2.copy_tasks.write().await.insert(copy_id, handle);
    });
}
