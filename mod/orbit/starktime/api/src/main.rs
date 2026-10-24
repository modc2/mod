//! starktime API — StakeTime for Starknet.
//!
//! One binary, one port (:51280): JSON API + the static Next.js app (app/
//! exported to dist/, atomic-swapped by build.sh).
//!
//! Two layers:
//!   * PAPER ledger (engine.rs) — the full protocol (lock, multiplier,
//!     streaming emissions, lapse, claim) on paper STRK. Same math as the
//!     Cairo contract in contracts/. No wallet, no gas, no real funds.
//!   * LIVE Starknet reads (starknet.rs) — chain head, native staking totals,
//!     STRK/ETH balances, and the deployed StarkTime contract when
//!     config.json `contract.address` is set.
//!
//! Routing: the gateway sends the app `/starktime/*` (prefix kept) and the
//! API `/starktime/_api/*` or with `/starktime/api` stripped, so every API
//! route is mounted at `/`, `/api`, `/_api`, `/starktime/api`,
//! `/starktime/_api`. Errors are 4xx JSON, never 5xx (Cloudflare eats 5xx
//! bodies).

mod engine;
mod starknet;

use axum::{
    extract::{Query, State},
    http::{header, StatusCode, Uri},
    response::{IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use engine::{Ledger, Params, Position, DAY};
use serde::Deserialize;
use serde_json::{json, Value};
use std::{
    path::{Path, PathBuf},
    sync::{Arc, Mutex},
    time::{Instant, SystemTime, UNIX_EPOCH},
};
use tower_http::cors::CorsLayer;

struct App {
    module_dir: PathBuf,
    state_file: PathBuf,
    ledger: Mutex<Ledger>,
    config: Value,
    network_cache: Mutex<Option<(Instant, Value)>>,
}

impl App {
    fn save(&self, l: &Ledger) {
        if let Some(dir) = self.state_file.parent() {
            let _ = std::fs::create_dir_all(dir);
        }
        let tmp = self.state_file.with_extension("json.tmp");
        if std::fs::write(&tmp, serde_json::to_string_pretty(l).unwrap()).is_ok() {
            let _ = std::fs::rename(&tmp, &self.state_file);
        }
    }

    fn contract(&self) -> (String, Option<String>) {
        let c = self.config.get("contract");
        let net = c.and_then(|c| c.get("network")).and_then(|n| n.as_str()).unwrap_or("sepolia").to_string();
        let addr = c
            .and_then(|c| c.get("address"))
            .and_then(|a| a.as_str())
            .filter(|a| starknet::valid_address(a))
            .map(String::from);
        (net, addr)
    }
}

fn now() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_secs()
}

fn bad(msg: impl Into<String>) -> Response {
    (StatusCode::BAD_REQUEST, Json(json!({"error": msg.into()}))).into_response()
}

fn r6(x: f64) -> f64 {
    (x * 1e6).round() / 1e6
}

fn position_json(l: &Ledger, p: &Position, t: u64) -> Value {
    json!({
        "id": p.id,
        "owner": p.owner,
        "validator": p.validator,
        "amount": r6(p.amount),
        "start": p.start,
        "lock_seconds": p.lock_seconds,
        "end": p.end(),
        "remaining": p.end().saturating_sub(t),
        "multiplier": p.multiplier_bps as f64 / engine::BPS,
        "weight": r6(p.weight),
        "share": if l.total_weight > 0.0 { p.weight / l.total_weight } else { 0.0 },
        "pending": r6(l.pending(p)),
        "lapsed": p.lapsed,
        "unlocked": t >= p.end(),
    })
}

fn stats_json(l: &Ledger, t: u64) -> Value {
    let stakers: std::collections::BTreeSet<&str> = l.positions.values().map(|p| p.owner.as_str()).collect();
    let rate = l.rate_per_second(t);
    // APR for a 1x position: yearly emission / total weight
    let apr_1x = if l.total_weight > 0.0 { rate * 365.0 * DAY as f64 / l.total_weight } else { 0.0 };
    json!({
        "now": t,
        "genesis": l.genesis,
        "epoch": l.epoch(t),
        "epoch_seconds": l.params.epoch_seconds,
        "next_epoch_at": l.genesis + (l.epoch(t) + 1) * l.params.epoch_seconds,
        "emission_epoch": r6(l.params.epoch_emission(l.epoch(t))),
        "rate_per_second": rate,
        "total_staked": r6(l.total_staked),
        "total_weight": r6(l.total_weight),
        "avg_multiplier": if l.total_staked > 0.0 { l.total_weight / l.total_staked } else { 1.0 },
        "total_emitted": r6(l.total_emitted),
        "total_claimed": r6(l.total_claimed),
        "positions": l.positions.len(),
        "stakers": stakers.len(),
        "apr_1x": apr_1x,
        "mode": "paper",
    })
}

// ── read handlers ──

async fn health() -> Json<Value> {
    Json(json!({"ok": true}))
}

async fn info(State(app): State<Arc<App>>) -> Json<Value> {
    let port = app.config.get("port").and_then(|p| p.as_u64()).unwrap_or(51280);
    let (net, addr) = app.contract();
    Json(json!({
        "name": "starktime",
        "description": "StakeTime for Starknet: lock STRK for time, earn weight = amount x lock multiplier, rewards stream pro-rata by weight",
        "port": port,
        "url": format!("http://localhost:{port}/starktime/"),
        "stack": {"api": "rust/axum", "app": "next.js static export", "contract": "cairo 2 (contracts/)"},
        "contract": {"network": net, "address": addr, "deployed": addr.is_some()},
        "fns": app.config.get("fns").cloned().unwrap_or(Value::Null),
    }))
}

async fn params(State(app): State<Arc<App>>) -> Json<Value> {
    let l = app.ledger.lock().unwrap();
    Json(json!({"params": l.params}))
}

#[derive(Deserialize)]
struct QuoteQ {
    #[serde(default)]
    amount: Option<f64>,
    #[serde(default)]
    lock_seconds: Option<u64>,
    #[serde(default)]
    lock_days: Option<f64>,
}

async fn quote(State(app): State<Arc<App>>, Query(q): Query<QuoteQ>) -> Response {
    let t = now();
    let amount = q.amount.unwrap_or(1000.0);
    if !amount.is_finite() || amount <= 0.0 {
        return bad("amount must be > 0");
    }
    let lock = q.lock_seconds.or(q.lock_days.map(|d| (d.max(0.0) * DAY as f64) as u64)).unwrap_or(0);
    let mut l = app.ledger.lock().unwrap();
    l.accrue(t);
    if lock > l.params.max_lock_seconds {
        return bad(format!("lock exceeds max ({}s)", l.params.max_lock_seconds));
    }
    let (m, weight) = l.quote(amount, lock);
    let new_total = l.total_weight + weight;
    let share = weight / new_total;
    let daily = l.params.epoch_emission(l.epoch(t)) * DAY as f64 / l.params.epoch_seconds as f64 * share;
    Json(json!({
        "amount": amount,
        "lock_seconds": lock,
        "multiplier": m as f64 / engine::BPS,
        "weight": r6(weight),
        "share": share,
        "daily_reward": r6(daily),
        "apr_while_locked": if amount > 0.0 { daily * 365.0 / amount } else { 0.0 },
        "unlock_at": t + lock,
    }))
    .into_response()
}

#[derive(Deserialize)]
struct CurveQ {
    #[serde(default)]
    samples: Option<usize>,
}

async fn curve(State(app): State<Arc<App>>, Query(q): Query<CurveQ>) -> Json<Value> {
    let l = app.ledger.lock().unwrap();
    let n = q.samples.unwrap_or(64).clamp(2, 512);
    let max = l.params.max_lock_seconds;
    let samples: Vec<Value> = (0..n)
        .map(|i| {
            let s = max * i as u64 / (n as u64 - 1);
            json!({"lock_seconds": s, "multiplier": l.params.multiplier_bps(s) as f64 / engine::BPS})
        })
        .collect();
    Json(json!({"points": l.params.points, "samples": samples, "max_lock_seconds": max}))
}

#[derive(Deserialize)]
struct EmissionsQ {
    #[serde(default)]
    epochs: Option<u64>,
}

async fn emissions(State(app): State<Arc<App>>, Query(q): Query<EmissionsQ>) -> Json<Value> {
    let t = now();
    let l = app.ledger.lock().unwrap();
    let n = q.epochs.unwrap_or(365).clamp(1, 3650);
    let cur = l.epoch(t);
    let mut cum = 0.0;
    let schedule: Vec<Value> = (0..n)
        .map(|k| {
            let e = l.params.epoch_emission(k);
            cum += e;
            json!({"epoch": k, "emission": r6(e), "cumulative": r6(cum)})
        })
        .collect();
    Json(json!({"current_epoch": cur, "epoch_seconds": l.params.epoch_seconds, "schedule": schedule}))
}

async fn stats(State(app): State<Arc<App>>) -> Json<Value> {
    let t = now();
    let mut l = app.ledger.lock().unwrap();
    l.accrue(t);
    Json(stats_json(&l, t))
}

#[derive(Deserialize)]
struct OwnerQ {
    #[serde(default)]
    owner: String,
}

async fn account(State(app): State<Arc<App>>, Query(q): Query<OwnerQ>) -> Response {
    let owner = match engine::normalize_owner(&q.owner) {
        Ok(o) => o,
        Err(e) => return bad(e),
    };
    let t = now();
    let mut l = app.ledger.lock().unwrap();
    l.accrue(t);
    let wallet = l.wallets.get(&owner).copied().unwrap_or(l.params.faucet);
    let mine: Vec<&Position> = l.positions.values().filter(|p| p.owner == owner).collect();
    let staked: f64 = mine.iter().map(|p| p.amount).sum();
    let weight: f64 = mine.iter().map(|p| p.weight).sum();
    let pending: f64 = mine.iter().map(|p| l.pending(p)).sum();
    let positions: Vec<Value> = mine.iter().map(|p| position_json(&l, p, t)).collect();
    let rate = l.rate_per_second(t);
    let share = if l.total_weight > 0.0 { weight / l.total_weight } else { 0.0 };
    Json(json!({
        "owner": owner,
        "wallet": r6(wallet),
        "staked": r6(staked),
        "weight": r6(weight),
        "share": share,
        "pending": r6(pending),
        "earning_per_second": rate * share,
        "positions": positions,
        "now": t,
    }))
    .into_response()
}

async fn positions(State(app): State<Arc<App>>) -> Json<Value> {
    let t = now();
    let mut l = app.ledger.lock().unwrap();
    l.accrue(t);
    let mut list: Vec<&Position> = l.positions.values().collect();
    list.sort_by(|a, b| b.weight.partial_cmp(&a.weight).unwrap());
    let out: Vec<Value> = list.iter().take(200).map(|p| position_json(&l, p, t)).collect();
    Json(json!({"positions": out, "total": l.positions.len()}))
}

async fn leaderboard(State(app): State<Arc<App>>) -> Json<Value> {
    let t = now();
    let mut l = app.ledger.lock().unwrap();
    l.accrue(t);
    let mut by: std::collections::BTreeMap<String, (f64, f64, f64, usize)> = Default::default();
    for p in l.positions.values() {
        let e = by.entry(p.owner.clone()).or_default();
        e.0 += p.amount;
        e.1 += p.weight;
        e.2 += l.pending(p);
        e.3 += 1;
    }
    let mut rows: Vec<Value> = by
        .into_iter()
        .map(|(owner, (amt, w, pend, n))| {
            json!({"owner": owner, "staked": r6(amt), "weight": r6(w), "pending": r6(pend), "positions": n,
                   "share": if l.total_weight > 0.0 { w / l.total_weight } else { 0.0 }})
        })
        .collect();
    rows.sort_by(|a, b| b["weight"].as_f64().partial_cmp(&a["weight"].as_f64()).unwrap());
    rows.truncate(100);
    Json(json!({"stakers": rows}))
}

async fn validators(State(app): State<Arc<App>>) -> Json<Value> {
    let t = now();
    let mut l = app.ledger.lock().unwrap();
    l.accrue(t);
    let mut by: std::collections::BTreeMap<String, (f64, f64, std::collections::BTreeSet<String>)> = Default::default();
    for p in l.positions.values() {
        let key = p.validator.clone().unwrap_or_else(|| "pool".into());
        let e = by.entry(key).or_default();
        e.0 += p.amount;
        e.1 += p.weight;
        e.2.insert(p.owner.clone());
    }
    let mut rows: Vec<Value> = by
        .into_iter()
        .map(|(v, (amt, w, owners))| json!({"validator": v, "staked": r6(amt), "weight": r6(w), "delegators": owners.len()}))
        .collect();
    rows.sort_by(|a, b| b["weight"].as_f64().partial_cmp(&a["weight"].as_f64()).unwrap());
    Json(json!({"validators": rows}))
}

async fn history(State(app): State<Arc<App>>) -> Json<Value> {
    let l = app.ledger.lock().unwrap();
    let ev: Vec<_> = l.history.iter().rev().take(100).cloned().collect();
    Json(json!({"events": ev}))
}

// ── write handlers (paper ledger) ──

#[derive(Deserialize)]
struct StakeBody {
    owner: String,
    amount: f64,
    #[serde(default)]
    lock_seconds: Option<u64>,
    #[serde(default)]
    lock_days: Option<f64>,
    #[serde(default)]
    validator: Option<String>,
}

async fn stake(State(app): State<Arc<App>>, Json(b): Json<StakeBody>) -> Response {
    let lock = b.lock_seconds.or(b.lock_days.map(|d| (d.max(0.0) * DAY as f64) as u64)).unwrap_or(0);
    let t = now();
    let mut l = app.ledger.lock().unwrap();
    match l.stake(t, &b.owner, b.amount, lock, b.validator) {
        Ok(p) => {
            app.save(&l);
            Json(json!({"ok": true, "position": position_json(&l, &p, t)})).into_response()
        }
        Err(e) => bad(e),
    }
}

#[derive(Deserialize)]
struct IdBody {
    owner: String,
    id: u64,
    #[serde(default)]
    lock_seconds: Option<u64>,
    #[serde(default)]
    lock_days: Option<f64>,
}

async fn unstake(State(app): State<Arc<App>>, Json(b): Json<IdBody>) -> Response {
    let t = now();
    let mut l = app.ledger.lock().unwrap();
    match l.unstake(t, &b.owner, b.id) {
        Ok((principal, rewards)) => {
            app.save(&l);
            Json(json!({"ok": true, "principal": r6(principal), "rewards": r6(rewards)})).into_response()
        }
        Err(e) => bad(e),
    }
}

async fn extend(State(app): State<Arc<App>>, Json(b): Json<IdBody>) -> Response {
    let Some(lock) = b.lock_seconds.or(b.lock_days.map(|d| (d.max(0.0) * DAY as f64) as u64)) else {
        return bad("lock_seconds or lock_days required");
    };
    let t = now();
    let mut l = app.ledger.lock().unwrap();
    match l.extend(t, &b.owner, b.id, lock) {
        Ok(p) => {
            app.save(&l);
            Json(json!({"ok": true, "position": position_json(&l, &p, t)})).into_response()
        }
        Err(e) => bad(e),
    }
}

#[derive(Deserialize)]
struct OwnerBody {
    owner: String,
}

async fn claim(State(app): State<Arc<App>>, Json(b): Json<OwnerBody>) -> Response {
    let t = now();
    let mut l = app.ledger.lock().unwrap();
    match l.claim(t, &b.owner) {
        Ok(paid) => {
            app.save(&l);
            Json(json!({"ok": true, "claimed": r6(paid)})).into_response()
        }
        Err(e) => bad(e),
    }
}

// ── live Starknet ──

async fn network(State(app): State<Arc<App>>) -> Response {
    if let Some((at, v)) = app.network_cache.lock().unwrap().clone() {
        if at.elapsed().as_secs() < 15 {
            return Json(v).into_response();
        }
    }
    let v = tokio::task::spawn_blocking(|| {
        let rpc = starknet::Rpc::new("mainnet");
        let block = rpc.block_number();
        let chain = rpc.chain_id().ok();
        let total_stake = rpc.call(starknet::STAKING, "get_total_stake", &[]).ok().map(|r| {
            starknet::units(&starknet::felt(r.first().map(|s| s.as_str()).unwrap_or("0x0")), 18)
        });
        let epoch = rpc
            .call(starknet::STAKING, "get_current_epoch", &[])
            .ok()
            .and_then(|r| r.first().map(|s| starknet::felt(s).to_string().parse::<u64>().unwrap_or(0)));
        let min_stake = rpc
            .call(starknet::STAKING, "contract_parameters_v1", &[])
            .ok()
            .and_then(|r| r.first().map(|s| starknet::units(&starknet::felt(s), 18)));
        let held = rpc.balance(starknet::STRK, starknet::STAKING).ok().map(|b| b.0);
        match block {
            Ok(b) => json!({
                "ok": true,
                "network": "mainnet",
                "chain_id": chain,
                "block": b,
                "staking": {
                    "contract": starknet::STAKING,
                    "total_stake_strk": total_stake,
                    "strk_held": held,
                    "epoch": epoch,
                    "min_validator_stake_strk": min_stake,
                },
                "tokens": {"STRK": starknet::STRK, "ETH": starknet::ETH},
            }),
            Err(e) => json!({"ok": false, "error": e}),
        }
    })
    .await
    .unwrap_or_else(|_| json!({"ok": false, "error": "rpc task failed"}));
    if v["ok"].as_bool() == Some(true) {
        *app.network_cache.lock().unwrap() = Some((Instant::now(), v.clone()));
    }
    Json(v).into_response()
}

#[derive(Deserialize)]
struct AddrQ {
    #[serde(default)]
    address: String,
    #[serde(default)]
    network: Option<String>,
}

async fn balance(Query(q): Query<AddrQ>) -> Response {
    if !starknet::valid_address(&q.address) {
        return bad("address must be a 0x Starknet address");
    }
    let net = q.network.unwrap_or_else(|| "mainnet".into());
    let addr = q.address.clone();
    let v = tokio::task::spawn_blocking(move || {
        let rpc = starknet::Rpc::new(&net);
        let strk = rpc.balance(starknet::STRK, &addr);
        let eth = rpc.balance(starknet::ETH, &addr);
        json!({
            "address": addr,
            "network": net,
            "strk": strk.as_ref().ok().map(|b| b.0),
            "eth": eth.as_ref().ok().map(|b| b.0),
            "error": strk.err(),
        })
    })
    .await
    .unwrap_or_else(|_| json!({"error": "rpc task failed"}));
    Json(v).into_response()
}

/// The Cairo contract: source + deployment status + live reads when deployed.
async fn contract(State(app): State<Arc<App>>) -> Json<Value> {
    let (net, addr) = app.contract();
    let src = std::fs::read_to_string(app.module_dir.join("contracts/src/lib.cairo")).unwrap_or_default();
    let live = match addr.clone() {
        None => Value::Null,
        Some(a) => {
            let net2 = net.clone();
            tokio::task::spawn_blocking(move || {
                let rpc = starknet::Rpc::new(&net2);
                match rpc.call(&a, "get_stats", &[]) {
                    // (total_staked u256, total_weight u256, positions u64, reward_rate u256)
                    Ok(r) => json!({
                        "total_staked": starknet::units(&starknet::u256(&r, 0), 18),
                        "total_weight": starknet::units(&starknet::u256(&r, 2), 18),
                        "positions": r.get(4).map(|s| starknet::felt(s).to_string()),
                        "reward_rate_per_s": starknet::units(&starknet::u256(&r, 5), 18),
                    }),
                    Err(e) => json!({"error": e}),
                }
            })
            .await
            .unwrap_or(Value::Null)
        }
    };
    Json(json!({
        "network": net,
        "address": addr,
        "deployed": addr.is_some(),
        "source": src,
        "live": live,
        "entrypoints": ["stake", "unstake", "extend", "claim", "claim_all", "poke", "notify_reward", "set_points",
                        "get_position", "positions_of", "pending", "get_multiplier", "get_points", "get_stats", "get_reward_period"],
    }))
}

async fn whitepaper(State(app): State<Arc<App>>) -> Response {
    let md = std::fs::read_to_string(app.module_dir.join("whitepaper.md")).unwrap_or_default();
    Json(json!({"markdown": md})).into_response()
}

async fn self_test() -> Json<Value> {
    // end-to-end on a throwaway ledger
    let t0 = 1_700_000_000;
    let mut l = Ledger::new(Params::default(), t0);
    let a = l.stake(t0, "0xa11ce", 1000.0, 365 * DAY, None).is_ok();
    let b = l.stake(t0, "0xb0b", 1000.0, 0, None).is_ok();
    l.accrue(t0 + DAY);
    let pa = l.pending(&l.positions[&0]);
    let pb = l.pending(&l.positions[&1]);
    let locked = l.unstake(t0 + DAY, "0xa11ce", 0).is_err();
    let passed = a && b && (pa / pb - 3.0).abs() < 1e-9 && locked;
    Json(json!({"passed": passed, "ratio_3x": pa / pb, "locked_rejected": locked}))
}

// ── static app (Next.js export of app/ in dist/) ──

fn content_type(path: &Path) -> &'static str {
    match path.extension().and_then(|e| e.to_str()).unwrap_or("") {
        "html" => "text/html; charset=utf-8",
        "js" | "mjs" => "application/javascript",
        "css" => "text/css",
        "json" => "application/json",
        "svg" => "image/svg+xml",
        "png" => "image/png",
        "ico" => "image/x-icon",
        "txt" => "text/plain; charset=utf-8",
        "woff2" => "font/woff2",
        "map" => "application/json",
        _ => "application/octet-stream",
    }
}

