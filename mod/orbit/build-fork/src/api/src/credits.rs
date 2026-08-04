//! Credit system — USD balances backed by the chain module's credit token.
//!
//! The mod protocol already has a credit system: core/chain's **Market**
//! contract. Pay it a whitelisted stablecoin and it mints you a dollar-
//! denominated credit token (8 decimals, $1.00 = 1e8) held by your own wallet.
//! This module doesn't reinvent that — it reads your Market balance for the
//! address you signed in with, and keeps only the off-chain half of the story:
//! what this module has metered against you, and any credit the owner granted
//! you directly.
//!
//!     available = on-chain Market credit + owner grants − metered spends
//!
//! Everything is denominated in USD micro-units (`usd6`, 1e-6 of a dollar) and
//! carried as decimal strings — u128 doesn't survive a JS number.
//!
//! Module-agnostic by design: the module name comes from CREDITS_MODULE /
//! MOD_NAME (default "build-fork"), the ledger lives under ~/.mod/{module}/credits/,
//! and the chain settings come from the chain module's own config.json for the
//! network this module targets (`chain_network`). A fork gets the whole system
//! by setting the env var — no code changes.
//!
//! State file (off-repo, like whitelist.json / grants.json):
//!   ~/.mod/{module}/credits/ledger.json — grants + spends, per identity

use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::sync::Mutex;

/// Serializes ledger read-modify-write cycles within this process.
static LEDGER_LOCK: Mutex<()> = Mutex::new(());

/// The Market credit token carries 8 decimals; we account in 6 (micro-dollars).
const MARKET_DECIMALS: u32 = 8;
const USD_DECIMALS: u32 = 6;

// ── Module identity + paths ──────────────────────────────────────────

/// Which module this credit system serves. Forks adapt by setting
/// CREDITS_MODULE (or MOD_NAME) — state dir, config and owner all follow.
pub fn module_name() -> String {
    std::env::var("CREDITS_MODULE")
        .or_else(|_| std::env::var("MOD_NAME"))
        .ok()
        .map(|s| s.trim().to_lowercase())
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| "build-fork".to_string())
}

fn credits_dir() -> Option<std::path::PathBuf> {
    Some(dirs::home_dir()?.join(".mod").join(module_name()).join("credits"))
}

/// Locate the module's config.json: walk up from the binary (works for any
/// fork running its own build), then the standard orbit/ and core/ locations.
fn module_config_path() -> Option<std::path::PathBuf> {
    if let Ok(exe) = std::env::current_exe() {
        let mut dir = exe.parent().map(|d| d.to_path_buf());
        for _ in 0..6 {
            match dir {
                Some(d) => {
                    let candidate = d.join("config.json");
                    if candidate.exists() {
                        return Some(candidate);
                    }
                    dir = d.parent().map(|p| p.to_path_buf());
                }
                None => break,
            }
        }
    }
    let home = dirs::home_dir()?;
    let name = module_name();
    for base in ["mod/mod/orbit", "mod/mod/core"] {
        let candidate = home.join(base).join(&name).join("config.json");
        if candidate.exists() {
            return Some(candidate);
        }
    }
    None
}

fn read_module_config() -> serde_json::Value {
    module_config_path()
        .and_then(|p| std::fs::read_to_string(p).ok())
        .and_then(|c| serde_json::from_str(&c).ok())
        .unwrap_or_else(|| serde_json::json!({}))
}

/// The chain module's config.json — it holds every deployment (rpc, chainId,
/// contract addresses) per network. Found relative to this module first (we
/// sit next to it in the fleet), then at the usual home locations.
fn chain_config_path() -> Option<std::path::PathBuf> {
    let mut roots: Vec<std::path::PathBuf> = Vec::new();
    if let Some(mine) = module_config_path().and_then(|p| p.parent().map(|d| d.to_path_buf())) {
        // .../mod/{orbit,core}/{module}/ → .../mod/
        if let Some(fleet) = mine.parent().and_then(|p| p.parent()) {
            roots.push(fleet.to_path_buf());
        }
    }
    if let Some(home) = dirs::home_dir() {
        roots.push(home.join("mod").join("mod"));
    }
    for root in roots {
        for base in ["core", "orbit"] {
            let candidate = root.join(base).join("chain").join("config.json");
            if candidate.exists() {
                return Some(candidate);
            }
        }
    }
    None
}

