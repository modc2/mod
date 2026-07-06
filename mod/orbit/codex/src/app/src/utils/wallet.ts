import { ethers } from "ethers";

export interface WalletHistory {
  transactions: Transaction[];
  lastFetched: number;
  address: string;
}

export interface Transaction {
  hash: string;
  from: string;
  to: string | null;
  value: string;
  timestamp: number;
  blockNumber: number;
  status: "success" | "failed" | "pending";
  gasUsed?: string;
  gasPrice?: string;
  type?: "send" | "receive" | "contract";
}

const CACHE_DURATION = 5 * 60 * 1000; // 5 minutes
const STORAGE_KEY = "claude_wallet_history";

/**
 * Get cached wallet history from localStorage
 */
export function getCachedHistory(address: string): WalletHistory | null {
  try {
    const cached = localStorage.getItem(`${STORAGE_KEY}_${address.toLowerCase()}`);
    if (!cached) return null;

    const data: WalletHistory = JSON.parse(cached);

    // Check if cache is still valid
    if (Date.now() - data.lastFetched > CACHE_DURATION) {
      return null;
    }

    return data;
  } catch (e) {
    console.error("Failed to get cached history:", e);
    return null;
  }
}

/**
 * Save wallet history to localStorage
 */
export function cacheHistory(address: string, transactions: Transaction[]): void {
  try {
    const data: WalletHistory = {
      transactions,
      lastFetched: Date.now(),
      address: address.toLowerCase(),
    };
    localStorage.setItem(`${STORAGE_KEY}_${address.toLowerCase()}`, JSON.stringify(data));
  } catch (e) {
    console.error("Failed to cache history:", e);
  }
}

/**
 * Clear cached history for an address
 */
export function clearCachedHistory(address: string): void {
  try {
    localStorage.removeItem(`${STORAGE_KEY}_${address.toLowerCase()}`);
  } catch (e) {
    console.error("Failed to clear cache:", e);
  }
}

/**
 * Format address to short version (0x1234...5678)
 */
export function formatAddress(address: string, startChars = 6, endChars = 4): string {
  if (!address) return "";
  return `${address.slice(0, startChars)}...${address.slice(-endChars)}`;
}

/**
 * Format ETH value with proper decimals
 */
export function formatEth(value: string | bigint, decimals = 4): string {
  try {
    const formatted = typeof value === "string" ? value : ethers.formatEther(value);
    return parseFloat(formatted).toFixed(decimals);
  } catch (e) {
    return "0.0000";
  }
}

/**
 * Get explorer URL for transaction
 */
export function getExplorerUrl(chainId: number, hash: string, type: "tx" | "address" = "tx"): string {
  const explorers: Record<number, string> = {
    1: "https://etherscan.io",
    5: "https://goerli.etherscan.io",
    11155111: "https://sepolia.etherscan.io",
    8453: "https://basescan.org",
    84532: "https://sepolia.basescan.org",
    137: "https://polygonscan.com",
    80001: "https://mumbai.polygonscan.com",
  };

  const baseUrl = explorers[chainId] || "https://etherscan.io";
  return `${baseUrl}/${type}/${hash}`;
}

/**
 * EVM Network definitions with chain params for wallet_addEthereumChain
 */
export interface EVMNetwork {
  chainId: number;
  name: string;
  symbol: string;
  rpcUrl: string;
  explorer: string;
  testnet: boolean;
}

export const EVM_NETWORKS: EVMNetwork[] = [
  { chainId: 1,        name: "Ethereum",         symbol: "ETH",   rpcUrl: "https://eth.llamarpc.com",              explorer: "https://etherscan.io",           testnet: false },
  { chainId: 8453,     name: "Base",              symbol: "ETH",   rpcUrl: "https://mainnet.base.org",              explorer: "https://basescan.org",           testnet: false },
  { chainId: 137,      name: "Polygon",           symbol: "MATIC", rpcUrl: "https://polygon-rpc.com",               explorer: "https://polygonscan.com",        testnet: false },
  { chainId: 42161,    name: "Arbitrum",           symbol: "ETH",   rpcUrl: "https://arb1.arbitrum.io/rpc",          explorer: "https://arbiscan.io",            testnet: false },
  { chainId: 10,       name: "Optimism",          symbol: "ETH",   rpcUrl: "https://mainnet.optimism.io",           explorer: "https://optimistic.etherscan.io", testnet: false },
  { chainId: 56,       name: "BNB Chain",         symbol: "BNB",   rpcUrl: "https://bsc-dataseed.binance.org",      explorer: "https://bscscan.com",            testnet: false },
  { chainId: 43114,    name: "Avalanche",         symbol: "AVAX",  rpcUrl: "https://api.avax.network/ext/bc/C/rpc", explorer: "https://snowtrace.io",           testnet: false },
  { chainId: 11155111, name: "Sepolia",           symbol: "ETH",   rpcUrl: "https://rpc.sepolia.org",               explorer: "https://sepolia.etherscan.io",   testnet: true },
  { chainId: 84532,    name: "Base Sepolia",      symbol: "ETH",   rpcUrl: "https://sepolia.base.org",              explorer: "https://sepolia.basescan.org",   testnet: true },
  { chainId: 421614,   name: "Arbitrum Sepolia",  symbol: "ETH",   rpcUrl: "https://sepolia-rollup.arbitrum.io/rpc", explorer: "https://sepolia.arbiscan.io",   testnet: true },
  { chainId: 80001,    name: "Mumbai",            symbol: "MATIC", rpcUrl: "https://rpc-mumbai.maticvigil.com",     explorer: "https://mumbai.polygonscan.com", testnet: true },
];

