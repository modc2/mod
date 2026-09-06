//! Modular finance — every place money can go, as a MODULE with its own
//! return, its own liquidity, and its own conditions.
//!
//! The yields table says what is paying. The desk can trade. What neither of
//! them said is the thing you actually decide on: *if I put money here, what
//! does it earn, when can I get it back, and what does it cost to leave?* So
//! this file folds the whole surface into one shape — a finance module:
//!
//!   * RETURNS — the APY, fees and emissions kept apart, and how the rate has
//!     behaved (DefiLlama's numbers, never ours);
//!   * LIQUIDITY — depth, how you enter, how you exit, and how long that takes:
//!     instant, a queue, a cooldown, a lock, a redemption window;
//!   * CONDITIONS — impermanent loss, emissions, KYC gates, outliers, mainnet
//!     confirmation, anything the money is subject to;
//!   * an ADAPTER — how THIS desk puts money in and takes it out, through the
//!     module that owns the chain (eth, solana, bt). A module with no adapter is
//!     still listed with its terms; it just cannot be entered from here yet.
//!
//! Four sources feed the registry: DefiLlama's index for Ethereum, Base and
//! Solana; the bt module's subnet list for Bittensor (dTAO pools — a stake is
//! the deposit); the composer's own deployed vaults; and the BlocTime treasury.
//! Adding a chain is adding a source. Adding a way in is one row in
//! `adapters.json`, and every address in that file was read back on chain
//! before it was written down.
//!
//! Positions are the ledger of money that went in through here:
//! `~/.mod/defi/positions/`. A row is written only when a chain module actually
//! sent something — a dry run, a refused confirm and a failed approval leave no
//! trace, because a ledger that records intentions as facts is worse than none.

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use tokio::sync::RwLock;

use crate::dex::{self, Dex};
use crate::yields::Pool;

const LLAMA_PROTOCOLS: &str = "https://api.llama.fi/protocols";
const DEPTH_FULL: f64 = 10_000_000.0;
const MAX_UINT: &str =
    "115792089237316195423570985008687907853269984665640564039457584007913129639935";

// ── the registry file ───────────────────────────────────────────────────────

#[derive(Debug, Clone, Deserialize, Default)]
pub struct Terms {
    #[serde(default = "instant")]
    pub entry: String,
    #[serde(default = "instant")]
    pub exit: String,
    #[serde(default)]
    pub exit_note: String,
    #[serde(default)]
    pub lock_days: u32,
    #[serde(default)]
    pub exit_delay_days: u32,
    #[serde(default)]
    pub gated: bool,
}

fn instant() -> String {
    "instant".into()
}

#[derive(Debug, Clone, Deserialize)]
pub struct Token {
    pub symbol: String,
    #[serde(default)]
    pub address: Option<String>,
    #[serde(default)]
    pub decimals: Option<u32>,
    #[serde(default)]
    pub native: bool,
}

impl Token {
    fn view(&self) -> Value {
        json!({ "symbol": self.symbol, "address": self.address, "decimals": self.decimals, "native": self.native })
    }
    /// What the DEX desk should be told to sell or buy.
    fn handle(&self) -> String {
        if self.native {
            self.symbol.clone()
        } else {
            self.address.clone().unwrap_or_else(|| self.symbol.clone())
        }
    }
}

#[derive(Debug, Clone, Deserialize)]
pub struct Adapter {
    #[serde(rename = "match")]
    pub rule: Value,
    pub kind: String,
    #[serde(default)]
    pub address: Option<String>,
    pub asset: Token,
    #[serde(default)]
    pub receipt: Option<Token>,
    #[serde(default)]
    pub exit_via: Option<String>,
}

impl Adapter {
    fn matches(&self, pool: &Pool) -> bool {
        if let Some(id) = self.rule.get("pool").and_then(|v| v.as_str()) {
            return pool.pool == id;
        }
        let same = |key: &str, have: &str| {
            self.rule
                .get(key)
                .and_then(|v| v.as_str())
                .map(|want| want.eq_ignore_ascii_case(have))
                .unwrap_or(true)
        };
        if !same("chain", &pool.chain) || !same("project", &pool.project) || !same("symbol", &pool.symbol) {
            return false;
        }
        // "meta": null in the rule means "the pool must have no meta" — that is
        // how aave's core market is told apart from its Prime and Umbrella rows.
        match self.rule.get("meta") {
            None => true,
            Some(Value::Null) => pool.pool_meta.as_deref().map(|m| m.trim().is_empty()).unwrap_or(true),
            Some(Value::String(want)) => pool.pool_meta.as_deref() == Some(want.as_str()),
            Some(_) => true,
        }
    }
}

#[derive(Debug, Clone, Deserialize, Default)]
pub struct Registry {
    #[serde(default)]
    pub categories: HashMap<String, Terms>,
    #[serde(default)]
    pub projects: HashMap<String, Value>,
    #[serde(default)]
    pub adapters: Vec<Adapter>,
}

impl Registry {
    pub fn load(path: &Path) -> Result<Self, String> {
        let text = std::fs::read_to_string(path).map_err(|e| format!("{}: {e}", path.display()))?;
        serde_json::from_str(&text).map_err(|e| format!("{} is not a registry: {e}", path.display()))
    }

    pub(crate) fn adapter_for(&self, pool: &Pool) -> Option<&Adapter> {
        self.adapters.iter().find(|a| a.matches(pool))
    }

    /// The terms a module gets: its category's defaults, then whatever the
    /// project override says on top.
    fn terms(&self, category: &str, project: &str) -> Terms {
        let mut terms = self.categories.get(category).cloned().unwrap_or_else(|| Terms {
            entry: instant(),
            exit: instant(),
            exit_note: "no terms on file for this category — assumed instant; check the protocol before relying on it".into(),
            ..Default::default()
        });
        if let Some(over) = self.projects.get(project) {
            if let Some(v) = over.get("entry").and_then(|v| v.as_str()) {
                terms.entry = v.into();
            }
            if let Some(v) = over.get("exit").and_then(|v| v.as_str()) {
                terms.exit = v.into();
            }
            if let Some(v) = over.get("exit_note").and_then(|v| v.as_str()) {
                terms.exit_note = v.into();
            }
            if let Some(v) = over.get("exit_delay_days").and_then(|v| v.as_u64()) {
                terms.exit_delay_days = v as u32;
            }
            if let Some(v) = over.get("lock_days").and_then(|v| v.as_u64()) {
                terms.lock_days = v as u32;
            }
            if let Some(v) = over.get("gated").and_then(|v| v.as_bool()) {
                terms.gated = v;
            }
        }
        terms
    }
}

// ── the filter ──────────────────────────────────────────────────────────────

#[derive(Default, Debug)]
pub struct Filter {
    pub chain: Option<String>,
    pub kind: Option<String>,
    pub q: Option<String>,
    pub addable_only: bool,
    pub instant_only: bool,
    pub min_tvl: f64,
    pub stable_only: bool,
    pub organic_only: bool,
    pub include_outliers: bool,
    pub sort: String,
    pub limit: usize,
}

impl Filter {
    pub fn from_query(q: &Value) -> Self {
        let s = |k: &str| {
            q.get(k)
                .and_then(|v| v.as_str())
                .map(|v| v.trim().to_string())
                .filter(|v| !v.is_empty())
        };
        let f = |k: &str, d: f64| {
            q.get(k)
                .and_then(|v| v.as_str().and_then(|s| s.parse().ok()).or_else(|| v.as_f64()))
                .unwrap_or(d)
        };
        let b = |k: &str| match q.get(k) {
            Some(Value::Bool(v)) => *v,
            Some(Value::String(v)) => !matches!(v.as_str(), "" | "0" | "false" | "no"),
            Some(Value::Number(n)) => n.as_f64().unwrap_or(0.0) != 0.0,
            _ => false,
        };
        Filter {
            chain: s("chain").map(|c| c.to_lowercase()),
            kind: s("kind"),
            q: s("q"),
            addable_only: b("addable"),
            instant_only: b("instant"),
            min_tvl: f("min_tvl", 100_000.0),
            stable_only: b("stable"),
            organic_only: b("organic"),
            include_outliers: b("outliers"),
            sort: s("sort").unwrap_or_else(|| "score".into()),
            limit: f("limit", 60.0).max(1.0).min(500.0) as usize,
        }
    }
}

// ── positions ───────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Position {
    pub id: String,
    pub owner: String,
    pub module: String,
    pub chain: String,
    #[serde(default)]
    pub network: String,
    pub project: String,
    pub symbol: String,
    pub kind: String,
    pub adapter: String,
    /// Human units of the asset that went in, as typed. Never a float.
    pub amount: String,
    pub asset: String,
    #[serde(default)]
    pub asset_address: Option<String>,
    #[serde(default)]
    pub receipt: Option<Value>,
    #[serde(default)]
    pub account: String,
    #[serde(default)]
    pub apy_at_entry: f64,
    #[serde(default)]
    pub apy_base_at_entry: f64,
    pub entered_at: u64,
    /// "open" · "closed"
    pub status: String,
    #[serde(default)]
    pub txs: Vec<String>,
    #[serde(default)]
    pub entry: Value,
    #[serde(default)]
    pub exits: Vec<Value>,
    #[serde(default)]
    pub note: String,
}

// ── the service ─────────────────────────────────────────────────────────────

struct Cached<T> {
    fetched: u64,
    value: Arc<T>,
}

pub struct Finance {
    root: PathBuf,
    pub registry: Registry,
    http: reqwest::Client,
    categories: RwLock<Option<Cached<HashMap<String, String>>>>,
    subnets: RwLock<Option<Cached<Vec<Value>>>>,
    pub registry_error: Option<String>,
}

impl Finance {
    pub fn new(data_dir: &Path, registry_path: &Path) -> Self {
        let root = data_dir.join("positions");
        let _ = std::fs::create_dir_all(&root);
        let (registry, registry_error) = match Registry::load(registry_path) {
            Ok(r) => (r, None),
            Err(e) => {
                eprintln!("[defi] adapters registry: {e}");
                (Registry::default(), Some(e))
            }
        };
        Self {
            root,
            registry,
            http: reqwest::Client::builder()
                .timeout(std::time::Duration::from_secs(60))
                .build()
                .expect("http client"),
            categories: RwLock::new(None),
            subnets: RwLock::new(None),
            registry_error,
        }
    }

