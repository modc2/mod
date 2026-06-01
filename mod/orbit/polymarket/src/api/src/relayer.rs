//! Polymarket V2 Gamma + Relayer client.
//!
//! End-to-end flow for "fully automated, no Polymarket UI":
//!   1. SIWE login against gamma-api → session cookie
//!   2. POST WALLET-CREATE to relayer with that cookie → returns txnID
//!   3. Poll relayer until STATE_CONFIRMED
//!   4. Deposit wallet now exists on-chain; API keys can be minted
//!      against it, orders can be signed with POLY_1271
//!
//! The relayer is gasless — Polymarket eats the deploy gas. We only need
//! the backend signer's private key (to sign the SIWE message) and HTTP.

use anyhow::{anyhow, Context, Result};
use base64::Engine;
use serde::Deserialize;

use crate::signer::SignerStore;

const GAMMA_URL: &str = "https://gamma-api.polymarket.com";
const RELAYER_URL: &str = "https://relayer-v2.polymarket.com";
const POLYMARKET_DOMAIN: &str = "polymarket.com";
const POLYGON_CHAIN_ID: u64 = 137;

// ─── EIP-55 checksum casing ─────────────────────────────────────────────
//
// SIWE (EIP-4361) requires the address line in checksum case. Polymarket's
// SIWE parser may also enforce this — our `signer_address` returns
// lowercase, so we explicitly checksum here.

fn eip55_checksum(addr_lc: &str) -> String {
    let stripped = addr_lc.strip_prefix("0x").unwrap_or(addr_lc).to_lowercase();
    let hash = crate::signer::keccak256(stripped.as_bytes());
    let hash_hex = hex::encode(hash);
    let mut out = String::with_capacity(42);
    out.push_str("0x");
    for (i, c) in stripped.chars().enumerate() {
        if c.is_ascii_alphabetic() {
            let nibble = u8::from_str_radix(&hash_hex[i..i + 1], 16).unwrap_or(0);
            if nibble >= 8 {
                out.push(c.to_ascii_uppercase());
            } else {
                out.push(c);
            }
        } else {
            out.push(c);
        }
    }
    out
}

// ─── SIWE login ──────────────────────────────────────────────────────────

#[derive(Debug, Deserialize)]
struct NonceResp {
    nonce: String,
}

/// Build the canonical SIWE message string Polymarket signs.
/// Format matches @polymarket/clob-client-v2 `gammaLogin` exactly —
/// blank lines, field ordering, and ISO-8601 timestamps are all
/// significant (the server parses it strictly).
fn siwe_message(
    address: &str,
    nonce: &str,
    issued_at: &str,
    expiration: &str,
) -> String {
    format!(
        "{domain} wants you to sign in with your Ethereum account:\n\
         {address}\n\
         \n\
         Welcome to Polymarket! Sign to connect.\n\
         \n\
         URI: https://{domain}\n\
         Version: 1\n\
         Chain ID: {chain}\n\
         Nonce: {nonce}\n\
         Issued At: {issued_at}\n\
         Expiration Time: {expiration}",
        domain = POLYMARKET_DOMAIN,
        address = address,
        chain = POLYGON_CHAIN_ID,
        nonce = nonce,
        issued_at = issued_at,
        expiration = expiration,
    )
}

/// Concatenate Set-Cookie headers into a single Cookie header value
/// (just the `name=value` part, dropping attributes like Path, Domain).
fn parse_set_cookies(resp: &reqwest::Response) -> String {
    resp.headers()
        .get_all(reqwest::header::SET_COOKIE)
        .iter()
        .filter_map(|hv| hv.to_str().ok())
        .filter_map(|c| c.split(';').next())
        .map(|c| c.trim().to_string())
        .collect::<Vec<_>>()
        .join("; ")
}

