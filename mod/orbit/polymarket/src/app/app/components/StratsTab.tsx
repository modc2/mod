"use client";

// THE STRATS TAB — where strats are BUILT, MANAGED and SHARED.
//
// The side panel's rail used to be INDEX · BACKTEST · LIVE, with the strat
// list squeezed into INDEX between the money and the copy book. That put
// "which strat is my capital on" and "let me author a new strategy" in the
// same 340px block, so building got two buttons and sharing got none. The
// split now:
//
//   INDEX   →  allocation. Whose money, how it's split across strats,
//              deposit/withdraw. (StratBlock, MoneyBlock.)
//   STRATS  →  this tab. Create, fork, rename, delete, chat with, and
//              SHARE strats. Every strat is PRIVATE BY DEFAULT; a per-row
//              toggle publishes it to the community gallery, and the
//              gallery below lets you fork anyone else's back in.
//
// Five sections, top to bottom in the order a user grows into them:
//
//   MY STRATS   the saved list with full management — the one place a strat
//               is renamed, forked, deleted or published. Selecting still
//               sets the ACTIVE strat (BACKTEST/LIVE read it).
//   BUILD       the machines that write strats for you: the AUTO STRAT
//               factory (one agent run invents + benches a recipe) and the
//               STRAT LAB (an agent that iterates until the data clears the
//               confidence bar). Both register their output in MY STRATS.
//   COMMUNITY   every published recipe strat on this deploy — fork one into
//               a private copy you own. Your own published cards show here
//               too so you can see exactly what's public and pull it back.
//   CODE        user-written Strat classes (mod.py) — upload, publish, and
//               the CID share/import path that works across deploys.
//   SCORE       the ▦ SCORE MARKET — this is its ONE home (it used to sit
//               inside the board's ƒ SCORE panel). USE broadcasts the source
//               over the formula bus (scoreFormula.FORMULA_EVENT), so the
//               board's score box adopts it live; + PUBLISH reads whatever
//               that box currently holds, synced back over the same bus.
//
// Publishing a RECIPE strat goes through useStratManager.setVisibility → the
// plaintext /strats/public gallery (stratSync.ts); the local token that
// published is the only credential that can unpublish. CODE strats have their
// own owner-keyed public flag + CID store (UserStratsPanel). Nothing here
// touches allocation: funding lives on INDEX, running lives on LIVE.

import { useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";

import { useAuth } from "../context/AuthContext";
import { DEFAULT_STRATS, orderedTemplates, traderIndexTemplate } from "../lib/defaultStrats";
import { useStratManager } from "../lib/stratManager";
import { useStratStats, useStratPnlHistory, fmtUsd, type StratPnlPoint } from "../lib/stratStats";
import { fetchPublicStrats, type PublicStratEntry } from "../lib/stratSync";
import { FORMULA_EVENT, broadcastFormula, loadSavedFormula } from "../lib/scoreFormula";
import { isTraderIndex } from "../lib/traderIndex";
import { describeTraderFilter } from "../lib/strats/strat";
import { shortAddress } from "../lib/auth";
import AutoStratPanel from "./AutoStratPanel";
import ConfirmDeleteStrat from "./ConfirmDeleteStrat";
import PositionsHistoryPanel from "./PositionsHistoryPanel";
import ScoreMarket from "./ScoreMarket";
import Sparkline from "./Sparkline";
import StratChat from "./StratChat";
import StratLab from "./StratLab";
import UserStratsPanel from "./UserStratsPanel";

function timeSince(ts: number): string {
  const s = Math.floor((Date.now() - ts) / 1000);
  if (s < 120) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

// Hover caption for the 7-day curve's buckets: when the point was sampled.
function curveHover(points: StratPnlPoint[]) {
  return (i: number, _v: number) => {
    const p = points[i];
    if (!p) return "7d live PnL";
    const d = new Date(p.t);
    return `${d.toLocaleDateString(undefined, { month: "short", day: "numeric" })} ${d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}`;
  };
}

function SectionHeader({ label, hint }: { label: string; hint: string }) {
  return (
    <div className="flex items-baseline gap-2 px-1 pt-1">
      <span className="text-[11px] font-semibold tracking-[0.2em] text-pixel-white">{label}</span>
      <span className="text-[9.5px] font-mono text-pixel-gray truncate">{hint}</span>
    </div>
  );
}

export default function StratsTab() {
  const { auth } = useAuth();
  const {
    indexes, activeId, select, fork, forkDefault, rename,
    requestDelete, pendingDelete, confirmDelete, cancelDelete,
    stopStrat, setVisibility, importPublic, broadcast,
  } = useStratManager();
  // Live money stats + running set: cards show deployed capital alongside backtest.
  const { stats: liveStats, running: liveStratIds } = useStratStats();
  // 7-day PnL curves per strat, from the server sidecar's 10-min samples.
  const pnlHistory = useStratPnlHistory();

  // STRATS = the manager (everything below) · TRADES = the account's actual
  // fills as positions, each with its own P&L (PositionsHistoryPanel — the
  // same record the LIVE tab buries under its trades view). The user asked
  // "show me the trades that were made and their pnl" from THIS page, so the
  // record gets a first-class tab here instead of a pointer at LIVE.
  const [view, setView] = useState<"strats" | "trades">("strats");
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  // Held by ID, not by object: the list re-reads every couple of seconds and
  // an open chat must follow the strat's edits rather than pin a stale copy.
  const [chatId, setChatId] = useState<string | null>(null);
  const [shelfOpen, setShelfOpen] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  // Which strat's publish/unpublish call is in flight (per-row spinner-ish).
  const [visBusy, setVisBusy] = useState<string | null>(null);
  const [gallery, setGallery] = useState<PublicStratEntry[]>([]);
  const chatStrat = chatId === null ? null : indexes.find((i) => i.id === chatId) ?? null;

  const commitRename = () => {
    if (renamingId && renameValue.trim()) rename(renamingId, renameValue.trim());
    setRenamingId(null);
  };

  const refreshGallery = useCallback(async () => {
    setGallery(await fetchPublicStrats());
  }, []);

  useEffect(() => {
    void refreshGallery();
  }, [refreshGallery]);

  const toggleVisibility = useCallback(async (id: string, name: string, pub: boolean) => {
    setStatus(null);
    setVisBusy(id);
    const ok = await setVisibility(id, pub);
    setVisBusy(null);
    if (!ok) {
      setStatus(pub
        ? `Couldn't publish "${name}" — sign in first (publishing is keyed to your account so only you can unpublish).`
        : `Couldn't unpublish "${name}".`);
    } else {
      setStatus(pub
        ? `"${name}" is PUBLIC — it's on the community gallery below, anyone can view and fork it.`
        : `"${name}" is private again.`);
    }
    void refreshGallery();
  }, [setVisibility, refreshGallery]);

  const myAddr = auth.address?.toLowerCase() ?? "";

  // The SCORE MARKET's view of the board's score box, over the formula bus:
  // seeded from the shared sessionStorage key, kept live by FORMULA_EVENT
  // (edits typed on the board while this tab is open), and USE/EDIT here
  // broadcasts back so mounted editors adopt the source immediately.
  const [formula, setFormulaState] = useState("");
  useEffect(() => {
    setFormulaState(loadSavedFormula());
    const onFormula = (e: Event) => setFormulaState((e as CustomEvent<string>).detail);
    window.addEventListener(FORMULA_EVENT, onFormula);
    return () => window.removeEventListener(FORMULA_EVENT, onFormula);
  }, []);
  const setFormula = useCallback((f: string) => {
    setFormulaState(f);
    broadcastFormula(f);
  }, []);

  return (
    <div className="p-2 space-y-3">
      {/* ── View switch + the always-visible + ──
          STRATS is the manager below; TRADES is the account's full trading
          record (every position ever held, each with its P&L). The + sits in
          this row because with 16 cards the dashed + NEW STRAT at the list's
          end is below the fold — creating a strat must not require scrolling
          past every existing one. */}
      <div className="flex items-center gap-2 px-1">
        <div className="flex gap-1 border border-pixel-border rounded-full overflow-hidden">
          {([
            ["strats", "STRATS"],
            ["trades", "TRADES"],
          ] as const).map(([v, label]) => (
            <button
              key={v}
              onClick={() => setView(v)}
              className={`px-3 py-1 text-[10px] font-semibold tracking-[0.14em] transition-colors ${
                view === v ? "bg-pixel-border-light text-pixel-white" : "text-pixel-gray hover:bg-pixel-border-light/50"
              }`}
              title={v === "strats" ? "Build, manage and share your strats" : "Every trade this account has made, each with its P&L"}
            >
              {label}
            </button>
          ))}
        </div>
        <button
          onClick={() => { forkDefault(traderIndexTemplate()); setView("strats"); }}
          title="New strat — a TRADER INDEX seeded with this week's best traders. Private until you publish it."
          className="ml-auto shrink-0 px-2.5 py-1 rounded-full border border-green-400/50 text-[10px] font-mono font-semibold tracking-[0.1em] text-green-400 hover:bg-green-400/10 transition-colors"
        >
          + NEW STRAT
        </button>
      </div>

      {/* ── TRADES — the trading record, one row per position with its P&L ── */}
      {view === "trades" && (
        <section className="space-y-1">
          {auth.connected ? (
            <PositionsHistoryPanel />
          ) : (
            <div className="px-1.5 py-2 text-[10.5px] font-mono text-pixel-gray">
              Sign in to see your trades — every position the account has held, with its P&amp;L.
            </div>
          )}
        </section>
      )}

      {view === "strats" && (<>
      <div className="px-1 text-[9.5px] font-mono leading-snug text-pixel-gray">
        Build, manage and share your strats here. Every strat is{" "}
        <span className="text-pixel-white">private by default</span> — publish one to the
        community gallery when it's ready. Money lives on the{" "}
        <span className="text-pixel-white">INDEX</span> tab: allocate there, run on LIVE.
      </div>

      {/* ── INVESTED — just the strats your money is on, nothing else ──
          The full roster below is a management surface; with 16 strats the
          two or three that actually hold capital drown in it. This is the
          plain answer to "where is my money": name · $ in play · PnL. */}
      {(() => {
        const invested = indexes.filter((idx) => {
          const m = liveStats[idx.id];
          return liveStratIds.has(idx.id) || (m != null && (m.moneyIn > 0 || m.openPositions > 0));
        });
        return (
          <section className="space-y-1">
            <SectionHeader label="INVESTED" hint="strats your money is on right now · click = active" />
            {invested.length === 0 ? (
              <div className="px-1.5 py-1 text-[10px] font-mono text-pixel-gray">
                No money on any strat. Deposit on <span className="text-pixel-white">INDEX</span>, then start one on LIVE.
              </div>
            ) : (
              <div className="flex flex-col gap-0.5">
                {invested.map((idx) => {
                  const m = liveStats[idx.id];
                  const isActive = idx.id === activeId;
                  const isRunning = liveStratIds.has(idx.id);
                  const inPlay = m?.openValue ?? 0;
                  const totalPnl = m?.totalPnl ?? 0;
                  const curve = pnlHistory[idx.id];
                  return (
                    <button
                      key={idx.id}
                      onClick={() => select(idx.id)}
                      className={`flex items-center gap-2 rounded-[var(--radius-sm)] px-2.5 py-2 text-left transition-colors ${
                        isActive
                          ? "bg-green-400/[0.07] ring-1 ring-green-400/30"
                          : "hover:bg-pixel-white/[0.04] ring-1 ring-pixel-border/60"
                      }`}
                    >
                      <span
                        className={`w-2 h-2 rounded-full shrink-0 ${
                          isRunning ? "bg-green-400 animate-pulse" : "bg-pixel-gray/50"
                        }`}
                        title={isRunning ? "Trading now" : "Holding positions, engine stopped"}
                      />
                      <span className={`flex-1 min-w-0 truncate text-[12px] font-mono font-semibold ${isActive ? "text-green-400" : "text-pixel-white"}`}>
                        {idx.name}
                      </span>
                      {curve && curve.length >= 2 && (
                        <span className="shrink-0" title="Last 7 days of live PnL">
                          <Sparkline data={curve.map((p) => p.pnl)} width={64} height={18} />
                        </span>
                      )}
                      <span className="shrink-0 text-[11px] font-mono tabular-nums text-pixel-white">
                        {inPlay > 0 ? fmtUsd(inPlay) : "flat"}
                      </span>
                      <span className={`shrink-0 text-[11px] font-mono font-semibold tabular-nums ${totalPnl > 0 ? "text-green-400" : totalPnl < 0 ? "text-red-400" : "text-pixel-gray"}`}>
                        {totalPnl >= 0 ? "+" : ""}{fmtUsd(totalPnl)}
                      </span>
                    </button>
                  );
                })}
              </div>
            )}
          </section>
        );
      })()}

      {/* ── MY STRATS — the management list ── */}
      <section className="space-y-1" style={{ borderTop: "1px solid var(--border)" }}>
        <SectionHeader label="MY STRATS" hint="click = active · live money + last backtest per card" />
        <div className="flex flex-col gap-0.5">
          {indexes.length === 0 && (
            <div className="px-1.5 py-2 text-[10.5px] font-mono text-pixel-gray">
              No strats yet — <span className="text-pixel-gray-light">+ NEW STRAT</span> makes the default one.
            </div>
          )}

          {indexes.map((idx) => {
            const isActive = idx.id === activeId;
            const isRunning = liveStratIds.has(idx.id);
            const isPublic = idx.visibility === "public";
            const indexed = isTraderIndex(idx);
            const money = liveStats[idx.id];
            const pnl24h = money?.pnl24h ?? 0;
            const roi24h = money?.roi24h ?? null;
            const inPlay = money?.openValue ?? 0;
            const openPos = money?.openPositions ?? 0;
            const totalPnl = money?.totalPnl ?? 0;
            const traded = openPos > 0 || (money?.fills ?? 0) > 0;
            const hasBt = idx.lastBacktestAt != null;
            // 7-day live PnL curve (server sidecar). Absent until the strat
            // has traded and the sidecar has sampled — the band then hides.
            const curve = pnlHistory[idx.id];
            const curve7d = curve && curve.length >= 2 ? curve : null;
            return (
              <div
                key={idx.id}
                onClick={() => select(idx.id)}
                className={`relative rounded-[var(--radius-sm)] cursor-pointer transition-colors overflow-hidden ${
                  isActive
                    ? "bg-green-400/[0.07] ring-1 ring-green-400/30"
                    : "hover:bg-pixel-white/[0.04] ring-1 ring-pixel-border/60"
                }`}
                style={{ marginBottom: 2 }}
              >
                {/* Active accent bar */}
                <span
                  className={`absolute left-0 top-0 bottom-0 w-[3px] rounded-l bg-green-400 transition-opacity ${
                    isActive ? "opacity-100 shadow-[0_0_8px_rgba(74,222,128,0.6)]" : "opacity-0"
                  }`}
                />

                {/* ── Card header: name + badges ── */}
                <div className="flex items-center gap-2 px-3 pt-2.5 pb-1">
                  {isRunning && (
                    <span className="w-2 h-2 rounded-full bg-green-400 animate-pulse shrink-0" title="Engine is running for this strat" />
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
                      className={`flex-1 min-w-0 truncate text-[12.5px] font-mono font-semibold ${isActive ? "text-green-400" : "text-pixel-white"}`}
                      title="Double-click to rename"
                    >
                      {idx.name}
                      {idx.identity && (
                        <span className="ml-1.5 text-[9px] tracking-[0.1em] text-cyan-400"> ID</span>
                      )}
                    </span>
                  )}
                  <button
                    onClick={(e) => { e.stopPropagation(); void toggleVisibility(idx.id, idx.name, !isPublic); }}
                    disabled={visBusy === idx.id}
                    className={`shrink-0 px-1.5 py-0.5 rounded border text-[8.5px] font-mono font-semibold tracking-[0.1em] transition-colors ${
                      isPublic
                        ? "border-green-400/60 text-green-400 hover:border-red-400/60 hover:text-red-400"
                        : "border-pixel-border/60 text-pixel-gray hover:border-green-400/60 hover:text-green-400"
                    } ${visBusy === idx.id ? "opacity-40" : ""}`}
                    title={isPublic ? "PUBLIC — click to make private" : "PRIVATE — click to publish"}
                  >
                    {isPublic ? "PUB" : "PRIV"}
                  </button>
                </div>

                {/* Sizing sub-line */}
                <div className="px-3 pb-1.5 text-[10px] font-mono text-pixel-gray truncate">
                  {indexed ? "1:1 · your $ ÷ their $" : "conviction · flow-sized"}
                  {" "}· {idx.traders.length}T
                  {idx.marketQuery?.trim() && (
                    <span className="text-amber-300/70" title={`Copying only markets matching: ${idx.marketQuery.trim()}`}>
                      {" "}· ⌕ {idx.marketQuery.trim()}
                    </span>
                  )}
                  {idx.filter && (
                    <span className="text-cyan-300/70">
                      {" "}· ▼ {describeTraderFilter(idx.filter)}
                    </span>
                  )}
                </div>

                {/* ── 7-day live PnL curve ── */}
                {curve7d && (
                  <div className="mx-3 mb-1.5">
                    <div className="flex items-baseline justify-between">
                      <span className="text-[8.5px] font-semibold tracking-[0.18em] text-pixel-gray">7D PNL</span>
                      <span className={`text-[9px] font-mono tabular-nums ${
                        curve7d[curve7d.length - 1].pnl - curve7d[0].pnl > 0 ? "text-green-400"
                          : curve7d[curve7d.length - 1].pnl - curve7d[0].pnl < 0 ? "text-red-400" : "text-pixel-gray"
                      }`}>
                        {(() => { const d = curve7d[curve7d.length - 1].pnl - curve7d[0].pnl; return `${d >= 0 ? "+" : ""}${fmtUsd(d)}`; })()}
                      </span>
                    </div>
                    <div className="text-pixel-gray">
                      <Sparkline data={curve7d.map((p) => p.pnl)} height={26} stretch hoverLabel={curveHover(curve7d)} />
                    </div>
                  </div>
                )}

                {/* ── Stats grid: LIVE | BACKTEST ── */}
                <div
                  className="grid grid-cols-2 mx-3 mb-2 rounded-[var(--radius-sm)] overflow-hidden"
                  style={{ border: "1px solid var(--border)" }}
                >
                  {/* LIVE column */}
                  <div className="px-2 py-1.5" style={{ borderRight: "1px solid var(--border)" }}>
                    <div className="flex items-center gap-1 mb-1">
                      <span className={`text-[8.5px] font-semibold tracking-[0.18em] ${isRunning ? "text-green-400" : "text-pixel-gray"}`}>
                        LIVE
                      </span>
                      {isRunning && <span className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse" />}
                    </div>
                    {traded ? (
                      <>
                        <div className="text-[11px] font-mono font-semibold text-pixel-white tabular-nums">
                          {openPos > 0 ? fmtUsd(inPlay) : "flat"}
                        </div>
                        <div className={`text-[9.5px] font-mono tabular-nums ${pnl24h > 0 ? "text-green-400" : pnl24h < 0 ? "text-red-400" : "text-pixel-gray"}`}>
                          24h {pnl24h >= 0 ? "+" : ""}{fmtUsd(pnl24h)}
                          {roi24h !== null && ` (${roi24h >= 0 ? "+" : ""}${roi24h.toFixed(1)}%)`}
                        </div>
                        <div className={`text-[9px] font-mono tabular-nums text-pixel-gray`}>
                          total {totalPnl >= 0 ? "+" : ""}{fmtUsd(totalPnl)}
                        </div>
                      </>
                    ) : (
                      <div className="text-[9.5px] font-mono text-pixel-gray/60">
                        {isRunning ? "running · no positions" : "not trading"}
                      </div>
                    )}
                  </div>

                  {/* BACKTEST column */}
                  <div className="px-2 py-1.5">
                    <div className="text-[8.5px] font-semibold tracking-[0.18em] text-pixel-gray mb-1">BACKTEST</div>
                    {hasBt ? (
                      <>
                        <div className={`text-[11px] font-mono font-semibold tabular-nums ${(idx.lastPnl ?? 0) >= 0 ? "text-green-400" : "text-red-400"}`}>
                          {(idx.lastPnl ?? 0) >= 0 ? "+" : ""}{fmtUsd(idx.lastPnl ?? 0)}
                        </div>
                        {idx.lastRoi1k != null && (
                          <div className={`text-[9.5px] font-mono tabular-nums ${idx.lastRoi1k >= 0 ? "text-green-400/80" : "text-red-400/80"}`}>
                            ROI/1k {idx.lastRoi1k >= 0 ? "+" : ""}{fmtUsd(idx.lastRoi1k)}
                          </div>
                        )}
                        <div className="text-[9px] font-mono text-pixel-gray">
                          {idx.lastTradeCount ?? 0} trades
                          {idx.lastBacktestAt && (
                            <span className="text-pixel-gray/60">
                              {" "}· {timeSince(idx.lastBacktestAt)}
                            </span>
                          )}
                        </div>
                      </>
                    ) : (
                      <div className="text-[9.5px] font-mono text-pixel-gray/60">never run</div>
                    )}
                  </div>
                </div>

                {/* ── Action strip ── */}
                <div
                  className="flex items-center gap-0 px-2 pb-1.5"
                  onClick={(e) => e.stopPropagation()}
                >
                  <button
                    onClick={() => setChatId(idx.id)}
                    className="px-2 py-0.5 text-[9px] font-mono font-semibold tracking-[0.08em] text-pixel-gray hover:text-green-400 transition-colors"
                    title={`Chat about "${idx.name}"`}
                  >
                    ASK
                  </button>
                  <span className="text-pixel-border/60 text-[10px]">·</span>
                  <button
                    onClick={() => fork(idx.id)}
                    className="px-2 py-0.5 text-[9px] font-mono font-semibold tracking-[0.08em] text-pixel-gray hover:text-green-400 transition-colors"
                    title={`Fork "${idx.name}" — an independent copy, stopped, un-funded and private`}
                  >
                    FORK
                  </button>
                  <span className="text-pixel-border/60 text-[10px]">·</span>
                  <button
                    onClick={() => { setRenamingId(idx.id); setRenameValue(idx.name); }}
                    className="px-2 py-0.5 text-[9px] font-mono font-semibold tracking-[0.08em] text-pixel-gray hover:text-green-400 transition-colors"
                    title="Rename"
                  >
                    RENAME
                  </button>
                  <span className="ml-auto flex items-center gap-1">
                    {isRunning && (
                      <button
                        onClick={() => void stopStrat(idx.id)}
                        className="px-2 py-0.5 text-[9px] font-mono font-semibold text-pixel-gray hover:text-red-400 transition-colors"
                        title="Stop this strat's engine"
                      >
                        STOP
                      </button>
                    )}
                    <button
                      onClick={() => requestDelete(idx.id)}
                      className="px-2 py-0.5 text-[13px] text-pixel-gray hover:text-red-400 transition-colors leading-none"
                      title="Delete (a published strat comes off the gallery too)"
                    >
                      ×
                    </button>
                  </span>
                </div>
              </div>
            );
          })}

          <button
            onClick={() => forkDefault(traderIndexTemplate())}
            title="New strat — a TRADER INDEX: copies the bench trade for trade, each one scaled by your capital against that trader's book. Seeded with this week's best traders. Private until you publish it."
            className="rounded-[var(--radius-sm)] border border-dashed border-pixel-border px-2 py-1.5 text-left text-[10.5px] font-mono font-semibold tracking-[0.08em] text-pixel-gray hover:text-green-400 hover:border-green-400/60 transition-colors"
          >
            + NEW STRAT
          </button>
        </div>

        {status && (
          <div className="px-1 text-[9.5px] font-mono leading-snug text-pixel-gray-light break-words">{status}</div>
        )}

        {/* Curated starting points, folded — a first-time user meeting eleven
            recipes has not been helped. */}
        <div className="px-1">
          <button
            onClick={() => setShelfOpen((v) => !v)}
            className="text-[9.5px] font-mono tracking-[0.12em] text-pixel-gray hover:text-pixel-white transition-colors"
          >
            {shelfOpen ? "⌃" : "⌄"} MORE RECIPES ({DEFAULT_STRATS.length})
          </button>
        </div>
        {shelfOpen && (
          <div className="flex flex-col gap-0.5">
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
      </section>

      {/* ── BUILD — the machines that write strats for you ── */}
      <section className="space-y-1" style={{ borderTop: "1px solid var(--border)" }}>
        <SectionHeader label="BUILD" hint="agents that invent + bench strats, registered above when they land" />
        {/* The factory: one agent run invents a strat off the live board and
            benches it over 1/3/7 days — on demand or on a loop. */}
        <AutoStratPanel />
        {/* The lab: an agent that finds, backtests and refines a strat until
            the data clears the confidence bar; ADOPT lands it above, paused. */}
        <StratLab />
      </section>

      {/* ── COMMUNITY — the public recipe gallery on this deploy ── */}
      <section className="space-y-1" style={{ borderTop: "1px solid var(--border)" }}>
        <div className="flex items-center gap-2 px-1 pt-1">
          <SectionHeader label="COMMUNITY" hint="published recipe strats — fork one into a private copy you own" />
          <button
            onClick={() => void refreshGallery()}
            className="ml-auto text-[10px] px-1.5 py-0.5 rounded border border-pixel-border text-pixel-gray hover:text-pixel-white transition-colors"
            title="Refresh the gallery"
          >
            ↻
          </button>
        </div>
        {gallery.length === 0 ? (
          <div className="px-1.5 py-1 text-[10px] font-mono text-pixel-gray">
            Nothing published yet. Flip one of your strats to PUBLIC above and it lists here for everyone.
          </div>
        ) : (
          <div className="flex flex-col gap-0.5">
            {gallery.map((entry) => {
              const mine = myAddr !== "" && entry.owner.toLowerCase() === myAddr;
              const localCopy = indexes.find((i) => i.id === entry.strat.id);
              return (
                <div
                  key={entry.id}
                  className="flex items-center gap-2 rounded-[var(--radius-sm)] px-2.5 py-1.5 text-pixel-gray hover:text-pixel-white hover:bg-pixel-white/[0.06] transition-colors"
                >
                  <span className="flex-1 min-w-0">
                    <span className="block truncate text-[11.5px] font-mono font-semibold">
                      {entry.strat.name}
                      {mine && (
                        <span className="ml-1.5 text-[9px] tracking-[0.1em] text-green-400/90">YOURS</span>
                      )}
                    </span>
                    <span className="block truncate text-[9.5px] font-mono text-pixel-gray">
                      by {mine ? "you" : shortAddress(entry.owner)} · {entry.strat.traders?.length ?? 0}T
                      {entry.strat.marketQuery?.trim() ? ` · ⌕ ${entry.strat.marketQuery.trim()}` : ""}
                    </span>
                  </span>
                  {mine && localCopy ? (
                    <button
                      onClick={() => void toggleVisibility(localCopy.id, localCopy.name, false)}
                      className="shrink-0 px-1.5 py-0.5 rounded border border-pixel-border text-[9px] font-mono font-semibold tracking-[0.1em] text-pixel-gray hover:text-red-400 hover:border-red-400/60 transition-colors"
                      title="Take this strat off the gallery"
                    >
                      MAKE PRIVATE
                    </button>
                  ) : (
                    <button
                      onClick={() => importPublic(entry)}
                      className="shrink-0 px-1.5 py-0.5 rounded border border-pixel-border text-[9px] font-mono font-semibold tracking-[0.1em] text-pixel-gray hover:text-green-400 hover:border-green-400/60 transition-colors"
                      title={`Fork "${entry.strat.name}" into your strats — the copy is private, stopped and yours`}
                    >
                      FORK
                    </button>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </section>

      {/* ── CODE — user-written Strat classes, with the CID share path ── */}
      <section className="space-y-1" style={{ borderTop: "1px solid var(--border)" }}>
        <SectionHeader label="CODE STRATS" hint="Python Strat classes — upload, publish, share by CID across deploys" />
        <UserStratsPanel eoa={auth.address ?? undefined} />
      </section>

      {/* ── SCORE — the ▦ SCORE MARKET's one home ── */}
      <section className="space-y-1" style={{ borderTop: "1px solid var(--border)" }}>
        <SectionHeader label="SCORE FUNCTIONS" hint="rank/filter functions for the trader board — USE drops one into its score box" />
        <div className="px-1">
          <ScoreMarket formula={formula} setFormula={setFormula} />
        </div>
      </section>
      </>)}

      <ConfirmDeleteStrat
        name={pendingDelete === null ? null : indexes.find((i) => i.id === pendingDelete)?.name ?? pendingDelete}
        onConfirm={confirmDelete}
        onCancel={cancelDelete}
      />

      {/* Portaled: the TopBar's backdrop-blur makes the header a containing
          block for fixed children, which would clip a modal rendered in place. */}
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
