//! Cross-chain deposit rails — fund Hyperliquid from any of twelve EVM
//! chains and any of the tokens you actually hold, in one transaction.
//!
//! Hyperliquid's own bridge only credits USDC sent to its contract on
//! Arbitrum, so historically every other chain meant a swap+bridge to
//! Arbitrum first and a second transaction to deposit. LI.FI now exposes
//! Hyperliquid Core itself as a routing destination (its chain id 1337),
//! so a single signed transaction on the source chain lands as perps USDC
//! in the depositor's Hyperliquid account — no Arbitrum layover, no second
//! wallet prompt, no waiting in between.
//!
//!   GET  /deposit/chains    — supported source chains + their tokens
//!   GET  /deposit/balances  — every spendable balance for an EOA, priced
//!   POST /deposit/quote     — route into Hyperliquid (tx for the wallet)
//!   GET  /deposit/status    — transfer status by source tx hash
//!   GET  /deposit/evm_chains — EVERY EVM chain LI.FI can route from (~65)
//!   GET  /deposit/token     — look up any ERC-20 on any of those chains
//!
//! Custom tokens: the twelve built-in chains carry a curated token list, but
//! any (chain, token) LI.FI knows can ride along — `/deposit/balances` takes
//! `extra=<chainId>:<address|native>,…` and `/deposit/quote` accepts any
//! EVM chain id + token address. The browser keeps the user's list; the
//! server only prices, reads balances and routes.
//!
//! Arbitrum USDC keeps its own path: a plain ERC-20 transfer straight to
//! Hyperliquid's bridge, which costs nothing and needs no third party.
//!
//! LI.FI (li.quest) is keyless for this volume; we proxy it server-side so
//! the browser never depends on third-party CORS and we keep one integrator
//! tag. Deposits stay fully self-custodial: every transaction is signed by
//! the user's wallet, and the only recipient is the user's own Hyperliquid
//! account.

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
/// LI.FI's id for Hyperliquid Core — the perps account itself, not HyperEVM.
pub const HL_CHAIN_ID: u64 = 1337;
/// The token id LI.FI uses for "USDC in the perps account". It reuses the
/// Arbitrum USDC address because that is what the bridge ultimately moves.
const HL_PERPS_USDC: &str = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831";
/// LI.FI's sentinel for a chain's native coin.
const NATIVE: &str = "0x0000000000000000000000000000000000000000";
/// Multicall3's canonical CREATE2 address — the same on every chain in
/// `CHAINS` except zkSync Era, whose different CREATE2 scheme lands it
/// elsewhere; each chain carries its own `multicall` for that reason.
const MULTICALL3: &str = "0xcA11bde05977b3631167028862bE2a173976CA11";
const MULTICALL3_ZKSYNC: &str = "0xF9cda624FBC7e059355ce98a31693d299FACd963";

