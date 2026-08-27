//! HTTP over the engine.
//!
//! This layer parses, calls one engine method, and serialises the answer. It
//! holds no rules of its own — if a check looks like it belongs here, it
//! belongs in `engine.rs` instead, because the tests and the CLI reach the
//! engine without passing through here.
//!
//! One endpoint is deliberately missing: there is nothing that turns a model
//! and a salt into a commitment. Doing that on the server would hand it the
//! very choice the commitment exists to hide. The console hashes in the
//! browser and `mod.py` hashes on your machine.

use axum::extract::{Path, Query, State as AxumState};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use serde::Deserialize;
use serde_json::{json, Value};
use tower_http::cors::{Any, CorsLayer};

use crate::engine::{round_view, Caller, EngineError};
use crate::types::{Money, Outcome, UsageReceipt, MICRO};
use crate::{now, App};

pub fn router(app: App) -> Router {
    Router::new()
        .route("/", get(info))
        .route("/health", get(health))
        .route("/status", get(status))
        .route("/rounds", get(rounds))
        .route("/rounds/:id", get(round_one))
        .route("/round", get(current_round))
        .route("/round/force", post(force_round))
        .route("/commit", post(commit))
        .route("/reveal", post(reveal))
        .route("/transfer", post(transfer))
        .route("/attest", post(attest))
        .route("/usage", post(usage))
        .route("/account/:addr", get(account))
        .route("/models", get(models))
        .route("/leaderboard", get(leaderboard))
        .route("/verify", get(verify))
        .route("/proof/:round/:commitment", get(proof))
        .route("/chain", get(chain))
        .route("/tick", post(tick))
        .route("/owner", post(set_owner))
        .route("/roster", post(set_roster))
        .route("/attestors", post(add_attestor))
        .route("/meters", post(add_meter))
        .route("/credits/grant", post(grant))
        .route("/dev/sign", post(dev_sign))
        .layer(
            CorsLayer::new()
                .allow_origin(Any)
                .allow_methods(Any)
                .allow_headers(Any),
        )
        .with_state(app)
}

// ── error plumbing ───────────────────────────────────────────────────

struct ApiError(StatusCode, String);

impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        (self.0, Json(json!({ "error": self.1 }))).into_response()
    }
}

impl From<EngineError> for ApiError {
    fn from(e: EngineError) -> Self {
        let code = match &e {
            EngineError::Denied(_) => StatusCode::FORBIDDEN,
            EngineError::Invalid(_) => StatusCode::BAD_REQUEST,
            EngineError::NotFound(_) => StatusCode::NOT_FOUND,
            EngineError::Conflict(_) => StatusCode::CONFLICT,
            EngineError::Io(_) => StatusCode::INTERNAL_SERVER_ERROR,
        };
        ApiError(code, e.to_string())
    }
}

type ApiResult = std::result::Result<Json<Value>, ApiError>;

// ── reads ────────────────────────────────────────────────────────────

async fn info(AxumState(app): AxumState<App>) -> Json<Value> {
    let engine = app.engine.lock();
    let state = engine.state();
    Json(json!({
        "name": "prerank",
        "what": "A daily prediction market over which model ranks first, where the \
                 house's margin on early usage is handed back as a position in the \
                 model you used.",
        "chain_id": state.chain_id,
        "owner": state.owner,
        "head": engine.head(),
        "events": engine.chain().len(),
        "open_mode": engine.open_mode,
        "schedule": engine.schedule,
        "params": engine.params,
        "micro": MICRO,
        "rounds": state.rounds.len(),
        "roster": state.roster,
        "endpoints": {
            "round": "GET /round | GET /rounds | GET /rounds/:id",
            "bet": "POST /commit {address,signature,round,commitment,amount,nonce}",
            "reveal": "POST /reveal {round,commitment,model,salt}",
            "token": "POST /transfer {address,signature,round,model,to,units,nonce}",
            "grade": "POST /attest {address,signature,round,ranking[]}",
            "usage": "POST /usage {id,user,model,spend,cost,at,meter,signature}",
            "account": "GET /account/:addr",
            "models": "GET /models | GET /leaderboard",
            "audit": "GET /verify | GET /chain | GET /proof/:round/:commitment",
            "owner": "POST /owner | /roster | /attestors | /meters | /credits/grant | /round/force",
        },
        "cheat_proofing": [
            "bets are sealed: the amount is public and locked, the model is a hash until the reveal window",
            "a commitment that is never opened forfeits its stake to the pool",
            "the log is hash-linked and the state is its fold — GET /verify replays it from genesis",
            "each sealed round publishes a Merkle root over its commitments; GET /proof gives inclusion",
            "the field and every payout parameter are hashed into spec_hash when the round opens",
            "a rank needs a quorum of registered graders to agree; two answers or none voids the round",
            "a grader holding a position in the round it grades is recorded and not counted",
            "edge credit comes from the house's margin on real metered spend, lands a round later, and is capped",
        ],
    }))
}

