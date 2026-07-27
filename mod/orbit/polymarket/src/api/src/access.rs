//! Owner-only access gate + signed Terms-of-Use acceptance.
//!
//! This deployment is PRIVATE: only the fleet's sudo/owner EOA may use it.
//! Every API route except `/health` and `/access/*` requires a Bearer token
//! that can only be minted by proving control of the owner key.
//!
//! Sign-in doubles as terms acceptance: the challenge message the wallet
//! signs embeds the Terms version + SHA-256 of the exact terms text, so the
//! stored signature is cryptographic proof the signer saw and accepted THAT
//! text — including the restricted-jurisdiction representations. Acceptances
//! are appended to `<dir>/terms_accepted.json` for audit.
//!
//! Owner resolution (first hit wins):
//!   1. `POLYMARKET_OWNER` env var
//!   2. `~/.mod/polymarket/owner.json`  {"owner": "0x..."}
//!   3. `~/.mod/claude/owner.json`      (the fleet-wide sudo address)
//! No owner found → the gate stays locked for everyone (fail closed).
//!
//! The HMAC token secret persists at `<dir>/server.secret` so sessions
//! survive API restarts (same lesson as the claude module: a random
//! per-boot secret 401s every client after each self-restart).
//!
//! `POLYMARKET_ACCESS_OPEN=1` disables the guard (local dev / CI only) —
//! it is logged loudly and never the default.

use std::path::PathBuf;
use std::sync::Arc;

use axum::extract::{Query, Request, State};
use axum::http::StatusCode;
use axum::middleware::Next;
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use hmac::{Hmac, Mac};
use k256::ecdsa::{RecoveryId, Signature, VerifyingKey};
use serde::Deserialize;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

use crate::signer::{address_from_pubkey, keccak256};

pub const TERMS_VERSION: &str = "1.0";

pub const TERMS_TEXT: &str = r#"POLYMARKET CONSOLE — TERMS OF USE
Version 1.0 — Effective 2026-07-16

READ CAREFULLY. BY SIGNING THE SIGN-IN MESSAGE YOU AGREE TO BE BOUND BY
THESE TERMS. IF YOU DO NOT AGREE, DO NOT SIGN AND DO NOT USE THIS SOFTWARE.

1. PRIVATE DEPLOYMENT — NOT A PUBLIC SERVICE
   This software ("the Console") is a private, self-hosted, personal
   research and trading tool operated for its owner's own account. It is
   not offered to the public, is not a brokerage, exchange, investment
   service, or gambling service, and no relationship of any kind is
   created between you and the operator or the developers by your use of
   it. Access is restricted to the deployment's designated owner address;
   any other access is unauthorized.

2. ELIGIBILITY AND RESTRICTED JURISDICTIONS
   By signing you REPRESENT AND WARRANT, each time you use the Console,
   that:
   (a) you are NOT located in, incorporated in, a citizen or resident of,
       or accessing the Console from the United States of America, France,
       Belgium, Poland, Thailand, Singapore, Taiwan, the United Arab
       Emirates, Ontario (Canada), the United Kingdom where prohibited, or
       any other jurisdiction in which access to or use of Polymarket,
       prediction markets, event contracts, or binary options is
       prohibited or restricted by applicable law or by Polymarket's own
       terms of service;
   (b) you are NOT a person or entity subject to, located in, or
       organized under the laws of any jurisdiction subject to
       comprehensive sanctions administered by OFAC, the EU, the UN, or
       the UK (including without limitation Cuba, Iran, North Korea,
       Syria, and the Crimea, Donetsk, and Luhansk regions), and are not
       named on any sanctions or restricted-parties list;
   (c) you will NOT use a VPN, proxy, or any other technique to obscure
       your location or to circumvent geographic restrictions imposed by
       Polymarket or by applicable law — such circumvention is expressly
       prohibited by these Terms;
   (d) you are of legal age and legally permitted to trade prediction
       markets in every jurisdiction whose laws apply to you; and
   (e) YOU ARE SOLELY RESPONSIBLE for determining whether your use of the
       Console and of Polymarket is lawful where you are. The operator
       and developers make no representation that the Console is
       appropriate or lawful in any particular location, and use in
       violation of applicable law is entirely at your own risk and your
       own liability.