/// How a token gets a USD price. Stables are pinned; everything else is
/// priced off Hyperliquid's own mids, so there is no extra price feed.
#[derive(Clone, Copy, PartialEq)]
pub enum Px {
    Stable,
    /// Hyperliquid perp coin whose mid prices this token.
    Coin(&'static str),
}

pub struct Token {
    pub symbol: &'static str,
    /// ERC-20 address, or `NATIVE` for the chain's own coin.
    pub address: &'static str,
    pub decimals: u32,
    pub px: Px,
}

impl Token {
    pub fn is_native(&self) -> bool {
        self.address == NATIVE
    }
}

pub struct Chain {
    pub key: &'static str,
    pub name: &'static str,
    pub chain_id: u64,
    pub rpc: &'static str,
    pub explorer: &'static str,
    /// Canonical USDC on this chain — the `usdc` alias and the withdrawal
    /// destination token.
    pub usdc: &'static str,
    pub native_symbol: &'static str,
    /// Native units held back for gas when the user deposits MAX.
    pub gas_reserve: f64,
    /// Native coin first, then every ERC-20 worth scanning for.
    pub tokens: &'static [Token],
}

const ETH_PX: Px = Px::Coin("ETH");
const BTC_PX: Px = Px::Coin("BTC");

pub const CHAINS: &[Chain] = &[
    Chain {
        key: "arbitrum", name: "Arbitrum", chain_id: 42161,
        rpc: "https://arb1.arbitrum.io/rpc", explorer: "https://arbiscan.io",
        usdc: ARBITRUM_USDC, native_symbol: "ETH", gas_reserve: 0.0005,
        tokens: &[
            Token { symbol: "ETH",    address: NATIVE, decimals: 18, px: ETH_PX },
            Token { symbol: "USDC",   address: ARBITRUM_USDC, decimals: 6, px: Px::Stable },
            Token { symbol: "USDC.e", address: "0xFF970A61A04b1cA14834A43f5dE4533eBDDB5CC8", decimals: 6, px: Px::Stable },
            Token { symbol: "USDT",   address: "0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9", decimals: 6, px: Px::Stable },
            Token { symbol: "WETH",   address: "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1", decimals: 18, px: ETH_PX },
            Token { symbol: "WBTC",   address: "0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f", decimals: 8, px: BTC_PX },
            Token { symbol: "ARB",    address: "0x912CE59144191C1204E64559FE8253a0e49E6548", decimals: 18, px: Px::Coin("ARB") },
        ],
    },
    Chain {
        key: "ethereum", name: "Ethereum", chain_id: 1,
        rpc: "https://ethereum-rpc.publicnode.com", explorer: "https://etherscan.io",
        usdc: "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        native_symbol: "ETH", gas_reserve: 0.004,
        tokens: &[
            Token { symbol: "ETH",  address: NATIVE, decimals: 18, px: ETH_PX },
            Token { symbol: "USDC", address: "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", decimals: 6, px: Px::Stable },
            Token { symbol: "USDT", address: "0xdAC17F958D2ee523a2206206994597C13D831ec7", decimals: 6, px: Px::Stable },
            Token { symbol: "DAI",  address: "0x6B175474E89094C44Da98b954EedeAC495271d0F", decimals: 18, px: Px::Stable },
            Token { symbol: "WETH", address: "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2", decimals: 18, px: ETH_PX },
            Token { symbol: "WBTC", address: "0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599", decimals: 8, px: BTC_PX },
        ],
    },
    Chain {
        key: "base", name: "Base", chain_id: 8453,
        // mainnet.base.org rate-limits a burst of eth_calls; publicnode does not.
        rpc: "https://base-rpc.publicnode.com", explorer: "https://basescan.org",
        usdc: "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        native_symbol: "ETH", gas_reserve: 0.0005,
        tokens: &[
            Token { symbol: "ETH",   address: NATIVE, decimals: 18, px: ETH_PX },
            Token { symbol: "USDC",  address: "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", decimals: 6, px: Px::Stable },
            Token { symbol: "USDbC", address: "0xd9aAEc86B65D86f6A7B5B1b0c42FFA531710b6CA", decimals: 6, px: Px::Stable },
            Token { symbol: "USDT",  address: "0xfde4C96c8593536E31F229EA8f37b2ADa2699bb2", decimals: 6, px: Px::Stable },
            Token { symbol: "WETH",  address: "0x4200000000000000000000000000000000000006", decimals: 18, px: ETH_PX },
            Token { symbol: "cbBTC", address: "0xcbB7C0000aB88B473b1f5aFd9ef808440eed33Bf", decimals: 8, px: BTC_PX },
        ],
    },
    Chain {
        key: "optimism", name: "OP Mainnet", chain_id: 10,
        rpc: "https://mainnet.optimism.io", explorer: "https://optimistic.etherscan.io",
        usdc: "0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85",
        native_symbol: "ETH", gas_reserve: 0.0005,
        tokens: &[
            Token { symbol: "ETH",    address: NATIVE, decimals: 18, px: ETH_PX },
            Token { symbol: "USDC",   address: "0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85", decimals: 6, px: Px::Stable },
            Token { symbol: "USDC.e", address: "0x7F5c764cBc14f9669B88837ca1490cCa17c31607", decimals: 6, px: Px::Stable },
            Token { symbol: "USDT",   address: "0x94b008aA00579c1307B0EF2c499aD98a8ce58e58", decimals: 6, px: Px::Stable },
            Token { symbol: "WETH",   address: "0x4200000000000000000000000000000000000006", decimals: 18, px: ETH_PX },
            Token { symbol: "OP",     address: "0x4200000000000000000000000000000000000042", decimals: 18, px: Px::Coin("OP") },
        ],
    },
    Chain {
        key: "polygon", name: "Polygon", chain_id: 137,
        rpc: "https://polygon-bor-rpc.publicnode.com", explorer: "https://polygonscan.com",
        usdc: "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359",
        native_symbol: "POL", gas_reserve: 0.5,
        tokens: &[
            Token { symbol: "POL",    address: NATIVE, decimals: 18, px: Px::Coin("POL") },
            Token { symbol: "USDC",   address: "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359", decimals: 6, px: Px::Stable },
            Token { symbol: "USDC.e", address: "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174", decimals: 6, px: Px::Stable },
            Token { symbol: "USDT",   address: "0xc2132D05D31c914a87C6611C10748AEb04B58e8F", decimals: 6, px: Px::Stable },
            Token { symbol: "DAI",    address: "0x8f3Cf7ad23Cd3CaDbD9735AFf958023239c6A063", decimals: 18, px: Px::Stable },
            Token { symbol: "WETH",   address: "0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619", decimals: 18, px: ETH_PX },
        ],
    },
    Chain {
        key: "bsc", name: "BNB Chain", chain_id: 56,
        rpc: "https://bsc-rpc.publicnode.com", explorer: "https://bscscan.com",
        usdc: "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d",
        native_symbol: "BNB", gas_reserve: 0.003,
        tokens: &[
            Token { symbol: "BNB",  address: NATIVE, decimals: 18, px: Px::Coin("BNB") },
            // BNB Chain's bridged stables are 18-decimal, not 6.
            Token { symbol: "USDT", address: "0x55d398326f99059fF775485246999027B3197955", decimals: 18, px: Px::Stable },
            Token { symbol: "USDC", address: "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d", decimals: 18, px: Px::Stable },
            Token { symbol: "WBNB", address: "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c", decimals: 18, px: Px::Coin("BNB") },
            Token { symbol: "BTCB", address: "0x7130d2A12B9BCbFAe4f2634d864A1Ee1Ce3Ead9c", decimals: 18, px: BTC_PX },
        ],
    },
    Chain {
        key: "avalanche", name: "Avalanche", chain_id: 43114,
        rpc: "https://avalanche-c-chain-rpc.publicnode.com", explorer: "https://snowtrace.io",
        usdc: "0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E",
        native_symbol: "AVAX", gas_reserve: 0.05,
        tokens: &[
            Token { symbol: "AVAX",   address: NATIVE, decimals: 18, px: Px::Coin("AVAX") },
            Token { symbol: "USDC",   address: "0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E", decimals: 6, px: Px::Stable },
            Token { symbol: "USDT",   address: "0x9702230A8Ea53601f5cD2dc00fDBc13d4dF4A8c7", decimals: 6, px: Px::Stable },
            Token { symbol: "USDC.e", address: "0xA7D7079b0FEaD91F3e65f86E8915Cb59c1a4C664", decimals: 6, px: Px::Stable },
            Token { symbol: "WAVAX",  address: "0xB31f66AA3C1e785363F0875A1B74E27b85FD66c7", decimals: 18, px: Px::Coin("AVAX") },
        ],
    },
    Chain {
        key: "linea", name: "Linea", chain_id: 59144,
        rpc: "https://linea-rpc.publicnode.com", explorer: "https://lineascan.build",
        usdc: "0x176211869cA2b568f2A7D4EE941E073a821EE1ff",
        native_symbol: "ETH", gas_reserve: 0.0005,
        tokens: &[
            Token { symbol: "ETH",  address: NATIVE, decimals: 18, px: ETH_PX },
            Token { symbol: "USDC", address: "0x176211869cA2b568f2A7D4EE941E073a821EE1ff", decimals: 6, px: Px::Stable },
            Token { symbol: "USDT", address: "0xA219439258ca9da29E9Cc4cE5596924745e12B93", decimals: 6, px: Px::Stable },
            Token { symbol: "WETH", address: "0xe5D7C2a44FfDDf6b295A15c148167daaAf5Cf34f", decimals: 18, px: ETH_PX },
        ],
    },
    Chain {
        key: "scroll", name: "Scroll", chain_id: 534352,
        rpc: "https://scroll-rpc.publicnode.com", explorer: "https://scrollscan.com",
        usdc: "0x06eFdBFf2a14a7c8E15944D1F4A48F9F95F663A4",
        native_symbol: "ETH", gas_reserve: 0.0005,
        tokens: &[
            Token { symbol: "ETH",  address: NATIVE, decimals: 18, px: ETH_PX },
            Token { symbol: "USDC", address: "0x06eFdBFf2a14a7c8E15944D1F4A48F9F95F663A4", decimals: 6, px: Px::Stable },
            Token { symbol: "USDT", address: "0xf55BEC9cafDbE8730f096Aa55dad6D22d44099Df", decimals: 6, px: Px::Stable },
            Token { symbol: "WETH", address: "0x5300000000000000000000000000000000000004", decimals: 18, px: ETH_PX },
        ],
    },
    Chain {
        key: "zksync", name: "zkSync Era", chain_id: 324,
        rpc: "https://mainnet.era.zksync.io", explorer: "https://era.zksync.network",
        usdc: "0x1d17CBcF0D6D143135aE902365D2E5e2A16538D4",
        native_symbol: "ETH", gas_reserve: 0.0008,
        tokens: &[
            Token { symbol: "ETH",    address: NATIVE, decimals: 18, px: ETH_PX },
            Token { symbol: "USDC",   address: "0x1d17CBcF0D6D143135aE902365D2E5e2A16538D4", decimals: 6, px: Px::Stable },
            Token { symbol: "USDC.e", address: "0x3355df6D4c9C3035724Fd0e3914dE96A5a83aaf4", decimals: 6, px: Px::Stable },
            Token { symbol: "USDT",   address: "0x493257fD37EDB34451f62EDf8D2a0C418852bA4C", decimals: 6, px: Px::Stable },
            Token { symbol: "WETH",   address: "0x5AEa5775959fBC2557Cc8789bC1bf90A239D9a91", decimals: 18, px: ETH_PX },
        ],
    },
    Chain {
        key: "gnosis", name: "Gnosis", chain_id: 100,
        rpc: "https://gnosis-rpc.publicnode.com", explorer: "https://gnosisscan.io",
        usdc: "0x2a22f9c3b484c3629090FeED35F17Ff8F88f76F0",
        // The gas coin IS a dollar stable, so the native row prices at $1.
        native_symbol: "xDAI", gas_reserve: 0.05,
        tokens: &[
            Token { symbol: "xDAI",   address: NATIVE, decimals: 18, px: Px::Stable },
            Token { symbol: "USDC.e", address: "0x2a22f9c3b484c3629090FeED35F17Ff8F88f76F0", decimals: 6, px: Px::Stable },
            Token { symbol: "USDC",   address: "0xDDAfbb505ad214D7b80b1f830fcCc89B60fb7A83", decimals: 6, px: Px::Stable },
            Token { symbol: "USDT",   address: "0x4ECaBa5870353805a9F068101A40E0f32ed605C6", decimals: 6, px: Px::Stable },
            Token { symbol: "WETH",   address: "0x6A023CCd1ff6F2045C3309768eAd9E68F978f6e1", decimals: 18, px: ETH_PX },
        ],
    },
    Chain {
        key: "unichain", name: "Unichain", chain_id: 130,
        rpc: "https://unichain-rpc.publicnode.com", explorer: "https://uniscan.xyz",
        usdc: "0x078D782b760474a361dDA0AF3839290b0EF57AD6",
        native_symbol: "ETH", gas_reserve: 0.0005,
        tokens: &[
            Token { symbol: "ETH",   address: NATIVE, decimals: 18, px: ETH_PX },
            Token { symbol: "USDC",  address: "0x078D782b760474a361dDA0AF3839290b0EF57AD6", decimals: 6, px: Px::Stable },
            Token { symbol: "USDT0", address: "0x9151434b16b9763660705744891fA906F660EcC5", decimals: 6, px: Px::Stable },
            Token { symbol: "WETH",  address: "0x4200000000000000000000000000000000000006", decimals: 18, px: ETH_PX },
        ],
    },
];

pub fn chain_by_id(id: u64) -> Option<&'static Chain> {
    CHAINS.iter().find(|c| c.chain_id == id)
}