async fn static_handler(State(app): State<Arc<App>>, uri: Uri) -> Response {
    let raw = uri.path();
    let rel = if raw == "/starktime" || raw == "/starktime/" || raw == "/" {
        ""
    } else if raw == "/starktime.txt" {
        "index.txt"
    } else if let Some(r) = raw.strip_prefix("/starktime/") {
        r
    } else {
        raw.trim_start_matches('/')
    };
    if rel.contains("..") {
        return bad("bad path");
    }
    let dist = app.module_dir.join("dist");
    let rel = if rel.is_empty() { "index.html" } else { rel };
    for cand in [dist.join(rel), dist.join(format!("{rel}.html")), dist.join(rel).join("index.html")] {
        if cand.is_file() {
            if let Ok(bytes) = std::fs::read(&cand) {
                return ([(header::CONTENT_TYPE, content_type(&cand))], bytes).into_response();
            }
        }
    }
    if !dist.exists() {
        return (
            StatusCode::NOT_FOUND,
            [(header::CONTENT_TYPE, "text/html; charset=utf-8")],
            "<h1>starktime</h1><p>app not built yet — run <code>m starktime/build</code>. API is up at /starktime/_api/info</p>",
        )
            .into_response();
    }
    (StatusCode::NOT_FOUND, Json(json!({"error": "not found"}))).into_response()
}

