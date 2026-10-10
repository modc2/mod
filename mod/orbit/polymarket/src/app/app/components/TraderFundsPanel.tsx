"use client";

// TRADER FUNDS — deposit into / withdraw from ONE copied trader.
//
// The sidebar's DEPOSIT panel is a portfolio budget screen; this is the other
// shape of the same decision, for the strat that copies exactly one trader:
// "put more money behind them" or "take some back". Opened from the $ button
// on a single-trader strat's sidebar row.
//
// Both verbs move the strat's ALLOCATION — the budget its engine session
// sizes mirrors against — through lib/multiFund's `adjustFunding`, which is
// where the asymmetry lives (deposit arms real orders; withdraw preserves the
// session's mode, never resumes a stopped one, and stops it at $0). No USDC
// moves between wallets here: every strat trades the one deposit wallet, and
// money already in open positions stays deployed until those positions exit.
//
// The numbers shown are engine truth where a session exists: the allocation
// comes from `/live/sessions`' config (the number the engine is actually
// budgeting with), not the strat store's `capital` intent, which can trail it.

import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { fetchLiveSessions } from "../lib/liveSessions";
import { adjustFunding, usd, type FundRow } from "../lib/multiFund";
import { fmtUsd, type StratMoney } from "../lib/stratStats";
import type { SavedIndex } from "../lib/types";

interface Props {
  /** The single-trader strat whose money is being moved. */
  strat: SavedIndex;
  /** This strat's engine money (useStratStats row) — the deployed column. */
  money?: StratMoney;
  /** Deposit wallet's free USDC; null = unknown, never treated as $0. */
  cash: number | null;
  /** Engine running for this strat (sidebar's engine-truth set). */
  running: boolean;
  /** Signed-in EOA. */
  eoa: string | null;
  onClose: () => void;
  /** Fired after an adjustment that succeeded. */
  onDone?: () => void;
}