fn read_chain_config() -> serde_json::Value {
    chain_config_path()
        .and_then(|p| std::fs::read_to_string(p).ok())
        .and_then(|c| serde_json::from_str(&c).ok())
        .unwrap_or_else(|| serde_json::json!({}))
}

// ── Chain config ─────────────────────────────────────────────────────

/// A stablecoin the Market accepts as payment — what a top-up is paid in.
#[derive(Serialize, Clone)]
pub struct Stable {
    pub symbol: String,
    pub address: String,
    pub decimals: u32,
}

#[derive(Serialize, Clone)]
pub struct ChainCfg {
    pub enabled: bool,
    /// Which chain-module deployment we read: testnet / mainnet / ganache …
    pub network: String,
    pub rpc: String,
    pub chain_id: u64,
    /// Credits are dollars — the Market token is $1 by construction.
    pub symbol: String,
    /// Market contract (the credit token itself). Empty ⇒ not deployed here.
    pub market: String,
    /// TokenGate contract — prices the stablecoins the Market accepts.
    pub tokengate: String,
    pub stables: Vec<Stable>,
    /// Block explorer for the addresses above (best effort, may be empty).
    pub explorer: String,
}

fn contract_address(contracts: &serde_json::Value, name: &str) -> String {
    contracts
        .get(name)
        .and_then(|c| c.get("address").and_then(|v| v.as_str()).or_else(|| c.as_str()))
        .unwrap_or("")
        .to_string()
}

/// chain's config.json writes chainId as a string on some networks and a
/// number on others — take either.
fn as_u64(v: Option<&serde_json::Value>) -> Option<u64> {
    let v = v?;
    v.as_u64().or_else(|| v.as_str()?.trim().parse().ok())
}

fn explorer_for(chain_id: u64) -> &'static str {
    match chain_id {
        1 => "https://etherscan.io",
        8453 => "https://basescan.org",
        84532 => "https://sepolia.basescan.org",
        137 => "https://polygonscan.com",
        11155111 => "https://sepolia.etherscan.io",
        _ => "",
    }
}

/// Chain settings: the chain module's deployment for the network this module
/// targets (`chain_network` in our config.json), with our own `credits`
/// section and env vars layered on top.
pub fn chain_cfg() -> ChainCfg {
    let cfg = read_module_config();
    let c = cfg.get("credits").cloned().unwrap_or_else(|| serde_json::json!({}));

    let network = std::env::var("CREDITS_NETWORK")
        .ok()
        .filter(|s| !s.trim().is_empty())
        .or_else(|| c.get("network").and_then(|v| v.as_str()).map(String::from))
        .or_else(|| cfg.get("chain_network").and_then(|v| v.as_str()).map(String::from))
        .unwrap_or_else(|| "testnet".to_string());

    let chain = read_chain_config();
    let deployment = chain
        .get("deployments")
        .and_then(|d| d.get(&network))
        .cloned()
        .unwrap_or_else(|| serde_json::json!({}));
    let contracts = deployment.get("contracts").cloned().unwrap_or_else(|| serde_json::json!({}));

    let mut stables: Vec<Stable> = Vec::new();
    for symbol in ["USDC", "USDT"] {
        let address = contract_address(&contracts, symbol);
        if !address.is_empty() {
            stables.push(Stable {
                symbol: symbol.to_string(),
                address,
                // The chain module's test stables are plain 18-decimal ERC-20s;
                // the exact figure is read on-chain before any top-up is sent.
                decimals: 18,
            });
        }
    }

    let mut out = ChainCfg {
        enabled: c.get("enabled").and_then(|v| v.as_bool()).unwrap_or(true),
        rpc: deployment
            .get("url")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string(),
        chain_id: as_u64(deployment.get("chainId")).unwrap_or(0),
        symbol: "USD".to_string(),
        market: contract_address(&contracts, "Market"),
        tokengate: contract_address(&contracts, "TokenGate"),
        stables,
        explorer: String::new(),
        network,
    };

    // Overrides — config first, then env (env wins, as everywhere else).
    if let Some(rpc) = c.get("rpc").and_then(|v| v.as_str()).filter(|s| !s.is_empty()) {
        out.rpc = rpc.to_string();
    }
    if let Some(id) = as_u64(c.get("chain_id")) {
        out.chain_id = id;
    }
    if let Some(market) = c.get("market").and_then(|v| v.as_str()).filter(|s| !s.is_empty()) {
        out.market = market.to_string();
    }
    if let Ok(rpc) = std::env::var("CREDITS_RPC") {
        if !rpc.trim().is_empty() {
            out.rpc = rpc.trim().to_string();
        }
    }
    if let Some(id) = std::env::var("CREDITS_CHAIN_ID").ok().and_then(|v| v.parse().ok()) {
        out.chain_id = id;
    }
    if let Ok(market) = std::env::var("CREDITS_MARKET") {
        if !market.trim().is_empty() {
            out.market = market.trim().to_string();
        }
    }
    out.explorer = explorer_for(out.chain_id).to_string();
    out
}

