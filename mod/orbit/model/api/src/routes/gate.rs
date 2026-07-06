use crate::AppState;
use axum::{extract::State, http::StatusCode, Json};
use ethers::{
    types::{Address, Signature},
    utils::hash_message,
};
use serde::{Deserialize, Serialize};
use std::sync::Arc;

#[derive(Deserialize)]
pub struct VerifyRequest {
    /// Address that signed the challenge — must be lowercase hex with 0x prefix.
    pub address: String,
    /// Exact challenge string the wallet signed (from ModelGate.challenge()).
    pub challenge: String,
    /// 0x… hex EIP-191 signature.
    pub signature: String,
}

#[derive(Serialize)]
pub struct VerifyResponse {
    pub ok: bool,
    pub recovered: String,
    pub address: String,
    pub gate: Option<String>,
    pub chain_id: u64,
    /// True iff `recovered == address` (case-insensitive). The on-chain
    /// roster check still needs to happen — see `note`.
    pub signature_valid: bool,
    pub note: &'static str,
}

/// Verify the EIP-191 personal-signed challenge. Does NOT do an RPC call to
/// the gate contract — that lookup is left to the caller (or a future cron
/// that caches the roster). Returns the recovered address so the front-end
/// can render a useful error before round-tripping to Base Sepolia.
pub async fn verify(
    State(s): State<Arc<AppState>>,
    Json(req): Json<VerifyRequest>,
) -> Result<Json<VerifyResponse>, (StatusCode, String)> {
    let claimed: Address = req.address.parse()
        .map_err(|e| (StatusCode::BAD_REQUEST, format!("bad address: {e}")))?;
    let sig: Signature = req.signature.parse()
        .map_err(|e| (StatusCode::BAD_REQUEST, format!("bad signature: {e}")))?;
    let hash = hash_message(req.challenge.as_bytes());
    let recovered = sig.recover(hash)
        .map_err(|e| (StatusCode::BAD_REQUEST, format!("recover failed: {e}")))?;

    Ok(Json(VerifyResponse {
        ok: true,
        recovered: format!("{recovered:#x}"),
        address: format!("{claimed:#x}"),
        gate: s.gate_address.map(|a| format!("{a:#x}")),
        chain_id: s.gate_chain_id,
        signature_valid: recovered == claimed,
        note: "signature only — caller must also check `allowed(address)` on the gate via RPC",
    }))
}
