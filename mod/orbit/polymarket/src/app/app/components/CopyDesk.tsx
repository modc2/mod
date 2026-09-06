"use client";

// THE COPY DESK — copy individual traders, with an amount against each name.
//
// One screen, one question: whose trades am I copying, with how much, and is
// it working? A row is one leader. The dollar figure on it is the whole
// position sizing model — the live engine budgets against it and the backtest
// replays with it, so changing the number changes both.
//
// Nothing on this screen is stored in the browser. Every read is GET
// /copy/book and every write is a POST to /copy/*, which are the same routes
// the `pm_copy_*` MCP tools call. Ask an agent to "put $50 on 0xab…" and this
// screen shows it on the next poll; move a slider here and the agent sees it.
// The alternative — a localStorage desk synced to the server — is how the two
// views drift, and an agent that can't see the desk can't reason about it.
//
// Each row's backtest is a replay of the IDENTITY STRAT that row materializes
// into (lib/identityStrat.ts): one leader, weight 1, this row's capital and
// gates. The same object the live engine runs. So the card's number is a claim
// about THIS row, not about a strategy that resembles it.
//
// Money moves are wallet-signed and SERVER-VERIFIED: loading dollars onto a
// trader, taking them back off, and dropping one go through /copy/signed/*
// (api/src/copy_actions.rs) — the server builds the exact challenge, MetaMask
// personal_signs it, and the server recovers the signer, requires the owner,
// enforces freshness + single use, and files a receipt. The Bearer token
// alone can't make these moves from here; rejecting the prompt cancels the
// change — the prompt IS the confirm dialog. Rebalance keeps the lighter
// wallet-confirm ceremony (lib/walletConfirm.ts): same prompt UX, token-
// authorized transport.

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";

import {
  fetchCopyBook, upsertAllocation, rebalanceBook, setBankroll,
  setAllocationMarketQuery, startCopying, stopCopying, stopSession, setCopyExecution, stallReason,
  signedCopyAction, type SignedCopyAction,
  type CopyBook, type CopyBookRow, type OtherSession,
} from "../lib/copyBook";
import {
  fetchTradersPage, formatPnl, timeAgo, WARMED_CANDIDATE_POOL, type TopTrader,
} from "../lib/polymarket";
import {
  MODE, armedDefault, autoExecuteFor, confirmGoLive, modeOf,
  type TradingMode,
} from "../lib/tradingMode";
import { ModeSwitch, SessionChip } from "./ModeControl";
import { identityStrat, shortAddress } from "../lib/identityStrat";
import { useHubBacktests, HUB_WINDOWS, type HubBacktest } from "../lib/hubBacktest";
import { addToDraft, BASKET_EVENT, readDraft } from "../lib/basketDraft";
import { describeMarketQuery } from "../lib/marketTypes";
import { confirmWithWallet, promptUsd, WalletDeclinedError } from "../lib/walletConfirm";
import FindTraders from "./FindTraders";
import DeskGuide from "./DeskGuide";
import DeskAllocationChart from "./DeskAllocationChart";
import { useAuth } from "../context/AuthContext";
import { getOwnerAddress } from "../lib/access";

const ADDR_RE = /^0x[0-9a-fA-F]{40}$/;
/** localStorage key for the TRADER dropdown's last selection. */
const VIEW_KEY = "poly_desk_trader_view";
/** The desk re-reads the book on this cadence. It is also how an allocation an
    agent changed over MCP arrives on screen without a reload. */
const POLL_MS = 15_000;

function fmtUsd(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  const sign = v < 0 ? "-" : "";
  return `${sign}$${Math.abs(v).toLocaleString("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}`;
}

function fmtSigned(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  return `${v >= 0 ? "+" : ""}${fmtUsd(v)}`;
}