/// Who the module bills for: `credits.payout` override, else the module
/// `owner`, else the runtime owner (~/.mod/{module}/owner.json).
pub fn payout_address() -> Option<String> {
    let cfg = read_module_config();
    let payout = cfg
        .get("credits")
        .and_then(|c| c.get("payout"))
        .and_then(|v| v.as_str())
        .map(|s| s.to_lowercase())
        .filter(|s| s.starts_with("0x") && s.len() == 42);
    if payout.is_some() {
        return payout;
    }
    if let Some(owner) = cfg
        .get("owner")
        .and_then(|v| v.as_str())
        .map(|s| s.to_lowercase())
        .filter(|s| s.starts_with("0x") && s.len() == 42)
    {
        return Some(owner);
    }
    // Runtime owner file (first sign-in claims ownership)
    let path = dirs::home_dir()?
        .join(".mod")
        .join(module_name())
        .join("owner.json");
    let content = std::fs::read_to_string(path).ok()?;
    let data: serde_json::Value = serde_json::from_str(&content).ok()?;
    data.get("owner")
        .and_then(|v| v.as_str())
        .map(|s| s.to_lowercase())
}

// ── USD helpers ──────────────────────────────────────────────────────

/// "12.5" / "$12.50" / "12" → micro-dollars. Rejects anything else.
pub fn parse_usd(input: &str) -> Result<u128, String> {
    let s = input.trim().trim_start_matches('$').replace(',', "");
    if s.is_empty() {
        return Err("amount required".to_string());
    }
    let (whole, frac) = match s.split_once('.') {
        Some((w, f)) => (w, f),
        None => (s.as_str(), ""),
    };
    let whole = if whole.is_empty() { "0" } else { whole };
    if !whole.chars().all(|c| c.is_ascii_digit()) || !frac.chars().all(|c| c.is_ascii_digit()) {
        return Err(format!("not a dollar amount: {}", input));
    }
    // More than six decimals is finer than the ledger resolves — truncate.
    let mut micro = String::from(&frac[..frac.len().min(USD_DECIMALS as usize)]);
    while micro.len() < USD_DECIMALS as usize {
        micro.push('0');
    }
    let whole: u128 = whole.parse().map_err(|_| "amount too large".to_string())?;
    let micro: u128 = micro.parse().map_err(|_| "amount too large".to_string())?;
    whole
        .checked_mul(10u128.pow(USD_DECIMALS))
        .and_then(|w| w.checked_add(micro))
        .ok_or_else(|| "amount too large".to_string())
}

/// Micro-dollars → a plain decimal string ("12.50", "0.004200").
pub fn fmt_usd(usd6: u128) -> String {
    let unit = 10u128.pow(USD_DECIMALS);
    let whole = usd6 / unit;
    let frac = usd6 % unit;
    if frac == 0 {
        return format!("{}.00", whole);
    }
    // Trim trailing zeros but always keep cents.
    let mut s = format!("{:06}", frac);
    while s.len() > 2 && s.ends_with('0') {
        s.pop();
    }
    format!("{}.{}", whole, s)
}