/**
 * Network logo SVG paths (viewBox 0 0 24 24)
 */
export const NETWORK_LOGOS: Record<number, { svg: string; color: string }> = {
  1:        { color: "#627EEA", svg: `<path d="M12 2L5 12l7 4 7-4L12 2z" fill="currentColor" opacity="0.6"/><path d="M12 2l7 10-7 4V2z" fill="currentColor"/><path d="M12 17l-7-4 7 9 7-9-7 4z" fill="currentColor" opacity="0.6"/><path d="M12 17l7-4-7 9V17z" fill="currentColor"/>` },
  8453:     { color: "#0052FF", svg: `<circle cx="12" cy="12" r="10" fill="currentColor"/><path d="M12 6a6 6 0 100 12 6 6 0 000-12z" fill="#0A0B0D"/><path d="M10.5 9h3a1 1 0 011 1v4a1 1 0 01-1 1h-3a1 1 0 01-1-1v-4a1 1 0 011-1z" fill="currentColor"/>` },
  137:      { color: "#8247E5", svg: `<path d="M16.3 10.2l-3.5-2a1.5 1.5 0 00-1.6 0l-3.5 2a1.6 1.6 0 00-.7 1.3v4.1c0 .5.3 1 .7 1.3l3.5 2a1.5 1.5 0 001.6 0l3.5-2c.4-.3.7-.8.7-1.3v-4.1c0-.5-.3-1-.7-1.3z" fill="currentColor"/>` },
  42161:    { color: "#28A0F0", svg: `<path d="M12 2l8 5v10l-8 5-8-5V7l8-5z" fill="currentColor" opacity="0.15" stroke="currentColor" stroke-width="1"/><path d="M10 9l2 6 2-6" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>` },
  10:       { color: "#FF0420", svg: `<circle cx="12" cy="12" r="10" fill="currentColor"/><circle cx="12" cy="12" r="5" fill="#0A0B0D"/>` },
  56:       { color: "#F0B90B", svg: `<path d="M12 2l2.5 2.5L12 7 9.5 4.5 12 2zm-5.5 5.5L9 5l2.5 2.5L9 10 6.5 7.5zM3 12l2.5-2.5L8 12l-2.5 2.5L3 12zm3.5 4.5L9 14l2.5 2.5L9 19l-2.5-2.5zM12 17l2.5-2.5L17 17l-2.5 2.5L12 17zm5.5-4.5L15 15l-2.5-2.5L15 10l2.5 2.5zM21 12l-2.5 2.5L16 12l2.5-2.5L21 12zm-3.5-4.5L15 10l-2.5-2.5L15 5l2.5 2.5z" fill="currentColor"/>` },
  43114:    { color: "#E84142", svg: `<path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2z" fill="currentColor"/><path d="M15.5 15.5H8.5l3.5-7 3.5 7z" fill="#fff"/>` },
  11155111: { color: "#627EEA", svg: `<path d="M12 2L5 12l7 4 7-4L12 2z" fill="currentColor" opacity="0.6"/><path d="M12 2l7 10-7 4V2z" fill="currentColor"/><path d="M12 17l-7-4 7 9 7-9-7 4z" fill="currentColor" opacity="0.6"/><path d="M12 17l7-4-7 9V17z" fill="currentColor"/>` },
  84532:    { color: "#0052FF", svg: `<circle cx="12" cy="12" r="10" fill="currentColor"/><path d="M12 6a6 6 0 100 12 6 6 0 000-12z" fill="#0A0B0D"/><path d="M10.5 9h3a1 1 0 011 1v4a1 1 0 01-1 1h-3a1 1 0 01-1-1v-4a1 1 0 011-1z" fill="currentColor"/>` },
  421614:   { color: "#28A0F0", svg: `<path d="M12 2l8 5v10l-8 5-8-5V7l8-5z" fill="currentColor" opacity="0.15" stroke="currentColor" stroke-width="1"/><path d="M10 9l2 6 2-6" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>` },
  80001:    { color: "#8247E5", svg: `<path d="M16.3 10.2l-3.5-2a1.5 1.5 0 00-1.6 0l-3.5 2a1.6 1.6 0 00-.7 1.3v4.1c0 .5.3 1 .7 1.3l3.5 2a1.5 1.5 0 001.6 0l3.5-2c.4-.3.7-.8.7-1.3v-4.1c0-.5-.3-1-.7-1.3z" fill="currentColor"/>` },
};