    // ── sources ───────────────────────────────────────────────────────────

    /// DefiLlama's protocol list, collapsed to slug → category, held for a day.
    /// Without it every module is "Yield"; with it a lender, an LST and an LP
    /// get the terms that actually apply to them.
    async fn categories(&self) -> Arc<HashMap<String, String>> {
        let now = crate::auth::now();
        {
            let cache = self.categories.read().await;
            if let Some(c) = cache.as_ref() {
                if now.saturating_sub(c.fetched) < 86_400 {
                    return c.value.clone();
                }
            }
        }
        let fetched = async {
            let body: Value = self.http.get(LLAMA_PROTOCOLS).send().await.ok()?.json().await.ok()?;
            let mut map = HashMap::new();
            for p in body.as_array()? {
                if let (Some(slug), Some(cat)) = (
                    p.get("slug").and_then(|v| v.as_str()),
                    p.get("category").and_then(|v| v.as_str()),
                ) {
                    map.insert(slug.to_string(), cat.to_string());
                }
            }
            Some(map)
        }
        .await;
        match fetched {
            Some(map) if !map.is_empty() => {
                let value = Arc::new(map);
                *self.categories.write().await = Some(Cached { fetched: now, value: value.clone() });
                value
            }
            _ => {
                let cache = self.categories.read().await;
                cache.as_ref().map(|c| c.value.clone()).unwrap_or_else(|| Arc::new(HashMap::new()))
            }
        }
    }

    /// The subnets, from the module that owns Bittensor. Held five minutes.
    async fn subnets(&self, dex: &Dex) -> Result<Arc<Vec<Value>>, String> {
        let now = crate::auth::now();
        {
            let cache = self.subnets.read().await;
            if let Some(c) = cache.as_ref() {
                if now.saturating_sub(c.fetched) < 300 {
                    return Ok(c.value.clone());
                }
            }
        }
        let out = dex.peer("bt", "bt_subnets", json!({ "n": 256 }), None).await?;
        let list: Vec<Value> = out
            .as_array()
            .cloned()
            .or_else(|| out.get("subnets").and_then(|s| s.as_array()).cloned())
            .unwrap_or_default();
        let value = Arc::new(list);
        *self.subnets.write().await = Some(Cached { fetched: now, value: value.clone() });
        Ok(value)
    }

    // ── one pool → one module ─────────────────────────────────────────────

    fn kind_of(&self, pool: &Pool, cats: &HashMap<String, String>) -> String {
        if let Some(c) = cats.get(&pool.project) {
            return c.clone();
        }
        let p = pool.project.to_lowercase();
        if p.contains("staked") || p.contains("staking") || p.contains("lido") {
            "Liquid Staking".into()
        } else if p.contains("lend") || p.contains("aave") || p.contains("compound") || p.contains("morpho") {
            "Lending".into()
        } else if ["swap", "dex", "amm", "curve", "uniswap", "aerodrome", "raydium", "orca", "meteora"]
            .iter()
            .any(|k| p.contains(k))
        {
            "Dexs".into()
        } else {
            "Yield".into()
        }
    }

    fn module_from_pool(&self, pool: &Pool, cats: &HashMap<String, String>) -> Value {
        let spec = dex::chain(&pool.chain.to_lowercase());
        let chain_id = spec.map(|c| c.id).unwrap_or("other");
        let kind = self.kind_of(pool, cats);
        let terms = self.registry.terms(&kind, &pool.project);
        let adapter = self.registry.adapter_for(pool);
        let apy = pool.apy.unwrap_or(0.0);
        let base = pool.apy_base.unwrap_or(0.0);
        let emissions = if apy > 0.0 { ((apy - base).max(0.0) / apy) * 100.0 } else { 0.0 };
        let multi = pool.exposure.as_deref() == Some("multi");
        let il = pool.il_risk.as_deref() == Some("yes");

        let mut conditions: Vec<Value> = Vec::new();
        let cond = |list: &mut Vec<Value>, level: &str, text: String| {
            list.push(json!({ "level": level, "text": text }));
        };
        if terms.gated {
            cond(&mut conditions, "hard", "KYC / whitelist gated — not enterable from this desk".into());
        }
        if il || multi {
            cond(&mut conditions, "risk", format!(
                "paired exposure{} — you hold both sides at the pool's ratio",
                if il { " with impermanent loss" } else { "" }
            ));
        }
        if emissions > 50.0 {
            cond(&mut conditions, "risk", format!(
                "{:.0}% of the rate is token emissions — a farm with an expiry date, not a yield",
                emissions
            ));
        }
        if pool.outlier {
            cond(&mut conditions, "risk", "flagged as a statistical outlier by the index".into());
        }
        if let Some(change) = pool.apy_pct_7d {
            if change <= -30.0 {
                cond(&mut conditions, "note", format!("rate fell {:.0}% over the last 7 days", -change));
            }
        }
        if pool.tvl_usd < 1_000_000.0 {
            cond(&mut conditions, "note", format!("thin — ${:.0}k in the pool", pool.tvl_usd / 1000.0));
        }
        if terms.lock_days > 0 {
            cond(&mut conditions, "hard", format!("locked {} days", terms.lock_days));
        }
        if terms.exit_delay_days > 0 {
            cond(&mut conditions, "note", format!(
                "native exit takes up to {} day{}",
                terms.exit_delay_days,
                if terms.exit_delay_days == 1 { "" } else { "s" }
            ));
        }
        if let Some(c) = spec {
            if !c.testnet {
                cond(&mut conditions, "note", format!("{} is real money — entering needs confirm=true", c.label));
            }
        }
        match adapter {
            Some(a) => {
                if a.kind == "swap_receipt" {
                    cond(&mut conditions, "note", format!(
                        "entry is a market buy of {} — slippage is the liquidity limit, measured live by a quote",
                        a.receipt.as_ref().map(|r| r.symbol.as_str()).unwrap_or("the receipt")
                    ));
                }
            }
            None => cond(&mut conditions, "note", "no adapter here yet — read-only; enter through the protocol's own app".into()),
        }

        json!({
            "id": format!("llama:{}", pool.pool),
            "source": "defillama",
            "chain": chain_id,
            "chain_label": spec.map(|c| c.label).unwrap_or(pool.chain.as_str()),
            "project": pool.project,
            "name": match &pool.pool_meta {
                Some(m) if !m.trim().is_empty() => format!("{} · {}", pool.symbol, m),
                _ => pool.symbol.clone(),
            },
            "symbol": pool.symbol,
            "kind": kind,
            "returns": {
                "apy": round2(apy),
                "apy_base": pool.apy_base.map(round2),
                "apy_reward": pool.apy_reward.map(round2),
                "apy_mean_30d": pool.apy_mean_30d.map(round2),
                "apy_change_7d": pool.apy_pct_7d.map(round2),
                "emissions_share": round2(emissions),
                "basis": "DefiLlama yields index — the protocol's own reported rate",
            },
            "liquidity": {
                "tvl_usd": round2(pool.tvl_usd),
                "depth": depth_word(pool.tvl_usd),
                "entry": terms.entry,
                "exit": terms.exit,
                "exit_note": terms.exit_note,
                "exit_delay_days": terms.exit_delay_days,
                "lock_days": terms.lock_days,
                "instant_exit": terms.exit == "instant" || terms.exit.ends_with("_or_market") || terms.exit == "market",
            },
            "conditions": conditions,
            "stablecoin": pool.stablecoin,
            "exposure": pool.exposure,
            "il_risk": pool.il_risk,
            "underlying": pool.underlying_tokens,
            "reward_tokens": pool.reward_tokens,
            "adapter": adapter.map(|a| adapter_view(a, spec, &terms)),
            "addable": adapter.is_some() && !terms.gated,
            "gated": terms.gated,
            "score": round2(apy * (pool.tvl_usd / DEPTH_FULL).min(1.0).max(0.0).sqrt()),
        })
    }

    fn module_from_subnet(&self, subnet: &Value) -> Option<Value> {
        let netuid = subnet.get("netuid")?.as_u64()?;
        let name = subnet
            .get("subnet_name")
            .or_else(|| subnet.get("name"))
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        let tao_in = subnet.get("tao_in").and_then(|v| v.as_f64()).unwrap_or(0.0);
        let alpha_in = subnet.get("alpha_in").and_then(|v| v.as_f64()).unwrap_or(0.0);
        let price = subnet.get("price").and_then(|v| v.as_f64()).unwrap_or(0.0);
        let volume = subnet.get("subnet_volume").and_then(|v| v.as_f64());
        let emission = subnet.get("emission").and_then(|v| v.as_f64()).unwrap_or(0.0);
        let mut conditions = vec![
            json!({ "level": "risk", "text": "the return is alpha emission on top of a floating alpha/TAO price — no APY is quoted because none is promised" }),
            json!({ "level": "note", "text": format!("pool depth {:.0} TAO — a buy of X TAO moves the price by roughly X/{:.0}", tao_in, tao_in.max(1.0)) }),
            json!({ "level": "note", "text": "Bittensor finney is real money — entering needs confirm=true; buying is a stake, selling is an unstake" }),
        ];
        if netuid == 0 {
            conditions.push(json!({ "level": "note", "text": "root: stake TAO itself, earns a share of every subnet's emission; price is pinned at 1" }));
        }
        Some(json!({
            "id": format!("tao:sn{netuid}"),
            "source": "bittensor",
            "chain": "tao",
            "chain_label": "Bittensor",
            "project": if name.is_empty() { format!("subnet {netuid}") } else { name },
            "name": format!("SN{netuid} {}", subnet.get("symbol").and_then(|v| v.as_str()).unwrap_or("α")),
            "symbol": format!("SN{netuid}"),
            "kind": "Subnet (dTAO)",
            "returns": {
                "apy": Value::Null,
                "apy_base": Value::Null,
                "apy_reward": Value::Null,
                "emissions_share": 100.0,
                "emission_tao_per_block": emission,
                "alpha_price_tao": price,
                "basis": "bt_subnets — alpha emission accrues to stakers; the TAO value of alpha floats with the pool",
            },
            "liquidity": {
                "tvl_usd": Value::Null,
                "tvl_tao": round2(tao_in),
                "alpha_in_pool": round2(alpha_in),
                "volume_tao": volume.map(round2),
                "depth": depth_word(tao_in * 350.0),
                "entry": "instant",
                "exit": "market",
                "exit_note": "unstake any time at the pool's constant-product price; size against the TAO reserve",
                "exit_delay_days": 0,
                "lock_days": 0,
                "instant_exit": true,
            },
            "conditions": conditions,
            "stablecoin": false,
            "adapter": {
                "kind": "tao_subnet",
                "module": "bt",
                "asset": { "symbol": "TAO", "native": true },
                "receipt": { "symbol": format!("SN{netuid} alpha"), "address": format!("SN{netuid}") },
                "enter": format!("bt_buy — stake TAO into SN{netuid}'s pool for alpha (bt signs with your coldkey)"),
                "exit": format!("bt_sell — unstake SN{netuid} alpha back to TAO at the pool price"),
                "executed_by": "bt module (dTAO pool)",
            },
            "addable": true,
            "gated": false,
            "score": 0.0,
        }))
    }

