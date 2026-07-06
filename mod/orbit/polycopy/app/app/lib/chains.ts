export interface ChainConfig {
  name: string;
  shortName: string;
  color: string;
  blockTimeMs: number;
  explorer: string;
}

export const CHAINS: Record<number, ChainConfig> = {
  137: {
    name: "Polygon",
    shortName: "MATIC",
    color: "#8247e5",
    blockTimeMs: 2000,
    explorer: "https://polygonscan.com",
  },
  8453: {
    name: "Base",
    shortName: "BASE",
    color: "#0052ff",
    blockTimeMs: 2000,
    explorer: "https://basescan.org",
  },
  42161: {
    name: "Arbitrum",
    shortName: "ARB",
    color: "#28a0f0",
    blockTimeMs: 260,
    explorer: "https://arbiscan.io",
  },
  1: {
    name: "Ethereum",
    shortName: "ETH",
    color: "#627eea",
    blockTimeMs: 12000,
    explorer: "https://etherscan.io",
  },
};

export const SUPPORTED_CHAIN_IDS = [137, 8453, 42161, 1];

// Well-known token addresses per chain (lowercase)
export const TOKEN_SYMBOLS: Record<number, Record<string, string>> = {
  137: {
    "0x0d500b1d8e8ef31e21c99d1db9a6444d3adf1270": "WMATIC",
    "0x7ceb23fd6bc0add59e62ac25578270cff1b9f619": "WETH",
    "0x2791bca1f2de4661ed88a30c99a7a9449aa84174": "USDC",
    "0x3c499c542cef5e3811e1192ce70d8cc03d5c3359": "USDC",
    "0xc2132d05d31c914a87c6611c10748aeb04b58e8f": "USDT",
    "0x8f3cf7ad23cd3cadbd9735aff958023239c6a063": "DAI",
    "0x1bfd67037b42cf73acf2047067bd4f2c47d9bfd6": "WBTC",
    "0x53e0bca35ec356bd5dddfebbd1fc0fd03fabad39": "LINK",
    "0xb33eaad8d922b1083446dc23f610c2567fb5180f": "UNI",
  },
  8453: {
    "0x4200000000000000000000000000000000000006": "WETH",
    "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913": "USDC",
    "0x50c5725949a6f0c72e6c4a641f24049a917db0cb": "DAI",
    "0xd9aaec86b65d86f6a7b5b1b0c42ffa531710b6ca": "USDbC",
    "0x2ae3f1ec7f1f5012cfeab0185bfc7aa3cf0dec22": "cbETH",
    "0xc1cba3fcea344f92d9239c08c0568f6f2f0ee452": "wstETH",
  },
  42161: {
    "0x82af49447d8a07e3bd95bd0d56f35241523fbab1": "WETH",
    "0xaf88d065e77c8cc2239327c5edb3a432268e5831": "USDC",
    "0xff970a61a04b1ca14834a43f5de4533ebddb5cc8": "USDC.e",
    "0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9": "USDT",
    "0xda10009cbd5d07dd0cecc66161fc93d7c9000da1": "DAI",
    "0x2f2a2543b76a4166549f7aab2e75bef0aefc5b0f": "WBTC",
    "0x912ce59144191c1204e64559fe8253a0e49e6548": "ARB",
    "0x5979d7b546e38e9ab8ef7c5db2b4ff3285abb0f7": "wstETH",
  },
  1: {
    "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2": "WETH",
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": "USDC",
    "0xdac17f958d2ee523a2206206994597c13d831ec7": "USDT",
    "0x6b175474e89094c44da98b954eedeac495271d0f": "DAI",
    "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599": "WBTC",
    "0x514910771af9ca656af840dff83e8264ecf986ca": "LINK",
    "0x1f9840a85d5af5bf1d1762f925bdaddc4201f984": "UNI",
  },
};

// Token decimals for known symbols
export const TOKEN_DECIMALS: Record<string, number> = {
  USDC: 6,
  "USDC.e": 6,
  USDbC: 6,
  USDT: 6,
  DAI: 18,
  WETH: 18,
  WMATIC: 18,
  WBTC: 8,
  LINK: 18,
  UNI: 18,
  ARB: 18,
  cbETH: 18,
  wstETH: 18,
};

export const STABLECOIN_SYMBOLS = new Set(["USDC", "USDC.e", "USDbC", "USDT", "DAI", "FRAX", "BUSD"]);
