//! Backend order placement.
//!
//! End-to-end flow:
//!   1. Caller submits a structured place-order request: `{eoa, creds, args}`
//!   2. We compute makerAmount / takerAmount in USDC base units (10^6) the
//!      same way the frontend's polymarketOrderSigning.ts does — same
//!      rounding rules so server and client agree on exact integer values.
//!   3. Build `OrderInput`, hash via `order_signing::order_digest`, sign
//!      with the backend's per-EOA key.
//!   4. Construct the full `SignedOrder` JSON payload (Order struct +
//!      signature) and HMAC-sign the L2 request (timestamp+POST+/order+body).
//!   5. POST to `https://clob.polymarket.com/order`.
//!   6. Return the CLOB response verbatim so the caller can read order id,
//!      status, fills, etc.
//!
//! All sizing math is done in integer microUSDC to avoid float drift —
//! Polymarket rejects orders whose base-unit amounts don't match what the
//! signature was computed over (the digest depends on the integer values).

use anyhow::{anyhow, Context, Result};
use base64::Engine;
use hmac::{Hmac, Mac};
use rand::rngs::OsRng;
use rand::RngCore;
use serde::{Deserialize, Serialize};
use sha2::Sha256;

use crate::deposit_wallet::derive_deposit_wallet;
use crate::order_signing::{
    order_digest, poly1271_outer_digest, wrap_poly1271_signature, ExchangeKind, OrderInput,
};
use crate::signer::SignerStore;

const CLOB_API: &str = "https://clob.polymarket.com";

// ─── Request / response shapes ──────────────────────────────────────────

#[derive(Debug, Clone, Deserialize)]
pub struct ClobCreds {
    #[serde(rename = "apiKey")]
    pub api_key: String,
    pub secret: String,     // base64url-encoded HMAC secret (from CLOB derive-key)
    pub passphrase: String,
}

#[derive(Debug, Clone, Copy, Deserialize)]
#[serde(rename_all = "UPPERCASE")]
pub enum OrderSide {
    Buy,
    Sell,
}

#[derive(Debug, Clone, Copy, Deserialize)]
#[serde(rename_all = "UPPERCASE")]
pub enum OrderTimeInForce {
    Gtc, // good-til-cancel (default)
    Gtd, // good-til-date
    Fok, // fill-or-kill
    Fak, // fill-and-kill
}

#[derive(Debug, Clone, Deserialize)]
pub struct PlaceOrderArgs {
    /// CLOB outcome token id (binary YES or NO branch).
    #[serde(rename = "tokenId")]
    pub token_id: String,
    pub side: OrderSide,
    /// Limit price in [0.0, 1.0].
    pub price: f64,
    /// Order size in shares (decimal — converted to base units server-side).
    pub size: f64,
    /// Fee rate in basis points. Unused in V2 (the exchange contract
    /// dropped per-order fee from the signed struct), kept on the wire
    /// only so older frontend builds don't fail deserialization.
    #[serde(rename = "feeRateBps", default)]
    #[allow(dead_code)]
    pub fee_rate_bps: u32,
    /// Optional GTD expiration (unix seconds). 0 for GTC.
    #[serde(default)]
    pub expiration: u64,
    /// 0 = EOA, 1 = POLY_PROXY, 2 = POLY_GNOSIS_SAFE, 3 = POLY_1271 (V2
    /// deposit wallet). Defaults to 3 — Polymarket V2 rejects every other
    /// maker type with "maker address not allowed". When sigType=3 we
    /// derive the maker ourselves from `eoa` (CREATE2), ignoring whatever
    /// the frontend sent (it typically still has the stale Safe address).
    #[serde(rename = "signatureType", default = "default_sig_type")]
    pub signature_type: u8,
    /// CLOB order type — defaults to GTC.
    #[serde(rename = "orderType", default = "default_order_type")]
    pub order_type: OrderTimeInForce,
    /// `true` if this market lives on the NegRisk exchange; selects which
    /// EIP-712 domain we hash against.
    #[serde(rename = "negRisk", default)]
    pub neg_risk: bool,
    /// Proxy (maker) address — Safe or POLY_PROXY that holds the funds.
    /// Distinct from `eoa`, which is the *signer* address. For sigType 0
    /// they collapse to the same value.
    pub maker: String,
}

