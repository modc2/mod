//! HTTP surface for the investment book.
//!
//! One verb — **invest** — and one noun — a **position**. The same three calls
//! (`POST /invest`, `/add`, `/withdraw`) mean the same thing whether the money
//! goes into a Hyperliquid vault or into a trader sleeve; only the plumbing
//! underneath differs, and the caller never has to know which.
//!
//! Every route here is investor-scoped. The auth guard binds `investor` (query
//! or body) to the signed-in wallet exactly like `eoa`, and each id-route
//! re-checks ownership against the book before it does anything — a valid
//! token for wallet A must never be able to touch wallet B's money.
//!
//! `GET /invest/preview` is deliberately public: it reads a *leader's* public
//! portfolio and does arithmetic. Being able to ask "what would $250 in this
//! trader actually buy me?" without connecting a wallet is the difference
//! between a product and a form.

use std::collections::HashMap;

use axum::{
    extract::{Path, Query, State},
    http::StatusCode,
    routing::{get, post},
    Extension, Json, Router,
};
use serde::Deserialize;
use serde_json::{json, Value};

use crate::actions;
use crate::invest::{self, Kind, Mode, Position, Risk, Status};
use crate::invest_engine::parse_leader_state;
use crate::AppState;

type ApiResult = Result<Json<Value>, (StatusCode, Json<Value>)>;

pub fn router() -> Router<AppState> {
    Router::new()
        // Sizing arithmetic on public data — no wallet needed.
        .route("/invest/preview", get(preview))
        // The book.
        .route("/invest", get(list).post(create))
        .route("/invest/:id", get(detail).patch(patch).delete(remove))
        .route("/invest/:id/add", post(add))
        .route("/invest/:id/withdraw", post(withdraw))
        .route("/invest/:id/pause", post(pause))
        .route("/invest/:id/resume", post(resume))
        .route("/invest/:id/close", post(close))
}

// ─── helpers ────────────────────────────────────────────────────────────

fn bad(msg: impl Into<String>) -> (StatusCode, Json<Value>) {
    (StatusCode::BAD_REQUEST, Json(json!({"error": msg.into()})))
}
fn not_found() -> (StatusCode, Json<Value>) {
    (StatusCode::NOT_FOUND, Json(json!({"error": "no such position"})))
}
fn forbidden() -> (StatusCode, Json<Value>) {
    (StatusCode::FORBIDDEN, Json(json!({"error": "this position belongs to a different wallet"})))
}
fn oops(e: impl std::fmt::Display) -> (StatusCode, Json<Value>) {
    (StatusCode::INTERNAL_SERVER_ERROR, Json(json!({"error": e.to_string()})))
}

/// Who is calling. The guard inserts the recovered address; in open/dev mode
/// there is none, so the caller's own claim is used instead.
fn who(ext: &Option<Extension<crate::auth::AuthedUser>>, fallback: Option<&str>) -> Option<String> {
    ext.as_ref().map(|Extension(u)| u.0.to_lowercase())
        .or_else(|| fallback.map(|s| s.to_lowercase()))
}

fn is_addr(a: &str) -> bool {
    a.len() == 42 && a.starts_with("0x") && a[2..].chars().all(|c| c.is_ascii_hexdigit())
}

/// Load a position and prove the caller owns it.
fn owned(s: &AppState, id: &str, caller: &Option<String>) -> Result<Position, (StatusCode, Json<Value>)> {
    let p = s.invest.get(id).ok_or_else(not_found)?;
    match caller {
        Some(c) if !p.investor.eq_ignore_ascii_case(c) => Err(forbidden()),
        None => Err((StatusCode::UNAUTHORIZED, Json(json!({"error": "sign in to manage a position"})))),
        _ => Ok(p),
    }
}

