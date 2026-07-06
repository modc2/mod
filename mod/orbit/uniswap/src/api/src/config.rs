use crate::models::chain::Chain;

/// Public archive RPC endpoints per chain (free, no API key needed).
/// Round-robin across these to distribute load.
pub fn rpc_endpoints(chain: &Chain) -> &'static [&'static str] {
    match chain {
        Chain::Base => &[
            "https://base.llamarpc.com",
            "https://base-rpc.publicnode.com",
            "https://1rpc.io/base",
            "https://base.drpc.org",
            "https://base.meowrpc.com",
        ],
        Chain::Ethereum => &[
            "https://eth.llamarpc.com",
            "https://ethereum-rpc.publicnode.com",
            "https://1rpc.io/eth",
            "https://eth.drpc.org",
            "https://eth.meowrpc.com",
            "https://rpc.ankr.com/eth",
        ],
        Chain::Arbitrum => &[
            "https://arbitrum.llamarpc.com",
            "https://arbitrum-one-rpc.publicnode.com",
            "https://1rpc.io/arb",
            "https://arbitrum.drpc.org",
            "https://arb1.arbitrum.io/rpc",
            "https://rpc.ankr.com/arbitrum",
        ],
        Chain::Polygon => &[
            "https://polygon.llamarpc.com",
            "https://polygon-bor-rpc.publicnode.com",
            "https://1rpc.io/matic",
            "https://polygon.drpc.org",
            "https://polygon-rpc.com",
            "https://rpc.ankr.com/polygon",
        ],
        Chain::Optimism => &[
            "https://optimism.llamarpc.com",
            "https://optimism-rpc.publicnode.com",
            "https://1rpc.io/op",
            "https://optimism.drpc.org",
            "https://mainnet.optimism.io",
            "https://rpc.ankr.com/optimism",
        ],
    }
}

/// Uniswap V3 Factory and Pool contracts
pub fn swap_event_topic() -> &'static str {
    // Swap(address,address,int256,int256,uint160,uint128,int24)
    "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67"
}

/// Uniswap V3 Factory address (same on all chains)
pub fn factory_address(chain: &Chain) -> &'static str {
    match chain {
        // Uniswap V3 canonical factory
        Chain::Ethereum | Chain::Arbitrum | Chain::Polygon | Chain::Optimism => {
            "0x1F98431c8aD98523631AE4a59f267346ea31F984"
        }
        // Base uses same factory
        Chain::Base => "0x33128a8fC17869897dcE68Ed026d694621f6FDfD",
    }
}

