use crate::types::*;

/// Public RPC endpoints (no API keys required).
/// Multiple per chain for redundancy and rotation.
pub fn default_chains() -> Vec<ChainConfig> {
    vec![
        ChainConfig {
            chain_id: 8453,
            name: "base".into(),
            enabled: true,
            rpc_urls: vec![
                "https://mainnet.base.org".into(),
                "https://base.llamarpc.com".into(),
                "https://base-rpc.publicnode.com".into(),
                "https://base.drpc.org".into(),
                "https://rpc.ankr.com/base".into(),
            ],
            routers: vec![
                RouterConfig {
                    address: "0x2626664c2603336E57B271c5C0b26F421741e481".into(),
                    name: "Uniswap V3 SwapRouter02".into(),
                    dex_type: DexType::UniswapV3,
                },
                RouterConfig {
                    address: "0x6BDED42c6DA8FBf0d2bA55B2fa120C5e0c8D7891".into(),
                    name: "SushiSwap".into(),
                    dex_type: DexType::UniswapV2,
                },
            ],
            proxy_address: None,
        },
        ChainConfig {
            chain_id: 137,
            name: "polygon".into(),
            enabled: true,
            rpc_urls: vec![
                "https://polygon-rpc.com".into(),
                "https://polygon.llamarpc.com".into(),
                "https://polygon-bor-rpc.publicnode.com".into(),
                "https://polygon.drpc.org".into(),
                "https://rpc.ankr.com/polygon".into(),
            ],
            routers: vec![
                RouterConfig {
                    address: "0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45".into(),
                    name: "Uniswap V3 SwapRouter02".into(),
                    dex_type: DexType::UniswapV3,
                },
                RouterConfig {
                    address: "0x1b02dA8Cb0d097eB8D57A175b88c7D8b47997506".into(),
                    name: "SushiSwap".into(),
                    dex_type: DexType::UniswapV2,
                },
                RouterConfig {
                    address: "0xa5E0829CaCEd8fFDD4De3c43696c57F7D7A678ff".into(),
                    name: "QuickSwap".into(),
                    dex_type: DexType::UniswapV2,
                },
            ],
            proxy_address: None,
        },
        ChainConfig {
            chain_id: 42161,
            name: "arbitrum".into(),
            enabled: true,
            rpc_urls: vec![
                "https://arb1.arbitrum.io/rpc".into(),
                "https://arbitrum.llamarpc.com".into(),
                "https://arbitrum-one-rpc.publicnode.com".into(),
                "https://arbitrum.drpc.org".into(),
                "https://rpc.ankr.com/arbitrum".into(),
            ],
            routers: vec![
                RouterConfig {
                    address: "0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45".into(),
                    name: "Uniswap V3 SwapRouter02".into(),
                    dex_type: DexType::UniswapV3,
                },
                RouterConfig {
                    address: "0x1b02dA8Cb0d097eB8D57A175b88c7D8b47997506".into(),
                    name: "SushiSwap".into(),
                    dex_type: DexType::UniswapV2,
                },
                RouterConfig {
                    address: "0xc873fEcbd354f5A56E00E710B90EF4201db2448d".into(),
                    name: "Camelot".into(),
                    dex_type: DexType::UniswapV2,
                },
            ],
            proxy_address: None,
        },
        ChainConfig {
            chain_id: 1,
            name: "ethereum".into(),
            enabled: false,
            rpc_urls: vec![
                "https://eth.llamarpc.com".into(),
                "https://ethereum-rpc.publicnode.com".into(),
                "https://eth.drpc.org".into(),
                "https://rpc.ankr.com/eth".into(),
                "https://rpc.flashbots.net".into(),
            ],
            routers: vec![
                RouterConfig {
                    address: "0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45".into(),
                    name: "Uniswap V3 SwapRouter02".into(),
                    dex_type: DexType::UniswapV3,
                },
                RouterConfig {
                    address: "0xd9e1cE17f2641f24aE83637ab66a2cca9C378B9F".into(),
                    name: "SushiSwap".into(),
                    dex_type: DexType::UniswapV2,
                },
            ],
            proxy_address: None,
        },
    ]
}

/// Uniswap V3 Swap event signature
pub const SWAP_EVENT_V3: &str = "Swap(address,address,int256,int256,uint160,uint128,int24)";
pub const SWAP_TOPIC_V3: &str = "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67";

/// Uniswap V2 Swap event signature
pub const SWAP_EVENT_V2: &str = "Swap(address,uint256,uint256,uint256,uint256,address)";
pub const SWAP_TOPIC_V2: &str = "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822";

/// Well-known stablecoin addresses per chain (for P&L calculation)
pub fn stablecoins(chain_id: u64) -> Vec<(&'static str, &'static str, u8)> {
    match chain_id {
        8453 => vec![
            ("0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", "USDC", 6),
            ("0x50c5725949A6F0c72E6C4a641F24049A917DB0Cb", "DAI", 18),
        ],
        137 => vec![
            ("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174", "USDC", 6),
            ("0xc2132D05D31c914a87C6611C10748AEb04B58e8F", "USDT", 6),
            ("0x8f3Cf7ad23Cd3CaDbD9735AFf958023239c6A063", "DAI", 18),
        ],
        42161 => vec![
            ("0xaf88d065e77c8cC2239327C5EDb3A432268e5831", "USDC", 6),
            ("0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9", "USDT", 6),
            ("0xDA10009cBd5D07dd0CeCc66161FC93D7c9000da1", "DAI", 18),
        ],
        1 => vec![
            ("0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", "USDC", 6),
            ("0xdAC17F958D2ee523a2206206994597C13D831ec7", "USDT", 6),
            ("0x6B175474E89094C44Da98b954EedeAC495271d0F", "DAI", 18),
        ],
        _ => vec![],
    }
}

/// Wrapped native token per chain
pub fn wrapped_native(chain_id: u64) -> Option<(&'static str, &'static str, u8)> {
    match chain_id {
        8453 => Some(("0x4200000000000000000000000000000000000006", "WETH", 18)),
        137 => Some(("0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270", "WMATIC", 18)),
        42161 => Some(("0x82aF49447D8a07e3bd95BD0d56f35241523fBab1", "WETH", 18)),
        1 => Some(("0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2", "WETH", 18)),
        _ => None,
    }
}