    fn modules_from_composer(&self, store: &crate::storage::Store, catalog: &crate::catalog::Catalog) -> Vec<Value> {
        let mut out = Vec::new();
        for protocol in store.list() {
            let Some(deployment) = protocol.deployments.last() else { continue };
            let (chain_id, network, label, testnet) = match deployment.chain_id {
                1 => ("ethereum", "mainnet".to_string(), "Ethereum".to_string(), false),
                8453 => ("base", "base".to_string(), "Base".to_string(), false),
                11155111 => ("sepolia", "sepolia".to_string(), "Ethereum Sepolia".to_string(), true),
                84532 => ("base-sepolia", "base-sepolia".to_string(), "Base Sepolia".to_string(), true),
                31337 => ("local", "local".to_string(), "local EVM".to_string(), true),
                id => ("evm", id.to_string(), format!("chain {id}"), true),
            };
            for node in &protocol.graph.nodes {
                if node.block != "vault" && node.block != "savings" {
                    continue;
                }
                let Some(address) = deployment.addresses.get(&node.id).and_then(|a| a.as_str()) else { continue };
                let asset_node = protocol
                    .graph
                    .edges
                    .iter()
                    .find(|e| e.to == node.id && e.port == "asset")
                    .and_then(|e| protocol.graph.nodes.iter().find(|n| n.id == e.from));
                let asset_address = asset_node
                    .and_then(|n| deployment.addresses.get(&n.id))
                    .and_then(|a| a.as_str())
                    .map(|s| s.to_string());
                let asset_symbol = asset_node
                    .and_then(|n| n.params.get("symbol_").and_then(|v| v.as_str()))
                    .unwrap_or("asset")
                    .to_string();
                let rate_bps = if node.block == "savings" {
                    node.params.get("rateBps_").and_then(|v| v.as_f64())
                } else {
                    protocol
                        .graph
                        .edges
                        .iter()
                        .find(|e| e.to == node.id && e.port == "strategy")
                        .and_then(|e| protocol.graph.nodes.iter().find(|n| n.id == e.from))
                        .and_then(|n| n.params.get("rateBps_").and_then(|v| v.as_f64()))
                };
                let share_symbol = node
                    .params
                    .get("symbol_")
                    .and_then(|v| v.as_str())
                    .unwrap_or("shares")
                    .to_string();
                let block_name = catalog.block(&node.block).map(|b| b.name.clone()).unwrap_or(node.block.clone());
                let mut conditions = vec![
                    json!({ "level": "note", "text": "a contract you composed and deployed — unaudited reference code; read the block's audit before trusting it with size" }),
                    json!({ "level": "note", "text": "the rate is only paid out of what the strategy's reserve was funded with — read shortfall()/totalAssets() to see whether it is backed" }),
                ];
                if !testnet {
                    conditions.push(json!({ "level": "note", "text": format!("{label} is real money — entering needs confirm=true") }));
                }
                out.push(json!({
                    "id": format!("own:{}:{}", protocol.id, node.id),
                    "source": "composer",
                    "chain": chain_id,
                    "chain_label": label,
                    "network": network,
                    "project": protocol.name,
                    "name": format!("{} · {}", block_name, node.label.clone().unwrap_or(node.id.clone())),
                    "symbol": share_symbol,
                    "kind": "Composed vault",
                    "returns": {
                        "apy": rate_bps.map(|b| round2(b / 100.0)),
                        "apy_base": rate_bps.map(|b| round2(b / 100.0)),
                        "apy_reward": 0.0,
                        "emissions_share": 0.0,
                        "basis": if rate_bps.is_some() { "the fixed rate the strategy/savings block was deployed with — paid only while its reserve lasts" } else { "no strategy wired — the vault earns whatever it is later pointed at" },
                    },
                    "liquidity": {
                        "tvl_usd": Value::Null,
                        "depth": "read totalAssets() on chain",
                        "entry": "instant",
                        "exit": "instant",
                        "exit_note": "withdraw(shares) any time; assets come back from the vault's idle balance plus what the strategy can return",
                        "exit_delay_days": 0,
                        "lock_days": 0,
                        "instant_exit": true,
                    },
                    "conditions": conditions,
                    "stablecoin": false,
                    "adapter": {
                        "kind": "mod_vault",
                        "module": "eth",
                        "network": network,
                        "address": address,
                        "asset": { "symbol": asset_symbol, "address": asset_address, "decimals": 18 },
                        "receipt": { "symbol": share_symbol, "address": address, "decimals": 18 },
                        "enter": format!("approve {asset_symbol} → deposit(assets, you) on {address}"),
                        "exit": "withdraw(shares, you) — instant",
                        "executed_by": "eth module (eth_approve + eth_write)",
                    },
                    "addable": asset_address.is_some(),
                    "gated": false,
                    "score": 0.0,
                    "protocol_id": protocol.id,
                    "deployed_at": deployment.at,
                }));
            }
        }
        out
    }

    fn treasury_module(&self, treasury: &crate::treasury::Treasury) -> Value {
        let binding = treasury.binding();
        let bound = !binding.address.is_empty();
        let chain = dex::chain(&binding.network).map(|c| c.id).unwrap_or("base-sepolia");
        json!({
            "id": "treasury",
            "source": "treasury",
            "chain": chain,
            "chain_label": dex::chain(&binding.network).map(|c| c.label).unwrap_or("Base Sepolia"),
            "network": if bound { binding.network.clone() } else { "base-sepolia".into() },
            "project": "BlocTime Treasury",
            "name": "weekly payout, split by BLOC",
            "symbol": if binding.asset.is_empty() { "asset".to_string() } else { "bound asset".to_string() },
            "kind": "Treasury",
            "returns": {
                "apy": Value::Null,
                "basis": "the yield of whichever module you chose for it — the treasury is where a payout goes, not a yield of its own",
            },
            "liquidity": {
                "tvl_usd": Value::Null,
                "depth": if bound { "read /treasury/onchain" } else { "not deployed yet" },
                "entry": "instant",
                "exit": "locked",
                "exit_note": "nothing comes back early: principal streams out a slice a week (return_principal=false) or returns after the term (true)",
                "exit_delay_days": 0,
                "lock_days": 0,
                "instant_exit": false,
            },
            "conditions": [
                { "level": "hard", "text": "locked for the term you choose — cannot be recalled" },
                { "level": "note", "text": "pays out Friday 12:00 EST, pro-rata by BLOC across the registered set" },
                { "level": "note", "text": if bound { format!("bound to {} on {}", binding.address, binding.network) } else { "no treasury bound yet — deploy the treasury block from COMPOSE, then bind it".to_string() } }
            ],
            "stablecoin": false,
            "adapter": {
                "kind": "treasury_lock",
                "module": "eth",
                "address": if bound { json!(binding.address) } else { Value::Null },
                "asset": { "symbol": "the bound asset", "address": if binding.asset.is_empty() { Value::Null } else { json!(binding.asset) } },
                "enter": "treasury_choose (a plan) → treasury_lock (approve + lock() — real)",
                "exit": "none before the term",
                "executed_by": "eth module (eth_approve + eth_write lock())",
            },
            "addable": bound,
            "gated": false,
            "score": 0.0,
        })
    }

    // ── the registry, assembled ───────────────────────────────────────────

