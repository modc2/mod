//! Bridge-in registry and live quote aggregator.
//!
//! taox's own desk (`/swap`) is one way into TAO, and a custodial one. This
//! module answers the broader question — *given N of some asset on Solana,
//! Base or Ethereum, what are all the ways to end up holding TAO, and which
//! one pays the most?* — by keeping a catalog of every route that actually
//! exists today and, for the providers that expose a key-free quote endpoint,
//! pricing all of them side by side in one request.
//!
//! Two things the ranking has to keep straight, because they are what make a
//! naive "best rate" comparison wrong:
//!
//!   * **What you end up holding.** A Jupiter swap on Solana buys canonical
//!     TAO (SPL), not the ss58 balance a Bittensor wallet stakes with. It
//!     usually prints the best headline rate precisely because it stops one
//!     hop short. `delivers` + `hops` carry that, and routes are ranked
//!     within a delivery form, never across.
//!   * **Whether the amount is even accepted.** Every instant-swap desk has a
//!     min and a max. A route that can't take your size isn't a worse rate,
//!     it's not a route — those come back `unavailable` with the bound that
//!     failed, rather than being silently dropped.

use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};
use serde_json::Value;

// ── source assets ──────────────────────────────────────────────────

/// A source asset, keyed `{chain}:{symbol}`. These are the eight things you
/// can actually be holding on Solana / Base / Ethereum that some route will
/// turn into TAO.
#[derive(Debug, Clone, Serialize)]
pub struct SourceAsset {
    pub key: &'static str,
    pub chain: &'static str,
    pub chain_label: &'static str,
    pub symbol: &'static str,
    pub decimals: u32,
    /// Contract / mint address. Empty for a chain's native coin.
    pub contract: &'static str,
    pub wallet: &'static str,
}

pub const SOURCE_ASSETS: &[SourceAsset] = &[
    SourceAsset { key: "sol:SOL",   chain: "solana",   chain_label: "Solana",   symbol: "SOL",  decimals: 9,
        contract: "", wallet: "Phantom / Solflare / SubWallet" },
    SourceAsset { key: "sol:USDC",  chain: "solana",   chain_label: "Solana",   symbol: "USDC", decimals: 6,
        contract: "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", wallet: "Phantom / Solflare / SubWallet" },
    SourceAsset { key: "sol:USDT",  chain: "solana",   chain_label: "Solana",   symbol: "USDT", decimals: 6,
        contract: "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB", wallet: "Phantom / Solflare / SubWallet" },
    SourceAsset { key: "eth:ETH",   chain: "ethereum", chain_label: "Ethereum", symbol: "ETH",  decimals: 18,
        contract: "", wallet: "MetaMask" },
    SourceAsset { key: "eth:USDC",  chain: "ethereum", chain_label: "Ethereum", symbol: "USDC", decimals: 6,
        contract: "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", wallet: "MetaMask" },
    SourceAsset { key: "eth:USDT",  chain: "ethereum", chain_label: "Ethereum", symbol: "USDT", decimals: 6,
        contract: "0xdAC17F958D2ee523a2206206994597C13D831ec7", wallet: "MetaMask" },
    SourceAsset { key: "base:ETH",  chain: "base",     chain_label: "Base",     symbol: "ETH",  decimals: 18,
        contract: "", wallet: "MetaMask" },
    SourceAsset { key: "base:USDC", chain: "base",     chain_label: "Base",     symbol: "USDC", decimals: 6,
        contract: "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", wallet: "MetaMask" },
];

pub fn source_asset(key: &str) -> Option<&'static SourceAsset> {
    SOURCE_ASSETS.iter().find(|a| a.key.eq_ignore_ascii_case(key))
}

// ── what a route hands you at the end ──────────────────────────────

/// The form of TAO a route delivers. Only `NativeSs58` is directly stakeable
/// on Bittensor; everything else needs a further hop, which is why routes are
/// ranked within a delivery form rather than across all of them.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Delivers {
    /// Native TAO credited to a Bittensor ss58 coldkey. Stakeable as-is.
    NativeSs58,
    /// Canonical TAO (SPL) on Solana via Wormhole NTT. Tradeable on Solana;
    /// one Sunrise/Wormhole redeem away from ss58.
    TaoSolanaSpl,
    /// TAO inside Bittensor's own EVM, held by an H160 address. One
    /// precompile call from ss58 — same chain, so it is a cheap last hop.
    TaoEvmH160,
    /// wTAO, an ERC-20 on Ethereum backed 1:1 by TAO locked on Bittensor.
    WtaoErc20,
    /// USDC delivered onto Bittensor EVM. Not TAO yet — you still swap.
    UsdcBittensorEvm,
}