fn default_sig_type() -> u8 { 3 }
fn default_order_type() -> OrderTimeInForce { OrderTimeInForce::Gtc }

#[derive(Debug, Clone, Deserialize)]
pub struct PlaceOrderRequest {
    pub eoa: String,
    pub creds: ClobCreds,
    pub args: PlaceOrderArgs,
}

/// CLOB-shaped Order struct as sent in the `order` field of POST /order.
/// All numeric fields are stringified base-unit integers — Polymarket's
/// API rejects floats, and the EIP-712 digest is computed over the same
/// integer values.
///
/// Note on `side`: the HTTP body uses the STRING form ("BUY" / "SELL"),
/// even though the EIP-712 typed data uses uint8 (0 / 1) for the hash.
/// This split matches @polymarket/clob-client's `orderToJson` — and is the
/// root cause of the "Invalid order payload" rejection we hit when sending
/// the numeric form.
/// V2 JSON body for the inner `order` field. Mirrors
/// @polymarket/clob-client-v2's `orderToJsonV2` exactly — including the
/// quirk that `taker` and `expiration` appear in the HTTP body even though
/// they're NOT part of the V2 EIP-712 hash. Sending these fields keeps
/// the matcher happy; omitting them surfaces as a 400.
#[derive(Debug, Clone, Serialize)]
pub struct ClobOrder {
    /// JSON number, not string. clob-client-v2 emits salt via
    /// `parseInt(order.salt, 10)` — sending it quoted trips the
    /// "Invalid order payload" 400. Bounded to ≤ 10 digits (see
    /// `random_salt`) so it fits in u64.
    pub salt: u64,
    pub maker: String,
    pub signer: String,
    /// Body-only — V2 EIP-712 hash no longer includes taker.
    pub taker: String,
    #[serde(rename = "tokenId")]
    pub token_id: String,
    #[serde(rename = "makerAmount")]
    pub maker_amount: String,
    #[serde(rename = "takerAmount")]
    pub taker_amount: String,
    /// "BUY" or "SELL" — string in the HTTP body (uint8 in the EIP-712 hash).
    pub side: String,
    #[serde(rename = "signatureType")]
    pub signature_type: u8,
    /// Unix milliseconds as a decimal string — matches V2's
    /// `Date.now().toString()`. Part of the EIP-712 hash.
    pub timestamp: String,
    /// Body-only — V2 EIP-712 hash no longer includes expiration.
    /// "0" means no expiration.
    pub expiration: String,
    /// 32-byte hex (0x + 64 chars). All-zero for unbranded orders.
    pub metadata: String,
    /// 32-byte hex builder code. All-zero for non-affiliate orders.
    pub builder: String,
    pub signature: String,
}

// ─── Amount math (mirrors frontend's polymarketOrderSigning.ts) ─────────

fn round2_normal(x: f64) -> f64 { (x * 100.0).round() / 100.0 }
fn round2_down(x: f64) -> f64 { (x * 100.0).floor() / 100.0 }
/// Round to 4 decimal places — the precision Polymarket allows for the
/// USDC notional on a tickSize=0.01 market. Rounding the notional to 2dp
/// (= whole cents) makes `makerAmount / takerAmount` land on a price
/// that's NOT a valid tick (e.g. 1.00 / 3.45 = 0.2898…), which trips
/// Polymarket's "Price breaks minimum tick size rule" 400. 4dp keeps
/// `notional = price_tick × size_2dp` exact for tickSize=0.01.
fn round4_down(x: f64) -> f64 { (x * 10_000.0).floor() / 10_000.0 }

/// Convert a decimal USDC amount (≤ 6 fractional digits) to base units
/// (×10^6) as a u128 without introducing float error. Polymarket cares
/// about the EXACT integer — a single-unit drift breaks the signature.
fn to_base_units_u128(amount: f64) -> Result<u128> {
    let scaled = (amount * 1_000_000.0).round();
    if scaled < 0.0 || !scaled.is_finite() {
        return Err(anyhow!("amount out of range: {}", amount));
    }
    Ok(scaled as u128)
}