async fn health(AxumState(app): AxumState<App>) -> Json<Value> {
    let engine = app.engine.lock();
    let v = engine.verify();
    Json(json!({
        "ok": v.ok,
        "head": engine.head(),
        "events": v.events,
        "rounds": v.rounds,
        "open_mode": engine.open_mode,
        "uptime": now() - app.started_at,
        "problems": v.problems,
    }))
}

async fn status(AxumState(app): AxumState<App>) -> Json<Value> {
    let mut engine = app.engine.lock();
    let t = now();
    let _ = engine.tick(t);
    let state = engine.state();
    let current = state.open_round(t).or_else(|| state.latest_round());
    Json(json!({
        "now": t,
        "chain_id": state.chain_id,
        "head": engine.head(),
        "events": engine.chain().len(),
        "owner": state.owner,
        "attestors": state.attestors,
        "meters": state.meters,
        "roster": state.roster,
        "treasury": state.treasury,
        "issued": state.issued,
        "accounts": state.balances.len(),
        "rounds": state.rounds.len(),
        "pending_edge": state.pending_edge.len(),
        "current": current.map(|r| round_view(r, t)),
    }))
}

#[derive(Deserialize)]
struct Paging {
    #[serde(default)]
    from: u64,
    #[serde(default)]
    limit: Option<u64>,
}

async fn rounds(AxumState(app): AxumState<App>) -> Json<Value> {
    let mut engine = app.engine.lock();
    let t = now();
    let _ = engine.tick(t);
    let state = engine.state();
    let list: Vec<Value> = state.rounds.values().rev().map(|r| round_view(r, t)).collect();
    Json(json!({ "rounds": list, "now": t }))
}

async fn round_one(AxumState(app): AxumState<App>, Path(id): Path<String>) -> ApiResult {
    let mut engine = app.engine.lock();
    let t = now();
    // Tick on the way in, like every other read. Without it a round whose
    // seal time has passed reports itself sealed — the phase is a pure
    // function of the clock — while the seal event that publishes its Merkle
    // root has not been written yet.
    let _ = engine.tick(t);
    let round = engine
        .state()
        .round(&id)
        .ok_or_else(|| ApiError(StatusCode::NOT_FOUND, format!("no round {id}")))?;
    Ok(Json(round_view(round, t)))
}

async fn current_round(AxumState(app): AxumState<App>) -> ApiResult {
    let mut engine = app.engine.lock();
    let t = now();
    let _ = engine.tick(t);
    let state = engine.state();
    let round = state
        .open_round(t)
        .or_else(|| state.latest_round())
        .ok_or_else(|| {
            ApiError(
                StatusCode::NOT_FOUND,
                "no round is open — the field needs at least two models (POST /roster)".into(),
            )
        })?;
    Ok(Json(round_view(round, t)))
}

async fn account(AxumState(app): AxumState<App>, Path(addr): Path<String>) -> Json<Value> {
    let engine = app.engine.lock();
    let state = engine.state();
    let addr = crate::crypto::norm_addr(&addr);
    let positions: Vec<Value> = state
        .positions_of(&addr)
        .into_iter()
        .map(|(round, model, units)| json!({ "round": round, "model": model, "units": units }))
        .collect();
    let pending: Vec<Value> = state
        .pending_edge
        .iter()
        .filter(|p| p.user == addr)
        .map(|p| json!({
            "receipt": p.receipt_id, "model": p.model, "margin": p.margin,
            "units": p.units, "weight": format!("{}/{}", p.weight_num, p.weight_den),
            "earned_at": p.earned_at, "rounds_waited": p.rounds_waited,
        }))
        .collect();
    let receipts: Vec<Value> = state
        .receipts
        .values()
        .filter(|r| r.user == addr)
        .map(|r| json!({
            "id": r.id, "model": r.model, "spend": r.spend, "cost": r.cost,
            "margin": r.margin(), "at": r.at,
        }))
        .collect();
    // The next unused nonce, so a client does not have to track one.
    let next_nonce = (0u64..)
        .find(|n| !state.used_nonces.contains(&(addr.clone(), *n)))
        .unwrap_or(0);
    Json(json!({
        "address": addr,
        "balance": state.balance(&addr),
        "locked": state.locked_of(&addr),
        "is_owner": state.is_owner(&addr),
        "is_attestor": state.attestors.contains_key(&addr),
        "is_meter": state.meters.contains_key(&addr),
        "next_nonce": next_nonce,
        "positions": positions,
        "pending_edge": pending,
        "receipts": receipts,
        "micro": MICRO,
    }))
}

