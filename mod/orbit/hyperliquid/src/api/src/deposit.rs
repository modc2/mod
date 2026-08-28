//! Cross-chain deposit rails — fund Hyperliquid from Ethereum, Base,
//! Polygon or Arbitrum in one guided flow.
//!
//! Hyperliquid only credits USDC sent to its bridge on Arbitrum, so any
//! other source chain needs a swap+bridge hop first. The frontend drives
//! MetaMask; this module supplies everything it needs:
//!   GET  /deposit/chains    — supported source chains (public)
//!   GET  /deposit/balances  — native + USDC balances per chain for an EOA,
//!                             priced in USD off Hyperliquid's own mids
//!   POST /deposit/quote     — LI.FI route into Arbitrum USDC (tx to sign)
//!   GET  /deposit/status    — LI.FI bridge-transfer status by source tx
//!
//! LI.FI (li.quest) is keyless for this volume; we proxy it server-side so
//! the browser never depends on third-party CORS and we keep one integrator
//! tag. Deposits stay fully self-custodial: every transaction is signed by
//! the user's wallet and funds only ever move to the user's own address
//! until the final USDC transfer to the Hyperliquid bridge.

use crate::AppState;
use axum::{
    extract::{Query, State},
    http::StatusCode,
    Json,
};
use serde::Deserialize;
use serde_json::{json, Value};

const LIFI: &str = "https://li.quest/v1";
const ARBITRUM_CHAIN_ID: u64 = 42161;
const ARBITRUM_USDC: &str = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831";
/// LI.FI's sentinel for a chain's native coin.
const NATIVE: &str = "0x0000000000000000000000000000000000000000";

pub struct Chain {
    pub key: &'static str,
    pub name: &'static str,
    pub chain_id: u64,
    pub rpc: &'static str,
    pub explorer: &'static str,
    pub usdc: &'static str,
    pub native_symbol: &'static str,
    /// Hyperliquid perp coin whose mid prices the native token in USD.
    pub px_coin: &'static str,
    /// Native units held back for gas when the user deposits MAX.
    pub gas_reserve: f64,
}

pub const CHAINS: &[Chain] = &[
    Chain {
        key: "arbitrum", name: "Arbitrum", chain_id: 42161,
        rpc: "https://arb1.arbitrum.io/rpc", explorer: "https://arbiscan.io",
        usdc: ARBITRUM_USDC,
        native_symbol: "ETH", px_coin: "ETH", gas_reserve: 0.0005,
    },
    Chain {
        key: "ethereum", name: "Ethereum", chain_id: 1,
        rpc: "https://ethereum-rpc.publicnode.com", explorer: "https://etherscan.io",
        usdc: "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        native_symbol: "ETH", px_coin: "ETH", gas_reserve: 0.004,
    },
    Chain {
        key: "base", name: "Base", chain_id: 8453,
        rpc: "https://mainnet.base.org", explorer: "https://basescan.org",
        usdc: "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        native_symbol: "ETH", px_coin: "ETH", gas_reserve: 0.0005,
    },
    Chain {
        key: "polygon", name: "Polygon", chain_id: 137,
        rpc: "https://polygon-bor-rpc.publicnode.com", explorer: "https://polygonscan.com",
        usdc: "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359",
        native_symbol: "POL", px_coin: "POL", gas_reserve: 0.5,
    },
];

pub fn chain_by_id(id: u64) -> Option<&'static Chain> {
    CHAINS.iter().find(|c| c.chain_id == id)
}

fn chain_json(c: &Chain) -> Value {
    json!({
        "key": c.key,
        "name": c.name,
        "chainId": c.chain_id,
        "chainIdHex": format!("0x{:x}", c.chain_id),
        "rpcUrl": c.rpc,
        "explorerUrl": c.explorer,
        "usdcAddress": c.usdc,
        "nativeSymbol": c.native_symbol,
        "gasReserve": c.gas_reserve,
        "direct": c.chain_id == ARBITRUM_CHAIN_ID,
    })
}

fn err(status: StatusCode, msg: impl std::fmt::Display) -> (StatusCode, Json<Value>) {
    (status, Json(json!({"error": msg.to_string()})))
}

// ── JSON-RPC helpers ────────────────────────────────────────────────────

async fn rpc(http: &reqwest::Client, url: &str, method: &str, params: Value) -> anyhow::Result<Value> {
    let body = json!({"jsonrpc": "2.0", "id": 1, "method": method, "params": params});
    let v: Value = http
        .post(url)
        .json(&body)
        .timeout(std::time::Duration::from_secs(6))
        .send()
        .await?
        .json()
        .await?;
    if let Some(e) = v.get("error") {
        anyhow::bail!("rpc error: {e}");
    }
    Ok(v.get("result").cloned().unwrap_or(Value::Null))
}

/// Parse an 0x-hex quantity into a scaled f64 (fine for display balances).
fn hex_units(v: &Value, decimals: u32) -> f64 {
    let s = v.as_str().unwrap_or("0x0").trim_start_matches("0x");
    // eth_call returns a 32-byte word; a u128 covers any real balance.
    let s = if s.len() > 32 { &s[s.len() - 32..] } else { s };
    u128::from_str_radix(if s.is_empty() { "0" } else { s }, 16)
        .map(|u| u as f64 / 10f64.powi(decimals as i32))
        .unwrap_or(0.0)
}

