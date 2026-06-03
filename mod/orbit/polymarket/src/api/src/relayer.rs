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

/// Public wrapper around the on-chain code check — used by the
/// `/deposit-wallet/info` route to surface deployment state to the UI.
pub async fn is_deposit_wallet_deployed(
    http: &reqwest::Client,
    wallet: &str,
) -> Result<bool> {
    has_contract_code(http, wallet).await
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
    let resp = http
        .post("https://polygon.drpc.org")
        .header(reqwest::header::CONTENT_TYPE, "application/json")
        .body(body.to_string())
        .send()
        .await
        .context("polygon rpc eth_getCode")?;
    let v: serde_json::Value = resp.json().await.context("polygon rpc json")?;
    let code = v.get("result").and_then(|r| r.as_str()).unwrap_or("0x");
    Ok(code.len() > 4)
}

/// Read the deposit wallet's TRADING balance (V2 collateral token, the
/// wrapped form that Polymarket's matcher actually counts). Returns
/// base-units as a decimal string (1e6 = $1) so the frontend doesn't
/// lose precision on large balances.
///
/// Reads the **wrapped** token (`0xC011...`), not raw USDC.e — after a
/// deposit the wrap step moves balance there, and a wallet showing
/// `Balance: $0.00` with $10 just wrapped was the exact "balance not
/// updating" UX bug. Use [`raw_usdce_balance`] when you need the
/// pre-wrap balance (e.g. detecting "user just deposited, need wrap").
pub async fn usdc_balance(http: &reqwest::Client, address: &str) -> Result<String> {
    erc20_balance(http, V2_COLLATERAL, address).await
}

/// Raw USDC.e balance — used by the wrap flow to figure out how much
/// unwrapped USDC.e is sitting in the wallet, and by the post-deposit
/// auto-wrap to size the batch.
pub async fn raw_usdce_balance(http: &reqwest::Client, address: &str) -> Result<String> {
    erc20_balance(http, USDC_E_ADDR, address).await
}

async fn erc20_balance(http: &reqwest::Client, token: &str, address: &str) -> Result<String> {
    let addr_no0x = address.strip_prefix("0x").unwrap_or(address);
    // selector(balanceOf(address)) = 0x70a08231, then 32-byte-padded addr.
    let calldata = format!("0x70a08231{:0>64}", addr_no0x);
    let body = serde_json::json!({
        "jsonrpc": "2.0",
        "method": "eth_call",
        "params": [{"to": token, "data": calldata}, "latest"],
        "id": 1,
    });
    let resp = http
        .post("https://polygon.drpc.org")
        .header(reqwest::header::CONTENT_TYPE, "application/json")
        .body(body.to_string())
        .send()
        .await
        .context("polygon rpc balanceOf")?;
    let v: serde_json::Value = resp.json().await.context("balanceOf json")?;
    let hex = v
        .get("result")
        .and_then(|r| r.as_str())
        .ok_or_else(|| anyhow!("balanceOf: no result"))?;
    let stripped = hex.strip_prefix("0x").unwrap_or(hex);
    if stripped.is_empty() {
        return Ok("0".to_string());
    }
    let n = u128::from_str_radix(stripped, 16).context("balanceOf hex parse")?;
    Ok(n.to_string())
}

// ─── Withdraw via deposit wallet Batch ───────────────────────────────────

const DEPOSIT_WALLET_DOMAIN_NAME: &str = "DepositWallet";
const DEPOSIT_WALLET_DOMAIN_VERSION: &str = "1";
const USDC_E_ADDR: &str = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174";