    /// Every module, from every source, through one filter.
    pub async fn modules(
        &self,
        filter: &Filter,
        yields: &crate::yields::Yields,
        dex: &Dex,
        store: &crate::storage::Store,
        catalog: &crate::catalog::Catalog,
        treasury: &crate::treasury::Treasury,
    ) -> Result<Value, String> {
        let want_chain = filter.chain.as_deref();
        let mut sources = serde_json::Map::new();
        let mut all: Vec<Value> = Vec::new();

        // DefiLlama — Ethereum, Base, Solana.
        let (pools, fetched) = yields.all().await?;
        let cats = self.categories().await;
        let mut llama_count = 0usize;
        for pool in pools.iter() {
            let Some(spec) = dex::chain(&pool.chain.to_lowercase()) else { continue };
            if spec.testnet {
                continue;
            }
            if let Some(c) = want_chain {
                if c != spec.id && c != "evm" && !(c == "evm" && spec.module == "eth") {
                    continue;
                }
            }
            if pool.tvl_usd < filter.min_tvl || pool.apy.unwrap_or(0.0) <= 0.0 {
                continue;
            }
            if !filter.include_outliers && pool.outlier {
                continue;
            }
            if filter.stable_only && !pool.stablecoin {
                continue;
            }
            if filter.organic_only {
                let base = pool.apy_base.unwrap_or(0.0);
                if base <= 0.0 || base < pool.apy.unwrap_or(0.0) * 0.6 {
                    continue;
                }
            }
            llama_count += 1;
            all.push(self.module_from_pool(pool, &cats));
        }
        sources.insert("defillama".into(), json!({
            "chains": ["ethereum", "base", "solana"],
            "pools_in_scope": llama_count,
            "as_of": fetched,
            "age_seconds": crate::auth::now().saturating_sub(fetched),
            "categories_known": cats.len(),
        }));

        // Bittensor — through the bt module. Only fetched when it could matter.
        let wants_tao = want_chain.map(|c| c == "tao" || c == "bittensor").unwrap_or(true);
        if wants_tao && filter.min_tvl <= 10_000_000.0 {
            match self.subnets(dex).await {
                Ok(list) => {
                    let mut n = 0;
                    for subnet in list.iter() {
                        if let Some(m) = self.module_from_subnet(subnet) {
                            all.push(m);
                            n += 1;
                        }
                    }
                    sources.insert("bittensor".into(), json!({ "subnets": n, "via": "bt_subnets", "note": "no APY is quoted for a subnet — the return is emission on a floating price" }));
                }
                Err(e) => {
                    sources.insert("bittensor".into(), json!({ "subnets": 0, "error": e }));
                }
            }
        }

        // The composer's own vaults, and the treasury.
        let own = self.modules_from_composer(store, catalog);
        sources.insert("composer".into(), json!({ "vaults": own.len() }));
        if want_chain.map(|c| c != "tao").unwrap_or(true) {
            all.extend(own);
            all.push(self.treasury_module(treasury));
        }

        // Cross-source filters.
        let mut hits: Vec<Value> = all
            .into_iter()
            .filter(|m| {
                if let Some(c) = want_chain {
                    let mc = m.get("chain").and_then(|v| v.as_str()).unwrap_or("");
                    if !(mc == c || (c == "evm" && (mc == "ethereum" || mc == "base"))) {
                        return false;
                    }
                }
                if filter.addable_only && m.get("addable") != Some(&json!(true)) {
                    return false;
                }
                if filter.instant_only && m.pointer("/liquidity/instant_exit") != Some(&json!(true)) {
                    return false;
                }
                if let Some(k) = &filter.kind {
                    if !m.get("kind").and_then(|v| v.as_str()).map(|v| v.eq_ignore_ascii_case(k)).unwrap_or(false) {
                        return false;
                    }
                }
                if let Some(q) = &filter.q {
                    let needle = q.to_lowercase();
                    let hay = format!(
                        "{} {} {} {} {}",
                        m.get("project").and_then(|v| v.as_str()).unwrap_or(""),
                        m.get("name").and_then(|v| v.as_str()).unwrap_or(""),
                        m.get("kind").and_then(|v| v.as_str()).unwrap_or(""),
                        m.get("chain_label").and_then(|v| v.as_str()).unwrap_or(""),
                        m.pointer("/adapter/kind").and_then(|v| v.as_str()).unwrap_or(""),
                    )
                    .to_lowercase();
                    if !hay.contains(&needle) {
                        return false;
                    }
                }
                true
            })
            .collect();

        let key = |m: &Value| -> f64 {
            match filter.sort.as_str() {
                "apy" => m.pointer("/returns/apy").and_then(|v| v.as_f64()).unwrap_or(0.0),
                "tvl" => m.pointer("/liquidity/tvl_usd").and_then(|v| v.as_f64()).unwrap_or(0.0),
                "base" => m.pointer("/returns/apy_base").and_then(|v| v.as_f64()).unwrap_or(0.0),
                "mean30d" => m.pointer("/returns/apy_mean_30d").and_then(|v| v.as_f64()).unwrap_or(0.0),
                _ => m.get("score").and_then(|v| v.as_f64()).unwrap_or(0.0),
            }
        };
        hits.sort_by(|a, b| key(b).partial_cmp(&key(a)).unwrap_or(std::cmp::Ordering::Equal));
        let matched = hits.len();
        let addable = hits.iter().filter(|m| m.get("addable") == Some(&json!(true))).count();
        hits.truncate(filter.limit);

        Ok(json!({
            "modules": hits,
            "matched": matched,
            "addable": addable,
            "shown": matched.min(filter.limit),
            "sort": filter.sort,
            "sources": sources,
            "chains": [
                { "id": "ethereum", "label": "Ethereum", "module": "eth", "venue": "Uniswap V3 + protocol contracts" },
                { "id": "base", "label": "Base", "module": "eth", "venue": "Uniswap V3 + protocol contracts" },
                { "id": "solana", "label": "Solana", "module": "solana", "venue": "Jupiter" },
                { "id": "tao", "label": "Bittensor", "module": "bt", "venue": "dTAO subnet pools", "preview": true }
            ],
            "rule": "a module is anything money can go into that gives a return. Each one carries its own returns, liquidity and conditions, and an adapter says how this desk enters it — through the module that owns the chain, never with a key of its own.",
        }))
    }

    /// Chains, kinds and adapters present right now, for a filter bar that does
    /// not go stale.
    pub async fn facets(
        &self,
        yields: &crate::yields::Yields,
        dex: &Dex,
        store: &crate::storage::Store,
        catalog: &crate::catalog::Catalog,
        treasury: &crate::treasury::Treasury,
    ) -> Result<Value, String> {
        // `modules` truncates to its limit; count off the full set.
        let filter = Filter { min_tvl: 100_000.0, limit: 100_000, sort: "score".into(), ..Default::default() };
        let all = self.modules(&filter, yields, dex, store, catalog, treasury).await?;
        let list = all.get("modules").and_then(|m| m.as_array()).cloned().unwrap_or_default();
        let mut chains: HashMap<String, (usize, usize)> = HashMap::new();
        let mut kinds: HashMap<String, (usize, usize)> = HashMap::new();
        let mut adapters: HashMap<String, usize> = HashMap::new();
        for m in &list {
            let addable = m.get("addable") == Some(&json!(true));
            let c = chains.entry(m.get("chain").and_then(|v| v.as_str()).unwrap_or("?").to_string()).or_default();
            c.0 += 1;
            if addable {
                c.1 += 1;
            }
            let k = kinds.entry(m.get("kind").and_then(|v| v.as_str()).unwrap_or("?").to_string()).or_default();
            k.0 += 1;
            if addable {
                k.1 += 1;
            }
            if let Some(a) = m.pointer("/adapter/kind").and_then(|v| v.as_str()) {
                *adapters.entry(a.to_string()).or_default() += 1;
            }
        }
        let mut kinds: Vec<Value> = kinds
            .into_iter()
            .map(|(k, (n, a))| json!({ "kind": k, "modules": n, "addable": a }))
            .collect();
        kinds.sort_by(|a, b| b["modules"].as_u64().cmp(&a["modules"].as_u64()));
        let chain_rows: Vec<Value> = ["ethereum", "base", "solana", "tao"]
            .iter()
            .map(|c| json!({
                "id": c,
                "modules": chains.get(*c).map(|v| v.0).unwrap_or(0),
                "addable": chains.get(*c).map(|v| v.1).unwrap_or(0)
            }))
            .collect();
        Ok(json!({
            "chains": chain_rows,
            "kinds": kinds,
            "adapters": adapters,
            "total": list.len(),
            "sources": all.get("sources").cloned().unwrap_or(Value::Null),
        }))
    }

    /// One module by id, from whichever source owns it.
    pub async fn module(
        &self,
        id: &str,
        yields: &crate::yields::Yields,
        dex: &Dex,
        store: &crate::storage::Store,
        catalog: &crate::catalog::Catalog,
        treasury: &crate::treasury::Treasury,
        history: bool,
    ) -> Result<Value, String> {
        if let Some(pool_id) = id.strip_prefix("llama:") {
            let (pools, _) = yields.all().await?;
            let pool = pools
                .iter()
                .find(|p| p.pool == pool_id)
                .ok_or_else(|| format!("no module '{id}' — ids come from /modules"))?;
            let cats = self.categories().await;
            let mut m = self.module_from_pool(pool, &cats);
            if history {
                if let Ok(detail) = yields.pool(pool_id, true).await {
                    m["chart"] = detail.get("chart").cloned().unwrap_or(Value::Null);
                }
            }
            return Ok(m);
        }
        if let Some(rest) = id.strip_prefix("tao:sn") {
            let netuid: u64 = rest.parse().map_err(|_| format!("'{id}' is not a subnet id"))?;
            let list = self.subnets(dex).await?;
            let subnet = list
                .iter()
                .find(|s| s.get("netuid").and_then(|v| v.as_u64()) == Some(netuid))
                .ok_or_else(|| format!("no subnet {netuid} in bt_subnets"))?;
            let mut m = self.module_from_subnet(subnet).ok_or("bad subnet row")?;
            if let Ok(price) = dex.peer("bt", "bt_price", json!({ "netuid": netuid }), None).await {
                m["price"] = price;
            }
            return Ok(m);
        }
        if id.starts_with("own:") {
            return self
                .modules_from_composer(store, catalog)
                .into_iter()
                .find(|m| m.get("id").and_then(|v| v.as_str()) == Some(id))
                .ok_or_else(|| format!("no composed module '{id}' — it needs a recorded deployment"));
        }
        if id == "treasury" {
            return Ok(self.treasury_module(treasury));
        }
        Err(format!("no module '{id}' — ids look like llama:<pool>, tao:sn<netuid>, own:<protocol>:<node>, or treasury"))
    }

    // ── quoting an entry ──────────────────────────────────────────────────