fn api_router() -> Router<Arc<App>> {
    Router::new()
        .route("/health", get(health))
        .route("/info", get(info))
        .route("/params", get(params))
        .route("/quote", get(quote))
        .route("/curve", get(curve))
        .route("/emissions", get(emissions))
        .route("/stats", get(stats))
        .route("/account", get(account))
        .route("/positions", get(positions))
        .route("/leaderboard", get(leaderboard))
        .route("/validators", get(validators))
        .route("/history", get(history))
        .route("/network", get(network))
        .route("/balance", get(balance))
        .route("/contract", get(contract))
        .route("/whitepaper", get(whitepaper))
        .route("/test", get(self_test))
        .route("/stake", post(stake))
        .route("/unstake", post(unstake))
        .route("/extend", post(extend))
        .route("/claim", post(claim))
}

fn load_ledger(state_file: &Path, config: &Value) -> Ledger {
    if let Some(l) = std::fs::read_to_string(state_file).ok().and_then(|s| serde_json::from_str(&s).ok()) {
        return l;
    }
    // first boot: protocol params from config.json `protocol`, else defaults
    let params = config
        .get("protocol")
        .and_then(|p| serde_json::from_value::<Params>(p.clone()).ok())
        .filter(|p| p.validate().is_ok())
        .unwrap_or_default();
    Ledger::new(params, now())
}

