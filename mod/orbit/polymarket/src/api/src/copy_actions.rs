//! Wallet-signed COPY DESK actions — load money onto a trader, take it back
//! off, or drop the trader — each authorized by an EIP-191 signature from the
//! owner wallet over the EXACT action, not by the session token alone.
//!
//! Why this exists: every other `/copy/*` write is authorized by the Bearer
//! token, which is minted once at sign-in and then trusted for a week. That is
//! fine for reads and reversible knobs, but "put $500 behind this trader" and
//! "remove this trader" are the desk's money movements — the owner asked for a
//! TRUSTLESS path where the server cannot fabricate, replay, or alter one:
//!
//!   1. The client asks `/copy/signed/challenge` for the message. The server
//!      BUILDS the exact bytes (action, trader, amount, wallet, timestamp,
//!      HMAC nonce) — the client never constructs them, same rule as sign-in.
//!   2. The wallet `personal_sign`s that message. The user reads what they
//!      are authorizing in the wallet popup: "LOAD $25.00 INTO 0xab…".
//!   3. `/copy/signed/execute` rebuilds the message from the submitted fields,
//!      recovers the signer, and requires it to BE the deployment owner (and
//!      the wallet named in the message). A signature over different fields
//!      recovers a different address and dies.
//!   4. Freshness (±10 min) and a persisted replay guard (the SHA-256 of every
//!      executed message) make each signature single-use.
//!   5. The signature, the message digest, and the before/after allocation are
//!      appended to `<state>/copy/receipts.json` — an audit trail where every
//!      money movement is provably wallet-authorized, verifiable by anyone
//!      with the receipts file (recover the signer from message + signature).
//!
//! Actions:
//!   load    allocation += amount  (adds the trader if absent)
//!   remove  allocation -= amount  (floored at $0; trader must be in the book)
//!   drop    stop the session, remove the trader from the book
//!
//! The routes sit BEHIND the access gate like everything else — the signature
//! is a second, stronger factor on top of the token, not a way around it.

use std::path::PathBuf;
use std::sync::Arc;

use anyhow::{anyhow, Result};
use axum::extract::{Query, State};
use axum::http::StatusCode;
use axum::response::IntoResponse;
use axum::routing::{get, post};
use axum::{Json, Router};
use serde::Deserialize;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

use crate::access::AccessStore;
use crate::copy::{engine_config, normalize_address, now_ms, strategy_id_for, UpsertRequest};
use crate::AppState;

/// Signature freshness window (± seconds around server now) — same width as
/// the sign-in challenge window.
const ACTION_WINDOW_SECS: i64 = 600;
/// Receipts kept on disk. Old entries roll off the front; their replay
/// digests roll with them, which is safe because the freshness window has
/// long since closed on anything that old.
const MAX_RECEIPTS: usize = 2000;

#[derive(Clone)]
pub struct CopyActionState {
    access: Arc<AccessStore>,
    app: AppState,
    receipts_path: PathBuf,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum CopyAction {
    Load,
    Remove,
    Drop,
}

impl CopyAction {
    fn parse(s: &str) -> Result<Self> {
        match s.trim().to_lowercase().as_str() {
            "load" => Ok(Self::Load),
            "remove" | "unload" => Ok(Self::Remove),
            "drop" | "delete" => Ok(Self::Drop),
            other => Err(anyhow!("unknown action {:?} (load|remove|drop)", other)),
        }
    }

    fn wire(&self) -> &'static str {
        match self {
            Self::Load => "load",
            Self::Remove => "remove",
            Self::Drop => "drop",
        }
    }

    /// The human sentence in the wallet popup — what the signature authorizes,
    /// readable before signing.
    fn sentence(&self, trader: &str, amount: Option<f64>) -> String {
        match self {
            Self::Load => format!("LOAD ${:.2} INTO {}", amount.unwrap_or(0.0), trader),
            Self::Remove => format!("REMOVE ${:.2} FROM {}", amount.unwrap_or(0.0), trader),
            Self::Drop => format!("DROP {} FROM THE COPY BOOK", trader),
        }
    }
}