    /// What putting `amount` in would do, and what taking it out today would
    /// cost — the liquidity restriction measured, not described. Reads only.
    pub async fn quote(&self, module: &Value, body: &Value, dex: &Dex, token: Option<&str>) -> Result<Value, String> {
        let amount = amount_arg(body)?;
        let adapter = module.get("adapter").filter(|a| !a.is_null()).ok_or_else(|| {
            "this module has no adapter here — it is listed with its terms, but cannot be entered from this desk".to_string()
        })?;
        if module.get("gated") == Some(&json!(true)) {
            return Err("this module is KYC / whitelist gated — enter it through the issuer".into());
        }
        let kind = adapter.get("kind").and_then(|v| v.as_str()).unwrap_or("");
        let chain = module.get("chain").and_then(|v| v.as_str()).unwrap_or("");
        let asset = adapter.get("asset").cloned().unwrap_or(Value::Null);
        let receipt = adapter.get("receipt").cloned().unwrap_or(Value::Null);
        let asset_symbol = asset.get("symbol").and_then(|v| v.as_str()).unwrap_or("asset").to_string();
        let network = adapter.get("network").and_then(|v| v.as_str()).map(|s| s.to_string())
            .or_else(|| dex::chain(chain).map(|c| c.network.to_string()))
            .unwrap_or_else(|| chain.to_string());

        let summary = json!({
            "id": module.get("id"), "project": module.get("project"), "name": module.get("name"),
            "chain": chain, "kind": module.get("kind"), "apy": module.pointer("/returns/apy"),
        });

        match kind {
            "swap_receipt" | "tao_subnet" => {
                let sell = token_handle(&asset);
                let buy = token_handle(&receipt);
                let entry = dex.quote(&json!({ "chain": chain, "sell": sell, "buy": buy, "amount": amount, "slippageBps": 50 }), token).await?;
                let expected = entry.pointer("/buy/amount").and_then(|v| v.as_str()).unwrap_or("0").to_string();
                // The way back: sell what you would hold. On Bittensor a sell is
                // sized in TAO-equivalent, so the same amount is the right ask.
                let back_amount = if kind == "tao_subnet" { amount.clone() } else { expected.clone() };
                let exit = dex.quote(&json!({ "chain": chain, "sell": buy, "buy": sell, "amount": back_amount, "slippageBps": 50 }), token).await;
                let (exit_view, round_trip) = match exit {
                    Ok(x) => {
                        let back: f64 = x.pointer("/buy/amount").and_then(|v| v.as_str()).and_then(|s| s.parse().ok()).unwrap_or(0.0);
                        let put: f64 = amount.parse().unwrap_or(0.0);
                        let cost = if put > 0.0 { (put - back) / put * 100.0 } else { 0.0 };
                        (json!({ "sell": back_amount, "get_back": x.pointer("/buy/amount"), "impact_pct": x.get("price_impact_pct"), "route": x.get("route"), "venue": x.get("venue") }), Some(round2(cost)))
                    }
                    Err(e) => (json!({ "error": e }), None),
                };
                Ok(json!({
                    "module": summary,
                    "amount": amount, "asset": asset_symbol,
                    "adapter": kind,
                    "plan": [
                        { "step": 1, "what": format!("{} — buy {} with {} {}", adapter.get("enter").and_then(|v| v.as_str()).unwrap_or("swap"), receipt.get("symbol").and_then(|v| v.as_str()).unwrap_or("receipt"), amount, asset_symbol), "by": adapter.get("module") }
                    ],
                    "entry": { "expected": expected, "receipt": receipt.get("symbol"), "impact_pct": entry.get("price_impact_pct"), "min_after_slippage": entry.get("min_received"), "route": entry.get("route"), "venue": entry.get("venue"), "quoted_by": entry.get("quoted_by") },
                    "exit_today": exit_view,
                    "round_trip_cost_pct": round_trip,
                    "liquidity": module.get("liquidity"),
                    "conditions": module.get("conditions"),
                    "reads_only": true,
                }))
            }
            "erc4626" | "mod_vault" => {
                let address = adapter.get("address").and_then(|v| v.as_str()).ok_or("adapter has no address")?;
                let decimals = asset.get("decimals").and_then(|v| v.as_u64()).unwrap_or(18) as u32;
                let units = dex::to_base_units(&amount, decimals)?;
                let abi = if kind == "erc4626" { erc4626_abi() } else { mod_vault_abi() };
                let preview_fn = if kind == "erc4626" { "previewDeposit" } else { "convertToShares" };
                let shares = dex
                    .peer("eth", "eth_read", json!({ "address": address, "function": preview_fn, "args": [units.to_string()], "network": network, "abi": abi }), token)
                    .await
                    .ok()
                    .and_then(|v| dex::first_uint(v.get("result")));
                let total = dex
                    .peer("eth", "eth_read", json!({ "address": address, "function": "totalAssets", "args": [], "network": network, "abi": abi }), token)
                    .await
                    .ok()
                    .and_then(|v| dex::first_uint(v.get("result")));
                let receipt_decimals = receipt.get("decimals").and_then(|v| v.as_u64()).unwrap_or(18) as u32;
                Ok(json!({
                    "module": summary,
                    "amount": amount, "asset": asset_symbol, "adapter": kind,
                    "plan": [
                        { "step": 1, "what": format!("eth_approve {} {} → {}", amount, asset_symbol, address), "by": "eth" },
                        { "step": 2, "what": format!("eth_write deposit({}, you) on {}", units, address), "by": "eth" }
                    ],
                    "entry": { "expected": shares.map(|s| dex::from_base_units(s, receipt_decimals)), "receipt": receipt.get("symbol"), "impact_pct": 0.0, "quoted_by": format!("{preview_fn}() on chain") },
                    "exit_today": { "how": if kind == "erc4626" { "withdraw(assets, you, you) or redeem(shares, you, you)" } else { "withdraw(shares, you)" }, "vault_total_assets": total.map(|t| dex::from_base_units(t, decimals)), "note": module.pointer("/liquidity/exit_note") },
                    "round_trip_cost_pct": 0.0,
                    "liquidity": module.get("liquidity"),
                    "conditions": module.get("conditions"),
                    "reads_only": true,
                }))
            }
            "aave_v3" | "compound_v3" => {
                let address = adapter.get("address").and_then(|v| v.as_str()).ok_or("adapter has no address")?;
                let decimals = asset.get("decimals").and_then(|v| v.as_u64()).unwrap_or(18) as u32;
                let units = dex::to_base_units(&amount, decimals)?;
                let call = if kind == "aave_v3" { format!("supply({}, {}, you, 0)", asset.get("address").and_then(|v| v.as_str()).unwrap_or("asset"), units) } else { format!("supply({}, {})", asset.get("address").and_then(|v| v.as_str()).unwrap_or("asset"), units) };
                Ok(json!({
                    "module": summary,
                    "amount": amount, "asset": asset_symbol, "adapter": kind,
                    "plan": [
                        { "step": 1, "what": format!("eth_approve {} {} → {}", amount, asset_symbol, address), "by": "eth" },
                        { "step": 2, "what": format!("eth_write {call} on {address}"), "by": "eth" }
                    ],
                    "entry": { "expected": amount, "receipt": if kind == "aave_v3" { "aToken (1:1, rebasing)" } else { "Comet balance (1:1, accruing)" }, "impact_pct": 0.0, "quoted_by": "1:1 by construction" },
                    "exit_today": { "how": if kind == "aave_v3" { "withdraw(asset, amount | all, you)" } else { "withdraw(asset, amount | all)" }, "note": module.pointer("/liquidity/exit_note") },
                    "round_trip_cost_pct": 0.0,
                    "liquidity": module.get("liquidity"),
                    "conditions": module.get("conditions"),
                    "reads_only": true,
                }))
            }
            "treasury_lock" => Ok(json!({
                "module": summary, "amount": amount, "adapter": kind,
                "plan": [
                    { "step": 1, "what": "POST /treasury/allocations — record the choice (a plan)", "by": "defi" },
                    { "step": 2, "what": "POST /treasury/lock — approve + lock(amount, termWeeks, returnPrincipal)", "by": "eth" }
                ],
                "entry": { "expected": amount, "receipt": "a lock in ModBlocTimeTreasury" },
                "exit_today": { "how": "none — the term is the term", "note": module.pointer("/liquidity/exit_note") },
                "liquidity": module.get("liquidity"), "conditions": module.get("conditions"), "reads_only": true,
            })),
            other => Err(format!("adapter kind '{other}' is not something this desk knows how to quote")),
        }
    }

    // ── entering ──────────────────────────────────────────────────────────