/**
 * Get network name from chain ID
 */
export function getNetworkName(chainId: number): string {
  const net = EVM_NETWORKS.find(n => n.chainId === chainId);
  return net ? net.name : `Unknown (${chainId})`;
}

/**
 * Get native token symbol for a chain
 */
export function getNativeSymbol(chainId: number): string {
  const net = EVM_NETWORKS.find(n => n.chainId === chainId);
  return net ? net.symbol : "ETH";
}

/**
 * Store the selected chain ID locally (for non-MetaMask wallets)
 */
export function getStoredChainId(): number {
  try {
    const stored = localStorage.getItem("claude_jobs_chain_id");
    return stored ? parseInt(stored, 10) : 84532; // default Base Sepolia
  } catch {
    return 84532;
  }
}

export function setStoredChainId(chainId: number): void {
  localStorage.setItem("claude_jobs_chain_id", chainId.toString());
}

/**
 * Switch the wallet to a different EVM network.
 * For MetaMask: uses wallet_switchEthereumChain.
 * For other wallet types: stores the chain selection locally.
 */
export async function switchNetwork(chainId: number): Promise<boolean> {
  const ethereum = (window as any).ethereum;

  // No browser wallet — store chain selection locally
  if (!ethereum) {
    const net = EVM_NETWORKS.find(n => n.chainId === chainId);
    if (!net) return false;
    setStoredChainId(chainId);
    return true;
  }

  const hexChainId = "0x" + chainId.toString(16);

  try {
    await ethereum.request({
      method: "wallet_switchEthereumChain",
      params: [{ chainId: hexChainId }],
    });
    setStoredChainId(chainId);
    return true;
  } catch (switchError: any) {
    // 4902 = chain not added yet
    if (switchError.code === 4902) {
      const net = EVM_NETWORKS.find(n => n.chainId === chainId);
      if (!net) return false;
      try {
        await ethereum.request({
          method: "wallet_addEthereumChain",
          params: [{
            chainId: hexChainId,
            chainName: net.name,
            nativeCurrency: { name: net.symbol, symbol: net.symbol, decimals: 18 },
            rpcUrls: [net.rpcUrl],
            blockExplorerUrls: [net.explorer],
          }],
        });
        setStoredChainId(chainId);
        return true;
      } catch {
        return false;
      }
    }
    return false;
  }
}

/**
 * Get an ethers provider for a given chain ID.
 * Uses BrowserProvider if available, otherwise falls back to JsonRpcProvider.
 */
export function getProvider(chainId?: number): ethers.BrowserProvider | ethers.JsonRpcProvider {
  const ethereum = (window as any).ethereum;
  if (ethereum) {
    return new ethers.BrowserProvider(ethereum);
  }
  const cid = chainId ?? getStoredChainId();
  const net = EVM_NETWORKS.find(n => n.chainId === cid);
  const rpcUrl = net?.rpcUrl || "https://sepolia.base.org";
  return new ethers.JsonRpcProvider(rpcUrl);
}

/**
 * Get gas cost in ETH
 */
export function calculateGasCost(gasUsed?: string, gasPrice?: string): string {
  if (!gasUsed || !gasPrice) return "0";

  try {
    const cost = BigInt(gasUsed) * BigInt(gasPrice);
    return ethers.formatEther(cost);
  } catch (e) {
    return "0";
  }
}

/**
 * Check if address is valid Ethereum address
 */
export function isValidAddress(address: string): boolean {
  try {
    return ethers.isAddress(address);
  } catch (e) {
    return false;
  }
}

/**
 * Get time ago string
 */
export function getTimeAgo(timestamp: number): string {
  const seconds = Math.floor(Date.now() / 1000 - timestamp);

  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  if (seconds < 2592000) return `${Math.floor(seconds / 86400)}d ago`;
  return new Date(timestamp * 1000).toLocaleDateString();
}

/**
 * Export transactions to CSV
 */