fn compute_amounts(side: OrderSide, price: f64, size: f64) -> Result<(u128, u128)> {
    if !(0.0..=1.0).contains(&price) {
        return Err(anyhow!("price must be in [0, 1], got {}", price));
    }
    if size <= 0.0 {
        return Err(anyhow!("size must be > 0, got {}", size));
    }
    let p = round2_normal(price);
    let s = round2_down(size);
    // USDC notional rounds to 4dp (not 2dp): keeps the implied price
    // `notional / size = p` exact down to the tick. With 2dp we'd get
    // e.g. p=0.29, s=3.45, notional=round(1.0005,2)=1.00, implied
    // 1.00/3.45 = 0.2898… → Polymarket rejects as off-tick.
    let usdc = round4_down(s * p);
    match side {
        OrderSide::Buy => {
            // maker pays USDC, takes shares
            Ok((to_base_units_u128(usdc)?, to_base_units_u128(s)?))
        }
        OrderSide::Sell => {
            // maker gives shares, takes USDC
            Ok((to_base_units_u128(s)?, to_base_units_u128(usdc)?))
        }
    }
}

/// Random salt as a small decimal string (≤ 10 digits) — matches
/// py-clob-client's `round(random.random() * 10**10)` and stays well
/// inside Number.MAX_SAFE_INTEGER so @polymarket/clob-client's
/// `parseInt(salt)` on the server side doesn't truncate. The CLOB
/// rejects salts that overflow its int parser with the opaque
/// "Invalid order payload" 400 — full uint256 salts (78 decimal digits)
/// trigger this. The on-chain Exchange contract accepts any uint256
/// salt, so going small is structurally safe; the field exists only to
/// make per-order hashes unique, not for security.
fn random_salt() -> String {
    let mut bytes = [0u8; 8];
    OsRng.fill_bytes(&mut bytes);
    let v = u64::from_be_bytes(bytes) % 10_000_000_000u64; // ≤ 10 digits
    v.to_string()
}


// ─── Place order pipeline ──────────────────────────────────────────────

/// Backend-owned CLOB API key minted against the backend signer's EOA.
/// Polymarket's CLOB rejects orders whose `POLY_ADDRESS` / `order.signer`
/// don't match the API key's bound EOA — so we mint a separate key tied
/// to the backend signer and ignore whatever creds the frontend hands us.
#[derive(Debug, Clone)]
pub struct BackendCreds {
    pub api_key: String,
    pub secret: String,
    pub passphrase: String,
}