    /// Put money in. Refuses a mainnet module without confirm=true, and the
    /// chain module refuses again underneath with its own rule.
    pub async fn enter(
        &self,
        module: &Value,
        body: &Value,
        who: Option<&str>,
        dex: &Dex,
        treasury: &crate::treasury::Treasury,
        token: Option<&str>,
    ) -> Result<Value, String> {
        let amount = amount_arg(body)?;
        let adapter = module.get("adapter").filter(|a| !a.is_null()).ok_or_else(|| {
            "this module has no adapter here — read-only. Enter it through the protocol's own app.".to_string()
        })?;
        if module.get("gated") == Some(&json!(true)) {
            return Err("this module is KYC / whitelist gated — not enterable from this desk".into());
        }
        let kind = adapter.get("kind").and_then(|v| v.as_str()).unwrap_or("").to_string();
        let chain = module.get("chain").and_then(|v| v.as_str()).unwrap_or("").to_string();
        let confirm = body.get("confirm").and_then(|v| v.as_bool()).unwrap_or(false);
        let dry_run = body.get("dryRun").or_else(|| body.get("dry_run")).and_then(|v| v.as_bool()).unwrap_or(false);
        let account = body.get("account").or_else(|| body.get("wallet")).and_then(|v| v.as_str()).map(|s| s.to_string());
        let asset = adapter.get("asset").cloned().unwrap_or(Value::Null);
        let receipt = adapter.get("receipt").cloned().unwrap_or(Value::Null);
        let network = adapter.get("network").and_then(|v| v.as_str()).map(|s| s.to_string())
            .or_else(|| dex::chain(&chain).map(|c| c.network.to_string()))
            .unwrap_or_else(|| chain.clone());
        let testnet = dex::chain(&chain).map(|c| c.testnet).unwrap_or(network.contains("sepolia") || network == "local" || network == "31337");

        if kind == "treasury_lock" {
            let who = who.ok_or("sign in with your wallet to put a choice in the treasury")?;
            let mut choice = body.clone();
            choice["amount"] = json!(amount);
            for (k, v) in [("project", module.get("project")), ("chain", module.get("chain")), ("symbol", module.get("symbol"))] {
                if choice.get(k).is_none() {
                    if let Some(v) = v {
                        choice[k] = v.clone();
                    }
                }
            }
            let allocation = treasury.choose(&choice, who, crate::auth::now())?;
            return Ok(json!({
                "entered": false, "planned": true,
                "allocation": allocation.view(crate::auth::now()),
                "next": "POST /treasury/lock {id, account, confirm} — approve + lock() through the eth module; nothing has moved yet",
            }));
        }

        let quote = self.quote(module, body, dex, token).await.ok();
        if dry_run {
            return Ok(json!({ "entered": false, "dry_run": true, "quote": quote, "reason": "dryRun=true — this is what would have been sent" }));
        }
        if !testnet && !confirm {
            return Ok(json!({
                "entered": false, "needs_confirm": true, "quote": quote,
                "reason": format!("{} is real money. Call again with confirm=true to enter it.", module.get("chain_label").and_then(|v| v.as_str()).unwrap_or(&chain)),
            }));
        }

        let result = match kind.as_str() {
            "swap_receipt" | "tao_subnet" => {
                let mut call = json!({
                    "chain": chain, "sell": token_handle(&asset), "buy": token_handle(&receipt),
                    "amount": amount, "confirm": confirm,
                    "slippageBps": body.get("slippageBps").and_then(|v| v.as_f64()).unwrap_or(50.0),
                });
                for key in ["account", "wallet", "hotkey", "password"] {
                    if let Some(v) = body.get(key) {
                        call[key] = v.clone();
                    }
                }
                let out = dex.swap(&call, token).await?;
                if out.get("traded") != Some(&json!(true)) {
                    return Ok(json!({ "entered": false, "swap": out }));
                }
                out
            }
            "erc4626" | "mod_vault" | "aave_v3" | "compound_v3" => {
                let account = account.clone().ok_or("'account' is required — the name of an eth-module account (eth_accounts lists yours)")?;
                let owner = dex.evm_address(&account, token).await?;
                let address = adapter.get("address").and_then(|v| v.as_str()).ok_or("adapter has no address")?;
                let asset_address = asset.get("address").and_then(|v| v.as_str()).ok_or("adapter asset has no address")?;
                let decimals = asset.get("decimals").and_then(|v| v.as_u64()).unwrap_or(18) as u32;
                let units = dex::to_base_units(&amount, decimals)?;
                let mut approve = json!({ "account": account, "token": asset_address, "spender": address, "amount": format!("{units}wei"), "network": network, "confirm": confirm });
                if let Some(p) = body.get("password") {
                    approve["password"] = p.clone();
                }
                let approval = dex
                    .peer("eth", "eth_approve", approve, token)
                    .await
                    .map_err(|e| format!("the approval failed, so nothing went in: {e}"))?;
                let (function, args, abi) = match kind.as_str() {
                    "erc4626" => ("deposit", json!([units.to_string(), owner]), erc4626_abi()),
                    "mod_vault" => ("deposit", json!([units.to_string(), owner]), mod_vault_abi()),
                    "aave_v3" => ("supply", json!([asset_address, units.to_string(), owner, 0]), aave_abi()),
                    _ => ("supply", json!([asset_address, units.to_string()]), comet_abi()),
                };
                let mut call = json!({ "account": account, "address": address, "function": function, "args": args, "network": network, "abi": abi, "confirm": confirm });
                if let Some(p) = body.get("password") {
                    call["password"] = p.clone();
                }
                let sent = dex.peer("eth", "eth_write", call, token).await?;
                json!({ "traded": true, "executed_by": format!("eth_write {function}() on {address}"), "approval": approval, "result": sent, "owner": owner })
            }
            other => return Err(format!("adapter kind '{other}' cannot be entered from here")),
        };

        let now = crate::auth::now();
        let txs: Vec<String> = ["/result/hash", "/result/signature", "/result/tx", "/result/tx_hash", "/result/result/hash"]
            .iter()
            .filter_map(|p| result.pointer(p).and_then(|v| v.as_str()).map(|s| s.to_string()))
            .collect();
        let position = Position {
            id: format!("p-{now}-{}", short(module.get("id").and_then(|v| v.as_str()).unwrap_or("m"))),
            owner: who.map(|w| w.to_string()).or_else(|| account.clone()).unwrap_or_else(|| "local".into()),
            module: module.get("id").and_then(|v| v.as_str()).unwrap_or("").to_string(),
            chain: chain.clone(),
            network,
            project: module.get("project").and_then(|v| v.as_str()).unwrap_or("").to_string(),
            symbol: module.get("symbol").and_then(|v| v.as_str()).unwrap_or("").to_string(),
            kind: module.get("kind").and_then(|v| v.as_str()).unwrap_or("").to_string(),
            adapter: kind.clone(),
            amount: amount.clone(),
            asset: asset.get("symbol").and_then(|v| v.as_str()).unwrap_or("").to_string(),
            asset_address: asset.get("address").and_then(|v| v.as_str()).map(|s| s.to_string()),
            receipt: if receipt.is_null() { None } else { Some(receipt.clone()) },
            account: account.unwrap_or_default(),
            apy_at_entry: module.pointer("/returns/apy").and_then(|v| v.as_f64()).unwrap_or(0.0),
            apy_base_at_entry: module.pointer("/returns/apy_base").and_then(|v| v.as_f64()).unwrap_or(0.0),
            entered_at: now,
            status: "open".into(),
            txs,
            entry: result.clone(),
            exits: Vec::new(),
            note: body.get("note").and_then(|v| v.as_str()).unwrap_or("").to_string(),
        };
        self.save(&position)?;
        Ok(json!({
            "entered": true,
            "position": self.view(&position, None),
            "quote": quote,
            "execution": result,
        }))
    }

    // ── leaving ───────────────────────────────────────────────────────────

    pub async fn exit(&self, id: &str, body: &Value, dex: &Dex, token: Option<&str>) -> Result<Value, String> {
        let mut position = self.get(id).ok_or_else(|| format!("no position '{id}'"))?;
        if position.status == "closed" {
            return Err("that position is already closed".into());
        }
        let confirm = body.get("confirm").and_then(|v| v.as_bool()).unwrap_or(false);
        let all = body.get("amount").and_then(|v| v.as_str()).map(|s| s.trim().eq_ignore_ascii_case("all")).unwrap_or(true);
        let amount = if all { None } else { Some(amount_arg(body)?) };
        let account = body.get("account").or_else(|| body.get("wallet")).and_then(|v| v.as_str())
            .map(|s| s.to_string())
            .or_else(|| Some(position.account.clone()).filter(|a| !a.is_empty()));
        let testnet = dex::chain(&position.chain).map(|c| c.testnet)
            .unwrap_or(position.network.contains("sepolia") || position.network == "local" || position.network == "31337");
        if !testnet && !confirm {
            return Ok(json!({ "exited": false, "needs_confirm": true, "reason": "real money — call again with confirm=true" }));
        }
        let receipt = position.receipt.clone().unwrap_or(Value::Null);

        let result = match position.adapter.as_str() {
            "swap_receipt" | "tao_subnet" => {
                let sell_amount = match (&amount, position.adapter.as_str(), position.chain.as_str()) {
                    (Some(a), _, _) => a.clone(),
                    (None, "tao_subnet", _) => position.amount.clone(),
                    (None, _, "solana") => return Err("say how much of the receipt token to sell — sol_portfolio shows what you hold; 'all' is not resolvable here on Solana yet".into()),
                    (None, _, _) => {
                        // Everything you hold of the receipt, read on chain.
                        let account = account.clone().ok_or("'account' is required")?;
                        let owner = dex.evm_address(&account, token).await?;
                        let bal = dex.peer("eth", "eth_balance", json!({ "address": owner, "token": receipt.get("address"), "network": position.network }), token).await?;
                        bal.get("balance").and_then(|v| v.as_str().map(|s| s.to_string()).or_else(|| v.as_f64().map(|f| f.to_string())))
                            .or_else(|| bal.get("formatted").and_then(|v| v.as_str()).map(|s| s.to_string()))
                            .ok_or_else(|| format!("could not read the {} balance: {}", receipt.get("symbol").and_then(|v| v.as_str()).unwrap_or("receipt"), bal))?
                    }
                };
                let mut call = json!({
                    "chain": position.chain, "sell": token_handle(&receipt),
                    "buy": if position.adapter == "tao_subnet" { "TAO".to_string() } else { position.asset_address.clone().unwrap_or_else(|| position.asset.clone()) },
                    "amount": sell_amount, "confirm": confirm,
                    "slippageBps": body.get("slippageBps").and_then(|v| v.as_f64()).unwrap_or(50.0),
                });
                if let Some(a) = &account {
                    call["account"] = json!(a);
                }
                for key in ["hotkey", "password"] {
                    if let Some(v) = body.get(key) {
                        call[key] = v.clone();
                    }
                }
                let out = dex.swap(&call, token).await?;
                if out.get("traded") != Some(&json!(true)) {
                    return Ok(json!({ "exited": false, "swap": out }));
                }
                out
            }
            "erc4626" | "mod_vault" | "aave_v3" | "compound_v3" => {
                let account = account.clone().ok_or("'account' is required — the eth-module account that entered")?;
                let owner = dex.evm_address(&account, token).await?;
                let address = receipt.get("address").and_then(|v| v.as_str()).map(|s| s.to_string())
                    .or_else(|| position.entry.get("executed_by").and_then(|v| v.as_str()).and_then(|s| s.rsplit(' ').next()).map(|s| s.to_string()))
                    .ok_or("this position has no contract address on record")?;
                let asset_address = position.asset_address.clone().unwrap_or_default();
                let decimals = 18u32.min(if position.asset.eq_ignore_ascii_case("USDC") || position.asset.eq_ignore_ascii_case("USDT") || position.asset.eq_ignore_ascii_case("USDbC") { 6 } else if position.asset.eq_ignore_ascii_case("WBTC") || position.asset.eq_ignore_ascii_case("cbBTC") { 8 } else { 18 });
                let units = amount.as_ref().map(|a| dex::to_base_units(a, decimals)).transpose()?;
                let (function, args, abi) = match position.adapter.as_str() {
                    "erc4626" => match units {
                        Some(u) => ("withdraw", json!([u.to_string(), owner, owner]), erc4626_abi()),
                        None => {
                            let shares = self.read_uint(dex, &address, "balanceOf", json!([owner]), &position.network, erc4626_abi(), token).await?;
                            ("redeem", json!([shares.to_string(), owner, owner]), erc4626_abi())
                        }
                    },
                    "mod_vault" => {
                        let shares = match units {
                            Some(u) => self.read_uint(dex, &address, "convertToShares", json!([u.to_string()]), &position.network, mod_vault_abi(), token).await?,
                            None => self.read_uint(dex, &address, "balanceOf", json!([owner]), &position.network, mod_vault_abi(), token).await?,
                        };
                        ("withdraw", json!([shares.to_string(), owner]), mod_vault_abi())
                    }
                    "aave_v3" => ("withdraw", json!([asset_address, units.map(|u| u.to_string()).unwrap_or_else(|| MAX_UINT.into()), owner]), aave_abi()),
                    _ => ("withdraw", json!([asset_address, units.map(|u| u.to_string()).unwrap_or_else(|| MAX_UINT.into())]), comet_abi()),
                };
                let mut call = json!({ "account": account, "address": address, "function": function, "args": args, "network": position.network, "abi": abi, "confirm": confirm });
                if let Some(p) = body.get("password") {
                    call["password"] = p.clone();
                }
                let sent = dex.peer("eth", "eth_write", call, token).await?;
                json!({ "traded": true, "executed_by": format!("eth_write {function}() on {address}"), "result": sent })
            }
            other => return Err(format!("positions entered through '{other}' cannot be exited from here")),
        };

        let now = crate::auth::now();
        position.exits.push(json!({ "at": now, "amount": amount.clone().unwrap_or_else(|| "all".into()), "result": result }));
        if all {
            position.status = "closed".into();
        }
        self.save(&position)?;
        Ok(json!({ "exited": true, "position": self.view(&position, None), "execution": position.exits.last() }))
    }

