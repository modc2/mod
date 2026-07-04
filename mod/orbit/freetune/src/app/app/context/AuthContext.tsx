"use client";

import { createContext, useCallback, useContext, useEffect, useState, ReactNode } from "react";
import { api, setOwnerToken } from "../lib/api";
import { buildModToken, connectWallet, hasWallet } from "../lib/wallet";

interface AuthState {
  address: string | null;
  connected: boolean;
  authenticated: boolean;
  gated: boolean; // true once we know the API requires an owner token
  ownerAddress: string | null;
  isOwner: boolean;
}

interface AuthContextValue {
  auth: AuthState;
  hasWallet: boolean;
  loading: boolean;
  error: string | null;
  signIn: () => Promise<void>;
  signOut: () => void;
}

const TOKEN_KEY = "freetune_owner_token";
const ADDR_KEY = "freetune_owner_addr";
const ISSUED_KEY = "freetune_owner_issued";
// Tokens are valid for 3600s server-side; stop offering a cached one a bit
// before that so we don't hand the API something it's about to reject.
const TOKEN_LIFETIME_SECS = 3500;

const defaultAuth: AuthState = {
  address: null,
  connected: false,
  authenticated: false,
  gated: false,
  ownerAddress: null,
  isOwner: false,
};

const AuthContext = createContext<AuthContextValue>({
  auth: defaultAuth,
  hasWallet: false,
  loading: false,
  error: null,
  signIn: async () => {},
  signOut: () => {},
});

export function useAuth() {
  return useContext(AuthContext);
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [auth, setAuth] = useState<AuthState>(defaultAuth);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [walletPresent, setWalletPresent] = useState(false);

  // Learn whether the API actually requires a token, and who the owner is,
  // so the UI can show "connect as 0x..." instead of guessing.
  useEffect(() => {
    setWalletPresent(hasWallet());
    api
      .info()
      .then((info) => {
        const ownerAddress = info.owner || null;
        setAuth((prev) => ({
          ...prev,
          gated: info.auth === "owner-gated",
          ownerAddress,
          isOwner: !!(prev.address && ownerAddress && prev.address.toLowerCase() === ownerAddress.toLowerCase()),
        }));
      })
      .catch(() => {});

    try {
      const token = localStorage.getItem(TOKEN_KEY);
      const address = localStorage.getItem(ADDR_KEY);
      const issuedAt = Number(localStorage.getItem(ISSUED_KEY) || 0);
      const fresh = issuedAt > 0 && Date.now() / 1000 - issuedAt < TOKEN_LIFETIME_SECS;
      if (token && address && fresh) {
        setOwnerToken(token);
        setAuth((prev) => ({ ...prev, address, connected: true, authenticated: true }));
      } else if (token) {
        localStorage.removeItem(TOKEN_KEY);
        localStorage.removeItem(ADDR_KEY);
        localStorage.removeItem(ISSUED_KEY);
      }
    } catch {}
  }, []);

  const signIn = useCallback(async () => {
    setError(null);
    setLoading(true);
    try {
      const { address } = await connectWallet();
      const token = await buildModToken(address, { app: "freetune" });
      setOwnerToken(token);
      try {
        localStorage.setItem(TOKEN_KEY, token);
        localStorage.setItem(ADDR_KEY, address);
        localStorage.setItem(ISSUED_KEY, String(Date.now() / 1000));
      } catch {}
      setAuth((prev) => ({
        ...prev,
        address,
        connected: true,
        authenticated: true,
        isOwner: !!(prev.ownerAddress && address.toLowerCase() === prev.ownerAddress.toLowerCase()),
      }));
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      if (!/reject|denied|cancel/i.test(msg)) setError(msg);
    } finally {
      setLoading(false);
    }
  }, []);

  const signOut = useCallback(() => {
    setOwnerToken(null);
    try {
      localStorage.removeItem(TOKEN_KEY);
      localStorage.removeItem(ADDR_KEY);
      localStorage.removeItem(ISSUED_KEY);
    } catch {}
    setAuth((prev) => ({ ...defaultAuth, gated: prev.gated, ownerAddress: prev.ownerAddress }));
  }, []);

  return (
    <AuthContext.Provider value={{ auth, hasWallet: walletPresent, loading, error, signIn, signOut }}>
      {children}
    </AuthContext.Provider>
  );
}
