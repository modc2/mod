"use client";

import { createContext, useContext, useState, useCallback, useEffect, useRef, ReactNode } from "react";
import { AuthState } from "../lib/types";
import { detectWallet, connectWallet, deriveClobApiKey } from "../lib/auth";
import type { ClobCredentials } from "../lib/types";

interface LocalToken {
  token: string;
  tokenPreview: string;
  issuedAt: number;
}

interface AuthContextValue {
  auth: AuthState;
  connect: () => Promise<void>;
  disconnect: () => void;
  authenticate: () => Promise<void>;
  generateToken: () => void;
  localToken: LocalToken | null;
  error: string | null;
  loading: boolean;
  hasWallet: boolean;
}

const defaultAuth: AuthState = {
  connected: false,
  address: null,
  chainId: null,
  clobCreds: null,
  authenticated: false,
};

const LOCAL_TOKEN_KEY = "poly8bit_local_token";
// CLOB creds are bound to a specific EOA (HMAC secret + apiKey are minted
// against the recovered signer). Cache per-address so users don't re-sign
// the EIP-712 ClobAuth on every page load. localStorage is the same trust
// boundary as the wallet's permission — anyone with browser access already
// has account-level reach, so this doesn't widen the attack surface.
const CLOB_CREDS_PREFIX = "poly_clob_creds:";

function loadCachedClobCreds(addr: string): ClobCredentials | null {
  try {
    const raw = localStorage.getItem(CLOB_CREDS_PREFIX + addr.toLowerCase());
    if (!raw) return null;
    const obj = JSON.parse(raw) as ClobCredentials;
    if (!obj.apiKey || !obj.secret || !obj.passphrase) return null;
    return obj;
  } catch {
    return null;
  }
}

function saveCachedClobCreds(addr: string, creds: ClobCredentials): void {
  try {
    localStorage.setItem(CLOB_CREDS_PREFIX + addr.toLowerCase(), JSON.stringify(creds));
  } catch {}
}

function clearCachedClobCreds(addr: string): void {
  try {
    localStorage.removeItem(CLOB_CREDS_PREFIX + addr.toLowerCase());
  } catch {}
}

const AuthContext = createContext<AuthContextValue>({
  auth: defaultAuth,
  connect: async () => {},
  disconnect: () => {},
  authenticate: async () => {},
  generateToken: () => {},
  localToken: null,
  error: null,
  loading: false,
  hasWallet: false,
});

export function useAuth() {
  return useContext(AuthContext);
}

