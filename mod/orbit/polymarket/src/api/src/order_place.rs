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

use crate::order_signing::{order_digest, ExchangeKind, OrderInput};
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
    /// Fee rate in basis points. 0 for taker-only sessions.
    #[serde(rename = "feeRateBps", default)]
    pub fee_rate_bps: u32,
    /// Optional GTD expiration (unix seconds). 0 for GTC.
    #[serde(default)]
    pub expiration: u64,
    /// 0 = EOA, 1 = POLY_PROXY (Maker), 2 = POLY_GNOSIS_SAFE. Default 2
    /// since the current proxy type in this codebase is Safe.
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

fn default_sig_type() -> u8 { 2 }
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
#[derive(Debug, Clone, Serialize)]
pub struct ClobOrder {
    pub salt: String,
    pub maker: String,
    pub signer: String,
    pub taker: String,
    #[serde(rename = "tokenId")]
    pub token_id: String,
    #[serde(rename = "makerAmount")]
    pub maker_amount: String,
    #[serde(rename = "takerAmount")]
    pub taker_amount: String,
    pub expiration: String,
    pub nonce: String,
    #[serde(rename = "feeRateBps")]
    pub fee_rate_bps: String,
    /// "BUY" or "SELL" — string in the HTTP body (uint8 in the EIP-712 hash).
    pub side: String,
    #[serde(rename = "signatureType")]
    pub signature_type: u8,
    pub signature: String,
}

// ─── Amount math (mirrors frontend's polymarketOrderSigning.ts) ─────────

fn round2_normal(x: f64) -> f64 { (x * 100.0).round() / 100.0 }
fn round2_down(x: f64) -> f64 { (x * 100.0).floor() / 100.0 }