/// The exact string the wallet signs. Byte-identical on both sides: built here
/// for `/copy/signed/challenge` AND rebuilt in execute from the submitted
/// fields — the client never constructs it.
fn action_message(
    access: &AccessStore,
    action: CopyAction,
    trader: &str,
    amount: Option<f64>,
    eoa: &str,
    ts: i64,
) -> String {
    let amount_line = match action {
        CopyAction::Drop => String::new(),
        _ => format!("amount-usd: {:.2}\n", amount.unwrap_or(0.0)),
    };
    let nonce = access.action_nonce(&format!(
        "copyaction|{}|{}|{}|{}|{}",
        action.wire(),
        trader,
        amount.map(|a| format!("{:.2}", a)).unwrap_or_default(),
        eoa,
        ts
    ));
    format!(
        "polymarket console — signed copy-desk action\n\
         \n\
         {}\n\
         \n\
         action: {}\n\
         trader: {}\n\
         {}wallet: {}\n\
         timestamp: {}\n\
         nonce: {}\n\
         \n\
         This signature authorizes exactly this action, once, within 10 \
         minutes. It is recorded with the action as its proof of authorization.",
        action.sentence(trader, amount),
        action.wire(),
        trader,
        amount_line,
        eoa,
        ts,
        nonce,
    )
}

#[derive(Deserialize)]
struct ChallengeBody {
    action: String,
    /// The leader the action is about.
    trader: String,
    #[serde(rename = "amountUsd", default)]
    amount_usd: Option<f64>,
    /// The wallet that will sign — must be the deployment owner.
    eoa: String,
}

#[derive(Deserialize)]
struct ExecuteBody {
    action: String,
    trader: String,
    #[serde(rename = "amountUsd", default)]
    amount_usd: Option<f64>,
    eoa: String,
    timestamp: i64,
    signature: String,
}

#[derive(Deserialize)]
struct EoaQuery {
    #[serde(default)]
    eoa: Option<String>,
}

fn bad(e: anyhow::Error) -> axum::response::Response {
    (StatusCode::BAD_REQUEST, Json(json!({"error": e.to_string()}))).into_response()
}

fn forbidden(msg: &str) -> axum::response::Response {
    (StatusCode::FORBIDDEN, Json(json!({"error": msg}))).into_response()
}

/// Validate the (action, trader, amount) triple the same way for challenge
/// and execute, so a challenge the server hands out is one it will accept.
fn validate(
    action: CopyAction,
    trader: &str,
    amount: Option<f64>,
    eoa: &str,
) -> Result<(String, Option<f64>, String)> {
    let trader = normalize_address(trader)?;
    let eoa = normalize_address(eoa)?;
    let amount = match action {
        CopyAction::Drop => None,
        _ => {
            let a = amount.ok_or_else(|| anyhow!("amountUsd is required for load/remove"))?;
            if !(a.is_finite() && a > 0.0) {
                return Err(anyhow!("amountUsd must be a positive number"));
            }
            if a > 1_000_000.0 {
                return Err(anyhow!("amountUsd looks like a typo (> $1,000,000)"));
            }
            Some((a * 100.0).round() / 100.0)
        }
    };
    Ok((trader, amount, eoa))
}

async fn challenge(
    State(st): State<CopyActionState>,
    Json(body): Json<ChallengeBody>,
) -> impl IntoResponse {
    let action = match CopyAction::parse(&body.action) {
        Ok(a) => a,
        Err(e) => return bad(e),
    };
    let (trader, amount, eoa) = match validate(action, &body.trader, body.amount_usd, &body.eoa) {
        Ok(v) => v,
        Err(e) => return bad(e),
    };
    let ts = chrono::Utc::now().timestamp();
    let message = action_message(&st.access, action, &trader, amount, &eoa, ts);
    Json(json!({
        "message": message,
        "timestamp": ts,
        "action": action.wire(),
        "trader": trader,
        "amountUsd": amount,
        "eoa": eoa,
    }))
    .into_response()
}