3. THIRD-PARTY SERVICES
   The Console interacts with Polymarket's public APIs and smart
   contracts and with public blockchain networks. Your use of Polymarket
   is governed independently by Polymarket's own Terms of Use, which you
   must review and comply with. The operator and developers are not
   affiliated with, endorsed by, or responsible for Polymarket or any
   other third-party service, API, RPC provider, or smart contract.

4. NON-CUSTODIAL; IRREVERSIBLE TRANSACTIONS
   The Console is non-custodial with respect to your primary wallet: you
   control your keys and your funds. Blockchain transactions are
   irreversible. Deposits, trades, redemptions, and withdrawals you
   initiate (including automated trades placed by strategies you enable)
   are your own actions, executed on your instructions.

5. ASSUMPTION OF RISK
   Trading prediction markets involves substantial risk, including the
   TOTAL LOSS of all funds committed. Automated / copy-trading strategies
   can compound losses quickly and may behave unexpectedly. Software
   bugs, API outages, rate limits, stale data, smart-contract defects,
   blockchain congestion, and third-party failures can each cause loss.
   You accept all such risks entirely.

6. NO ADVICE
   Nothing in the Console — including market data, leaderboards, trader
   statistics, backtests, equity curves, or strategy output — is
   financial, investment, legal, or tax advice, or a recommendation to
   enter any transaction. Backtested or simulated performance does not
   predict future results.

7. NO WARRANTY
   THE CONSOLE IS PROVIDED "AS IS" AND "AS AVAILABLE", WITHOUT WARRANTY
   OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING WITHOUT LIMITATION
   MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE, ACCURACY, AND
   NON-INFRINGEMENT. NO ORAL OR WRITTEN INFORMATION OBTAINED FROM THE
   CONSOLE CREATES ANY WARRANTY.

8. LIMITATION OF LIABILITY
   TO THE MAXIMUM EXTENT PERMITTED BY LAW, THE OPERATOR, DEVELOPERS, AND
   CONTRIBUTORS SHALL NOT BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
   SPECIAL, CONSEQUENTIAL, EXEMPLARY, OR PUNITIVE DAMAGES, OR FOR ANY
   LOSS OF PROFITS, FUNDS, TOKENS, DATA, OR GOODWILL, ARISING OUT OF OR
   RELATING TO THE CONSOLE OR THESE TERMS, HOWEVER CAUSED AND UNDER ANY
   THEORY OF LIABILITY, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH
   DAMAGES. AGGREGATE LIABILITY SHALL IN NO EVENT EXCEED ZERO DOLLARS
   (USD $0), REFLECTING THAT THE CONSOLE IS PROVIDED FREE OF CHARGE.

9. INDEMNIFICATION
   You will indemnify, defend, and hold harmless the operator,
   developers, and contributors from and against all claims, damages,
   losses, liabilities, penalties, and expenses (including reasonable
   legal fees) arising from your use of the Console, your trading
   activity, or your breach of these Terms — including any use from, or
   on behalf of any person in, a restricted jurisdiction and any
   circumvention of geographic restrictions.

10. ACCESS AND TERMINATION
   Access may be suspended, revoked, or modified at any time, for any
   reason, without notice. Sections 2 and 5–9 survive termination.

11. CHANGES
   These Terms may be updated; a new version requires a fresh signed
   acceptance before continued use.

12. GENERAL
   If any provision is unenforceable, the remainder stays in effect.
   These Terms are the entire agreement regarding the Console. Your
   signed sign-in message, which embeds the SHA-256 hash of this exact
   text, is conclusive evidence of your acceptance.
"#;

/// Token lifetime — 7 days. Owner-only deployment, so long-lived sessions
/// are a convenience win, not a risk multiplier; revocation = rotate
/// `server.secret`.
const TOKEN_TTL_SECS: u64 = 7 * 24 * 3600;
/// Challenge freshness window (± seconds around server now).
const CHALLENGE_WINDOW_SECS: i64 = 600;