/// Merge two cookie strings into one, keeping the latest value per name.
fn merge_cookies(a: &str, b: &str) -> String {
    let mut map: std::collections::BTreeMap<String, String> = std::collections::BTreeMap::new();
    for part in a.split("; ").chain(b.split("; ")) {
        let part = part.trim();
        if let Some((name, _)) = part.split_once('=') {
            map.insert(name.to_string(), part.to_string());
        }
    }
    map.into_values().collect::<Vec<_>>().join("; ")
}

/// Log into Polymarket's Gamma API via SIWE. Returns a cookie string
/// suitable for the `Cookie` header on subsequent relayer calls.
pub async fn gamma_login(
    http: &reqwest::Client,
    signer_store: &SignerStore,
    eoa: &str,
) -> Result<String> {
    // SIWE wants EIP-55 checksum case — signer_address returns lowercase.
    let backend_addr = eip55_checksum(&signer_store.signer_address(eoa)?);

    // 1. Pull a fresh nonce + capture the session cookie.
    let nonce_resp = http
        .get(format!("{}/nonce", GAMMA_URL))
        .send()
        .await
        .context("gamma /nonce request")?;
    if !nonce_resp.status().is_success() {
        return Err(anyhow!(
            "gamma /nonce HTTP {}",
            nonce_resp.status()
        ));
    }
    let nonce_cookies = parse_set_cookies(&nonce_resp);
    let nonce_body: NonceResp = nonce_resp
        .json()
        .await
        .context("gamma /nonce json")?;

    // 2. Build SIWE message + JSON envelope.
    let issued_at = chrono::Utc::now()
        .format("%Y-%m-%dT%H:%M:%S%.3fZ")
        .to_string();
    let expiration = (chrono::Utc::now() + chrono::Duration::days(7))
        .format("%Y-%m-%dT%H:%M:%S%.3fZ")
        .to_string();
    let message = siwe_message(&backend_addr, &nonce_body.nonce, &issued_at, &expiration);

    // 3. EIP-191 personal_sign with the backend signer key.
    let sig = signer_store.personal_sign(eoa, message.as_bytes())?;
    let sig_hex = format!("0x{}", hex::encode(sig));

    // 4. Bearer envelope: base64( `{json}:::{sig_hex}` ).
    let json_payload = serde_json::json!({
        "domain": POLYMARKET_DOMAIN,
        "address": backend_addr,
        "statement": "Welcome to Polymarket! Sign to connect.",
        "uri": format!("https://{}", POLYMARKET_DOMAIN),
        "version": "1",
        "chainId": POLYGON_CHAIN_ID,
        "nonce": nonce_body.nonce,
        "issuedAt": issued_at,
        "expirationTime": expiration,
    })
    .to_string();
    let envelope = format!("{}:::{}", json_payload, sig_hex);
    let auth_token = base64::engine::general_purpose::STANDARD.encode(envelope.as_bytes());

    // 5. Exchange for a logged-in session cookie.
    let login_resp = http
        .get(format!("{}/login", GAMMA_URL))
        .header(reqwest::header::AUTHORIZATION, format!("Bearer {}", auth_token))
        .header(reqwest::header::COOKIE, &nonce_cookies)
        .send()
        .await
        .context("gamma /login request")?;
    if !login_resp.status().is_success() {
        let status = login_resp.status();
        let body = login_resp.text().await.unwrap_or_default();
        return Err(anyhow!(
            "gamma /login HTTP {}: {}",
            status,
            body.chars().take(200).collect::<String>()
        ));
    }
    let login_cookies = parse_set_cookies(&login_resp);
    Ok(merge_cookies(&nonce_cookies, &login_cookies))
}

// ─── Wallet deploy ──────────────────────────────────────────────────────

#[derive(Debug, Deserialize)]
struct SubmitResp {
    #[serde(rename = "transactionID")]
    transaction_id: Option<String>,
    state: Option<String>,
}

#[derive(Debug, Deserialize)]
struct TxnStatusEntry {
    state: String,
    #[serde(rename = "transactionHash", default)]
    transaction_hash: Option<String>,
}