/// The Multicall3 deployment to use on a chain. zkSync Era's CREATE2
/// derivation differs from everyone else's, so its copy lives at a
/// different address; using the canonical one there would read every
/// balance as "chain unreachable".
fn multicall_for(chain_id: u64) -> &'static str {
    if chain_id == 324 { MULTICALL3_ZKSYNC } else { MULTICALL3 }
}

// ── Any EVM chain / any token (LI.FI registry) ──────────────────────────

/// A source chain resolved at runtime — one of `CHAINS`, or any EVM chain
/// in LI.FI's registry. Owned, so both kinds flow through the same code.
#[derive(Clone, Debug)]
pub struct AnyChain {
    pub chain_id: u64,
    pub key: String,
    pub name: String,
    pub rpc: String,
    pub explorer: String,
    pub native_symbol: String,
    pub native_decimals: u32,
    pub logo: Option<String>,
    /// True for the twelve chains with a curated token list.
    pub builtin: bool,
}

impl AnyChain {
    fn from_builtin(c: &Chain) -> AnyChain {
        AnyChain {
            chain_id: c.chain_id, key: c.key.into(), name: c.name.into(),
            rpc: c.rpc.into(), explorer: c.explorer.into(),
            native_symbol: c.native_symbol.into(), native_decimals: 18,
            logo: None, builtin: true,
        }
    }

    fn json(&self) -> Value {
        json!({
            "chainId": self.chain_id,
            "chainIdHex": format!("0x{:x}", self.chain_id),
            "key": self.key,
            "name": self.name,
            "rpcUrl": self.rpc,
            "explorerUrl": self.explorer,
            "nativeSymbol": self.native_symbol,
            "nativeDecimals": self.native_decimals,
            "logo": self.logo,
            "builtin": self.builtin,
        })
    }
}

/// Parse one entry of LI.FI's `/chains?chainTypes=EVM`. Anything without a
/// usable RPC is dropped — we could not read a balance there anyway.
fn parse_lifi_chain(v: &Value) -> Option<AnyChain> {
    let id = v.get("id")?.as_u64()?;
    if id == HL_CHAIN_ID || v.get("mainnet").and_then(|m| m.as_bool()) == Some(false) {
        return None;
    }
    let mm = v.get("metamask");
    let rpc = mm
        .and_then(|m| m.get("rpcUrls"))
        .and_then(|r| r.as_array())
        .and_then(|a| a.iter().filter_map(|u| u.as_str()).find(|u| u.starts_with("https://")))?
        .to_string();
    let explorer = mm
        .and_then(|m| m.get("blockExplorerUrls"))
        .and_then(|r| r.as_array())
        .and_then(|a| a.first())
        .and_then(|u| u.as_str())
        .unwrap_or("")
        .trim_end_matches('/')
        .to_string();
    let native = mm.and_then(|m| m.get("nativeCurrency"));
    Some(AnyChain {
        chain_id: id,
        key: v.get("key").and_then(|k| k.as_str()).unwrap_or("").to_string(),
        name: v.get("name").and_then(|k| k.as_str()).unwrap_or("").to_string(),
        rpc,
        explorer,
        native_symbol: native
            .and_then(|n| n.get("symbol")).and_then(|s| s.as_str())
            .or_else(|| v.get("coin").and_then(|c| c.as_str()))
            .unwrap_or("ETH").to_string(),
        native_decimals: native
            .and_then(|n| n.get("decimals")).and_then(|d| d.as_u64())
            .unwrap_or(18) as u32,
        logo: v.get("logoURI").and_then(|l| l.as_str()).map(String::from),
        builtin: false,
    })
}

type Stamped<T> = (std::time::Instant, T);
static LIFI_CHAINS: std::sync::OnceLock<parking_lot::RwLock<Option<Stamped<Vec<AnyChain>>>>> =
    std::sync::OnceLock::new();
const LIFI_CHAINS_TTL: std::time::Duration = std::time::Duration::from_secs(6 * 3600);

/// Every EVM source chain: the built-ins first (our curated RPCs win), then
/// whatever else LI.FI routes from. Cached 6h; a failed refresh keeps the
/// stale list, and with no list at all the built-ins still answer.
pub async fn evm_chains(http: &reqwest::Client) -> Vec<AnyChain> {
    let cell = LIFI_CHAINS.get_or_init(|| parking_lot::RwLock::new(None));
    let cached = cell.read().clone();
    let fresh = cached.as_ref().map(|(t, _)| t.elapsed() < LIFI_CHAINS_TTL).unwrap_or(false);
    let remote = if fresh {
        cached.map(|(_, v)| v).unwrap_or_default()
    } else {
        let got = async {
            let v: Value = http
                .get(format!("{LIFI}/chains?chainTypes=EVM"))
                .timeout(std::time::Duration::from_secs(15))
                .send().await.ok()?
                .json().await.ok()?;
            let list: Vec<AnyChain> = v.get("chains")?.as_array()?
                .iter().filter_map(parse_lifi_chain).collect();
            (!list.is_empty()).then_some(list)
        }.await;
        match got {
            Some(list) => {
                *cell.write() = Some((std::time::Instant::now(), list.clone()));
                list
            }
            None => cached.map(|(_, v)| v).unwrap_or_default(),
        }
    };
    merge_chains(remote)
}