type HmacSha256 = Hmac<Sha256>;

pub struct AccessStore {
    owner: Option<String>,
    secret: [u8; 32],
    dir: PathBuf,
    open: bool,
}

impl AccessStore {
    pub fn from_env() -> Arc<Self> {
        let dir = std::env::var("POLYMARKET_ACCESS_DIR")
            .map(PathBuf::from)
            .unwrap_or_else(|_| {
                let home = std::env::var("HOME").unwrap_or_else(|_| "/root".into());
                PathBuf::from(home).join(".mod").join("polymarket")
            });
        std::fs::create_dir_all(&dir).ok();

        let owner = Self::resolve_owner(&dir);
        match &owner {
            Some(o) => tracing::info!(owner = %o, "access gate: owner-only mode"),
            None => tracing::error!(
                "access gate: NO OWNER RESOLVED — gate is locked for everyone. \
                 Set POLYMARKET_OWNER or write {}/owner.json",
                dir.display()
            ),
        }

        let open = std::env::var("POLYMARKET_ACCESS_OPEN").ok().as_deref() == Some("1");
        if open {
            tracing::warn!("access gate DISABLED via POLYMARKET_ACCESS_OPEN=1 — dev/CI only");
        }

        let secret = Self::load_or_init_secret(&dir);
        Arc::new(Self { owner, secret, dir, open })
    }

    fn resolve_owner(dir: &PathBuf) -> Option<String> {
        if let Ok(o) = std::env::var("POLYMARKET_OWNER") {
            let o = o.trim().to_lowercase();
            if o.starts_with("0x") && o.len() == 42 {
                return Some(o);
            }
        }
        let read_owner_json = |p: PathBuf| -> Option<String> {
            let raw = std::fs::read_to_string(p).ok()?;
            let v: Value = serde_json::from_str(&raw).ok()?;
            let o = v.get("owner")?.as_str()?.trim().to_lowercase();
            (o.starts_with("0x") && o.len() == 42).then_some(o)
        };
        if let Some(o) = read_owner_json(dir.join("owner.json")) {
            return Some(o);
        }
        // Fleet-wide sudo address: the claude module's owner is the orbit's
        // root identity, so a polymarket deployment with no explicit owner
        // config inherits it rather than shipping unlocked or hardcoded.
        let home = std::env::var("HOME").unwrap_or_else(|_| "/root".into());
        read_owner_json(PathBuf::from(home).join(".mod").join("claude").join("owner.json"))
    }

    /// Persisted HMAC secret — random-per-boot would invalidate every
    /// session on each restart.
    fn load_or_init_secret(dir: &PathBuf) -> [u8; 32] {
        let path = dir.join("server.secret");
        if let Ok(raw) = std::fs::read_to_string(&path) {
            if let Ok(bytes) = hex::decode(raw.trim()) {
                if bytes.len() == 32 {
                    let mut out = [0u8; 32];
                    out.copy_from_slice(&bytes);
                    return out;
                }
            }
        }
        let mut out = [0u8; 32];
        use rand::RngCore;
        rand::rngs::OsRng.fill_bytes(&mut out);
        if std::fs::write(&path, hex::encode(out)).is_err() {
            tracing::warn!(path = %path.display(), "could not persist access secret — sessions will not survive restarts");
        }
        out
    }

    pub fn terms_sha256() -> String {
        hex::encode(Sha256::digest(TERMS_TEXT.as_bytes()))
    }

    fn hmac_hex(&self, data: &str) -> String {
        let mut mac = HmacSha256::new_from_slice(&self.secret).expect("hmac key");
        mac.update(data.as_bytes());
        hex::encode(mac.finalize().into_bytes())
    }

    /// Stateless challenge nonce — recomputable at verify time from the
    /// same (address, timestamp), so nothing needs to be stored per attempt.
    fn nonce_for(&self, address: &str, ts: i64) -> String {
        self.hmac_hex(&format!("challenge|{}|{}", address, ts))[..16].to_string()
    }