/// Mint or look up a backend-owned CLOB API key. For Polymarket V2's
/// POLY_1271 (deposit wallet) flow, the API key must be bound to the
/// *deposit wallet address*, not the backend signer EOA — V2 enforces
/// `order.signer == api_key.address` (we saw the "the order signer
/// address has to be the address of the API KEY" 400 when this didn't
/// hold). We use the V2 `address` override in ClobAuth: hash the typed
/// data against the deposit-wallet address, sign with the backend signer
/// EOA, and pass `POLY_ADDRESS = deposit_wallet`. Polymarket recovers the
/// EOA from the signature and verifies it's authorized for the claimed
/// address.
///
/// For non-POLY_1271 paths (legacy V1, no longer reachable since we
/// always override to sigType=3), `bind_address` falls back to the
/// backend signer's own EOA.
pub async fn ensure_backend_creds(
    http: &reqwest::Client,
    signer_store: &SignerStore,
    eoa: &str,
) -> Result<(BackendCreds, String)> {
    let backend_addr = signer_store.signer_address(eoa)?;
    // Make sure the deposit wallet exists on-chain before we attempt any
    // CLOB operations (it's the maker for V2 POLY_1271 orders). Gasless,
    // via gamma SIWE + relayer WALLET-CREATE — no Polymarket UI step.
    // Idempotent + in-process-cached.
    let _wallet = crate::relayer::ensure_deposit_wallet_deployed(http, signer_store, eoa).await?;
    // Mint the API key against the backend signer's *EOA*, not the wallet.
    // V2's /auth/api-key + /auth/derive-api-key only accept signatures
    // where the recovered EOA equals the typed-data `address` field —
    // attempting to bind directly to a smart-contract wallet trips
    // 401 "Invalid L1 Request headers". For POLY_1271 orders Polymarket
    // re-derives the expected order.signer (the deposit wallet) from the
    // api_key.address via CREATE2, so the EOA binding is the right one.
    let bind_address = backend_addr.clone();
    let timestamp = chrono::Utc::now().timestamp().to_string();
    let nonce: u64 = 0;
    let digest = crate::clob_auth::clob_auth_digest(&bind_address, &timestamp, nonce)?;
    let sig = signer_store.sign_digest(eoa, &digest)?;
    let sig_hex = format!("0x{}", hex::encode(sig));

    async fn call(
        http: &reqwest::Client,
        method: reqwest::Method,
        path: &str,
        addr: &str,
        sig_hex: &str,
        timestamp: &str,
        nonce: u64,
    ) -> Result<(reqwest::StatusCode, serde_json::Value)> {
        let url = format!("{}{}", CLOB_API, path);
        let r = http
            .request(method, &url)
            .header("POLY_ADDRESS", addr)
            .header("POLY_SIGNATURE", sig_hex)
            .header("POLY_TIMESTAMP", timestamp)
            .header("POLY_NONCE", nonce.to_string())
            .send()
            .await
            .context("clob auth upstream")?;
        let status = r.status();
        let text = r.text().await.unwrap_or_default();
        let json: serde_json::Value =
            serde_json::from_str(&text).unwrap_or_else(|_| serde_json::json!({"raw": text}));
        Ok((status, json))
    }

    let (status, body) = call(
        http,
        reqwest::Method::GET,
        "/auth/derive-api-key",
        &bind_address,
        &sig_hex,
        &timestamp,
        nonce,
    )
    .await?;
    let body = if status.is_success() {
        body
    } else {
        let (s2, b2) = call(
            http,
            reqwest::Method::POST,
            "/auth/api-key",
            &bind_address,
            &sig_hex,
            &timestamp,
            nonce,
        )
        .await?;
        if !s2.is_success() {
            return Err(anyhow!("mint backend creds HTTP {}: {}", s2, b2));
        }
        b2
    };
    let api_key = body
        .get("apiKey")
        .and_then(|v| v.as_str())
        .ok_or_else(|| anyhow!("creds: missing apiKey"))?
        .to_string();
    let secret = body
        .get("secret")
        .and_then(|v| v.as_str())
        .ok_or_else(|| anyhow!("creds: missing secret"))?
        .to_string();
    let passphrase = body
        .get("passphrase")
        .and_then(|v| v.as_str())
        .ok_or_else(|| anyhow!("creds: missing passphrase"))?
        .to_string();
    Ok((BackendCreds { api_key, secret, passphrase }, backend_addr))
}