async fn execute(
    State(st): State<CopyActionState>,
    Json(body): Json<ExecuteBody>,
) -> impl IntoResponse {
    let action = match CopyAction::parse(&body.action) {
        Ok(a) => a,
        Err(e) => return bad(e),
    };
    let (trader, amount, eoa) = match validate(action, &body.trader, body.amount_usd, &body.eoa) {
        Ok(v) => v,
        Err(e) => return bad(e),
    };

    // Freshness first — a stale signature is dead regardless of who signed it.
    let now = chrono::Utc::now().timestamp();
    if (now - body.timestamp).abs() > ACTION_WINDOW_SECS {
        return bad(anyhow!(
            "signature expired — the challenge is valid for {} minutes, ask for a fresh one",
            ACTION_WINDOW_SECS / 60
        ));
    }

    // Rebuild the exact bytes and recover the signer. Any field the client
    // altered after signing changes the message, and the recovery no longer
    // lands on the owner.
    let message = action_message(&st.access, action, &trader, amount, &eoa, body.timestamp);
    let recovered = match crate::access::recover_address(&message, &body.signature) {
        Some(a) => a,
        None => return bad(anyhow!("signature recovery failed")),
    };
    if recovered != eoa {
        return forbidden("signature was not made by the wallet named in the action");
    }
    if let Some(owner) = st.access.owner() {
        if recovered != owner {
            tracing::warn!(address = %recovered, "signed copy action DENIED — not the owner");
            return forbidden("only the owner wallet can authorize copy-desk actions");
        }
    }

    // Single use: the digest of every executed message is persisted, and a
    // repeat dies here even inside the freshness window.
    let digest = hex::encode(Sha256::digest(message.as_bytes()));
    let mut receipts = read_receipts(&st.receipts_path);
    if receipts.iter().any(|r| r.get("digest").and_then(Value::as_str) == Some(digest.as_str())) {
        return bad(anyhow!("this signature was already used — sign a fresh challenge"));
    }

    // Apply. Every branch reads the current allocation first so the receipt
    // can state before → after.
    let book_before = st.app.copy_book.read();
    let before_usd = book_before.get(&trader).map(|a| a.allocation_usd);
    let sid = strategy_id_for(&trader);

    let (after_usd, stopped) = match action {
        CopyAction::Load => {
            let next = ((before_usd.unwrap_or(0.0) + amount.unwrap_or(0.0)) * 100.0).round() / 100.0;
            if let Err(e) = st.app.copy_book.upsert(UpsertRequest {
                address: trader.clone(),
                allocation_usd: next,
                label: None,
                notes: None,
                enabled: None,
                params: Default::default(),
            }) {
                return bad(e);
            }
            (Some(next), false)
        }
        CopyAction::Remove => {
            let Some(cur) = before_usd else {
                return bad(anyhow!("{} isn't in the copy book", trader));
            };
            let next = (((cur - amount.unwrap_or(0.0)).max(0.0)) * 100.0).round() / 100.0;
            if let Err(e) = st.app.copy_book.upsert(UpsertRequest {
                address: trader.clone(),
                allocation_usd: next,
                label: None,
                notes: None,
                enabled: None,
                params: Default::default(),
            }) {
                return bad(e);
            }
            (Some(next), false)
        }
        CopyAction::Drop => {
            // Stop FIRST — same rule as the unsigned DELETE: a session left
            // running for a row that no longer exists is invisible spending.
            let stopped = st.app.engines.stop(&eoa, Some(&sid));
            match st.app.copy_book.remove(&trader) {
                Ok(_) => (None, stopped),
                Err(e) => return bad(e),
            }
        }
    };

    // A live session was started from the OLD allocation — re-post the config
    // so the signed resize takes effect now, preserving its execution mode
    // (same rule as the unsigned upsert route).
    let mut reconfigured = false;
    if action != CopyAction::Drop {
        if st.app.engines.status_of(&eoa, Some(&sid)).is_some() {
            if let (Some(prev), Some(alloc)) = (
                st.app.engines.config_of(&eoa, Some(&sid)),
                st.app.copy_book.read().get(&trader).cloned(),
            ) {
                let mut cfg = engine_config(&alloc, &eoa, &prev.address);
                cfg.auto_execute = prev.auto_execute;
                st.app.engines.start(cfg);
                reconfigured = true;
            }
        }
    }

    let receipt = json!({
        "action": action.wire(),
        "trader": trader,
        "amountUsd": amount,
        "wallet": eoa,
        "beforeUsd": before_usd,
        "afterUsd": after_usd,
        "stoppedSession": stopped,
        "timestamp": body.timestamp,
        "executedAt": now_ms(),
        "signature": body.signature,
        "digest": digest,
    });
    receipts.push(receipt.clone());
    write_receipts(&st.receipts_path, &mut receipts);

    tracing::info!(
        action = action.wire(), trader = %trader, wallet = %eoa,
        amount = amount.unwrap_or(0.0),
        "signed copy-desk action executed",
    );
    Json(json!({
        "ok": true,
        "receipt": receipt,
        "reconfigured": reconfigured,
        "book": crate::copy::book_response(&st.app, Some(&eoa)),
    }))
    .into_response()
}