impl Delivers {
    pub fn label(&self) -> &'static str {
        match self {
            Delivers::NativeSs58 => "native TAO (ss58)",
            Delivers::TaoSolanaSpl => "TAO on Solana (SPL)",
            Delivers::TaoEvmH160 => "TAO on Bittensor EVM (H160)",
            Delivers::WtaoErc20 => "wTAO (ERC-20, Ethereum)",
            Delivers::UsdcBittensorEvm => "USDC on Bittensor EVM",
        }
    }
    /// True when the user is done — holding stakeable TAO on Bittensor.
    pub fn is_terminal(&self) -> bool {
        matches!(self, Delivers::NativeSs58)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Custody {
    /// You never give up keys; the protocol holds funds in a contract.
    NonCustodial,
    /// A swap desk takes your deposit and sends the other asset back. Funds
    /// are theirs in between — the standard instant-exchange trade-off.
    SwapDesk,
    /// A named operator holds the float.
    TrustedOperator,
    /// A centralized exchange account, with the account controls that implies.
    Exchange,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Kind {
    /// Deposit asset A, receive asset B at a quoted rate. No account.
    InstantSwap,
    /// A real message-passing bridge with contracts on both sides.
    OnchainBridge,
    /// A DEX swap that lands you a bridged representation of TAO.
    Dex,
    /// Centralized exchange: deposit, trade, withdraw.
    Cex,
    /// taox's own custodial desk.
    Desk,
}

// ── catalog ────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize)]
pub struct BridgeRoute {
    pub id: &'static str,
    pub name: &'static str,
    pub kind: Kind,
    pub custody: Custody,
    pub delivers: Delivers,
    pub delivers_label: &'static str,
    /// Source asset keys this route accepts. `*` means "any listed source".
    pub sources: &'static [&'static str],
    /// Whether `/bridges/quote` can price this route live and key-free.
    pub live_quote: bool,
    /// User-visible transfers required end to end, including the last hop to
    /// ss58 where the route doesn't get you there itself.
    pub hops: u8,
    pub eta: &'static str,
    pub fees: &'static str,
    pub kyc: &'static str,
    pub url: &'static str,
    pub docs: &'static str,
    pub steps: &'static [&'static str],
    pub notes: &'static str,
}

