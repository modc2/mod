"use client";

/**
 * The one place the console asks "are you allowed to do this?".
 *
 * Wrap a primary action in it and the answer arrives *before* the work, in the
 * place the work happens:
 *
 *     <AuthGate action="save this strat">
 *       <button className="btn-primary" onClick={save}>save strat</button>
 *     </AuthGate>
 *
 * Signed in, the button renders untouched. Signed out, the same slot becomes
 * the button that fixes it — worded for what the user was actually trying to
 * do, not "unauthorized". Watching read-only, it says so and offers the real
 * connection instead.
 *
 * The point is that the gate lives next to the action rather than inside ten
 * different click handlers. Ten copies of `if (!address) setErr("connect
 * wallet first")` will disagree with each other and with the server; one
 * component derived from `useSession()` cannot.
 */

import { useState } from "react";
import { useSession } from "../lib/auth";
import { useWallet } from "../lib/wallet";

export default function AuthGate({
  action,
  children,
  className = "",
}: {
  /** Verb phrase completing "Sign in to ___" — e.g. "save this strat". */
  action: string;
  children: React.ReactNode;
  className?: string;
}) {
  const { canWrite, needsSignIn, isWatching, state, signIn, error } = useSession();
  const { connect, hasProvider } = useWallet();
  const [busy, setBusy] = useState(false);

  if (canWrite) return <>{children}</>;

  // Don't flash a sign-in prompt at someone who is already signed in and just
  // hasn't finished revalidating. Reserve the space instead.
  if (state === "loading") {
    return (
      <div className={`flex items-center gap-2 text-xs text-muted ${className}`}>
        <span className="h-1.5 w-1.5 rounded-full bg-muted animate-pulse" />
        Checking your session…
      </div>
    );
  }

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    try { await fn(); } finally { setBusy(false); }
  };

  // No wallet extension at all: a button that can't work is worse than a
  // sentence that explains why.
  if (!hasProvider && !isWatching) {
    return (
      <Note className={className}>
        A browser wallet is needed to {action}.{" "}
        <a className="text-accent underline underline-offset-2"
           href="https://metamask.io/download/" target="_blank" rel="noreferrer">
          Install MetaMask
        </a>{" "}
        and reload.
      </Note>
    );
  }

  const label =
    isWatching ? `Connect a wallet to ${action}`
    : needsSignIn ? `Sign in to ${action}`
    : `Connect a wallet to ${action}`;

  const onClick = () => run(needsSignIn ? signIn : connect);

  return (
    <div className={`space-y-2 ${className}`}>
      <button className="btn-primary" onClick={onClick} disabled={busy}>
        {busy ? "check your wallet…" : label}
      </button>
      <p className="text-[11px] text-muted leading-relaxed">
        {isWatching
          ? "You're viewing a watched address. Connect that wallet to act on it."
          : "One signature proves the wallet is yours. It costs no gas and authorises nothing on-chain."}
      </p>
      {error && <p className="text-[11px] text-warn">{error}</p>}
    </div>
  );
}

function Note({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return (
    <p className={`text-[11px] text-muted leading-relaxed ${className}`}>{children}</p>
  );
}

/**
 * The same judgement without the chrome — for rows and toolbars where a
 * paragraph would be absurd. Renders children when writes are allowed, and a
 * compact sign-in link otherwise.
 */
export function AuthGateInline({ action, children }: { action: string; children: React.ReactNode }) {
  const { canWrite, needsSignIn, isWatching, signIn } = useSession();
  const { connect } = useWallet();
  if (canWrite) return <>{children}</>;
  return (
    <button
      className="btn !text-[11px] whitespace-nowrap"
      title={isWatching ? "Watching read-only" : `Sign in to ${action}`}
      onClick={() => (needsSignIn ? signIn() : connect())}
    >
      {needsSignIn ? "sign in" : "connect"}
    </button>
  );
}