// ── Ledger ───────────────────────────────────────────────────────────

/// Credit the owner handed out directly (promo, refund, off-chain payment).
#[derive(Serialize, Deserialize, Clone)]
pub struct GrantEntry {
    /// Micro-dollars, decimal string.
    pub usd6: String,
    pub reason: String,
    pub ts: i64,
}

#[derive(Serialize, Deserialize, Clone)]
pub struct SpendEntry {
    pub usd6: String,
    pub reason: String,
    pub ts: i64,
}

#[derive(Serialize, Deserialize, Clone, Default)]
pub struct Account {
    #[serde(default)]
    pub grants: Vec<GrantEntry>,
    #[serde(default)]
    pub spends: Vec<SpendEntry>,
}

impl Account {
    pub fn granted_usd6(&self) -> u128 {
        self.grants.iter().filter_map(|g| g.usd6.parse::<u128>().ok()).sum()
    }
    pub fn spent_usd6(&self) -> u128 {
        self.spends.iter().filter_map(|s| s.usd6.parse::<u128>().ok()).sum()
    }
    /// The off-chain half only — on-chain Market credit is added by `view()`.
    pub fn offchain_usd6(&self) -> i128 {
        self.granted_usd6() as i128 - self.spent_usd6() as i128
    }
}

#[derive(Serialize, Deserialize, Default)]
pub struct Ledger {
    #[serde(default)]
    pub accounts: HashMap<String, Account>,
}

fn ledger_path() -> Option<std::path::PathBuf> {
    Some(credits_dir()?.join("ledger.json"))
}

pub fn read_ledger() -> Ledger {
    ledger_path()
        .and_then(|p| std::fs::read_to_string(p).ok())
        .and_then(|c| serde_json::from_str(&c).ok())
        .unwrap_or_default()
}

fn write_ledger(ledger: &Ledger) -> Result<(), String> {
    let path = ledger_path().ok_or("no home dir")?;
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|e| format!("mkdir: {}", e))?;
    }
    let json = serde_json::to_string_pretty(ledger).map_err(|e| format!("encode: {}", e))?;
    std::fs::write(&path, json).map_err(|e| format!("write: {}", e))
}

/// Ensure an account row exists for `identity`. Returns it.
pub fn ensure_account(identity: &str) -> Result<Account, String> {
    let ident = identity.trim().to_lowercase();
    let _guard = LEDGER_LOCK.lock().unwrap();
    let mut ledger = read_ledger();
    let account = ledger.accounts.entry(ident).or_default().clone();
    write_ledger(&ledger)?;
    Ok(account)
}

/// Owner-issued credit — the off-chain path in, for anyone who paid the owner
/// some other way (or for a promo). On-chain top-ups need no server help.
pub fn grant(identity: &str, usd6: u128, reason: &str) -> Result<Account, String> {
    let ident = identity.trim().to_lowercase();
    let _guard = LEDGER_LOCK.lock().unwrap();
    let mut ledger = read_ledger();
    let account = ledger.accounts.entry(ident.clone()).or_default();
    account.grants.push(GrantEntry {
        usd6: usd6.to_string(),
        reason: reason.to_string(),
        ts: chrono::Utc::now().timestamp(),
    });
    let out = account.clone();
    write_ledger(&ledger)?;
    println!("✓ credits: granted ${} to {} ({})", fmt_usd(usd6), ident, reason);
    Ok(out)
}

/// Meter usage against an account. Charges the grant balance first and only
/// then the on-chain credit, so a grant is spent before someone's own money.
/// Errors (and records nothing) if the two together don't cover it.
pub async fn debit(identity: &str, usd6: u128, reason: &str) -> Result<Account, String> {
    let ident = identity.trim().to_lowercase();
    let available = available_usd6(&ident).await?;
    if available < usd6 as i128 {
        return Err(format!(
            "insufficient credits: balance ${}, charge ${}",
            fmt_usd(available.max(0) as u128),
            fmt_usd(usd6)
        ));
    }
    let _guard = LEDGER_LOCK.lock().unwrap();
    let mut ledger = read_ledger();
    let account = ledger.accounts.entry(ident.clone()).or_default();
    account.spends.push(SpendEntry {
        usd6: usd6.to_string(),
        reason: reason.to_string(),
        ts: chrono::Utc::now().timestamp(),
    });
    let out = account.clone();
    write_ledger(&ledger)?;
    Ok(out)
}