pub const ROUTES: &[BridgeRoute] = &[
    // ── instant swap desks: the only routes that hand you ss58 TAO in one step
    BridgeRoute {
        id: "sideshift",
        name: "SideShift.ai",
        kind: Kind::InstantSwap,
        custody: Custody::SwapDesk,
        delivers: Delivers::NativeSs58,
        delivers_label: "native TAO (ss58)",
        sources: &["sol:SOL", "sol:USDC", "sol:USDT", "eth:ETH", "eth:USDC", "eth:USDT", "base:ETH", "base:USDC"],
        live_quote: true,
        hops: 1,
        eta: "~2-15 min",
        fees: "spread only, quoted in the rate",
        kyc: "none",
        url: "https://sideshift.ai/",
        docs: "https://docs.sideshift.ai/",
        steps: &[
            "Pick your source asset + network and TAO (Bittensor) as the settle coin.",
            "Paste your Bittensor ss58 coldkey as the settle address.",
            "Send the deposit from your own wallet to the address SideShift returns.",
            "Native TAO lands on your ss58 — no further hop.",
        ],
        notes: "Widest source coverage of any single-step route: the only quotable desk here that takes Base ETH and Base USDC and still settles native ss58 TAO.",
    },
    BridgeRoute {
        id: "changenow",
        name: "ChangeNOW",
        kind: Kind::InstantSwap,
        custody: Custody::SwapDesk,
        delivers: Delivers::NativeSs58,
        delivers_label: "native TAO (ss58)",
        sources: &["sol:SOL", "sol:USDC", "sol:USDT", "eth:ETH", "eth:USDC", "eth:USDT", "base:ETH", "base:USDC"],
        live_quote: true,
        hops: 1,
        eta: "~10-60 min (their own forecast)",
        fees: "spread only, quoted in the rate",
        kyc: "none for standard flow; risk-flagged deposits can be held",
        url: "https://changenow.io/currencies/bittensor-main",
        docs: "https://changenow.io/api/docs",
        steps: &[
            "Choose your source asset and network, TAO as the receive coin.",
            "Confirm the receive network reads Bittensor before you send.",
            "Send the deposit and paste your ss58 as the payout address.",
        ],
        notes: "Their legacy quote API carries no network field on the payout side, so confirm on their site that TAO is settling on Bittensor and not as the Solana SPL.",
    },
    BridgeRoute {
        id: "exolix",
        name: "Exolix",
        kind: Kind::InstantSwap,
        custody: Custody::SwapDesk,
        delivers: Delivers::NativeSs58,
        delivers_label: "native TAO (ss58)",
        sources: &["sol:SOL", "sol:USDC", "sol:USDT", "eth:ETH", "eth:USDC", "eth:USDT"],
        live_quote: true,
        hops: 1,
        eta: "~5-30 min",
        fees: "spread + a fixed TAO withdrawal fee",
        kyc: "none by default",
        url: "https://exolix.com/",
        docs: "https://exolix.com/developers",
        steps: &[
            "Select source coin + network, TAO on the Bittensor network as destination.",
            "Enter your ss58, send the deposit, wait for settlement.",
        ],
        notes: "No Base support — Exolix returns 'exchange pair is not available' for both Base ETH and Base USDC. Ethereum and Solana legs are live. Quotes carry a real max as well as a min; large orders get rejected on the upper bound.",
    },
    BridgeRoute {
        id: "godex",
        name: "Godex",
        kind: Kind::InstantSwap,
        custody: Custody::SwapDesk,
        delivers: Delivers::NativeSs58,
        delivers_label: "native TAO (ss58)",
        sources: &["sol:SOL", "sol:USDC", "sol:USDT", "eth:ETH", "eth:USDC", "eth:USDT", "base:ETH", "base:USDC"],
        live_quote: true,
        hops: 1,
        eta: "~10-40 min",
        fees: "spread + a fixed TAO withdrawal fee",
        kyc: "none",
        url: "https://godex.io/",
        docs: "https://godex.io/api",
        steps: &[
            "Pick the source coin, then the source network (BASE / SOL / ETH).",
            "Set TAO as the destination and paste your ss58.",
        ],
        notes: "Higher minimums than the others — commonly ~$160 equivalent — so it drops out on small tickets.",
    },
    BridgeRoute {
        id: "stealthex",
        name: "StealthEX",
        kind: Kind::InstantSwap,
        custody: Custody::SwapDesk,
        delivers: Delivers::NativeSs58,
        delivers_label: "native TAO (ss58)",
        sources: &["*"],
        live_quote: false,
        hops: 1,
        eta: "~5-30 min",
        fees: "spread only",
        kyc: "none",
        url: "https://stealthex.io/?to=tao",
        docs: "https://stealthex.io/api-doc/",
        steps: &["Same shape as the other desks: source asset in, ss58 out."],
        notes: "Not priced here — StealthEX's estimate endpoint requires a partner API key. Set TAOX_STEALTHEX_KEY to bring it into the comparison.",
    },
    BridgeRoute {
        id: "simpleswap",
        name: "SimpleSwap",
        kind: Kind::InstantSwap,
        custody: Custody::SwapDesk,
        delivers: Delivers::NativeSs58,
        delivers_label: "native TAO (ss58)",
        sources: &["*"],
        live_quote: false,
        hops: 1,
        eta: "~5-30 min",
        fees: "spread only",
        kyc: "none",
        url: "https://simpleswap.io/coins/bittensor",
        docs: "https://api.simpleswap.io/",
        steps: &["Source asset in, ss58 out."],
        notes: "Not priced here — quote endpoint requires an API key. Set TAOX_SIMPLESWAP_KEY to include it.",
    },

    // ── real on-chain bridges
    BridgeRoute {
        id: "sunrise_wormhole",
        name: "Sunrise / Wormhole NTT",
        kind: Kind::OnchainBridge,
        custody: Custody::NonCustodial,
        delivers: Delivers::TaoSolanaSpl,
        delivers_label: "TAO on Solana (SPL)",
        sources: &["sol:SOL", "sol:USDC", "sol:USDT"],
        live_quote: false,
        hops: 2,
        eta: "swap instant; NTT redeem to ss58 minutes",
        fees: "DEX fee + Solana gas + Wormhole relay",
        kyc: "none",
        url: "https://sunrise.xyz/",
        docs: "https://wormhole.com/docs/products/native-token-transfers/overview/",
        steps: &[
            "Swap SOL/USDC/USDT for TAO on any Solana DEX (see the Jupiter row for a live price).",
            "Canonical TAO mint on Solana: taoC6xyv2v8tDLcev4uaGUgV4vdQsWJrGft2kcBRrBY.",
            "Use Sunrise/Wormhole NTT to move that TAO back to a Bittensor ss58 when you want the native balance.",
        ],
        notes: "Since May 2026 TAO on Solana is canonical rather than a wrapped fork — one representation, backed by Wormhole's Native Token Transfer framework, tradeable on Jupiter and Meteora. Stop after step 1 and you hold SPL TAO, which cannot stake on a subnet.",
    },
    BridgeRoute {
        id: "jupiter",
        name: "Jupiter (Solana DEX aggregator)",
        kind: Kind::Dex,
        custody: Custody::NonCustodial,
        delivers: Delivers::TaoSolanaSpl,
        delivers_label: "TAO on Solana (SPL)",
        sources: &["sol:SOL", "sol:USDC", "sol:USDT"],
        live_quote: true,
        hops: 2,
        eta: "seconds for the swap; minutes for the NTT hop",
        fees: "pool fees + price impact + ~0.00001 SOL gas",
        kyc: "none",
        url: "https://jup.ag/",
        docs: "https://dev.jup.ag/docs/swap-api/",
        steps: &[
            "Swap your Solana asset for the canonical TAO mint through Jupiter.",
            "Then redeem to ss58 through Sunrise / Wormhole NTT if you want native TAO.",
        ],
        notes: "Usually shows the best headline rate on this board because it stops one hop short — you are holding SPL TAO, not a stakeable ss58 balance. Solana-side TAO liquidity is still thin, so check the price impact on the quote before sizing up.",
    },
    BridgeRoute {
        id: "taofi",
        name: "TaoFi bridge (Hyperlane)",
        kind: Kind::OnchainBridge,
        custody: Custody::NonCustodial,
        delivers: Delivers::UsdcBittensorEvm,
        delivers_label: "USDC on Bittensor EVM",
        sources: &["eth:USDC", "base:USDC", "sol:USDC"],
        live_quote: false,
        hops: 3,
        eta: "bridge ~minutes, then a swap",
        fees: "1:1 bridge, gas only; then the UniV3 pool fee on the swap",
        kyc: "none",
        url: "https://taofi.com/",
        docs: "https://docs.taofi.com/bridge",
        steps: &[
            "Bridge USDC from Ethereum, Base or Solana to Bittensor EVM — 1:1, no bridge fee beyond gas.",
            "Bridging at least 100 USDC also drops you 0.01 TAO to cover EVM gas on arrival.",
            "Swap the bridged USDC for TAO in TaoFi's Uniswap-V3 pool on Bittensor EVM (chain ID 964).",
            "Move the resulting TAO from your H160 to your ss58 with the withdrawal precompile if you want the native balance.",
        ],
        notes: "The only stablecoin route that is a genuine bridge rather than a swap desk — Hyperlane warp routes, with the USDC on Bittensor EVM fully backed on the origin chains. Costs more hops but you never hand custody to anyone. USDC only; it does not bridge ETH or SOL.",
    },
    BridgeRoute {
        id: "taobridge",
        name: "TaoBridge (wTAO)",
        kind: Kind::OnchainBridge,
        custody: Custody::TrustedOperator,
        delivers: Delivers::WtaoErc20,
        delivers_label: "wTAO (ERC-20, Ethereum)",
        sources: &["eth:ETH", "eth:USDC", "eth:USDT"],
        live_quote: false,
        hops: 2,
        eta: "bridge settles every 300 blocks — up to ~2 hours",
        fees: "Uniswap pool fee + Ethereum gas + bridge fee",
        kyc: "none",
        url: "https://taobridge.xyz/",
        docs: "https://taobridge.xyz/",
        steps: &[
            "Buy wTAO on Ethereum (Uniswap V3) — contract 0x77E06c9eCCf2E797fd462A92B6D7642EF85b0A44.",
            "Bridge wTAO to native TAO on Subtensor Finney at taobridge.xyz.",
        ],
        notes: "wTAO is ERC-20 TAO backed 1:1 by TAO locked on Bittensor. Bridging batches every ~300 blocks, so budget up to two hours. The bridge is run by a single operator (a Bittensor validator), which is the trust assumption to weigh — and Ethereum wTAO liquidity is a few hundred thousand dollars, so size accordingly.",
    },
    BridgeRoute {
        id: "bittensor_evm",
        name: "Bittensor EVM transfer (last hop)",
        kind: Kind::OnchainBridge,
        custody: Custody::NonCustodial,
        delivers: Delivers::NativeSs58,
        delivers_label: "native TAO (ss58)",
        sources: &[],
        live_quote: false,
        hops: 1,
        eta: "one block",
        fees: "Bittensor EVM gas only",
        kyc: "none",
        url: "https://bridge.bittensor.com/",
        docs: "https://bittensor.com/docs",
        steps: &[
            "Add Bittensor EVM to MetaMask: chain ID 964 (0x3c4), RPC https://lite.chain.opentensor.ai.",
            "Move TAO between your own H160 and your own ss58 through the bridge UI or the transfer precompile.",
        ],
        notes: "Not a cross-chain bridge — both sides are the same Bittensor chain, one substrate and one EVM. This is the last hop that finishes the TaoFi route, and it only ever moves funds between wallets you control.",
    },
    BridgeRoute {
        id: "cex",
        name: "Centralized exchange",
        kind: Kind::Cex,
        custody: Custody::Exchange,
        delivers: Delivers::NativeSs58,
        delivers_label: "native TAO (ss58)",
        sources: &["*"],
        live_quote: false,
        hops: 3,
        eta: "minutes to hours, deposit confirmations dominate",
        fees: "trading fee + a fixed TAO withdrawal fee",
        kyc: "yes, on every venue that lists TAO with real depth",
        url: "https://www.binance.com/en/trade/TAO_USDT",
        docs: "",
        steps: &[
            "Deposit ETH / SOL / USDC to an exchange that lists TAO (Binance, Kraken, MEXC, Gate).",
            "Trade into TAO on the spot book.",
            "Withdraw TAO to your ss58, making sure the withdrawal network is Bittensor.",
        ],
        notes: "Deepest liquidity and the tightest spread by far on large size — the reason to reach for it is a big ticket, not convenience. The cost is KYC, an account that can freeze, and three separate steps.",
    },
    BridgeRoute {
        id: "taox_desk",
        name: "taox desk (this module)",
        kind: Kind::Desk,
        custody: Custody::TrustedOperator,
        delivers: Delivers::NativeSs58,
        delivers_label: "native TAO (ss58)",
        sources: &["sol:SOL", "sol:USDC", "sol:USDT", "eth:ETH", "eth:USDC", "eth:USDT"],
        live_quote: true,
        hops: 1,
        eta: "operator-driven",
        fees: "fee_bps from config.json (default 0.50%)",
        kyc: "none",
        url: "",
        docs: "",
        steps: &[
            "Open an order at /swap, send to the deposit address, operator delivers TAO to your ss58.",
        ],
        notes: "Custodial swap-by-deposit against this deployment's own float, priced off CoinGecko mid minus the configured fee. Delivery is operator-driven — it is only as good as whoever runs this instance. Listed for comparison, not because it beats the desks above.",
    },
];

