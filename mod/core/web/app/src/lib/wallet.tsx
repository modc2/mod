"use client";

// Browser-wallet identity for the explorer.
//
// Everything on-chain the visitor does here is signed by THEIR wallet: staking
// NAT into the BlocTime protocol, unstaking, and the personal_sign that backs a
// module with BLOC. The server holds no key for them, so nothing here ever
// posts a private key or a session — only signatures and transactions the
// wallet itself produced.
//
// The connection is restored silently on load with `eth_accounts` (which never
// prompts): if the site is still authorized, the address comes back; if not,
// the header just shows "connect wallet".

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { BrowserProvider, Contract, type Eip1193Provider, type Signer } from "ethers";

/** Base Sepolia — where the BlocTime + Registry contracts live. */
export const DEFAULT_CHAIN = {
  id: 84532,
  hex: "0x14a34",
  name: "Base Sepolia",
  rpc: "https://sepolia.base.org",
  explorer: "https://sepolia.basescan.org",
};

const ADDR_KEY = "mod.web.wallet";

type Ethereum = Eip1193Provider & {
  on?: (event: string, handler: (...args: never[]) => void) => void;
  removeListener?: (event: string, handler: (...args: never[]) => void) => void;
  isMetaMask?: boolean;
};

function eth(): Ethereum | null {
  if (typeof window === "undefined") return null;
  return (window as unknown as { ethereum?: Ethereum }).ethereum ?? null;
}

export type WalletState = {
  /** Lowercase 0x address, or "" when not connected. */
  address: string;
  chainId: number | null;
  /** True when a wallet extension is present at all. */
  hasWallet: boolean;
  connecting: boolean;
  error: string;
  /** True when connected but on the wrong network for these contracts. */
  wrongNetwork: boolean;
  connect: () => Promise<string>;
  disconnect: () => void;
  /** Prompt a switch (adding the chain if the wallet doesn't know it). */
  switchNetwork: () => Promise<void>;
  signMessage: (message: string) => Promise<string>;
  getSigner: () => Promise<Signer>;
  short: string;
};

const Ctx = createContext<WalletState | null>(null);

export function shortAddress(a: string): string {
  return a && a.length > 12 ? `${a.slice(0, 6)}…${a.slice(-4)}` : a;
}

