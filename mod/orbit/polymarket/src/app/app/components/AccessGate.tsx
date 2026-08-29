"use client";

// Full-screen gate in front of the entire console. The API is owner-only
// (access.rs): until the sudo address signs the terms-acceptance challenge,
// every data/action call 401s — so rather than render a UI full of dead
// panels, we hold the whole app behind this screen.
//
// Flow: check stored token → open. Otherwise show the Terms of Use (served
// by the API — its hash is embedded in what gets signed) and CONNECT & SIGN.
// The signature IS the acceptance: the signed challenge message already
// states "I have read and accept the Terms of Use in full, including the
// jurisdiction clause" — no checkboxes. A non-owner signer gets a clear
// ACCESS DENIED, not a broken console.

import { useCallback, useEffect, useState } from "react";
import {
  ACCESS_REVOKED_EVENT,
  checkAccess,
  clearAccessToken,
  fetchAccessInfo,
  installAccessFetch,
  matchesOwnerHint,
  signInAsOwner,
  type AccessInfo,
} from "../lib/access";
import { connectWallet } from "../lib/auth";

type Phase = "checking" | "locked" | "open";

export default function AccessGate({ children }: { children: React.ReactNode }) {
  const [phase, setPhase] = useState<Phase>("checking");
  const [info, setInfo] = useState<AccessInfo | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [denied, setDenied] = useState<string | null>(null);

  const lock = useCallback(async () => {
    setPhase("locked");
    try {
      setInfo(await fetchAccessInfo());
    } catch {
      // API down — leave terms empty; the sign-in button will surface it.
    }
  }, []);

  useEffect(() => {
    installAccessFetch();
    let cancelled = false;
    (async () => {
      const ok = await checkAccess();
      if (cancelled) return;
      if (ok) setPhase("open");
      else void lock();
    })();
    const onRevoked = () => {
      setDenied(null);
      void lock();
    };
    window.addEventListener(ACCESS_REVOKED_EVENT, onRevoked);
    return () => {
      cancelled = true;
      window.removeEventListener(ACCESS_REVOKED_EVENT, onRevoked);
    };
  }, [lock]);

  const signIn = useCallback(async () => {
    setError(null);
    setDenied(null);
    setBusy(true);
    try {
      const { address } = await connectWallet();
      // The gate only admits the deployment owner. MetaMask hands back its
      // currently-selected account first — not necessarily the owner. If the
      // owner's address (per the API's hint) is among the authorized
      // accounts, sign with it instead of bouncing off ACCESS DENIED.
      let signer = address;
      if (info?.ownerHint && window.ethereum) {
        try {
          const accts = (await window.ethereum.request({
            method: "eth_accounts",
          })) as string[];
          const owner = accts?.find((a) => matchesOwnerHint(a, info.ownerHint!));
          if (owner) signer = owner;
        } catch {}
      }
      await signInAsOwner(signer);
      setPhase("open");
    } catch (e) {
      const msg = e instanceof Error ? e.message : "SIGN-IN FAILED";
      if (/access denied|private to its operator/i.test(msg)) {
        clearAccessToken();
        setDenied(msg);
      } else {
        setError(msg);
      }
    } finally {
      setBusy(false);
    }
  }, [info]);

  if (phase === "open") return <>{children}</>;

  if (phase === "checking") {
    return (
      <div className="min-h-screen flex flex-col items-center justify-center gap-3">
        <LockBadge className="opacity-60 animate-pulse" />
        <div className="text-pixel-gray text-[11px] font-mono tracking-[0.25em] animate-pulse">
          CHECKING ACCESS…
        </div>
      </div>
    );
  }

  return (
    <div className="relative min-h-screen flex items-center justify-center px-4 py-10 overflow-hidden">
      {/* Soft emerald glow rising behind the card — the gate is the first
          thing anyone sees, so it gets the same ambient treatment as the
          console proper instead of a flat void. */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0"
        style={{
          background:
            "radial-gradient(640px 340px at 50% 12%, rgb(var(--accent) / 0.08), transparent 70%), radial-gradient(520px 300px at 82% 88%, rgb(var(--accent-3) / 0.05), transparent 70%)",
        }}
      />

      <div className="relative w-full max-w-[680px] pixel-panel overflow-hidden">
        {/* ── Header: lock badge + identity + terms version pill ── */}
        <div
          className="px-5 py-4 flex items-center gap-3.5"
          style={{ borderBottom: "1px solid var(--border)" }}
        >
          <LockBadge denied={!!denied} />
          <div className="min-w-0 flex-1">
            <div className="font-display text-[15px] font-semibold tracking-[0.1em] text-pixel-white">
              POLYMARKET CONSOLE
            </div>
            <div className="text-[10.5px] font-mono tracking-[0.16em] text-pixel-gray mt-0.5">
              PRIVATE DEPLOYMENT — OWNER ACCESS ONLY
            </div>
          </div>
          <span
            className="shrink-0 text-[10px] font-mono tracking-[0.12em] text-pixel-gray-light rounded-full px-2.5 py-1"
            style={{ border: "1px solid var(--border-strong)", background: "var(--btn-bg)" }}
          >
            TERMS v{info?.termsVersion ?? "2.0"}
          </span>
        </div>

        {denied ? (
          <div className="px-5 py-7 space-y-4">
            <div className="flex items-center gap-2">
              <span className="w-1.5 h-1.5 rounded-full bg-red-400 shadow-[0_0_10px_rgba(248,113,113,0.7)]" />
              <span className="text-[13px] font-mono font-bold tracking-[0.2em] text-red-400">
                ACCESS DENIED
              </span>
            </div>
            <p className="text-[12.5px] leading-5 text-pixel-gray-light max-w-[52ch]">
              This console is restricted to its operator&apos;s sudo address. The
              wallet you signed with is not authorized.
            </p>
            <button
              onClick={() => setDenied(null)}
              className="pixel-btn text-[12px] px-4 py-2 text-pixel-gray-light hover:text-pixel-white"
            >
              TRY A DIFFERENT WALLET
            </button>
          </div>
        ) : (
          <div className="px-5 py-5 space-y-4">
            {/* One line at the card's width — the hash/acceptance detail it
                used to carry lives in the fine print under the terms well. */}
            <p className="text-[13px] leading-5 text-pixel-gray-light">
              Sign-in requires reading and cryptographically accepting these
              Terms of Use.
            </p>

            {/* Terms well — the document is short by design, so it grows to
                fit and only scrolls if a future version outgrows the card. */}
            <div
              className="rounded-[var(--radius)] max-h-[280px] overflow-y-auto px-4 py-3.5"
              style={{ border: "1px solid var(--border-strong)", background: "var(--input-bg)" }}
            >
              <pre className="whitespace-pre-wrap text-[11.5px] leading-[1.65] text-pixel-white/75 font-mono">
                {info?.terms ?? "Loading terms…"}
              </pre>
            </div>

            <p className="text-[10.5px] leading-[1.6] text-pixel-gray">
              Your signature is your acceptance — the signed message embeds
              these terms and their hash.
            </p>

            {error && (
              <div className="text-[11px] leading-4 text-red-400 rounded-[var(--radius-sm)] border border-red-400/40 bg-red-400/[0.06] px-3 py-2">
                {error}
              </div>
            )}

            <button
              onClick={signIn}
              disabled={busy}
              className="w-full rounded-[var(--radius)] px-4 py-3 text-[13px] font-bold tracking-[0.1em] transition-all hover:brightness-110 hover:-translate-y-px active:translate-y-0 active:brightness-95 disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:translate-y-0 disabled:hover:brightness-100"
              style={{
                color: "#06130c",
                background:
                  "linear-gradient(180deg, rgb(var(--accent)) 0%, rgb(var(--accent) / 0.82) 100%)",
                boxShadow:
                  "0 1px 0 rgba(255,255,255,0.25) inset, 0 10px 28px -10px rgb(var(--accent) / 0.55)",
              }}
            >
              {busy ? "WAITING FOR SIGNATURE…" : "CONNECT WALLET & SIGN TERMS"}
            </button>
            <p className="text-[10px] leading-4 text-pixel-gray/80 text-center">
              Only the deployment owner&apos;s sudo address
              {info?.ownerHint ? (
                <>
                  {" "}
                  <span className="font-mono text-pixel-gray-light">{info.ownerHint}</span>
                </>
              ) : null}{" "}
              can enter — any other signer is refused.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

// Rounded lock tile used by both the checking spinner and the gate header.
function LockBadge({ denied = false, className = "" }: { denied?: boolean; className?: string }) {
  return (
    <div
      className={`w-10 h-10 grid place-items-center rounded-[var(--radius)] shrink-0 ${
        denied
          ? "text-red-400 border border-red-400/35 bg-red-400/[0.08]"
          : "text-green-400 border border-green-400/30 bg-green-400/[0.08]"
      } ${className}`}
      style={{
        boxShadow: denied
          ? "0 0 18px rgba(248,113,113,0.12)"
          : "0 0 18px rgb(var(--accent) / 0.14)",
      }}
    >
      <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <rect x="4" y="11" width="16" height="10" rx="2.5" />
        <path d="M8 11V7a4 4 0 0 1 8 0v4" />
      </svg>
    </div>
  );
}