    /// The exact string the wallet personal_signs. Byte-identical on both
    /// sides: the server builds it here for /access/challenge AND rebuilds
    /// it in verify — the client never constructs it.
    fn message_for(&self, address: &str, ts: i64) -> String {
        format!(
            "polymarket console — owner sign-in & terms acceptance\n\
             \n\
             address: {}\n\
             timestamp: {}\n\
             nonce: {}\n\
             terms-version: {}\n\
             terms-sha256: {}\n\
             \n\
             I have read and accept the Terms of Use in full, including the \
             eligibility and restricted-jurisdiction provisions in Section 2. \
             I represent that I am not located in, resident of, or accessing \
             this software from any jurisdiction where the use of Polymarket \
             or prediction markets is prohibited or restricted, that I am not \
             using a VPN or proxy to circumvent such restrictions, and that I \
             am solely responsible for compliance with the laws that apply to \
             me.",
            address,
            ts,
            self.nonce_for(address, ts),
            TERMS_VERSION,
            Self::terms_sha256(),
        )
    }

    fn issue_token(&self, address: &str) -> (String, u64) {
        let exp = chrono::Utc::now().timestamp() as u64 + TOKEN_TTL_SECS;
        let sig = self.hmac_hex(&format!("pma1|{}|{}", address, exp));
        (format!("pma1.{}.{}.{}", address, exp, sig), exp)
    }

    /// Returns the token's address when valid + unexpired + owned.
    pub fn verify_token(&self, token: &str) -> Option<String> {
        let mut parts = token.split('.');
        let (v, addr, exp, sig) = (parts.next()?, parts.next()?, parts.next()?, parts.next()?);
        if v != "pma1" || parts.next().is_some() {
            return None;
        }
        let exp_n: u64 = exp.parse().ok()?;
        if (chrono::Utc::now().timestamp() as u64) >= exp_n {
            return None;
        }
        let expect = self.hmac_hex(&format!("pma1|{}|{}", addr, exp_n));
        // Constant-time compare via HMAC of both strings (avoids timing
        // leaks without pulling in subtle directly).
        if self.hmac_hex(sig) != self.hmac_hex(&expect) {
            return None;
        }
        // Ownership is re-checked on every request so rotating the owner
        // immediately locks out tokens minted for the previous one.
        if self.owner.as_deref() != Some(addr) {
            return None;
        }
        Some(addr.to_string())
    }

    /// Append-only audit trail of signed terms acceptances.
    fn record_acceptance(&self, address: &str, ts: i64, signature: &str) {
        let path = self.dir.join("terms_accepted.json");
        let mut list: Vec<Value> = std::fs::read_to_string(&path)
            .ok()
            .and_then(|raw| serde_json::from_str(&raw).ok())
            .unwrap_or_default();
        list.push(json!({
            "address": address,
            "terms_version": TERMS_VERSION,
            "terms_sha256": Self::terms_sha256(),
            "challenge_timestamp": ts,
            "accepted_at": chrono::Utc::now().to_rfc3339(),
            "signature": signature,
        }));
        if let Ok(raw) = serde_json::to_string_pretty(&list) {
            std::fs::write(&path, raw).ok();
        }
    }
}

// ─── EIP-191 recovery ───────────────────────────────────────────────────

fn eip191_digest(message: &str) -> [u8; 32] {
    let prefix = format!("\x19Ethereum Signed Message:\n{}", message.len());
    let mut buf = Vec::with_capacity(prefix.len() + message.len());
    buf.extend_from_slice(prefix.as_bytes());
    buf.extend_from_slice(message.as_bytes());
    keccak256(&buf)
}

/// Recover the signer address from a 65-byte (r||s||v) hex signature over
/// an EIP-191 personal_sign digest. Accepts v ∈ {0,1,27,28}.
fn recover_address(message: &str, sig_hex: &str) -> Option<String> {
    let raw = hex::decode(sig_hex.trim_start_matches("0x")).ok()?;
    if raw.len() != 65 {
        return None;
    }
    let sig = Signature::try_from(&raw[..64]).ok()?;
    let v = raw[64];
    let recid = RecoveryId::try_from(if v >= 27 { v - 27 } else { v }).ok()?;
    let digest = eip191_digest(message);
    let vk = VerifyingKey::recover_from_prehash(&digest, &sig, recid).ok()?;
    Some(address_from_pubkey(&vk).to_lowercase())
}

