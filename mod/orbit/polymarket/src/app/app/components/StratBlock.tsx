"use client";

// STRATS — the saved strategies, in the side panel.
//
// A strat is the console's unit of "how am I copying": a bench of traders, a
// pot of capital, and the sizing model that turns one of their fills into one
// of yours. Every screen already reads the ACTIVE one (Workspace →
// ensureActiveStrat, CopyIndex → getActiveIndexId) and re-reads on the
// `strat-updated` event — but when the old StratSidebar was replaced by
// UserSidebar the LIST went with it, so the console had an active strat and
// no way to see it, name it, or switch off it. This is that list, back where
// the rest of the account lives.
//
// It sits directly above the copy book, and the order is the decision:
//
//   ACCOUNT → MONEY → STRATS → COPY
//   who am I · what can I spend · how do I copy · who do I copy
//
// THE DEFAULT STRAT IS A TRADER INDEX, and each row says so in one line,
// because it is the one thing about a copy strat you cannot afford to guess:
//
//     mirror$ = their$ × yourCapital / theirBankroll
//
// A 1:1 copy of every trade the bench makes, normalized by the two account
// sizes — they put 2% of their book on something, you put 2% of yours. There
// is no per-name dollar amount to set. `sizing: "bankroll"` in the strat, the
// `1:N` on the row is `theirBankroll / yourCapital` resolved by
// lib/traderIndex.ts, and `+ NEW STRAT` forks that recipe rather than minting
// a blank shell (lib/defaultStrats.ts → traderIndexTemplate).
//
// Restraints, same ones the rest of this column keeps:
//
//   • The rows show REAL money — open positions marked to current prices, and
//     the engine's per-strat 24h ledger. A strat's `capital` field is an
//     intention, not funds; rendering it per row made eight idle strats each
//     claim $1,000 of a $223 wallet.
//   • The heavy hooks (useStratManager polls the store + syncs the server,
//     useStratStats polls /live/sessions) only mount while the block is
//     EXPANDED. The collapsed header reads the store once and listens for
//     `strat-updated` — no timer, no request.
//   • Expanded is the DEFAULT, unlike MONEY. Which strategy is running is not
//     a thing you go and look up; it is a thing that should be on screen.

import { useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";

import { useAuth } from "../context/AuthContext";
import { DEFAULT_STRATS, orderedTemplates, traderIndexTemplate } from "../lib/defaultStrats";
import { listStrats } from "../lib/activeStrat";
import { getActiveIndexId } from "../lib/indexStore";
import { useStratManager } from "../lib/stratManager";
import { useStratStats, fmtUsd } from "../lib/stratStats";
import { isTraderIndex } from "../lib/traderIndex";
import { describeTraderFilter } from "../lib/strats/strat";
import ConfirmDeleteStrat from "./ConfirmDeleteStrat";
import DepositPanel from "./DepositPanel";
import StratChat from "./StratChat";

/** Ask the side panel for the STRATS block by name. */
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

  useEffect(() => {
    const onAsk = () => setOpenPersisted(true);
    window.addEventListener(OPEN_STRATS_EVENT, onAsk);
    return () => window.removeEventListener(OPEN_STRATS_EVENT, onAsk);
  }, [setOpenPersisted]);

  return (
    <section style={{ borderTop: "1px solid var(--border)" }}>
      <button
        onClick={() => setOpenPersisted(!open)}
        aria-expanded={open}
        className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-pixel-white/[0.04] transition-colors"
        title="Your saved strategies — the bench, the capital and the sizing model behind every copied trade"
      >
        <span className="text-[11px] font-semibold tracking-[0.2em] text-pixel-white">STRATS</span>
        <span className="ml-auto flex items-center gap-2 min-w-0 shrink">
          {summary.active && (
            <span className="text-[10px] font-mono text-green-400 truncate max-w-[130px]" title={`Active strat: ${summary.active}`}>
              {summary.active}
            </span>
          )}
          <span className="text-[10px] font-mono text-pixel-gray shrink-0" title={`${summary.count} saved strat(s)`}>
            {summary.count} saved
          </span>
          <span className={`text-[9px] text-pixel-gray transition-transform ${open ? "rotate-90" : ""}`}>▶</span>
        </span>
      </button>
      {open && <StratList />}
    </section>
  );
}