/// Convert a 2dp decimal amount to USDC base units (×10^6) as a u128
/// without introducing float error. Polymarket cares about the EXACT
/// integer — a single-unit drift breaks the signature.
fn to_base_units_u128(amount_2dp: f64) -> Result<u128> {
    // amount is rounded to 2 decimals already, so multiplying by 1e6 and
    // rounding is safe within f64's 52-bit mantissa for any realistic order
    // size (10^15 microUSDC ≈ $1B, well below 2^52).
    let scaled = (amount_2dp * 1_000_000.0).round();
    if scaled < 0.0 || !scaled.is_finite() {
        return Err(anyhow!("amount out of range: {}", amount_2dp));
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
    let usdc = round2_down(s * p);
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

/// Random 256-bit salt as a decimal string (matches the frontend's
/// `BigInt(randomHex)` shape — Polymarket's signer expects decimal).
fn random_salt() -> String {
    let mut bytes = [0u8; 32];
    OsRng.fill_bytes(&mut bytes);
    // Render as decimal via 4×u64 limbs.
    let mut limbs: [u64; 4] = [0; 4];
    for i in 0..4 {
        let mut b = [0u8; 8];
        b.copy_from_slice(&bytes[i * 8..(i + 1) * 8]);
        limbs[3 - i] = u64::from_be_bytes(b);
    }
    u256_limbs_to_decimal_str(limbs)
}

fn u256_limbs_to_decimal_str(mut limbs: [u64; 4]) -> String {
    if limbs.iter().all(|l| *l == 0) {
        return "0".to_string();
    }
    let mut digits = Vec::with_capacity(78);
    while limbs.iter().any(|l| *l != 0) {
        let mut rem: u128 = 0;
        for limb in limbs.iter_mut().rev() {
            let cur = (rem << 64) | (*limb as u128);
            *limb = (cur / 10) as u64;
            rem = cur % 10;
        }
        digits.push((b'0' + rem as u8) as char);
    }
    digits.into_iter().rev().collect()
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

/// Mint or look up a backend-owned CLOB API key for `eoa`. Hits
/// /auth/derive-api-key first (returns the existing key for this backend
/// EOA if one was ever minted), falls back to /auth/api-key if not.
pub async fn ensure_backend_creds(
    http: &reqwest::Client,
    signer_store: &SignerStore,
    eoa: &str,
) -> Result<(BackendCreds, String)> {
    let backend_addr = signer_store.signer_address(eoa)?;
    let timestamp = chrono::Utc::now().timestamp().to_string();
    let nonce: u64 = 0;
    let digest = crate::clob_auth::clob_auth_digest(&backend_addr, &timestamp, nonce)?;
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
        &backend_addr,
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
            &backend_addr,
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
    //    Safe (proxy) recognizes as an authorized owner.
    let (backend_creds, signer_addr) =
        ensure_backend_creds(http, signer_store, &req.eoa).await?;

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
    let order_input = OrderInput {
        salt: salt.clone(),
        maker: req.args.maker.clone(),
        // signer field semantics:
        //   sigType 0 (EOA)         — signer = maker (trading EOA itself).
        //   sigType 1 (POLY_PROXY)  — signer = the EOA that owns the proxy.
        //                             predictProxy(signer) == maker AND
        //                             ecrecover(sig) == signer.
        //   sigType 2 (SAFE)        — signer = the Safe-owner EOA whose key
        //                             actually signed. CLOB validates
        //                             ecrecover(sig) == signer AND that
        //                             signer is a registered Safe owner on
        //                             maker (via eth_call to
        //                             Safe.isValidSignature). For our
        //                             backend setup that's signer_addr,
        //                             not req.eoa — the backend key signs
        //                             the digest, so order.signer must match
        //                             what ECDSA recovery yields.
        signer: if req.args.signature_type == 0 {
            req.args.maker.clone()
        } else {
            signer_addr.clone()
        },
        taker: "0x0000000000000000000000000000000000000000".to_string(),
        token_id: req.args.token_id.clone(),
        maker_amount: maker_amount.to_string(),
        taker_amount: taker_amount.to_string(),
        expiration: req.args.expiration.to_string(),
        nonce: "0".to_string(),
        fee_rate_bps: req.args.fee_rate_bps.to_string(),
        side: side_u8,
        signature_type: req.args.signature_type,
        exchange,
    };

    // 4. Sign.
    let digest = order_digest(&order_input)?;
    let sig = signer_store.sign_digest(&req.eoa, &digest)?;
    let sig_hex = format!("0x{}", hex::encode(sig));

    let order = ClobOrder {
        salt: order_input.salt,
        maker: order_input.maker,
        signer: order_input.signer,
        taker: order_input.taker,
        token_id: order_input.token_id,
        maker_amount: order_input.maker_amount,
        taker_amount: order_input.taker_amount,
        expiration: order_input.expiration,
        nonce: order_input.nonce,
        fee_rate_bps: order_input.fee_rate_bps,
        // HTTP body wants "BUY" / "SELL" strings — the digest was already
        // computed over the uint8 form in `order_input.side` above, so the
        // signature stays valid.
        side: match order_input.side {
            0 => "BUY".to_string(),
            1 => "SELL".to_string(),
            other => return Err(anyhow!("invalid side {} (must be 0=BUY or 1=SELL)", other)),
        },
        signature_type: order_input.signature_type,
        signature: sig_hex,
    };

    // 5. POST /order to Polymarket CLOB with L2 HMAC headers.
    let body = serde_json::json!({
        "order": order,
        "owner": backend_creds.api_key,
        "orderType": match req.args.order_type {
            OrderTimeInForce::Gtc => "GTC",
            OrderTimeInForce::Gtd => "GTD",
            OrderTimeInForce::Fok => "FOK",
            OrderTimeInForce::Fak => "FAK",
        },
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
        sig_type = req.args.signature_type,
        neg_risk = req.args.neg_risk,
        maker = %order.maker,
        signer = %order.signer,
        order_body = %body_str,
        "POST /order outbound",
    );

    let resp = http
        .post(format!("{}{}", CLOB_API, path))
        // POLY_ADDRESS = backend signer EOA. Must match the address
        // bound to backend_creds.api_key (which IS the backend signer
        // — that's how ensure_backend_creds mints it). order.signer
        // matches too. Sign-with-backend + key-owned-by-backend = no
        // mismatch errors.
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
    fn salt_is_non_empty_decimal() {
        let s = random_salt();
        assert!(!s.is_empty());
        assert!(s.chars().all(|c| c.is_ascii_digit()));
    }

    #[test]
    fn clob_order_serializes_side_as_string() {
        // Polymarket's POST /order rejects numeric side with "Invalid order
        // payload"; the JSON body must use "BUY" / "SELL". The EIP-712 hash
        // still uses uint8 — only the HTTP body shape differs.
        let order = ClobOrder {
            salt: "1".into(),
            maker: "0x0000000000000000000000000000000000000001".into(),
            signer: "0x0000000000000000000000000000000000000002".into(),
            taker: "0x0000000000000000000000000000000000000000".into(),
            token_id: "9".into(),
            maker_amount: "1000000".into(),
            taker_amount: "2000000".into(),
            expiration: "0".into(),
            nonce: "0".into(),
            fee_rate_bps: "0".into(),
            side: "BUY".into(),
            signature_type: 2,
            signature: "0xdead".into(),
        };
        let v = serde_json::to_value(&order).unwrap();
        assert_eq!(v["side"], serde_json::json!("BUY"));
        assert_eq!(v["signatureType"], serde_json::json!(2));
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