fn merge_chains(remote: Vec<AnyChain>) -> Vec<AnyChain> {
    let mut out: Vec<AnyChain> = CHAINS.iter().map(AnyChain::from_builtin).collect();
    for o in out.iter_mut() {
        if let Some(rc) = remote.iter().find(|c| c.chain_id == o.chain_id) {
            o.logo = rc.logo.clone();
        }
    }
    let mut extra: Vec<AnyChain> = remote
        .into_iter()
        .filter(|c| chain_by_id(c.chain_id).is_none())
        .collect();
    extra.sort_by(|a, b| a.name.to_lowercase().cmp(&b.name.to_lowercase()));
    out.extend(extra);
    out
}

/// Resolve any EVM source chain id.
pub async fn any_chain(http: &reqwest::Client, id: u64) -> Option<AnyChain> {
    if let Some(c) = chain_by_id(id) {
        return Some(AnyChain::from_builtin(c));
    }
    evm_chains(http).await.into_iter().find(|c| c.chain_id == id)
}

/// A token as LI.FI knows it — symbol, decimals, and a USD price.
#[derive(Clone, Debug)]
pub struct TokenInfo {
    pub chain_id: u64,
    pub address: String,
    pub symbol: String,
    pub name: String,
    pub decimals: u32,
    pub price_usd: Option<f64>,
    pub logo: Option<String>,
    pub verified: bool,
}

impl TokenInfo {
    fn native(&self) -> bool { self.address == NATIVE }
    fn json(&self) -> Value {
        json!({
            "chainId": self.chain_id,
            "address": self.address,
            "symbol": self.symbol,
            "name": self.name,
            "decimals": self.decimals,
            "native": self.native(),
            "priceUsd": self.price_usd,
            "logo": self.logo,
            "verified": self.verified,
        })
    }
}

fn parse_lifi_token(v: &Value) -> Option<TokenInfo> {
    let address = v.get("address")?.as_str()?.to_string();
    Some(TokenInfo {
        chain_id: v.get("chainId")?.as_u64()?,
        symbol: v.get("symbol").and_then(|s| s.as_str()).unwrap_or("?").to_string(),
        name: v.get("name").and_then(|s| s.as_str()).unwrap_or("").to_string(),
        decimals: v.get("decimals")?.as_u64()? as u32,
        price_usd: v.get("priceUSD")
            .and_then(|p| p.as_str().and_then(|s| s.parse::<f64>().ok()).or_else(|| p.as_f64()))
            .filter(|p| *p > 0.0),
        logo: v.get("logoURI").and_then(|l| l.as_str()).map(String::from),
        verified: v.get("verificationStatus").and_then(|s| s.as_str()) == Some("verified"),
        address: if address == NATIVE { NATIVE.to_string() } else { address },
    })
}

static TOKENS: std::sync::OnceLock<dashmap::DashMap<String, Stamped<Option<TokenInfo>>>> =
    std::sync::OnceLock::new();
const TOKEN_TTL: std::time::Duration = std::time::Duration::from_secs(300);

/// Look a token up on LI.FI (`native` = the chain's own coin). `None` means
/// LI.FI doesn't know it — and a token LI.FI can't name it can't route, so
/// that is the honest "unsupported" answer. Cached 5 min (prices move).
pub async fn lookup_token(http: &reqwest::Client, chain_id: u64, token: &str) -> Option<TokenInfo> {
    let token = if token.eq_ignore_ascii_case("native") { NATIVE } else { token };
    if !is_addr(token) {
        return None;
    }
    let key = format!("{chain_id}:{}", token.to_lowercase());
    let map = TOKENS.get_or_init(dashmap::DashMap::new);
    if let Some(hit) = map.get(&key) {
        if hit.0.elapsed() < TOKEN_TTL {
            return hit.1.clone();
        }
    }
    let resp = http
        .get(format!("{LIFI}/token?chain={chain_id}&token={token}"))
        .timeout(std::time::Duration::from_secs(12))
        .send().await.ok()?;
    let found = if resp.status().is_success() {
        resp.json::<Value>().await.ok().as_ref().and_then(parse_lifi_token)
    } else if resp.status().as_u16() == 404 || resp.status().as_u16() == 400 {
        None
    } else {
        // Rate limit / outage — don't cache a transient miss.
        return None;
    };
    if map.len() > 2000 {
        map.clear();
    }
    map.insert(key, (std::time::Instant::now(), found.clone()));
    found
}

/// Parse `extra=8453:0xabc…,10:native` — at most 40 entries, deduped.
pub fn parse_extra(s: &str) -> Vec<(u64, String)> {
    let mut out: Vec<(u64, String)> = Vec::new();
    for part in s.split(',').map(str::trim).filter(|p| !p.is_empty()) {
        let Some((cid, tok)) = part.split_once(':') else { continue };
        let Ok(cid) = cid.trim().parse::<u64>() else { continue };
        let tok = tok.trim().to_lowercase();
        let tok = if tok == "native" { NATIVE.to_string() } else { tok };
        if cid == HL_CHAIN_ID || !is_addr(&tok) || out.iter().any(|(c, t)| *c == cid && *t == tok) {
            continue;
        }
        out.push((cid, tok));
        if out.len() >= 40 {
            break;
        }
    }
    out
}

/// One balance read: native via eth_getBalance, ERC-20 via balanceOf.
async fn read_balance(http: &reqwest::Client, rpc_url: &str, token: &str, eoa: &str, decimals: u32) -> Option<f64> {
    let res = if token == NATIVE {
        rpc(http, rpc_url, "eth_getBalance", json!([eoa, "latest"])).await.ok()?
    } else {
        let who = pad32(eoa.trim_start_matches("0x"));
        rpc(http, rpc_url, "eth_call",
            json!([{"to": token, "data": format!("0x70a08231{who}")}, "latest"])).await.ok()?
    };
    let hex_s = res.as_str()?.trim_start_matches("0x");
    if hex_s.is_empty() {
        return Some(0.0);
    }
    let padded = format!("{:0>64}", hex_s);
    let bytes = hex::decode(&padded[padded.len().saturating_sub(64)..]).ok()?;
    Some(word_units(&bytes, decimals))
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
        // `direct` = "USDC here goes to Hyperliquid's own bridge with no
        // third party in the path". Only Arbitrum can do that.
        "direct": c.chain_id == ARBITRUM_CHAIN_ID,
        "tokens": c.tokens.iter().map(|t| json!({
            "symbol": t.symbol,
            "address": t.address,
            "decimals": t.decimals,
            "native": t.is_native(),
        })).collect::<Vec<_>>(),
    })
}

fn err(status: StatusCode, msg: impl std::fmt::Display) -> (StatusCode, Json<Value>) {
    (status, Json(json!({"error": msg.to_string()})))
}

fn is_addr(s: &str) -> bool {
    s.len() == 42 && s.starts_with("0x") && s[2..].chars().all(|c| c.is_ascii_hexdigit())
}

// ── JSON-RPC + ABI helpers ──────────────────────────────────────────────

async fn rpc(http: &reqwest::Client, url: &str, method: &str, params: Value) -> anyhow::Result<Value> {
    let body = json!({"jsonrpc": "2.0", "id": 1, "method": method, "params": params});
    let v: Value = http
        .post(url)
        .json(&body)
        .timeout(std::time::Duration::from_secs(8))
        .send()
        .await?
        .json()
        .await?;
    if let Some(e) = v.get("error") {
        anyhow::bail!("rpc error: {e}");
    }
    Ok(v.get("result").cloned().unwrap_or(Value::Null))
}

