// EVM network configs for the wallet funding panel. USDC contracts here are
// the canonical bridge/native USDC on each chain (the one most CEX withdrawals
// land in and the one wallets show by default). Polygon uses the bridged USDC.e
// because that's what Polymarket settles in.

export interface NetworkConfig {
  id: string;
  name: string;
  chainId: number;
  chainIdHex: string;
  usdc: string;            // USDC ERC-20 address on this chain (6 decimals)
  usdt: string;            // USDT ERC-20 address on this chain (6 decimals)
  rpcUrl: string;          // public RPC for read-only balance lookups
  rpcUrls: string[];       // for wallet_addEthereumChain
  nativeCurrency: { name: string; symbol: string; decimals: number };
  blockExplorerUrls: string[];
  color: string;           // brand color hex for the UI badge
  short: string;           // 3-letter ticker shown in compact pills
  glyph: string;           // unicode shape rendered in the chain badge
}

// LiFi/Jumper uses the all-zero address as the native-asset sentinel
// (ETH / MATIC / etc.). Same convention as 1inch and most aggregators.
export const NATIVE_TOKEN_ADDRESS = "0x0000000000000000000000000000000000000000";

export const NETWORKS: NetworkConfig[] = [
  {
    id: "polygon",
    name: "POLYGON",
    short: "POL",
    color: "#8247E5",
    glyph: "⬢",
    chainId: 137,
    chainIdHex: "0x89",
    usdc: "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174", // USDC.e bridged (Polymarket settlement)
    usdt: "0xc2132D05D31c914a87C6611C10748AEb04B58e8F",
    rpcUrl: "https://polygon-rpc.com",
    rpcUrls: [
      "https://polygon-rpc.com",
      "https://polygon.llamarpc.com",
      "https://polygon.drpc.org",
      "https://rpc.ankr.com/polygon",
      "https://polygon-bor-rpc.publicnode.com",
      "https://1rpc.io/matic",
    ],
    nativeCurrency: { name: "MATIC", symbol: "MATIC", decimals: 18 },
    blockExplorerUrls: ["https://polygonscan.com"],
  },
  {
    id: "base",
    name: "BASE",
    short: "BAS",
    color: "#0052FF",
    glyph: "◉",
    chainId: 8453,
    chainIdHex: "0x2105",
    usdc: "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
    usdt: "0xfde4C96c8593536E31F229EA8f37b2ADa2699bb2",
    rpcUrl: "https://mainnet.base.org",
    rpcUrls: [
      "https://mainnet.base.org",
      "https://base.llamarpc.com",
      "https://base.drpc.org",
      "https://base-rpc.publicnode.com",
      "https://1rpc.io/base",
    ],
    nativeCurrency: { name: "ETH", symbol: "ETH", decimals: 18 },
    blockExplorerUrls: ["https://basescan.org"],
  },
  {
    id: "ethereum",
    name: "ETHEREUM",
    short: "ETH",
    color: "#627EEA",
    glyph: "◆",
    chainId: 1,
    chainIdHex: "0x1",
    usdc: "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
    usdt: "0xdAC17F958D2ee523a2206206994597C13D831ec7",
    rpcUrl: "https://eth.llamarpc.com",
    rpcUrls: [
      "https://eth.llamarpc.com",
      "https://ethereum-rpc.publicnode.com",
      "https://eth.drpc.org",
      "https://rpc.ankr.com/eth",
      "https://1rpc.io/eth",
    ],
    nativeCurrency: { name: "ETH", symbol: "ETH", decimals: 18 },
    blockExplorerUrls: ["https://etherscan.io"],
  },
  {
    id: "arbitrum",
    name: "ARBITRUM",
    short: "ARB",
    color: "#28A0F0",
    glyph: "▲",
    chainId: 42161,
    chainIdHex: "0xa4b1",
    usdc: "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
    usdt: "0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9",
    rpcUrl: "https://arb1.arbitrum.io/rpc",
    rpcUrls: [
      "https://arb1.arbitrum.io/rpc",
      "https://arbitrum.llamarpc.com",
      "https://arbitrum-one-rpc.publicnode.com",
      "https://1rpc.io/arb",
    ],
    nativeCurrency: { name: "ETH", symbol: "ETH", decimals: 18 },
    blockExplorerUrls: ["https://arbiscan.io"],
  },
  {
    id: "optimism",
    name: "OPTIMISM",
    short: "OPT",
    color: "#FF0420",
    glyph: "●",
    chainId: 10,
    chainIdHex: "0xa",
    usdc: "0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85",
    usdt: "0x94b008aA00579c1307B0EF2c499aD98a8ce58e58",
    rpcUrl: "https://mainnet.optimism.io",
    rpcUrls: [
      "https://mainnet.optimism.io",
      "https://optimism.llamarpc.com",
      "https://optimism-rpc.publicnode.com",
      "https://1rpc.io/op",
    ],
    nativeCurrency: { name: "ETH", symbol: "ETH", decimals: 18 },
    blockExplorerUrls: ["https://optimistic.etherscan.io"],
  },
];

/// Try each RPC in `net.rpcUrls` until one returns successfully. Returns
/// the result of the first RPC that doesn't throw, or throws the last
/// error if every endpoint failed. Useful for reads where any healthy
/// node will do.
export async function withRpcFallback<T>(
  net: NetworkConfig,
  fn: (rpcUrl: string) => Promise<T>,
): Promise<T> {
  const urls = net.rpcUrls.length > 0 ? net.rpcUrls : [net.rpcUrl];
  let lastErr: unknown = new Error(`no RPC URLs for ${net.id}`);
  for (const url of urls) {
    try {
      return await fn(url);
    } catch (e) {
      lastErr = e;
    }
  }
  throw lastErr;
}

export function networkByChainId(chainId: number): NetworkConfig | undefined {
  return NETWORKS.find((n) => n.chainId === chainId);
}

export function networkById(id: string): NetworkConfig | undefined {
  return NETWORKS.find((n) => n.id === id);
}

interface ProviderError {
  code?: number;
  message?: string;
}

/// Switch the wallet to the target network, adding it first if unknown.
/// Throws with a user-readable error if the wallet rejects.
export async function ensureChain(
  ethereum: {
    request: (args: { method: string; params?: unknown[] }) => Promise<unknown>;
  },
  net: NetworkConfig,
): Promise<void> {
  const current = (await ethereum.request({ method: "eth_chainId" })) as string;
  if (parseInt(current, 16) === net.chainId) return;

  try {
    await ethereum.request({
      method: "wallet_switchEthereumChain",
      params: [{ chainId: net.chainIdHex }],
    });
  } catch (e: unknown) {
    const code = (e as ProviderError)?.code;
    const message = (e as ProviderError)?.message || "";
    // 4902 = chain not yet added to the wallet.
    if (code === 4902 || /unrecognized chain|not been added/i.test(message)) {
      await ethereum.request({
        method: "wallet_addEthereumChain",
        params: [{
          chainId: net.chainIdHex,
          chainName: net.name,
          nativeCurrency: net.nativeCurrency,
          rpcUrls: net.rpcUrls,
          blockExplorerUrls: net.blockExplorerUrls,
        }],
      });
      await ethereum.request({
        method: "wallet_switchEthereumChain",
        params: [{ chainId: net.chainIdHex }],
      });
      return;
    }
    if (code === 4001 || /reject|denied|cancel/i.test(message)) {
      throw new Error(`CHAIN SWITCH REJECTED — approve ${net.name} in your wallet`);
    }
    throw new Error(`CHAIN SWITCH FAILED: ${message.slice(0, 200)}`);
  }
}