// ── live quotes ────────────────────────────────────────────────────

#[derive(Debug, Deserialize)]
pub struct BridgeQuoteIn {
    /// Source asset key, e.g. "base:USDC".
    pub asset: String,
    pub amount: f64,
}

#[derive(Debug, Clone, Serialize)]
pub struct RouteQuote {
    pub id: &'static str,
    pub name: &'static str,
    pub kind: Kind,
    pub custody: Custody,
    pub delivers: Delivers,
    pub delivers_label: &'static str,
    pub hops: u8,
    pub eta: &'static str,
    pub url: &'static str,
    /// `ok` | `unavailable` | `error`
    pub status: &'static str,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tao_out: Option<f64>,
    /// TAO per one unit of the source asset, after the route's own costs.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub rate: Option<f64>,
    /// Signed % versus the CoinGecko mid for the same trade. Negative is the
    /// normal case — it is what the route costs you.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub vs_mid_pct: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub min_in: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub max_in: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub price_impact_pct: Option<f64>,
    /// True when the number is a modelled price rather than a firm quote the
    /// provider will honour. Indicative rows are kept off `best_native` and
    /// sort below real quotes — a mid-minus-fee model always looks better
    /// than a desk quoting a real spread, and that flattery isn't a price.
    #[serde(skip_serializing_if = "std::ops::Not::not")]
    pub indicative: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub detail: Option<String>,
}