pub async fn place_order(
    http: &reqwest::Client,
    signer_store: &SignerStore,
    req: PlaceOrderRequest,
) -> Result<serde_json::Value> {
    // 1. Compute integer amounts.
    let (maker_amount, taker_amount) =
        compute_amounts(req.args.side, req.args.price, req.args.size)?;

    // 2. Resolve backend creds + signer address. We IGNORE req.creds
    //    entirely — those were minted against the user's EOA and CLOB
    //    won't accept orders signed by anyone else for that key. Backend
    //    creds are minted against the backend signer's EOA, which the
    //    Safe/proxy recognizes as an authorized owner.
    let (backend_creds, signer_addr) =
        ensure_backend_creds(http, signer_store, &req.eoa).await?;

    // 2a. POLY_1271 (sigType 3) override: Polymarket V2 phased out Gnosis
    //     Safe makers and now requires deposit wallets. The wallet address
    //     is a deterministic CREATE2 of (factory, implementation, EOA), so
    //     we derive it ourselves rather than trusting whatever maker the
    //     frontend hands us — that maker is typically the user's stale
    //     Safe address, which V2 rejects with "maker address not allowed".
    //
    //     We *always* upgrade to sigType=3 here regardless of what the
    //     frontend sent. The legacy 0/1/2 paths universally fail against
    //     V2's exchange ("maker address not allowed" for any maker type
    //     other than a deposit wallet, per probing). Once sigType=3 is the
    //     only viable path, hardcoding it server-side keeps the frontend
    //     ignorant of the V2 transition.
    let mut effective_sig_type = req.args.signature_type;
    if effective_sig_type != 3 {
        tracing::info!(
            from = effective_sig_type,
            "overriding to POLY_1271 (sigType=3) — V2 exchange rejects other maker types",
        );
        effective_sig_type = 3;
    }
    // Derive the deposit wallet from the BACKEND SIGNER's EOA, not the
    // user's. The wallet's EIP-1271 verifier auto-trusts the EOA used as
    // its CREATE2 salt — so deriving from the backend signer means the
    // wallet recognizes the backend signer's signatures with no on-chain
    // authorization step. User funds USDC into this address (shown in UI)
    // and the backend can autonomously sign and place orders.
    let derived_maker = derive_deposit_wallet(&signer_addr)?;
    let effective_maker = derived_maker.clone();
    tracing::info!(
        backend_signer = %signer_addr,
        deposit_wallet = %derived_maker,
        "derived V2 deposit wallet (CREATE2 from backend signer EOA)",
    );

    // 3. Build OrderInput for the digest.
    let salt = random_salt();
    let side_u8: u8 = match req.args.side {
        OrderSide::Buy => 0,
        OrderSide::Sell => 1,
    };
    let exchange = if req.args.neg_risk {
        ExchangeKind::Negrisk
    } else {
        ExchangeKind::Standard
    };
    // V2 uses millisecond unix timestamp as the per-order entropy bumper
    // (replaces V1's `nonce`). Matches clob-client-v2's `Date.now().toString()`.
    let timestamp_ms = chrono::Utc::now().timestamp_millis().to_string();
    const ZERO_BYTES32: &str =
        "0x0000000000000000000000000000000000000000000000000000000000000000";

    let order_input = OrderInput {
        salt: salt.clone(),
        maker: effective_maker.clone(),
        // signer field semantics:
        //   sigType 0 (EOA)         — signer = maker (trading EOA itself).
        //   sigType 1 (POLY_PROXY)  — signer = the EOA that owns the proxy.
        //   sigType 2 (SAFE)        — signer = the Safe-owner EOA whose key
        //                             actually signed. Backend setup: that's
        //                             signer_addr; ecrecover must match this.
        //   sigType 3 (POLY_1271)   — signer = the smart-contract wallet
        //                             (also the maker). Wallet uses EIP-1271
        //                             to verify a wrapped sig where the
        //                             *inner* signer is the EOA owner.
        signer: match effective_sig_type {
            0 => effective_maker.clone(),
            3 => effective_maker.clone(),
            _ => signer_addr.clone(),
        },
        token_id: req.args.token_id.clone(),
        maker_amount: maker_amount.to_string(),
        taker_amount: taker_amount.to_string(),
        side: side_u8,
        signature_type: effective_sig_type,
        timestamp: timestamp_ms.clone(),
        metadata: ZERO_BYTES32.to_string(),
        builder: ZERO_BYTES32.to_string(),
        exchange,
    };

    // 4. Sign.
    // POLY_1271: outer digest is the Solady TypedDataSign wrapper that the
    // deposit wallet's EIP-1271 verifier reconstructs. We sign that, then
    // wrap (innerSig || app_sep || contents_hash || ORDER_TYPE_STRING || len_be).
    // Other sigTypes (EOA / POLY_PROXY / SAFE): straight EIP-712 order digest.
    let sig_hex = if effective_sig_type == 3 {
        let (outer, app_sep, contents) =
            poly1271_outer_digest(&order_input, &effective_maker)?;
        let inner = signer_store.sign_digest(&req.eoa, &outer)?;
        wrap_poly1271_signature(&inner, &app_sep, &contents)
    } else {
        let digest = order_digest(&order_input)?;
        let sig = signer_store.sign_digest(&req.eoa, &digest)?;
        format!("0x{}", hex::encode(sig))
    };

    let salt_u64: u64 = order_input.salt.parse().map_err(|e| {
        anyhow!("salt parse to u64 failed (must fit in u64 for JSON number): {}", e)
    })?;
    let order = ClobOrder {
        salt: salt_u64,
        // Lowercased to mirror clob-client-v2 / py-clob-client behavior.
        // The EIP-712 hash normalizes case in encode_address, so the
        // signature stays valid against either form.
        maker: order_input.maker.to_lowercase(),
        signer: order_input.signer.to_lowercase(),
        taker: "0x0000000000000000000000000000000000000000".to_string(),
        token_id: order_input.token_id,
        maker_amount: order_input.maker_amount,
        taker_amount: order_input.taker_amount,
        // HTTP body wants "BUY" / "SELL" strings — the digest was computed
        // over the uint8 form in `order_input.side`, signature stays valid.
        side: match order_input.side {
            0 => "BUY".to_string(),
            1 => "SELL".to_string(),
            other => return Err(anyhow!("invalid side {} (must be 0=BUY or 1=SELL)", other)),
        },
        signature_type: order_input.signature_type,
        timestamp: order_input.timestamp,
        expiration: req.args.expiration.to_string(),
        metadata: order_input.metadata,
        builder: order_input.builder,
        signature: sig_hex,
    };

    // 5. POST /order to Polymarket CLOB with L2 HMAC headers.
    // V2 always sends `deferExec` and `postOnly` at top level, even when
    // false. Omitting them has been observed to produce the same opaque
    // "Invalid order payload" 400 — V2's validator is strict about the
    // outer envelope shape, not just the inner order struct.
    let body = serde_json::json!({
        "order": order,
        "owner": backend_creds.api_key,
        "orderType": match req.args.order_type {
            OrderTimeInForce::Gtc => "GTC",
            OrderTimeInForce::Gtd => "GTD",
            OrderTimeInForce::Fok => "FOK",
            OrderTimeInForce::Fak => "FAK",
        },
        "deferExec": false,
        "postOnly": false,
    });
    let body_str = serde_json::to_string(&body)?;
    let timestamp = chrono::Utc::now().timestamp().to_string();
    let path = "/order";
    let hmac_sig = build_hmac_signature(&backend_creds.secret, &timestamp, "POST", path, &body_str)?;

    // Log the outbound body and the upstream response side-by-side so a
    // rejection like "Invalid order payload" can be diagnosed without
    // wiring a packet capture. Stays at info because order-placement is
    // a relatively rare event and the operator typically wants to see
    // every one in the early operational window.
    tracing::info!(
        path = path,
        sig_type = effective_sig_type,
        neg_risk = req.args.neg_risk,
        maker = %order.maker,
        signer = %order.signer,
        order_body = %body_str,
        "POST /order outbound",
    );

    let resp = http
        .post(format!("{}{}", CLOB_API, path))
        // POLY_ADDRESS = the address the api_key was bound against, which
        // is the backend signer's *EOA* (mint endpoint forbids non-EOA
        // bindings). order.signer is still the deposit wallet — Polymarket
        // re-derives `expected_signer = depositWallet(POLY_ADDRESS)` via
        // CREATE2 for POLY_1271 and compares against order.signer.
        .header("POLY_ADDRESS", &signer_addr)
        .header("POLY_SIGNATURE", hmac_sig)
        .header("POLY_TIMESTAMP", timestamp)
        .header("POLY_API_KEY", &backend_creds.api_key)
        .header("POLY_PASSPHRASE", &backend_creds.passphrase)
        .header("Content-Type", "application/json")
        .body(body_str)
        .send()
        .await
        .context("post /order upstream")?;

    let status = resp.status();
    let text = resp.text().await.unwrap_or_default();
    let json: serde_json::Value = serde_json::from_str(&text)
        .unwrap_or_else(|_| serde_json::json!({"raw": text}));
    tracing::info!(%status, response = %json, "POST /order response");
    if !status.is_success() {
        return Err(anyhow!("CLOB /order HTTP {}: {}", status, json));
    }
    Ok(json)
}