// ── On-chain read (the chain module's Market credit token) ───────────

async fn rpc_call(
    rpc: &str,
    method: &str,
    params: serde_json::Value,
) -> Result<serde_json::Value, String> {
    let client = reqwest::Client::new();
    let body = serde_json::json!({ "jsonrpc": "2.0", "id": 1, "method": method, "params": params });
    let resp = client
        .post(rpc)
        .json(&body)
        .timeout(std::time::Duration::from_secs(15))
        .send()
        .await
        .map_err(|e| format!("rpc {}: {}", method, e))?;
    let data: serde_json::Value = resp
        .json()
        .await
        .map_err(|e| format!("rpc {} decode: {}", method, e))?;
    if let Some(err) = data.get("error") {
        return Err(format!("rpc {}: {}", method, err));
    }
    data.get("result")
        .cloned()
        .ok_or_else(|| format!("rpc {}: empty result", method))
}

fn parse_hex_u128(v: &serde_json::Value) -> Result<u128, String> {
    let s = v.as_str().ok_or("expected hex string")?.trim_start_matches("0x");
    // eth_call pads to 32 bytes; a credit balance never needs more than 16.
    let s = if s.len() > 32 { &s[s.len() - 32..] } else { s };
    if s.is_empty() {
        return Ok(0);
    }
    u128::from_str_radix(s, 16).map_err(|e| format!("hex: {}", e))
}

/// Only real EVM addresses have an on-chain balance — "local" sessions and
/// short QR key ids don't.
fn is_address(identity: &str) -> bool {
    let s = identity.trim();
    s.len() == 42 && s.starts_with("0x") && s[2..].chars().all(|c| c.is_ascii_hexdigit())
}

/// `balanceOf(address)` on the Market credit token, in micro-dollars.
/// A chain that can't be reached is an error; an identity with no on-chain
/// presence (local session, QR key) is simply zero.
pub async fn market_usd6(identity: &str) -> Result<u128, String> {
    let cfg = chain_cfg();
    if !cfg.enabled || cfg.market.is_empty() || cfg.rpc.is_empty() || !is_address(identity) {
        return Ok(0);
    }
    let data = format!(
        "0x70a08231000000000000000000000000{}",
        identity.trim().trim_start_matches("0x").to_lowercase()
    );
    let result = rpc_call(
        &cfg.rpc,
        "eth_call",
        serde_json::json!([{ "to": cfg.market, "data": data }, "latest"]),
    )
    .await?;
    let raw = parse_hex_u128(&result)?;
    // Market is 8-decimal, the ledger is 6-decimal.
    Ok(raw / 10u128.pow(MARKET_DECIMALS - USD_DECIMALS))
}

/// Spendable balance: on-chain credit + grants − spends. Signed, because a
/// grant that was overspent before a chain hiccup shouldn't wrap around.
pub async fn available_usd6(identity: &str) -> Result<i128, String> {
    let onchain = market_usd6(identity).await.unwrap_or(0) as i128;
    let account = read_ledger()
        .accounts
        .get(&identity.trim().to_lowercase())
        .cloned()
        .unwrap_or_default();
    Ok(onchain + account.offchain_usd6())
}

/// Everything the console shows for one identity, in dollars.
#[derive(Serialize)]
pub struct AccountView {
    pub identity: String,
    /// Spendable, as a decimal dollar string.
    pub usd: String,
    pub usd6: String,
    /// Where it came from / went.
    pub onchain_usd: String,
    pub granted_usd: String,
    pub spent_usd: String,
    /// True when the on-chain read failed — the number below is grants only.
    pub chain_error: Option<String>,
    pub grants: Vec<GrantEntry>,
    pub spends: Vec<SpendEntry>,
}

