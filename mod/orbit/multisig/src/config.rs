use std::env;

#[derive(Debug, Clone)]
pub struct Config {
    pub host: String,
    pub port: u16,
    pub data_dir: String,
    pub base_rpc: String,
    pub tao_rpc: String,
    pub solana_rpc: String,
    pub base_chain_id: u64,
}

impl Config {
    pub fn load() -> anyhow::Result<Self> {
        Ok(Self {
            host: env::var("MULTISIG_HOST").unwrap_or_else(|_| "0.0.0.0".into()),
            port: env::var("MULTISIG_PORT")
                .ok()
                .and_then(|p| p.parse().ok())
                .unwrap_or(50100),
            data_dir: env::var("MULTISIG_DATA_DIR").unwrap_or_else(|_| {
                let base = env::var("HOME").unwrap_or_else(|_| ".".into());
                format!("{base}/.mod/multisig/data")
            }),
            base_rpc: env::var("BASE_RPC")
                .unwrap_or_else(|_| "https://mainnet.base.org".into()),
            tao_rpc: env::var("TAO_RPC")
                .unwrap_or_else(|_| "https://lite.chain.opentensor.ai".into()),
            solana_rpc: env::var("SOLANA_RPC")
                .unwrap_or_else(|_| "https://api.mainnet-beta.solana.com".into()),
            base_chain_id: env::var("BASE_CHAIN_ID")
                .ok()
                .and_then(|c| c.parse().ok())
                .unwrap_or(8453),
        })
    }
}