export default function TraderFundsPanel({ strat, money, cash, running, eoa, onClose, onDone }: Props) {
  const [amount, setAmount] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  // Engine truth, loaded on open: the allocation the session is budgeting
  // with and whether it is running. Falls back to the strat store's intent
  // until (or unless) the session answers.
  const [session, setSession] = useState<{ allocated: number; running: boolean } | null>(null);

  useEffect(() => {
    if (!eoa) return;
    let cancelled = false;
    fetchLiveSessions(eoa)
      .then((all) => {
        if (cancelled) return;
        const s = all.find((x) => x.strategyId === strat.id);
        if (s) setSession({ allocated: s.config?.capital ?? strat.capital ?? 0, running: s.running });
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [eoa, strat.id, strat.capital]);

  const trader = strat.identity ?? strat.traders[0]?.address ?? "";
  const traderShort = trader ? `${trader.slice(0, 6)}…${trader.slice(-4)}` : "—";
  const allocated = session?.allocated ?? strat.capital ?? 0;
  const isRunning = session?.running ?? running;
  const deployed = money?.moneyIn ?? 0;
  const free = usd(Math.max(0, allocated - deployed));

  const row: FundRow = useMemo(() => ({
    id: strat.id,
    name: strat.name,
    kind: "strat",
    strat,
    allocated,
    deployed,
    running: isRunning,
  }), [strat, allocated, deployed, isRunning]);

  const amt = (() => {
    const n = Number(amount);
    return Number.isFinite(n) && n > 0 ? usd(n) : 0;
  })();

  // Deposit is checked against free wallet USDC — same refusal DepositPanel
  // makes, same "unknown is not zero" rule when the balance is unreadable.
  const depositOver = cash !== null && amt > cash + 0.005;
  const canDeposit = !!eoa && amt > 0 && !depositOver && !busy;
  // Withdraw can never exceed the allocation; cutting into the deployed basis
  // is allowed (it just stops new buys) and gets a note, not a refusal.
  const withdrawOver = amt > allocated + 0.005;
  const intoDeployed = !withdrawOver && amt > free + 0.005;
  const canWithdraw = !!eoa && amt > 0 && allocated > 0 && !withdrawOver && !busy;

  const act = async (deltaUsd: number) => {
    if (!eoa || busy) return;
    setBusy(true);
    setNote(null);
    const out = await adjustFunding(eoa, row, deltaUsd);
    setBusy(false);
    if (out.ok) {
      setSession({ allocated: out.allocated, running: out.stopped ? false : deltaUsd > 0 ? true : isRunning });
      setAmount("");
      setNote({
        ok: true,
        text:
          deltaUsd > 0
            ? `Deposited ${fmtUsd(deltaUsd)} — copying ${traderShort} with ${fmtUsd(out.allocated)}, real orders armed.`
            : out.stopped
              ? `Withdrew everything — session stopped. Open positions stay on until they exit.`
              : `Withdrew ${fmtUsd(-deltaUsd)} — budget is now ${fmtUsd(out.allocated)}.`,
      });
      onDone?.();
    } else {
      setNote({ ok: false, text: out.error || "the engine refused the change" });
    }
  };

  return createPortal(
    <div className="fixed inset-0 z-[80] grid place-items-center p-4" onClick={onClose}>
      <div className="absolute inset-0" style={{ background: "rgb(var(--pixel-black-rgb)/0.65)" }} />
      <div
        role="dialog"
        aria-modal="true"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => { if (e.key === "Escape") onClose(); }}
        tabIndex={-1}
        ref={(el) => el?.focus()}
        className="relative w-full max-w-[420px] flex flex-col rounded-[var(--radius)] backdrop-blur-md outline-none"
        style={{
          background:
            "linear-gradient(180deg, rgb(var(--pixel-black-rgb)/0.98), rgb(var(--pixel-bg-rgb)/0.96))",
          border: "1px solid var(--border)",
          boxShadow: "0 24px 64px rgba(0,0,0,0.6)",
          animation: "drawer-in-left 0.14s ease-out",
        }}
      >
        {/* ── Header: which trader's money ── */}
        <div className="shrink-0 flex items-center gap-2 px-4 py-3" style={{ borderBottom: "1px solid var(--border)" }}>
          <span className="text-[12px] font-mono font-bold tracking-[0.18em] text-green-400">TRADER FUNDS</span>
          <span className="flex-1 min-w-0 text-[10.5px] font-mono text-pixel-gray truncate" title={trader}>
            {strat.name} · copying <span className="text-pixel-white">{traderShort}</span>
          </span>
          {isRunning && (
            <span className="w-2 h-2 rounded-full bg-green-400 animate-pulse shrink-0" title="Engine running" />
          )}
          <button onClick={onClose} className="text-[15px] leading-none text-pixel-gray hover:text-pixel-white shrink-0" title="Close">
            ×
          </button>
        </div>

        {/* ── The money, in one row of facts ── */}
        <div className="grid grid-cols-4 gap-px px-4 pt-3 pb-2 text-center">
          {[
            ["BUDGET", fmtUsd(allocated), "The allocation this trader's engine session sizes mirrors against"],
            ["DEPLOYED", fmtUsd(deployed), "Cost basis currently in open positions — frees as they exit"],
            ["FREE", fmtUsd(free), "Budget not yet in positions — what a withdrawal comes out of first"],
            ["WALLET", cash === null ? "…" : fmtUsd(cash), "Deposit wallet's free USDC — the pot every strat trades from"],
          ].map(([label, value, tip]) => (
            <span key={label} title={tip}>
              <span className="block text-[9px] font-mono tracking-[0.14em] text-pixel-gray">{label}</span>
              <span className="block text-[12px] font-mono tabular-nums text-pixel-white">{value}</span>
            </span>
          ))}
        </div>

        {/* ── Amount + verbs ── */}
        <div className="px-4 pb-4 space-y-2">
          <div className="flex items-center gap-2">
            <span className="text-[11px] font-mono text-pixel-gray">$</span>
            <input
              type="number"
              min={0}
              step="1"
              value={amount}
              placeholder="0"
              autoFocus
              onChange={(e) => { setAmount(e.target.value); setNote(null); }}
              className="flex-1 px-2 py-1.5 rounded-[var(--radius-sm)] bg-[var(--input-bg)] border border-pixel-border text-right text-[12.5px] font-mono tabular-nums text-pixel-white focus:outline-none focus:border-green-400/60"
            />
            {/* Quick fills: what you could take without touching positions,
                and the whole budget. */}
            {free > 0 && (
              <button
                onClick={() => { setAmount(free.toFixed(2)); setNote(null); }}
                className="px-2 py-1 rounded-[var(--radius-sm)] border border-pixel-border text-[9.5px] font-mono tracking-[0.08em] text-pixel-gray hover:text-pixel-white hover:border-pixel-white/40 transition-colors"
                title={`Fill with the free budget — ${fmtUsd(free)} not in positions`}
              >
                FREE
              </button>
            )}
            {allocated > 0 && (
              <button
                onClick={() => { setAmount(allocated.toFixed(2)); setNote(null); }}
                className="px-2 py-1 rounded-[var(--radius-sm)] border border-pixel-border text-[9.5px] font-mono tracking-[0.08em] text-pixel-gray hover:text-pixel-white hover:border-pixel-white/40 transition-colors"
                title={`Fill with the whole budget — withdrawing it stops the session`}
              >
                ALL
              </button>
            )}
          </div>

          {/* Everything that can make a click not do what it looks like. */}
          {depositOver && (
            <div className="px-2.5 py-1.5 rounded-[var(--radius-sm)] border border-red-400/40 text-[10px] font-mono text-red-400/90 leading-relaxed">
              {fmtUsd(amt)} against {fmtUsd(cash ?? 0)} free in the wallet — the extra budget couldn&apos;t
              buy anything, it would just size mirrors that fail to fill.
            </div>
          )}
          {withdrawOver && amt > 0 && (
            <div className="px-2.5 py-1.5 rounded-[var(--radius-sm)] border border-red-400/40 text-[10px] font-mono text-red-400/90 leading-relaxed">
              Only {fmtUsd(allocated)} is allocated to this trader.
            </div>
          )}
          {intoDeployed && amt > 0 && (
            <div className="px-2.5 py-1.5 rounded-[var(--radius-sm)] border border-amber-400/40 text-[10px] font-mono text-amber-400/90 leading-relaxed">
              {fmtUsd(amt)} is more than the {fmtUsd(free)} free — the budget drops below what&apos;s already
              in positions, so new buys stop and the rest of the cash frees as positions exit.
            </div>
          )}
          {cash === null && (
            <div className="px-2.5 py-1.5 rounded-[var(--radius-sm)] border border-amber-400/40 text-[10px] font-mono text-amber-400/90 leading-relaxed">
              Wallet balance unreadable right now — deposits aren&apos;t being checked against anything.
            </div>
          )}
          {note && (
            <div className={`px-2.5 py-1.5 rounded-[var(--radius-sm)] border text-[10px] font-mono leading-relaxed ${
              note.ok ? "border-green-400/40 text-green-400/90" : "border-red-400/40 text-red-400/90"
            }`}>
              {note.text}
            </div>
          )}

          <div className="flex items-center gap-2">
            <button
              onClick={() => void act(amt)}
              disabled={!canDeposit}
              title={
                !eoa ? "Sign in first"
                  : amt <= 0 ? "Type an amount"
                    : depositOver ? "More than the wallet has free"
                      : `Add ${fmtUsd(amt)} behind ${traderShort} and arm real orders at ${fmtUsd(allocated + amt)}`
              }
              className="flex-1 rounded-[var(--radius-sm)] border border-green-400/50 bg-green-400/10 px-3 py-1.5 text-[11px] font-mono font-semibold tracking-[0.06em] text-green-400 hover:bg-green-400/20 hover:border-green-400 disabled:opacity-40 disabled:hover:bg-green-400/10 disabled:hover:border-green-400/50 transition-colors"
            >
              {busy ? "…" : "DEPOSIT"}
            </button>
            <button
              onClick={() => void act(-amt)}
              disabled={!canWithdraw}
              title={
                !eoa ? "Sign in first"
                  : allocated <= 0 ? "Nothing is allocated to this trader"
                    : amt <= 0 ? "Type an amount"
                      : withdrawOver ? `Only ${fmtUsd(allocated)} allocated`
                        : amt >= allocated - 0.005
                          ? "Withdraw the whole budget — stops this trader's session (positions stay on until they exit)"
                          : `Take ${fmtUsd(amt)} out of this trader's budget, keeping the session's current mode`
              }
              className="flex-1 rounded-[var(--radius-sm)] border border-amber-400/50 bg-amber-400/10 px-3 py-1.5 text-[11px] font-mono font-semibold tracking-[0.06em] text-amber-400 hover:bg-amber-400/20 hover:border-amber-400 disabled:opacity-40 disabled:hover:bg-amber-400/10 disabled:hover:border-amber-400/50 transition-colors"
            >
              {busy ? "…" : "WITHDRAW"}
            </button>
          </div>
          <div className="text-[9.5px] font-mono text-pixel-gray/80 leading-snug">
            Depositing arms this trader for REAL orders at the new budget. Withdrawing only shrinks
            the budget — it never sells positions and never turns a test session live.
          </div>
        </div>
      </div>
    </div>,
    document.body,
  );
}