// ─── HMAC L2 auth (mirrors clobClient.ts buildL2Headers) ────────────────

fn build_hmac_signature(
    secret_b64url: &str,
    timestamp: &str,
    method: &str,
    path: &str,
    body: &str,
) -> Result<String> {
    // Polymarket issues secrets in base64url. Normalize to standard base64
    // before decoding so the RustCrypto base64 engine accepts it.
    let normalized: String = secret_b64url
        .chars()
        .map(|c| match c {
            '-' => '+',
            '_' => '/',
            _ => c,
        })
        .collect();
    // Re-pad if needed.
    let padded = match normalized.len() % 4 {
        2 => format!("{}==", normalized),
        3 => format!("{}=", normalized),
        _ => normalized,
    };
    let key_bytes = base64::engine::general_purpose::STANDARD
        .decode(&padded)
        .context("decode HMAC secret")?;

    let msg = format!("{}{}{}{}", timestamp, method, path, body);
    type HmacSha256 = Hmac<Sha256>;
    let mut mac = HmacSha256::new_from_slice(&key_bytes)
        .map_err(|e| anyhow!("hmac init: {}", e))?;
    mac.update(msg.as_bytes());
    let digest = mac.finalize().into_bytes();

    // Polymarket expects url-safe base64 *with* the `=` padding kept.
    let standard = base64::engine::general_purpose::STANDARD.encode(digest);
    let url_safe: String = standard
        .chars()
        .map(|c| match c {
            '+' => '-',
            '/' => '_',
            _ => c,
        })
        .collect();
    Ok(url_safe)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn buy_amounts_are_integers_in_base_units() {
        let (m, t) = compute_amounts(OrderSide::Buy, 0.55, 10.0).unwrap();
        // BUY @ 0.55, size 10 → maker pays $5.50, takes 10 shares.
        assert_eq!(m, 5_500_000);   // $5.50 in microUSDC
        assert_eq!(t, 10_000_000);  // 10 shares as 6-decimal int
    }

    #[test]
    fn sell_amounts_invert_buy() {
        let (m, t) = compute_amounts(OrderSide::Sell, 0.55, 10.0).unwrap();
        // SELL @ 0.55, size 10 → maker gives 10 shares, takes $5.50.
        assert_eq!(m, 10_000_000);
        assert_eq!(t, 5_500_000);
    }

    #[test]
    fn rounds_down_below_2dp() {
        // 13.3333 shares × 0.05 = 0.666665 → rounds down to 0.66
        let (m, _) = compute_amounts(OrderSide::Buy, 0.05, 13.3333).unwrap();
        assert_eq!(m, 660_000);
    }

    #[test]
    fn rejects_out_of_range_price() {
        assert!(compute_amounts(OrderSide::Buy, 1.5, 10.0).is_err());
        assert!(compute_amounts(OrderSide::Buy, -0.01, 10.0).is_err());
    }

    #[test]
    fn salt_is_non_empty_small_decimal() {
        let s = random_salt();
        assert!(!s.is_empty());
        assert!(s.chars().all(|c| c.is_ascii_digit()));
        // Must fit in Number.MAX_SAFE_INTEGER (2^53 - 1 = 9007199254740991,
        // 16 digits) so the server's `parseInt(salt)` doesn't truncate. We
        // cap at 10^10 so this is plenty of headroom.
        assert!(s.len() <= 11, "salt too large: {} (would overflow JS Number)", s);
    }

    #[test]
    fn clob_order_serializes_side_as_string() {
        // Polymarket's POST /order rejects numeric side with "Invalid order
        // payload"; the JSON body must use "BUY" / "SELL". The EIP-712 hash
        // still uses uint8 — only the HTTP body shape differs.
        let order = ClobOrder {
            salt: 1,
            maker: "0x0000000000000000000000000000000000000001".into(),
            signer: "0x0000000000000000000000000000000000000002".into(),
            taker: "0x0000000000000000000000000000000000000000".into(),
            token_id: "9".into(),
            maker_amount: "1000000".into(),
            taker_amount: "2000000".into(),
            side: "BUY".into(),
            signature_type: 2,
            timestamp: "1780000000000".into(),
            expiration: "0".into(),
            metadata: "0x0000000000000000000000000000000000000000000000000000000000000000".into(),
            builder: "0x0000000000000000000000000000000000000000000000000000000000000000".into(),
            signature: "0xdead".into(),
        };
        let v = serde_json::to_value(&order).unwrap();
        assert_eq!(v["side"], serde_json::json!("BUY"));
        assert_eq!(v["signatureType"], serde_json::json!(2));
        // salt must be emitted as a JSON Number, not a String — Polymarket's
        // body validator rejects quoted salt with "Invalid order payload".
        assert!(v["salt"].is_number(), "salt must serialize as JSON number, got {:?}", v["salt"]);
        assert_eq!(v["salt"], serde_json::json!(1));
    }

    #[test]
    fn hmac_matches_reference_vector() {
        // Vector cross-checked against @polymarket/clob-client buildPolyHmacSignature
        // and py-clob-client. Same secret/timestamp/method/path/body → same digest.
        let secret = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=";
        let sig = build_hmac_signature(secret, "1000000", "test-sign", "/orders", "{\"hash\": \"0x123\"}").unwrap();
        assert_eq!(sig, "ZwAdJKvoYRlEKDkNMwd5BuwNNtg93kNaR_oU2HrfVvc=");
    }
}