function loadStoredToken(): LocalToken | null {
  try {
    const raw = localStorage.getItem(LOCAL_TOKEN_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [auth, setAuth] = useState<AuthState>(defaultAuth);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [hasWallet, setHasWallet] = useState(false);
  const [localToken, setLocalToken] = useState<LocalToken | null>(null);

  useEffect(() => {
    setHasWallet(detectWallet());
    // Phone pairing: when the user scans the desktop's QR code, the URL
    // fragment carries the token (`#token=…`). Adopt it before reading the
    // existing local token so a freshly-paired phone matches the desktop
    // session immediately. Fragments never reach the server, so this is
    // safe to read even on a shared / proxied host.
    if (typeof window !== "undefined") {
      const hash = window.location.hash;
      const m = hash.match(/[#&]token=([a-f0-9]{32,128})/i);
      if (m && m[1]) {
        const token = m[1].toLowerCase();
        const entry: LocalToken = {
          token,
          tokenPreview: token.slice(0, 8),
          issuedAt: Date.now(),
        };
        try { localStorage.setItem(LOCAL_TOKEN_KEY, JSON.stringify(entry)); } catch {}
        setLocalToken(entry);
        // Clear the fragment so it isn't preserved in browser history /
        // shared again accidentally.
        try {
          const clean = window.location.pathname + window.location.search;
          window.history.replaceState(null, "", clean);
        } catch {}
      } else {
        setLocalToken(loadStoredToken());
      }
    } else {
      setLocalToken(loadStoredToken());
    }
    // Silent reconnect: if the wallet still reports an authorized account
    // for this origin, rehydrate it + cached CLOB creds without prompting.
    // eth_accounts (vs eth_requestAccounts) returns [] on no-permission,
    // so the user never sees a popup just by opening the page.
    if (typeof window === "undefined" || !window.ethereum) return;
    const eth = window.ethereum as {
      request: (a: { method: string; params?: unknown[] }) => Promise<unknown>;
    };
    (async () => {
      try {
        const accts = (await eth.request({ method: "eth_accounts" })) as string[];
        const addr = accts?.[0];
        if (!addr) return;
        const chainIdHex = (await eth.request({ method: "eth_chainId" })) as string;
        const chainId = parseInt(chainIdHex, 16);
        const cached = loadCachedClobCreds(addr);
        setAuth((prev) => ({
          ...prev,
          connected: true,
          address: addr,
          chainId,
          clobCreds: cached,
          authenticated: !!cached,
        }));
      } catch {}
    })();
  }, []);

  // Clear CLOB creds when the active wallet account changes. The HMAC
  // creds are minted against a specific EOA, so leaving them in place
  // after a switch produces silent 401s on every L2 call.
  useEffect(() => {
    if (typeof window === "undefined" || !window.ethereum) return;
    const eth = window.ethereum as {
      on?: (e: string, h: (...a: unknown[]) => void) => void;
      removeListener?: (e: string, h: (...a: unknown[]) => void) => void;
    };
    if (!eth.on || !eth.removeListener) return;
    const handler = (...args: unknown[]) => {
      const accts = (args[0] as string[]) || [];
      const next = accts[0]?.toLowerCase() || null;
      setAuth((prev) => {
        const cur = prev.address?.toLowerCase() || null;
        if (next === cur) return prev;
        // Account changed (or disconnected) — drop CLOB creds; user must
        // re-authenticate against the new address.
        return {
          ...prev,
          address: next,
          authenticated: false,
          clobCreds: null,
          connected: !!next,
        };
      });
    };
    eth.on("accountsChanged", handler);
    return () => eth.removeListener?.("accountsChanged", handler);
  }, []);

  const generateToken = useCallback(() => {
    const bytes = new Uint8Array(32);
    crypto.getRandomValues(bytes);
    const token = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
    const entry: LocalToken = { token, tokenPreview: token.slice(0, 8), issuedAt: Date.now() };
    try { localStorage.setItem(LOCAL_TOKEN_KEY, JSON.stringify(entry)); } catch {}
    setLocalToken(entry);
  }, []);

  const connect = useCallback(async () => {
    setError(null);
    setLoading(true);
    try {
      const { address, chainId } = await connectWallet();
      // Try to hydrate cached CLOB creds for this EOA — saves the EIP-712
      // sign prompt on every reload. Cache is invalidated by disconnect()
      // or by the user clicking the CLOB chip to force a re-derive.
      const cached = loadCachedClobCreds(address);
      setAuth((prev) => ({
        ...prev,
        connected: true,
        address,
        chainId,
        clobCreds: cached,
        authenticated: !!cached,
      }));
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "CONNECTION FAILED");
    } finally {
      setLoading(false);
    }
  }, []);

  const disconnect = useCallback(() => {
    // Explicit disconnect wipes the cached creds too — leaving them behind
    // would re-authenticate silently on the next reload and surprise the user.
    if (auth.address) clearCachedClobCreds(auth.address);
    setAuth(defaultAuth);
    setError(null);
  }, [auth.address]);

  const authenticate = useCallback(async () => {
    if (!auth.address) return;
    setError(null);
    setLoading(true);
    try {
      const creds = await deriveClobApiKey(auth.address);
      saveCachedClobCreds(auth.address, creds);
      setAuth((prev) => ({ ...prev, clobCreds: creds, authenticated: true }));
    } catch (e: unknown) {
      console.error("CLOB auth error:", e);
      setError(e instanceof Error ? e.message : "AUTH FAILED");
    } finally {
      setLoading(false);
    }
  }, [auth.address]);

  // Auto-initiate CLOB auth as soon as the wallet is connected. Tracks the
  // last EOA we attempted (success OR fail) so a rejected MetaMask prompt
  // doesn't get re-opened on every render. Re-tries only when the account
  // changes — accountsChanged handler above clears clobCreds, which resets
  // the precondition that auth.authenticated is false against a new addr.
  const autoAuthAttemptedFor = useRef<string | null>(null);
  useEffect(() => {
    if (!auth.connected || !auth.address) return;
    if (auth.authenticated) return;
    if (loading) return;
    if (autoAuthAttemptedFor.current === auth.address) return;
    autoAuthAttemptedFor.current = auth.address;
    void authenticate();
  }, [auth.connected, auth.address, auth.authenticated, loading, authenticate]);

  return (
    <AuthContext.Provider value={{ auth, connect, disconnect, authenticate, generateToken, localToken, error, loading, hasWallet }}>
      {children}
    </AuthContext.Provider>
  );
}