impl RouteQuote {
    fn from_route(r: &'static BridgeRoute, status: &'static str) -> Self {
        RouteQuote {
            id: r.id, name: r.name, kind: r.kind, custody: r.custody,
            delivers: r.delivers, delivers_label: r.delivers_label, hops: r.hops,
            eta: r.eta, url: r.url, status,
            tao_out: None, rate: None, vs_mid_pct: None,
            min_in: None, max_in: None, price_impact_pct: None,
            indicative: false, detail: None,
        }
    }
    fn err(r: &'static BridgeRoute, detail: impl Into<String>) -> Self {
        let mut q = Self::from_route(r, "error");
        q.detail = Some(detail.into());
        q
    }
    fn unavailable(r: &'static BridgeRoute, detail: impl Into<String>) -> Self {
        let mut q = Self::from_route(r, "unavailable");
        q.detail = Some(detail.into());
        q
    }
    fn ok(r: &'static BridgeRoute, amount: f64, tao_out: f64) -> Self {
        let mut q = Self::from_route(r, "ok");
        q.tao_out = Some(tao_out);
        q.rate = (amount > 0.0).then(|| tao_out / amount);
        q
    }
}

fn route(id: &str) -> &'static BridgeRoute {
    ROUTES.iter().find(|r| r.id == id).expect("route id present in ROUTES")
}

/// Route ids we can price without an API key.
const QUOTABLE: &[&str] = &["sideshift", "changenow", "exolix", "godex", "jupiter"];

// -- per-provider ticker maps. Derived by probing each API, not guessed:
//    every entry below returned a real quote against TAO.

/// SideShift addresses coins as `{coin}-{network}`.
fn sideshift_pair(key: &str) -> Option<&'static str> {
    Some(match key {
        "sol:SOL" => "sol-solana",
        "sol:USDC" => "usdc-solana",
        "sol:USDT" => "usdt-solana",
        "eth:ETH" => "eth-ethereum",
        "eth:USDC" => "usdc-ethereum",
        "eth:USDT" => "usdt-ethereum",
        "base:ETH" => "eth-base",
        "base:USDC" => "usdc-base",
        _ => return None,
    })
}

/// ChangeNOW's legacy v1 API uses one flat ticker per (coin, network).
/// Note the asymmetry: plain `usdc` is the Ethereum ERC-20 while USDT on
/// Ethereum is `usdterc20`. Both verified against live quotes.
fn changenow_ticker(key: &str) -> Option<&'static str> {
    Some(match key {
        "sol:SOL" => "sol",
        "sol:USDC" => "usdcsol",
        "sol:USDT" => "usdtsol",
        "eth:ETH" => "eth",
        "eth:USDC" => "usdc",
        "eth:USDT" => "usdterc20",
        "base:ETH" => "ethbase",
        "base:USDC" => "usdcbase",
        _ => return None,
    })
}

/// Exolix takes coin and network separately. It has no Base pairs against
/// TAO, so Base keys map to nothing and the route reports unavailable.
fn exolix_pair(key: &str) -> Option<(&'static str, &'static str)> {
    Some(match key {
        "sol:SOL" => ("SOL", "SOL"),
        "sol:USDC" => ("USDC", "SOL"),
        "sol:USDT" => ("USDT", "SOL"),
        "eth:ETH" => ("ETH", "ETH"),
        "eth:USDC" => ("USDC", "ETH"),
        "eth:USDT" => ("USDT", "ETH"),
        _ => return None,
    })
}