/// Top Uniswap V3 pools per chain (high volume, covers most traders)
/// eth_getLogs requires an address filter on public RPCs
pub fn top_pools(chain: &Chain) -> &'static [&'static str] {
    match chain {
        Chain::Base => &[
            "0xd0b53d9277642d899df5c87a3966a349a798f224", // WETH/USDbC 0.05%
            "0x4c36388be6f416a29c8d8eee81c771ce6be14b18", // WETH/USDC 0.05%
            "0x10648ba41b8565907cfa1496765fa4d95390aa0d", // cbETH/WETH 0.05%
            "0xb2cc224c1c9fee385f8ad6a55b4d94e92359dc59", // WETH/USDC 0.3%
            "0x6c561b446416e1a00e8e93e221854d6eA4171372", // WETH/DAI 0.3%
            "0x70aCDF2Ad0bf2402C957154f944c19Ef4e1cbAE1", // BRETT/WETH 1%
            "0xcDAC0d6c6C59727a65F871236188350531885C43", // WETH/DEGEN 1%
            "0x22fCA0A20E8393a6582E10DB832302D5E2352F03", // USDC/USDbC 0.01%
        ],
        Chain::Ethereum => &[
            "0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640", // USDC/WETH 0.05%
            "0x4e68ccd3e89f51c3074ca5072bbac773960dfa36", // WETH/USDT 0.3%
            "0xcbcdf9626bc03e24f779434178a73a0b4bad62ed", // WBTC/WETH 0.3%
            "0x8ad599c3a0ff1de082011efddc58f1908eb6e6d8", // USDC/WETH 0.3%
            "0x11b815efb8f581194ae79006d24e0d814b7697f6", // WETH/USDT 0.05%
            "0x5777d92f208679db4b9778590fa3cab3ac9e2168", // DAI/USDC 0.01%
            "0x4585fe77225b41b697c938b018e2ac67ac5a20c0", // WBTC/WETH 0.05%
            "0x109830a1aaad605bbf02a9dfa7b0b92ec2fb7daa", // wstETH/WETH 0.01%
        ],
        Chain::Arbitrum => &[
            "0xc31e54c7a869b9fcbecc14363cf510d1c41fa443", // WETH/USDC 0.05%
            "0x641c00a822e8b671738d32a431a4fb6074e5c79d", // WETH/USDT 0.05%
            "0x2f5e87c9312fa29aed5c179e456625d79015299c", // WBTC/WETH 0.05%
            "0xc6962004f452be9203591991d15f6b388e09e8d0", // WETH/USDC 0.3%
            "0x80a9ae39310abf666a87c743d6ebbd0e8c42158e", // WETH/ARB 0.05%
            "0xc473e2aee3441bf9240be85eb122abb059a3b57c", // GMX/WETH 1%
        ],
        Chain::Polygon => &[
            "0x45dda9cb7c25131df268515131f647d726f50608", // WETH/USDC 0.05%
            "0xa374094527e1673a86de625aa7475db8d0ea3e74", // WMATIC/WETH 0.3%
            "0x0e44ceb592acfc5d3f09d996302eb4c499ff8c10", // WMATIC/USDC 0.05%
            "0x3f5228d0e7d75d72a5d3e5ae44ad40b25f653400", // WBTC/WETH 0.05%
            "0xdac8a8e6dbf8c690ec6815e0ff03491b2770255d", // WMATIC/USDT 0.3%
        ],
        Chain::Optimism => &[
            "0x85149247691df622eaf1a8bd0cafd40bc45154a9", // WETH/USDC 0.05%
            "0x535541f624973895a6d3c9e6bbfcd835b6c1a730", // WETH/USDT 0.05%
            "0x85c31ffa3706d1cce9d525a00f1c7d4a2911754c", // WETH/WBTC 0.05%
            "0x0392b358ce4547601befa962680bede836606ae2", // WETH/OP 0.3%
            "0xd28f71e383e93c570d3edfe82ebbceb35ec6c412", // WETH/DAI 0.3%
        ],
    }
}

/// Block range per RPC request (archive nodes can handle large ranges)
pub const BLOCK_RANGE: u64 = 2000;

/// Concurrent requests for enrichment phase
pub const ENRICHMENT_CONCURRENCY: usize = 64;

/// Max swap pages per trader during enrichment
pub const MAX_SWAP_PAGES: usize = 5;

/// Logs per page from RPC eth_getLogs
pub const PAGE_SIZE: usize = 1000;

/// Background warmup combinations
pub const WARMUP_COMBOS: &[(Chain, u32)] = &[
    (Chain::Base, 1),
    (Chain::Base, 7),
    (Chain::Base, 14),
    (Chain::Base, 30),
    (Chain::Ethereum, 7),
    (Chain::Ethereum, 30),
    (Chain::Arbitrum, 7),
    (Chain::Polygon, 7),
    (Chain::Optimism, 7),
];

/// Approx blocks per day per chain
pub fn blocks_per_day(chain: &Chain) -> u64 {
    match chain {
        Chain::Ethereum => 7200,   // ~12s blocks
        Chain::Arbitrum => 345600, // ~0.25s blocks
        Chain::Base => 43200,      // ~2s blocks
        Chain::Polygon => 38000,   // ~2.3s blocks
        Chain::Optimism => 43200,  // ~2s blocks
    }
}
