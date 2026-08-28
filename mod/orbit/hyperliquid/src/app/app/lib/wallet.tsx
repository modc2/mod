"use client";

import {
  createContext, useCallback, useContext, useEffect, useMemo, useState,
} from "react";

// Two ways to attach a wallet:
//   metamask — real EIP-1193 connection; can sign typed data + send txs,
//              so deposits / withdrawals / agent approval all work.
//   watch    — pasted address, read-only. Everything renders, nothing signs.
export type WalletKind = "metamask" | "watch";

type Wallet = {
  address: string | null;
  kind: WalletKind | null;
  hasProvider: boolean;
  /** mod protocol-auth bearer token (null until sign-in / for watch mode). */
  token: string | null;
  /** personal_sign a fresh protocol-auth token for the connected account. */
  signIn: () => Promise<string | null>;
  /** Connect MetaMask (eth_requestAccounts) + sign in. Returns the address or null. */
  connect: () => Promise<string | null>;
  /** Attach a read-only address. */
  watch: (a: string) => void;
  disconnect: () => void;
  /** Back-compat: watch(a) / disconnect(null). */
  setAddress: (a: string | null) => void;
  /** eth_signTypedData_v4 with the connected account. */
  signTypedData: (typedData: any) => Promise<string>;
  /** Switch (or add+switch) MetaMask to the given chain. */
  ensureChain: (cfg: { chainIdHex: string; chainName: string; rpcUrl: string; explorerUrl: string }) => Promise<void>;
  /** eth_sendTransaction from the connected account. Returns the tx hash. */
  sendTransaction: (tx: { to: string; data?: string; value?: string }) => Promise<string>;
};

const C = createContext<Wallet>({
  address: null, kind: null, hasProvider: false,
  token: null, signIn: async () => null,
  connect: async () => null, watch: () => {}, disconnect: () => {},
  setAddress: () => {},
  signTypedData: async () => { throw new Error("no wallet"); },
  ensureChain: async () => {},
  sendTransaction: async () => { throw new Error("no wallet"); },
});

const KEY = "hl_wallet_v2";
const LEGACY_KEY = "hl_wallet";

const eth = () => (typeof window !== "undefined" ? (window as any).ethereum : undefined);

// modc2.com modules share one localStorage origin — never let a quota error
// crash sign-in, and keep the persisted blob tiny.
function persist(v: { address: string; kind: WalletKind; token?: string | null } | null) {
  try {
    if (v) localStorage.setItem(KEY, JSON.stringify(v));
    else { localStorage.removeItem(KEY); localStorage.removeItem(LEGACY_KEY); }
  } catch {}
}

// ── mod protocol-auth token ──────────────────────────────────────────────
// The API verifies this statelessly (src/api/src/auth.rs): base64url of
// { data, time, key, signature } where signature = personal_sign of the
// compact JSON {"data":…,"time":…}. Identity = the recovered signer.
const TOKEN_TTL_MS = 7 * 24 * 3600 * 1000; // must not exceed HYPERLIQUID_SESSION_TTL