pub async fn account_view(identity: &str) -> AccountView {
    let ident = identity.trim().to_lowercase();
    let account = read_ledger().accounts.get(&ident).cloned().unwrap_or_default();
    let (onchain, chain_error) = match market_usd6(&ident).await {
        Ok(v) => (v, None),
        Err(e) => (0, Some(e)),
    };
    let total = (onchain as i128 + account.offchain_usd6()).max(0) as u128;
    AccountView {
        identity: ident,
        usd: fmt_usd(total),
        usd6: total.to_string(),
        onchain_usd: fmt_usd(onchain),
        granted_usd: fmt_usd(account.granted_usd6()),
        spent_usd: fmt_usd(account.spent_usd6()),
        chain_error,
        grants: account.grants,
        spends: account.spends,
    }
}

// ── Tests ────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_usd_forms() {
        assert_eq!(parse_usd("1").unwrap(), 1_000_000);
        assert_eq!(parse_usd("$12.50").unwrap(), 12_500_000);
        assert_eq!(parse_usd(" 0.004200 ").unwrap(), 4_200);
        assert_eq!(parse_usd("1,000").unwrap(), 1_000_000_000);
        // Finer than the ledger resolves — truncated, not rounded up.
        assert_eq!(parse_usd("0.0000009").unwrap(), 0);
        assert!(parse_usd("abc").is_err());
        assert!(parse_usd("").is_err());
        assert!(parse_usd("-1").is_err());
    }

    #[test]
    fn test_fmt_usd_keeps_cents() {
        assert_eq!(fmt_usd(0), "0.00");
        assert_eq!(fmt_usd(1_000_000), "1.00");
        assert_eq!(fmt_usd(12_500_000), "12.50");
        assert_eq!(fmt_usd(4_200), "0.0042");
        assert_eq!(fmt_usd(1), "0.000001");
    }

    #[test]
    fn test_ledger_math_is_grants_minus_spends() {
        let mut account = Account::default();
        account.grants.push(GrantEntry { usd6: "1000000".into(), reason: "promo".into(), ts: 0 });
        account.grants.push(GrantEntry { usd6: "500000".into(), reason: "promo".into(), ts: 0 });
        account.spends.push(SpendEntry { usd6: "300000".into(), reason: "job".into(), ts: 0 });
        assert_eq!(account.granted_usd6(), 1_500_000);
        assert_eq!(account.spent_usd6(), 300_000);
        assert_eq!(account.offchain_usd6(), 1_200_000);
    }

    #[test]
    fn test_market_balance_decoding() {
        // 8-decimal Market amount ($99,000.99) → micro-dollars.
        let raw = serde_json::json!("0x000000000000000000000000000000000000000000000000000009010be256c0");
        assert_eq!(parse_hex_u128(&raw).unwrap() / 100, 99_000_990_000);
        assert_eq!(parse_hex_u128(&serde_json::json!("0x0")).unwrap(), 0);
    }

    #[test]
    fn test_only_real_addresses_read_the_chain() {
        assert!(is_address("0xd779eb61ced815570f74ab15a52ee8378a66996f"));
        assert!(!is_address("local"));
        assert!(!is_address("0x89bc"));
        assert!(!is_address("0xzzzzeb61ced815570f74ab15a52ee8378a66996f"));
    }

    #[tokio::test]
    async fn test_non_address_identities_have_no_onchain_credit() {
        assert_eq!(market_usd6("local").await.unwrap(), 0);
    }

    #[test]
    fn test_chain_cfg_reads_the_chain_module_deployment() {
        // Whatever network we target, the settings must come from the chain
        // module (or be explicitly disabled) — never invented here.
        let cfg = chain_cfg();
        assert_eq!(cfg.symbol, "USD");
        if !cfg.market.is_empty() {
            assert!(cfg.market.starts_with("0x"), "market address from chain config");
            assert!(cfg.chain_id > 0, "chain id from chain config");
            assert!(!cfg.rpc.is_empty(), "rpc from chain config");
        }
    }
}