    async fn read_uint(&self, dex: &Dex, address: &str, function: &str, args: Value, network: &str, abi: Value, token: Option<&str>) -> Result<u128, String> {
        let out = dex.peer("eth", "eth_read", json!({ "address": address, "function": function, "args": args, "network": network, "abi": abi }), token).await?;
        dex::first_uint(out.get("result")).ok_or_else(|| format!("{function}() on {address} did not return a number: {out}"))
    }

    /// What a position is worth now, read on chain where this desk can.
    pub async fn value(&self, id: &str, dex: &Dex, token: Option<&str>) -> Result<Value, String> {
        let position = self.get(id).ok_or_else(|| format!("no position '{id}'"))?;
        let receipt = position.receipt.clone().unwrap_or(Value::Null);
        let read = match position.adapter.as_str() {
            "erc4626" | "mod_vault" | "compound_v3" | "swap_receipt" if position.chain != "solana" && position.chain != "tao" => {
                let owner = dex.evm_address(&position.account, token).await?;
                let address = receipt.get("address").and_then(|v| v.as_str()).ok_or("no receipt address on record")?;
                let dec = receipt.get("decimals").and_then(|v| v.as_u64()).unwrap_or(18) as u32;
                match position.adapter.as_str() {
                    "erc4626" | "mod_vault" => {
                        let abi = if position.adapter == "erc4626" { erc4626_abi() } else { mod_vault_abi() };
                        let shares = self.read_uint(dex, address, "balanceOf", json!([owner]), &position.network, abi.clone(), token).await?;
                        let assets = self.read_uint(dex, address, "convertToAssets", json!([shares.to_string()]), &position.network, abi, token).await?;
                        let adec = if position.asset.eq_ignore_ascii_case("USDC") || position.asset.eq_ignore_ascii_case("USDT") { 6 } else if position.asset.to_uppercase().ends_with("BTC") { 8 } else { 18 };
                        json!({ "shares": dex::from_base_units(shares, dec), "assets": dex::from_base_units(assets, adec), "symbol": position.asset, "basis": "balanceOf → convertToAssets on chain" })
                    }
                    "compound_v3" => {
                        let bal = self.read_uint(dex, address, "balanceOf", json!([owner]), &position.network, comet_abi(), token).await?;
                        let adec = if position.asset.to_uppercase().starts_with("USD") { 6 } else { 18 };
                        json!({ "assets": dex::from_base_units(bal, adec), "symbol": position.asset, "basis": "Comet.balanceOf — base asset incl. accrued interest" })
                    }
                    _ => {
                        let bal = dex.peer("eth", "eth_balance", json!({ "address": owner, "token": address, "network": position.network }), token).await?;
                        json!({ "receipt": bal, "symbol": receipt.get("symbol"), "basis": "eth_balance of the receipt token — sell it to see the asset value (quote)" })
                    }
                }
            }
            "aave_v3" => json!({ "note": "aToken balance — read it with eth_balance on the reserve's aToken, or on app.aave.com; not derived here" }),
            _ if position.chain == "solana" => json!({ "note": "read with sol_portfolio on the solana module — the receipt token is what you hold" }),
            _ if position.chain == "tao" => json!({ "note": "read with bt_portfolio on the bt module — your alpha and its TAO value" }),
            _ => json!({ "note": "not readable from here" }),
        };
        Ok(json!({ "position": self.view(&position, None), "value": read }))
    }

    // ── the ledger ────────────────────────────────────────────────────────

    fn path(&self, id: &str) -> PathBuf {
        self.root.join(format!("{}.json", sanitize(id)))
    }

    pub fn list(&self) -> Vec<Position> {
        let mut out = Vec::new();
        if let Ok(entries) = std::fs::read_dir(&self.root) {
            for entry in entries.flatten() {
                if let Ok(body) = std::fs::read_to_string(entry.path()) {
                    if let Ok(p) = serde_json::from_str::<Position>(&body) {
                        out.push(p);
                    }
                }
            }
        }
        out.sort_by(|a, b| b.entered_at.cmp(&a.entered_at));
        out
    }

    pub fn get(&self, id: &str) -> Option<Position> {
        serde_json::from_str(&std::fs::read_to_string(self.path(id)).ok()?).ok()
    }

    fn save(&self, p: &Position) -> Result<(), String> {
        std::fs::write(self.path(&p.id), serde_json::to_string_pretty(p).map_err(|e| e.to_string())?).map_err(|e| e.to_string())
    }

    pub fn forget(&self, id: &str, who: Option<&str>, owner: &str) -> Result<(), String> {
        let p = self.get(id).ok_or_else(|| format!("no position '{id}'"))?;
        let mine = who.map(|w| w.eq_ignore_ascii_case(&p.owner) || w.eq_ignore_ascii_case(owner)).unwrap_or(false);
        if !mine && p.owner != "local" {
            return Err("that position was entered by someone else".into());
        }
        std::fs::remove_file(self.path(id)).map_err(|e| e.to_string())
    }

    fn view(&self, p: &Position, live: Option<&Value>) -> Value {
        let mut v = serde_json::to_value(p).unwrap_or(Value::Null);
        let days = (crate::auth::now().saturating_sub(p.entered_at)) as f64 / 86_400.0;
        let amount: f64 = p.amount.parse().unwrap_or(0.0);
        v["days_in"] = json!(round2(days));
        v["projected_earned"] = json!(round4(amount * (p.apy_at_entry / 100.0) * days / 365.0));
        v["projected_basis"] = json!("amount × APY at entry × days/365 — a projection off the rate when you entered, not a balance");
        if let Some(m) = live {
            v["apy_now"] = m.pointer("/returns/apy").cloned().unwrap_or(Value::Null);
            v["apy_drift"] = m
                .pointer("/returns/apy")
                .and_then(|a| a.as_f64())
                .map(|a| json!(round2(a - p.apy_at_entry)))
                .unwrap_or(Value::Null);
            v["liquidity_now"] = m.get("liquidity").cloned().unwrap_or(Value::Null);
        }
        v
    }

    /// The book: every position, with the module's rate now beside the rate at
    /// entry, so drift is visible without a second query.
    pub async fn positions(&self, yields: &crate::yields::Yields, who: Option<&str>) -> Result<Value, String> {
        let list = self.list();
        let (pools, _) = yields.all().await.unwrap_or((Arc::new(Vec::new()), 0));
        let cats = self.categories().await;
        let mut rows = Vec::new();
        let mut open_by_chain: HashMap<String, usize> = HashMap::new();
        for p in &list {
            let live = p
                .module
                .strip_prefix("llama:")
                .and_then(|pid| pools.iter().find(|x| x.pool == pid))
                .map(|pool| self.module_from_pool(pool, &cats));
            if p.status == "open" {
                *open_by_chain.entry(p.chain.clone()).or_default() += 1;
            }
            rows.push(self.view(p, live.as_ref()));
        }
        Ok(json!({
            "positions": rows,
            "count": list.len(),
            "open": list.iter().filter(|p| p.status == "open").count(),
            "open_by_chain": open_by_chain,
            "viewer": who,
            "ledger": "~/.mod/defi/positions/ — written only when a chain module actually sent something",
        }))
    }
}

// ── helpers ─────────────────────────────────────────────────────────────────

fn adapter_view(a: &Adapter, spec: Option<&'static dex::Chain>, terms: &Terms) -> Value {
    let module = spec.map(|c| c.module).unwrap_or("eth");
    let venue = spec.map(|c| c.venue).unwrap_or("the DEX");
    let receipt = a.receipt.as_ref().map(|r| r.symbol.clone()).unwrap_or_else(|| "the receipt".into());
    let (enter, exit, by) = match a.kind.as_str() {
        "swap_receipt" => (
            format!("buy {receipt} with {} on {venue}", a.asset.symbol),
            format!("sell {receipt} back to {} on {venue} at market — or natively: {}", a.asset.symbol, terms.exit_note),
            format!("{module} module signs the swap"),
        ),
        "erc4626" => (
            format!("approve {} → deposit(assets, you) on {}", a.asset.symbol, a.address.clone().unwrap_or_default()),
            format!("withdraw(assets, you, you) or redeem(shares, you, you) — {}", terms.exit_note),
            "eth module (eth_approve + eth_write)".into(),
        ),
        "aave_v3" => (
            format!("approve {} → Pool.supply(asset, amount, you, 0)", a.asset.symbol),
            format!("Pool.withdraw(asset, amount | all, you) — {}", terms.exit_note),
            "eth module (eth_approve + eth_write)".into(),
        ),
        "compound_v3" => (
            format!("approve {} → Comet.supply(asset, amount)", a.asset.symbol),
            format!("Comet.withdraw(asset, amount | all) — {}", terms.exit_note),
            "eth module (eth_approve + eth_write)".into(),
        ),
        other => (format!("{other}"), terms.exit_note.clone(), format!("{module} module")),
    };
    json!({
        "kind": a.kind,
        "module": module,
        "address": a.address,
        "asset": a.asset.view(),
        "receipt": a.receipt.as_ref().map(|r| r.view()),
        "exit_via": a.exit_via,
        "enter": enter,
        "exit": exit,
        "executed_by": by,
    })
}

fn token_handle(t: &Value) -> String {
    if t.get("native") == Some(&json!(true)) {
        return t.get("symbol").and_then(|v| v.as_str()).unwrap_or("").to_string();
    }
    t.get("address")
        .and_then(|v| v.as_str())
        .or_else(|| t.get("symbol").and_then(|v| v.as_str()))
        .unwrap_or("")
        .to_string()
}

fn amount_arg(body: &Value) -> Result<String, String> {
    let raw = match body.get("amount") {
        Some(Value::String(s)) => s.trim().to_string(),
        Some(Value::Number(n)) => n.to_string(),
        _ => return Err("'amount' is required — human units of the asset, as a string".into()),
    };
    let parsed: f64 = raw.parse().map_err(|_| format!("'{raw}' is not an amount"))?;
    if parsed <= 0.0 {
        return Err("an amount of nothing is not a position".into());
    }
    Ok(raw)
}