/// Submit an arbitrary call through the user's deposit wallet via the
/// relayer's gasless WALLET batch flow. Used by `withdraw_usdc` — kept
/// general in case we add allowance auto-setup later (USDC→Exchange,
/// CTF→Exchange) which uses the same machinery.
async fn submit_wallet_batch(
    http: &reqwest::Client,
    signer_store: &SignerStore,
    eoa: &str,
    target: &str,
    calldata_hex: &str,
) -> Result<serde_json::Value> {
    let backend_addr_lc = signer_store.signer_address(eoa)?;
    let backend_addr = eip55_checksum(&backend_addr_lc);
    let wallet = crate::deposit_wallet::derive_deposit_wallet(&backend_addr_lc)?;
    let wallet_cs = eip55_checksum(&wallet);

    // Login + on-chain wallet+nonce read in parallel-ish.
    let cookies = gamma_login(http, signer_store, eoa).await?;
    let nonce = read_wallet_nonce(http, &wallet).await?;
    let deadline = (chrono::Utc::now().timestamp() + 290) as u64; // ~5 min, just under relayer cap of 300s

    // Sign the Batch typed data with the backend signer key (the wallet's
    // EIP-1271 verifier auto-trusts the EOA that salted its CREATE2 — that's
    // our backend signer).
    let digest = batch_digest(&wallet_cs, nonce, deadline, target, calldata_hex)?;
    let sig = signer_store.sign_digest(eoa, &digest)?;
    let sig_hex = format!("0x{}", hex::encode(sig));

    let body = serde_json::json!({
        "type": "WALLET",
        "from": backend_addr,
        "to": crate::deposit_wallet::DEPOSIT_WALLET_FACTORY,
        "nonce": nonce.to_string(),
        "signature": sig_hex,
        "depositWalletParams": {
            "depositWallet": wallet_cs,
            "deadline": deadline.to_string(),
            "calls": [{
                "target": target,
                "value": "0",
                "data": calldata_hex,
            }],
        },
    });
    let resp = http
        .post(format!("{}/submit", RELAYER_URL))
        .header(reqwest::header::CONTENT_TYPE, "application/json")
        .header(reqwest::header::COOKIE, &cookies)
        .body(body.to_string())
        .send()
        .await
        .context("relayer /submit WALLET")?;
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
    let txn_id = parsed
        .transaction_id
        .ok_or_else(|| anyhow!("relayer /submit: missing transactionID, raw={}", text))?;
    let tx_hash = wait_for_confirm(http, &txn_id, std::time::Duration::from_secs(180)).await?;
    Ok(serde_json::json!({
        "transactionID": txn_id,
        "transactionHash": tx_hash,
    }))
}

/// Read the deposit wallet's on-chain `nonce()` (used as replay protection
/// in the Batch typed data — must be the current value or relayer rejects).
async fn read_wallet_nonce(http: &reqwest::Client, wallet: &str) -> Result<u64> {
    // selector(nonce()) = 0xaffed0e0
    let body = serde_json::json!({
        "jsonrpc": "2.0",
        "method": "eth_call",
        "params": [{"to": wallet, "data": "0xaffed0e0"}, "latest"],
        "id": 1,
    });
    let resp = http
        .post("https://polygon.drpc.org")
        .header(reqwest::header::CONTENT_TYPE, "application/json")
        .body(body.to_string())
        .send()
        .await
        .context("polygon rpc nonce()")?;
    let v: serde_json::Value = resp.json().await.context("nonce json")?;
    let hex = v
        .get("result")
        .and_then(|r| r.as_str())
        .unwrap_or("0x0");
    let stripped = hex.strip_prefix("0x").unwrap_or(hex);
    if stripped.is_empty() {
        return Ok(0);
    }
    Ok(u64::from_str_radix(stripped, 16).unwrap_or(0))
}

