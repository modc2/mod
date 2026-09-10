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
//
// Presentation: the document is short by design, so it is shown in full,
// re-flowed as numbered clauses (READABLE) with a one-press VERBATIM view of
// the exact bytes that get hashed and signed. The IN SHORT chips above it are
// a summary, labelled as one, and pinned to the terms version they were
// written against so a future version drops them instead of misquoting.

import { useCallback, useEffect, useMemo, useState } from "react";
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
  const [deniedAddr, setDeniedAddr] = useState<string | null>(null);
  const [verbatim, setVerbatim] = useState(false);

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
    let attempted: string | null = null;
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
      attempted = signer;
      await signInAsOwner(signer);
      setPhase("open");
    } catch (e) {
      const msg = e instanceof Error ? e.message : "SIGN-IN FAILED";
      if (/access denied|private to its operator/i.test(msg)) {
        clearAccessToken();
        setDeniedAddr(attempted);
        setDenied(msg);
      } else {
        setError(msg);
      }
    } finally {
      setBusy(false);
    }
  }, [info]);

  // Enter signs, from anywhere on the gate — there is exactly one action here.
  useEffect(() => {
    if (phase !== "locked" || denied) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Enter" && !busy) void signIn();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [phase, denied, busy, signIn]);

  const parsed = useMemo(() => parseTerms(info?.terms), [info?.terms]);

  if (phase === "open") return <>{children}</>;

  if (phase === "checking") {
    return (
      <div className="min-h-screen flex flex-col items-center justify-center gap-4">
        <LockBadge className="opacity-70" />
        <div className="text-pixel-gray text-[10px] font-mono tracking-[0.28em]">
          CHECKING ACCESS
        </div>
        {/* A bar that fills rather than a spinner: this probe is one request,
            so the shape should read as "briefly", not "indefinitely". */}
        <div
          className="w-[136px] h-[2px] rounded-full overflow-hidden"
          style={{ background: "var(--border)" }}
        >
          <div className="gate-sweep h-full w-1/3 rounded-full" />
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

      <div className="gate-in relative w-full max-w-[720px] pixel-panel overflow-hidden">
        {/* Accent hairline across the top edge — the one bit of color that
            says "this is the console" before any of it has loaded. */}
        <div
          aria-hidden
          className="absolute inset-x-0 top-0 h-px"
          style={{
            background:
              "linear-gradient(90deg, transparent, rgb(var(--accent) / 0.7) 30%, rgb(var(--accent-3) / 0.6) 70%, transparent)",
          }}
        />

        {/* ── Header: lock badge + identity + terms version pill ── */}
        <div
          className="px-5 py-4 flex items-center gap-3.5"
          style={{ borderBottom: "1px solid var(--border)" }}
        >
          <LockBadge denied={!!denied} />
          <div className="min-w-0 flex-1">
            <div className="font-display text-[16px] font-semibold tracking-[0.09em] text-pixel-white">
              POLYMARKET CONSOLE
            </div>
            <div className="text-[10px] font-mono tracking-[0.16em] text-pixel-gray mt-1 flex items-center gap-1.5">
              <span
                className="w-1 h-1 rounded-full shrink-0"
                style={{
                  background: denied ? "rgb(248 113 113)" : "rgb(var(--accent))",
                  boxShadow: denied
                    ? "0 0 8px rgba(248,113,113,0.8)"
                    : "0 0 8px rgb(var(--accent) / 0.8)",
                }}
              />
              <span className="whitespace-nowrap">
                <span className="hidden sm:inline">PRIVATE DEPLOYMENT · </span>
                OWNER ACCESS ONLY
              </span>
            </div>
          </div>
          <div className="shrink-0 text-right">
            <div
              className="text-[10px] font-mono tracking-[0.12em] text-pixel-gray-light rounded-full px-2.5 py-1"
              style={{ border: "1px solid var(--border-strong)", background: "var(--btn-bg)" }}
            >
              TERMS v{info?.termsVersion ?? parsed?.version ?? "2.0"}
            </div>
            {parsed?.effective && (
              <div className="text-[9px] font-mono tracking-[0.1em] text-pixel-gray mt-1.5">
                EFF. {parsed.effective}
              </div>
            )}
          </div>
        </div>

        {denied ? (
          <div className="px-5 py-7 space-y-4">
            <div className="flex items-center gap-2">
              <span className="w-1.5 h-1.5 rounded-full bg-red-400 shadow-[0_0_10px_rgba(248,113,113,0.7)]" />
              <span className="text-[13px] font-mono font-bold tracking-[0.2em] text-red-400">
                ACCESS DENIED
              </span>
            </div>
            <p className="text-[12.5px] leading-5 max-w-[52ch]" style={{ color: "var(--fg-muted)" }}>
              This console is restricted to its operator&apos;s sudo address. The
              wallet you signed with is not authorized.
            </p>
            {/* Naming both sides turns "denied" into a fixable instruction:
                switch accounts in the wallet, then sign again. */}
            {(deniedAddr || info?.ownerHint) && (
              <div
                className="rounded-[var(--radius-sm)] px-3 py-2.5 space-y-1.5 font-mono text-[11px]"
                style={{ border: "1px solid var(--border)", background: "var(--input-bg)" }}
              >
                {deniedAddr && (
                  <div className="flex items-center justify-between gap-3">
                    <span className="text-pixel-gray tracking-[0.1em]">YOU SIGNED WITH</span>
                    <span className="text-red-400 truncate">{shortAddr(deniedAddr)}</span>
                  </div>
                )}
                {info?.ownerHint && (
                  <div className="flex items-center justify-between gap-3">
                    <span className="text-pixel-gray tracking-[0.1em]">OWNER IS</span>
                    <span className="text-pixel-white truncate">{info.ownerHint}</span>
                  </div>
                )}
              </div>
            )}
            <button
              onClick={() => {
                setDenied(null);
                setDeniedAddr(null);
              }}
              className="pixel-btn text-[12px] px-4 py-2 text-pixel-gray-light hover:text-pixel-white"
            >
              TRY A DIFFERENT WALLET
            </button>
          </div>
        ) : (
          <div className="px-5 py-5 space-y-5">
            {/* ── IN SHORT — the summary, labelled as a summary ───────────── */}
            {info?.termsVersion === SUMMARY_FOR_VERSION && (
              <div className="space-y-2.5">
                <SectionLabel>IN SHORT — THE FULL TEXT IS BELOW</SectionLabel>
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
                  {SUMMARY.map((s) => (
                    <div
                      key={s.title}
                      className="rounded-[var(--radius-sm)] px-3 py-2.5 space-y-1.5"
                      style={{
                        border: "1px solid var(--border)",
                        background: "var(--input-bg)",
                      }}
                    >
                      <div className="flex items-center gap-1.5 font-mono text-[10px] tracking-[0.12em]">
                        <span
                          className="shrink-0 grid place-items-center"
                          style={{ color: s.warn ? "rgb(var(--warn))" : "rgb(var(--accent))" }}
                        >
                          <s.Icon />
                        </span>
                        <span style={{ color: "var(--fg)" }}>{s.title}</span>
                      </div>
                      <div className="text-[11px] leading-[1.5]" style={{ color: "var(--fg-muted)" }}>
                        {s.body}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* ── The agreement itself ────────────────────────────────────── */}
            <div className="space-y-2.5">
              <div className="flex items-end justify-between gap-3">
                {/* "POLYMARKET CONSOLE — TERMS OF USE" is already the card's
                    header; the label only needs the part after the dash. */}
                <SectionLabel>{parsed?.docLabel ?? "TERMS OF USE"}</SectionLabel>
                <div className="flex items-center gap-2 shrink-0">
                  {info?.termsSha256 && <HashChip hash={info.termsSha256} />}
                  {parsed && (
                    <button
                      onClick={() => setVerbatim((v) => !v)}
                      className="pixel-btn btn-xs text-pixel-gray hover:text-pixel-white"
                      title={
                        verbatim
                          ? "Re-flowed for reading — same words"
                          : "The exact bytes that are hashed and signed"
                      }
                    >
                      {verbatim ? "READABLE" : "VERBATIM"}
                    </button>
                  )}
                </div>
              </div>

              <div
                className="rounded-[var(--radius)] max-h-[300px] overflow-y-auto px-4 py-3.5"
                style={{ border: "1px solid var(--border-strong)", background: "var(--input-bg)" }}
              >
                {!info?.terms ? (
                  <TermsSkeleton />
                ) : verbatim || !parsed ? (
                  <pre className="whitespace-pre-wrap text-[11.5px] leading-[1.65] text-pixel-white/75 font-mono">
                    {info.terms}
                  </pre>
                ) : (
                  <ol className="space-y-3">
                    {parsed.clauses.map((c, i) => (
                      <li key={i} className="flex gap-3">
                        <span className="shrink-0 mt-[3px] w-[18px] h-[18px] grid place-items-center rounded-full border border-pixel-green/50 text-pixel-green font-mono text-[9.5px]">
                          {i + 1}
                        </span>
                        <p className="text-[12.5px] leading-[1.6] text-pixel-white/85">{c}</p>
                      </li>
                    ))}
                  </ol>
                )}
              </div>
            </div>

            {error && (
              <div className="text-[11px] leading-4 text-red-400 rounded-[var(--radius-sm)] border border-red-400/40 bg-red-400/[0.06] px-3 py-2">
                {error}
              </div>
            )}

            {/* ── The one action ──────────────────────────────────────────── */}
            <div className="space-y-2.5">
              <button
                onClick={signIn}
                disabled={busy}
                className="w-full rounded-[var(--radius)] px-4 py-3 text-[13px] font-bold tracking-[0.1em] transition-all flex items-center justify-center gap-2.5 hover:brightness-110 hover:-translate-y-px active:translate-y-0 active:brightness-95 disabled:opacity-45 disabled:cursor-not-allowed disabled:hover:translate-y-0 disabled:hover:brightness-100"
                style={{
                  color: "#06130c",
                  background:
                    "linear-gradient(180deg, rgb(var(--accent)) 0%, rgb(var(--accent) / 0.82) 100%)",
                  boxShadow:
                    "0 1px 0 rgba(255,255,255,0.25) inset, 0 10px 28px -10px rgb(var(--accent) / 0.55)",
                }}
              >
                {busy ? <Spinner /> : <SignIcon />}
                {busy ? "WAITING FOR SIGNATURE…" : "CONNECT WALLET & SIGN TERMS"}
              </button>

              <p
                className="text-[10.5px] leading-[1.6] text-center max-w-[58ch] mx-auto"
                style={{ color: "var(--fg-muted)" }}
              >
                {busy ? (
                  <>Approve the signature request in your wallet. It costs no gas and moves nothing.</>
                ) : (
                  <>
                    Signing is your acceptance — the message you sign embeds these
                    terms and their hash. Only the owner
                    {info?.ownerHint ? (
                      <>
                        {" "}
                        <span className="font-mono" style={{ color: "var(--fg)" }}>
                          {info.ownerHint}
                        </span>
                      </>
                    ) : (
                      <>&apos;s sudo address</>
                    )}{" "}
                    can enter; any other signer is refused.
                  </>
                )}
              </p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Terms text → structure ───────────────────────────────────────────────────
// The served document is "TITLE / Version … — Effective … / blank / clauses".
// Anything that doesn't fit that shape parses to null and falls back to the
// verbatim view, so a rewritten document can never render as an empty card.

interface ParsedTerms {
  title: string;
  /** Title minus the product name — "TERMS OF USE", not the whole heading. */
  docLabel: string;
  version: string;
  effective: string;
  clauses: string[];
}

function parseTerms(raw?: string | null): ParsedTerms | null {
  if (!raw) return null;
  const lines = raw.replace(/\r/g, "").trimStart().split("\n");
  const title = (lines[0] ?? "").trim();
  const meta = (lines[1] ?? "").trim();
  const clauses = lines
    .slice(2)
    .join("\n")
    .trim()
    .split(/\n\s*\n/)
    // Un-wrap the hard-wrapped source: the line breaks are typography, not
    // meaning, and re-flowing is what makes it readable at card width.
    .map((p) => p.split("\n").map((l) => l.trim()).join(" ").trim())
    .filter(Boolean);
  if (!title || clauses.length === 0) return null;
  return {
    title,
    docLabel: title.split(/\s+[—–-]\s+/).pop() || title,
    version: meta.match(/version\s+([\d.]+)/i)?.[1] ?? "",
    effective: meta.match(/effective\s+([0-9][0-9-]{4,})/i)?.[1] ?? "",
    clauses,
  };
}

// Plain-language gloss of TERMS v2.0. Pinned to that version on purpose: when
// the document changes, this stops rendering rather than describing terms that
// no longer exist. Bump both together.
const SUMMARY_FOR_VERSION = "2.0";

const SUMMARY = [
  {
    title: "YOUR OWN TOOL",
    body: "Self-hosted, for the owner's own account. Not a service, broker, or advice.",
    warn: false,
    Icon: ToolIcon,
  },
  {
    title: "NO WARRANTY",
    body: "As is, zero liability. You can lose everything, and every trade is your own action.",
    warn: true,
    Icon: WarnIcon,
  },
  {
    title: "YOUR JURISDICTION",
    body: "You confirm you may use Polymarket where you are — no VPN to get around it.",
    warn: false,
    Icon: GlobeIcon,
  },
] as const;

// ── Small parts ──────────────────────────────────────────────────────────────

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="font-mono text-[9.5px] tracking-[0.18em] text-pixel-gray uppercase">
      {children}
    </div>
  );
}

/** The terms hash, click-to-copy — the thing you'd check the signature against. */
function HashChip({ hash }: { hash: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      onClick={() => {
        navigator.clipboard?.writeText(hash).then(
          () => {
            setCopied(true);
            setTimeout(() => setCopied(false), 1200);
          },
          () => {},
        );
      }}
      title={`SHA-256 of these terms — ${hash}`}
      className="pixel-btn btn-xs normal-case font-mono text-pixel-gray hover:text-pixel-white"
    >
      {copied ? "copied" : `sha256 ${hash.slice(0, 6)}…${hash.slice(-4)}`}
    </button>
  );
}

function TermsSkeleton() {
  return (
    <div className="space-y-2.5" aria-hidden>
      {[92, 78, 96, 64].map((w, i) => (
        <div
          key={i}
          className="h-[9px] rounded-full gate-pulse"
          style={{ width: `${w}%`, background: "var(--border-strong)" }}
        />
      ))}
    </div>
  );
}

function shortAddr(a: string): string {
  return a.length > 12 ? `${a.slice(0, 6)}…${a.slice(-4)}` : a;
}

function Spinner() {
  return (
    <svg className="animate-spin" width="14" height="14" viewBox="0 0 24 24" fill="none">
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeOpacity="0.3" strokeWidth="3" />
      <path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}

function SignIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 20h9" />
      <path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z" />
    </svg>
  );
}

function ToolIcon() {
  return (
    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="4" width="18" height="14" rx="2" />
      <path d="M8 21h8M12 18v3" />
    </svg>
  );
}

function WarnIcon() {
  return (
    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M10.3 3.7 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.7a2 2 0 0 0-3.4 0Z" />
      <path d="M12 9v4M12 17h.01" />
    </svg>
  );
}

function GlobeIcon() {
  return (
    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="9" />
      <path d="M3 12h18M12 3c2.5 2.7 2.5 15.3 0 18-2.5-2.7-2.5-15.3 0-18Z" />
    </svg>
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