#[tokio::main]
async fn main() {
    let module_dir = std::env::var("STARKTIME_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|_| std::env::current_dir().unwrap());
    let config: Value = std::fs::read_to_string(module_dir.join("config.json"))
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or_else(|| json!({}));
    let port: u16 = std::env::var("STARKTIME_PORT")
        .ok()
        .and_then(|p| p.parse().ok())
        .or_else(|| config.get("port").and_then(|p| p.as_u64()).map(|p| p as u16))
        .unwrap_or(51280);
    let data_dir = std::env::var("STARKTIME_DATA")
        .map(PathBuf::from)
        .unwrap_or_else(|_| module_dir.join("state"));
    let state_file = data_dir.join("ledger.json");

    let app = Arc::new(App {
        ledger: Mutex::new(load_ledger(&state_file, &config)),
        module_dir,
        state_file,
        config,
        network_cache: Mutex::new(None),
    });

    let api = api_router();
    let router = Router::new()
        .nest("/api", api.clone())
        .nest("/_api", api.clone())
        .nest("/starktime/api", api.clone())
        .nest("/starktime/_api", api.clone())
        .merge(api)
        .fallback(static_handler)
        .layer(CorsLayer::permissive())
        .with_state(app);

    let addr = std::net::SocketAddr::from(([0, 0, 0, 0], port));
    println!("starktime on :{port} -> http://localhost:{port}/starktime/");
    let listener = tokio::net::TcpListener::bind(addr).await.expect("bind");
    axum::serve(listener, router).await.unwrap();
}