/// The one JSON shape the console renders, for both kinds of position.
fn view(p: &Position, marks: &HashMap<String, f64>, follower_state: Option<&Value>) -> Value {
    let v = if p.is_trader() {
        invest::value_sleeve(p, marks)
    } else {
        invest::value_vault(p, follower_state)
    };
    let legs: Vec<Value> = p.sleeve.open_legs().map(|(coin, leg)| {
        let mark = marks.get(coin).copied().unwrap_or(leg.avg_px);
        json!({
            "coin": coin,
            "size": leg.size,
            "avg_px": leg.avg_px,
            "mark": mark,
            "notional": leg.size.abs() * mark,
            "unrealized": (mark - leg.avg_px) * leg.size,
        })
    }).collect();

    json!({
        "id": p.id,
        "investor": p.investor,
        "kind": p.kind.as_str(),
        "target": p.target,
        "name": p.name,
        "status": p.status.as_str(),
        "mode": p.mode.as_str(),
        "group_id": p.group_id,
        "group_name": p.group_name,
        "group_weight": p.group_weight,
        "contributed_usd": p.contributed_usd,
        "withdrawn_usd": p.withdrawn_usd,
        "net_contributed": p.net_contributed(),
        "basis": p.basis(),
        "risk": p.risk,
        "value": v,
        "legs": legs,
        "created_ms": p.created_ms,
        "updated_ms": p.updated_ms,
        "last_sync_ms": p.last_sync_ms,
        "last_error": p.last_error,
        "next_attempt_ms": p.next_attempt_ms,
    })
}

/// How much of the investor's Hyperliquid account is already spoken for.
///
/// Trader sleeves don't move money — they occupy margin inside the same
/// account — so "can I afford this?" is account equity minus what the other
/// live sleeves are already sized against. Getting this wrong is how someone
/// ends up 3× levered while believing they invested 3 × $100.
async fn capacity(s: &AppState, investor: &str) -> Value {
    let state = s.hl.user_state(investor).await.unwrap_or(Value::Null);
    let num = |v: &Value, k: &str| v.get(k).and_then(|x| x.as_str())
        .and_then(|t| t.parse::<f64>().ok()).unwrap_or(0.0);
    let account_value = state.get("marginSummary")
        .map(|m| num(m, "accountValue")).unwrap_or(0.0);
    let withdrawable = num(&state, "withdrawable");
    let committed: f64 = s.invest.list(investor).iter()
        .filter(|p| p.is_trader() && p.mode == Mode::Live
            && matches!(p.status, Status::Active | Status::Closing))
        .map(|p| p.basis())
        .sum();
    json!({
        "account_value": account_value,
        "withdrawable": withdrawable,
        "committed": committed,
        "free": (account_value - committed).max(0.0),
    })
}

// ─── GET /invest — the portfolio ────────────────────────────────────────