/// EIP-712 digest for the deposit wallet's `Batch` struct.
///
/// Types (verbatim from clob-client-v2 `approveDepositWalletAllowances.ts`):
///   Batch(address wallet, uint256 nonce, uint256 deadline, Call[] calls)
///   Call(address target, uint256 value, bytes data)
///
/// Domain: `{ name: "DepositWallet", version: "1", chainId: 137,
/// verifyingContract: <wallet> }`.
fn batch_digest(
    wallet_cs: &str,
    nonce: u64,
    deadline: u64,
    target: &str,
    calldata_hex: &str,
) -> Result<[u8; 32]> {
    use crate::signer::keccak256;

    // typehashes
    let call_type = b"Call(address target,uint256 value,bytes data)";
    let call_th = keccak256(call_type);
    let batch_type = b"Batch(address wallet,uint256 nonce,uint256 deadline,Call[] calls)Call(address target,uint256 value,bytes data)";
    let batch_th = keccak256(batch_type);

    // Encode the single Call's struct hash.
    // For `bytes` type, EIP-712 hashes the raw bytes (not the type-hash chain).
    let target_bytes = hex::decode(target.strip_prefix("0x").unwrap_or(target))
        .map_err(|e| anyhow!("target hex: {}", e))?;
    let data_bytes = hex::decode(calldata_hex.strip_prefix("0x").unwrap_or(calldata_hex))
        .map_err(|e| anyhow!("calldata hex: {}", e))?;
    let mut padded_target = [0u8; 32];
    padded_target[12..].copy_from_slice(&target_bytes);
    let value_be = [0u8; 32]; // value = 0
    let data_hash = keccak256(&data_bytes);

    let mut call_buf = Vec::with_capacity(4 * 32);
    call_buf.extend_from_slice(&call_th);
    call_buf.extend_from_slice(&padded_target);
    call_buf.extend_from_slice(&value_be);
    call_buf.extend_from_slice(&data_hash);
    let call_hash = keccak256(&call_buf);

    // calls is Call[] → hash of the concatenated call struct hashes.
    let calls_array_hash = keccak256(&call_hash);

    // Batch struct hash
    let mut wallet_padded = [0u8; 32];
    let wallet_bytes = hex::decode(wallet_cs.strip_prefix("0x").unwrap_or(wallet_cs))
        .map_err(|e| anyhow!("wallet hex: {}", e))?;
    wallet_padded[12..].copy_from_slice(&wallet_bytes);
    let mut nonce_be = [0u8; 32];
    nonce_be[24..].copy_from_slice(&nonce.to_be_bytes());
    let mut deadline_be = [0u8; 32];
    deadline_be[24..].copy_from_slice(&deadline.to_be_bytes());

    let mut batch_buf = Vec::with_capacity(5 * 32);
    batch_buf.extend_from_slice(&batch_th);
    batch_buf.extend_from_slice(&wallet_padded);
    batch_buf.extend_from_slice(&nonce_be);
    batch_buf.extend_from_slice(&deadline_be);
    batch_buf.extend_from_slice(&calls_array_hash);
    let batch_struct_hash = keccak256(&batch_buf);

    // Domain separator (verifyingContract = wallet)
    let domain_type_hash = keccak256(
        b"EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)",
    );
    let mut dom_buf = Vec::with_capacity(5 * 32);
    dom_buf.extend_from_slice(&domain_type_hash);
    dom_buf.extend_from_slice(&keccak256(DEPOSIT_WALLET_DOMAIN_NAME.as_bytes()));
    dom_buf.extend_from_slice(&keccak256(DEPOSIT_WALLET_DOMAIN_VERSION.as_bytes()));
    let mut chain_be = [0u8; 32];
    chain_be[24..].copy_from_slice(&POLYGON_CHAIN_ID.to_be_bytes());
    dom_buf.extend_from_slice(&chain_be);
    dom_buf.extend_from_slice(&wallet_padded);
    let dom_sep = keccak256(&dom_buf);

    // Final digest
    let mut prefix = Vec::with_capacity(66);
    prefix.push(0x19);
    prefix.push(0x01);
    prefix.extend_from_slice(&dom_sep);
    prefix.extend_from_slice(&batch_struct_hash);
    Ok(keccak256(&prefix))
}