fn depth_word(tvl: f64) -> &'static str {
    if tvl >= 1e9 {
        "very deep"
    } else if tvl >= 1e8 {
        "deep"
    } else if tvl >= 1e7 {
        "adequate"
    } else if tvl >= 1e6 {
        "thin"
    } else {
        "very thin"
    }
}

fn short(id: &str) -> String {
    let s = sanitize(id);
    s[..s.len().min(10)].to_string()
}

fn sanitize(id: &str) -> String {
    id.chars().map(|c| if c.is_ascii_alphanumeric() || c == '-' || c == '_' { c } else { '_' }).collect()
}

fn round2(v: f64) -> f64 {
    if !v.is_finite() {
        return 0.0;
    }
    (v * 100.0).round() / 100.0
}

fn round4(v: f64) -> f64 {
    if !v.is_finite() {
        return 0.0;
    }
    (v * 10_000.0).round() / 10_000.0
}

pub fn erc4626_abi() -> Value {
    json!([
        { "type": "function", "name": "deposit", "stateMutability": "nonpayable", "inputs": [{ "name": "assets", "type": "uint256" }, { "name": "receiver", "type": "address" }], "outputs": [{ "type": "uint256" }] },
        { "type": "function", "name": "withdraw", "stateMutability": "nonpayable", "inputs": [{ "name": "assets", "type": "uint256" }, { "name": "receiver", "type": "address" }, { "name": "owner", "type": "address" }], "outputs": [{ "type": "uint256" }] },
        { "type": "function", "name": "redeem", "stateMutability": "nonpayable", "inputs": [{ "name": "shares", "type": "uint256" }, { "name": "receiver", "type": "address" }, { "name": "owner", "type": "address" }], "outputs": [{ "type": "uint256" }] },
        { "type": "function", "name": "previewDeposit", "stateMutability": "view", "inputs": [{ "name": "assets", "type": "uint256" }], "outputs": [{ "type": "uint256" }] },
        { "type": "function", "name": "convertToAssets", "stateMutability": "view", "inputs": [{ "name": "shares", "type": "uint256" }], "outputs": [{ "type": "uint256" }] },
        { "type": "function", "name": "totalAssets", "stateMutability": "view", "inputs": [], "outputs": [{ "type": "uint256" }] },
        { "type": "function", "name": "balanceOf", "stateMutability": "view", "inputs": [{ "name": "owner", "type": "address" }], "outputs": [{ "type": "uint256" }] }
    ])
}

pub fn mod_vault_abi() -> Value {
    json!([
        { "type": "function", "name": "deposit", "stateMutability": "nonpayable", "inputs": [{ "name": "assets", "type": "uint256" }, { "name": "receiver", "type": "address" }], "outputs": [{ "type": "uint256" }] },
        { "type": "function", "name": "withdraw", "stateMutability": "nonpayable", "inputs": [{ "name": "shares", "type": "uint256" }, { "name": "receiver", "type": "address" }], "outputs": [{ "type": "uint256" }] },
        { "type": "function", "name": "convertToShares", "stateMutability": "view", "inputs": [{ "name": "assets", "type": "uint256" }], "outputs": [{ "type": "uint256" }] },
        { "type": "function", "name": "convertToAssets", "stateMutability": "view", "inputs": [{ "name": "shares", "type": "uint256" }], "outputs": [{ "type": "uint256" }] },
        { "type": "function", "name": "totalAssets", "stateMutability": "view", "inputs": [], "outputs": [{ "type": "uint256" }] },
        { "type": "function", "name": "balanceOf", "stateMutability": "view", "inputs": [{ "name": "owner", "type": "address" }], "outputs": [{ "type": "uint256" }] }
    ])
}

pub fn aave_abi() -> Value {
    json!([
        { "type": "function", "name": "supply", "stateMutability": "nonpayable", "inputs": [{ "name": "asset", "type": "address" }, { "name": "amount", "type": "uint256" }, { "name": "onBehalfOf", "type": "address" }, { "name": "referralCode", "type": "uint16" }], "outputs": [] },
        { "type": "function", "name": "withdraw", "stateMutability": "nonpayable", "inputs": [{ "name": "asset", "type": "address" }, { "name": "amount", "type": "uint256" }, { "name": "to", "type": "address" }], "outputs": [{ "type": "uint256" }] }
    ])
}

pub fn comet_abi() -> Value {
    json!([
        { "type": "function", "name": "supply", "stateMutability": "nonpayable", "inputs": [{ "name": "asset", "type": "address" }, { "name": "amount", "type": "uint256" }], "outputs": [] },
        { "type": "function", "name": "withdraw", "stateMutability": "nonpayable", "inputs": [{ "name": "asset", "type": "address" }, { "name": "amount", "type": "uint256" }], "outputs": [] },
        { "type": "function", "name": "balanceOf", "stateMutability": "view", "inputs": [{ "name": "owner", "type": "address" }], "outputs": [{ "type": "uint256" }] }
    ])
}

#[cfg(test)]
mod tests {
    use super::*;

    fn registry() -> Registry {
        let here = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("adapters.json");
        Registry::load(&here).expect("adapters.json parses")
    }

    fn pool(chain: &str, project: &str, symbol: &str, meta: Option<&str>) -> Pool {
        Pool {
            chain: chain.into(),
            project: project.into(),
            symbol: symbol.into(),
            pool: format!("{project}-{chain}-{symbol}"),
            tvl_usd: 5e7,
            apy: Some(4.0),
            apy_base: Some(4.0),
            apy_reward: Some(0.0),
            apy_mean_30d: Some(4.0),
            apy_pct_7d: None,
            apy_pct_30d: None,
            stablecoin: true,
            il_risk: Some("no".into()),
            exposure: Some("single".into()),
            pool_meta: meta.map(|m| m.into()),
            outlier: false,
            reward_tokens: None,
            underlying_tokens: None,
            predictions: None,
        }
    }

    #[test]
    fn every_adapter_has_what_its_kind_needs() {
        for a in registry().adapters {
            match a.kind.as_str() {
                "swap_receipt" => assert!(a.receipt.as_ref().and_then(|r| r.address.clone()).is_some(), "{:?}", a.rule),
                "erc4626" | "aave_v3" | "compound_v3" => {
                    assert!(a.address.is_some(), "{:?}", a.rule);
                    assert!(a.asset.address.is_some() && a.asset.decimals.is_some(), "{:?}", a.rule);
                }
                other => panic!("unknown adapter kind {other}"),
            }
        }
    }

    #[test]
    fn aave_core_market_is_told_apart_from_its_other_instances() {
        let r = registry();
        assert!(r.adapter_for(&pool("Ethereum", "aave-v3", "USDC", None)).is_some());
        assert!(r.adapter_for(&pool("Ethereum", "aave-v3", "USDC", Some("Umbrella"))).is_none());
        assert!(r.adapter_for(&pool("Ethereum", "aave-v3", "USDC", Some(""))).is_some());
    }

    #[test]
    fn a_pool_id_rule_matches_only_that_pool() {
        let r = registry();
        let mut p = pool("Base", "morpho-blue", "STEAKUSDC", None);
        assert!(r.adapter_for(&p).is_none(), "symbol alone must not pick a Morpho vault");
        p.pool = "81ae8812-f04f-4f6e-9d71-ee5778f3a178".into();
        let a = r.adapter_for(&p).expect("pinned by pool id");
        assert_eq!(a.address.as_deref(), Some("0xbeeF010f9cb27031ad51e3333f9aF9C6B1228183"));
    }

    #[test]
    fn terms_come_from_the_category_then_the_project() {
        let r = registry();
        let lending = r.terms("Lending", "nobody");
        assert_eq!(lending.exit, "instant");
        let lido = r.terms("Liquid Staking", "lido");
        assert_eq!(lido.exit_delay_days, 5);
        assert!(lido.exit.contains("market"));
        let rwa = r.terms("RWA", "blackrock-buidl");
        assert!(rwa.gated);
    }

    #[test]
    fn a_gated_module_is_listed_but_not_addable() {
        let f = Finance::new(&std::env::temp_dir().join("defi-finance-test"), &std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("adapters.json"));
        let mut cats = HashMap::new();
        cats.insert("blackrock-buidl".to_string(), "RWA".to_string());
        let m = f.module_from_pool(&pool("Ethereum", "blackrock-buidl", "BUIDL", None), &cats);
        assert_eq!(m["addable"], json!(false));
        assert_eq!(m["gated"], json!(true));
        assert!(m["conditions"].as_array().unwrap().iter().any(|c| c["level"] == "hard"));
    }

    #[test]
    fn an_adapter_makes_a_module_addable_and_says_how() {
        let f = Finance::new(&std::env::temp_dir().join("defi-finance-test"), &std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("adapters.json"));
        let mut cats = HashMap::new();
        cats.insert("lido".to_string(), "Liquid Staking".to_string());
        let m = f.module_from_pool(&pool("Ethereum", "lido", "STETH", None), &cats);
        assert_eq!(m["addable"], json!(true));
        assert_eq!(m["adapter"]["kind"], json!("swap_receipt"));
        assert_eq!(m["adapter"]["receipt"]["symbol"], json!("wstETH"));
        assert_eq!(m["liquidity"]["exit_delay_days"], json!(5));
        assert_eq!(m["chain"], json!("ethereum"));
    }

    #[test]
    fn the_filter_reads_booleans_the_way_a_query_string_writes_them() {
        let f = Filter::from_query(&json!({ "addable": "1", "instant": "true", "stable": "0", "min_tvl": "5000000", "chain": "Base" }));
        assert!(f.addable_only && f.instant_only && !f.stable_only);
        assert_eq!(f.min_tvl, 5_000_000.0);
        assert_eq!(f.chain.as_deref(), Some("base"));
    }

    #[test]
    fn an_amount_must_be_a_positive_number_string() {
        assert!(amount_arg(&json!({ "amount": "0" })).is_err());
        assert!(amount_arg(&json!({ "amount": "abc" })).is_err());
        assert_eq!(amount_arg(&json!({ "amount": 12.5 })).unwrap(), "12.5");
        assert_eq!(amount_arg(&json!({ "amount": " 100 " })).unwrap(), "100");
    }
}