async fn native_balance(http: &reqwest::Client, c: &Chain, eoa: &str) -> f64 {
    rpc(http, c.rpc, "eth_getBalance", json!([eoa, "latest"]))
        .await
        .map(|v| hex_units(&v, 18))
        .unwrap_or(-1.0) // -1 = RPC unreachable, distinguishable from a true 0
}

async fn usdc_balance(http: &reqwest::Client, c: &Chain, eoa: &str) -> f64 {
    let data = format!(
        "0x70a08231{:0>64}", // balanceOf(address)
        eoa.trim_start_matches("0x").to_lowercase()
    );
    rpc(http, c.rpc, "eth_call", json!([{"to": c.usdc, "data": data}, "latest"]))
        .await
        .map(|v| hex_units(&v, 6))
        .unwrap_or(-1.0)
}

// ── Handlers ────────────────────────────────────────────────────────────

pub async fn deposit_chains(State(s): State<AppState>) -> Json<Value> {
    // Cross-chain routing is mainnet-only; on testnet the wallet page keeps
    // its direct Arbitrum-Sepolia flow and this list is empty.
    let chains: Vec<Value> = if s.hl.testnet {
        vec![]
    } else {
        CHAINS.iter().map(chain_json).collect()
    };
    Json(json!({
        "testnet": s.hl.testnet,
        "toChainId": ARBITRUM_CHAIN_ID,
        "toUsdc": ARBITRUM_USDC,
        "minDepositUsd": 5.0,
        "chains": chains,
    }))
}

#[derive(Deserialize)]
pub struct BalancesQuery {
    eoa: String,
}

pub async fn deposit_balances(
    State(s): State<AppState>,
    Query(q): Query<BalancesQuery>,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    let eoa = q.eoa.trim().to_lowercase();
    if !eoa.starts_with("0x") || eoa.len() != 42 || !eoa[2..].chars().all(|c| c.is_ascii_hexdigit()) {
        return Err(err(StatusCode::BAD_REQUEST, "eoa must be a 0x address"));
    }
    if s.hl.testnet {
        return Ok(Json(json!({"eoa": eoa, "chains": []})));
    }

    // Price natives off Hyperliquid's own mids — no extra price dependency.
    let mids = s.hl.all_mids().await.unwrap_or(Value::Null);
    let px = |coin: &str| -> f64 {
        mids.get(coin)
            .and_then(|v| v.as_str())
            .and_then(|s| s.parse::<f64>().ok())
            .unwrap_or(0.0)
    };

    let fetches = CHAINS.iter().map(|c| {
        let http = s.http.clone();
        let eoa = eoa.clone();
        async move {
            let (native, usdc) = tokio::join!(
                native_balance(&http, c, &eoa),
                usdc_balance(&http, c, &eoa),
            );
            (c, native, usdc)
        }
    });
    let results = futures::future::join_all(fetches).await;

    let chains: Vec<Value> = results
        .into_iter()
        .map(|(c, native, usdc)| {
            let native_px = px(c.px_coin);
            let ok = native >= 0.0 && usdc >= 0.0;
            let native = native.max(0.0);
            let usdc = usdc.max(0.0);
            json!({
                "key": c.key,
                "chainId": c.chain_id,
                "name": c.name,
                "ok": ok,
                "native": {
                    "symbol": c.native_symbol,
                    "balance": native,
                    "usd": native * native_px,
                    "priceUsd": native_px,
                    "gasReserve": c.gas_reserve,
                },
                "usdc": { "balance": usdc, "usd": usdc },
            })
        })
        .collect();

    Ok(Json(json!({"eoa": eoa, "chains": chains})))
}

#[derive(Deserialize)]
pub struct QuoteBody {
    /// Source chain id (1, 8453, 137, 42161).
    pub from_chain_id: u64,
    /// "usdc" | "native".
    pub token: String,
    /// Amount in human token units, e.g. "120.5" USDC or "0.05" ETH.
    pub amount: String,
    /// The depositor — both source and destination of the bridge.
    pub eoa: String,
    /// Destination chain id — defaults to Arbitrum (deposit direction).
    /// Withdrawals set this to bridge Arbitrum USDC out to another chain.
    #[serde(default)]
    pub to_chain_id: Option<u64>,
    /// Recipient on the destination chain — defaults to `eoa`.
    #[serde(default)]
    pub to_address: Option<String>,
}

