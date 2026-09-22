"use client";

// ALLOCATION — your money across your strats, in the side panel.
//
// This block used to be the whole strat MANAGER (create/fork/rename/delete,
// recipes, chat) squeezed between MONEY and the copy book. Management moved
// to the side panel's STRATS tab (components/StratsTab.tsx) — building and
// sharing are a workshop, not a drawer. What stays here is the money view of
// the same list, because the INDEX tab's job is allocation:
//
//   ALLOCATION  (this)   — how the wallet is split across strats: what each
//                          one has in play, its 24h P&L, which is ACTIVE
//                          (BACKTEST and LIVE read it), ■ to de-arm one, and
//                          $ ALLOCATE to move money amongst them in one pass
//                          (DepositPanel — deposits AND withdrawals are the
//                          same budget screen). Topping the wallet up and
//                          taking money out is the rail's MONEY tab.
//
// Rules kept from the block this replaces:
//
//   • Rows show REAL money — open positions marked to current prices and the
//     engine's per-strat ledger. The strat's `capital` (its PLAN) shows
//     dimmed and labeled, so idle plans can't read as funds. The ALLOCATION
//     answer per row is: in-play $ when there are positions, else the plan —
//     with a share bar (solid = real money, dim = plan only) so "how is my
//     wallet split" is readable at a glance.
//   • The heavy hooks (useStratManager syncs the store, useStratStats polls
//     /live/sessions) only mount while the block is EXPANDED. The collapsed
//     header reads the store once and listens for `strat-updated`.
//   • Expanded is the DEFAULT, unlike MONEY: which strat is running and how
//     much it holds is a thing that should be on screen.
//   • Light management lives here too (user: "allow me to manage my strats
//     in the sidebar"): the ACTIVE row unlocks FORK · RENAME · × delete, and
//     + NEW forks the trader-index template. The heavy workshop (recipes,
//     publishing, agents, code strats) stays on /strats.

import { useCallback, useEffect, useState } from "react";

import { useAuth } from "../context/AuthContext";
import { listStrats } from "../lib/activeStrat";
import { traderIndexTemplate } from "../lib/defaultStrats";
import { getActiveIndexId } from "../lib/indexStore";
import { useStratManager } from "../lib/stratManager";
import { useStratStats, fmtUsd } from "../lib/stratStats";
import { isTraderIndex } from "../lib/traderIndex";
import ConfirmDeleteStrat from "./ConfirmDeleteStrat";
import DepositPanel from "./DepositPanel";
import TraderFundsPanel from "./TraderFundsPanel";

/** Ask the side panel for the strat manager by name. Handled by UserSidebar,
    which opens the STRATS tab — the block that used to expand here. */
export const OPEN_STRATS_EVENT = "poly-open-strats";

const OPEN_KEY = "poly_strats_open";

export default function StratBlock() {
  const [open, setOpen] = useState(true);
  // Cheap header readout while collapsed: one store read, refreshed on the
  // broadcast every mutation already fires. No poll, no fetch.
  const [summary, setSummary] = useState<{ count: number; active: string | null }>({ count: 0, active: null });

  useEffect(() => {
    try {
      setOpen(localStorage.getItem(OPEN_KEY) !== "0");
    } catch {}
  }, []);

  useEffect(() => {
    const read = () => {
      const all = listStrats();
      const id = getActiveIndexId();
      const active = (id ? all.find((s) => s.id === id) : null) ?? all[0] ?? null;
      setSummary({ count: all.length, active: active?.name ?? null });
    };
    read();
    window.addEventListener("strat-updated", read);
    return () => window.removeEventListener("strat-updated", read);
  }, []);

  const setOpenPersisted = useCallback((next: boolean) => {
    setOpen(next);
    try {
      localStorage.setItem(OPEN_KEY, next ? "1" : "0");
    } catch {}
  }, []);

  return (
    <section style={{ borderTop: "1px solid var(--border)" }}>
      <button
        onClick={() => setOpenPersisted(!open)}
        aria-expanded={open}
        className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-pixel-white/[0.04] transition-colors"
        title="How your wallet is allocated across your strats — each one's share, fund, de-arm, pick the active one, and manage it (fork · rename · delete). The deep workshop lives on the STRATS page."
      >
        <span className="text-[11px] font-semibold tracking-[0.2em] text-pixel-white">ALLOCATION</span>
        <span className="ml-auto flex items-center gap-2 min-w-0 shrink">
          {summary.active && (
            <span className="text-[10px] font-mono text-green-400 truncate max-w-[130px]" title={`Active strat: ${summary.active}`}>
              {summary.active}
            </span>
          )}
          <span className="text-[10px] font-mono text-pixel-gray shrink-0" title={`${summary.count} saved strat(s)`}>
            {summary.count} strats
          </span>
          <span className={`text-[9px] text-pixel-gray transition-transform ${open ? "rotate-90" : ""}`}>▶</span>
        </span>
      </button>
      {open && <AllocationList />}
    </section>
  );
}