/// Submit a WALLET-CREATE request to the relayer. Returns the relayer's
/// transaction ID; the wallet isn't on-chain yet — poll with
/// [`wait_for_confirm`] until STATE_CONFIRMED.
pub async fn submit_wallet_create(
    http: &reqwest::Client,
    cookies: &str,
    from_address: &str,
    factory: &str,
) -> Result<String> {
    let body = serde_json::json!({
        "type": "WALLET-CREATE",
        "from": from_address,
        "to": factory,
    });
    let resp = http
        .post(format!("{}/submit", RELAYER_URL))
        .header(reqwest::header::CONTENT_TYPE, "application/json")
        .header(reqwest::header::COOKIE, cookies)
        .body(body.to_string())
        .send()
        .await
        .context("relayer /submit WALLET-CREATE")?;
    let status = resp.status();
    let text = resp.text().await.unwrap_or_default();
    if !status.is_success() {
        return Err(anyhow!(
            "relayer /submit HTTP {}: {}",
            status,
            text.chars().take(300).collect::<String>()
        ));
    }
    let parsed: SubmitResp =
        serde_json::from_str(&text).context("relayer /submit response parse")?;
    parsed
        .transaction_id
        .ok_or_else(|| anyhow!("relayer /submit: missing transactionID, raw={}", text))
}

/// Block until the relayer reports STATE_CONFIRMED (or fails). Polls every
/// 3s with a generous timeout — wallet deploys typically confirm within
/// ~30s but Polygon can spike.
pub async fn wait_for_confirm(
    http: &reqwest::Client,
    txn_id: &str,
    timeout: std::time::Duration,
) -> Result<String> {
    let start = std::time::Instant::now();
    loop {
        if start.elapsed() > timeout {
            return Err(anyhow!("relayer txn {} timed out after {:?}", txn_id, timeout));
        }
        let resp = http
            .get(format!("{}/transaction?id={}", RELAYER_URL, txn_id))
            .send()
            .await
            .context("relayer /transaction poll")?;
        if !resp.status().is_success() {
            // Transient; back off and retry.
            tokio::time::sleep(std::time::Duration::from_secs(3)).await;
            continue;
        }
        let entries: Vec<TxnStatusEntry> = resp
            .json()
            .await
            .context("relayer /transaction parse")?;
        let Some(first) = entries.first() else {
            tokio::time::sleep(std::time::Duration::from_secs(3)).await;
            continue;
        };
        match first.state.as_str() {
            "STATE_CONFIRMED" => {
                return Ok(first.transaction_hash.clone().unwrap_or_default());
            }
            "STATE_FAILED" | "STATE_INVALID" => {
                return Err(anyhow!(
                    "relayer txn {} ended in {}",
                    txn_id,
                    first.state
                ));
            }
            _ => {
                tokio::time::sleep(std::time::Duration::from_secs(3)).await;
            }
        }
    }
}

// ─── Wallet deployment cache (per-process) ─────────────────────────────

use std::sync::OnceLock;
use std::collections::HashSet;
use parking_lot::Mutex;

/// Set of deposit-wallet addresses we've already confirmed deployed in
/// this process. Avoids redundant on-chain checks + relayer calls when
/// placing many orders in a row.
fn deployed_cache() -> &'static Mutex<HashSet<String>> {
    static CACHE: OnceLock<Mutex<HashSet<String>>> = OnceLock::new();
    CACHE.get_or_init(|| Mutex::new(HashSet::new()))
}