function ago(ts: number | null | undefined): string {
  if (!ts) return "never";
  const s = Math.max(0, Math.round((Date.now() - ts) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

export default function CopyDesk() {
  const { auth } = useAuth();
  // Single-owner deployment: the wallet that signed into the gate IS the
  // funded one, and auth.address lags behind it after a wallet switch. Same
  // rule as the portfolio panels.
  const eoa = getOwnerAddress() ?? auth.address ?? null;

  const [book, setBook] = useState<CopyBook | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [days, setDays] = useState(1);
  // Mode a STOPPED row will start in. A running row reads its mode off the
  // server (`live.autoExecute`) and this map is ignored — the screen never
  // renders a mode the engine isn't actually in. Keyed by address; the desk
  // key "" is the header's start-all mode.
  const [armed, setArmed] = useState<Record<string, TradingMode>>({});
  // Names shortlisted into the BASKET draft (lib/basketDraft.ts). The desk
  // doesn't own that list — it just badges the rows already on it, so "+
  // BASKET" is idempotent-looking from here as well as from /copy/basket.
  const [basket, setBasket] = useState<Set<string>>(new Set());
  useEffect(() => {
    const sync = () => setBasket(new Set(readDraft().map((l) => l.address)));
    sync();
    // The sidebar tray's + BASKET writes the draft too — writeDraft announces
    // every mutation, so the IN BASKET chips here stay honest.
    window.addEventListener(BASKET_EVENT, sync);
    return () => window.removeEventListener(BASKET_EVENT, sync);
  }, []);
  // The add box is the whole screen while the book is empty and a one-line
  // bar once it isn't — the rows are what the desk is for. null = "whatever
  // the book size implies"; a click pins it either way.
  const [addOpen, setAddOpen] = useState<boolean | null>(null);
  // The bar opened in WHERE THE MONEY IS. Defaults to the first row and
  // follows the book: a removed leader can't stay selected.
  const [selected, setSelected] = useState<string | null>(null);
  // The TRADER dropdown: "all", or one address — which cards render below.
  // Persisted so the desk reopens on the trader you were working. Small
  // fixed-size value, safe for the shared modc2 localStorage origin.
  const [view, setViewState] = useState<string>("all");
  useEffect(() => {
    try {
      const v = localStorage.getItem(VIEW_KEY);
      if (v) setViewState(v);
    } catch {}
  }, []);
  const setView = useCallback((v: string) => {
    setViewState(v);
    try { localStorage.setItem(VIEW_KEY, v); } catch {}
  }, []);

  const load = useCallback(async () => {
    try {
      setBook(await fetchCopyBook(eoa));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [eoa]);

  useEffect(() => {
    void load();
    const t = setInterval(() => void load(), POLL_MS);
    return () => clearInterval(t);
  }, [load]);

  /** Run a mutation, show its error where the user can read it, re-read the
      book from the response so the screen never guesses at server state.
      Routes that don't answer with a book (`/live/execution`) fall through to
      a fresh GET, so the mode chip still comes from the server. */
  const mutate = useCallback(
    async (key: string, fn: () => Promise<unknown>) => {
      setBusy(key);
      setError(null);
      try {
        const res = await fn();
        const next = (res as { book?: CopyBook }).book ?? (res as CopyBook);
        if (next && Array.isArray((next as CopyBook).allocations)) setBook(next as CopyBook);
        else await load();
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
        await load();
      } finally {
        setBusy(null);
      }
    },
    [load],
  );

  /** A money move: put the change in front of MetaMask first, then run the
      mutation. Rejecting the prompt cancels the change with no error banner —
      the prompt itself was the question. Resolves with whether it applied,
      so an input can put its old number back on decline. */
  const mutateSigned = useCallback(
    async (key: string, lines: string[], fn: () => Promise<unknown>): Promise<boolean> => {
      setBusy(key);
      setError(null);
      try {
        await confirmWithWallet(lines);
      } catch (e) {
        setBusy(null);
        if (!(e instanceof WalletDeclinedError)) {
          setError(e instanceof Error ? e.message : String(e));
        }
        return false;
      }
      await mutate(key, fn);
      return true;
    },
    [mutate],
  );

  const rows = useMemo(() => book?.allocations ?? [], [book]);

  /** A per-trader money move on the TRUSTLESS path (api/src/copy_actions.rs):
      the wallet signs the server-built challenge for THIS action ("LOAD
      $25.00 INTO 0xab…") and the SERVER verifies the signature — owner
      recovery, 10-minute window, single use — before touching the book, then
      files a receipt at /copy/signed/receipts. Declining in MetaMask cancels
      with no error banner, same contract as mutateSigned. */
  const mutateVerified = useCallback(
    async (
      key: string,
      action: SignedCopyAction,
      trader: string,
      amountUsd: number | null,
    ): Promise<boolean> => {
      if (!eoa) {
        setError("connect the owner wallet first — signed actions need it");
        return false;
      }
      setBusy(key);
      setError(null);
      try {
        const res = await signedCopyAction(action, trader, amountUsd, eoa);
        if (res.book && Array.isArray(res.book.allocations)) setBook(res.book);
        else await load();
        return true;
      } catch (e) {
        if (!(e instanceof WalletDeclinedError)) {
          setError(e instanceof Error ? e.message : String(e));
        }
        await load();
        return false;
      } finally {
        setBusy(null);
      }
    },
    [eoa, load],
  );

  /** Absolute "$N behind this trader" → the signed delta it means (LOAD the
      increase / REMOVE the decrease) — shared by the row's $ box, its ±$
      buttons and the chart's ladder, so every path signs the same sentence. */
  const allocateVerified = useCallback(
    (address: string, usd: number): Promise<boolean> => {
      const cur = rows.find((r) => r.address.toLowerCase() === address.toLowerCase());
      if (!cur) return mutateVerified(`alloc:${address}`, "load", address, usd);
      const delta = Math.round((usd - cur.allocationUsd) * 100) / 100;
      if (delta === 0) return Promise.resolve(true);
      return mutateVerified(
        `alloc:${address}`,
        delta > 0 ? "load" : "remove",
        address,
        Math.abs(delta),
      );
    },
    [rows, mutateVerified],
  );

  /** The mode a row is in (running) or would start in (stopped).
   *
   *  Order matters: a live session's real mode always wins, then whatever the
   *  user armed, then the capital-derived default. So the switch never shows
   *  LIVE over a session that is quietly in TEST — the exact lie that let a
   *  funded wallet sit for a week placing nothing. */
  const modeFor = useCallback(
    (row: CopyBookRow): TradingMode =>
      row.live?.running
        ? modeOf(row.live.autoExecute)
        : armed[row.address] ?? armedDefault(row.allocationUsd > 0),
    [armed],
  );

  /** Desk-wide arm state for START ALL — LIVE only once some enabled row has
      dollars behind it. */
  const deskCanGoLive = useMemo(
    () => rows.some((r) => r.enabled && r.allocationUsd > 0),
    [rows],
  );
  const deskMode = armed[""] ?? armedDefault(deskCanGoLive);

  // Every row, replayed. The worker keeps these warm on its own (it reads the
  // copy book server-side), so `publish` is off — publishing here would
  // overwrite the strat hub's manifest with the desk's roster.
  const strats = useMemo(() => rows.map((r) => identityStrat(r)), [rows]);
  const { results, pending, loading: btLoading, worker, refresh } =
    useHubBacktests(strats, days, { publish: false, templates: false });

  // The leader's OWN last-24h record, so a row says who you're copying and
  // whether they're trading right now — not just what your mirror of them did.
  const leaders = useLeaderStats(rows.map((r) => r.address));
  const others = book?.sessions ?? [];
  useEffect(() => {
    if (rows.length === 0) { setSelected(null); return; }
    if (!selected || !rows.some((r) => r.address === selected)) setSelected(rows[0].address);
  }, [rows, selected]);

  // The dropdown can't keep pointing at a trader who left the book. Only
  // once the book has actually loaded — resetting against the empty
  // pre-load rows would wipe the persisted choice on every visit.
  useEffect(() => {
    if (book !== null && view !== "all" && !rows.some((r) => r.address === view)) {
      setView("all");
    }
  }, [book, rows, view, setView]);
  const visibleRows = view === "all" ? rows : rows.filter((r) => r.address === view);

  return (
    <div className="space-y-4">
      <DeskHeader
        book={book}
        eoa={eoa}
        busy={busy}
        days={days}
        onDays={setDays}
        onRefreshBacktests={refresh}
        workerAt={worker?.at ?? null}
        onBankroll={(v) => mutate("bankroll", () => setBankroll(v, eoa))}
        onRebalance={(mode) => {
          const bankroll = book?.bankroll ?? 0;
          const n = rows.filter((r) => r.enabled).length;
          void mutateSigned(
            "rebalance",
            mode === "equal"
              ? [`Split ${promptUsd(bankroll)} evenly across ${n} enabled trader${n === 1 ? "" : "s"}`]
              : [`Rescale every enabled allocation to fit ${promptUsd(bankroll)}, keeping the proportions`],
            () => rebalanceBook(bankroll, mode, eoa),
          );
        }}
        mode={deskMode}
        canGoLive={deskCanGoLive}
        onMode={(m) => setArmed((a) => ({ ...a, "": m }))}
        onStartAll={() => {
          // The confirm lives with START, not with the switch: arming LIVE on
          // a stopped desk can't fill anything, pressing START can.
          const enabled = rows.filter((r) => r.enabled);
          if (deskMode === "LIVE") {
            const total = enabled.reduce((s, r) => s + r.allocationUsd, 0);
            const subject = `All ${enabled.length} enabled trader${enabled.length === 1 ? "" : "s"}`;
            if (!confirmGoLive(subject, total)) return;
          }
          mutate("start-all", () =>
            startCopying(eoa!, { autoExecute: autoExecuteFor(deskMode) }),
          );
        }}
        onStopAll={() => mutate("stop-all", () => stopCopying(eoa!))}
      />

      {error && (
        <div className="pixel-panel-red p-3 font-mono text-[12px] text-red-300">
          {error}
        </div>
      )}

      {/* Whose trades, chosen by which market. The query that found them
          becomes their gate — `params.marketQuery` is what the live engine and
          the backtest both filter entries on, so the row goes on meaning what
          the search meant. An empty query is explicit too: it CLEARS the gate,
          which is how "actually, copy them everywhere" is said. */}
      {(addOpen ?? rows.length === 0) ? (
        <div className="relative">
          {rows.length > 0 && (
            <button
              className="absolute right-3 top-3 z-10 font-mono text-[10px] tracking-[0.1em] text-pixel-gray hover:text-pixel-white"
              onClick={() => setAddOpen(false)}
              title="Fold the add box away"
            >
              HIDE ▴
            </button>
          )}
      <FindTraders
        busy={busy === "add"}
        existing={new Set(rows.map((r) => r.address))}
        inBasket={basket}
        // Shortlist, don't commit: the basket is where several names get
        // different amounts and the whole split gets replayed. Nothing here
        // touches the copy book until APPLY TO DESK over there.
        onBasket={(address, allocationUsd, marketQuery) => {
          addToDraft({
            address, allocationUsd, enabled: true,
            ...(marketQuery ? { params: { marketQuery } } : {}),
          });
          setBasket(new Set(readDraft().map((l) => l.address)));
        }}
        onAdd={async (address, allocationUsd, marketQuery) => {
          // Adding IS loading — the signed LOAD both creates the row and puts
          // the dollars behind it, verified server-side. The market gate is a
          // filter, not money, so it's patched on after the load lands (a
          // params-only upsert, same as the row's gate chip).
          const ok = await mutateVerified("add", "load", address, allocationUsd);
          if (ok && marketQuery.trim()) {
            await mutate(`gate:${address}`, () =>
              setAllocationMarketQuery(address, marketQuery, eoa),
            );
          }
        }}
      />
        </div>
      ) : (
        <button
          className="pixel-panel w-full p-3 text-left flex flex-wrap items-baseline gap-x-3 gap-y-1 hover:border-pixel-green/60 transition-colors"
          onClick={() => setAddOpen(true)}
          title="Paste an address, or find the best traders in a market"
        >
          <span className="font-mono text-[12px] tracking-[0.14em] text-pixel-green">+ ADD A TRADER</span>
          <span className="font-mono text-[10px] text-pixel-gray">paste an address, or find the best in a market</span>
        </button>
      )}

      {/* Money moving on this wallet that the book doesn't own. Before the
          rows because "is it working?" has to include it — a desk reporting
          "none running" over a wallet placing real orders was the most
          misleading state this screen had. */}
      {others.length > 0 && eoa && (
        <OtherSessions
          sessions={others}
          busy={busy}
          onStop={(sid) => mutate(`stop-other:${sid}`, () => stopSession(eoa, sid))}
        />
      )}

      {book === null ? (
        <div className="pixel-panel p-6 text-center font-mono text-[12px] text-pixel-gray">
          READING THE COPY BOOK…
        </div>
      ) : rows.length === 0 ? (
        <EmptyDesk />
      ) : (
        <div className="space-y-3">
          {/* THE TRADER DROPDOWN — the whole desk, or one name at a time.
              Every card it narrows to carries the signed LOAD/REMOVE/× for
              that trader, so "work on one trader" is: pick them here, move
              money there. */}
          <TraderView rows={rows} view={view} onView={setView} />
          {/* The book as a chart: every $ against every name, the backtest
              at that $ on each bar, and the opened bar replayed at any size.
              Same `results` the rows' chips read, same `upsertAllocation`
              the rows' $ boxes write. */}
          <DeskAllocationChart
            rows={rows}
            results={results}
            pending={pending}
            days={days}
            selected={selected}
            onSelect={setSelected}
            busy={busy}
            onAllocate={(address, usd) => void allocateVerified(address, usd)}
          />
          {visibleRows.map((row) => (
            <TraderRow
              key={row.address}
              row={row}
              eoa={eoa}
              days={days}
              backtest={results[row.strategyId]}
              backtestPending={pending.has(row.strategyId) || btLoading}
              leader={leaders[row.address]}
              busy={busy}
              onAllocate={(usd) => allocateVerified(row.address, usd)}
              onToggleEnabled={() =>
                mutate(`toggle:${row.address}`, () =>
                  upsertAllocation(
                    {
                      address: row.address,
                      allocationUsd: row.allocationUsd,
                      enabled: !row.enabled,
                    },
                    eoa,
                  ),
                )
              }
              // The market gate, changed after the fact. `params` is a PATCH
              // server-side, so this leaves every other knob alone — and "" is
              // a value, not an omission: it clears the gate back to every
              // market they trade.
              onGate={(marketQuery) =>
                mutate(`gate:${row.address}`, () =>
                  setAllocationMarketQuery(row.address, marketQuery, eoa),
                )
              }
              mode={modeFor(row)}
              onMode={(m) => {
                // Running ⇒ flip the live session in place (the switch has
                // already confirmed). Stopped ⇒ just remember the arm.
                if (row.live?.running) {
                  mutate(`mode:${row.address}`, () =>
                    setCopyExecution(eoa!, row.strategyId, autoExecuteFor(m)),
                  );
                } else {
                  setArmed((a) => ({ ...a, [row.address]: m }));
                }
              }}
              onStart={() => {
                const m = modeFor(row);
                if (m === "LIVE" && !confirmGoLive(row.name, row.allocationUsd)) return;
                mutate(`start:${row.address}`, () =>
                  startCopying(eoa!, {
                    address: row.address,
                    autoExecute: autoExecuteFor(m),
                  }),
                );
              }}
              onStop={() =>
                mutate(`stop:${row.address}`, () => stopCopying(eoa!, row.address))
              }
              // The signed DROP — the wallet popup reads "DROP 0x… FROM THE
              // COPY BOOK", the server stops the session, removes the row and
              // files the receipt.
              onRemove={() =>
                void mutateVerified(`rm:${row.address}`, "drop", row.address, null)
              }
            />
          ))}
        </div>
      )}
    </div>
  );
}

// ── Header: one line of totals, then the desk's few global controls ──
//
// This used to be a dashboard: six stat tiles, two split buttons, five
// backtest-window buttons, a mode toggle, START/STOP, and two lines of
// explainer — for a book that usually holds zero to five names. Now it is
// a sentence and, once there is something to run, one row of controls.
// The tooltips carry what the explainers said.

function DeskHeader({
  book, eoa, busy, days, onDays, onRefreshBacktests, workerAt,
  onBankroll, onRebalance, mode, canGoLive, onMode, onStartAll, onStopAll,
}: {
  book: CopyBook | null;
  eoa: string | null;
  busy: string | null;
  days: number;
  onDays: (d: number) => void;
  onRefreshBacktests: () => void;
  workerAt: number | null;
  onBankroll: (v: number) => void;
  onRebalance: (mode: "equal" | "weighted") => void;
  mode: TradingMode;
  canGoLive: boolean;
  onMode: (mode: TradingMode) => void;
  onStartAll: () => void;
  onStopAll: () => void;
}) {
  const [bankrollDraft, setBankrollDraft] = useState("");
  useEffect(() => {
    if (book) setBankrollDraft(String(book.bankroll || ""));
  }, [book?.bankroll]); // eslint-disable-line react-hooks/exhaustive-deps

  const t = book?.totals;
  const traders = t?.traders ?? 0;
  const running = t?.running ?? 0;
  const executing = t?.executing ?? 0;
  const over = (t?.unallocatedUsd ?? 0) < 0;
  const hasRows = traders > 0;

  return (
    <div className="pixel-panel p-4 space-y-3">
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
        <h1 className="font-mono text-[15px] tracking-[0.18em] text-pixel-green" title="Who you copy, with how much, and whether it is working">COPY DESK</h1>

        {/* The whole state of the desk, as one sentence. */}
        <span className="font-mono text-[11px] text-pixel-gray-light">
          {book === null ? (
            "reading the book…"
          ) : !hasRows ? (
            <span className="text-pixel-gray">nobody yet — start with the box below</span>
          ) : (
            <>
              {traders} trader{traders === 1 ? "" : "s"}
              {" · "}
              <span
                className={over ? "text-amber-400" : ""}
                title={
                  over
                    ? "The traders below are promised more than the bankroll — this happens when you lower BANKROLL $ after allocating. FIT shrinks every allocation by the same ratio; or raise BANKROLL $ back."
                    : "How much of the bankroll is promised to the traders below"
                }
              >
                {over ? (
                  <>
                    {fmtUsd(t?.allocatedUsd, 0)} promised · bankroll {fmtUsd(book.bankroll, 0)}
                  </>
                ) : (
                  <>
                    {fmtUsd(t?.allocatedUsd, 0)} of {fmtUsd(book.bankroll, 0)} allocated
                  </>
                )}
              </span>
              {over && book.bankroll > 0 && (
                <button
                  className="pixel-btn btn-xs border-amber-400 text-amber-400 ml-1"
                  disabled={busy !== null}
                  onClick={() => onRebalance("weighted")}
                  title="Shrink every allocation by the same ratio so together they add up to the bankroll"
                >
                  FIT TO {fmtUsd(book.bankroll, 0)}
                </button>
              )}
              {" · "}
              {running === 0 ? (
                <span className="text-pixel-gray">none running</span>
              ) : executing > 0 ? (
                <span className="text-pixel-green">{executing} of {running} LIVE</span>
              ) : (
                <span className="text-amber-400">{running} running · TEST</span>
              )}
            </>
          )}
        </span>

        <span className="flex-1" />

        <Link
          href="/copy/trades"
          className="font-mono text-[11px] tracking-[0.1em] text-pixel-gray hover:text-green-400"
          title="What the copying actually did: their trades against my fills, and how much of their flow I got"
        >
          RESULTS →
        </Link>
        <Link
          href="/copy/basket"
          className="font-mono text-[11px] tracking-[0.1em] text-pixel-gray hover:text-green-400"
          title="Size several traders against each other before committing — a different amount per name, replayed as one set"
        >
          BASKET →
        </Link>
      </div>

      {!eoa && (
        <div className="font-mono text-[11px] text-amber-400">
          no wallet connected — you can look, but nothing can START until you sign in (top right)
        </div>
      )}

      {/* Controls only once there is something to control. On an empty desk
          START ALL, STOP ALL and a split are buttons that do nothing. */}
      {hasRows && (
        <div className="flex flex-wrap items-center gap-2 border-t border-pixel-gray/20 pt-3">
          <label className="font-mono text-[9px] tracking-[0.14em] text-pixel-gray">BANKROLL $</label>
          <input
            className="pixel-input-sm w-24 font-mono text-[12px]"
            value={bankrollDraft}
            inputMode="decimal"
            onChange={(e) => setBankrollDraft(e.target.value)}
            onBlur={() => {
              const v = Number(bankrollDraft);
              if (Number.isFinite(v) && v >= 0 && v !== book?.bankroll) onBankroll(v);
            }}
          />
          <button
            className="pixel-btn text-[11px]"
            disabled={busy !== null || !book?.bankroll}
            onClick={() => onRebalance("equal")}
            title="Give every enabled trader the same share of the bankroll"
          >
            SPLIT EVENLY
          </button>

          <span className="w-px h-5 bg-pixel-gray/20 mx-1" />

          <ModeSwitch
            mode={mode}
            onPick={onMode}
            running={false}
            canGoLive={canGoLive}
            subject="The whole desk"
            amountUsd={book?.totals.allocatedUsd}
            disabled={!eoa || busy !== null}
          />
          <button
            className={`pixel-btn text-[11px] ${
              mode === "LIVE" ? "border-green-400 text-green-400" : ""
            }`}
            disabled={!eoa || busy !== null}
            onClick={onStartAll}
            title={`Start every enabled trader in ${mode} — ${MODE[mode].meaning}. Each row has its own switch too.`}
          >
            START ALL
          </button>
          <button
            className="pixel-btn text-[11px] border-red-500 text-red-400"
            disabled={!eoa || busy !== null || running === 0}
            onClick={onStopAll}
            title="Stop every running session. Open positions are left alone."
          >
            STOP ALL
          </button>

          <span className="flex-1" />

          <label className="font-mono text-[9px] tracking-[0.14em] text-pixel-gray">BACKTEST</label>
          <select
            value={days}
            onChange={(e) => onDays(Number(e.target.value))}
            title="Replay every trader over this window"
            className="bg-pixel-black/40 border border-pixel-border/60 rounded px-1.5 py-0.5 font-mono text-[11px] text-pixel-white outline-none cursor-pointer"
          >
            {HUB_WINDOWS.map((d) => (
              <option key={d} value={d}>{d}D</option>
            ))}
          </select>
          <button
            className="pixel-btn text-[10px] px-2"
            onClick={onRefreshBacktests}
            title={`Re-run every row's backtest now · ${workerAt ? `worker ran ${ago(workerAt)}` : "no worker pass yet"}`}
          >
            ↻
          </button>
        </div>
      )}
    </div>
  );
}

// ── The leader's own record ──

/** The last-24h leaderboard row for each address, or null when they aren't
    on it (= no fill in 24h — itself the fact worth showing). One small read
    per leader against the warm 1D cache; refreshed every five minutes. */
function useLeaderStats(addresses: string[]): Record<string, TopTrader | null | undefined> {
  const [stats, setStats] = useState<Record<string, TopTrader | null>>({});
  const [tick, setTick] = useState(0);
  const key = addresses.join(",");

  useEffect(() => {
    const t = setInterval(() => setTick((n) => n + 1), 5 * 60_000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    let cancelled = false;
    const list = key ? key.split(",") : [];
    void (async () => {
      for (const address of list) {
        try {
          const res = await fetchTradersPage({
            days: 1, pool: WARMED_CANDIDATE_POOL, pageSize: 3, search: address,
          });
          const hit = res.cold
            ? null
            : res.traders.find((t) => t.address.toLowerCase() === address.toLowerCase()) ?? null;
          if (!cancelled) setStats((prev) => ({ ...prev, [address]: hit }));
        } catch {
          if (!cancelled) setStats((prev) => ({ ...prev, [address]: null }));
        }
      }
    })();
    return () => { cancelled = true; };
  }, [key, tick]);

  return stats;
}

// ── Sessions outside the book ──

function OtherSessions({
  sessions, busy, onStop,
}: {
  sessions: OtherSession[];
  busy: string | null;
  onStop: (strategyId: string) => void;
}) {
  return (
    <div className="pixel-panel-amber p-3 space-y-2">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <div className="font-mono text-[11px] tracking-[0.14em] text-amber-400">
          ALSO TRADING WITH THIS WALLET
        </div>
        <span className="font-mono text-[10px] text-pixel-gray">
          {sessions.length} older session{sessions.length === 1 ? "" : "s"} not listed on this desk — same wallet, same USDC. Stop {sessions.length === 1 ? "it" : "them"} here or leave {sessions.length === 1 ? "it" : "them"} running.
        </span>
      </div>
      {sessions.map((s) => {
        const mode = modeOf(s.autoExecute);
        const what = s.momentum
          ? "candle bot"
          : s.traders.length === 0
            ? "no leaders"
            : `${s.traders.length} leader${s.traders.length === 1 ? "" : "s"}`;
        return (
          <div key={s.strategyId} className="flex flex-wrap items-center gap-x-3 gap-y-1.5 border-t border-pixel-gray/15 pt-2">
            <span className="font-mono text-[12px] text-pixel-gray-light">{s.strategyId}</span>
            <SessionChip run={s.error ? "ERROR" : "RUNNING"} mode={mode} />
            <span className="font-mono text-[10px] text-pixel-gray">
              {fmtUsd(s.capital, 0)} capital · {what}
              {s.traders.slice(0, 2).map((a) => (
                <Link key={a} href={`/traders/${a}`} className="ml-1.5 text-pixel-gray-light hover:text-pixel-green normal-case">
                  {shortAddress(a)} ↗
                </Link>
              ))}
              {s.traders.length > 2 && <> +{s.traders.length - 2}</>}
              {s.marketQuery && <> · in “{s.marketQuery}”</>}
            </span>
            <span className="flex-1" />
            <div className="flex items-center gap-4 font-mono text-[10px]">
              <Field
                label="REALIZED"
                value={fmtSigned(s.realized === null ? null : s.realized - (s.fees ?? 0))}
                tone={s.realized === null ? undefined : s.realized - (s.fees ?? 0) >= 0 ? "up" : "down"}
              />
              {(s.fees ?? 0) > 0 && <Field label="FEES" value={`-${fmtUsd(s.fees ?? 0)}`} tone="down" />}
              <Field label="ORDERS" value={String(s.ordersPlaced)} />
              <Field label="LAST FILL" value={ago(s.lastFillAt || null)} />
              <Field label="WALLET" value={fmtUsd(s.balance, 0)} />
            </div>
            <button
              className="pixel-btn btn-xs border-red-500 text-red-400"
              disabled={busy !== null}
              onClick={() => {
                if (window.confirm(`Stop session ${s.strategyId}? Open positions are left alone.`)) onStop(s.strategyId);
              }}
              title="Stop this session. Open positions are left alone."
            >
              STOP
            </button>
            {s.error && <div className="w-full font-mono text-[10px] text-red-400">{s.error}</div>}
          </div>
        );
      })}
    </div>
  );
}

function EmptyDesk() {
  return <DeskGuide />;
}

// ── The TRADER dropdown ──

/** The book as a select: ALL TRADERS, or one name — which cards render
    below. Each option carries the row's money and state so the closed
    dropdown already answers "who, with how much, doing what". The choice
    is persisted (VIEW_KEY), so the desk reopens on the trader being worked. */
function TraderView({
  rows, view, onView,
}: {
  rows: CopyBookRow[];
  view: string;
  onView: (v: string) => void;
}) {
  const word = (r: CopyBookRow) =>
    !r.enabled ? "PAUSED" : r.live?.running ? MODE[modeOf(r.live.autoExecute)].label : "OFF";
  return (
    <div className="pixel-panel p-3 flex flex-wrap items-center gap-x-3 gap-y-1.5">
      <label className="font-mono text-[9px] tracking-[0.14em] text-pixel-gray">TRADER</label>
      <select
        className="bg-pixel-black/40 border border-pixel-border/60 rounded px-1.5 py-1 font-mono text-[11px] text-pixel-white outline-none cursor-pointer max-w-[360px]"
        value={view}
        onChange={(e) => onView(e.target.value)}
        title="Work the whole desk, or one trader at a time"
      >
        <option value="all">ALL TRADERS ({rows.length})</option>
        {rows.map((r) => (
          <option key={r.address} value={r.address}>
            {r.name} · {fmtUsd(r.allocationUsd, 0)} · {word(r)}
          </option>
        ))}
      </select>
      <span className="font-mono text-[10px] text-pixel-gray">
        {view === "all"
          ? "every trader below — pick one name to work on just their card"
          : "one trader — the $ box, ±$ and × on their card are wallet-signed and server-verified"}
      </span>
    </div>
  );
}

// ── One leader ──

function TraderRow({
  row, eoa, days, backtest, backtestPending, leader, busy, mode,
  onAllocate, onToggleEnabled, onGate, onMode, onStart, onStop, onRemove,
}: {
  row: CopyBookRow;
  eoa: string | null;
  days: number;
  backtest?: HubBacktest;
  backtestPending: boolean;
  /** undefined = not looked up yet, null = not on the 24h board. */
  leader: TopTrader | null | undefined;
  busy: string | null;
  /** Wallet-confirmed upstream; resolves false when MetaMask declined, so
      the $ box can put the old number back. */
  onAllocate: (usd: number) => Promise<boolean>;
  onToggleEnabled: () => void;
  /** New market gate. "" clears it — copy them everywhere. */
  onGate: (marketQuery: string) => void;
  mode: TradingMode;
  onMode: (mode: TradingMode) => void;
  onStart: () => void;
  onStop: () => void;
  onRemove: () => void;
}) {
  const [draft, setDraft] = useState(String(row.allocationUsd));
  useEffect(() => setDraft(String(row.allocationUsd)), [row.allocationUsd]);

  const live = row.live;
  const running = !!live?.running;
  const executing = running && !!live?.autoExecute;
  const stall = stallReason(row);
  // NET of fees — what the wallet kept, not what the exits grossed.
  const realized = live?.ledger ? live.ledger.realized - (live.ledger.fees ?? 0) : null;
  const feesPaid = live?.ledger?.fees ?? 0;
  const rowBusy = busy?.endsWith(row.address) ?? false;

  const commit = () => {
    const before = row.allocationUsd;
    const v = Number(draft);
    if (Number.isFinite(v) && v >= 0 && v !== before) {
      void onAllocate(v).then((ok) => {
        if (!ok) setDraft(String(before));
      });
    } else setDraft(String(before));
  };

  return (
    <div
      className={`pixel-panel p-3 space-y-2 ${row.enabled ? "" : "opacity-60"} ${
        executing ? "border-pixel-green" : ""
      }`}
    >
      {/* Line 1: who, which markets, how much, and the switches. */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <div className="flex items-center gap-2 min-w-[180px]">
          <Link
            href={`/copy/${row.address}`}
            className="font-mono text-[13px] text-pixel-gray-light hover:text-pixel-green"
            title={`Open ${row.name} — backtest, live session and wallet`}
          >
            {row.name}
          </Link>
          <StatusPill running={running} enabled={row.enabled} mode={mode} />
        </div>
        <Link
          href={`/traders/${row.address}${
            row.params?.marketQuery?.trim()
              ? `?mq=${encodeURIComponent(row.params.marketQuery.trim())}`
              : ""
          }`}
          className="font-mono text-[10px] text-pixel-gray hover:text-pixel-green normal-case"
          title={
            row.params?.marketQuery?.trim()
              ? `${row.address} — their record in “${row.params.marketQuery.trim()}”, this row's gate`
              : `${row.address} — this leader's own trading record`
          }
        >
          {shortAddress(row.address)} ↗
        </Link>

        <MarketGate query={row.params?.marketQuery ?? ""} disabled={rowBusy} onChange={onGate} />

        <span className="flex-1" />

        <div className="flex items-center gap-1.5">
          <span className="font-mono text-[9px] tracking-[0.14em] text-pixel-gray">$</span>
          <input
            className="pixel-input-sm input-xs w-20 font-mono text-[12px]"
            value={draft}
            inputMode="decimal"
            disabled={rowBusy}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={commit}
            onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
            title="Dollars behind this trader — the whole sizing model, for the engine and the backtest alike. Changing it asks MetaMask to confirm."
          />
          <AmountNudge
            disabled={!eoa || rowBusy}
            onDelta={(delta) => {
              const next = Math.max(0, Math.round((row.allocationUsd + delta) * 100) / 100);
              if (next !== row.allocationUsd) void onAllocate(next);
            }}
          />
        </div>

        <div className="flex items-center gap-1.5 shrink-0">
          <ModeSwitch
            size="sm"
            mode={mode}
            onPick={onMode}
            running={running}
            canGoLive={row.allocationUsd > 0 && (live?.balance ?? 1) > 0}
            subject={row.name}
            amountUsd={row.allocationUsd}
            disabled={!eoa || rowBusy || !row.enabled}
          />
          {running ? (
            <button
              className="pixel-btn btn-xs border-red-500 text-red-400"
              disabled={rowBusy}
              onClick={onStop}
              title="Stop this leader's session. Open positions are left alone."
            >
              STOP
            </button>
          ) : (
            <button
              className={`pixel-btn btn-xs ${mode === "LIVE" ? "border-green-400 text-green-400" : ""}`}
              disabled={!eoa || rowBusy || !row.enabled}
              onClick={onStart}
              title={`Start copying ${row.name} in ${mode} — ${MODE[mode].meaning}`}
            >
              START
            </button>
          )}
          <button
            className="pixel-btn btn-xs"
            disabled={rowBusy}
            onClick={onToggleEnabled}
            title={row.enabled ? "Pause: keep the allocation and history, leave them out of START ALL" : "Un-pause"}
          >
            {row.enabled ? "PAUSE" : "RESUME"}
          </button>
          <button
            className="pixel-btn btn-xs"
            disabled={!eoa || rowBusy}
            onClick={onRemove}
            title="Stop the session and drop them from the book — MetaMask asks you to confirm"
          >
            ×
          </button>
        </div>
      </div>

      {/* Line 2: the three facts a row is for — them, the replay, the session. */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-x-4 gap-y-2 border-t border-pixel-gray/15 pt-2">
        <LeaderChip leader={leader} />
        <BacktestChip bt={backtest} pending={backtestPending} days={days} />
        <div className="font-mono text-[10px]">
          <div className="text-[9px] tracking-[0.14em] text-pixel-gray">
            MY COPYING{live ? "" : " — not started"}
          </div>
          {live ? (
            <div className="flex flex-wrap items-baseline gap-x-3">
              <span
                className={realized === null ? "text-pixel-gray" : realized >= 0 ? "text-pixel-green" : "text-red-400"}
                title={
                  "Booked P&L from this leader's mirrors, NET of Polymarket's taker fee. Open positions are excluded — a mark reads high."
                  + (feesPaid > 0 ? ` Gross ${fmtSigned(live!.ledger!.realized)} less ${fmtUsd(feesPaid)} of fees.` : "")
                }
              >
                {fmtSigned(realized)} realized
              </span>
              {feesPaid > 0 && (
                <span
                  className="text-amber-400"
                  title="Polymarket's taker fee on this row's fills — rate x price x (1 - price) x shares, charged by the matcher on both the way in and the way out."
                >
                  -{fmtUsd(feesPaid)} fees
                </span>
              )}
              <span className="text-pixel-gray-light">{live.ordersPlaced} orders</span>
              <span className="text-pixel-gray">last fill {ago(live.ledger?.lastFillAt || null)}</span>
              {live.balance !== null && (
                <span className="text-pixel-gray" title="USDC in the trading wallet, capped at this row's allocation">
                  wallet {fmtUsd(live.balance, 0)}
                </span>
              )}
            </div>
          ) : (
            <div className="text-pixel-gray">press START to begin — in {mode}, {MODE[mode].meaning}</div>
          )}
        </div>
      </div>

      {stall && (
        <div className="font-mono text-[10px] text-amber-400 border-t border-pixel-gray/15 pt-1.5">{stall}</div>
      )}
    </div>
  );
}

/** ± money on this row, as two explicit buttons beside the $ box. Each opens
    a tiny amount input; OK hands the delta up, where it becomes a
    wallet-confirmed set of the new total ("Add $25 to X — $50 → $75" in the
    MetaMask prompt). Taking more than the row holds clamps at $0; dropping
    the trader entirely stays the × button's job. */
function AmountNudge({
  disabled, onDelta,
}: {
  disabled: boolean;
  onDelta: (delta: number) => void;
}) {
  const [dir, setDir] = useState<1 | -1 | null>(null);
  const [amt, setAmt] = useState("25");

  if (dir === null) {
    return (
      <span className="inline-flex items-center gap-1">
        <button
          className="pixel-btn btn-xs"
          disabled={disabled}
          onClick={() => setDir(1)}
          title="Put more dollars behind this trader — MetaMask asks you to confirm"
        >
          +$
        </button>
        <button
          className="pixel-btn btn-xs"
          disabled={disabled}
          onClick={() => setDir(-1)}
          title="Take dollars off this trader — MetaMask asks you to confirm"
        >
          −$
        </button>
      </span>
    );
  }

  const commit = () => {
    const v = Number(amt);
    if (Number.isFinite(v) && v > 0) onDelta(dir * v);
    setDir(null);
  };
  return (
    <span className="inline-flex items-center gap-1">
      <span className={`font-mono text-[11px] ${dir === 1 ? "text-pixel-green" : "text-red-400"}`}>
        {dir === 1 ? "+$" : "−$"}
      </span>
      <input
        autoFocus
        className="pixel-input-sm input-xs w-14 font-mono text-[12px]"
        value={amt}
        inputMode="decimal"
        disabled={disabled}
        onChange={(e) => setAmt(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") commit();
          if (e.key === "Escape") setDir(null);
        }}
      />
      <button className="pixel-btn btn-xs" disabled={disabled} onClick={commit} title="Confirm this amount in MetaMask">
        OK
      </button>
      <button className="pixel-btn btn-xs" onClick={() => setDir(null)} title="Never mind">
        ✕
      </button>
    </span>
  );
}

/** What the leader themself did in the last 24h, from the same board the
    finder ranks. Not on it = no fill in 24h, which is exactly the trader a
    fresh allocation would sit idle behind. */
function LeaderChip({ leader }: { leader: TopTrader | null | undefined }) {
  return (
    <div className="font-mono text-[10px]">
      <div className="text-[9px] tracking-[0.14em] text-pixel-gray">THE LEADER · 24H</div>
      {leader === undefined ? (
        <div className="text-pixel-gray">looking up…</div>
      ) : leader === null ? (
        <div className="text-amber-400" title="Not on the 24h leaderboard — nothing to mirror until they trade again">
          no fills in the last 24h
        </div>
      ) : (
        <div className="flex flex-wrap items-baseline gap-x-3">
          <span className={leader.pnl > 0 ? "text-pixel-green" : leader.pnl < 0 ? "text-red-400" : "text-pixel-gray"}>
            {formatPnl(leader.pnl)}
          </span>
          <span
            className="text-pixel-gray-light"
            title={
              leader.winRate < 0
                ? "No positions settled in this window yet."
                : `Of ${leader.decidedPositions} position(s) that settled in this window.`
            }
          >
            {leader.winRate < 0
              ? "— win"
              : `${Math.round(leader.winRate)}% win of ${leader.decidedPositions}`}
          </span>
          <span className="text-pixel-gray-light">{leader.recentTrades.toLocaleString()} trades</span>
          <span className="text-pixel-gray">
            last {leader.lastTradeTs ? timeAgo(leader.lastTradeTs * 1000) : "—"}
          </span>
        </div>
      )}
    </div>
  );
}

/** WHICH of the leader's trades this row copies — the market gate, as one
    chip. The query is the same string the finder searched with, applied in
    `Strat.shouldMirror` and the engine's `market_ok`. Entries only: an exit
    is never gated, or a filter change would strand an open position. */
function MarketGate({
  query, disabled, onChange,
}: {
  query: string;
  disabled: boolean;
  onChange: (q: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(query);
  useEffect(() => setDraft(query), [query]);

  const gated = query.trim() !== "";

  const commit = () => {
    setEditing(false);
    if (draft.trim() !== query.trim()) onChange(draft.trim());
  };

  if (editing) {
    return (
      <input
        autoFocus
        className="pixel-input-sm input-xs w-56 font-mono text-[11px]"
        placeholder="bitcoin, btc — blank for every market"
        value={draft}
        disabled={disabled}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") commit();
          if (e.key === "Escape") { setDraft(query); setEditing(false); }
        }}
        onBlur={commit}
      />
    );
  }
  return (
    <span className="inline-flex items-center gap-1">
      <button
        className={`pixel-btn btn-xs normal-case ${gated ? "border-pixel-green text-pixel-green" : ""}`}
        disabled={disabled}
        onClick={() => setEditing(true)}
        title={
          gated
            ? `Only their trades in markets matching “${query}” are mirrored — click to change`
            : "Every market they trade — click to gate it to one topic"
        }
      >
        {gated ? `IN ${describeMarketQuery(query)}` : "ALL MARKETS"}
      </button>
      {gated && (
        <button
          className="pixel-btn btn-xs"
          disabled={disabled}
          onClick={() => onChange("")}
          title="Drop the gate — copy every market they trade"
        >
          ✕
        </button>
      )}
    </span>
  );
}

function Field({
  label, value, tone, title,
}: {
  label: string;
  value: string;
  tone?: "up" | "down";
  title?: string;
}) {
  const color =
    tone === "up" ? "text-pixel-green" : tone === "down" ? "text-red-400" : "text-pixel-gray-light";
  return (
    <div title={title}>
      <div className="text-[9px] tracking-[0.14em] text-pixel-gray">{label}</div>
      <div className={color}>{value}</div>
    </div>
  );
}

/** Where the row is, in the two words the whole console uses. `enabled` is a
    third state that isn't a run state — a paused leader is in the book and
    won't be started — so it short-circuits before the shared chip. */
function StatusPill({
  running, enabled, mode,
}: {
  running: boolean;
  enabled: boolean;
  mode: TradingMode;
}) {
  if (!enabled) {
    return (
      <span
        className="font-mono text-[9px] tracking-[0.12em] border border-pixel-gray/40 text-pixel-gray rounded-[3px] px-1.5 py-[1px]"
        title="Paused — in the book, kept out of START ALL"
      >
        PAUSED
      </span>
    );
  }
  return <SessionChip run={running ? "RUNNING" : "STOPPED"} mode={mode} />;
}

/** The row's replay. A backtest is a claim about the past; the walk-forward
    verdict is the only part that says whether the claim survived a window it
    didn't get to see. */
function BacktestChip({
  bt, pending, days,
}: {
  bt?: HubBacktest;
  pending: boolean;
  days: number;
}) {
  const head = (
    <div className="text-[9px] tracking-[0.14em] text-pixel-gray">
      {bt ? bt.days : days}D BACKTEST{bt && bt.by !== "worker" ? " (local)" : ""}
    </div>
  );
  if (!bt) {
    return (
      <div className="font-mono text-[10px]">
        {head}
        <div className="text-pixel-gray" title="The worker replays the desk on its own pass; ↻ in the header runs it now">
          {pending ? "replaying…" : "queued — no replay yet"}
        </div>
      </div>
    );
  }
  const good = bt.pnl >= 0;
  const verdict = bt.forward?.verdict;
  const verdictTone =
    verdict === "held"
      ? "text-pixel-green"
      : verdict === "faded" || verdict === "no-edge"
        ? "text-red-400"
        : "text-pixel-gray";
  const marked = bt.settlement?.markedUsd ?? 0;
  return (
    <div className="font-mono text-[10px]">
      {head}
      <div className="flex flex-wrap items-baseline gap-x-3">
        <span className={good ? "text-pixel-green" : "text-red-400"}>{fmtSigned(bt.pnl)}</span>
        <span className="text-pixel-gray-light">{bt.trades} trades</span>
        <span
          className={verdict ? verdictTone : "text-pixel-gray"}
          title={
            verdict
              ? "Walk-forward: replayed over the PREVIOUS window too. HELD is the only pass."
              : "No walk-forward check on this replay"
          }
        >
          {verdict ? verdict.toUpperCase() : "UNCHECKED"}
        </span>
        {marked > 0.5 && (
          <span
            className="text-amber-400"
            title={`${fmtUsd(marked)} was still open at the end of the window, valued at the last observed price — unresolved marks read HIGH`}
          >
            {fmtUsd(marked, 0)} unsettled
          </span>
        )}
        {(bt.warming ?? 0) > 0 && (
          <span className="text-amber-400" title="The feed store was still filling when this ran — the leader replayed as having done nothing">
            incomplete
          </span>
        )}
      </div>
      {bt.note && <div className="text-pixel-gray truncate" title={bt.note}>{bt.note}</div>}
    </div>
  );
}
