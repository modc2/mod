//! x402 paywall for the **paid** path — users who don't bring their own key
//! pay per request (USDC) and we fund the call with our backend Venice key.
//!
//! This implements the standard x402 "exact" scheme over an HTTP facilitator:
//!
//!   * No `X-PAYMENT` header  → respond `402` with a `{ x402Version, accepts,
//!     error }` body describing what to pay. The browser's `x402-fetch`
//!     wrapper reads this, makes the on-chain payment authorization, and
//!     retries with the header set.
//!   * `X-PAYMENT` present    → POST the decoded payload + our requirements to
//!     the facilitator's `/verify`; if valid, `/settle` it, then allow the
//!     request through to Venice. The settlement receipt is surfaced back to
//!     the client in `X-PAYMENT-RESPONSE`.
//!
//! Everything is config-driven via env (see `from_env`). The paid path is
//! only advertised when a receiver address is configured AND a backend Venice
//! key exists — otherwise users must bring their own key.

use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use base64::Engine;
use serde_json::{json, Value};

#[derive(Clone)]
pub struct X402Config {
    pub enabled: bool,
    pub receiver: String,
    pub network: String,
    pub max_amount_required: String, // atomic units (USDC has 6 decimals)
    pub price_display: String,       // human "0.01" for the UI
    pub asset: String,               // token contract address
    pub asset_name: String,          // EIP-712 domain name
    pub asset_version: String,       // EIP-712 domain version
    pub facilitator: String,
    pub resource: String,
    pub max_timeout_seconds: u64,
}

impl X402Config {
    /// Build from env. The paid path is enabled only when a receiver is set.
    pub fn from_env() -> Self {
        let receiver = env_or("VENICE_X402_RECEIVER", "");
        let network = env_or("VENICE_X402_NETWORK", "base");
        // Default asset = USDC on Base mainnet (6 decimals). Override per network.
        let (default_asset, default_name) = match network.as_str() {
            "base-sepolia" => ("0x036CbD53842c5426634e7929541eC2318f3dCF7e", "USDC"),
            _ => ("0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", "USD Coin"),
        };
        let asset = env_or("VENICE_X402_ASSET", default_asset);
        let asset_name = env_or("VENICE_X402_ASSET_NAME", default_name);
        let price_display = env_or("VENICE_X402_PRICE", "0.01");
        // Convert the human price into atomic units (6-decimal USDC) unless an
        // explicit atomic amount is provided.
        let max_amount_required = std::env::var("VENICE_X402_AMOUNT_ATOMIC")
            .ok()
            .filter(|s| !s.is_empty())
            .unwrap_or_else(|| usdc_atomic(&price_display));
        Self {
            enabled: !receiver.is_empty(),
            receiver,
            network,
            max_amount_required,
            price_display,
            asset,
            asset_name,
            asset_version: env_or("VENICE_X402_ASSET_VERSION", "2"),
            facilitator: env_or("VENICE_X402_FACILITATOR", "https://x402.org/facilitator"),
            resource: env_or("VENICE_X402_RESOURCE", "https://modc2.com/venice/chat"),
            max_timeout_seconds: env_or("VENICE_X402_TIMEOUT", "60").parse().unwrap_or(60),
        }
    }

    /// The single payment-requirements object the client must satisfy.
    fn requirements(&self) -> Value {
        json!({
            "scheme": "exact",
            "network": self.network,
            "maxAmountRequired": self.max_amount_required,
            "resource": self.resource,
            "description": "Venice chat completion (backend-funded)",
            "mimeType": "application/json",
            "payTo": self.receiver,
            "maxTimeoutSeconds": self.max_timeout_seconds,
            "asset": self.asset,
            "extra": { "name": self.asset_name, "version": self.asset_version }
        })
    }

    /// 402 challenge body: tells the wallet what to pay.
    pub fn challenge(&self) -> Response {
        let body = json!({
            "x402Version": 1,
            "accepts": [ self.requirements() ],
            "error": "X-PAYMENT header required",
        });
        (StatusCode::PAYMENT_REQUIRED, axum::Json(body)).into_response()
    }

    /// Verify (and settle) an `X-PAYMENT` header against the facilitator.
    /// Returns `Ok(Some(settlement_header))` on success — the value to echo in
    /// `X-PAYMENT-RESPONSE`. `Ok(None)` is never returned; failures are `Err`.
    pub async fn verify_and_settle(
        &self,
        http: &reqwest::Client,
        x_payment: &str,
    ) -> Result<String, String> {
        // The header is base64(JSON(paymentPayload)).
        let decoded = base64::engine::general_purpose::STANDARD
            .decode(x_payment.trim())
            .map_err(|_| "X-PAYMENT not valid base64".to_string())?;
        let payload: Value =
            serde_json::from_slice(&decoded).map_err(|_| "X-PAYMENT not valid json".to_string())?;
        let requirements = self.requirements();

        // 1) verify
        let verify_url = format!("{}/verify", self.facilitator.trim_end_matches('/'));
        let v: Value = http
            .post(&verify_url)
            .json(&json!({
                "x402Version": 1,
                "paymentPayload": payload,
                "paymentRequirements": requirements,
            }))
            .send()
            .await
            .map_err(|e| format!("facilitator verify unreachable: {e}"))?
            .json()
            .await
            .map_err(|e| format!("facilitator verify bad response: {e}"))?;
        let valid = v.get("isValid").and_then(|x| x.as_bool()).unwrap_or(false);
        if !valid {
            let reason = v
                .get("invalidReason")
                .and_then(|x| x.as_str())
                .unwrap_or("payment invalid");
            return Err(reason.to_string());
        }

        // 2) settle — moves the funds. If settlement fails we must NOT serve.
        let settle_url = format!("{}/settle", self.facilitator.trim_end_matches('/'));
        let s: Value = http
            .post(&settle_url)
            .json(&json!({
                "x402Version": 1,
                "paymentPayload": payload,
                "paymentRequirements": requirements,
            }))
            .send()
            .await
            .map_err(|e| format!("facilitator settle unreachable: {e}"))?
            .json()
            .await
            .map_err(|e| format!("facilitator settle bad response: {e}"))?;
        let success = s.get("success").and_then(|x| x.as_bool()).unwrap_or(false);
        if !success {
            let reason = s
                .get("errorReason")
                .or_else(|| s.get("error"))
                .and_then(|x| x.as_str())
                .unwrap_or("settlement failed");
            return Err(reason.to_string());
        }

        // Echo the settlement receipt back to the client per the x402 spec.
        let receipt = base64::engine::general_purpose::STANDARD.encode(s.to_string());
        Ok(receipt)
    }
}

fn env_or(key: &str, default: &str) -> String {
    std::env::var(key)
        .ok()
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| default.to_string())
}

/// Convert a decimal USDC string ("0.01") into 6-decimal atomic units ("10000").
/// Falls back to "10000" (= $0.01) on malformed input.
fn usdc_atomic(display: &str) -> String {
    let parsed: f64 = display.trim().parse().unwrap_or(0.01);
    let atomic = (parsed * 1_000_000.0).round() as u128;
    atomic.to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn atomic_conversion() {
        assert_eq!(usdc_atomic("0.01"), "10000");
        assert_eq!(usdc_atomic("1"), "1000000");
        assert_eq!(usdc_atomic("0.5"), "500000");
    }

    #[test]
    fn disabled_without_receiver() {
        std::env::remove_var("VENICE_X402_RECEIVER");
        let cfg = X402Config::from_env();
        assert!(!cfg.enabled);
    }
}