fn pad32(hex_no_prefix: &str) -> String {
    format!("{:0>64}", hex_no_prefix)
}

/// Scale a 32-byte big-endian word into a display f64. A u128 covers any
/// real balance; anything wider is a broken token, so it reads as 0.
fn word_units(word: &[u8], decimals: u32) -> f64 {
    if word.len() < 32 {
        return 0.0;
    }
    let w = &word[16..32]; // low 128 bits
    let mut n: u128 = 0;
    for b in w {
        n = (n << 8) | *b as u128;
    }
    n as f64 / 10f64.powi(decimals as i32)
}

/// ABI-encode `aggregate3((address,bool,bytes)[])` for a list of
/// (target, calldata) pairs. Every call is `allowFailure: true` — a token
/// that isn't a contract on this chain must not sink the whole batch.
fn encode_aggregate3(calls: &[(String, String)]) -> String {
    let n = calls.len();
    // Each element: target + allowFailure + bytes-offset + bytes-len + padded data.
    let padded: Vec<Vec<u8>> = calls
        .iter()
        .map(|(_, data)| {
            let mut b = hex::decode(data.trim_start_matches("0x")).unwrap_or_default();
            while b.len() % 32 != 0 {
                b.push(0);
            }
            b
        })
        .collect();

    let mut heads = String::new();
    let mut tails = String::new();
    // Element offsets are relative to the start of the array's data section,
    // which begins after the n head words.
    let mut cursor = 32 * n;
    for (i, (target, data)) in calls.iter().enumerate() {
        heads.push_str(&pad32(&format!("{:x}", cursor)));
        let raw_len = hex::decode(data.trim_start_matches("0x")).map(|b| b.len()).unwrap_or(0);
        tails.push_str(&pad32(target.trim_start_matches("0x")));
        tails.push_str(&pad32("1")); // allowFailure
        tails.push_str(&pad32("60")); // bytes start, relative to this tuple
        tails.push_str(&pad32(&format!("{:x}", raw_len)));
        tails.push_str(&hex::encode(&padded[i]));
        cursor += 32 * 3 + 32 + padded[i].len();
    }

    format!(
        "0x82ad56cb{}{}{}{}",
        pad32("20"),                        // offset to the array
        pad32(&format!("{:x}", n)),         // array length
        heads,
        tails,
    )
}

/// Decode `(bool success, bytes returnData)[]` into one 32-byte word per
/// call, or None where the call failed / returned nothing usable.
fn decode_aggregate3(ret: &str) -> Vec<Option<Vec<u8>>> {
    let bytes = match hex::decode(ret.trim_start_matches("0x")) {
        Ok(b) => b,
        Err(_) => return vec![],
    };
    let word = |off: usize| -> Option<usize> {
        let w = bytes.get(off..off + 32)?;
        // Offsets and lengths always fit in the low 8 bytes.
        Some(u64::from_be_bytes(w[24..32].try_into().ok()?) as usize)
    };
    let Some(arr) = word(0) else { return vec![] };
    let Some(n) = word(arr) else { return vec![] };
    let data = arr + 32;
    (0..n)
        .map(|i| {
            let tuple = data + word(data + i * 32)?;
            let success = word(tuple)? == 1;
            let boff = tuple + word(tuple + 32)?;
            let len = word(boff)?;
            if !success || len < 32 {
                return None;
            }
            bytes.get(boff + 32..boff + 64).map(|s| s.to_vec())
        })
        .collect()
}

/// Every token balance on one chain in a single eth_call. Returns raw
/// scaled amounts; `None` for the whole chain means the RPC was unreachable
/// (which we surface as `ok: false` rather than as a wallet holding zero).
async fn chain_balances(http: &reqwest::Client, c: &Chain, eoa: &str) -> Option<Vec<f64>> {
    let who = pad32(eoa.trim_start_matches("0x"));
    let multicall = multicall_for(c.chain_id);
    let calls: Vec<(String, String)> = c
        .tokens
        .iter()
        .map(|t| {
            if t.is_native() {
                // Multicall3.getEthBalance(address)
                (multicall.to_string(), format!("0x4d2301cc{who}"))
            } else {
                // ERC20.balanceOf(address)
                (t.address.to_string(), format!("0x70a08231{who}"))
            }
        })
        .collect();

    let data = encode_aggregate3(&calls);
    let res = rpc(
        http,
        c.rpc,
        "eth_call",
        json!([{"to": multicall, "data": data}, "latest"]),
    )
    .await
    .ok()?;
    let words = decode_aggregate3(res.as_str()?);
    if words.len() != c.tokens.len() {
        return None;
    }
    Some(
        words
            .iter()
            .zip(c.tokens)
            .map(|(w, t)| w.as_ref().map(|w| word_units(w, t.decimals)).unwrap_or(0.0))
            .collect(),
    )
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
        // Deposits land in the Hyperliquid perps account directly.
        "toChainId": HL_CHAIN_ID,
        "toUsdc": HL_PERPS_USDC,
        // Kept for the withdrawal direction, which still exits via Arbitrum.
        "arbitrumChainId": ARBITRUM_CHAIN_ID,
        "arbitrumUsdc": ARBITRUM_USDC,
        "minDepositUsd": 5.0,
        "chains": chains,
    }))
}

/// Every EVM chain a deposit can start from — the twelve built-ins (with
/// curated token lists) plus everything else LI.FI routes from.
pub async fn deposit_evm_chains(State(s): State<AppState>) -> Json<Value> {
    if s.hl.testnet {
        return Json(json!({"testnet": true, "chains": []}));
    }
    let chains = evm_chains(&s.http).await;
    Json(json!({
        "testnet": false,
        "count": chains.len(),
        "chains": chains.iter().map(AnyChain::json).collect::<Vec<_>>(),
    }))
}

#[derive(Deserialize)]
pub struct TokenQuery {
    chain_id: u64,
    /// ERC-20 address, or `native`.
    address: String,
}

/// Look up one token on any EVM chain: symbol, decimals, USD price and
/// whether LI.FI can route it. Used by "add a token" before it is saved.
pub async fn deposit_token(
    State(s): State<AppState>,
    Query(q): Query<TokenQuery>,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    let addr = q.address.trim();
    if !(addr.eq_ignore_ascii_case("native") || is_addr(addr)) {
        return Err(err(StatusCode::BAD_REQUEST, "address must be a 0x token address or 'native'"));
    }
    let chain = any_chain(&s.http, q.chain_id).await
        .ok_or_else(|| err(StatusCode::BAD_REQUEST, "LI.FI doesn't route from this chain"))?;
    let tok = lookup_token(&s.http, chain.chain_id, addr).await.ok_or_else(|| {
        err(StatusCode::NOT_FOUND, format!(
            "LI.FI doesn't know this token on {} — check the address (it must be the token contract on that chain)",
            chain.name))
    })?;
    let mut v = tok.json();
    v["chain"] = chain.json();
    // Already scanned by default? Then adding it changes nothing.
    v["builtin"] = json!(chain_by_id(chain.chain_id)
        .map(|c| c.tokens.iter().any(|t| t.address.eq_ignore_ascii_case(&tok.address)))
        .unwrap_or(false));
    Ok(Json(v))
}

