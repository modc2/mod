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
// The sections used to stack in one long scroll; 2026-10-06 ("need to have
// tabs of this") they became sub-tabs (?sec=, lib/stratsNav.ts). The
// sub-tabs, in the order a user grows into them:
//
//   INVESTED    just the strats money is on — name · $ in play · PnL · STOP.
//               Pinned above the strip at first; same day, "can we have the
//               invested be a tab", so it leads the strip instead.
//   MY STRATS   the saved list with full management — the one place a strat
//               is renamed, forked, deleted or published. Selecting still
//               sets the ACTIVE strat (BACKTEST/LIVE read it). The recipe
//               shelf and the dashed new-strat tiles live here too.
//   SCORES      SCORE STRATS (each score function as a TOP-N copy strat,
//               ranked + backtested out of sample) and the ▦ SCORE MARKET —
//               its ONE home (it used to sit inside the board's ƒ SCORE
//               panel). USE broadcasts the source over the formula bus
//               (scoreFormula.FORMULA_EVENT), so the board's score box
//               adopts it live; + PUBLISH reads whatever that box currently
//               holds, synced back over the same bus.
//   BUILD       the machines that write strats for you: VIBE (words in,
//               backtest out), the AUTO STRAT factory (one agent run invents
//               + benches a recipe) and the STRAT LAB (an agent that iterates
//               until the data clears the confidence bar). Both agents
//               register their output in MY STRATS.
//   COMMUNITY   every published recipe strat on this deploy — fork one into
//               a private copy you own. Your own published cards show here
//               too so you can see exactly what's public and pull it back.
//   CODE        user-written Strat classes (strat.py / strat.rs / strat.ts)
//               — upload, publish, and the CID share/import path that works
//               across deploys. The header row's ⇪ UPLOAD and the grid's
//               UPLOAD CODE tile both feed this store.
//
// Publishing a RECIPE strat goes through useStratManager.setVisibility → the
// plaintext /strats/public gallery (stratSync.ts); the local token that
// published is the only credential that can unpublish. CODE strats have their
// own owner-keyed public flag + CID store (UserStratsPanel). Nothing here
// touches allocation: funding lives on INDEX, running lives on LIVE.

import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useRouter, useSearchParams } from "next/navigation";

import { useAuth } from "../context/AuthContext";
import { DEFAULT_STRATS, orderedTemplates, traderIndexTemplate } from "../lib/defaultStrats";
import { useStratManager } from "../lib/stratManager";
import { useStratStats, useStratPnlHistory, fmtUsd, moneyOnStrat, sessionLabel, type StratPnlPoint } from "../lib/stratStats";
import { fetchPublicStrats, type PublicStratEntry } from "../lib/stratSync";
import { FORMULA_EVENT, broadcastFormula, loadSavedFormula } from "../lib/scoreFormula";
import { isTraderIndex } from "../lib/traderIndex";
import { describeTraderFilter } from "../lib/strats/strat";
import { shortAddress } from "../lib/auth";
import { isStratsSection, isStratsView, stratsHref, type StratsSection, type StratsView } from "../lib/stratsNav";
import AccountsPanel from "./AccountsPanel";
import AutoStratPanel from "./AutoStratPanel";
import ConfirmDeleteStrat from "./ConfirmDeleteStrat";
import CopyPanel from "./CopyPanel";
import MoneyTab from "./MoneyBlock";
import SelectionTray from "./SelectionTray";
import Workspace from "./Workspace";
import PositionsHistoryPanel from "./PositionsHistoryPanel";
import ScoreMarket from "./ScoreMarket";
import ScoreStratsPanel from "./ScoreStratsPanel";
import Sparkline from "./Sparkline";
import StratChat from "./StratChat";
import StratLab from "./StratLab";
import StratVibe, { focusVibe } from "./StratVibe";
import UserStratsPanel, { USER_STRATS_CHANGED_EVENT } from "./UserStratsPanel";
import WindowStrip from "./WindowStrip";
import { HUB_WINDOWS, useStratWindows } from "../lib/hubBacktest";
import { computeStratVerdict, type StratVerdict, type VerdictTier } from "../lib/stratVerdict";

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

// The STRATS view's own sub-tabs — the old one-page scroll, cut into rooms
// (?sec=, lib/stratsNav.ts — the URL is the state, same as ?tab=). Inactive
// sections stay MOUNTED, css-hidden — ScoreStratsPanel's re-rank loop and
// the other panels' background fetches must keep running off-screen
// (unmounting StratHub is how the ladder manifest went stale).
const SECTION_TABS: [StratsSection, string, string][] = [
  ["invested", "INVESTED", "Where your money is right now — every strat holding capital, with its P&L"],
  ["mine", "MY STRATS", "Every strat you saved — rename, fork, publish, delete · click a card = active"],
  ["scores", "SCORES", "Score strats (each score function copies its top N) + score functions for the trader board"],
  ["build", "BUILD", "Machines that write strats — vibe one from words, or let an agent invent and refine one"],
  ["community", "COMMUNITY", "Published strats on this deploy — fork one into a private copy you own"],
  ["code", "CODE", "Your own strat.py / strat.rs / strat.ts — upload, publish, share by CID"],
];