/** The list itself. Split out so its polling hooks only exist while open. */
function StratList() {
  const { auth } = useAuth();
  const {
    indexes, activeId, select, fork, forkDefault, rename,
    requestDelete, pendingDelete, confirmDelete, cancelDelete,
    stopStrat, broadcast,
  } = useStratManager();
  const { stats: stratStats, cash, running: liveStratIds } = useStratStats();

  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  // Held by ID, not by object: the list re-reads every couple of seconds and
  // an open chat must follow the strat's edits rather than pin a stale copy.
  const [chatId, setChatId] = useState<string | null>(null);
  const [depositOpen, setDepositOpen] = useState(false);
  const [shelfOpen, setShelfOpen] = useState(false);
  const chatStrat = chatId === null ? null : indexes.find((i) => i.id === chatId) ?? null;

  const commitRename = () => {
    if (renamingId && renameValue.trim()) rename(renamingId, renameValue.trim());
    setRenamingId(null);
  };

  const totalInPlay = indexes.reduce((s, i) => s + (stratStats[i.id]?.openValue ?? 0), 0);

  return (
    <div className="pb-2">
      <div className="px-3 pb-1.5 text-[9.5px] font-mono leading-snug text-pixel-gray">
        Click one to make it the strat that BACKTEST and LIVE are looking at.
      </div>

      <div className="px-1.5 flex flex-col gap-0.5">
        {indexes.length === 0 && (
          <div className="px-1.5 py-2 text-[10.5px] font-mono text-pixel-gray">
            No strats yet — <span className="text-pixel-gray-light">+ NEW STRAT</span> makes the default one.
          </div>
        )}

        {indexes.map((idx) => {
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
          // The sizing model, in the row, in words. `bankroll` (the default)
          // is the 1:1 index: their trade × my capital ÷ their book.
          const indexed = isTraderIndex(idx);
          const capital = Number(idx.capital) || 0;

          return (
            <div
              key={idx.id}
              onClick={() => select(idx.id)}
              className={`relative flex items-center gap-2 rounded-[var(--radius-sm)] px-2.5 py-1.5 text-left cursor-pointer transition-colors ${
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
              {isRunning && (
                <span className="w-2 h-2 rounded-full bg-green-400 animate-pulse shrink-0" title="Engine running for this strat" />
              )}
              {renamingId === idx.id ? (
                <input
                  value={renameValue}
                  onChange={(e) => setRenameValue(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter") commitRename(); if (e.key === "Escape") setRenamingId(null); }}
                  onBlur={commitRename}
                  onClick={(e) => e.stopPropagation()}
                  autoFocus
                  className="flex-1 min-w-0 bg-transparent border-b border-green-400 text-green-400 font-mono text-[12px] outline-none"
                />
              ) : (
                <span
                  onDoubleClick={(e) => { e.stopPropagation(); setRenamingId(idx.id); setRenameValue(idx.name); }}
                  className="flex-1 min-w-0"
                  title="Double-click to rename"
                >
                  <span className="block truncate text-[12px] font-mono font-semibold">
                    {idx.name}
                    {idx.identity && (
                      <span className="ml-1.5 text-[9px] tracking-[0.1em] text-cyan-400" title={`IDENTITY strat — copies exactly one trader: ${idx.identity}`}>
                        ID
                      </span>
                    )}
                  </span>

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
                  </span>

                  {idx.marketQuery?.trim() && (
                    <span
                      className="block truncate text-[10px] font-mono text-amber-300/70"
                      title={`Copying only markets matching: ${idx.marketQuery.trim()}`}
                    >
                      ⌕ {idx.marketQuery.trim()}
                    </span>
                  )}
                  {idx.filter && (
                    <span
                      className="block truncate text-[10px] font-mono text-cyan-300/70"
                      title={`Trader filter — of ${idx.traders.length} watched traders this strat copies only the ${describeTraderFilter(idx.filter)}, re-ranked every scan.`}
                    >
                      ▼ {describeTraderFilter(idx.filter)}
                    </span>
                  )}
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
              )}

              {/* Real money only — open positions at current prices. */}
              <span
                className="shrink-0 font-mono text-[11px] tabular-nums text-pixel-gray-light"
                title={
                  openPositions > 0
                    ? `${idx.name} has ${fmtUsd(inPlay)} in play across ${openPositions} open position(s).`
                    : `${idx.name} holds no positions.`
                }
              >
                {openPositions > 0 ? fmtUsd(inPlay) : <span className="opacity-40">—</span>}
              </span>

              <button
                onClick={(e) => { e.stopPropagation(); setChatId(idx.id); }}
                className="text-[9.5px] font-mono font-semibold tracking-[0.08em] text-pixel-gray hover:text-green-400 shrink-0"
                title={`Chat about "${idx.name}" — ask for a change in words and apply the patch it proposes`}
              >
                ASK
              </button>
              {/* Text, not a glyph: U+2442 has no coverage in the console's
                  font on this host and rendered as tofu. */}
              <button
                onClick={(e) => { e.stopPropagation(); fork(idx.id); }}
                className="text-[9.5px] font-mono font-semibold tracking-[0.08em] text-pixel-gray hover:text-green-400 shrink-0"
                title={`Fork "${idx.name}" — an independent copy, stopped and un-funded`}
              >
                FORK
              </button>
              {isRunning ? (
                <button
                  onClick={(e) => { e.stopPropagation(); void stopStrat(idx.id); }}
                  className="text-[10px] font-mono text-pixel-gray hover:text-red-400 shrink-0"
                  title="Stop this strat's engine — the wallet's other funded strats keep running"
                >
                  ■
                </button>
              ) : (
                <button
                  onClick={(e) => { e.stopPropagation(); setRenamingId(idx.id); setRenameValue(idx.name); }}
                  className="text-[11px] text-pixel-gray hover:text-green-400 shrink-0"
                  title="Rename"
                >
                  ✎
                </button>
              )}
              <button
                onClick={(e) => { e.stopPropagation(); requestDelete(idx.id); }}
                className="text-[13px] text-pixel-gray hover:text-red-400 shrink-0"
                title="Delete"
              >
                ×
              </button>
            </div>
          );
        })}

        {/* ONE primary action. A new strat is the DEFAULT recipe — a trader
            index sized to your capital — not a nameless empty shell. */}
        <div className="flex items-center gap-1 mt-1">
          <button
            onClick={() => forkDefault(traderIndexTemplate())}
            title="New strat — a TRADER INDEX: copies the bench trade for trade, each one scaled by your capital against that trader's book. Seeded with this week's best traders."
            className="flex-1 rounded-[var(--radius-sm)] border border-dashed border-pixel-border px-2 py-1.5 text-left text-[10.5px] font-mono font-semibold tracking-[0.08em] text-pixel-gray hover:text-green-400 hover:border-green-400/60 transition-colors"
          >
            + NEW STRAT
          </button>
          {indexes.length > 0 && (
            <button
              onClick={() => setDepositOpen(true)}
              title="Allocate this wallet across your strats and arm them in one pass"
              className="shrink-0 rounded-[var(--radius-sm)] border border-pixel-border px-2 py-1.5 text-[10.5px] font-mono font-semibold tracking-[0.08em] text-pixel-gray hover:text-green-400 hover:border-green-400/60 transition-colors"
            >
              $ FUND
            </button>
          )}
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

      {/* Curated starting points, folded — a first-time user meeting eleven
          recipes has not been helped. */}
      <div className="px-3 pt-1.5">
        <button
          onClick={() => setShelfOpen((v) => !v)}
          className="text-[9.5px] font-mono tracking-[0.12em] text-pixel-gray hover:text-pixel-white transition-colors"
        >
          {shelfOpen ? "⌃" : "⌄"} MORE RECIPES ({DEFAULT_STRATS.length})
        </button>
      </div>
      {shelfOpen && (
        <div className="px-1.5 pt-1 flex flex-col gap-0.5">
          {orderedTemplates().map((t) => (
            <button
              key={t.slug}
              onClick={() => forkDefault(t)}
              title={`Fork "${t.name}" into your strats`}
              className="group w-full flex items-start gap-2 rounded-[var(--radius-sm)] px-2.5 py-1.5 text-left text-pixel-gray hover:text-pixel-white hover:bg-pixel-white/[0.06] transition-colors"
            >
              <span className="flex-1 min-w-0">
                <span className="block truncate text-[11px] font-mono font-semibold group-hover:text-green-400">
                  {t.name}
                  {t.isDefault && t.slug === traderIndexTemplate().slug && (
                    <span className="ml-1.5 text-[9px] tracking-[0.1em] text-green-400/80">DEFAULT</span>
                  )}
                </span>
                <span className="block text-[9.5px] leading-snug text-pixel-gray/80 line-clamp-3">{t.description}</span>
              </span>
              <span className="text-[9.5px] font-mono font-semibold tracking-[0.08em] shrink-0 mt-0.5 opacity-60 group-hover:opacity-100 group-hover:text-green-400">
                FORK
              </span>
            </button>
          ))}
        </div>
      )}

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

      {/* Portaled for the same reason the column is: the TopBar's
          backdrop-blur makes the header a containing block for fixed
          children, which would clip a modal rendered in place. */}
      {chatStrat && createPortal(
        <StratChat
          strat={chatStrat}
          eoa={auth.address}
          onClose={() => setChatId(null)}
          onApplied={() => broadcast()}
        />,
        document.body,
      )}
    </div>
  );
}