function b64url(obj: unknown): string {
  const s = JSON.stringify(obj);
  const b64 = btoa(unescape(encodeURIComponent(s)));
  return b64.replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

async function mintToken(provider: any, address: string): Promise<string> {
  const data = { scope: "hyperliquid" };
  const time = (Date.now() / 1000).toString();
  const sigData = JSON.stringify({ data, time });
  const signature: string = await provider.request({
    method: "personal_sign",
    params: [sigData, address],
  });
  return b64url({ data, time, key: address.toLowerCase(), signature });
}

// A restored token past the server's freshness window would just 401 —
// drop it up front so the UI shows "sign in" instead of failing requests.
function tokenFresh(token: string | null | undefined): boolean {
  if (!token) return false;
  try {
    const b64 = token.replace(/-/g, "+").replace(/_/g, "/");
    const env = JSON.parse(decodeURIComponent(escape(atob(b64))));
    return Date.now() - Number(env.time) * 1000 < TOKEN_TTL_MS - 60_000;
  } catch { return false; }
}

export function WalletProvider({ children }: { children: React.ReactNode }) {
  const [address, setAddr] = useState<string | null>(null);
  const [kind, setKind] = useState<WalletKind | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [hasProvider, setHasProvider] = useState(false);

  useEffect(() => {
    setHasProvider(!!eth());
    try {
      const raw = localStorage.getItem(KEY);
      if (raw) {
        const v = JSON.parse(raw);
        if (v?.address) {
          setAddr(String(v.address).toLowerCase());
          setKind(v.kind === "metamask" ? "metamask" : "watch");
          if (v.kind === "metamask" && tokenFresh(v.token)) setToken(v.token);
          return;
        }
      }
      const legacy = localStorage.getItem(LEGACY_KEY);
      if (legacy) { setAddr(legacy.toLowerCase()); setKind("watch"); }
    } catch {}
  }, []);

  // The API answered 401 (stale/revoked token) — drop the session token so
  // the header offers a fresh sign-in instead of every request failing.
  useEffect(() => {
    const onUnauthorized = () => {
      setToken(null);
      setAddr((a) => {
        setKind((k) => {
          if (a && k) persist({ address: a, kind: k, token: null });
          return k;
        });
        return a;
      });
    };
    window.addEventListener("hl:unauthorized", onUnauthorized);
    return () => window.removeEventListener("hl:unauthorized", onUnauthorized);
  }, []);

  // Track MetaMask account switches / disconnects.
  useEffect(() => {
    const p = eth();
    if (!p?.on) return;
    const onAccounts = (accs: string[]) => {
      setKind((k) => {
        if (k !== "metamask") return k;
        const a = accs?.[0]?.toLowerCase() ?? null;
        setAddr(a);
        // The token is bound to the old account — the new one must re-sign.
        setToken(null);
        persist(a ? { address: a, kind: "metamask", token: null } : null);
        return a ? "metamask" : null;
      });
    };
    p.on("accountsChanged", onAccounts);
    return () => { p.removeListener?.("accountsChanged", onAccounts); };
  }, []);

  const signIn = useCallback(async (): Promise<string | null> => {
    const p = eth();
    if (!p) return null;
    const accs: string[] = await p.request({ method: "eth_requestAccounts" });
    const a = accs?.[0]?.toLowerCase();
    if (!a) return null;
    const t = await mintToken(p, a);
    setAddr(a); setKind("metamask"); setToken(t);
    persist({ address: a, kind: "metamask", token: t });
    return t;
  }, []);

  const connect = useCallback(async (): Promise<string | null> => {
    const p = eth();
    if (!p) return null;
    const accs: string[] = await p.request({ method: "eth_requestAccounts" });
    const a = accs?.[0]?.toLowerCase();
    if (!a) return null;
    setAddr(a); setKind("metamask");
    // Sign-in is part of connecting: mint the protocol-auth token in the same
    // gesture. A rejected signature leaves the wallet attached but signed out.
    let t: string | null = null;
    try { t = await mintToken(p, a); } catch {}
    setToken(t);
    persist({ address: a, kind: "metamask", token: t });
    return a;
  }, []);

  const watch = useCallback((a: string) => {
    const lc = a.toLowerCase();
    setAddr(lc); setKind("watch"); setToken(null);
    persist({ address: lc, kind: "watch" });
  }, []);

  const disconnect = useCallback(() => {
    setAddr(null); setKind(null); setToken(null);
    persist(null);
  }, []);

  const setAddress = useCallback((a: string | null) => {
    if (a) watch(a); else disconnect();
  }, [watch, disconnect]);

  const signTypedData = useCallback(async (typedData: any): Promise<string> => {
    const p = eth();
    if (!p) throw new Error("MetaMask not available");
    const accs: string[] = await p.request({ method: "eth_requestAccounts" });
    const from = accs?.[0];
    if (!from) throw new Error("no MetaMask account");
    return p.request({
      method: "eth_signTypedData_v4",
      params: [from, JSON.stringify(typedData)],
    });
  }, []);

  const ensureChain = useCallback(async (cfg: { chainIdHex: string; chainName: string; rpcUrl: string; explorerUrl: string }) => {
    const p = eth();
    if (!p) throw new Error("MetaMask not available");
    const current = await p.request({ method: "eth_chainId" });
    if (String(current).toLowerCase() === cfg.chainIdHex.toLowerCase()) return;
    try {
      await p.request({ method: "wallet_switchEthereumChain", params: [{ chainId: cfg.chainIdHex }] });
    } catch (e: any) {
      // 4902 = chain not added to MetaMask yet.
      if (e?.code === 4902 || String(e?.message ?? "").includes("4902")) {
        await p.request({
          method: "wallet_addEthereumChain",
          params: [{
            chainId: cfg.chainIdHex,
            chainName: cfg.chainName,
            nativeCurrency: { name: "Ether", symbol: "ETH", decimals: 18 },
            rpcUrls: [cfg.rpcUrl],
            blockExplorerUrls: [cfg.explorerUrl],
          }],
        });
      } else throw e;
    }
  }, []);

  const sendTransaction = useCallback(async (tx: { to: string; data?: string; value?: string }): Promise<string> => {
    const p = eth();
    if (!p) throw new Error("MetaMask not available");
    const accs: string[] = await p.request({ method: "eth_requestAccounts" });
    const from = accs?.[0];
    if (!from) throw new Error("no MetaMask account");
    return p.request({ method: "eth_sendTransaction", params: [{ from, ...tx }] });
  }, []);

  const value = useMemo(() => ({
    address, kind, hasProvider, token, signIn,
    connect, watch, disconnect, setAddress,
    signTypedData, ensureChain, sendTransaction,
  }), [address, kind, hasProvider, token, signIn, connect, watch, disconnect, setAddress, signTypedData, ensureChain, sendTransaction]);

  return <C.Provider value={value}>{children}</C.Provider>;
}

export const useWallet = () => useContext(C);
