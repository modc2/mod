"use client";

// The session, and the one function that creates one.
//
// Sign-in is always the same three steps whichever key you brought: ask the
// API for a nonce, hand the wallet the sentence it minted, post the signature
// back. The token that comes out is a bearer for every gated call and lives in
// localStorage until it expires or you sign out.

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { fetchMe, requestNonce, setAuthToken, verifySignature } from "../lib/api";
import type { Connected, WalletKind } from "../lib/wallets";
import type { Session } from "../lib/types";

const TOKEN_KEY = "lq_session";          // shared origin — namespace everything

interface AuthValue {
  token: string | null;
  session: Session | null;
  busy: boolean;
  error: string | null;
  signIn: (wallet: Connected) => Promise<Session>;
  signOut: () => void;
  clearError: () => void;
}

const AuthContext = createContext<AuthValue>({
  token: null, session: null, busy: false, error: null,
  signIn: async () => { throw new Error("no provider"); },
  signOut: () => {}, clearError: () => {},
});

export const useAuth = () => useContext(AuthContext);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(null);
  const [session, setSession] = useState<Session | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // api.ts holds the bearer for every call, so it has to learn about the
  // token the moment it changes — including the moment it becomes null.
  useEffect(() => { setAuthToken(token); }, [token]);

  // Replay a stored token against /auth/me rather than trusting it: the secret
  // may have been rotated, the token may have aged out, and a console that
  // shows you signed in when the API disagrees is worse than one that doesn't.
  useEffect(() => {
    let stored: string | null = null;
    try { stored = localStorage.getItem(TOKEN_KEY); } catch {}
    if (!stored) return;
    setToken(stored);
    fetchMe(stored)
      .then((me) => {
        if (me.signed_in) setSession(me);
        else { setToken(null); try { localStorage.removeItem(TOKEN_KEY); } catch {} }
      })
      .catch(() => {});
  }, []);

  const signIn = useCallback(async (wallet: Connected) => {
    setBusy(true);
    setError(null);
    try {
      const { nonce, message } = await requestNonce(wallet.address, wallet.kind);
      const signature = await wallet.sign(message);
      const out = await verifySignature(nonce, signature, wallet.pubkey);
      const next: Session = {
        signed_in: true,
        address: out.account.address,
        kind: out.account.kind as WalletKind,
        owner: out.owner,
        expires: Date.now() / 1000 + out.expires_in,
        label: wallet.label,
      };
      try { localStorage.setItem(TOKEN_KEY, out.token); } catch {}
      setToken(out.token);
      setSession(next);
      return next;
    } catch (e: any) {
      // Wallet rejections come back as codes; the message is what to show.
      const why = e?.code === 4001 ? "signature rejected in the wallet"
        : String(e?.message || e);
      setError(why);
      throw new Error(why);
    } finally {
      setBusy(false);
    }
  }, []);

  const signOut = useCallback(() => {
    try { localStorage.removeItem(TOKEN_KEY); } catch {}
    setToken(null);
    setSession(null);
  }, []);

  const value = useMemo(
    () => ({ token, session, busy, error, signIn, signOut,
             clearError: () => setError(null) }),
    [token, session, busy, error, signIn, signOut],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