async fn models(AxumState(app): AxumState<App>) -> Json<Value> {
    let engine = app.engine.lock();
    let state = engine.state();
    let mut rows: Vec<Value> = Vec::new();
    for (model, credits) in &state.model_credits {
        let margin = *state.model_margin.get(model).unwrap_or(&0);
        // Where the earliness curve stands for this model right now: what a
        // credit of margin spent on the next call would be worth.
        let (units, num, den) =
            crate::market::earliness_units(MICRO, *credits, engine.params.earliness_k);
        rows.push(json!({
            "model": model,
            "credits": credits,
            "margin": margin,
            "in_roster": state.roster.contains(model),
            "edge_weight": format!("{num}/{den}"),
            "units_per_credit_of_margin": units,
        }));
    }
    rows.sort_by(|a, b| {
        b["credits"].as_u64().unwrap_or(0).cmp(&a["credits"].as_u64().unwrap_or(0))
    });
    Json(json!({ "models": rows, "roster": state.roster, "micro": MICRO }))
}

async fn leaderboard(AxumState(app): AxumState<App>) -> Json<Value> {
    let engine = app.engine.lock();
    let state = engine.state();
    let mut wins: std::collections::BTreeMap<String, (u64, u64, Money)> = Default::default();
    let mut settled = 0u64;
    for round in state.rounds.values() {
        let Some(result) = &round.result else { continue };
        if result.outcome == Outcome::Void {
            continue;
        }
        settled += 1;
        for model in &round.entrants {
            wins.entry(model.clone()).or_insert((0, 0, 0)).1 += 1;
        }
        if let Some(w) = &result.winner {
            let slot = wins.entry(w.clone()).or_insert((0, 0, 0));
            slot.0 += 1;
            slot.2 += result.total_pool;
        }
    }
    let mut rows: Vec<Value> = wins
        .into_iter()
        .map(|(model, (won, entered, pool))| {
            json!({
                "model": model, "wins": won, "rounds": entered,
                "win_rate": if entered > 0 { won as f64 / entered as f64 } else { 0.0 },
                "pool_won": pool,
            })
        })
        .collect();
    rows.sort_by(|a, b| b["wins"].as_u64().unwrap_or(0).cmp(&a["wins"].as_u64().unwrap_or(0)));
    Json(json!({ "leaderboard": rows, "settled_rounds": settled }))
}

async fn verify(AxumState(app): AxumState<App>) -> Json<Value> {
    let engine = app.engine.lock();
    Json(serde_json::to_value(engine.verify()).unwrap_or_default())
}

async fn proof(
    AxumState(app): AxumState<App>,
    Path((round, commitment)): Path<(String, String)>,
) -> ApiResult {
    let mut engine = app.engine.lock();
    let _ = engine.tick(now());
    let p = engine.inclusion_proof(&round, &commitment)?;
    Ok(Json(serde_json::to_value(p).unwrap_or_default()))
}

async fn chain(AxumState(app): AxumState<App>, Query(p): Query<Paging>) -> Json<Value> {
    let engine = app.engine.lock();
    let limit = p.limit.unwrap_or(200).min(2_000) as usize;
    let entries: Vec<Value> = engine
        .chain()
        .entries()
        .iter()
        .skip(p.from as usize)
        .take(limit)
        .map(|e| json!({
            "seq": e.seq, "hash": e.hash, "prev": e.prev,
            "kind": e.event.kind(), "round": e.event.round(), "event": e.event,
        }))
        .collect();
    Json(json!({
        "from": p.from, "count": entries.len(), "length": engine.chain().len(),
        "head": engine.head(), "entries": entries,
    }))
}

// ── writes ───────────────────────────────────────────────────────────

#[derive(Deserialize)]
struct Signed {
    #[serde(default)]
    address: String,
    #[serde(default)]
    signature: String,
}