/// Unwrap V2 collateral back to USDC.e and send straight to a destination
/// address. Used for "send my Polymarket balance to my MetaMask" — the
/// wallet's steady-state balance is wrapped collateral (`0xC011...`), not
/// raw USDC.e, so a plain `withdraw_usdc` would have nothing to transfer.
///
/// Single Batch:
///   1. V2 collateral.approve(Offramp, MAX) — idempotent, ~21k gas if
///      already maxed
///   2. Offramp.unwrap(USDC.e, destination, amount) — burns collateral
///      from wallet, mints USDC.e directly to destination
///
/// `amount` is in V2 collateral base units (1e6 = $1).
pub async fn unwrap_and_send(
    http: &reqwest::Client,
    signer_store: &SignerStore,
    eoa: &str,
    destination: &str,
    amount_base_units: &str,
) -> Result<serde_json::Value> {
    let dst_no0x = destination.strip_prefix("0x").unwrap_or(destination);
    if hex::decode(dst_no0x).map(|b| b.len()).unwrap_or(0) != 20 {
        return Err(anyhow!("destination must be a 20-byte 0x-address"));
    }
    let amount: u128 = amount_base_units
        .parse()
        .map_err(|e| anyhow!("amount not a u128 base-units integer: {}", e))?;
    if amount == 0 {
        return Err(anyhow!("amount must be > 0"));
    }

    let offramp_no0x = COLLATERAL_OFFRAMP
        .strip_prefix("0x")
        .unwrap_or(COLLATERAL_OFFRAMP)
        .to_lowercase();
    let usdce_no0x = USDC_E_ADDR.strip_prefix("0x").unwrap_or(USDC_E_ADDR).to_lowercase();

    // Call 1: V2 collateral.approve(Offramp, MAX_UINT256). Safe to issue
    // every time — overwrites the slot if already approved.
    let mut approve = String::from("0x095ea7b3");
    approve.push_str(&format!("{:0>64}", offramp_no0x));
    approve.push_str(MAX_UINT256_HEX);

    // Call 2: Offramp.unwrap(USDC.e, destination, amount)
    //   selector(unwrap(address,address,uint256)) = 0x8cc7104f
    let mut unwrap = String::from("0x8cc7104f");
    unwrap.push_str(&format!("{:0>64}", usdce_no0x));
    unwrap.push_str(&format!("{:0>64}", dst_no0x.to_lowercase()));
    unwrap.push_str(&format!("{:0>64x}", amount));

    let calls = vec![
        (V2_COLLATERAL.to_string(), approve),
        (COLLATERAL_OFFRAMP.to_string(), unwrap),
    ];
    submit_wallet_multi_batch(http, signer_store, eoa, &calls).await
}

/// Withdraw USDC.e from the user's deposit wallet to `destination`.
/// Gasless — relayer pays Polygon gas. Returns `{transactionID,
/// transactionHash}` once confirmed.
pub async fn withdraw_usdc(
    http: &reqwest::Client,
    signer_store: &SignerStore,
    eoa: &str,
    destination: &str,
    amount_base_units: &str,
) -> Result<serde_json::Value> {
    // Sanity-check destination is a 20-byte 0x address.
    let dst_no0x = destination.strip_prefix("0x").unwrap_or(destination);
    if hex::decode(dst_no0x).map(|b| b.len()).unwrap_or(0) != 20 {
        return Err(anyhow!("destination must be a 20-byte 0x-address"));
    }
    let amount: u128 = amount_base_units
        .parse()
        .map_err(|e| anyhow!("amount not a u128 base-units integer: {}", e))?;
    if amount == 0 {
        return Err(anyhow!("amount must be > 0"));
    }

    // Encode USDC.e.transfer(destination, amount) calldata.
    // selector(transfer(address,uint256)) = 0xa9059cbb
    let mut calldata = String::from("0xa9059cbb");
    calldata.push_str(&format!("{:0>64}", dst_no0x.to_lowercase()));
    calldata.push_str(&format!("{:0>64x}", amount));

    submit_wallet_batch(http, signer_store, eoa, USDC_E_ADDR, &calldata).await
}

// ─── USDC.e → V2 collateral wrap (CollateralOnramp) ─────────────────────
//
// Polymarket V2 reads trading balance from a *wrapped* token at
// `0xC011...`, not raw USDC.e (`0x2791...`). Users naturally send USDC.e
// to their deposit wallet, see it on Polygonscan, but Polymarket reports
// "balance: 0" because nothing is wrapped. The CollateralOnramp at
// `0x93070a847efEf7F70739046A929D47a521F5B8ee` mints 1:1 wrapped
// collateral to the wallet in exchange for USDC.e — call it from inside
// the deposit wallet (via Batch) and the trading balance matches.