#[derive(Deserialize)]
pub struct BalancesQuery {
    eoa: String,
    /// Custom tokens to scan as well: `<chainId>:<address|native>,…`.
    #[serde(default)]
    extra: Option<String>,
}

pub async fn deposit_balances(
    State(s): State<AppState>,
    Query(q): Query<BalancesQuery>,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    let eoa = q.eoa.trim().to_lowercase();
    if !is_addr(&eoa) {
        return Err(err(StatusCode::BAD_REQUEST, "eoa must be a 0x address"));
    }
    if s.hl.testnet {
        return Ok(Json(json!({"eoa": eoa, "chains": [], "sources": []})));
    }

    // Price non-stables off Hyperliquid's own mids — no extra dependency.
    // A missing mid yields `priceUsd: null` rather than $0: a token whose
    // price we can't read is still perfectly depositable, and pricing it at
    // zero would silently hide the user's money.
    let mids = s.hl.all_mids().await.unwrap_or(Value::Null);
    let px = |t: &Token| -> Option<f64> {
        match t.px {
            Px::Stable => Some(1.0),
            Px::Coin(c) => mids
                .get(c)
                .and_then(|v| v.as_str().and_then(|s| s.parse::<f64>().ok()).or_else(|| v.as_f64()))
                .filter(|p| *p > 0.0),
        }
    };

    let fetches = CHAINS.iter().map(|c| {
        let http = s.http.clone();
        let eoa = eoa.clone();
        async move { (c, chain_balances(&http, c, &eoa).await) }
    });
    let results = futures::future::join_all(fetches).await;

    let mut chains = Vec::new();
    let mut sources = Vec::new();
    for (c, bals) in results {
        let ok = bals.is_some();
        let bals = bals.unwrap_or_else(|| vec![0.0; c.tokens.len()]);

        for (t, &balance) in c.tokens.iter().zip(bals.iter()) {
            if balance <= 0.0 {
                continue;
            }
            let price = px(t);
            // Natives pay for their own gas, so MAX can't spend the lot.
            let max = if t.is_native() {
                (balance - c.gas_reserve).max(0.0)
            } else {
                balance
            };
            if max <= 0.0 {
                continue;
            }
            sources.push(json!({
                "chainKey": c.key,
                "chainName": c.name,
                "chainId": c.chain_id,
                "symbol": t.symbol,
                "address": t.address,
                "decimals": t.decimals,
                "native": t.is_native(),
                "balance": balance,
                "max": max,
                "priceUsd": price,
                "usd": price.map(|p| balance * p),
                "gasReserve": if t.is_native() { c.gas_reserve } else { 0.0 },
                // USDC on Arbitrum is the one source that needs no router.
                "direct": c.chain_id == ARBITRUM_CHAIN_ID && t.address == c.usdc,
            }));
        }

        // Legacy per-chain shape: the withdrawal flow reads Arbitrum USDC
        // from here, so it keeps working unchanged.
        let native_tok = c.tokens.iter().position(|t| t.is_native());
        let usdc_tok = c.tokens.iter().position(|t| t.address == c.usdc);
        let native_bal = native_tok.map(|i| bals[i]).unwrap_or(0.0);
        let native_px = native_tok.and_then(|i| px(&c.tokens[i]));
        chains.push(json!({
            "key": c.key,
            "chainId": c.chain_id,
            "name": c.name,
            "ok": ok,
            "native": {
                "symbol": c.native_symbol,
                "balance": native_bal,
                "usd": native_px.map(|p| native_bal * p).unwrap_or(0.0),
                "priceUsd": native_px.unwrap_or(0.0),
                "gasReserve": c.gas_reserve,
            },
            "usdc": {
                "balance": usdc_tok.map(|i| bals[i]).unwrap_or(0.0),
                "usd": usdc_tok.map(|i| bals[i]).unwrap_or(0.0),
            },
        }));
    }

    // Custom tokens — any EVM chain LI.FI routes from. Each is one direct
    // read (no multicall: arbitrary chains don't all have Multicall3), priced
    // off LI.FI's own token price. A token that's already in a built-in list
    // was scanned above and is skipped here.
    let extra = q.extra.as_deref().map(parse_extra).unwrap_or_default();
    let mut custom = Vec::new();
    if !extra.is_empty() {
        let reads = extra.into_iter()
            .filter(|(cid, tok)| !chain_by_id(*cid)
                .map(|c| c.tokens.iter().any(|t| t.address.eq_ignore_ascii_case(tok)))
                .unwrap_or(false))
            .map(|(cid, tok)| {
                let http = s.http.clone();
                let eoa = eoa.clone();
                async move {
                    let chain = any_chain(&http, cid).await;
                    let info = lookup_token(&http, cid, &tok).await;
                    let bal = match (&chain, &info) {
                        (Some(c), Some(t)) => read_balance(&http, &c.rpc, &t.address, &eoa, t.decimals).await,
                        _ => None,
                    };
                    (cid, tok, chain, info, bal)
                }
            });
        for (cid, tok, chain, info, bal) in futures::future::join_all(reads).await {
            let (Some(c), Some(t)) = (chain, info) else {
                custom.push(json!({"chainId": cid, "address": tok, "ok": false,
                    "error": "LI.FI doesn't know this chain or token — it can't be routed"}));
                continue;
            };
            let Some(balance) = bal else {
                custom.push(json!({"chainId": cid, "address": t.address, "symbol": t.symbol,
                    "ok": false, "error": format!("{} RPC unreachable", c.name)}));
                continue;
            };
            // Natives keep ~$1 of gas back (or a sliver when unpriced).
            let reserve = if t.native() {
                t.price_usd.map(|p| (1.0 / p).min(balance)).unwrap_or(balance * 0.02)
            } else { 0.0 };
            custom.push(json!({"chainId": cid, "address": t.address, "symbol": t.symbol,
                "ok": true, "balance": balance}));
            let max = (balance - reserve).max(0.0);
            if balance <= 0.0 || max <= 0.0 {
                continue;
            }
            sources.push(json!({
                "chainKey": c.key,
                "chainName": c.name,
                "chainId": c.chain_id,
                "symbol": t.symbol,
                "address": t.address,
                "decimals": t.decimals,
                "native": t.native(),
                "balance": balance,
                "max": max,
                "priceUsd": t.price_usd,
                "usd": t.price_usd.map(|p| balance * p),
                "gasReserve": reserve,
                "direct": false,
                "custom": true,
            }));
        }
    }

    // Richest first — the default pick should be the one that can actually
    // clear the minimum. Unpriced tokens sort last but stay selectable.
    sources.sort_by(|a, b| {
        let v = |x: &Value| x.get("usd").and_then(|u| u.as_f64()).unwrap_or(-1.0);
        v(b).partial_cmp(&v(a)).unwrap_or(std::cmp::Ordering::Equal)
    });

    Ok(Json(json!({"eoa": eoa, "chains": chains, "sources": sources, "custom": custom})))
}