/// Godex takes a coin plus a `coin_from_network`.
fn godex_pair(key: &str) -> Option<(&'static str, &'static str)> {
    Some(match key {
        "sol:SOL" => ("SOL", "SOL"),
        "sol:USDC" => ("USDC", "SOL"),
        "sol:USDT" => ("USDT", "SOL"),
        "eth:ETH" => ("ETH", "ETH"),
        "eth:USDC" => ("USDC", "ETH"),
        "eth:USDT" => ("USDT", "ETH"),
        "base:ETH" => ("ETH", "BASE"),
        "base:USDC" => ("USDC", "BASE"),
        _ => return None,
    })
}

/// Canonical TAO on Solana (Wormhole NTT, via Sunrise). Confirmed against
/// Sunrise's own published contract address.
pub const TAO_SOLANA_MINT: &str = "taoC6xyv2v8tDLcev4uaGUgV4vdQsWJrGft2kcBRrBY";
const WSOL_MINT: &str = "So11111111111111111111111111111111111111112";

fn jupiter_mint(key: &str) -> Option<&'static str> {
    Some(match key {
        "sol:SOL" => WSOL_MINT,
        "sol:USDC" => "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
        "sol:USDT" => "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
        _ => return None,
    })
}

fn f64_of(v: &Value) -> Option<f64> {
    v.as_f64().or_else(|| v.as_str().and_then(|s| s.parse().ok()))
}

// Each fetcher owns its own failure. A provider that is down, rate-limiting
// or has dropped the pair comes back as one `error`/`unavailable` row rather
// than taking the whole comparison with it.

async fn quote_sideshift(http: &reqwest::Client, key: &str, amount: f64) -> RouteQuote {
    let r = route("sideshift");
    let Some(pair) = sideshift_pair(key) else {
        return RouteQuote::unavailable(r, "pair not offered for this asset");
    };
    let url = format!("https://sideshift.ai/api/v2/pair/{pair}/tao-bittensor");
    let body: Value = match http.get(&url).send().await {
        Ok(resp) => match resp.json().await {
            Ok(v) => v,
            Err(e) => return RouteQuote::err(r, format!("decode: {e}")),
        },
        Err(e) => return RouteQuote::err(r, format!("fetch: {e}")),
    };
    if let Some(msg) = body.pointer("/error/message").and_then(|v| v.as_str()) {
        return RouteQuote::unavailable(r, msg.to_string());
    }
    let Some(rate) = body.get("rate").and_then(f64_of) else {
        return RouteQuote::err(r, "no rate in response");
    };
    let min = body.get("min").and_then(f64_of);
    let max = body.get("max").and_then(f64_of);
    let mut q = RouteQuote::ok(r, amount, rate * amount);
    q.min_in = min;
    q.max_in = max;
    apply_bounds(&mut q, amount);
    q
}

async fn quote_changenow(http: &reqwest::Client, key: &str, amount: f64) -> RouteQuote {
    let r = route("changenow");
    let Some(t) = changenow_ticker(key) else {
        return RouteQuote::unavailable(r, "pair not offered for this asset");
    };
    let url = format!("https://api.changenow.io/v1/exchange-amount/{amount}/{t}_tao");
    let resp = match http.get(&url).send().await {
        Ok(x) => x,
        Err(e) => return RouteQuote::err(r, format!("fetch: {e}")),
    };
    // A 400 here is almost always "below the minimum", so surface the min
    // alongside it rather than a bare status code.
    if !resp.status().is_success() {
        let status = resp.status();
        let min = changenow_min(http, t).await;
        let mut q = RouteQuote::unavailable(
            r,
            match min {
                Some(m) => format!("rejected (HTTP {status}); minimum is {m}"),
                None => format!("rejected (HTTP {status})"),
            },
        );
        q.min_in = min;
        return q;
    }
    let body: Value = match resp.json().await {
        Ok(v) => v,
        Err(e) => return RouteQuote::err(r, format!("decode: {e}")),
    };
    let Some(out) = body.get("estimatedAmount").and_then(f64_of) else {
        return RouteQuote::err(r, "no estimatedAmount in response");
    };
    let mut q = RouteQuote::ok(r, amount, out);
    q.min_in = changenow_min(http, t).await;
    if let Some(w) = body.get("warningMessage").and_then(|v| v.as_str()) {
        q.detail = Some(w.to_string());
    }
    apply_bounds(&mut q, amount);
    q
}

async fn changenow_min(http: &reqwest::Client, ticker: &str) -> Option<f64> {
    let url = format!("https://api.changenow.io/v1/min-amount/{ticker}_tao");
    let body: Value = http.get(&url).send().await.ok()?.json().await.ok()?;
    body.get("minAmount").and_then(f64_of)
}