const VIEW_TABS: [StratsView, string, string][] = [
  ["strats", "STRATS", "Build, manage and share your strats"],
  ["copy", "COPY", "Who you copy, with how much — start / stop"],
  ["money", "MONEY", "Your account and liquidity — top up, take out, bridge"],
  ["backtest", "BACKTEST", "Replay the active strat on history — simulated money, no wallet touched"],
  ["live", "LIVE", "Run the active strat against the real book with real money"],
  ["trades", "TRADES", "Every trade this account has made, each with its P&L"],
];

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
    stopStrat, setVisibility, importPublic, broadcast, adoptSession,
  } = useStratManager();
  // Live money stats + running set: cards show deployed capital alongside backtest.
  const { stats: liveStats, running: liveStratIds, sessions: liveSessions, cash } = useStratStats();
  const moneyOn = (id: string) => moneyOnStrat(liveStats[id], liveStratIds.has(id));
  // 7-day PnL curves per strat, from the server sidecar's 10-min samples.
  const pnlHistory = useStratPnlHistory();
  // Every strat backtested over 1/3/7/14/30 days (the worker's ladder).
  const ladder = useStratWindows(indexes);

  // ── The verdict layer: which of these is actually working? ──
  // One tier per strat, folded from the ladder + the live book + the holdout
  // check (lib/stratVerdict.ts). MY STRATS defaults to showing ONLY the
  // consistent ones ("i only have time to see the strats that are doing well
  // and are consistently good") — ALL is one click away, and hidden strats
  // that hold money are called out so the filter can never bury a bleed.
  const [stratShow, setStratShowState] = useState<"working" | "all">("working");
  useEffect(() => {
    try {
      const v = localStorage.getItem("poly_strats_show");
      if (v === "all" || v === "working") setStratShowState(v);
    } catch {}
  }, []);
  const setStratShow = useCallback((v: "working" | "all") => {
    setStratShowState(v);
    try { localStorage.setItem("poly_strats_show", v); } catch {}
  }, []);
  const verdictById: Record<string, StratVerdict> = {};
  for (const idx of indexes) {
    // The card's saved backtest is evidence too — and when it was an OOS run
    // (score-fn strats), it carries holdout weight in the verdict.
    const last = idx.lastBacktestAt != null
      ? {
          pnl: idx.lastPnl ?? 0,
          trades: idx.lastTradeCount ?? 0,
          oos: !!(idx.scoreFn?.oos && idx.lastBacktestAt === idx.scoreFn.oos.at),
        }
      : null;
    verdictById[idx.id] = computeStratVerdict(ladder.byId[idx.id], liveStats[idx.id], last);
  }
  // Best first: tier, then evidence quality, then money as the tiebreak.
  const sortedStrats = [...indexes].sort((a, b) =>
    (verdictById[b.id].score - verdictById[a.id].score) || (moneyOn(b.id) - moneyOn(a.id)));
  const workingStrats = sortedStrats.filter((i) => verdictById[i.id].tier === "consistent");
  const shownStrats = stratShow === "working" ? workingStrats : sortedStrats;
  // Money the WORKING filter would hide — never let "doing well only" mean
  // "didn't notice the funded strat that's bleeding".
  const hiddenFunded = stratShow === "working"
    ? sortedStrats.filter((i) => verdictById[i.id].tier !== "consistent" && (moneyOn(i.id) > 0 || liveStratIds.has(i.id)))
    : [];
  const hiddenMoney = hiddenFunded.reduce((t, i) => t + moneyOn(i.id), 0);

  // STRATS = the manager (everything below) · TRADES = the account's actual
  // fills as positions, each with its own P&L (PositionsHistoryPanel — the
  // same record the LIVE tab buries under its trades view). The user asked
  // "show me the trades that were made and their pnl" from THIS page, so the
  // record gets a first-class tab here instead of a pointer at LIVE.
  //
  // 2026-10-01 the right-hand side panel was removed ("too complicated") and
  // its tabs moved in here: COPY (the copy book + the finder's shortlist),
  // MONEY (account + liquidity), BACKTEST and LIVE (the workspace). The URL
  // (?tab=) is the state — see lib/stratsNav.ts.
  const router = useRouter();
  const params = useSearchParams();
  const tabParam = params?.get("tab");
  const view: StratsView = isStratsView(tabParam) ? tabParam : "strats";
  const setView = useCallback((v: StratsView) => router.replace(stratsHref(v), { scroll: false }), [router]);
  // Which room of the STRATS view is showing (?sec=) — same contract as ?tab=:
  // the URL is the state, so a section is linkable and the back button works.
  const secParam = params?.get("sec");
  const sec: StratsSection = isStratsSection(secParam) ? secParam : "mine";
  const setSec = useCallback((s: StratsSection) => router.replace(stratsHref("strats", s), { scroll: false }), [router]);
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

  // ── Upload a code strat (strat.py / strat.rs / strat.ts) from this tab ──
  // The file lands in the CODE STRATS store below (private, owned by the
  // connected wallet); the kind comes from the extension, the id from the
  // filename. USER_STRATS_CHANGED_EVENT tells UserStratsPanel to re-read.
  const uploadRef = useRef<HTMLInputElement | null>(null);
  const [uploading, setUploading] = useState(false);
  const handleStratFile = useCallback(async (file: File) => {
    setStatus(null);
    const ext = /\.(py|rs|ts)$/i.exec(file.name)?.[1]?.toLowerCase();
    if (!ext) {
      setStatus(`"${file.name}" isn't a strat file — upload a strat.py, strat.rs or strat.ts.`);
      return;
    }
    const id = (file.name.replace(/\.(py|rs|ts)$/i, "").replace(/[^a-zA-Z0-9_-]/g, "_").slice(0, 64)) || "strat";
    setUploading(true);
    try {
      const content = await file.text();
      const r = await fetch("/polymarket/api/user-strats", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ id, kind: ext, content, owner: myAddr, title: id, description: "", public: false }),
      });
      if (!r.ok) {
        const text = await r.text();
        let detail = text.slice(0, 200);
        try {
          const j = JSON.parse(text) as { error?: string };
          if (j.error) detail = j.error.slice(0, 200);
        } catch {}
        throw new Error(detail);
      }
      setStatus(`Uploaded "${id}.${ext}" — it's here under CODE STRATS, private and yours. Publish or SHARE it from this list.`);
      window.dispatchEvent(new Event(USER_STRATS_CHANGED_EVENT));
      setSec("code");
    } catch (e) {
      setStatus(`Upload failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setUploading(false);
      if (uploadRef.current) uploadRef.current.value = "";
    }
  }, [myAddr, setSec]);

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
      <div className="flex flex-wrap items-center gap-2 px-1">
        <div className="flex gap-1 border border-pixel-border rounded-full overflow-hidden">
          {VIEW_TABS.map(([v, label, hint]) => (
            <button
              key={v}
              onClick={() => setView(v)}
              className={`px-3 py-1 text-[10px] font-semibold tracking-[0.14em] transition-colors ${
                view === v
                  ? v === "live" ? "bg-red-400/15 text-red-400" : "bg-pixel-border-light text-pixel-white"
                  : "text-pixel-gray hover:bg-pixel-border-light/50"
              }`}
              title={hint}
            >
              {label}
            </button>
          ))}
        </div>
        {view === "strats" && (<>
        <input
          ref={uploadRef}
          type="file"
          accept=".py,.rs,.ts"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) void handleStratFile(f);
          }}
        />
        <button
          onClick={() => uploadRef.current?.click()}
          disabled={uploading}
          title="Add a strat from code — upload a strat.py, strat.rs or strat.ts. It lands under CODE STRATS, private until you publish it."
          className={`ml-auto shrink-0 px-2.5 py-1 rounded-full border border-pixel-border text-[10px] font-mono font-semibold tracking-[0.1em] text-pixel-gray hover:text-green-400 hover:border-green-400/50 transition-colors ${uploading ? "opacity-40" : ""}`}
        >
          {uploading ? "UPLOADING…" : "⇪ UPLOAD"}
        </button>
        <button
          onClick={() => { setSec("build"); focusVibe(); }}
          title="Vibecode a strat — describe it in plain words, an agent writes the params and backtests them over 1/3/7 days. Opens the BUILD tab's VIBE box."
          className="shrink-0 px-2.5 py-1 rounded-full border border-pixel-border text-[10px] font-mono font-semibold tracking-[0.1em] text-pixel-gray hover:text-green-400 hover:border-green-400/50 transition-colors"
        >
          ✧ VIBE
        </button>
        <button
          onClick={() => { forkDefault(traderIndexTemplate()); setSec("mine"); }}
          title="New strat from the default COPY TRADING template — a TRADER INDEX seeded with this week's best traders, every trade scaled to your capital. Private until you publish it."
          className="shrink-0 px-2.5 py-1 rounded-full border border-green-400/50 text-[10px] font-mono font-semibold tracking-[0.1em] text-green-400 hover:bg-green-400/10 transition-colors"
        >
          + NEW STRAT
        </button>
        </>)}
      </div>

      {/* ── COPY / MONEY / BACKTEST / LIVE — what the side panel used to hold ── */}
      {view === "copy" && (
        <section className="rounded-[var(--radius-sm)] border border-pixel-border overflow-hidden">
          {/* The finder's checked shortlist (renders nothing when empty),
              then the copy book: who, how much, start / stop. */}
          <SelectionTray />
          <CopyPanel />
        </section>
      )}
      {view === "money" && (
        <section className="rounded-[var(--radius-sm)] border border-pixel-border overflow-hidden">
          <AccountsPanel />
          <MoneyTab />
        </section>
      )}
      {(view === "backtest" || view === "live") && (
        /* Keyed so a half-run replay never leaks into LIVE and back. */
        <Workspace key={view} mode={view === "live" ? "LIVE" : "BACKTEST"} bare />
      )}

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
        community gallery when it&apos;s ready. Fund on{" "}
        <span className="text-pixel-white">MONEY</span>, pick who to copy on{" "}
        <span className="text-pixel-white">COPY</span>, run on <span className="text-pixel-white">LIVE</span>.
      </div>

      {/* ── The sub-tab strip — the manager's rooms (?sec=) ──
          Underline style, not pills: the pill row above switches VIEWS, this
          switches rooms within one. The sections below stay MOUNTED and hide
          with CSS — ScoreStratsPanel's re-rank loop, the gallery refresh and
          the ladder polling must keep running off-screen (unmounting is how
          the ladder manifest went stale, see polymarket_window_ladder). */}
      <div className="flex flex-wrap items-center px-1 border-b border-pixel-border/60">
        {SECTION_TABS.map(([s, label, hint]) => (
          <button
            key={s}
            onClick={() => setSec(s)}
            title={hint}
            className={`px-2.5 py-1.5 -mb-px border-b-2 text-[10px] font-mono font-semibold tracking-[0.12em] transition-colors ${
              sec === s
                ? "border-green-400 text-green-400"
                : "border-transparent text-pixel-gray hover:text-pixel-white"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {/* Feedback from uploads / publish toggles — above the sections, so it
          is visible no matter which room the action landed the user in. */}
      {status && (
        <div className="px-1 text-[9.5px] font-mono leading-snug text-pixel-gray-light break-words">{status}</div>
      )}

      {/* ── INVESTED — just the strats your money is on, nothing else ──
          The full roster on MY STRATS is a management surface; with 16 strats
          the two or three that actually hold capital drown in it. This is the
          plain answer to "where is my money": name · $ in play · PnL. */}
      {(() => {
        // Every dollar, not just the dollars with a local card: sessions the
        // engine runs for strats this browser never saved (WHO I COPY rows,
        // another device) are listed by their session label.
        type Row = { id: string; name: string; local: boolean; running: boolean; executing: boolean; money: number };
        const known = new Set(indexes.map((i) => i.id));
        const rows: Row[] = [
          ...indexes.map((idx) => ({
            id: idx.id, name: idx.name, local: true,
            running: liveStratIds.has(idx.id),
            executing: liveSessions[idx.id]?.executing ?? false,
            money: moneyOn(idx.id),
          })),
          ...Object.values(liveSessions).filter((ss) => !known.has(ss.strategyId)).map((ss) => ({
            id: ss.strategyId, name: sessionLabel(ss), local: false,
            running: ss.running, executing: ss.executing,
            money: moneyOn(ss.strategyId),
          })),
        ].filter((r) => r.running || r.money > 0)
          .sort((a, b) => b.money - a.money);
        const total = rows.reduce((t, r) => t + r.money, 0);
        return (
          <section className={`space-y-1 ${sec === "invested" ? "" : "hidden"}`}>
            <div className="flex items-baseline justify-between gap-2">
              <SectionHeader label="INVESTED" hint="where your money is right now" />
              {rows.length > 0 && (
                <span className="shrink-0 text-[10.5px] font-mono tabular-nums text-pixel-gray">
                  <span className="text-pixel-white font-semibold">{fmtUsd(total)}</span> on {rows.length} strat{rows.length === 1 ? "" : "s"}
                  {cash !== null && <> · wallet {fmtUsd(cash)}</>}
                </span>
              )}
            </div>
            {rows.length === 0 ? (
              <div className="px-1.5 py-1 text-[10px] font-mono text-pixel-gray">
                No money on any strat. Deposit on <span className="text-pixel-white">MONEY</span>, then start one on LIVE.
              </div>
            ) : (
              <div className="flex flex-col gap-0.5">
                {rows.map((r) => {
                  const m = liveStats[r.id];
                  const isActive = r.id === activeId;
                  const totalPnl = m?.totalPnl ?? 0;
                  const deployed = m?.moneyIn ?? 0;
                  const curve = pnlHistory[r.id];
                  return (
                    <div
                      key={r.id}
                      onClick={() => r.local && select(r.id)}
                      className={`flex items-center gap-2 rounded-[var(--radius-sm)] px-2.5 py-2 text-left transition-colors ${
                        r.local ? "cursor-pointer" : ""
                      } ${
                        isActive
                          ? "bg-green-400/[0.07] ring-1 ring-green-400/30"
                          : "hover:bg-pixel-white/[0.04] ring-1 ring-pixel-border/60"
                      }`}
                    >
                      <span
                        className={`w-2 h-2 rounded-full shrink-0 ${
                          r.running ? "bg-green-400 animate-pulse" : "bg-pixel-gray/50"
                        }`}
                        title={r.running ? "Trading now" : "Holding positions, engine stopped"}
                      />
                      <span className={`flex-1 min-w-0 truncate text-[12px] font-mono font-semibold ${isActive ? "text-green-400" : "text-pixel-white"}`}>
                        {r.name}
                        {r.running && !r.executing && (
                          <span className="ml-1.5 text-[9px] tracking-[0.1em] text-amber-300/80" title="Dry run — computes every order, places none">PAPER</span>
                        )}
                      </span>
                      {!r.local && liveSessions[r.id] && (
                        <button
                          onClick={(e) => { e.stopPropagation(); adoptSession(liveSessions[r.id], r.name); }}
                          className="shrink-0 px-1.5 py-0.5 rounded border border-green-400/50 text-[9px] font-mono text-green-400 hover:bg-green-400/10"
                          title="This strat has money on the engine but no card in MY STRATS here — SAVE adds it (same id, so its money and P&L stay attached)"
                        >
                          SAVE
                        </button>
                      )}
                      {curve && curve.length >= 2 && (
                        <span className="shrink-0" title="Last 7 days of live PnL">
                          <Sparkline data={curve.map((p) => p.pnl)} width={64} height={18} />
                        </span>
                      )}
                      <span
                        className="shrink-0 text-[12px] font-mono font-semibold tabular-nums text-pixel-white"
                        title={`${fmtUsd(r.money)} of your money on this strat · ${fmtUsd(deployed)} of it in open positions right now`}
                      >
                        {fmtUsd(r.money)}
                      </span>
                      <span className={`shrink-0 w-14 text-right text-[11px] font-mono font-semibold tabular-nums ${totalPnl > 0 ? "text-green-400" : totalPnl < 0 ? "text-red-400" : "text-pixel-gray"}`}>
                        {totalPnl >= 0 ? "+" : ""}{fmtUsd(totalPnl)}
                      </span>
                      {!!m?.pnl24h && (
                        <span
                          className={`shrink-0 text-[9.5px] font-mono tabular-nums ${m.pnl24h > 0 ? "text-green-400" : "text-red-400"}`}
                          title="24-hour P&L"
                        >
                          24h {m.pnl24h >= 0 ? "+" : ""}{fmtUsd(m.pnl24h)}
                        </span>
                      )}
                      {r.running && (
                        <button
                          onClick={(e) => { e.stopPropagation(); void stopStrat(r.id); }}
                          className="shrink-0 px-1.5 py-0.5 rounded border border-pixel-border/60 text-[9px] font-mono text-pixel-gray hover:text-red-400 hover:border-red-400/60"
                          title="Stop this strat's engine. Open positions stay open until you sell or they resolve."
                        >
                          STOP
                        </button>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </section>
        );
      })()}

      {/* ── SCORE STRATS — every score function is a strat: copy its top N ──
          Each row is also a card under MY STRATS (id scorefn-*), kept
          ranked and backtested out of sample by useScoreStrats. */}
      <section className={`space-y-1 ${sec === "scores" ? "" : "hidden"}`}>
        <SectionHeader label="SCORE STRATS" hint="each score function copies its top N traders · backtested on picks made before the test window" />
        <ScoreStratsPanel
          indexes={indexes}
          activeId={activeId}
          select={select}
          owner={auth.address ?? null}
          running={liveStratIds}
        />
      </section>

      {/* ── MY STRATS — the management list ── */}
      <section className={`space-y-1 ${sec === "mine" ? "" : "hidden"}`}>
        <div className="flex items-center gap-2 pr-1">
          <SectionHeader label="MY STRATS" hint="best first · click = active" />
          {/* WORKING = only the strats earning a ✓ CONSISTENT verdict —
              green across the ladder windows that traded, green on the live
              book if it has traded, and not flagged as a selection leak by
              the holdout check. The default, because that's the only list
              most visits are here for. */}
          <div className="ml-auto flex shrink-0 gap-1 border border-pixel-border rounded-full overflow-hidden">
            {([["working", `✓ WORKING (${workingStrats.length})`, "Only strats that are consistently green — ladder, live book and holdout all agree"],
               ["all", `ALL (${indexes.length})`, "Every strat you saved, best first"]] as const).map(([v, label, hint]) => (
              <button
                key={v}
                onClick={() => setStratShow(v)}
                title={hint}
                className={`px-2 py-0.5 text-[9px] font-mono font-semibold tracking-[0.1em] transition-colors ${
                  stratShow === v ? "bg-pixel-border-light text-pixel-white" : "text-pixel-gray hover:text-pixel-white"
                }`}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
        {/* Cards: each strat is a self-contained card. Columns come from the
            CONTAINER's width (auto-fill), not a viewport breakpoint — this tab
            renders inside frames (modc2, the phone view) whose width has
            nothing to do with the window's, so `sm:` lies here and the cards
            were collapsing to full-width slabs. The dashed tiles at the end
            are the ways a new strat is born — vibecode one from words, fork
            the default COPY TRADING template, or upload your own code. */}
        <div
          className="grid gap-1.5 items-stretch"
          style={{ gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))" }}
        >
          {indexes.length === 0 && (
            <div style={{ gridColumn: "1 / -1" }} className="px-1.5 py-2 text-[10.5px] font-mono text-pixel-gray">
              No strats yet — <span className="text-pixel-gray-light">+ NEW STRAT</span> makes the default copy-trading one.
            </div>
          )}
          {/* An honest empty WORKING view — "none qualify" is the answer, not
              a blank page, and the way out (ALL) is in the sentence. */}
          {indexes.length > 0 && stratShow === "working" && workingStrats.length === 0 && (
            <div style={{ gridColumn: "1 / -1" }} className="px-1.5 py-2 text-[10.5px] font-mono leading-relaxed text-pixel-gray">
              None of your {indexes.length} strats is <span className="text-green-400">consistently green</span> right
              now — a strat earns ✓ when its backtest windows and live book agree it&apos;s winning, and the holdout
              check doesn&apos;t flag the gains as hindsight.{" "}
              <button onClick={() => setStratShow("all")} className="text-pixel-white underline underline-offset-2 hover:text-green-400">
                Show all {indexes.length}
              </button>
              {" "}· or try SCORES — its strats are ranked out-of-sample.
            </div>
          )}

          {shownStrats.map((idx) => {
            const isActive = idx.id === activeId;
            const isRunning = liveStratIds.has(idx.id);
            const onIt = moneyOn(idx.id);
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
            // No manual backtest saved on the strat, but the background
            // worker may have replayed it anyway — headline the widest
            // window that actually traded rather than saying "never run"
            // over a populated ladder.
            const ladderRow = ladder.byId[idx.id];
            const ladderBest = (!hasBt && ladderRow)
              ? [...HUB_WINDOWS].reverse().map((d) => {
                  const bt = ladderRow[d];
                  return bt && bt.trades > 0 ? { d, bt } : null;
                }).find(Boolean) ?? null
              : null;
            // 7-day live PnL curve (server sidecar). Absent until the strat
            // has traded and the sidecar has sampled — the band then hides.
            const curve = pnlHistory[idx.id];
            const curve7d = curve && curve.length >= 2 ? curve : null;
            return (
              <div
                key={idx.id}
                onClick={() => select(idx.id)}
                className={`relative flex flex-col rounded-[var(--radius-sm)] cursor-pointer transition-colors overflow-hidden ${
                  isActive
                    ? "bg-green-400/[0.07] ring-1 ring-green-400/30"
                    : "bg-pixel-white/[0.02] hover:bg-pixel-white/[0.05] ring-1 ring-pixel-border"
                }`}
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
                    <span
                      className={`w-2 h-2 rounded-full animate-pulse shrink-0 ${liveSessions[idx.id]?.executing === false ? "bg-amber-400" : "bg-green-400"}`}
                      title={liveSessions[idx.id]?.executing === false ? "Dry run — engine is computing orders but placing none" : "Engine is running for this strat"}
                    />
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
                      {idx.scoreFn && (
                        <span className="ml-1.5 text-[9px] tracking-[0.1em] text-amber-300/90" title={`Roster = the top ${idx.scoreFn.topN} of the "${idx.scoreFn.fnName}" score function, re-ranked from SCORE STRATS`}> ƒ</span>
                      )}
                    </span>
                  )}
                  {/* The verdict, said once — the ladder/live/holdout fold
                      from lib/stratVerdict.ts. UNKNOWN renders nothing: a
                      chip saying "no data" on every new card is noise. */}
                  {(() => {
                    const v = verdictById[idx.id];
                    if (!v || v.tier === "unknown") return null;
                    const look: Record<Exclude<VerdictTier, "unknown">, [string, string]> = {
                      consistent: ["✓ CONSISTENT", "border-green-400/60 text-green-400"],
                      mixed: ["~ MIXED", "border-amber-300/50 text-amber-300"],
                      bleeding: ["✗ BLEEDING", "border-red-400/60 text-red-400"],
                    };
                    const [label, cls] = look[v.tier];
                    return (
                      <span
                        title={v.reason}
                        className={`shrink-0 px-1.5 py-0.5 rounded border text-[8.5px] font-mono font-semibold tracking-[0.1em] ${cls}`}
                      >
                        {label}
                      </span>
                    );
                  })()}
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
                      {isRunning && liveSessions[idx.id]?.executing === false ? (
                        <>
                          <span className="text-[8.5px] font-semibold tracking-[0.18em] text-amber-300">PAPER</span>
                          <span className="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse" />
                        </>
                      ) : idx.liveEnabled && !isRunning ? (
                        <button
                          onClick={() => setView("live")}
                          className="flex items-center gap-1 hover:opacity-80 transition-opacity"
                          title="Engine stopped — click to go to LIVE tab to restart"
                        >
                          <span className="text-[8.5px] font-semibold tracking-[0.18em] text-amber-400">STOPPED</span>
                        </button>
                      ) : (
                        <>
                          <span className={`text-[8.5px] font-semibold tracking-[0.18em] ${isRunning ? "text-green-400" : "text-pixel-gray"}`}>
                            LIVE
                          </span>
                          {isRunning && <span className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse" />}
                        </>
                      )}
                    </div>
                    {/* Headline = the money on this strat (committed capital
                        while running, open positions once stopped). */}
                    {onIt > 0 && (
                      <div className="text-[13px] font-mono font-semibold text-pixel-white tabular-nums" title={`${fmtUsd(onIt)} of your money on this strat · ${fmtUsd(inPlay)} in ${openPos} open position${openPos === 1 ? "" : "s"}`}>
                        {fmtUsd(onIt)}
                        <span className="ml-1 text-[9px] font-normal text-pixel-gray">
                          {openPos > 0 ? `${openPos} pos ${fmtUsd(inPlay)}` : "none in positions"}
                        </span>
                      </div>
                    )}
                    {traded ? (
                      <>
                        {onIt <= 0 && (
                          <div className="text-[11px] font-mono font-semibold text-pixel-white tabular-nums">
                            {openPos > 0 ? fmtUsd(inPlay) : "flat"}
                          </div>
                        )}
                        <div className={`text-[9.5px] font-mono tabular-nums ${pnl24h > 0 ? "text-green-400" : pnl24h < 0 ? "text-red-400" : "text-pixel-gray"}`}>
                          24h {pnl24h >= 0 ? "+" : ""}{fmtUsd(pnl24h)}
                          {roi24h !== null && ` (${roi24h >= 0 ? "+" : ""}${roi24h.toFixed(1)}%)`}
                        </div>
                        <div className={`text-[9px] font-mono tabular-nums text-pixel-gray`}>
                          total {totalPnl >= 0 ? "+" : ""}{fmtUsd(totalPnl)}
                        </div>
                      </>
                    ) : (
                      onIt <= 0 && (
                        <div className="text-[10px] font-mono text-pixel-gray">
                          {isRunning ? "running · no positions" : "not trading"}
                        </div>
                      )
                    )}
                  </div>

                  {/* BACKTEST column */}
                  <div className="px-2 py-1.5">
                    <div
                      className="text-[8.5px] font-semibold tracking-[0.18em] text-pixel-gray mb-1"
                      title={idx.scoreFn?.oos ? `Out of sample: traders picked by "${idx.scoreFn.fnName}" on the board from ${idx.scoreFn.oos.testDays}d ago, traded the ${idx.scoreFn.oos.testDays}d since` : undefined}
                    >
                      BACKTEST{idx.scoreFn?.oos && idx.lastBacktestAt === idx.scoreFn.oos.at ? ` · OOS ${idx.scoreFn.oos.testDays}D` : idx.lastBacktestDays ? ` · ${idx.lastBacktestDays}D` : ""}
                    </div>
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
                    ) : ladderBest ? (
                      <>
                        <div className={`text-[11px] font-mono font-semibold tabular-nums ${ladderBest.bt.pnl >= 0 ? "text-green-400" : "text-red-400"}`}>
                          {ladderBest.bt.pnl >= 0 ? "+" : "−"}{fmtUsd(Math.abs(ladderBest.bt.pnl))}
                        </div>
                        <div className={`text-[9.5px] font-mono tabular-nums ${ladderBest.bt.roi >= 0 ? "text-green-400/80" : "text-red-400/80"}`}>
                          {ladderBest.bt.roi >= 0 ? "+" : ""}{ladderBest.bt.roi.toFixed(1)}% over {ladderBest.d}D
                        </div>
                        <div className="text-[9px] font-mono text-pixel-gray">
                          {ladderBest.bt.trades} trades
                          <span className="text-pixel-gray/60"> · {timeSince(ladderBest.bt.at)}</span>
                        </div>
                      </>
                    ) : (
                      <div className="text-[10px] font-mono text-pixel-gray">never run</div>
                    )}
                  </div>
                </div>

                {/* ── Backtest ladder: 1D · 3D · 7D · 14D · 30D ── */}
                <WindowStrip row={ladderRow} loading={ladder.loading} running={ladder.worker?.running} traderCount={idx.traders.length} />

                {/* ── Action strip — mt-auto pins it so cards in a grid row
                    stay equal-height with actions on the bottom edge ── */}
                <div
                  className="mt-auto flex items-center gap-0 px-2 pb-1.5"
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
                    {(isRunning || idx.liveEnabled) && (
                      <button
                        onClick={() => void stopStrat(idx.id)}
                        className="px-2 py-0.5 text-[9px] font-mono font-semibold text-pixel-gray hover:text-red-400 transition-colors"
                        title={isRunning ? "Stop this strat's engine" : "Clear stale live flag"}
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
            onClick={() => { setSec("build"); focusVibe(); }}
            title="Vibecode a strat — describe it in plain words and an agent writes the params, picks real traders off the board, and backtests it over 1/3/7 days. SAVE if the numbers are good. Opens the BUILD tab."
            className="flex flex-col justify-center gap-1 rounded-[var(--radius-sm)] border border-dashed border-green-400/40 px-3 py-3 text-left text-pixel-gray hover:text-green-400 hover:border-green-400/70 transition-colors min-h-[72px]"
          >
            <span className="text-[11px] font-mono font-semibold tracking-[0.08em] text-green-400/90">✧ VIBE A STRAT</span>
            <span className="text-[9.5px] font-mono leading-snug text-pixel-gray/80">
              describe it in plain words — an agent writes it and backtests it over 1/3/7 days
            </span>
          </button>
          <button
            onClick={() => forkDefault(traderIndexTemplate())}
            title="New strat — a TRADER INDEX: copies the bench trade for trade, each one scaled by your capital against that trader's book. Seeded with this week's best traders. Private until you publish it."
            className="flex flex-col justify-center gap-1 rounded-[var(--radius-sm)] border border-dashed border-pixel-border px-3 py-3 text-left text-pixel-gray hover:text-green-400 hover:border-green-400/60 transition-colors min-h-[72px]"
          >
            <span className="text-[11px] font-mono font-semibold tracking-[0.08em]">+ NEW STRAT</span>
            <span className="text-[9.5px] font-mono leading-snug text-pixel-gray/80">
              COPY TRADING template — mirrors your trader bench, every trade sized to your capital
            </span>
          </button>
          <button
            onClick={() => uploadRef.current?.click()}
            disabled={uploading}
            title="Add a strat from code — upload a strat.py, strat.rs or strat.ts. It lands under CODE STRATS, private until you publish it."
            className={`flex flex-col justify-center gap-1 rounded-[var(--radius-sm)] border border-dashed border-pixel-border px-3 py-3 text-left text-pixel-gray hover:text-green-400 hover:border-green-400/60 transition-colors min-h-[72px] ${uploading ? "opacity-40" : ""}`}
          >
            <span className="text-[11px] font-mono font-semibold tracking-[0.08em]">{uploading ? "UPLOADING…" : "⇪ UPLOAD CODE"}</span>
            <span className="text-[9.5px] font-mono leading-snug text-pixel-gray/80">
              your own strat.py · strat.rs · strat.ts — lands under the CODE tab
            </span>
          </button>
        </div>

        {/* The filter must never bury a bleed: if WORKING hides strats that
            hold money or have an engine running, say so where it can be seen. */}
        {hiddenFunded.length > 0 && (
          <div className="px-1.5 text-[9.5px] font-mono leading-snug text-amber-300/90">
            ⚠ {fmtUsd(hiddenMoney)} of your money is on {hiddenFunded.length} strat{hiddenFunded.length === 1 ? "" : "s"} that
            {hiddenFunded.length === 1 ? " isn't" : " aren't"} making the cut
            {" "}({hiddenFunded.slice(0, 3).map((i) => i.name).join(", ")}{hiddenFunded.length > 3 ? ", …" : ""}) —{" "}
            <button onClick={() => setStratShow("all")} className="text-pixel-white underline underline-offset-2 hover:text-amber-300">
              show all
            </button>
            {" "}or stop them on <button onClick={() => setSec("invested")} className="text-pixel-white underline underline-offset-2 hover:text-amber-300">INVESTED</button>.
          </div>
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
      <section className={`space-y-1 ${sec === "build" ? "" : "hidden"}`}>
        <SectionHeader label="BUILD" hint="describe a strat and test it, or let an agent invent one" />
        {/* VIBE first, and unfolded: describing what you want is the shortest
            path from an idea to a backtest, so it is the one that gets the
            top of the section. The two agents below are for when you would
            rather be handed an idea than have one. */}
        <StratVibe />
        {/* The factory: one agent run invents a strat off the live board and
            benches it over 1/3/7 days — on demand or on a loop. */}
        <AutoStratPanel />
        {/* The lab: an agent that finds, backtests and refines a strat until
            the data clears the confidence bar; ADOPT lands it above, paused. */}
        <StratLab />
      </section>

      {/* ── COMMUNITY — the public recipe gallery on this deploy ── */}
      <section className={`space-y-1 ${sec === "community" ? "" : "hidden"}`}>
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
            Nothing published yet. Flip one of your strats to PUBLIC on MY STRATS and it lists here for everyone.
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
                      onClick={() => { importPublic(entry); setSec("mine"); }}
                      className="shrink-0 px-1.5 py-0.5 rounded border border-pixel-border text-[9px] font-mono font-semibold tracking-[0.1em] text-pixel-gray hover:text-green-400 hover:border-green-400/60 transition-colors"
                      title={`Fork "${entry.strat.name}" into your strats — the copy is private, stopped and yours. Opens MY STRATS on it.`}
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
      <section className={`space-y-1 ${sec === "code" ? "" : "hidden"}`}>
        <SectionHeader label="CODE STRATS" hint="your own strat.py / strat.rs / strat.ts — upload, publish, share by CID across deploys" />
        <UserStratsPanel eoa={auth.address ?? undefined} />
      </section>

      {/* ── SCORE — the ▦ SCORE MARKET's one home; shares the SCORES tab ── */}
      <section className={`space-y-1 ${sec === "scores" ? "" : "hidden"}`} style={{ borderTop: "1px solid var(--border)" }}>
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