#[derive(Deserialize)]
pub struct QuoteBody {
    /// Source chain id — any entry in `CHAINS`.
    pub from_chain_id: u64,
    /// `"usdc"`, `"native"`, or any token address listed for that chain.
    pub token: String,
    /// Amount in human token units, e.g. "120.5" USDC or "0.05" ETH.
    pub amount: String,
    /// The depositor — both the payer and the Hyperliquid account credited.
    pub eoa: String,
    /// Destination chain id. Defaults to Hyperliquid Core, i.e. a deposit.
    /// Withdrawals set this to bridge Arbitrum USDC out to another chain.
    #[serde(default)]
    pub to_chain_id: Option<u64>,
    /// Recipient on the destination chain — defaults to `eoa`.
    #[serde(default)]
    pub to_address: Option<String>,
}

/// A resolved source token (built-in or looked up).
struct Tok {
    symbol: String,
    address: String,
    decimals: u32,
}

/// Resolve a `token` field against a chain's token list.
fn resolve_token(c: &'static Chain, token: &str) -> Option<&'static Token> {
    match token {
        "native" => c.tokens.iter().find(|t| t.is_native()),
        "usdc" => c.tokens.iter().find(|t| t.address.eq_ignore_ascii_case(c.usdc)),
        other => c.tokens.iter().find(|t| {
            t.address.eq_ignore_ascii_case(other) || t.symbol.eq_ignore_ascii_case(other)
        }),
    }
}

fn usd_sum(costs: Option<&Value>) -> f64 {
    costs
        .and_then(|v| v.as_array())
        .map(|a| {
            a.iter()
                .filter_map(|c| c.get("amountUSD").and_then(|x| x.as_str()))
                .filter_map(|s| s.parse::<f64>().ok())
                .sum()
        })
        .unwrap_or(0.0)
}

pub async fn deposit_quote(
    State(s): State<AppState>,
    Json(b): Json<QuoteBody>,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    if s.hl.testnet {
        return Err(err(StatusCode::BAD_REQUEST, "cross-chain deposit is mainnet-only"));
    }
    // Built-in chains resolve against their curated list (symbols, `usdc`,
    // `native`); anything else — or an address not in that list — goes to
    // LI.FI's registry, so any EVM chain and any token LI.FI can route works.
    let chain = any_chain(&s.http, b.from_chain_id).await
        .ok_or_else(|| err(StatusCode::BAD_REQUEST, "unsupported source chain — LI.FI doesn't route from it"))?;
    let amount: f64 = b.amount.trim().parse().unwrap_or(0.0);
    if amount <= 0.0 {
        return Err(err(StatusCode::BAD_REQUEST, "amount must be > 0"));
    }
    let want = b.token.trim();
    let tok: Tok = match chain_by_id(chain.chain_id).and_then(|c| resolve_token(c, want)) {
        Some(t) => Tok { symbol: t.symbol.to_string(), address: t.address.to_string(), decimals: t.decimals },
        None => lookup_token(&s.http, chain.chain_id, want).await
            .map(|t| Tok { symbol: t.symbol, address: t.address, decimals: t.decimals })
            .ok_or_else(|| err(StatusCode::BAD_REQUEST,
                format!("unknown token on {} — pass its 0x address", chain.name)))?,
    };

    // Destination: Hyperliquid Core by default (deposit). Withdrawals name a
    // real EVM chain, which must be one we know how to price and explore.
    let (to_chain_id, to_token, to_name) = match b.to_chain_id {
        None | Some(HL_CHAIN_ID) => (HL_CHAIN_ID, HL_PERPS_USDC, "Hyperliquid"),
        Some(id) => {
            let c = chain_by_id(id)
                .ok_or_else(|| err(StatusCode::BAD_REQUEST, "unsupported destination chain"))?;
            (c.chain_id, c.usdc, c.name)
        }
    };
    let to_address = b.to_address.as_deref().unwrap_or(&b.eoa).trim();
    if !is_addr(to_address) {
        return Err(err(StatusCode::BAD_REQUEST, "to_address must be a 0x address"));
    }
    // Same-chain USDC never needs a route — the caller does a plain transfer
    // (to Hyperliquid's bridge on Arbitrum, or to the recipient elsewhere).
    if chain.chain_id == to_chain_id && tok.address.eq_ignore_ascii_case(to_token) {
        return Err(err(StatusCode::BAD_REQUEST, "same-chain USDC moves don't need a bridge route"));
    }
    let from_amount = ((amount * 10f64.powi(tok.decimals as i32)).round() as u128).to_string();

    let url = format!(
        "{LIFI}/quote?fromChain={}&toChain={}&fromToken={}&toToken={}\
         &fromAmount={}&fromAddress={}&toAddress={}&slippage=0.005&integrator=mod-hyperliquid",
        chain.chain_id, to_chain_id, tok.address, to_token, from_amount, b.eoa, to_address,
    );
    let resp = s
        .http
        .get(&url)
        .timeout(std::time::Duration::from_secs(25))
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
    // Destination is always USDC, 6 decimals, on every chain we route to.
    let to_amount = |k: &str| {
        est.get(k)
            .and_then(|x| x.as_str())
            .and_then(|x| x.parse::<f64>().ok())
            .map(|u| u / 1e6)
            .unwrap_or(0.0)
    };
    let tool = v
        .get("toolDetails")
        .and_then(|t| t.get("name"))
        .cloned()
        .or_else(|| v.get("tool").cloned())
        .unwrap_or(Value::Null);
    Ok(Json(json!({
        "tool": tool,
        "fromChainId": chain.chain_id,
        "fromChainName": chain.name,
        "fromToken": tok.address,
        "fromSymbol": tok.symbol,
        "fromAmountUnits": from_amount,
        "toChainId": to_chain_id,
        "toChainName": to_name,
        // A deposit route ends inside the Hyperliquid account itself, so no
        // follow-up transaction is needed — the client keys its flow on this.
        "landsOnHyperliquid": to_chain_id == HL_CHAIN_ID,
        "toUsdc": to_amount("toAmount"),
        "toUsdcMin": to_amount("toAmountMin"),
        "gasUsd": usd_sum(est.get("gasCosts")),
        "feeUsd": usd_sum(est.get("feeCosts")),
        "durationSec": est.get("executionDuration").cloned().unwrap_or(json!(0)),
        "approvalAddress": est.get("approvalAddress").cloned().unwrap_or(Value::Null),
        "transactionRequest": v.get("transactionRequest").cloned().unwrap_or(Value::Null),
    })))
}

#[derive(Deserialize)]
pub struct StatusQuery {
    tx_hash: String,
    from_chain_id: u64,
    /// Defaults to Hyperliquid Core (the deposit direction).
    #[serde(default)]
    to_chain_id: Option<u64>,
}

pub async fn deposit_status(
    State(s): State<AppState>,
    Query(q): Query<StatusQuery>,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    // The hash is interpolated into the LI.FI URL — pin it to a 32-byte hex
    // hash so a crafted value can't smuggle extra query parameters.
    let tx = q.tx_hash.trim();
    if !(tx.len() == 66 && tx.starts_with("0x") && tx[2..].chars().all(|c| c.is_ascii_hexdigit())) {
        return Err(err(StatusCode::BAD_REQUEST, "tx_hash must be a 0x… 32-byte transaction hash"));
    }
    let url = format!(
        "{LIFI}/status?txHash={}&fromChain={}&toChain={}",
        tx, q.from_chain_id, q.to_chain_id.unwrap_or(HL_CHAIN_ID),
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
        "receivingTxHash": v.pointer("/receiving/txHash").cloned().unwrap_or(Value::Null),
    })))
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Every chain must have a native token, its canonical USDC in the
    /// token list, well-formed addresses, and a well-formed Multicall3 —
    /// a typo in any of these would silently zero a chain.
    #[test]
    fn every_chain_has_a_native_and_a_usdc() {
        for c in CHAINS {
            assert!(c.tokens.iter().any(|t| t.is_native()), "{} has no native token", c.key);
            assert!(
                c.tokens.iter().any(|t| t.address.eq_ignore_ascii_case(c.usdc)),
                "{} usdc is not in its token list",
                c.key
            );
            assert!(is_addr(multicall_for(c.chain_id)), "{} bad multicall", c.key);
            for t in c.tokens {
                assert!(is_addr(t.address), "{} {} bad address", c.key, t.symbol);
            }
        }
    }

    /// The user-facing promise: every chain accepts USDC, and every chain
    /// accepts either ETH natively or a USDT flavor — so "deposit via
    /// ETH / USDC / USDT from any chain" holds everywhere it can.
    #[test]
    fn eth_usdc_usdt_coverage() {
        for c in CHAINS {
            assert!(
                c.tokens.iter().any(|t| t.symbol.starts_with("USDC")),
                "{} has no USDC", c.key
            );
            let has_eth = c.tokens.iter().any(|t| t.symbol == "ETH" || t.symbol == "WETH");
            let has_usdt = c.tokens.iter().any(|t| t.symbol.starts_with("USDT"));
            assert!(has_eth || has_usdt, "{} has neither ETH nor USDT", c.key);
        }
    }

    #[test]
    fn token_aliases_resolve() {
        let arb = chain_by_id(42161).unwrap();
        assert_eq!(resolve_token(arb, "native").unwrap().symbol, "ETH");
        assert_eq!(resolve_token(arb, "usdc").unwrap().address, ARBITRUM_USDC);
        assert_eq!(resolve_token(arb, "ARB").unwrap().decimals, 18);
        assert_eq!(
            resolve_token(arb, "0x912CE59144191C1204E64559FE8253a0e49E6548").unwrap().symbol,
            "ARB"
        );
        assert!(resolve_token(arb, "DOGE").is_none());
    }

    /// aggregate3 encoding is hand-rolled; a round trip against a known-good
    /// response layout is the cheapest guard against an offset regression.
    #[test]
    fn aggregate3_round_trip() {
        let who = pad32("d8da6bf26964af9d7eed9e03e53415d37aa96045");
        let calls = vec![
            (MULTICALL3.to_string(), format!("0x4d2301cc{who}")),
            ("0xaf88d065e77c8cC2239327C5EDb3A432268e5831".into(), format!("0x70a08231{who}")),
        ];
        let enc = encode_aggregate3(&calls);
        assert!(enc.starts_with("0x82ad56cb"));
        // 4-byte selector + head(2) + len(1) + 2 offsets + 2 tuples of 6 words.
        assert_eq!((enc.len() - 10) / 64, 2 + 2 + 12);

        // A response with one success (value 7) and one failure.
        let mut w = String::new();
        w.push_str(&pad32("20")); // array offset
        w.push_str(&pad32("2")); // length
        w.push_str(&pad32("40")); // elem 0 offset
        w.push_str(&pad32("e0")); // elem 1 offset
        w.push_str(&pad32("1")); // success
        w.push_str(&pad32("40")); // bytes offset
        w.push_str(&pad32("20")); // bytes length
        w.push_str(&pad32("7")); // value
        w.push_str(&pad32("0")); // success = false
        w.push_str(&pad32("40"));
        w.push_str(&pad32("0"));
        let out = decode_aggregate3(&format!("0x{w}"));
        assert_eq!(out.len(), 2);
        assert_eq!(word_units(out[0].as_ref().unwrap(), 0), 7.0);
        assert!(out[1].is_none());
    }

    #[test]
    fn decimals_scale_correctly() {
        let mut w = vec![0u8; 32];
        w[24..32].copy_from_slice(&1_500_000u64.to_be_bytes());
        assert_eq!(word_units(&w, 6), 1.5);
        assert_eq!(word_units(&w, 0), 1_500_000.0);
    }

    #[test]
    fn extra_tokens_parse_dedupe_and_reject_junk() {
        let x = parse_extra("8453:0x4ed4E862860beD51a9570b96d89aF5E1B0Efefed, 10:native,\
            8453:0x4ed4e862860bed51a9570b96d89af5e1b0efefed,1337:native,abc:0x1,5:nope,,");
        assert_eq!(x.len(), 2);
        assert_eq!(x[0], (8453, "0x4ed4e862860bed51a9570b96d89af5e1b0efefed".to_string()));
        assert_eq!(x[1], (10, NATIVE.to_string()));
        let many: String = (0..60).map(|i| format!("{i}:native,")).collect();
        assert_eq!(parse_extra(&many).len(), 40);
    }

    #[test]
    fn lifi_chain_and_token_parse() {
        let c = parse_lifi_chain(&json!({
            "id": 81457, "key": "bls", "name": "Blast", "coin": "ETH", "mainnet": true,
            "logoURI": "x.svg",
            "metamask": {"rpcUrls": ["wss://no", "https://rpc.blast.io"],
                         "blockExplorerUrls": ["https://blastscan.io/"],
                         "nativeCurrency": {"symbol": "ETH", "decimals": 18}}
        })).unwrap();
        assert_eq!(c.rpc, "https://rpc.blast.io");
        assert_eq!(c.explorer, "https://blastscan.io");
        assert!(!c.builtin);
        // Hyperliquid Core itself and RPC-less chains are not sources.
        assert!(parse_lifi_chain(&json!({"id": 1337, "metamask": {"rpcUrls": ["https://x"]}})).is_none());
        assert!(parse_lifi_chain(&json!({"id": 5, "metamask": {"rpcUrls": []}})).is_none());

        let t = parse_lifi_token(&json!({"address": "0xabc", "chainId": 8453, "symbol": "DEGEN",
            "decimals": 18, "priceUSD": "0.00095", "verificationStatus": "verified"})).unwrap();
        assert_eq!(t.decimals, 18);
        assert!((t.price_usd.unwrap() - 0.00095).abs() < 1e-12);
        assert!(t.verified);
        let z = parse_lifi_token(&json!({"address": NATIVE, "chainId": 1, "symbol": "ETH",
            "decimals": 18, "priceUSD": "0"})).unwrap();
        assert!(z.native() && z.price_usd.is_none());
    }

    #[test]
    fn merged_registry_keeps_builtins_first_and_unique() {
        let remote = vec![
            AnyChain { chain_id: 8453, key: "bas".into(), name: "Base".into(), rpc: "https://other".into(),
                explorer: "".into(), native_symbol: "ETH".into(), native_decimals: 18,
                logo: Some("b.svg".into()), builtin: false },
            AnyChain { chain_id: 81457, key: "bls".into(), name: "Blast".into(), rpc: "https://rpc.blast.io".into(),
                explorer: "".into(), native_symbol: "ETH".into(), native_decimals: 18, logo: None, builtin: false },
        ];
        let m = merge_chains(remote);
        assert_eq!(m.len(), CHAINS.len() + 1);
        let base = m.iter().find(|c| c.chain_id == 8453).unwrap();
        assert!(base.builtin && base.rpc != "https://other" && base.logo.as_deref() == Some("b.svg"));
        assert_eq!(m.last().unwrap().chain_id, 81457);
    }
}