const COLLATERAL_ONRAMP: &str = "0x93070a847efEf7F70739046A929D47a521F5B8ee";
// Reverse of the Onramp — burns V2 collateral and sends the underlying
// USDC.e to a destination. Used by the "send to my EOA" withdraw flow
// when the wallet holds wrapped collateral (the steady state for any
// user who has been trading).
const COLLATERAL_OFFRAMP: &str = "0x2957922Eb93258b93368531d39fAcCA3B4dC5854";
// V2 trading collateral token — what Polymarket reads for balance.
const V2_COLLATERAL: &str = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB";
// V2 Exchange contracts — must be approved as spender for the wallet's
// V2 collateral before BUY orders fill. NegRisk uses a separate contract.
const EXCHANGE_V2: &str = "0xE111180000d2663C0091e4f400237545B87B996B";
const NEG_RISK_EXCHANGE_V2: &str = "0xe2222d279d744050d28e00520010520000310F59";
// Conditional Tokens — must be `setApprovalForAll(exchange, true)` to
// SELL existing positions back to the market.
const CONDITIONAL_TOKENS: &str = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045";
const MAX_UINT256_HEX: &str =
    "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff";

/// Wrap `amount` USDC.e (base-units, 1e6=$1) sitting in the deposit
/// wallet into V2 collateral, minted back to the same wallet. Submits
/// the approve+wrap calls in a single relayer Batch — one tx, one
/// confirmation. If `amount` is "all", wraps the wallet's full USDC.e
/// balance read on-chain.
pub async fn wrap_usdce_to_collateral(
    http: &reqwest::Client,
    signer_store: &SignerStore,
    eoa: &str,
    amount_base_units: Option<&str>,
) -> Result<serde_json::Value> {
    let backend_addr_lc = signer_store.signer_address(eoa)?;
    let wallet = crate::deposit_wallet::derive_deposit_wallet(&backend_addr_lc)?;

    // Resolve "all" → on-chain USDC.e balance. amount=0 is fine and
    // means "skip the wrap step but still (re-)submit trading approvals
    // in case they got revoked". Useful when called manually after a
    // wallet recovery or when the user already has wrapped balance and
    // just hit "allowance is not enough" on a trade.
    let amount: u128 = match amount_base_units {
        Some(s) => s.parse().map_err(|e| anyhow!("amount: {}", e))?,
        // "wrap all" path uses the RAW USDC.e balance — that's what's
        // wrappable. `usdc_balance` now returns V2 collateral (already
        // wrapped), so using it here would size the batch wrong.
        None => raw_usdce_balance(http, &wallet).await?.parse().unwrap_or(0),
    };

    let wallet_no0x = wallet.strip_prefix("0x").unwrap_or(&wallet).to_lowercase();
    let onramp_no0x = COLLATERAL_ONRAMP
        .strip_prefix("0x")
        .unwrap_or(COLLATERAL_ONRAMP)
        .to_lowercase();
    let usdce_no0x = USDC_E_ADDR.strip_prefix("0x").unwrap_or(USDC_E_ADDR).to_lowercase();

    // Trading allowances — without these the wrap succeeds but the
    // first order fails with "allowance is not enough". Bundle them
    // into the same Batch so a single user action (DEPOSIT) covers
    // wrap + every approval the V2 trading flow needs.
    let exch_no0x = EXCHANGE_V2.strip_prefix("0x").unwrap_or(EXCHANGE_V2).to_lowercase();
    let neg_exch_no0x = NEG_RISK_EXCHANGE_V2
        .strip_prefix("0x")
        .unwrap_or(NEG_RISK_EXCHANGE_V2)
        .to_lowercase();

    let mut approve_collat_to_exch = String::from("0x095ea7b3");
    approve_collat_to_exch.push_str(&format!("{:0>64}", exch_no0x));
    approve_collat_to_exch.push_str(MAX_UINT256_HEX);

    let mut approve_collat_to_neg_exch = String::from("0x095ea7b3");
    approve_collat_to_neg_exch.push_str(&format!("{:0>64}", neg_exch_no0x));
    approve_collat_to_neg_exch.push_str(MAX_UINT256_HEX);

    // CTF.setApprovalForAll(exchange, true) — enables SELL orders. Bool
    // `true` encodes as 32-byte 0x...01.
    let mut setapprovalforall_v2_exch = String::from("0xa22cb465");
    setapprovalforall_v2_exch.push_str(&format!("{:0>64}", exch_no0x));
    setapprovalforall_v2_exch.push_str(&format!("{:0>64}", "1"));

    let mut setapprovalforall_v2_neg_exch = String::from("0xa22cb465");
    setapprovalforall_v2_neg_exch.push_str(&format!("{:0>64}", neg_exch_no0x));
    setapprovalforall_v2_neg_exch.push_str(&format!("{:0>64}", "1"));

    let mut calls: Vec<(String, String)> = Vec::with_capacity(6);
    if amount > 0 {
        // Call 1+2: approve USDC.e → Onramp, then Onramp.wrap. Skipped
        // when amount=0 (approvals-only refresh path).
        let mut approve_usdce = String::from("0x095ea7b3");
        approve_usdce.push_str(&format!("{:0>64}", onramp_no0x));
        approve_usdce.push_str(MAX_UINT256_HEX);

        // selector(wrap(address,address,uint256)) = 0x62355638
        let mut wrap_data = String::from("0x62355638");
        wrap_data.push_str(&format!("{:0>64}", usdce_no0x));
        wrap_data.push_str(&format!("{:0>64}", wallet_no0x));
        wrap_data.push_str(&format!("{:0>64x}", amount));

        calls.push((USDC_E_ADDR.to_string(), approve_usdce));
        calls.push((COLLATERAL_ONRAMP.to_string(), wrap_data));
    }
    calls.push((V2_COLLATERAL.to_string(), approve_collat_to_exch));
    calls.push((V2_COLLATERAL.to_string(), approve_collat_to_neg_exch));
    calls.push((CONDITIONAL_TOKENS.to_string(), setapprovalforall_v2_exch));
    calls.push((CONDITIONAL_TOKENS.to_string(), setapprovalforall_v2_neg_exch));

    submit_wallet_multi_batch(http, signer_store, eoa, &calls).await
}