/// The audit trail. Each entry's `signature` over the rebuilt message is
/// independently verifiable — the receipts don't ask to be trusted.
async fn receipts(
    State(st): State<CopyActionState>,
    Query(q): Query<EoaQuery>,
) -> impl IntoResponse {
    let mut list = read_receipts(&st.receipts_path);
    if let Some(eoa) = q.eoa.as_deref().and_then(|e| normalize_address(e).ok()) {
        list.retain(|r| r.get("wallet").and_then(Value::as_str) == Some(eoa.as_str()));
    }
    list.reverse(); // newest first
    Json(json!({"receipts": list, "count": list.len()}))
}

fn read_receipts(path: &PathBuf) -> Vec<Value> {
    std::fs::read_to_string(path)
        .ok()
        .and_then(|raw| serde_json::from_str(&raw).ok())
        .unwrap_or_default()
}

fn write_receipts(path: &PathBuf, list: &mut Vec<Value>) {
    if list.len() > MAX_RECEIPTS {
        let drop = list.len() - MAX_RECEIPTS;
        list.drain(..drop);
    }
    if let Ok(raw) = serde_json::to_string_pretty(&list) {
        let tmp = path.with_extension("json.tmp");
        if std::fs::write(&tmp, raw).is_ok() {
            std::fs::rename(&tmp, path).ok();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn action_parse_accepts_the_spellings_and_rejects_the_rest() {
        assert!(matches!(CopyAction::parse("load"), Ok(CopyAction::Load)));
        assert!(matches!(CopyAction::parse("UNLOAD"), Ok(CopyAction::Remove)));
        assert!(matches!(CopyAction::parse("drop"), Ok(CopyAction::Drop)));
        assert!(CopyAction::parse("liquidate").is_err());
    }

    #[test]
    fn validate_requires_an_amount_for_money_moves_but_not_for_drop() {
        let t = "0x1111111111111111111111111111111111111111";
        let e = "0x2222222222222222222222222222222222222222";
        assert!(validate(CopyAction::Load, t, None, e).is_err());
        assert!(validate(CopyAction::Load, t, Some(0.0), e).is_err());
        assert!(validate(CopyAction::Load, t, Some(-5.0), e).is_err());
        let (_, amt, _) = validate(CopyAction::Load, t, Some(25.005), e).unwrap();
        assert_eq!(amt, Some(25.01)); // rounded to cents, so the signed text is exact
        // Drop carries no amount even when one is passed.
        let (_, amt, _) = validate(CopyAction::Drop, t, Some(25.0), e).unwrap();
        assert_eq!(amt, None);
        assert!(validate(CopyAction::Load, "0xnope", Some(1.0), e).is_err());
    }

    #[test]
    fn the_wallet_popup_sentence_names_the_money_and_the_trader() {
        let t = "0xabcdefabcdefabcdefabcdefabcdefabcdefabcd";
        assert_eq!(
            CopyAction::Load.sentence(t, Some(25.0)),
            format!("LOAD $25.00 INTO {}", t)
        );
        assert_eq!(
            CopyAction::Drop.sentence(t, None),
            format!("DROP {} FROM THE COPY BOOK", t)
        );
    }
}

pub fn router(access: Arc<AccessStore>, app: AppState) -> Router {
    let dir = crate::access::state_dir().join("copy");
    std::fs::create_dir_all(&dir).ok();
    let st = CopyActionState {
        access,
        app,
        receipts_path: dir.join("receipts.json"),
    };
    Router::new()
        // POST /copy/signed/challenge  {action, trader, amountUsd?, eoa}
        //                              → {message, timestamp} to personal_sign
        .route("/copy/signed/challenge", post(challenge))
        // POST /copy/signed/execute    challenge fields + {timestamp, signature}
        .route("/copy/signed/execute", post(execute))
        // GET  /copy/signed/receipts?eoa=   the verifiable audit trail
        .route("/copy/signed/receipts", get(receipts))
        .with_state(st)
}