#[derive(Deserialize)]
struct ListQ { investor: String, #[serde(default)] include_closed: bool }

async fn list(State(s): State<AppState>, Query(q): Query<ListQ>) -> ApiResult {
    if !is_addr(&q.investor) { return Err(bad("investor must be a 0x… address")); }
    let all = s.invest.list(&q.investor);
    let positions: Vec<Position> = all.into_iter()
        .filter(|p| q.include_closed || p.status != Status::Closed)
        .collect();

    let marks = s.engine.marks().await;

    // Vault equity comes from HL, one call per distinct vault (45s cached).
    let mut vault_states: HashMap<String, Value> = HashMap::new();
    for p in positions.iter().filter(|p| p.kind == Kind::Vault) {
        if vault_states.contains_key(&p.target) { continue; }
        if let Ok(d) = s.hl.vault_details(&p.target, Some(&q.investor)).await {
            if let Some(fs) = d.get("followerState") {
                if !fs.is_null() { vault_states.insert(p.target.clone(), fs.clone()); }
            }
        }
    }

    let views: Vec<Value> = positions.iter()
        .map(|p| view(p, &marks, vault_states.get(&p.target)))
        .collect();

    // Totals the investor actually cares about, computed from the same views
    // the table renders — so the header can never disagree with the rows.
    let sum = |key: &str| -> f64 {
        views.iter().filter_map(|v| v.get("value").and_then(|x| x.get(key)).and_then(|x| x.as_f64())).sum()
    };
    let invested: f64 = views.iter()
        .filter_map(|v| v.get("net_contributed").and_then(|x| x.as_f64())).sum();
    let equity = sum("equity");
    let pnl = sum("pnl");

    Ok(Json(json!({
        "investor": q.investor.to_lowercase(),
        "positions": views,
        "totals": {
            "count": views.len(),
            "invested": invested,
            "equity": equity,
            "pnl": pnl,
            "roi_pct": if invested > 0.0 { pnl / invested * 100.0 } else { 0.0 },
            "exposure": sum("exposure"),
        },
        "capacity": capacity(&s, &q.investor).await,
        "engine": {
            "dry_run": s.engine.is_dry(),
            "stats": s.engine.stats(),
        },
    })))
}

// ─── GET /invest/:id — one position, in full ────────────────────────────

async fn detail(
    State(s): State<AppState>,
    Path(id): Path<String>,
    ext: Option<Extension<crate::auth::AuthedUser>>,
) -> ApiResult {
    let caller = who(&ext, None);
    let p = match &caller {
        Some(_) => owned(&s, &id, &caller)?,
        // Open/dev mode: no identity to check against.
        None => s.invest.get(&id).ok_or_else(not_found)?,
    };
    let marks = s.engine.marks().await;
    let fs = if p.kind == Kind::Vault {
        s.hl.vault_details(&p.target, Some(&p.investor)).await.ok()
            .and_then(|d| d.get("followerState").cloned())
            .filter(|v| !v.is_null())
    } else { None };

    let mut body = view(&p, &marks, fs.as_ref());
    body["fills"] = json!(p.sleeve.fills);
    body["flows"] = json!(p.flows);
    body["events"] = json!(p.events);
    body["realized_pnl"] = json!(p.sleeve.realized_pnl);

    // What the sleeve is aiming at right now — the difference between the
    // "legs" above and these targets is exactly what the engine will trade.
    if p.is_trader() {
        if let Ok(st) = s.hl.user_state(&p.target).await {
            let snap = parse_leader_state(&st, &marks);
            let plan = invest::plan(&snap.positions, snap.equity, p.basis(), &p.risk);
            body["leader"] = json!({
                "address": p.target,
                "equity": snap.equity,
                "positions": snap.positions.len(),
                "scale": plan.scale,
                "deleverage": plan.deleverage,
                "targets": plan.targets.iter().map(|t| json!({
                    "coin": t.coin, "size": t.size, "mark": t.mark,
                    "notional": t.size.abs() * t.mark,
                })).collect::<Vec<_>>(),
            });
        }
    }
    Ok(Json(body))
}

// ─── GET /invest/preview — "what would my money do?" ────────────────────

#[derive(Deserialize)]
struct PreviewQ {
    trader: String,
    amount: f64,
    #[serde(default)] max_leverage: Option<f64>,
    #[serde(default)] min_order_usd: Option<f64>,
    #[serde(default)] coins_allow: Option<String>,
}

async fn preview(State(s): State<AppState>, Query(q): Query<PreviewQ>) -> ApiResult {
    if !is_addr(&q.trader) { return Err(bad("trader must be a 0x… address")); }
    if !(q.amount > 0.0) { return Err(bad("enter an amount above zero")); }

    let mut risk = Risk::default();
    if let Some(l) = q.max_leverage { risk.max_leverage = l; }
    if let Some(m) = q.min_order_usd { risk.min_order_usd = m; }
    if let Some(c) = q.coins_allow {
        risk.coins_allow = c.split(',').map(|x| x.trim().to_string()).filter(|x| !x.is_empty()).collect();
    }
    let risk = risk.sanitized();

    let marks = s.engine.marks().await;
    let state = s.hl.user_state(&q.trader).await.map_err(oops)?;
    let snap = parse_leader_state(&state, &marks);
    let plan = invest::plan(&snap.positions, snap.equity, q.amount, &risk);

    // The honest part: at small sizes, some of the leader's positions are
    // simply too small to copy — HL won't take a $3 order. Say so up front
    // instead of letting the investor discover it as silence.
    let mut tradable = Vec::new();
    let mut too_small = Vec::new();
    for t in &plan.targets {
        let notional = t.size.abs() * t.mark;
        let row = json!({
            "coin": t.coin, "size": t.size, "mark": t.mark, "notional": notional,
            "side": if t.size > 0.0 { "long" } else { "short" },
        });
        if notional >= risk.min_order_usd { tradable.push(row); } else { too_small.push(row); }
    }
    let covered: f64 = tradable.iter().filter_map(|r| r["notional"].as_f64()).sum();

    Ok(Json(json!({
        "trader": q.trader.to_lowercase(),
        "amount": q.amount,
        "leader_equity": snap.equity,
        "leader_positions": snap.positions.len(),
        "scale": plan.scale,
        "deleverage": plan.deleverage,
        "gross": plan.gross,
        "leverage": if q.amount > 0.0 { plan.gross / q.amount } else { 0.0 },
        "positions": tradable,
        "too_small": too_small,
        "covered_pct": if plan.gross > 0.0 { covered / plan.gross * 100.0 } else { 0.0 },
        "min_order_usd": risk.min_order_usd,
        "note": if snap.equity <= 0.0 {
            "this trader's account reads as empty right now — nothing to copy"
        } else if plan.targets.is_empty() {
            "this trader is holding no positions right now — your money would sit in cash until they open one"
        } else if !too_small.is_empty() {
            "some of this trader's positions are too small to copy at this amount — invest more to cover them"
        } else {
            "this amount can copy every position this trader currently holds"
        },
    })))
}

// ─── POST /invest — put money in ────────────────────────────────────────

#[derive(Deserialize)]
struct CreateBody {
    investor: String,
    /// "vault" | "trader" | "strat"
    kind: String,
    /// Vault address, leader address, or strat/index id (kind = strat).
    #[serde(default)] target: Option<String>,
    #[serde(default)] index_id: Option<String>,
    amount_usd: f64,
    #[serde(default)] name: Option<String>,
    #[serde(default)] mode: Option<String>,
    #[serde(default)] risk: Option<Risk>,
}

async fn create(State(s): State<AppState>, Json(b): Json<CreateBody>) -> ApiResult {
    let investor = b.investor.to_lowercase();
    if !is_addr(&investor) { return Err(bad("investor must be a 0x… address")); }
    if !(b.amount_usd > 0.0) { return Err(bad("enter an amount above zero")); }
    let mode = match b.mode.as_deref() {
        Some("paper") => Mode::Paper,
        _ => Mode::Live,
    };
    let risk = b.risk.unwrap_or_default().sanitized();
    let now = chrono::Utc::now().timestamp_millis();

    match b.kind.as_str() {
        "vault" => {
            let vault = b.target.clone().unwrap_or_default().to_lowercase();
            if !is_addr(&vault) { return Err(bad("target must be the vault's 0x… address")); }
            if mode == Mode::Paper {
                return Err(bad("vault deposits are real transfers — paper mode only applies to trader sleeves"));
            }
            // Move the money first: a position row that claims a deposit that
            // never landed is worse than no row at all.
            deposit_to_vault(&s, &investor, &vault, b.amount_usd).await?;
            let name = b.name.clone().unwrap_or_else(|| vault_name(&s, &vault).to_string());
            let mut p = new_position(&investor, Kind::Vault, &vault, &name, mode, risk, now);
            fund(&mut p, b.amount_usd, "deposited into the vault", now);
            Ok(Json(json!({"ok": true, "position": s.invest.insert(p)})))
        }
        "trader" => {
            let leader = b.target.clone().unwrap_or_default().to_lowercase();
            if !is_addr(&leader) { return Err(bad("target must be the trader's 0x… address")); }
            if leader == investor { return Err(bad("that's your own wallet — pick a trader to follow")); }
            if mode == Mode::Live {
                check_capacity(&s, &investor, b.amount_usd).await?;
            }
            let name = b.name.clone().unwrap_or_else(|| short(&leader));
            let mut p = new_position(&investor, Kind::Trader, &leader, &name, mode, risk, now);
            fund(&mut p, b.amount_usd, "allocated to this trader", now);
            Ok(Json(json!({"ok": true, "position": s.invest.insert(p)})))
        }
        "strat" => {
            let id = b.index_id.clone().or(b.target.clone()).unwrap_or_default();
            let idx = s.store.get_index(&id).ok_or_else(|| bad("no such strat"))?;
            if idx.legs.is_empty() { return Err(bad("this strat has no traders in it")); }
            if mode == Mode::Live {
                check_capacity(&s, &investor, b.amount_usd).await?;
            }
            let group_id = uuid::Uuid::new_v4().to_string();
            let mut made = Vec::new();
            for leg in &idx.legs {
                let slice = b.amount_usd * leg.weight;
                if slice <= 0.0 { continue; }
                let leader = leg.address.to_lowercase();
                if leader == investor { continue; }
                let mut p = new_position(&investor, Kind::Trader, &leader, &short(&leader),
                                         mode, risk.clone(), now);
                p.group_id = Some(group_id.clone());
                p.group_name = Some(idx.name.clone());
                p.group_weight = leg.weight;
                fund(&mut p, slice, &format!("{}% of the {} basket", (leg.weight * 100.0).round(), idx.name), now);
                made.push(s.invest.insert(p));
            }
            if made.is_empty() { return Err(bad("nothing to invest — every leg of this strat is your own wallet")); }
            Ok(Json(json!({
                "ok": true, "group_id": group_id, "positions": made,
            })))
        }
        other => Err(bad(format!("unknown kind '{other}' — use vault, trader or strat"))),
    }
}

fn new_position(
    investor: &str, kind: Kind, target: &str, name: &str,
    mode: Mode, risk: Risk, now: i64,
) -> Position {
    Position {
        id: uuid::Uuid::new_v4().to_string(),
        investor: investor.to_string(),
        kind,
        target: target.to_string(),
        name: name.to_string(),
        status: Status::Active,
        mode,
        group_id: None, group_name: None, group_weight: 0.0,
        contributed_usd: 0.0, withdrawn_usd: 0.0,
        risk,
        sleeve: Default::default(),
        flows: Vec::new(), events: Vec::new(),
        created_ms: now, updated_ms: now, last_sync_ms: 0,
        last_error: None, next_attempt_ms: 0, fail_streak: 0,
    }
}

fn fund(p: &mut Position, amount: f64, note: &str, now: i64) {
    p.contributed_usd += amount;
    p.add_flow("in", amount, note, now);
    p.log("funded", format!("{} added: ${:.2}", if p.kind == Kind::Vault { "deposit" } else { "allocation" }, amount), now);
}

fn short(a: &str) -> String {
    if a.len() >= 10 { format!("{}…{}", &a[..6], &a[a.len() - 4..]) } else { a.to_string() }
}

fn vault_name(_s: &AppState, vault: &str) -> String { short(vault) }

/// Reject an allocation the account can't actually back.
async fn check_capacity(s: &AppState, investor: &str, amount: f64) -> Result<(), (StatusCode, Json<Value>)> {
    let cap = capacity(s, investor).await;
    let free = cap.get("free").and_then(|v| v.as_f64()).unwrap_or(0.0);
    let account = cap.get("account_value").and_then(|v| v.as_f64()).unwrap_or(0.0);
    if account <= 0.0 {
        return Err(bad("your Hyperliquid account is empty — deposit USDC on the Wallet page first, or invest in paper mode to try it risk-free"));
    }
    if amount > free + 1.0 {
        return Err(bad(format!(
            "that's more than this account can back: ${:.2} free of ${:.2} (the rest is already allocated to other positions)",
            free, account)));
    }
    Ok(())
}

/// Real USDC into a Hyperliquid vault, signed by the investor's agent key.
async fn deposit_to_vault(s: &AppState, investor: &str, vault: &str, amount: f64)
    -> Result<Value, (StatusCode, Json<Value>)>
{
    vault_move(s, investor, vault, true, amount).await
}

async fn vault_move(s: &AppState, investor: &str, vault: &str, is_deposit: bool, amount: f64)
    -> Result<Value, (StatusCode, Json<Value>)>
{
    let usd_micro = (amount * 1_000_000.0).round() as u64;
    if usd_micro == 0 { return Err(bad("amount rounds to zero")); }
    let action = actions::build_vault_transfer_action(vault, is_deposit, usd_micro);
    let nonce = chrono::Utc::now().timestamp_millis() as u64;
    let res = actions::post_l1_action(&s.http, &s.hl, &s.signer, investor, action, nonce, None)
        .await
        .map_err(|e| bad(format!("Hyperliquid rejected the transfer: {e}")))?;
    if res.get("status").and_then(|x| x.as_str()) == Some("err") {
        let msg = res.get("response").and_then(|r| r.as_str()).unwrap_or("transfer failed");
        return Err(bad(msg.to_string()));
    }
    s.hl.cache_evict_prefix(&format!("vaultDetails:{}", vault.to_lowercase()));
    Ok(res)
}

// ─── POST /invest/:id/{add,withdraw,pause,resume,close} ─────────────────

#[derive(Deserialize)]
struct AmountBody {
    #[serde(default)] investor: Option<String>,
    #[serde(default)] amount_usd: Option<f64>,
    #[serde(default)] all: bool,
}

async fn add(
    State(s): State<AppState>,
    Path(id): Path<String>,
    ext: Option<Extension<crate::auth::AuthedUser>>,
    Json(b): Json<AmountBody>,
) -> ApiResult {
    let caller = who(&ext, b.investor.as_deref());
    let p = owned(&s, &id, &caller)?;
    let amount = b.amount_usd.unwrap_or(0.0);
    if !(amount > 0.0) { return Err(bad("enter an amount above zero")); }
    if p.status == Status::Closed { return Err(bad("this position is closed — start a new one")); }

    if p.kind == Kind::Vault {
        vault_move(&s, &p.investor, &p.target, true, amount).await?;
    } else if p.mode == Mode::Live {
        check_capacity(&s, &p.investor, amount).await?;
    }
    let now = chrono::Utc::now().timestamp_millis();
    let updated = s.invest.update(&id, |p| {
        fund(p, amount, "added", now);
        // A top-up is new information — retry immediately rather than
        // sitting out the backoff from an earlier failure.
        p.next_attempt_ms = 0;
        p.clone()
    }).ok_or_else(not_found)?;
    Ok(Json(json!({"ok": true, "position": updated})))
}

async fn withdraw(
    State(s): State<AppState>,
    Path(id): Path<String>,
    ext: Option<Extension<crate::auth::AuthedUser>>,
    Json(b): Json<AmountBody>,
) -> ApiResult {
    let caller = who(&ext, b.investor.as_deref());
    let p = owned(&s, &id, &caller)?;
    let now = chrono::Utc::now().timestamp_millis();

    if p.kind == Kind::Vault {
        // What HL will actually let out right now (lockups are real).
        let d = s.hl.vault_details(&p.target, Some(&p.investor)).await.unwrap_or(Value::Null);
        let max = d.get("maxWithdrawable").and_then(|x| x.as_str())
            .and_then(|t| t.parse::<f64>().ok()).unwrap_or(0.0);
        let amount = if b.all { max } else { b.amount_usd.unwrap_or(0.0) };
        if !(amount > 0.0) { return Err(bad("enter an amount above zero")); }
        if amount > max + 1e-9 {
            return Err(bad(format!("Hyperliquid will only release ${max:.2} right now (the rest is inside the vault's lockup)")));
        }
        vault_move(&s, &p.investor, &p.target, false, amount).await?;
        let updated = s.invest.update(&id, |p| {
            p.withdrawn_usd += amount;
            p.add_flow("out", amount, "withdrawn from the vault", now);
            p.log("withdraw", format!("withdrew ${amount:.2} back to your Hyperliquid balance"), now);
            if b.all { p.status = Status::Closed; }
            p.clone()
        }).ok_or_else(not_found)?;
        return Ok(Json(json!({"ok": true, "position": updated})));
    }

    // Trader sleeve: the money never left the account, so "withdraw" means
    // *release* — shrink the basis and let the reconciler size down to match.
    let available = p.basis();
    let amount = if b.all { available } else { b.amount_usd.unwrap_or(0.0) };
    if !(amount > 0.0) { return Err(bad("enter an amount above zero")); }
    if amount > available + 1e-9 {
        return Err(bad(format!("this position is only sized against ${available:.2}")));
    }
    let closing_all = b.all || amount >= available - 1e-9;
    let updated = s.invest.update(&id, |p| {
        p.withdrawn_usd += amount;
        p.add_flow("out", amount, "released back to your balance", now);
        if closing_all {
            p.status = Status::Closing;
            p.log("closing", "releasing everything — the engine is closing this sleeve's positions now", now);
        } else {
            p.log("withdraw", format!(
                "released ${amount:.2} — positions shrink to match on the next pass"), now);
        }
        p.next_attempt_ms = 0;
        p.clone()
    }).ok_or_else(not_found)?;
    Ok(Json(json!({"ok": true, "position": updated})))
}

async fn pause(
    State(s): State<AppState>, Path(id): Path<String>,
    ext: Option<Extension<crate::auth::AuthedUser>>,
    body: Option<Json<AmountBody>>,
) -> ApiResult {
    let caller = who(&ext, body.as_ref().and_then(|b| b.investor.as_deref()));
    owned(&s, &id, &caller)?;
    let now = chrono::Utc::now().timestamp_millis();
    let updated = s.invest.update(&id, |p| {
        p.status = Status::Paused;
        p.log("paused", "paused — your positions stay as they are, nothing new is opened", now);
        p.clone()
    }).ok_or_else(not_found)?;
    Ok(Json(json!({"ok": true, "position": updated})))
}

async fn resume(
    State(s): State<AppState>, Path(id): Path<String>,
    ext: Option<Extension<crate::auth::AuthedUser>>,
    body: Option<Json<AmountBody>>,
) -> ApiResult {
    let caller = who(&ext, body.as_ref().and_then(|b| b.investor.as_deref()));
    let p = owned(&s, &id, &caller)?;
    if p.status == Status::Closed { return Err(bad("this position is closed — start a new one")); }
    let now = chrono::Utc::now().timestamp_millis();
    let updated = s.invest.update(&id, |p| {
        p.status = Status::Active;
        p.next_attempt_ms = 0;
        p.fail_streak = 0;
        p.log("resumed", "tracking again — the next pass re-aligns with the trader", now);
        p.clone()
    }).ok_or_else(not_found)?;
    Ok(Json(json!({"ok": true, "position": updated})))
}

async fn close(
    State(s): State<AppState>, Path(id): Path<String>,
    ext: Option<Extension<crate::auth::AuthedUser>>,
    body: Option<Json<AmountBody>>,
) -> ApiResult {
    let caller = who(&ext, body.as_ref().and_then(|b| b.investor.as_deref()));
    let p = owned(&s, &id, &caller)?;
    let now = chrono::Utc::now().timestamp_millis();

    if p.kind == Kind::Vault {
        let d = s.hl.vault_details(&p.target, Some(&p.investor)).await.unwrap_or(Value::Null);
        let max = d.get("maxWithdrawable").and_then(|x| x.as_str())
            .and_then(|t| t.parse::<f64>().ok()).unwrap_or(0.0);
        if max > 0.0 {
            vault_move(&s, &p.investor, &p.target, false, max).await?;
        }
        let updated = s.invest.update(&id, |p| {
            if max > 0.0 {
                p.withdrawn_usd += max;
                p.add_flow("out", max, "closed out of the vault", now);
            }
            p.status = Status::Closed;
            p.log("closed", format!("closed — ${max:.2} returned to your Hyperliquid balance"), now);
            p.clone()
        }).ok_or_else(not_found)?;
        return Ok(Json(json!({"ok": true, "position": updated})));
    }

    let updated = s.invest.update(&id, |p| {
        let left = p.basis();
        p.withdrawn_usd += left;
        p.status = Status::Closing;
        p.next_attempt_ms = 0;
        p.add_flow("out", left, "closed", now);
        p.log("closing", "closing — the engine is flattening every position this sleeve holds", now);
        p.clone()
    }).ok_or_else(not_found)?;
    Ok(Json(json!({"ok": true, "position": updated})))
}

// ─── PATCH /invest/:id — change the dials ───────────────────────────────

#[derive(Deserialize)]
struct PatchBody {
    #[serde(default)] investor: Option<String>,
    #[serde(default)] name: Option<String>,
    #[serde(default)] mode: Option<String>,
    #[serde(default)] risk: Option<Risk>,
}

async fn patch(
    State(s): State<AppState>,
    Path(id): Path<String>,
    ext: Option<Extension<crate::auth::AuthedUser>>,
    Json(b): Json<PatchBody>,
) -> ApiResult {
    let caller = who(&ext, b.investor.as_deref());
    let p = owned(&s, &id, &caller)?;
    let now = chrono::Utc::now().timestamp_millis();

    // Switching a funded sleeve between paper and live would make its ledger
    // a mix of imaginary and real fills. Close it and open a new one instead.
    if let Some(m) = &b.mode {
        let want = if m == "paper" { Mode::Paper } else { Mode::Live };
        if want != p.mode && !p.sleeve.legs.is_empty() {
            return Err(bad("close this position before switching between paper and live — a half-simulated ledger can't be trusted"));
        }
    }

    let updated = s.invest.update(&id, |p| {
        if let Some(n) = &b.name { p.name = n.clone(); }
        if let Some(m) = &b.mode { p.mode = if m == "paper" { Mode::Paper } else { Mode::Live }; }
        if let Some(r) = b.risk.clone() {
            p.risk = r.sanitized();
            p.log("risk", "risk settings updated — targets re-size on the next pass", now);
        }
        p.next_attempt_ms = 0;
        p.clone()
    }).ok_or_else(not_found)?;
    Ok(Json(json!({"ok": true, "position": updated})))
}

// ─── DELETE /invest/:id — forget a closed position ──────────────────────

#[derive(Deserialize)]
struct DeleteQ { #[serde(default)] investor: Option<String> }

async fn remove(
    State(s): State<AppState>,
    Path(id): Path<String>,
    Query(q): Query<DeleteQ>,
    ext: Option<Extension<crate::auth::AuthedUser>>,
) -> ApiResult {
    let caller = who(&ext, q.investor.as_deref());
    let p = owned(&s, &id, &caller)?;
    if p.status != Status::Closed {
        return Err(bad("close this position first — deleting the record wouldn't close the trades"));
    }
    Ok(Json(json!({"ok": s.invest.delete(&id)})))
}