export function WalletProvider({ children }: { children: ReactNode }) {
  const [address, setAddress] = useState("");
  const [chainId, setChainId] = useState<number | null>(null);
  const [connecting, setConnecting] = useState(false);
  const [error, setError] = useState("");
  const [hasWallet, setHasWallet] = useState(false);

  // Silent restore + live account/chain tracking.
  useEffect(() => {
    const provider = eth();
    setHasWallet(!!provider);
    if (!provider) return;

    let alive = true;
    const remembered = (() => {
      try {
        return window.localStorage.getItem(ADDR_KEY) || "";
      } catch {
        return "";
      }
    })();

    provider
      .request({ method: "eth_accounts" })
      .then((accts) => {
        const list = (accts as string[]) || [];
        if (!alive || list.length === 0) return;
        // Prefer the address the visitor last used here if it's still unlocked.
        const pick =
          list.find((a) => a.toLowerCase() === remembered.toLowerCase()) || list[0];
        setAddress(pick.toLowerCase());
      })
      .catch(() => {});
    provider
      .request({ method: "eth_chainId" })
      .then((id) => alive && setChainId(Number(id as string)))
      .catch(() => {});

    const onAccounts = (...args: never[]) => {
      const list = (args[0] as unknown as string[]) || [];
      setAddress(list.length ? String(list[0]).toLowerCase() : "");
      setError("");
    };
    const onChain = (...args: never[]) => setChainId(Number(args[0] as unknown as string));
    provider.on?.("accountsChanged", onAccounts);
    provider.on?.("chainChanged", onChain);
    return () => {
      alive = false;
      provider.removeListener?.("accountsChanged", onAccounts);
      provider.removeListener?.("chainChanged", onChain);
    };
  }, []);

  // Remember the address so a reload restores the same one when several are
  // unlocked. Shared modc2.com origin — keep the write quota-safe.
  useEffect(() => {
    try {
      if (address) window.localStorage.setItem(ADDR_KEY, address);
      else window.localStorage.removeItem(ADDR_KEY);
    } catch {
      /* storage blocked — non-fatal, the session still works */
    }
  }, [address]);

  const connect = useCallback(async () => {
    const provider = eth();
    if (!provider) {
      const msg =
        "no browser wallet found — install MetaMask (or any EIP-1193 wallet) to stake";
      setError(msg);
      throw new Error(msg);
    }
    setConnecting(true);
    setError("");
    try {
      const accts = (await provider.request({
        method: "eth_requestAccounts",
      })) as string[];
      const addr = String(accts[0] || "").toLowerCase();
      setAddress(addr);
      const id = await provider.request({ method: "eth_chainId" });
      setChainId(Number(id as string));
      return addr;
    } catch (e) {
      // 4001 = user rejected: not an error worth shouting about.
      const msg =
        (e as { code?: number })?.code === 4001
          ? "connection cancelled"
          : String((e as Error)?.message || e);
      setError(msg);
      throw new Error(msg);
    } finally {
      setConnecting(false);
    }
  }, []);

  const disconnect = useCallback(() => {
    // EIP-1193 has no "disconnect" — forget the address on our side and stop
    // acting on it. The wallet keeps its own permission grant.
    setAddress("");
    setError("");
  }, []);

  const switchNetwork = useCallback(async () => {
    const provider = eth();
    if (!provider) return;
    try {
      await provider.request({
        method: "wallet_switchEthereumChain",
        params: [{ chainId: DEFAULT_CHAIN.hex }],
      });
    } catch (e) {
      // 4902 = the wallet has never heard of this chain — add it, then it's
      // switched automatically.
      if ((e as { code?: number })?.code === 4902) {
        await provider.request({
          method: "wallet_addEthereumChain",
          params: [
            {
              chainId: DEFAULT_CHAIN.hex,
              chainName: DEFAULT_CHAIN.name,
              nativeCurrency: { name: "Ether", symbol: "ETH", decimals: 18 },
              rpcUrls: [DEFAULT_CHAIN.rpc],
              blockExplorerUrls: [DEFAULT_CHAIN.explorer],
            },
          ],
        });
      } else {
        throw e;
      }
    }
  }, []);

  const getSigner = useCallback(async () => {
    const provider = eth();
    if (!provider) throw new Error("no wallet connected");
    const browser = new BrowserProvider(provider);
    return browser.getSigner();
  }, []);

  const signMessage = useCallback(
    async (message: string) => {
      const signer = await getSigner();
      return signer.signMessage(message);
    },
    [getSigner],
  );

  const value = useMemo<WalletState>(
    () => ({
      address,
      chainId,
      hasWallet,
      connecting,
      error,
      wrongNetwork: !!address && chainId !== null && chainId !== DEFAULT_CHAIN.id,
      connect,
      disconnect,
      switchNetwork,
      signMessage,
      getSigner,
      short: shortAddress(address),
    }),
    [
      address,
      chainId,
      hasWallet,
      connecting,
      error,
      connect,
      disconnect,
      switchNetwork,
      signMessage,
      getSigner,
    ],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useWallet(): WalletState {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useWallet must be used inside <WalletProvider>");
  return ctx;
}

/** Minimal ERC-20 surface: enough to read a balance and approve a spender. */
export const ERC20_ABI = [
  "function balanceOf(address) view returns (uint256)",
  "function allowance(address,address) view returns (uint256)",
  "function approve(address,uint256) returns (bool)",
  "function symbol() view returns (string)",
  "function decimals() view returns (uint8)",
];

/** The BlocTime calls a staker needs — reads come from the module's API. */
export const BLOCTIME_ABI = [
  "function stake(uint256 amount, uint256 lockBlocks)",
  "function unstake(uint256 stakeId)",
  "function claimRewards()",
];

export function contractAt(address: string, abi: string[], signer: Signer): Contract {
  return new Contract(address, abi, signer);
}
