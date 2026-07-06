declare global {
  interface Window {
    ethereum?: {
      request: (args: { method: string; params?: unknown[] }) => Promise<unknown>;
      on: (event: string, handler: (...args: unknown[]) => void) => void;
      removeListener: (event: string, handler: (...args: unknown[]) => void) => void;
      isMetaMask?: boolean;
    };
    solana?: {
      isPhantom?: boolean;
      connect: () => Promise<{ publicKey: { toString: () => string } }>;
      disconnect: () => Promise<void>;
    };
  }
}

export function shortAddress(addr: string): string {
  if (!addr || addr.length < 10) return addr || "";
  return `${addr.slice(0, 6)}...${addr.slice(-4)}`;
}

export function detectWallets(): { metamask: boolean; phantom: boolean } {
  if (typeof window === "undefined") return { metamask: false, phantom: false };
  return {
    metamask: !!(window.ethereum && window.ethereum.isMetaMask),
    phantom: !!(window.solana && window.solana.isPhantom),
  };
}

export async function connectMetaMask(): Promise<{ address: string; chainId: number }> {
  if (!window.ethereum) throw new Error("MetaMask not installed");
  const accounts = (await window.ethereum.request({
    method: "eth_requestAccounts",
  })) as string[];
  const chainIdHex = (await window.ethereum.request({
    method: "eth_chainId",
  })) as string;
  return {
    address: accounts[0],
    chainId: parseInt(chainIdHex, 16),
  };
}

export async function connectPhantom(): Promise<{ address: string }> {
  if (!window.solana || !window.solana.isPhantom) throw new Error("Phantom not installed");
  const resp = await window.solana.connect();
  return { address: resp.publicKey.toString() };
}

const CHAIN_PARAMS: Record<number, { chainId: string; chainName: string; rpcUrls: string[]; nativeCurrency: { name: string; symbol: string; decimals: number } }> = {
  137: {
    chainId: "0x89",
    chainName: "Polygon",
    rpcUrls: ["https://polygon-bor-rpc.publicnode.com"],
    nativeCurrency: { name: "MATIC", symbol: "MATIC", decimals: 18 },
  },
  8453: {
    chainId: "0x2105",
    chainName: "Base",
    rpcUrls: ["https://mainnet.base.org"],
    nativeCurrency: { name: "Ether", symbol: "ETH", decimals: 18 },
  },
  42161: {
    chainId: "0xa4b1",
    chainName: "Arbitrum One",
    rpcUrls: ["https://arb1.arbitrum.io/rpc"],
    nativeCurrency: { name: "Ether", symbol: "ETH", decimals: 18 },
  },
  1: {
    chainId: "0x1",
    chainName: "Ethereum",
    rpcUrls: ["https://eth.llamarpc.com"],
    nativeCurrency: { name: "Ether", symbol: "ETH", decimals: 18 },
  },
};

export async function switchChain(chainId: number): Promise<void> {
  if (!window.ethereum) throw new Error("MetaMask not installed");
  const hexChainId = "0x" + chainId.toString(16);
  try {
    await window.ethereum.request({
      method: "wallet_switchEthereumChain",
      params: [{ chainId: hexChainId }],
    });
  } catch (err: unknown) {
    const error = err as { code?: number };
    if (error.code === 4902) {
      const params = CHAIN_PARAMS[chainId];
      if (params) {
        await window.ethereum.request({
          method: "wallet_addEthereumChain",
          params: [params],
        });
      }
    }
  }
}