// ─── Routes ─────────────────────────────────────────────────────────────

pub fn router(store: Arc<AccessStore>) -> Router {
    Router::new()
        .route("/access/info", get(info))
        .route("/access/challenge", get(challenge))
        .route("/access/verify", post(verify))
        .route("/access/check", get(check))
        .with_state(store)
}

/// Public: what the gate wants. Serves the authoritative terms text the
/// client must display — the signed message binds to its hash.
async fn info(State(store): State<Arc<AccessStore>>) -> impl IntoResponse {
    // Short owner hint (0x1234…abcd) so the gate can say WHO can enter
    // without the client hardcoding an address that config can change.
    let owner_hint = store.owner.as_deref().map(|o| {
        if o.len() == 42 { format!("{}…{}", &o[..6], &o[38..]) } else { o.to_string() }
    });
    Json(json!({
        "gated": !store.open,
        "mode": "owner-only",
        "ownerHint": owner_hint,
        "termsVersion": TERMS_VERSION,
        "termsSha256": AccessStore::terms_sha256(),
        "terms": TERMS_TEXT,
    }))
}

#[derive(Deserialize)]
struct ChallengeQuery {
    address: String,
}

async fn challenge(
    State(store): State<Arc<AccessStore>>,
    Query(q): Query<ChallengeQuery>,
) -> impl IntoResponse {
    let address = q.address.trim().to_lowercase();
    if !address.starts_with("0x") || address.len() != 42 {
        return (StatusCode::BAD_REQUEST, Json(json!({"error": "invalid address"}))).into_response();
    }
    let ts = chrono::Utc::now().timestamp();
    Json(json!({
        "address": address,
        "timestamp": ts,
        "message": store.message_for(&address, ts),
        "termsVersion": TERMS_VERSION,
        "termsSha256": AccessStore::terms_sha256(),
    }))
    .into_response()
}

#[derive(Deserialize)]
struct VerifyPayload {
    address: String,
    timestamp: i64,
    signature: String,
}

async fn verify(
    State(store): State<Arc<AccessStore>>,
    Json(p): Json<VerifyPayload>,
) -> impl IntoResponse {
    let address = p.address.trim().to_lowercase();
    let now = chrono::Utc::now().timestamp();
    if (now - p.timestamp).abs() > CHALLENGE_WINDOW_SECS {
        return (
            StatusCode::FORBIDDEN,
            Json(json!({"error": "challenge expired — request a fresh one"})),
        )
            .into_response();
    }
    let message = store.message_for(&address, p.timestamp);
    let recovered = match recover_address(&message, &p.signature) {
        Some(a) => a,
        None => {
            return (
                StatusCode::FORBIDDEN,
                Json(json!({"error": "signature recovery failed"})),
            )
                .into_response()
        }
    };
    if recovered != address {
        return (
            StatusCode::FORBIDDEN,
            Json(json!({"error": "signature does not match the claimed address"})),
        )
            .into_response();
    }
    if store.owner.as_deref() != Some(recovered.as_str()) {
        tracing::warn!(address = %recovered, "access DENIED — not the owner address");
        return (
            StatusCode::FORBIDDEN,
            Json(json!({
                "error": "access denied",
                "detail": "this deployment is private to its operator's sudo address",
            })),
        )
            .into_response();
    }
    store.record_acceptance(&recovered, p.timestamp, &p.signature);
    let (token, exp) = store.issue_token(&recovered);
    tracing::info!(address = %recovered, "owner signed in + accepted terms v{}", TERMS_VERSION);
    Json(json!({
        "token": token,
        "address": recovered,
        "exp": exp,
        "termsVersion": TERMS_VERSION,
    }))
    .into_response()
}