async fn quote_exolix(http: &reqwest::Client, key: &str, amount: f64) -> RouteQuote {
    let r = route("exolix");
    let Some((coin, net)) = exolix_pair(key) else {
        return RouteQuote::unavailable(r, "no TAO pair on this network");
    };
    let url = format!(
        "https://exolix.com/api/v2/rate?coinFrom={coin}&networkFrom={net}\
         &coinTo=TAO&networkTo=TAO&amount={amount}&rateType=float"
    );
    let resp = match http.get(&url).send().await {
        Ok(x) => x,
        Err(e) => return RouteQuote::err(r, format!("fetch: {e}")),
    };
    let ok = resp.status().is_success();
    let body: Value = match resp.json().await {
        Ok(v) => v,
        Err(e) => return RouteQuote::err(r, format!("decode: {e}")),
    };
    if !ok {
        // Exolix answers both "pair doesn't exist" and "amount out of range"
        // with a 422, and the bounds only sometimes come back in the body.
        let min = body.get("minAmount").and_then(f64_of);
        let max = body.get("maxAmount").and_then(f64_of);
        let msg = body
            .get("error")
            .or_else(|| body.get("message"))
            .and_then(|v| v.as_str())
            .map(String::from)
            .or_else(|| match (min, max) {
                (Some(m), _) if amount < m => Some(format!("below minimum of {m}")),
                (_, Some(m)) if amount > m => Some(format!("above maximum of {m}")),
                _ => None,
            })
            .unwrap_or_else(|| "rejected — amount is outside the accepted range".into());
        let mut q = RouteQuote::unavailable(r, msg);
        q.min_in = min;
        q.max_in = max;
        return q;
    }
    let Some(out) = body.get("toAmount").and_then(f64_of) else {
        return RouteQuote::err(r, "no toAmount in response");
    };
    let mut q = RouteQuote::ok(r, amount, out);
    q.min_in = body.get("minAmount").and_then(f64_of);
    q.max_in = body.get("maxAmount").and_then(f64_of);
    apply_bounds(&mut q, amount);
    q
}

async fn quote_godex(http: &reqwest::Client, key: &str, amount: f64) -> RouteQuote {
    let r = route("godex");
    let Some((coin, net)) = godex_pair(key) else {
        return RouteQuote::unavailable(r, "pair not offered for this asset");
    };
    let payload = serde_json::json!({
        "from": coin, "to": "TAO", "amount": amount.to_string(),
        "coin_from_network": net, "coin_to_network": "TAO",
    });
    let resp = match http.post("https://api.godex.io/api/v1/info").json(&payload).send().await {
        Ok(x) => x,
        Err(e) => return RouteQuote::err(r, format!("fetch: {e}")),
    };
    let ok = resp.status().is_success();
    let body: Value = match resp.json().await {
        Ok(v) => v,
        Err(e) => return RouteQuote::err(r, format!("decode: {e}")),
    };
    if !ok {
        let msg = body.get("error").and_then(|v| v.as_str()).unwrap_or("rejected");
        return RouteQuote::unavailable(r, msg.to_string());
    }
    let Some(out) = body.get("amount").and_then(f64_of) else {
        return RouteQuote::err(r, "no amount in response");
    };
    // Godex quotes gross and bills the withdrawal fee separately in TAO, so
    // subtract it — otherwise it looks better than the desks that quote net.
    let withdraw_fee = body.get("withdrawal_fee").and_then(f64_of).unwrap_or(0.0);
    let mut q = RouteQuote::ok(r, amount, (out - withdraw_fee).max(0.0));
    q.min_in = body.get("min_amount").and_then(f64_of);
    q.max_in = body.get("max_amount").and_then(f64_of);
    if withdraw_fee > 0.0 {
        q.detail = Some(format!("net of a {withdraw_fee} TAO withdrawal fee"));
    }
    apply_bounds(&mut q, amount);
    q
}

async fn quote_jupiter(http: &reqwest::Client, key: &str, amount: f64, decimals: u32) -> RouteQuote {
    let r = route("jupiter");
    let Some(mint) = jupiter_mint(key) else {
        return RouteQuote::unavailable(r, "source is not on Solana");
    };
    let base_units = (amount * 10f64.powi(decimals as i32)).round() as u128;
    if base_units == 0 {
        return RouteQuote::unavailable(r, "amount rounds to zero base units");
    }
    let url = format!(
        "https://lite-api.jup.ag/swap/v1/quote?inputMint={mint}&outputMint={TAO_SOLANA_MINT}\
         &amount={base_units}&slippageBps=50"
    );
    let resp = match http.get(&url).send().await {
        Ok(x) => x,
        Err(e) => return RouteQuote::err(r, format!("fetch: {e}")),
    };
    let ok = resp.status().is_success();
    let body: Value = match resp.json().await {
        Ok(v) => v,
        Err(e) => return RouteQuote::err(r, format!("decode: {e}")),
    };
    if !ok {
        let msg = body.get("error").and_then(|v| v.as_str()).unwrap_or("no route");
        return RouteQuote::unavailable(r, msg.to_string());
    }
    let Some(out_raw) = body.get("outAmount").and_then(f64_of) else {
        return RouteQuote::err(r, "no outAmount in response");
    };
    // TAO is 9 decimals on Solana, same as native.
    let mut q = RouteQuote::ok(r, amount, out_raw / 1e9);
    q.price_impact_pct = body.get("priceImpactPct").and_then(f64_of).map(|p| p * 100.0);
    let hops = body.get("routePlan").and_then(|v| v.as_array()).map(|a| a.len()).unwrap_or(0);
    let labels: Vec<String> = body
        .get("routePlan")
        .and_then(|v| v.as_array())
        .map(|a| {
            a.iter()
                .filter_map(|s| s.pointer("/swapInfo/label").and_then(|l| l.as_str()))
                .map(String::from)
                .collect()
        })
        .unwrap_or_default();
    if hops > 0 {
        q.detail = Some(format!("via {}", labels.join(" → ")));
    }
    q
}

