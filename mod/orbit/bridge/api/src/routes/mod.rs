use axum::{
    extract::{ConnectInfo, Path, Query, State},
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use serde::Deserialize;
use serde_json::json;
use sha2::{Digest, Sha256};
use std::{
    net::{IpAddr, SocketAddr},
    time::SystemTime,
};

use crate::{
    crypto,
    state::AppState,
    store::{Claim, Commitment},
    validate,
};

pub fn router() -> Router<AppState> {
    Router::new()
        .route("/", get(health))
        .route("/health", get(health))
        .route("/status", get(status).post(status))
        .route("/owner", get(owner))
        .route("/contract_info", get(contract_info))
        .route("/audit", get(audit))
        // Snapshot
        .route("/in_snapshot/:address", get(in_snapshot))
        .route("/balances", get(balances).post(balances_post))
        .route("/get_total_balances", post(get_total_balances))
        // Claims
        .route("/claim", post(claim))
        .route("/has_claimed/:address", get(has_claimed))
        .route("/unclaimed/:address", get(unclaimed))
        .route("/claims", get(claims))
        .route("/get_claims", post(claims))
        .route("/claims_array", get(claims_array))
        .route("/delete_claim", post(delete_claim))
        .route("/reset", post(reset))
        // Commitments
        .route("/commit", post(commit))
        .route("/update_commitment", post(update_commitment))
        .route("/commitments", get(commitments))
        .route("/get_commitments", post(commitments))
        .route("/commitment/:source_address", get(commitment))
}

// ── Helpers ─────────────────────────────────────────────────────

fn now_ts() -> i64 {
    SystemTime::now()
        .duration_since(SystemTime::UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0)
}

fn err(status: StatusCode, msg: impl Into<String>) -> Response {
    (status, Json(json!({ "error": msg.into() }))).into_response()
}

/// Resolve the real client IP for rate-limiting purposes. When the connection
/// itself comes from loopback we trust X-Forwarded-For (set by the local Caddy
/// reverse proxy); otherwise we use the connecting address. This prevents
/// external clients from spoofing X-Forwarded-For while still letting Caddy
/// pass the real upstream IP.
fn client_ip(headers: &HeaderMap, conn: SocketAddr) -> IpAddr {
    let conn_ip = conn.ip();
    let from_loopback = match conn_ip {
        IpAddr::V4(a) => a.is_loopback(),
        IpAddr::V6(a) => a.is_loopback(),
    };
    if !from_loopback {
        return conn_ip;
    }
    if let Some(h) = headers.get("x-forwarded-for") {
        if let Ok(s) = h.to_str() {
            if let Some(first) = s.split(',').next() {
                if let Ok(ip) = first.trim().parse::<IpAddr>() {
                    return ip;
                }
            }
        }
    }
    conn_ip
}

fn check_rate(state: &AppState, headers: &HeaderMap, conn: SocketAddr) -> Result<(), Response> {
    let ip = client_ip(headers, conn);
    if !state.rate_limiter.check(ip) {
        return Err(err(
            StatusCode::TOO_MANY_REQUESTS,
            "Rate limit exceeded. Max 30 requests per minute.",
        ));
    }
    Ok(())
}

// ── Health / Status ─────────────────────────────────────────────

async fn health(State(state): State<AppState>) -> Json<serde_json::Value> {
    Json(json!({
        "status": "ok",
        "module": "bridge",
        "snapshot_addresses": state.store.snapshot_len(),
        "claims": state.store.claims_count(),
        "admin_key_configured": state.config.admin_key.is_some(),
    }))
}

async fn status(State(state): State<AppState>) -> Json<serde_json::Value> {
    let total_owed: f64 = state.store.snapshot_total();
    let total_claimed: f64 = state.store.claims_total();
    let total_committed: f64 = state.store.committed_balance_total();
    let commitment_count = state.store.commitments_count();
    Json(json!({
        "total_addresses": state.store.snapshot_len(),
        "total_owed": total_owed,
        "total_claimed": total_claimed,
        "total_committed": total_committed,
        "total_unclaimed": total_owed - total_claimed - total_committed,
        "claim_count": state.store.claims_count(),
        "commitment_count": commitment_count,
    }))
}

async fn owner(State(_state): State<AppState>) -> Json<serde_json::Value> {
    // owner is config-driven; for the Rust API we don't need it as a hard dep.
    // Kept as an empty string so the front-end shape stays compatible.
    Json(json!({ "owner": "" }))
}

async fn contract_info(State(_state): State<AppState>) -> Json<serde_json::Value> {
    Json(json!({
        "network": "testnet",
        "abi_stored": false,
        "note": "contract_info served by orbit module — Rust API is read-only for chain metadata"
    }))
}

async fn audit(State(state): State<AppState>) -> Json<serde_json::Value> {
    // Public auditability surface — anyone can re-compute SHA-256 of
    // snapshot/total_balances.json on disk and verify it matches `cid`.
    Json(json!({
        "snapshot": {
            "cid": state.snapshot_audit.cid,
            "updated_at": state.snapshot_audit.updated_at,
            "bytes": state.snapshot_audit.bytes,
            "addresses": state.store.snapshot_len(),
            "algo": "sha256",
        },
    }))
}

// ── Snapshot ────────────────────────────────────────────────────

async fn in_snapshot(
    State(state): State<AppState>,
    Path(address): Path<String>,
) -> Result<Json<serde_json::Value>, Response> {
    let addr = validate::check_address(&address).map_err(|e| err(StatusCode::BAD_REQUEST, e))?;
    Ok(Json(json!({
        "address": addr,
        "in_snapshot": state.store.snapshot_contains(addr),
        "balance": state.store.balance(addr),
    })))
}

#[derive(Debug, Deserialize, Default)]
pub struct PageParams {
    #[serde(default)]
    pub page: usize,
    #[serde(default)]
    pub limit: Option<usize>,
    #[serde(default)]
    pub q: Option<String>,
    #[serde(default)]
    pub sort_by: Option<String>,
    #[serde(default)]
    pub dir: Option<String>,
    #[serde(default)]
    pub claim_filter: Option<String>,
}

async fn balances(
    ConnectInfo(conn): ConnectInfo<SocketAddr>,
    headers: HeaderMap,
    State(state): State<AppState>,
    Query(p): Query<PageParams>,
) -> Result<Json<serde_json::Value>, Response> {
    check_rate(&state, &headers, conn)?;
    let limit = p.limit.unwrap_or(50).min(2000).max(1);
    let sort_by = p.sort_by.as_deref().unwrap_or("address");
    let dir = p.dir.as_deref().unwrap_or("asc");
    let claim_filter = p.claim_filter.as_deref().unwrap_or("all");
    let (entries, total) = state.store.snapshot_filtered_page(
        p.page,
        limit,
        p.q.as_deref(),
        sort_by,
        dir,
        claim_filter,
    );
    // Preserve the existing { balances: { addr: bal } } shape while also
    // returning a richer ordered list so the client can render directly
    // without re-sorting.
    let balances: serde_json::Map<String, serde_json::Value> = entries
        .iter()
        .map(|(k, v, _)| (k.clone(), json!(v)))
        .collect();
    let entries_array: Vec<serde_json::Value> = entries
        .iter()
        .map(|(addr, bal, claimed)| {
            json!({ "address": addr, "balance": bal, "claimed": claimed })
        })
        .collect();
    Ok(Json(json!({
        "balances": balances,
        "entries": entries_array,
        "total": total,
        "page": p.page,
        "limit": limit,
        "q": p.q.unwrap_or_default(),
        "sort_by": sort_by,
        "dir": dir,
        "claim_filter": claim_filter,
    })))
}

async fn balances_post(
    conn: ConnectInfo<SocketAddr>,
    headers: HeaderMap,
    state: State<AppState>,
    Json(p): Json<PageParams>,
) -> Result<Json<serde_json::Value>, Response> {
    balances(conn, headers, state, Query(p)).await
}

async fn get_total_balances(
    ConnectInfo(conn): ConnectInfo<SocketAddr>,
    headers: HeaderMap,
    State(state): State<AppState>,
) -> Result<Json<serde_json::Value>, Response> {
    // Compatibility shim for old front-end callers. Rate-limited to prevent
    // DoS — a single response can be megabytes for large snapshots.
    check_rate(&state, &headers, conn)?;
    let map: serde_json::Map<String, serde_json::Value> = state
        .store
        .snapshot_iter()
        .map(|(k, v)| (k.clone(), json!(*v)))
        .collect();
    Ok(Json(serde_json::Value::Object(map)))
}

// ── Claims ──────────────────────────────────────────────────────

#[derive(Debug, Deserialize)]
pub struct ClaimRequest {
    pub address: String,
    pub signature: String,
    pub timestamp: i64,
    #[serde(default)]
    pub recipient: Option<String>,
}

async fn claim(
    ConnectInfo(conn): ConnectInfo<SocketAddr>,
    headers: HeaderMap,
    State(state): State<AppState>,
    Json(req): Json<ClaimRequest>,
) -> Result<Json<serde_json::Value>, Response> {
    check_rate(&state, &headers, conn)?;

    let address = validate::check_address(&req.address)
        .map_err(|e| err(StatusCode::BAD_REQUEST, e))?
        .to_string();
    let sig = validate::check_signature_hex(&req.signature)
        .map_err(|e| err(StatusCode::BAD_REQUEST, e))?;

    // Timestamp window — guards against replay of stolen signatures.
    let now = now_ts();
    if (now - req.timestamp).abs() > 300 {
        return Err(err(
            StatusCode::BAD_REQUEST,
            "Timestamp too old or in the future (must be within 5 minutes)",
        ));
    }

    let commitment = state
        .store
        .get_commitment(&address)
        .ok_or_else(|| err(StatusCode::BAD_REQUEST, format!("No verified commitment for {address}. Commit first.")))?;

    let source_type = commitment.source_type.as_str();
    let message = format!("claim {}", req.timestamp).into_bytes();

    // Per-claim hash for replay protection (one signature, one claim).
    let digest = {
        let mut h = Sha256::new();
        h.update(format!("{address}:claim:{}", req.timestamp).as_bytes());
        hex::encode(h.finalize())
    };
    if state.store.is_sig_used(&digest) {
        return Err(err(
            StatusCode::BAD_REQUEST,
            "Claim signature already used (replay rejected)",
        ));
    }

    let valid = match source_type {
        "substrate" => match crypto::ss58_decode(&address) {
            Ok(pk) => crypto::verify_sr25519(&pk, &sig, &message),
            Err(_) => false,
        },
        "solana" => match crypto::solana_address_to_pubkey(&address) {
            Ok(pk) => crypto::verify_ed25519(&pk, &sig, &message),
            Err(_) => false,
        },
        _ => false,
    };
    if !valid {
        return Err(err(
            StatusCode::BAD_REQUEST,
            "Invalid signature — caller does not own source address",
        ));
    }

    if let Some(r) = &req.recipient {
        if r.to_lowercase() != commitment.evm_address.to_lowercase() {
            return Err(err(
                StatusCode::BAD_REQUEST,
                "Recipient does not match committed EVM address",
            ));
        }
    }
    let recipient = commitment.evm_address.clone();

    let total = state.store.balance(&address);
    if total <= 0.0 {
        return Err(err(
            StatusCode::BAD_REQUEST,
            format!("No allocation for {address}"),
        ));
    }
    if state.store.has_claim(&address) {
        return Err(err(
            StatusCode::BAD_REQUEST,
            format!("Already claimed for {address}"),
        ));
    }
    let claimed = state.store.claim_amount(&address);
    let amount = total - claimed;
    if amount <= 0.0 {
        return Err(err(StatusCode::BAD_REQUEST, "Nothing to claim"));
    }

    // Record sig BEFORE the claim — if write fails later we still block replay.
    state
        .store
        .mark_sig_used(&digest)
        .map_err(|e| err(StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    let claim_record = Claim {
        amount,
        recipient: recipient.clone(),
        from: address.clone(),
        timestamp: now as f64,
    };
    state
        .store
        .add_claim(&address, claim_record)
        .map_err(|e| err(StatusCode::BAD_REQUEST, e.to_string()))?;

    Ok(Json(json!({
        "success": true,
        "amount": amount,
        "recipient": recipient,
        "from": address,
    })))
}

async fn has_claimed(
    State(state): State<AppState>,
    Path(address): Path<String>,
) -> Result<Json<serde_json::Value>, Response> {
    let addr = validate::check_address(&address).map_err(|e| err(StatusCode::BAD_REQUEST, e))?;
    Ok(Json(json!({
        "claimed": state.store.has_claim(addr),
        "address": addr,
    })))
}

async fn unclaimed(
    State(state): State<AppState>,
    Path(address): Path<String>,
) -> Result<Json<serde_json::Value>, Response> {
    let addr = validate::check_address(&address).map_err(|e| err(StatusCode::BAD_REQUEST, e))?;
    let total = state.store.balance(addr);
    let claimed = state.store.claim_amount(addr);
    Ok(Json(json!({
        "address": addr,
        "unclaimed": (total - claimed).max(0.0),
    })))
}

async fn claims(State(state): State<AppState>) -> Json<serde_json::Value> {
    Json(json!(state.store.claims_clone()))
}

async fn claims_array(State(state): State<AppState>) -> Json<serde_json::Value> {
    let items: Vec<serde_json::Value> = state
        .store
        .claims_clone()
        .into_iter()
        .map(|(addr, c)| json!({
            "address": addr,
            "amount": c.amount,
            "recipient": c.recipient,
            "from": c.from,
            "timestamp": c.timestamp,
        }))
        .collect();
    Json(json!(items))
}

#[derive(Debug, Deserialize)]
pub struct DeleteClaimRequest {
    pub address: String,
    pub auth_token: String,
}

async fn delete_claim(
    State(state): State<AppState>,
    Json(req): Json<DeleteClaimRequest>,
) -> Result<Json<serde_json::Value>, Response> {
    let admin = state
        .config
        .admin_key
        .as_deref()
        .ok_or_else(|| err(StatusCode::FORBIDDEN, "Admin auth not configured"))?;
    if !crypto::constant_eq(&req.auth_token, admin) {
        return Err(err(
            StatusCode::FORBIDDEN,
            "Valid owner auth_token required",
        ));
    }
    let addr = validate::check_address(&req.address).map_err(|e| err(StatusCode::BAD_REQUEST, e))?;
    let removed = state
        .store
        .delete_claim(addr)
        .map_err(|e| err(StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    if !removed {
        return Err(err(
            StatusCode::NOT_FOUND,
            format!("No claim found for {addr}"),
        ));
    }
    Ok(Json(json!({ "success": true, "deleted": addr })))
}

#[derive(Debug, Deserialize)]
pub struct ResetRequest {
    pub auth_token: String,
}

async fn reset(
    State(state): State<AppState>,
    Json(req): Json<ResetRequest>,
) -> Result<Json<serde_json::Value>, Response> {
    let admin = state
        .config
        .admin_key
        .as_deref()
        .ok_or_else(|| err(StatusCode::FORBIDDEN, "Admin auth not configured"))?;
    if !crypto::constant_eq(&req.auth_token, admin) {
        return Err(err(StatusCode::FORBIDDEN, "Valid admin auth_token required"));
    }
    state
        .store
        .reset()
        .map_err(|e| err(StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    Ok(Json(json!({
        "success": true,
        "reset": ["claims", "commitments", "used_signatures"],
    })))
}

// ── Commitments ─────────────────────────────────────────────────

#[derive(Debug, Deserialize)]
pub struct CommitRequest {
    pub source_address: String,
    pub evm_address: String,
    pub signature: String,
    pub source_type: String,
}

async fn commit(
    ConnectInfo(conn): ConnectInfo<SocketAddr>,
    headers: HeaderMap,
    State(state): State<AppState>,
    Json(req): Json<CommitRequest>,
) -> Result<Json<serde_json::Value>, Response> {
    check_rate(&state, &headers, conn)?;
    do_commit(state, req, false).await
}

async fn update_commitment(
    ConnectInfo(conn): ConnectInfo<SocketAddr>,
    headers: HeaderMap,
    State(state): State<AppState>,
    Json(req): Json<CommitRequest>,
) -> Result<Json<serde_json::Value>, Response> {
    check_rate(&state, &headers, conn)?;
    do_commit(state, req, true).await
}

async fn do_commit(
    state: AppState,
    req: CommitRequest,
    is_update: bool,
) -> Result<Json<serde_json::Value>, Response> {
    let source_address = validate::check_address(&req.source_address)
        .map_err(|e| err(StatusCode::BAD_REQUEST, e))?
        .to_string();
    let evm_address =
        validate::check_evm_address(&req.evm_address).map_err(|e| err(StatusCode::BAD_REQUEST, e))?;
    let source_type = validate::check_source_type(&req.source_type)
        .map_err(|e| err(StatusCode::BAD_REQUEST, e))?;
    let sig = validate::check_signature_hex(&req.signature)
        .map_err(|e| err(StatusCode::BAD_REQUEST, e))?;

    if !state.store.snapshot_contains(&source_address) {
        return Err(err(
            StatusCode::BAD_REQUEST,
            format!("Address not in snapshot: {source_address}"),
        ));
    }

    let existing = state.store.get_commitment(&source_address);
    if is_update {
        let prev = existing.as_ref().ok_or_else(|| {
            err(
                StatusCode::BAD_REQUEST,
                format!("No existing commitment for {source_address}"),
            )
        })?;
        if prev.source_type != source_type {
            return Err(err(
                StatusCode::BAD_REQUEST,
                "source_type does not match original commitment",
            ));
        }
        if prev.evm_address.to_lowercase() == evm_address.to_lowercase() {
            return Err(err(
                StatusCode::BAD_REQUEST,
                "New EVM address is the same as current",
            ));
        }
        if state.store.has_claim(&source_address) {
            return Err(err(
                StatusCode::BAD_REQUEST,
                "Cannot update commitment after claim",
            ));
        }
    } else if existing.is_some() {
        return Err(err(
            StatusCode::BAD_REQUEST,
            format!("Already committed: {source_address}"),
        ));
    }

    // Replay protection — same triple can't be reused.
    let digest = {
        let mut h = Sha256::new();
        h.update(format!("{source_address}:{evm_address}:{source_type}").as_bytes());
        hex::encode(h.finalize())
    };
    if state.store.is_sig_used(&digest) {
        return Err(err(
            StatusCode::BAD_REQUEST,
            "Commitment already used (replay rejected)",
        ));
    }

    let message = format!("commit {evm_address}").into_bytes();
    let valid = match source_type {
        "substrate" => match crypto::ss58_decode(&source_address) {
            Ok(pk) => crypto::verify_sr25519(&pk, &sig, &message),
            Err(_) => false,
        },
        "solana" => match crypto::solana_address_to_pubkey(&source_address) {
            Ok(pk) => crypto::verify_ed25519(&pk, &sig, &message),
            Err(_) => false,
        },
        _ => false,
    };
    if !valid {
        return Err(err(StatusCode::BAD_REQUEST, "Invalid signature"));
    }

    state
        .store
        .mark_sig_used(&digest)
        .map_err(|e| err(StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;

    let prev_evm = existing.as_ref().map(|c| c.evm_address.clone());
    let record = Commitment {
        source_address: source_address.clone(),
        evm_address: evm_address.clone(),
        source_type: source_type.to_string(),
        timestamp: now_ts() as f64,
        previous_evm: if is_update { prev_evm.clone() } else { None },
        chain: None,
    };
    state
        .store
        .add_commitment(&source_address, record)
        .map_err(|e| err(StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;

    let mut response = json!({
        "success": true,
        "source_address": source_address,
        "evm_address": evm_address,
        "source_type": source_type,
    });
    if is_update {
        if let Some(pe) = prev_evm {
            response["previous_evm"] = json!(pe);
        }
    }
    Ok(Json(response))
}

async fn commitments(State(state): State<AppState>) -> Json<serde_json::Value> {
    Json(json!(state.store.commitments_clone()))
}

async fn commitment(
    State(state): State<AppState>,
    Path(addr): Path<String>,
) -> Result<Json<serde_json::Value>, Response> {
    let addr = validate::check_address(&addr).map_err(|e| err(StatusCode::BAD_REQUEST, e))?;
    state
        .store
        .get_commitment(addr)
        .map(|c| Json(json!(c)))
        .ok_or_else(|| err(StatusCode::NOT_FOUND, format!("No commitment for {addr}")))
}
