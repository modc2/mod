use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use serde::Deserialize;

#[derive(Debug, Clone, Deserialize, serde::Serialize)]
pub struct SourceCfg {
    pub chain: String,
    #[serde(default)]
    pub wallet: String,
    #[serde(default)]
    pub decimals: u32,
    pub deposit_env: String,
    #[serde(default)]
    pub coingecko_id: String,
    /// ERC-20 / SPL mint address. Empty for native tokens.
    #[serde(default)]
    pub token_contract: String,
    /// "native", "erc20", "spl", or "" (defaults to native).
    #[serde(default)]
    pub asset_type: String,
}

#[derive(Debug, Clone, Deserialize)]
pub struct DestinationCfg {
    pub chain: String,
    #[serde(default)]
    pub wallet: String,
    #[serde(default)]
    pub address_format: String,
    #[serde(default)]
    pub decimals: u32,
    pub coingecko_id: String,
}

#[derive(Debug, Clone, Deserialize)]
pub struct RawConfig {
    #[serde(default = "default_port")]
    pub port: u16,
    #[serde(default = "default_app_port")]
    pub app_port: u16,
    #[serde(default = "default_fee_bps")]
    pub fee_bps: u32,
    #[serde(default)]
    pub sources: BTreeMap<String, SourceCfg>,
    pub destination: DestinationCfg,
}

fn default_port() -> u16 { 8870 }
fn default_app_port() -> u16 { 3870 }
fn default_fee_bps() -> u32 { 50 }

#[derive(Debug, Clone)]
pub struct Config {
    pub port: u16,
    pub app_port: u16,
    pub fee_bps: u32,
    pub sources: BTreeMap<String, SourceCfg>,
    pub destination: DestinationCfg,
    pub store_dir: PathBuf,
}

impl Config {
    /// Load from `config.json` searched up from CWD, then build runtime view.
    pub fn load() -> anyhow::Result<Self> {
        let path = find_config()?;
        let raw_text = std::fs::read_to_string(&path)?;
        let raw: RawConfig = serde_json::from_str(&raw_text)?;

        let store_dir = dirs::home_dir()
            .unwrap_or_else(|| PathBuf::from("."))
            .join(".taox");
        std::fs::create_dir_all(&store_dir)?;

        Ok(Config {
            port: raw.port,
            app_port: raw.app_port,
            fee_bps: raw.fee_bps,
            sources: raw.sources,
            destination: raw.destination,
            store_dir,
        })
    }
}

fn find_config() -> anyhow::Result<PathBuf> {
    if let Ok(explicit) = std::env::var("TAOX_CONFIG") {
        return Ok(PathBuf::from(explicit));
    }
    let mut dir: PathBuf = std::env::current_dir()?;
    for _ in 0..6 {
        let candidate = dir.join("config.json");
        if candidate.exists() {
            return Ok(candidate);
        }
        if !dir.pop() {
            break;
        }
    }
    anyhow::bail!("config.json not found (set TAOX_CONFIG or run from module dir)")
}

pub fn looks_like_evm(addr: &str) -> bool {
    let a = addr.trim();
    a.len() == 42 && a.starts_with("0x") && a[2..].chars().all(|c| c.is_ascii_hexdigit())
}

pub fn looks_like_ss58(addr: &str) -> bool {
    let a = addr.trim();
    let len = a.len();
    (45..=50).contains(&len) && a.chars().all(|c| c.is_ascii_alphanumeric())
}

pub fn looks_like_solana(addr: &str) -> bool {
    let a = addr.trim();
    let len = a.len();
    (32..=44).contains(&len) && a.chars().all(|c| c.is_ascii_alphanumeric())
}

pub fn validate_source_address(cfg: &Config, sym: &str, addr: &str) -> Option<String> {
    let Some(src) = cfg.sources.get(sym) else {
        return Some(format!("unknown source '{sym}'"));
    };
    match src.chain.as_str() {
        "ethereum" => (!looks_like_evm(addr)).then(|| "invalid EVM address".into()),
        "solana" => (!looks_like_solana(addr)).then(|| "invalid Solana address".into()),
        other => Some(format!("unsupported chain '{other}' for source '{sym}'")),
    }
}

#[allow(dead_code)]
pub fn _ensure_path(_p: &Path) {}
