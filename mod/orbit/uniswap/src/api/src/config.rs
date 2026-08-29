use crate::models::chain::Chain;

/// Public archive RPC endpoints per chain, round-robined to spread load.
///
/// Only endpoints that actually answer `eth_getLogs` over a multi-thousand
/// block range belong here. Most "free public RPC" lists do not: they either
/// refuse the method outright, cap the range at 10-50 blocks, or demand a
/// token for anything the node considers an archive request. An endpoint that
/// fails that way is worse than a missing one, because the pipeline reads its
/// error as an empty page and silently reports zero traders.
pub fn rpc_endpoints(chain: &Chain) -> &'static [&'static str] {
    match chain {
        Chain::Base => &[
            "https://mainnet.base.org",
            "https://base.drpc.org",
            "https://base.gateway.tenderly.co",
            "https://gateway.tenderly.co/public/base",
        ],
        Chain::Ethereum => &[
            "https://gateway.tenderly.co/public/mainnet",
            "https://eth.drpc.org",
            "https://rpc.mevblocker.io",
        ],
        Chain::Arbitrum => &[
            "https://arb1.arbitrum.io/rpc",
            "https://gateway.tenderly.co/public/arbitrum",
        ],
        Chain::Polygon => &[
            "https://polygon-bor-rpc.publicnode.com",
            "https://gateway.tenderly.co/public/polygon",
        ],
        Chain::Optimism => &[
            "https://mainnet.optimism.io",
            "https://optimism.drpc.org",
            "https://gateway.tenderly.co/public/optimism",
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

/// The tokens whose Uniswap V3 pools are worth sampling on each chain.
///
/// Pools are not listed by address anywhere in this module. They are derived
/// by asking the V3 factory for `getPool(tokenA, tokenB, fee)` over these
/// tokens and the four fee tiers, which is the only way to be sure an address
/// is a real V3 pool on the chain being queried. The previous hardcoded list
/// did not survive contact with the chain: its comments disagreed with the
/// tokens the pools actually hold, one "pool" reported a fee tier of 468 —
/// not a tier Uniswap V3 has — and two of eight addresses answered nothing
/// at all.
pub fn tokens(chain: &Chain) -> &'static [&'static str] {
    match chain {
        Chain::Ethereum => &[
            "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2", // WETH
            "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", // USDC
            "0xdAC17F958D2ee523a2206206994597C13D831ec7", // USDT
            "0x6B175474E89094C44Da98b954EedeAC495271d0F", // DAI
            "0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599", // WBTC
        ],
        Chain::Base => &[
            "0x4200000000000000000000000000000000000006", // WETH
            "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", // USDC
            "0xd9aAEc86B65D86f6A7B5B1b0c42FFA531710b6CA", // USDbC
            "0x50c5725949A6F0c72E6C4a641F24049A917DB0Cb", // DAI
            "0xcbB7C0000aB88B473b1f5aFd9ef808440eed33Bf", // cbBTC
        ],
        Chain::Arbitrum => &[
            "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1", // WETH
            "0xaf88d065e77c8cC2239327C5EDb3A432268e5831", // USDC
            "0xFF970A61A04b1cA14834A43f5dE4533eBDDB5CC8", // USDC.e
            "0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9", // USDT
            "0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f", // WBTC
            "0x912CE59144191C1204E64559FE8253a0e49E6548", // ARB
        ],
        Chain::Polygon => &[
            "0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619", // WETH
            "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359", // USDC
            "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174", // USDC.e
            "0xc2132D05D31c914a87C6611C10748AEb04B58e8F", // USDT
            "0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270", // WMATIC
            "0x1BFD67037B42Cf73acF2047067bd4F2C47D9BfD6", // WBTC
        ],
        Chain::Optimism => &[
            "0x4200000000000000000000000000000000000006", // WETH
            "0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85", // USDC
            "0x7F5c764cBc14f9669B88837ca1490cCa17c31607", // USDC.e
            "0x94b008aA00579c1307B0EF2c499aD98a8ce58e58", // USDT
            "0xDA10009cBd5D07dd0CeCc66161FC93D7c9000da1", // DAI
            "0x68f180fcCe6836688e9084f035309E29Bf0A2095", // WBTC
            "0x4200000000000000000000000000000000000042", // OP
        ],
    }
}

/// The fee tiers Uniswap V3 deploys pools at, in basis-point hundredths.
pub const FEE_TIERS: &[u32] = &[100, 500, 3000, 10_000];

/// How many discovered pools to actually sample, ranked by the balance of
/// their quote token — a direct read of which pools hold the most money.
pub const MAX_POOLS: usize = 8;

/// Block range per eth_getLogs request. Sized so one request stays under the
/// response limits public nodes enforce, per chain's block rate.
pub fn block_range(chain: &Chain) -> u64 {
    match chain {
        Chain::Ethereum => 2_000,   // ~6.7h
        Chain::Base => 2_000,       // ~66m
        Chain::Optimism => 2_000,   // ~66m
        Chain::Polygon => 2_000,    // ~76m
        Chain::Arbitrum => 20_000,  // ~83m (0.25s blocks)
    }
}

/// Stay this far behind the head block. Public endpoints are load balanced
/// across nodes at slightly different heights, so a range that ends exactly at
/// the head reported by one node is "beyond current head block" to the next.
pub const HEAD_MARGIN: u64 = 64;

/// Number of probe windows spread evenly across the requested period.
///
/// The window is far too large to read whole from public RPCs, so the scrape
/// samples it. Sampling evenly matters: walking forward contiguously from the
/// start of the window until the swap budget runs out (what this did before)
/// returns the first hour of a 30-day request and calls it a month.
pub const SAMPLE_WINDOWS: usize = 24;

/// Concurrent eth_getLogs requests during collection. Kept modest on purpose:
/// the endpoints are free public nodes, and pushing them harder trades a few
/// seconds of wall clock for holes in the sample.
pub const FETCH_CONCURRENCY: usize = 4;

/// How many times to cycle the endpoint set before giving a request up.
pub const RPC_ROUNDS: usize = 3;

/// Base backoff between retry rounds, multiplied by the round number.
pub const RPC_BACKOFF_MS: u64 = 250;

/// Concurrent eth_getCode requests when classifying traders.
pub const CODE_CONCURRENCY: usize = 8;

/// Concurrent eth_call requests during pool metadata resolution.
pub const META_CONCURRENCY: usize = 6;

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

/// Approx blocks per day per chain. Only a first guess for where the window
/// starts — the real block rate is measured against two on-chain timestamps
/// before any logs are read (see pipeline::blocks).
pub fn blocks_per_day(chain: &Chain) -> u64 {
    match chain {
        Chain::Ethereum => 7200,   // ~12s blocks
        Chain::Arbitrum => 345600, // ~0.25s blocks
        Chain::Base => 43200,      // ~2s blocks
        Chain::Polygon => 38000,   // ~2.3s blocks
        Chain::Optimism => 43200,  // ~2s blocks
    }
}

/// Symbols treated as $1.00 when pricing a swap.
pub fn is_stable(symbol: &str) -> bool {
    matches!(
        symbol.to_uppercase().as_str(),
        "USDC" | "USDT" | "DAI" | "USDBC" | "USDC.E" | "USDT.E" | "FRAX" | "LUSD" | "TUSD"
            | "BUSD" | "USDE" | "SUSD" | "MIM" | "USDS"
    )
}

/// Symbols priced off the chain's ETH reference price.
pub fn is_eth_like(symbol: &str) -> bool {
    matches!(
        symbol.to_uppercase().as_str(),
        "WETH" | "ETH"
    )
}
