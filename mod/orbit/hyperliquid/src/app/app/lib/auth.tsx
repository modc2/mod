"use client";

/**
 * The session, as its own module.
 *
 * `lib/wallet.tsx` owns the **wallet**: which account is attached, and how to
 * make it sign things. This owns the **session**: whether that account holds a
 * live mod-protocol token, how to get one, and — the part that matters — what
 * the rest of the app is allowed to do without one.
 *
 * Splitting the two is the whole fix for "why did saving my strat 401".
 * Before, every write surface asked the same question: `if (!address) …`. But
 * an address is true in three situations that are not the same thing at all:
 *
 *   - a pasted watch-only address, which can never sign anything;
 *   - a connected wallet whose signature prompt was dismissed, which left the
 *     header showing an address and the token silently `null`;
 *   - a wallet whose token expired quietly a week after sign-in.
 *
 * In all three the console looked signed in, let the user fill in a form, and
 * then reported a status code. A UI that can't tell those states apart will
 * always fail at the last step, which is the most expensive place to fail.
 *
 * So the session is one value with five states, and `canWrite` is derived from
 * it rather than guessed at each call site.
 */

import {
  createContext, useCallback, useContext, useEffect, useMemo, useRef, useState,
} from "react";
import { authMe, setTokenRefresher } from "./api";
import { useWallet } from "./wallet";

export type SessionState =
  /** Reading localStorage / revalidating a restored token. Show nothing yet. */
  | "loading"
  /** No wallet attached at all. */
  | "anonymous"
  /** A read-only address. Everything renders; nothing can be signed. */
  | "watching"
  /** A real wallet is attached but holds no live token. One click away. */
  | "signed-out"
  /** Verified token. Writes are allowed. */
  | "signed-in";

export type Session = {
  state: SessionState;
  address: string | null;
  /** The signed-in wallet, or null. Use this — never a raw `address` — when
   *  a value is about to be sent to the API as "me". */
  me: string | null;
  /** Writes will be accepted. */
  canWrite: boolean;
  /** A signature would get us there: attached wallet, no live token. */
  needsSignIn: boolean;
  /** Attached read-only — signing is impossible without connecting properly. */
  isWatching: boolean;
  /** Mint a token (prompts the wallet). Returns it, or null if declined. */
  signIn: () => Promise<string | null>;
  /** Why the last sign-in attempt failed, if it did. */
  error: string | null;
  /** One line naming the current state, for buttons and empty states. */
  label: string;
};

const LABELS: Record<SessionState, string> = {
  loading: "Checking your session…",
  anonymous: "Connect a wallet to get started.",
  watching: "You're watching read-only — connect a wallet to make changes.",
  "signed-out": "Sign in with your wallet to continue.",
  "signed-in": "Signed in.",
};

const Ctx = createContext<Session>({
  state: "loading", address: null, me: null,
  canWrite: false, needsSignIn: false, isWatching: false,
  signIn: async () => null, error: null, label: LABELS.loading,
});

export function SessionProvider({ children }: { children: React.ReactNode }) {
  const { address, kind, token, signIn: mint } = useWallet();
  const [checked, setChecked] = useState(false);
  const [live, setLive] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // A restored token is a claim, not a fact: it was minted up to a week ago
  // and the server's freshness window may have moved under it. Ask once, on
  // mount, so the header never announces "Signed in" over a dead session —
  // the lie is what let a user reach the save button before finding out.
  const probed = useRef<string | null>(null);
  useEffect(() => {
    if (!token) {
      // Nothing to verify. Signed-out is a known state, not a pending one.
      probed.current = null;
      setLive(false);
      setChecked(true);
      return;
    }
    if (probed.current === token) return;
    probed.current = token;
    setLive(false);
    setChecked(false);

    let cancelled = false;
    authMe()
      .then(() => { if (!cancelled) setLive(true); })
      .catch(() => { if (!cancelled) setLive(false); })
      .finally(() => { if (!cancelled) setChecked(true); });
    return () => { cancelled = true; };
  }, [token]);

  const signIn = useCallback(async () => {
    setError(null);
    try {
      const t = await mint();
      if (!t) {
        setError("Sign-in was cancelled.");
        return null;
      }
      setLive(true);
      return t;
    } catch (e: any) {
      // MetaMask's user-rejection code. Anything else is worth showing raw.
      const rejected = e?.code === 4001 || /user rejected|denied/i.test(String(e?.message ?? ""));
      setError(rejected ? "You declined the signature, so nothing was saved." : String(e?.message ?? e));
      return null;
    }
  }, [mint]);

  // Hand the transport its way back. A write that lands on an expired session
  // now costs one signature prompt instead of an error the user can't act on.
  // Registered here rather than in api.ts because only this layer knows how to
  // reach the wallet — api.ts stays a transport and nothing more.
  useEffect(() => {
    setTokenRefresher(kind === "metamask" ? signIn : null);
    return () => setTokenRefresher(null);
  }, [kind, signIn]);

  const value = useMemo<Session>(() => {
    const state: SessionState =
      !checked && !!token ? "loading"
      : !address ? "anonymous"
      : kind === "watch" ? "watching"
      : token && live ? "signed-in"
      : "signed-out";

    return {
      state,
      address,
      me: state === "signed-in" ? address : null,
      canWrite: state === "signed-in",
      needsSignIn: state === "signed-out",
      isWatching: state === "watching",
      signIn,
      error,
      label: LABELS[state],
    };
  }, [checked, token, live, address, kind, signIn, error]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export const useSession = () => useContext(Ctx);