impl Signed {
    fn caller(&self) -> Caller {
        Caller { address: self.address.clone(), signature: self.signature.clone() }
    }
}

#[derive(Deserialize)]
struct CommitReq {
    #[serde(flatten)]
    who: Signed,
    round: Option<String>,
    commitment: String,
    amount: Money,
    nonce: u64,
}

async fn commit(AxumState(app): AxumState<App>, Json(req): Json<CommitReq>) -> ApiResult {
    let mut engine = app.engine.lock();
    let t = now();
    engine.tick(t).map_err(ApiError::from)?;
    let round = match req.round {
        Some(r) => r,
        None => engine
            .state()
            .open_round(t)
            .map(|r| r.id.clone())
            .ok_or_else(|| ApiError(StatusCode::CONFLICT, "no round is taking bets".into()))?,
    };
    let commitment = engine.commit(&req.who.caller(), &round, &req.commitment, req.amount, req.nonce, t)?;
    let state = engine.state();
    Ok(Json(json!({
        "ok": true, "round": round, "commitment": commitment,
        "amount": req.amount, "locked": state.locked_of(&crate::crypto::norm_addr(&req.who.address)),
        "balance": state.balance(&crate::crypto::norm_addr(&req.who.address)),
        "reveal_from": state.round(&round).map(|r| r.reveal_at),
        "reveal_until": state.round(&round).map(|r| r.seal_at),
        "note": "keep the salt — an unopened bet forfeits its stake when the round seals",
    })))
}

#[derive(Deserialize)]
struct RevealReq {
    round: String,
    commitment: String,
    model: String,
    salt: String,
}

async fn reveal(AxumState(app): AxumState<App>, Json(req): Json<RevealReq>) -> ApiResult {
    let mut engine = app.engine.lock();
    let t = now();
    engine.tick(t).map_err(ApiError::from)?;
    let (owner, amount) = engine.reveal(&req.round, &req.commitment, &req.model, &req.salt, t)?;
    let book = engine.state().book_of(&req.round, &req.model);
    Ok(Json(json!({
        "ok": true, "round": req.round, "model": req.model, "owner": owner,
        "amount": amount, "units": amount, "model_units": book.units,
    })))
}

#[derive(Deserialize)]
struct TransferReq {
    #[serde(flatten)]
    who: Signed,
    round: String,
    model: String,
    to: String,
    units: Money,
    nonce: u64,
}

async fn transfer(AxumState(app): AxumState<App>, Json(req): Json<TransferReq>) -> ApiResult {
    let mut engine = app.engine.lock();
    let t = now();
    engine.tick(t).map_err(ApiError::from)?;
    let remaining = engine.transfer(
        &req.who.caller(), &req.round, &req.model, &req.to, req.units, req.nonce, t,
    )?;
    Ok(Json(json!({ "ok": true, "remaining": remaining })))
}

#[derive(Deserialize)]
struct AttestReq {
    #[serde(flatten)]
    who: Signed,
    round: String,
    ranking: Vec<String>,
}

async fn attest(AxumState(app): AxumState<App>, Json(req): Json<AttestReq>) -> ApiResult {
    let mut engine = app.engine.lock();
    let t = now();
    engine.tick(t).map_err(ApiError::from)?;
    let counted = engine.attest(&req.who.caller(), &req.round, req.ranking.clone(), t)?;
    let round = engine.state().round(&req.round).cloned();
    Ok(Json(json!({
        "ok": true, "counted": counted,
        "rank_hash": crate::types::rank_hash(&req.round, &req.ranking),
        "attestations": round.as_ref().map(|r| r.attestations.len()).unwrap_or(0),
        "quorum": round.as_ref().map(|r| r.params.quorum).unwrap_or(0),
        "settles_at": round.map(|r| r.settle_at),
        "note": if counted { Value::Null } else {
            json!("recorded but not counted — you hold a position in this round")
        },
    })))
}

async fn usage(AxumState(app): AxumState<App>, Json(receipt): Json<UsageReceipt>) -> ApiResult {
    let mut engine = app.engine.lock();
    let t = now();
    let user = crate::crypto::norm_addr(&receipt.user);
    let model = receipt.model.clone();
    let (margin, units) = engine.post_usage(receipt, t)?;
    Ok(Json(json!({
        "ok": true, "user": user, "model": model, "margin": margin,
        "edge_units": units,
        "note": "the position lands when the next round with this model in its field opens",
    })))
}

