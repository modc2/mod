"use client";

// THE DEPOSIT PANEL — put money behind several strats in one action.
//
// Funding a strat is arming it: the amount becomes the strat's `capital`, and
// its engine session is started (or reconfigured) to size against that number.
// That has always been a per-strat trip through the LIVE tab, which is fine
// for one strat and wrong for a portfolio — nothing ever showed the SUM of
// what you were committing next to the money you actually have, so eight
// strats each claiming $1000 of a $223 wallet was a thing the console let you
// do without a word.
//
// So this panel is a budget screen first and a form second:
//
//   • WALLET is free USDC in the deposit wallet. Every strat trades through
//     that one wallet — allocations are budgets against it, not transfers, and
//     the panel says so rather than implying money moves.
//   • The budget you may commit is that free cash PLUS the cost basis the
//     SELECTED strats already hold (`fundingBudget`), so re-arming a funded
//     strat at its current size isn't counted as over-allocating.
//   • Over the budget, DEPOSIT is refused. When the balance is UNKNOWN it
//     warns instead: unknown is not zero.
//
// COPY DESK leaders are rows here too. Their dollars live server-side, keyed
// by address rather than by strat id (api/src/copy.rs), and funding one is a
// different pair of calls — but it is the same decision, so it is the same
// list. `lib/multiFund.ts` owns both paths; this file is the screen.