/// Multi-call variant of `submit_wallet_batch`. Shares the same SIWE +
/// nonce machinery, but signs a `Batch` typed-data containing an array
/// of Calls (the V2 deposit wallet supports arbitrary-length batches).
async fn submit_wallet_multi_batch(
    http: &reqwest::Client,
    signer_store: &SignerStore,
    eoa: &str,
    calls: &[(String, String)],
) -> Result<serde_json::Value> {
    let backend_addr_lc = signer_store.signer_address(eoa)?;
    let backend_addr = eip55_checksum(&backend_addr_lc);
    let wallet = crate::deposit_wallet::derive_deposit_wallet(&backend_addr_lc)?;
    let wallet_cs = eip55_checksum(&wallet);

    let cookies = gamma_login(http, signer_store, eoa).await?;
    let nonce = read_wallet_nonce(http, &wallet).await?;
    let deadline = (chrono::Utc::now().timestamp() + 290) as u64; // ~5 min, just under relayer cap of 300s

    let digest = batch_digest_multi(&wallet_cs, nonce, deadline, calls)?;
    let sig = signer_store.sign_digest(eoa, &digest)?;
    let sig_hex = format!("0x{}", hex::encode(sig));

    let calls_json: Vec<serde_json::Value> = calls
        .iter()
        .map(|(target, data)| {
            serde_json::json!({
                "target": target,
                "value": "0",
                "data": data,
            })
        })
        .collect();

    let body = serde_json::json!({
        "type": "WALLET",
        "from": backend_addr,
        "to": crate::deposit_wallet::DEPOSIT_WALLET_FACTORY,
        "nonce": nonce.to_string(),
        "signature": sig_hex,
        "depositWalletParams": {
            "depositWallet": wallet_cs,
            "deadline": deadline.to_string(),
            "calls": calls_json,
        },
    });
    let resp = http
        .post(format!("{}/submit", RELAYER_URL))
        .header(reqwest::header::CONTENT_TYPE, "application/json")
        .header(reqwest::header::COOKIE, &cookies)
        .body(body.to_string())
        .send()
        .await
        .context("relayer /submit WALLET multi")?;
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
    let txn_id = parsed
        .transaction_id
        .ok_or_else(|| anyhow!("relayer /submit: missing transactionID, raw={}", text))?;
    let tx_hash = wait_for_confirm(http, &txn_id, std::time::Duration::from_secs(180)).await?;
    Ok(serde_json::json!({
        "transactionID": txn_id,
        "transactionHash": tx_hash,
    }))
}