export function exportToCSV(transactions: Transaction[], address: string): void {
  const headers = ["Hash", "From", "To", "Value (ETH)", "Type", "Status", "Block", "Timestamp", "Gas Used", "Gas Price"];

  const rows = transactions.map(tx => [
    tx.hash,
    tx.from,
    tx.to || "",
    tx.value,
    tx.type || "unknown",
    tx.status,
    tx.blockNumber.toString(),
    new Date(tx.timestamp * 1000).toISOString(),
    tx.gasUsed || "",
    tx.gasPrice || "",
  ]);

  const csv = [
    headers.join(","),
    ...rows.map(row => row.map(cell => `"${cell}"`).join(","))
  ].join("\n");

  const blob = new Blob([csv], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `transactions-${formatAddress(address)}-${Date.now()}.csv`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

/**
 * Export transactions to JSON
 */
export function exportToJSON(transactions: Transaction[], address: string): void {
  const data = {
    address,
    exportedAt: new Date().toISOString(),
    transactionCount: transactions.length,
    transactions,
  };

  const json = JSON.stringify(data, null, 2);
  const blob = new Blob([json], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `transactions-${formatAddress(address)}-${Date.now()}.json`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

/**
 * Copy text to clipboard
 */
export async function copyToClipboard(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch (e) {
    console.error("Failed to copy to clipboard:", e);
    return false;
  }
}

/**
 * Get token balance with proper decimals
 */
export async function getTokenBalance(
  provider: ethers.BrowserProvider,
  tokenAddress: string,
  walletAddress: string,
  decimals: number = 18
): Promise<string> {
  try {
    const erc20Abi = ["function balanceOf(address) view returns (uint256)"];
    const contract = new ethers.Contract(tokenAddress, erc20Abi, provider);
    const balance = await contract.balanceOf(walletAddress);
    return ethers.formatUnits(balance, decimals);
  } catch (e) {
    console.error("Failed to get token balance:", e);
    return "0";
  }
}

/**
 * Common token addresses by chain
 */
export interface TokenInfo {
  address: string;
  symbol: string;
  decimals: number;
  name: string;
}

export const COMMON_TOKENS: Record<number, TokenInfo[]> = {
  1: [
    { address: "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", symbol: "USDC", decimals: 6, name: "USD Coin" },
    { address: "0xdAC17F958D2ee523a2206206994597C13D831ec7", symbol: "USDT", decimals: 6, name: "Tether USD" },
    { address: "0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599", symbol: "WBTC", decimals: 8, name: "Wrapped BTC" },
    { address: "0x6B175474E89094C44Da98b954EedeAC495271d0F", symbol: "DAI", decimals: 18, name: "Dai Stablecoin" },
  ],
  8453: [
    { address: "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", symbol: "USDC", decimals: 6, name: "USD Coin" },
  ],
  84532: [
    { address: "0x036CbD53842c5426634e7929541eC2318f3dCF7e", symbol: "USDC", decimals: 6, name: "USD Coin" },
  ],
  137: [
    { address: "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174", symbol: "USDC", decimals: 6, name: "USD Coin" },
    { address: "0xc2132D05D31c914a87C6611C10748AEb04B58e8F", symbol: "USDT", decimals: 6, name: "Tether USD" },
  ],
};

const CUSTOM_TOKENS_KEY = "claude_custom_tokens";

/**
 * Get user-added custom tokens for a chain from localStorage
 */
export function getCustomTokens(chainId: number): TokenInfo[] {
  try {
    const stored = localStorage.getItem(`${CUSTOM_TOKENS_KEY}_${chainId}`);
    return stored ? JSON.parse(stored) : [];
  } catch {
    return [];
  }
}

/**
 * Save a custom token for a chain
 */
export function saveCustomToken(chainId: number, token: TokenInfo): void {
  const tokens = getCustomTokens(chainId);
  if (tokens.some(t => t.address.toLowerCase() === token.address.toLowerCase())) return;
  tokens.push(token);
  localStorage.setItem(`${CUSTOM_TOKENS_KEY}_${chainId}`, JSON.stringify(tokens));
}

/**
 * Remove a custom token for a chain
 */
export function removeCustomToken(chainId: number, address: string): void {
  const tokens = getCustomTokens(chainId).filter(
    t => t.address.toLowerCase() !== address.toLowerCase()
  );
  localStorage.setItem(`${CUSTOM_TOKENS_KEY}_${chainId}`, JSON.stringify(tokens));
}

/**
 * Fetch ERC20 token metadata (name, symbol, decimals) from the contract on-chain
 */
export async function fetchTokenMetadata(
  provider: ethers.BrowserProvider | ethers.JsonRpcProvider,
  tokenAddress: string
): Promise<TokenInfo | null> {
  const erc20MetadataAbi = [
    "function name() view returns (string)",
    "function symbol() view returns (string)",
    "function decimals() view returns (uint8)",
  ];
  try {
    const contract = new ethers.Contract(tokenAddress, erc20MetadataAbi, provider);
    const [name, symbol, decimals] = await Promise.all([
      contract.name(),
      contract.symbol(),
      contract.decimals(),
    ]);
    return { address: tokenAddress, name, symbol, decimals: Number(decimals) };
  } catch (e) {
    console.error("Failed to fetch token metadata:", e);
    return null;
  }
}