/** The list itself. Split out so its polling hooks only exist while open. */
function AllocationList() {
  const { auth } = useAuth();
  const {
    indexes, activeId, select, stopStrat, broadcast,
    fork, forkDefault, rename,
    requestDelete, pendingDelete, confirmDelete, cancelDelete,
  } = useStratManager();
  const { stats: stratStats, cash, running: liveStratIds } = useStratStats();

  const [depositOpen, setDepositOpen] = useState(false);
  // The single-trader DEPOSIT/WITHDRAW screen, keyed by strat id — the list
  // reloads under it, so an open panel follows the strat rather than pinning
  // a stale copy.
  const [fundsId, setFundsId] = useState<string | null>(null);
  const fundsStrat = fundsId === null ? null : indexes.find((i) => i.id === fundsId) ?? null;
  // Inline rename, on the active row's RENAME. Held by id so a list re-read
  // under an open input doesn't detach it from its strat.
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const commitRename = () => {
    if (renamingId && renameValue.trim()) rename(renamingId, renameValue.trim());
    setRenamingId(null);
  };

  const totalInPlay = indexes.reduce((s, i) => s + (stratStats[i.id]?.openValue ?? 0), 0);

  // ALLOCATION per strat: real in-play money when it has positions, else the
  // planned budget ($ ALLOCATE's `capital`). The share bars are drawn off
  // this total — solid when the money is real, dim when it's still a plan.
  const allocOf = (i: (typeof indexes)[number]) => {
    const real = stratStats[i.id]?.openValue ?? 0;
    return real > 0 ? real : Number(i.capital) || 0;
  };
  const totalAlloc = indexes.reduce((s, i) => s + allocOf(i), 0);

  // Money first. With a dozen-plus saved strats, the handful actually
  // running or holding positions are the ones "how much do I have on each"
  // is asking about — they float to the top, store order kept within each
  // group so rows don't shuffle on every poll.
  const holdsMoney = (id: string) => liveStratIds.has(id) || (stratStats[id]?.openValue ?? 0) > 0;
  const ordered = [...indexes.filter((i) => holdsMoney(i.id)), ...indexes.filter((i) => !holdsMoney(i.id))];

  return (
    <div className="pb-2">
      <div className="px-3 pb-1.5 text-[9.5px] font-mono leading-snug text-pixel-gray">
        Your strats. Click one to make it active — BACKTEST and LIVE run the active strat,
        and its row unlocks FORK · RENAME · delete. Bars show where your money sits.
      </div>

      <div className="px-1.5 flex flex-col gap-0.5">
        {indexes.length === 0 && (
          <div className="px-1.5 py-2 text-[10.5px] font-mono text-pixel-gray">
            No strats yet —{" "}
            <button
              onClick={() => window.dispatchEvent(new CustomEvent(OPEN_STRATS_EVENT))}
              className="text-pixel-gray-light underline hover:text-green-400 transition-colors"
            >
              build one on the STRATS tab
            </button>
            .
          </div>
        )}

        {ordered.map((idx) => {
          const isActive = idx.id === activeId;
          const money = stratStats[idx.id];
          const pnl24h = money?.pnl24h ?? 0;
          const roi24h = money?.roi24h ?? null;
          const inPlay = money?.openValue ?? 0;
          const openPositions = money?.openPositions ?? 0;
          const traded = openPositions > 0 || (money?.fills ?? 0) > 0;
          // The engine is the truth about what's running; the strat's own
          // `liveEnabled` flag goes stale when a session is stopped elsewhere.
          const isRunning = liveStratIds.has(idx.id);
          const indexed = isTraderIndex(idx);
          const capital = Number(idx.capital) || 0;
          const alloc = inPlay > 0 ? inPlay : capital;
          const share = totalAlloc > 0 && alloc > 0 ? (alloc / totalAlloc) * 100 : 0;

          return (
            <div
              key={idx.id}
              onClick={() => select(idx.id)}
              className={`relative rounded-[var(--radius-sm)] px-2.5 pt-1.5 pb-2 text-left cursor-pointer transition-colors ${
                isActive
                  ? "text-green-400 bg-green-400/10"
                  : "text-pixel-gray hover:text-pixel-white hover:bg-pixel-white/[0.06]"
              }`}
            >
              <span
                className={`absolute left-0 top-1/2 -translate-y-1/2 w-[3px] rounded-full bg-green-400 transition-all duration-200 ${
                  isActive ? "h-5 opacity-100 shadow-[0_0_10px_rgba(74,222,128,0.7)]" : "h-0 opacity-0"
                }`}
              />
              <div className="flex items-center gap-2">
              {isRunning && (
                <span className="w-2 h-2 rounded-full bg-green-400 animate-pulse shrink-0" title="Engine running for this strat" />
              )}
              <span className="flex-1 min-w-0">
                {renamingId === idx.id ? (
                  <input
                    value={renameValue}
                    onChange={(e) => setRenameValue(e.target.value)}
                    onKeyDown={(e) => { if (e.key === "Enter") commitRename(); if (e.key === "Escape") setRenamingId(null); }}
                    onBlur={commitRename}
                    onClick={(e) => e.stopPropagation()}
                    autoFocus
                    className="block w-full bg-transparent border-b border-green-400 text-green-400 font-mono text-[12px] outline-none"
                  />
                ) : (
                <span className="block truncate text-[12px] font-mono font-semibold">
                  {idx.name}
                  {idx.identity && (
                    <span className="ml-1.5 text-[9px] tracking-[0.1em] text-cyan-400" title={`IDENTITY strat — copies exactly one trader: ${idx.identity}`}>
                      ID
                    </span>
                  )}
                  {idx.visibility === "public" && (
                    <span className="ml-1.5 text-[9px] tracking-[0.1em] text-green-400/90" title="Published to the community gallery — manage on the STRATS tab">
                      PUB
                    </span>
                  )}
                </span>
                )}

                {/* THE SIZING LINE. Every copy strat answers "how big is my
                    version of their trade" and the answer is not a setting
                    you should have to open a panel to see. */}
                <span
                  className={`block truncate text-[10px] font-mono ${indexed ? "text-pixel-gray-light" : "text-amber-300/70"}`}
                  title={
                    indexed
                      ? `1:1 INDEX — every trade of theirs is copied, scaled by your capital against their book:\n\n    mirror$ = their$ × yourCapital / theirBankroll\n\nSo a trade that is 2% of their account becomes 2% of yours. ${capital > 0 ? `This strat plans $${capital.toLocaleString()} across ${idx.traders.length} trader(s); ` : ""}live, the engine divides by what your wallet is actually worth that cycle, not by this number.`
                      : `CONVICTION sizing (sizing: "flow") — your allocation is spread over the capital they DEPLOYED in the window rather than their net worth. Bigger orders on a small account, but you are no longer risking the same fraction of your book that they risked of theirs.`
                  }
                >
                  {indexed ? "1:1 · your $ ÷ their $" : "conviction · flow-sized"}
                  <span className="text-pixel-gray"> · {idx.traders.length}T</span>
                  {/* When the strat is FLAT the right column already prints
                      the plan — only repeat it here when in-play money has
                      taken that column over. */}
                  {capital > 0 && openPositions > 0 && (
                    <span className="text-pixel-gray/70" title={`Planned allocation — the budget $ ALLOCATE set. An intention, not funds; the number on the right is real money.`}>
                      {" "}· plan {fmtUsd(capital)}
                    </span>
                  )}
                </span>

                <span
                  className="block truncate text-[10px] font-mono text-pixel-gray"
                  title={
                    money
                      ? `${fmtUsd(money.moneyIn)} cost basis across ${money.openPositions} open position(s) · total ${money.totalPnl >= 0 ? "+" : ""}${fmtUsd(money.totalPnl)} (realized ${fmtUsd(money.realized)} · unrealized ${fmtUsd(money.unrealized)})`
                      : "No fills from your wallet in this strat yet"
                  }
                >
                  {traded ? (
                    <>
                      {openPositions > 0 ? `${openPositions} pos` : "flat"}
                      {" "}· 24h{" "}
                      <span className={pnl24h > 0 ? "text-green-400" : pnl24h < 0 ? "text-red-400" : ""}>
                        {pnl24h >= 0 ? "+" : ""}{fmtUsd(pnl24h)}
                        {roi24h !== null && ` (${roi24h >= 0 ? "+" : ""}${roi24h.toFixed(1)}%)`}
                      </span>
                    </>
                  ) : isRunning ? (
                    "running · no positions yet"
                  ) : (
                    "not trading"
                  )}
                </span>
              </span>

              {/* THE ALLOCATION COLUMN. Real money when there are positions;
                  the dimmed PLAN when the budget is set but idle; — when
                  neither. The % beneath is this strat's share of the total. */}
              <span className="shrink-0 text-right">
                <span
                  className={`block font-mono text-[11px] tabular-nums ${openPositions > 0 ? "text-pixel-gray-light" : "text-pixel-gray/60"}`}
                  title={
                    openPositions > 0
                      ? `${idx.name} has ${fmtUsd(inPlay)} in play across ${openPositions} open position(s).`
                      : capital > 0
                        ? `${idx.name} holds no positions — ${fmtUsd(capital)} is its planned budget ($ ALLOCATE), not funds in play.`
                        : `${idx.name} holds no positions and has no budget — $ ALLOCATE to give it one.`
                  }
                >
                  {openPositions > 0 ? fmtUsd(inPlay) : capital > 0 ? `plan ${fmtUsd(capital)}` : <span className="opacity-40">—</span>}
                </span>
                {share > 0 && (
                  <span
                    className="block font-mono text-[8.5px] tabular-nums text-pixel-gray"
                    title={`${share.toFixed(1)}% of everything you have allocated across strats (in-play money, or the plan where a strat is flat)`}
                  >
                    {share >= 1 ? share.toFixed(0) : "<1"}%
                  </span>
                )}
              </span>

              {/* A strat that copies exactly ONE trader gets its own money
                  verb pair: deposit into / withdraw from that trader
                  (TraderFundsPanel). Multi-trader strats stay with the
                  $ ALLOCATE screen — "take $50 back from these 12 traders"
                  isn't one decision. */}
              {(idx.traders.length === 1 || idx.identity) && (
                <button
                  onClick={(e) => { e.stopPropagation(); setFundsId(idx.id); }}
                  className="text-[10px] font-mono font-semibold text-pixel-gray hover:text-green-400 shrink-0"
                  title={`Deposit into / withdraw from this trader — move the budget "${idx.name}" copies them with`}
                >
                  $
                </button>
              )}
              {isRunning && (
                <button
                  onClick={(e) => { e.stopPropagation(); void stopStrat(idx.id); }}
                  className="text-[10px] font-mono text-pixel-gray hover:text-red-400 shrink-0"
                  title="Stop this strat's engine — the wallet's other funded strats keep running"
                >
                  ■
                </button>
              )}
              </div>

              {/* THE SHARE BAR — this strat's slice of everything allocated.
                  Solid green = real in-play money; dim = a plan only. */}
              {totalAlloc > 0 && (
                <span className="mt-1 block h-[3px] rounded-full bg-pixel-white/[0.07] overflow-hidden">
                  <span
                    className={`block h-full rounded-full transition-[width] duration-300 ${
                      inPlay > 0 ? "bg-green-400/80" : "bg-pixel-gray/40"
                    }`}
                    style={{ width: `${Math.max(share, alloc > 0 ? 2 : 0)}%` }}
                  />
                </span>
              )}

              {/* THE MANAGEMENT STRIP — active row only, so the list stays a
                  list. FORK/RENAME/× come from the same useStratManager the
                  /strats workshop uses; deep work (publish, agents, code)
                  keeps living there via BUILD & SHARE. */}
              {isActive && renamingId !== idx.id && (
                <div className="mt-1 flex items-center gap-0" onClick={(e) => e.stopPropagation()}>
                  <button
                    onClick={() => fork(idx.id)}
                    className="px-1.5 py-0.5 text-[9px] font-mono font-semibold tracking-[0.08em] text-pixel-gray hover:text-green-400 transition-colors"
                    title={`Fork "${idx.name}" — an independent copy, stopped, un-funded and private`}
                  >
                    FORK
                  </button>
                  <span className="text-pixel-border/60 text-[10px]">·</span>
                  <button
                    onClick={() => { setRenamingId(idx.id); setRenameValue(idx.name); }}
                    className="px-1.5 py-0.5 text-[9px] font-mono font-semibold tracking-[0.08em] text-pixel-gray hover:text-green-400 transition-colors"
                    title="Rename this strat"
                  >
                    RENAME
                  </button>
                  <button
                    onClick={() => requestDelete(idx.id)}
                    className="ml-auto px-1.5 py-0.5 text-[13px] leading-none text-pixel-gray hover:text-red-400 transition-colors"
                    title="Delete this strat (a published strat comes off the gallery too)"
                  >
                    ×
                  </button>
                </div>
              )}
            </div>
          );
        })}

        {/* The allocation actions. Moving money amongst strats is ONE screen
            (DepositPanel budgets the wallet across every row, deposits and
            withdrawals alike); + NEW forks the trader-index template right
            here (never create() — templates seed properly); the deep
            workshop (recipes, publishing, agents, code strats) stays on
            /strats behind BUILD & SHARE. */}
        <div className="flex items-center gap-1 mt-1">
          {indexes.length > 0 && (
            <button
              onClick={() => setDepositOpen(true)}
              title="Allocate this wallet across your strats — raise, lower or zero each one's budget and arm them in one pass"
              className="flex-1 rounded-[var(--radius-sm)] border border-pixel-border px-2 py-1.5 text-left text-[10.5px] font-mono font-semibold tracking-[0.08em] text-pixel-gray hover:text-green-400 hover:border-green-400/60 transition-colors"
            >
              $ ALLOCATE
            </button>
          )}
          <button
            onClick={() => forkDefault(traderIndexTemplate())}
            title="New strat — a TRADER INDEX seeded with this week's best traders. Private until you publish it on the STRATS page."
            className="shrink-0 rounded-[var(--radius-sm)] border border-green-400/50 px-2 py-1.5 text-[10.5px] font-mono font-semibold tracking-[0.08em] text-green-400 hover:bg-green-400/10 transition-colors"
          >
            + NEW
          </button>
          <button
            onClick={() => window.dispatchEvent(new CustomEvent(OPEN_STRATS_EVENT))}
            title="Open the STRATS tab — create, fork, build and share strats (private by default, publishable)"
            className={`${indexes.length > 0 ? "shrink-0" : "flex-1 text-left"} rounded-[var(--radius-sm)] border border-dashed border-pixel-border px-2 py-1.5 text-[10.5px] font-mono font-semibold tracking-[0.08em] text-pixel-gray hover:text-green-400 hover:border-green-400/60 transition-colors`}
          >
            BUILD &amp; SHARE →
          </button>
        </div>
      </div>

      {/* The wallet reconciliation — real money, across every strat. */}
      <div className="mt-2 px-3 pt-1.5 flex items-center justify-between text-[10px] font-mono" style={{ borderTop: "1px solid var(--border)" }}>
        <span className="text-pixel-gray tracking-[0.14em]">IN PLAY</span>
        <span className="text-pixel-white tabular-nums" title="Open positions across all strats, marked to current prices">
          {fmtUsd(totalInPlay)}
          {cash !== null && <span className="text-pixel-gray"> · {fmtUsd(cash)} free</span>}
        </span>
      </div>

      {/* Deposit into / withdraw from ONE copied trader — the single-trader
          counterpart of DepositPanel below. Portals itself. */}
      {fundsStrat && (
        <TraderFundsPanel
          strat={fundsStrat}
          money={stratStats[fundsStrat.id]}
          cash={cash}
          running={liveStratIds.has(fundsStrat.id)}
          eoa={auth.address}
          onClose={() => setFundsId(null)}
          onDone={broadcast}
        />
      )}

      {/* Delete confirmation — portals itself above the column. */}
      <ConfirmDeleteStrat
        name={pendingDelete === null ? null : indexes.find((i) => i.id === pendingDelete)?.name ?? pendingDelete}
        onConfirm={confirmDelete}
        onCancel={cancelDelete}
      />

      {depositOpen && (
        <DepositPanel
          indexes={indexes}
          stats={stratStats}
          cash={cash}
          running={liveStratIds}
          eoa={auth.address}
          onClose={() => setDepositOpen(false)}
          onDone={broadcast}
        />
      )}
    </div>
  );
}