/// Idempotently ensure the deposit wallet for `backend_signer_eoa` exists
/// on Polygon. Returns the wallet address either way.
///
/// Fast path: check the in-process cache; if seen, return immediately.
/// Slow path: on-chain `eth_getCode` check via Polymarket's public Polygon
/// RPC; if deployed, cache + return. Otherwise SIWE-login + relayer deploy
/// + wait for confirmation, then cache.
pub async fn ensure_deposit_wallet_deployed(
    http: &reqwest::Client,
    signer_store: &SignerStore,
    eoa: &str,
) -> Result<String> {
    let backend_addr_lc = signer_store.signer_address(eoa)?;
    let backend_addr = eip55_checksum(&backend_addr_lc);
    let wallet = crate::deposit_wallet::derive_deposit_wallet(&backend_addr_lc)?;
    let wallet_lc = wallet.to_lowercase();

    {
        let cache = deployed_cache().lock();
        if cache.contains(&wallet_lc) {
            return Ok(wallet);
        }
    }

    // On-chain code check via a public Polygon RPC. Cheap and
    // distinguishes "not deployed yet" from "deployed, just not cached".
    if has_contract_code(http, &wallet).await? {
        deployed_cache().lock().insert(wallet_lc);
        tracing::info!(
            wallet = %wallet,
            "deposit wallet already deployed on-chain"
        );
        return Ok(wallet);
    }

    tracing::info!(
        backend_signer = %backend_addr,
        wallet = %wallet,
        "deposit wallet not deployed — submitting WALLET-CREATE via Polymarket relayer"
    );
    let cookies = gamma_login(http, signer_store, eoa).await?;
    let txn_id = submit_wallet_create(
        http,
        &cookies,
        &backend_addr,
        crate::deposit_wallet::DEPOSIT_WALLET_FACTORY,
    )
    .await?;
    tracing::info!(txn_id = %txn_id, "relayer accepted WALLET-CREATE, waiting for confirm");
    let tx_hash = wait_for_confirm(http, &txn_id, std::time::Duration::from_secs(180)).await?;
    tracing::info!(
        wallet = %wallet,
        tx_hash = %tx_hash,
        "deposit wallet deployed on-chain"
    );
    deployed_cache().lock().insert(wallet_lc);
    Ok(wallet)
}

/// Read-only eth_getCode against a public Polygon endpoint. Returns true
/// iff `address` has bytecode > 0 bytes (i.e. a deployed contract).
async fn has_contract_code(http: &reqwest::Client, address: &str) -> Result<bool> {
    let body = serde_json::json!({
        "jsonrpc": "2.0",
        "method": "eth_getCode",
        "params": [address, "latest"],
        "id": 1,
    });
    // Use Polymarket's published RPC endpoint (from py-sdk environments).
    let resp = http
        .post("https://polygon.drpc.org")
        .header(reqwest::header::CONTENT_TYPE, "application/json")
        .body(body.to_string())
        .send()
        .await
        .context("polygon rpc eth_getCode")?;
    let v: serde_json::Value = resp.json().await.context("polygon rpc json")?;
    let code = v
        .get("result")
        .and_then(|r| r.as_str())
        .unwrap_or("0x");
    // "0x" or "0x0" means no code. Anything longer is a deployed contract.
    Ok(code.len() > 4)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn siwe_message_format_matches_polymarket() {
        // Mirror of the layout in @polymarket/clob-client-v2 gammaLogin —
        // any drift here breaks Polymarket's SIWE parser (it's strict).
        let msg = siwe_message(
            "0x71ef1e4b2c3ad5901e204ff0c45709d0f7dff092",
            "abc123",
            "2026-06-01T00:00:00.000Z",
            "2026-06-08T00:00:00.000Z",
        );
        assert!(msg.starts_with("polymarket.com wants you to sign in"));
        assert!(msg.contains("Chain ID: 137"));
        assert!(msg.contains("Nonce: abc123"));
        assert!(msg.contains("URI: https://polymarket.com"));
    }

    #[test]
    fn merge_cookies_keeps_latest() {
        let merged = merge_cookies("a=1; b=2", "b=3; c=4");
        // BTreeMap iteration is alphabetical; check all expected pairs survived
        // with b updated.
        assert!(merged.contains("a=1"));
        assert!(merged.contains("b=3"));
        assert!(merged.contains("c=4"));
        assert!(!merged.contains("b=2"));
    }
}