/// Cheap session probe for the frontend gate.
async fn check(State(store): State<Arc<AccessStore>>, req: Request) -> impl IntoResponse {
    match bearer_address(&store, &req) {
        Some(addr) => Json(json!({"ok": true, "address": addr, "termsVersion": TERMS_VERSION}))
            .into_response(),
        None => (
            StatusCode::UNAUTHORIZED,
            Json(json!({"ok": false, "error": "unauthorized"})),
        )
            .into_response(),
    }
}

fn bearer_address(store: &AccessStore, req: &Request) -> Option<String> {
    let header = req.headers().get(axum::http::header::AUTHORIZATION)?.to_str().ok()?;
    let token = header.strip_prefix("Bearer ").or_else(|| header.strip_prefix("bearer "))?;
    store.verify_token(token)
}

#[cfg(test)]
mod tests {
    use super::*;
    use k256::ecdsa::{signature::hazmat::PrehashSigner, SigningKey};

    fn test_store(owner: Option<String>) -> AccessStore {
        AccessStore {
            owner,
            secret: [7u8; 32],
            dir: std::env::temp_dir().join("polymarket-access-test"),
            open: false,
        }
    }

    fn sign_personal(key: &SigningKey, message: &str) -> String {
        let digest = eip191_digest(message);
        let (sig, recid): (Signature, RecoveryId) = key.sign_prehash(&digest).unwrap();
        let mut out = [0u8; 65];
        out[..32].copy_from_slice(&sig.to_bytes()[..32]);
        out[32..64].copy_from_slice(&sig.to_bytes()[32..64]);
        out[64] = 27 + u8::from(recid);
        format!("0x{}", hex::encode(out))
    }

    #[test]
    fn recover_roundtrip() {
        let key = SigningKey::random(&mut rand::rngs::OsRng);
        let addr = address_from_pubkey(key.verifying_key()).to_lowercase();
        let store = test_store(Some(addr.clone()));
        let ts = chrono::Utc::now().timestamp();
        let message = store.message_for(&addr, ts);
        let sig = sign_personal(&key, &message);
        assert_eq!(recover_address(&message, &sig), Some(addr.clone()));
        // A different message must not recover to the same address.
        let other = store.message_for(&addr, ts + 1);
        assert_ne!(recover_address(&other, &sig), recover_address(&message, &sig));
    }

    #[test]
    fn token_roundtrip_and_owner_binding() {
        let addr = "0x1111111111111111111111111111111111111111".to_string();
        let store = test_store(Some(addr.clone()));
        let (token, _exp) = store.issue_token(&addr);
        assert_eq!(store.verify_token(&token), Some(addr.clone()));
        // Tampered signature → rejected.
        let mut broken = token.clone();
        broken.pop();
        broken.push('0');
        assert_eq!(store.verify_token(&broken), None);
        // Owner rotated after issuance → old token dies immediately.
        let rotated = test_store(Some("0x2222222222222222222222222222222222222222".into()));
        // Same secret, different owner:
        assert_eq!(rotated.verify_token(&token), None);
        // No owner configured → fail closed.
        let unowned = test_store(None);
        assert_eq!(unowned.verify_token(&token), None);
    }
}

// ─── Middleware ─────────────────────────────────────────────────────────

/// Deny-by-default guard over the whole API. Public surface is exactly:
/// `/health` (pm2/activator liveness) and `/access/*` (the gate itself).
pub async fn guard(
    State(store): State<Arc<AccessStore>>,
    req: Request,
    next: Next,
) -> Response {
    if store.open {
        return next.run(req).await;
    }
    let path = req.uri().path();
    if path == "/health" || path.starts_with("/access/") {
        return next.run(req).await;
    }
    // CORS preflights carry no Authorization header by spec — let the CORS
    // layer answer them; the real request is still gated.
    if req.method() == axum::http::Method::OPTIONS {
        return next.run(req).await;
    }
    if bearer_address(&store, &req).is_some() {
        return next.run(req).await;
    }
    (
        StatusCode::UNAUTHORIZED,
        Json(json!({
            "error": "unauthorized",
            "gate": "polymarket-access",
            "detail": "owner-only deployment — sign in via /access/challenge + /access/verify",
        })),
    )
        .into_response()
}