pub async fn deposit_quote(
    State(s): State<AppState>,
    Json(b): Json<QuoteBody>,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    if s.hl.testnet {
        return Err(err(StatusCode::BAD_REQUEST, "cross-chain deposit is mainnet-only"));
    }
    let chain = chain_by_id(b.from_chain_id)
        .ok_or_else(|| err(StatusCode::BAD_REQUEST, "unsupported source chain"))?;
    let amount: f64 = b.amount.trim().parse().unwrap_or(0.0);
    if amount <= 0.0 {
        return Err(err(StatusCode::BAD_REQUEST, "amount must be > 0"));
    }
    let to_chain = match b.to_chain_id {
        None => chain_by_id(ARBITRUM_CHAIN_ID).expect("arbitrum in CHAINS"),
        Some(id) => chain_by_id(id)
            .ok_or_else(|| err(StatusCode::BAD_REQUEST, "unsupported destination chain"))?,
    };
    let to_address = b.to_address.as_deref().unwrap_or(&b.eoa).trim();
    if !to_address.starts_with("0x")
        || to_address.len() != 42
        || !to_address[2..].chars().all(|c| c.is_ascii_hexdigit())
    {
        return Err(err(StatusCode::BAD_REQUEST, "to_address must be a 0x address"));
    }
    let (from_token, decimals) = match b.token.as_str() {
        "usdc" => (chain.usdc, 6u32),
        "native" => (NATIVE, 18u32),
        _ => return Err(err(StatusCode::BAD_REQUEST, "token must be usdc|native")),
    };
    // Same-chain USDC never needs a route — the frontend does a plain
    // transfer. Reaching here with it is a client bug.
    if chain.chain_id == to_chain.chain_id && from_token == chain.usdc {
        return Err(err(StatusCode::BAD_REQUEST, "same-chain USDC moves don't need a bridge route"));
    }
    let from_amount = ((amount * 10f64.powi(decimals as i32)).round() as u128).to_string();

    let url = format!(
        "{LIFI}/quote?fromChain={}&toChain={}&fromToken={}&toToken={}\
         &fromAmount={}&fromAddress={}&toAddress={}&slippage=0.005&integrator=mod-hyperliquid",
        chain.chain_id, to_chain.chain_id, from_token, to_chain.usdc, from_amount, b.eoa, to_address,
    );
    let resp = s
        .http
        .get(&url)
        .timeout(std::time::Duration::from_secs(20))
        .send()
        .await
        .map_err(|e| err(StatusCode::BAD_GATEWAY, format!("li.fi unreachable: {e}")))?;
    let status = resp.status();
    let v: Value = resp
        .json()
        .await
        .map_err(|e| err(StatusCode::BAD_GATEWAY, format!("li.fi bad response: {e}")))?;
    if !status.is_success() {
        let msg = v
            .get("message")
            .and_then(|m| m.as_str())
            .unwrap_or("no route found for this amount");
        return Err(err(StatusCode::BAD_REQUEST, format!("li.fi: {msg}")));
    }

    let est = v.get("estimate").cloned().unwrap_or(Value::Null);
    let to_amount = |k: &str| {
        est.get(k)
            .and_then(|x| x.as_str())
            .and_then(|x| x.parse::<f64>().ok())
            .map(|u| u / 1e6)
            .unwrap_or(0.0)
    };
    Ok(Json(json!({
        "tool": v.get("toolDetails").and_then(|t| t.get("name")).cloned()
            .or_else(|| v.get("tool").cloned()).unwrap_or(Value::Null),
        "fromChainId": chain.chain_id,
        "fromToken": from_token,
        "fromAmountUnits": from_amount,
        "toUsdc": to_amount("toAmount"),
        "toUsdcMin": to_amount("toAmountMin"),
        "durationSec": est.get("executionDuration").cloned().unwrap_or(json!(0)),
        "approvalAddress": est.get("approvalAddress").cloned().unwrap_or(Value::Null),
        "transactionRequest": v.get("transactionRequest").cloned().unwrap_or(Value::Null),
    })))
}

#[derive(Deserialize)]
pub struct StatusQuery {
    tx_hash: String,
    from_chain_id: u64,
    /// Defaults to Arbitrum (deposit direction).
    #[serde(default)]
    to_chain_id: Option<u64>,
}

pub async fn deposit_status(
    State(s): State<AppState>,
    Query(q): Query<StatusQuery>,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    let url = format!(
        "{LIFI}/status?txHash={}&fromChain={}&toChain={}",
        q.tx_hash, q.from_chain_id, q.to_chain_id.unwrap_or(ARBITRUM_CHAIN_ID),
    );
    let v: Value = s
        .http
        .get(&url)
        .timeout(std::time::Duration::from_secs(15))
        .send()
        .await
        .map_err(|e| err(StatusCode::BAD_GATEWAY, format!("li.fi unreachable: {e}")))?
        .json()
        .await
        .map_err(|e| err(StatusCode::BAD_GATEWAY, format!("li.fi bad response: {e}")))?;

    // NOT_FOUND right after broadcast is normal — the indexer lags the chain.
    let status = v.get("status").and_then(|x| x.as_str()).unwrap_or("PENDING");
    let received = v
        .pointer("/receiving/amount")
        .and_then(|x| x.as_str())
        .and_then(|x| x.parse::<f64>().ok())
        .map(|u| u / 1e6);
    Ok(Json(json!({
        "status": status,
        "substatus": v.get("substatus").cloned().unwrap_or(Value::Null),
        "substatusMessage": v.get("substatusMessage").cloned().unwrap_or(Value::Null),
        "receivedUsdc": received,
    })))
}