import { useCallback, useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { fetchCopyBook, type CopyBookRow } from "../lib/copyBook";
import {
  depositInto, evenSplit, fundingBudget, usd,
  type DepositOutcome, type FundRow,
} from "../lib/multiFund";
import { fmtUsd, type StratMoney } from "../lib/stratStats";
import type { SavedIndex } from "../lib/types";

interface Props {
  /** Every saved strat, in the order the caller wants them listed. */
  indexes: SavedIndex[];
  /** Per-strat engine money (useStratStats) — the deployed column. */
  stats: Record<string, StratMoney>;
  /** Deposit wallet's free USDC; null = unknown, never render as $0. */
  cash: number | null;
  /** Strat ids with a running engine. */
  running: Set<string>;
  /** Signed-in EOA. Without one there is nothing to fund. */
  eoa: string | null;
  onClose: () => void;
  /** Fired after a deposit that armed at least one row. */
  onDone?: () => void;
}

export default function DepositPanel({ indexes, stats, cash, running, eoa, onClose, onDone }: Props) {
  // COPY DESK allocations, best-effort: the desk is optional, and a console
  // with no book still funds strats. Kept as the book's own rows, not as
  // FundRows — their money comes from `stats`, which arrives on its own clock,
  // and baking it in at fetch time would freeze every desk row at $0.
  const [book, setBook] = useState<CopyBookRow[]>([]);
  const [amounts, setAmounts] = useState<Record<string, string>>({});
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [bulk, setBulk] = useState("");
  const [busy, setBusy] = useState(false);
  const [results, setResults] = useState<DepositOutcome[] | null>(null);

  useEffect(() => {
    if (!eoa) return;
    let cancelled = false;
    fetchCopyBook(eoa)
      .then((b) => { if (!cancelled) setBook(b.allocations); })
      .catch(() => setBook([]));
    return () => { cancelled = true; };
    // Loaded once per open: the list is a picker, and re-ordering it under a
    // half-typed allocation is worse than a slightly stale row.
  }, [eoa]);

  const rows: FundRow[] = useMemo(() => [
    ...indexes.map((idx) => ({
      id: idx.id,
      name: idx.name,
      kind: "strat" as const,
      strat: idx,
      allocated: idx.capital ?? 0,
      deployed: stats[idx.id]?.moneyIn ?? 0,
      running: running.has(idx.id),
    })),
    // The desk's ledger is realized-only, so a desk row's open basis comes
    // from the same engine map the strat rows use — keyed by the desk's
    // derived `copy-<address>` strategyId.
    ...book.map((a) => ({
      id: a.strategyId,
      name: a.name,
      kind: "copy" as const,
      address: a.address,
      allocated: a.allocationUsd,
      deployed: stats[a.strategyId]?.moneyIn ?? 0,
      running: a.live?.running ?? false,
    })),
  ], [indexes, stats, running, book]);

  const selected = useMemo(() => rows.filter((r) => picked.has(r.id)), [rows, picked]);

  /** What a row is being funded with, as a number. Blank/garbage = 0. */
  const amountOf = useCallback((row: FundRow): number => {
    const raw = amounts[row.id];
    if (raw === undefined || raw.trim() === "") {
      // Default: what the CAPITAL PLAN suggested for this strat, else the
      // allocation it already carries. Neither is money — both are a starting
      // point the user can overwrite before anything is committed, which is
      // why the prefill is CAPPED at what this row could actually be funded
      // with. A blank strat carries the $1000 house default, and prefilling
      // that against a $200 wallet would open the panel already refusing.
      const want = usd(row.strat?.suggestedCapital ?? row.allocated ?? 0);
      if (cash === null) return want;
      return Math.min(want, usd(cash + Math.max(0, row.deployed)));
    }
    const n = Number(raw);
    return Number.isFinite(n) && n > 0 ? usd(n) : 0;
  }, [amounts, cash]);

  const total = usd(selected.reduce((s, r) => s + amountOf(r), 0));
  const budget = fundingBudget(cash, selected);
  const over = budget !== null && total > budget + 0.005;
  const emptyRow = selected.some((r) => amountOf(r) <= 0);
  const canDeposit = !!eoa && selected.length > 0 && !emptyRow && !over && !busy;

  const toggle = (id: string) => {
    setPicked((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
    setResults(null);
  };

  /** Split a number evenly across the selected rows, cent-exact. */
  const split = (totalUsd: number) => {
    if (selected.length === 0) return;
    const parts = evenSplit(totalUsd, selected.length);
    setAmounts((prev) => {
      const next = { ...prev };
      selected.forEach((r, i) => { next[r.id] = parts[i].toFixed(2); });
      return next;
    });
    setResults(null);
  };

  const deposit = async () => {
    if (!eoa || !canDeposit) return;
    setBusy(true);
    setResults(null);
    const plan = selected.map((row) => ({ row, amountUsd: amountOf(row) }));
    const out = await depositInto(eoa, plan);
    setBusy(false);
    setResults(out);
    if (out.some((r) => r.ok)) onDone?.();
  };

  const armed = results?.filter((r) => r.ok).length ?? 0;
  const refused = results?.filter((r) => !r.ok) ?? [];

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
        className="relative w-full max-w-[640px] max-h-[86vh] flex flex-col rounded-[var(--radius)] backdrop-blur-md outline-none"
        style={{
          background:
            "linear-gradient(180deg, rgb(var(--pixel-black-rgb)/0.98), rgb(var(--pixel-bg-rgb)/0.96))",
          border: "1px solid var(--border)",
          boxShadow: "0 24px 64px rgba(0,0,0,0.6)",
          animation: "drawer-in-left 0.14s ease-out",
        }}
      >
        {/* ── Header: what you have ── */}
        <div className="shrink-0 flex items-center gap-3 px-4 py-3" style={{ borderBottom: "1px solid var(--border)" }}>
          <span className="text-[12px] font-mono font-bold tracking-[0.18em] text-green-400">DEPOSIT</span>
          <span
            className="flex-1 text-[10.5px] font-mono text-pixel-gray truncate"
            title="Every strat trades through ONE deposit wallet. An allocation is the budget its engine sizes against — no money moves between strats."
          >
            wallet{" "}
            <span className={cash === null ? "" : "text-pixel-white"}>
              {cash === null ? "unknown" : fmtUsd(cash)}
            </span>{" "}
            free · one wallet, one pot
          </span>
          <button
            onClick={onClose}
            className="text-[15px] leading-none text-pixel-gray hover:text-pixel-white shrink-0"
            title="Close"
          >
            ×
          </button>
        </div>

        {/* ── Rows ── */}
        <div className="flex-1 overflow-y-auto p-2">
          {rows.length === 0 && (
            <div className="px-3 py-6 text-center text-[11.5px] font-mono text-pixel-gray">
              No strats yet — fork one from the STRAT HUB, then fund it here.
            </div>
          )}
          {rows.map((row) => {
            const on = picked.has(row.id);
            const amount = amountOf(row);
            return (
              <label
                key={row.id}
                className={`flex items-center gap-2.5 rounded-[var(--radius-sm)] px-3 py-2 cursor-pointer transition-colors ${
                  on ? "bg-green-400/[0.08]" : "hover:bg-pixel-white/[0.05]"
                }`}
              >
                <input
                  type="checkbox"
                  checked={on}
                  onChange={() => toggle(row.id)}
                  className="shrink-0 accent-green-400"
                />
                <span className="flex-1 min-w-0">
                  <span className="flex items-center gap-1.5">
                    {row.running && (
                      <span className="w-2 h-2 rounded-full bg-green-400 animate-pulse shrink-0" title="Engine running" />
                    )}
                    <span className={`truncate text-[12px] font-mono font-semibold ${on ? "text-green-400" : "text-pixel-white"}`}>
                      {row.name}
                    </span>
                    {row.kind === "copy" && (
                      <span
                        className="shrink-0 text-[9px] font-mono tracking-[0.1em] text-cyan-400"
                        title={`COPY DESK allocation — mirrors ${row.address} and is funded server-side`}
                      >
                        COPY
                      </span>
                    )}
                  </span>
                  <span className="block text-[10px] font-mono text-pixel-gray">
                    {row.deployed > 0
                      ? `${fmtUsd(row.deployed)} deployed · `
                      : ""}
                    {row.allocated > 0 ? `allocated ${fmtUsd(row.allocated)}` : "never funded"}
                  </span>
                </span>
                <span className="shrink-0 flex items-center gap-1">
                  <span className={`text-[11px] font-mono ${on ? "text-green-400" : "text-pixel-gray"}`}>$</span>
                  <input
                    type="number"
                    min={0}
                    step="1"
                    value={amounts[row.id] ?? (amount > 0 ? String(amount) : "")}
                    placeholder="0"
                    onChange={(e) => {
                      setAmounts((prev) => ({ ...prev, [row.id]: e.target.value }));
                      setResults(null);
                      if (!picked.has(row.id)) toggle(row.id);
                    }}
                    onClick={(e) => e.stopPropagation()}
                    className={`w-[86px] px-2 py-1 rounded-[var(--radius-sm)] bg-[var(--input-bg)] border text-right text-[11.5px] font-mono tabular-nums focus:outline-none ${
                      on && amount <= 0
                        ? "border-red-400/60 text-red-400"
                        : "border-pixel-border text-pixel-white focus:border-green-400/60"
                    }`}
                  />
                </span>
              </label>
            );
          })}
        </div>

        {/* ── Budget + actions ── */}
        <div className="shrink-0 px-4 py-3 space-y-2" style={{ borderTop: "1px solid var(--border)" }}>
          {/* Split helper — type one number, spread it over what's ticked. */}
          <div className="flex items-center gap-2">
            <span className="text-[10px] font-mono tracking-[0.12em] text-pixel-gray">SPLIT</span>
            <input
              type="number"
              min={0}
              value={bulk}
              placeholder={budget !== null ? budget.toFixed(2) : "total"}
              onChange={(e) => setBulk(e.target.value)}
              className="w-[110px] px-2 py-1 rounded-[var(--radius-sm)] bg-[var(--input-bg)] border border-pixel-border text-right text-[11.5px] font-mono tabular-nums text-pixel-white focus:outline-none focus:border-green-400/60"
            />
            <button
              onClick={() => split(Number(bulk) > 0 ? Number(bulk) : (budget ?? 0))}
              disabled={selected.length === 0}
              title={`Divide this evenly across the ${selected.length} ticked row(s), to the cent`}
              className="px-2.5 py-1 rounded-[var(--radius-sm)] border border-pixel-border text-[10.5px] font-mono font-semibold tracking-[0.08em] text-pixel-gray hover:text-green-400 hover:border-green-400/60 disabled:opacity-40 disabled:hover:text-pixel-gray disabled:hover:border-pixel-border transition-colors"
            >
              EVENLY
            </button>
            {budget !== null && (
              <button
                onClick={() => { setBulk(budget.toFixed(2)); split(budget); }}
                disabled={selected.length === 0 || budget <= 0}
                title="Commit everything the wallet has free (plus what the ticked strats already hold) across the ticked rows"
                className="px-2.5 py-1 rounded-[var(--radius-sm)] border border-pixel-border text-[10.5px] font-mono font-semibold tracking-[0.08em] text-pixel-gray hover:text-green-400 hover:border-green-400/60 disabled:opacity-40 disabled:hover:text-pixel-gray disabled:hover:border-pixel-border transition-colors"
              >
                ALL IN
              </button>
            )}
            <span className="flex-1" />
            <span className="text-[11px] font-mono tabular-nums">
              <span className="text-pixel-gray">{selected.length} selected · </span>
              <span className={over ? "text-red-400 font-semibold" : "text-pixel-white"}>{fmtUsd(total)}</span>
              {budget !== null && <span className="text-pixel-gray"> / {fmtUsd(budget)}</span>}
            </span>
          </div>

          {/* Everything that can make this deposit not do what it looks like. */}
          {over && (
            <div className="px-2.5 py-1.5 rounded-[var(--radius-sm)] border border-red-400/40 text-[10.5px] font-mono text-red-400/90 leading-relaxed">
              {fmtUsd(total)} allocated against {fmtUsd(budget ?? 0)} available. Every strat draws on the
              same wallet, so the overflow wouldn&apos;t buy anything — it would just make each engine
              size mirrors it can&apos;t fill.
            </div>
          )}
          {cash === null && (
            <div className="px-2.5 py-1.5 rounded-[var(--radius-sm)] border border-amber-400/40 text-[10.5px] font-mono text-amber-400/90 leading-relaxed">
              Wallet balance unreadable right now — the total isn&apos;t being checked against anything.
            </div>
          )}
          {cash !== null && cash <= 0 && (
            <div className="px-2.5 py-1.5 rounded-[var(--radius-sm)] border border-amber-400/40 text-[10.5px] font-mono text-amber-400/90 leading-relaxed">
              Trading wallet is empty. These strats will arm and compute mirrors, but nothing fills
              until you deposit USDC into it (LIVE → WALLET).
            </div>
          )}
          {!eoa && (
            <div className="px-2.5 py-1.5 rounded-[var(--radius-sm)] border border-amber-400/40 text-[10.5px] font-mono text-amber-400/90">
              Sign in with a wallet to fund anything.
            </div>
          )}

          {/* What actually happened, per row — a partial success is the normal
              outcome when one strat has no traders or the desk has no signer. */}
          {results && (
            <div className="max-h-[110px] overflow-y-auto space-y-1">
              <div className={`text-[10.5px] font-mono ${armed > 0 ? "text-green-400" : "text-red-400"}`}>
                {armed > 0
                  ? `${armed} strat${armed > 1 ? "s" : ""} armed with real orders${refused.length ? ` · ${refused.length} refused` : ""}`
                  : "Nothing was armed"}
              </div>
              {refused.map((r) => (
                <div key={r.id} className="text-[10px] font-mono text-red-400/85 leading-snug">
                  ✗ {r.name} — {r.error || "refused"}
                </div>
              ))}
            </div>
          )}

          <div className="flex items-center justify-between gap-2">
            <span className="text-[9.5px] font-mono text-pixel-gray/80 leading-snug">
              Depositing arms each strat for REAL orders at its amount.
            </span>
            <div className="flex gap-2 shrink-0">
              <button
                onClick={onClose}
                className="rounded-[var(--radius-sm)] border border-pixel-border px-3 py-1.5 text-[11px] font-mono font-semibold tracking-[0.06em] text-pixel-gray hover:text-pixel-white hover:border-pixel-white/40 transition-colors"
              >
                CLOSE
              </button>
              <button
                onClick={() => void deposit()}
                disabled={!canDeposit}
                title={
                  !eoa ? "Sign in first"
                    : selected.length === 0 ? "Tick the strats to fund"
                      : emptyRow ? "Every ticked strat needs an amount above $0"
                        : over ? "Total exceeds what the wallet has"
                          : `Allocate ${fmtUsd(total)} across ${selected.length} strat(s) and start each one`
                }
                className="rounded-[var(--radius-sm)] border border-green-400/50 bg-green-400/10 px-3 py-1.5 text-[11px] font-mono font-semibold tracking-[0.06em] text-green-400 hover:bg-green-400/20 hover:border-green-400 disabled:opacity-40 disabled:hover:bg-green-400/10 disabled:hover:border-green-400/50 transition-colors"
              >
                {busy ? "ARMING…" : `DEPOSIT ${fmtUsd(total)}`}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>,
    document.body,
  );
}