#[derive(Deserialize)]
struct GrantReq {
    #[serde(flatten)]
    who: Signed,
    account: String,
    amount: Money,
    #[serde(default)]
    memo: String,
}

async fn grant(AxumState(app): AxumState<App>, Json(req): Json<GrantReq>) -> ApiResult {
    let mut engine = app.engine.lock();
    let balance = engine.credit(&req.who.caller(), &req.account, req.amount, &req.memo, now())?;
    Ok(Json(json!({ "ok": true, "account": req.account, "balance": balance })))
}

#[derive(Deserialize)]
struct OwnerReq {
    #[serde(flatten)]
    who: Signed,
    #[serde(default)]
    owner: Option<String>,
}

async fn set_owner(AxumState(app): AxumState<App>, Json(req): Json<OwnerReq>) -> ApiResult {
    let mut engine = app.engine.lock();
    let target = req.owner.clone().unwrap_or_else(|| req.who.address.clone());
    let owner = engine.set_owner(&req.who.caller(), &target, now())?;
    Ok(Json(json!({ "ok": true, "owner": owner })))
}

#[derive(Deserialize)]
struct RosterReq {
    #[serde(flatten)]
    who: Signed,
    models: Vec<String>,
}

async fn set_roster(AxumState(app): AxumState<App>, Json(req): Json<RosterReq>) -> ApiResult {
    let mut engine = app.engine.lock();
    let models = engine.set_roster(&req.who.caller(), req.models, now())?;
    Ok(Json(json!({ "ok": true, "roster": models })))
}

/// The target is `target`, not `address` — `address` is the caller, and a
/// flattened duplicate of that key would have silently made every role grant
/// a self-grant.
#[derive(Deserialize)]
struct RoleReq {
    #[serde(flatten)]
    who: Signed,
    target: String,
    #[serde(default)]
    label: String,
    #[serde(default)]
    remove: bool,
}

async fn add_attestor(AxumState(app): AxumState<App>, Json(req): Json<RoleReq>) -> ApiResult {
    let mut engine = app.engine.lock();
    let t = now();
    if req.remove {
        engine.remove_attestor(&req.who.caller(), &req.target, t)?;
        return Ok(Json(json!({ "ok": true, "removed": req.target })));
    }
    let who = engine.register_attestor(&req.who.caller(), &req.target, &req.label, t)?;
    Ok(Json(json!({ "ok": true, "attestor": who })))
}

async fn add_meter(AxumState(app): AxumState<App>, Json(req): Json<RoleReq>) -> ApiResult {
    let mut engine = app.engine.lock();
    let t = now();
    if req.remove {
        engine.remove_meter(&req.who.caller(), &req.target, t)?;
        return Ok(Json(json!({ "ok": true, "removed": req.target })));
    }
    let who = engine.register_meter(&req.who.caller(), &req.target, &req.label, t)?;
    Ok(Json(json!({ "ok": true, "meter": who })))
}

#[derive(Deserialize)]
struct ForceReq {
    #[serde(flatten)]
    who: Signed,
    #[serde(default)]
    entrants: Option<Vec<String>>,
}

async fn force_round(AxumState(app): AxumState<App>, Json(req): Json<ForceReq>) -> ApiResult {
    let mut engine = app.engine.lock();
    let id = engine.force_round(&req.who.caller(), req.entrants, now())?;
    Ok(Json(json!({ "ok": true, "round": id })))
}

async fn tick(AxumState(app): AxumState<App>) -> ApiResult {
    let mut engine = app.engine.lock();
    let did = engine.tick(now()).map_err(ApiError::from)?;
    Ok(Json(json!({ "ok": true, "did": did, "head": engine.head() })))
}

#[derive(Deserialize)]
struct SignReq {
    wallet: u64,
    message: String,
}

/// Sign a message with one of the deterministic development wallets.
///
/// Only reachable in open mode, and open mode is on the health card. It
/// exists so the console and the test suite can drive a signed market
/// without a browser extension; the keys are derived from a published
/// string and are worth nothing.
async fn dev_sign(AxumState(app): AxumState<App>, Json(req): Json<SignReq>) -> ApiResult {
    let engine = app.engine.lock();
    if !engine.open_mode {
        return Err(ApiError(StatusCode::FORBIDDEN, "dev signing is off".into()));
    }
    let (address, signature) = crate::testkit::sign(req.wallet, &req.message);
    Ok(Json(json!({ "address": address, "signature": signature, "message": req.message })))
}