/// Mark a quote unavailable when the amount falls outside the provider's own
/// accepted range. The rate they returned is real but they will refuse the
/// order, so showing it as a live option would be a lie.
fn apply_bounds(q: &mut RouteQuote, amount: f64) {
    let breach = match (q.min_in, q.max_in) {
        (Some(min), _) if amount < min => Some(format!("below minimum of {min}")),
        (_, Some(max)) if max > 0.0 && amount > max => Some(format!("above maximum of {max}")),
        _ => None,
    };
    if let Some(why) = breach {
        q.status = "unavailable";
        q.detail = Some(why);
        // Drop the figures too. The provider quoted a rate but will refuse
        // this size, so leaving a number behind would put an amount on the
        // board that nobody can actually get.
        q.tao_out = None;
        q.rate = None;
        q.vs_mid_pct = None;
    }
}

#[derive(Debug, Serialize)]
pub struct BridgeQuoteOut {
    pub asset: String,
    pub chain: &'static str,
    pub symbol: &'static str,
    pub amount: f64,
    /// CoinGecko mid for the same trade, before any route's costs. The
    /// benchmark every `vs_mid_pct` is measured against.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub mid_tao: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub mid_stale: Option<bool>,
    /// Every quotable route, best first. Routes delivering native ss58 TAO
    /// sort ahead of ones that leave you a hop short, regardless of rate.
    pub routes: Vec<RouteQuote>,
    /// The route that pays the most native ss58 TAO in one step.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub best_native: Option<String>,
    /// Routes that exist for this asset but can't be priced key-free.
    pub manual: Vec<&'static BridgeRoute>,
    pub ts: i64,
}

/// Price every quotable route for one (asset, amount) concurrently.
pub async fn quote_all(
    http: &reqwest::Client,
    asset: &SourceAsset,
    amount: f64,
    mid_tao: Option<f64>,
) -> Vec<RouteQuote> {
    let key = asset.key;
    let (a, b, c, d, e) = tokio::join!(
        quote_sideshift(http, key, amount),
        quote_changenow(http, key, amount),
        quote_exolix(http, key, amount),
        quote_godex(http, key, amount),
        quote_jupiter(http, key, amount, asset.decimals),
    );
    let mut out = vec![a, b, c, d, e];

    if let Some(mid) = mid_tao.filter(|m| *m > 0.0) {
        for q in out.iter_mut() {
            if let Some(t) = q.tao_out {
                q.vs_mid_pct = Some((t / mid - 1.0) * 100.0);
            }
        }
    }

    sort_board(&mut out);
    out
}

/// Board order: routes that actually quoted first, then ones that finish the
/// job in native ss58 TAO, then firm quotes ahead of indicative ones, and
/// only then by how much TAO you end up with.
pub fn sort_board(rows: &mut [RouteQuote]) {
    rows.sort_by(|x, y| {
        let rank = |q: &RouteQuote| (q.status != "ok", !q.delivers.is_terminal(), q.indicative);
        rank(x).cmp(&rank(y)).then_with(|| {
            y.tao_out
                .unwrap_or(f64::MIN)
                .partial_cmp(&x.tao_out.unwrap_or(f64::MIN))
                .unwrap_or(std::cmp::Ordering::Equal)
        })
    });
}

/// The best route that both finishes in native ss58 TAO and is a firm quote.
pub fn best_native(rows: &[RouteQuote]) -> Option<String> {
    rows.iter()
        .find(|q| q.status == "ok" && q.delivers.is_terminal() && !q.indicative)
        .map(|q| q.id.to_string())
}

/// Catalog rows relevant to one asset that `quote_all` cannot price.
pub fn manual_routes(key: &str) -> Vec<&'static BridgeRoute> {
    ROUTES
        .iter()
        // `taox_desk` is priced inline on the board for the assets it takes,
        // so it never belongs in the manual list as well.
        .filter(|r| !QUOTABLE.contains(&r.id) && r.id != "taox_desk")
        .filter(|r| {
            r.sources.contains(&"*")
                || r.sources.contains(&key)
                // The EVM last hop takes no source asset of its own but is
                // always worth showing — it is how the TaoFi route finishes.
                || r.id == "bittensor_evm"
        })
        .collect()
}

/// Grouped catalog view: which routes accept each source asset.
pub fn coverage() -> BTreeMap<&'static str, Vec<&'static str>> {
    let mut map: BTreeMap<&'static str, Vec<&'static str>> = BTreeMap::new();
    for a in SOURCE_ASSETS {
        let ids = ROUTES
            .iter()
            .filter(|r| r.sources.contains(&"*") || r.sources.contains(&a.key))
            .map(|r| r.id)
            .collect();
        map.insert(a.key, ids);
    }
    map
}