/// Multi-call variant of `batch_digest`. The Batch struct hash takes the
/// keccak256 of the concatenated per-Call struct hashes for the
/// `calls: Call[]` field — the only divergence from the single-call path.
fn batch_digest_multi(
    wallet_cs: &str,
    nonce: u64,
    deadline: u64,
    calls: &[(String, String)],
) -> Result<[u8; 32]> {
    use crate::signer::keccak256;

    let call_th = keccak256(b"Call(address target,uint256 value,bytes data)");
    let batch_th = keccak256(
        b"Batch(address wallet,uint256 nonce,uint256 deadline,Call[] calls)Call(address target,uint256 value,bytes data)",
    );

    let mut calls_concat = Vec::with_capacity(calls.len() * 32);
    for (target, calldata_hex) in calls {
        let target_bytes = hex::decode(target.strip_prefix("0x").unwrap_or(target))
            .map_err(|e| anyhow!("target hex: {}", e))?;
        let data_bytes = hex::decode(calldata_hex.strip_prefix("0x").unwrap_or(calldata_hex))
            .map_err(|e| anyhow!("calldata hex: {}", e))?;
        let mut padded_target = [0u8; 32];
        padded_target[12..].copy_from_slice(&target_bytes);
        let value_be = [0u8; 32];
        let data_hash = keccak256(&data_bytes);

        let mut call_buf = Vec::with_capacity(4 * 32);
        call_buf.extend_from_slice(&call_th);
        call_buf.extend_from_slice(&padded_target);
        call_buf.extend_from_slice(&value_be);
        call_buf.extend_from_slice(&data_hash);
        calls_concat.extend_from_slice(&keccak256(&call_buf));
    }
    let calls_array_hash = keccak256(&calls_concat);

    let mut wallet_padded = [0u8; 32];
    let wallet_bytes = hex::decode(wallet_cs.strip_prefix("0x").unwrap_or(wallet_cs))
        .map_err(|e| anyhow!("wallet hex: {}", e))?;
    wallet_padded[12..].copy_from_slice(&wallet_bytes);
    let mut nonce_be = [0u8; 32];
    nonce_be[24..].copy_from_slice(&nonce.to_be_bytes());
    let mut deadline_be = [0u8; 32];
    deadline_be[24..].copy_from_slice(&deadline.to_be_bytes());

    let mut batch_buf = Vec::with_capacity(5 * 32);
    batch_buf.extend_from_slice(&batch_th);
    batch_buf.extend_from_slice(&wallet_padded);
    batch_buf.extend_from_slice(&nonce_be);
    batch_buf.extend_from_slice(&deadline_be);
    batch_buf.extend_from_slice(&calls_array_hash);
    let batch_struct_hash = keccak256(&batch_buf);

    let domain_type_hash = keccak256(
        b"EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)",
    );
    let mut dom_buf = Vec::with_capacity(5 * 32);
    dom_buf.extend_from_slice(&domain_type_hash);
    dom_buf.extend_from_slice(&keccak256(DEPOSIT_WALLET_DOMAIN_NAME.as_bytes()));
    dom_buf.extend_from_slice(&keccak256(DEPOSIT_WALLET_DOMAIN_VERSION.as_bytes()));
    let mut chain_be = [0u8; 32];
    chain_be[24..].copy_from_slice(&POLYGON_CHAIN_ID.to_be_bytes());
    dom_buf.extend_from_slice(&chain_be);
    dom_buf.extend_from_slice(&wallet_padded);
    let dom_sep = keccak256(&dom_buf);

    let mut prefix = Vec::with_capacity(66);
    prefix.push(0x19);
    prefix.push(0x01);
    prefix.extend_from_slice(&dom_sep);
    prefix.extend_from_slice(&batch_struct_hash);
    Ok(keccak256(&prefix))
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
