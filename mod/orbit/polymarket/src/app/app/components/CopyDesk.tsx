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

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";

import {
  fetchCopyBook, upsertAllocation, removeAllocation, rebalanceBook, setBankroll,
  setAllocationMarketQuery, startCopying, stopCopying, setCopyExecution, stallReason,
  type CopyBook, type CopyBookRow,
} from "../lib/copyBook";
import {
  MODE, armedDefault, autoExecuteFor, confirmGoLive, describeFleet, modeOf,
  type TradingMode,
} from "../lib/tradingMode";
import { ModeSwitch, SessionChip, ModeLegend } from "./ModeControl";
import { identityStrat, shortAddress } from "../lib/identityStrat";
import { useHubBacktests, HUB_WINDOWS, type HubBacktest } from "../lib/hubBacktest";
import { addToDraft, readDraft } from "../lib/basketDraft";
import { describeMarketQuery } from "../lib/marketTypes";
import FindTraders from "./FindTraders";
import { useAuth } from "../context/AuthContext";
import { getOwnerAddress } from "../lib/access";

const ADDR_RE = /^0x[0-9a-fA-F]{40}$/;
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
  useEffect(() => { setBasket(new Set(readDraft().map((l) => l.address))); }, []);

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

  const rows = useMemo(() => book?.allocations ?? [], [book]);

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
        onRebalance={(mode) =>
          mutate("rebalance", () => rebalanceBook(book?.bankroll ?? 0, mode, eoa))
        }
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

      <AddTrader
        busy={busy === "add"}
        existing={new Set(rows.map((r) => r.address))}
        onAdd={(address, allocationUsd, label) =>
          mutate("add", () => upsertAllocation({ address, allocationUsd, label }, eoa))
        }
      />

      {/* Whose trades, chosen by which market. The query that found them
          becomes their gate — `params.marketQuery` is what the live engine and
          the backtest both filter entries on, so the row goes on meaning what
          the search meant. An empty query is explicit too: it CLEARS the gate,
          which is how "actually, copy them everywhere" is said. */}
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
        onAdd={(address, allocationUsd, marketQuery) =>
          mutate("add", () =>
            upsertAllocation(
              { address, allocationUsd, params: { marketQuery } },
              eoa,
            ),
          )
        }
      />

      {book === null ? (
        <div className="pixel-panel p-6 text-center font-mono text-[12px] text-pixel-gray">
          READING THE COPY BOOK…
        </div>
      ) : rows.length === 0 ? (
        <EmptyDesk />
      ) : (
        <div className="space-y-3">
          {rows.map((row) => (
            <TraderRow
              key={row.address}
              row={row}
              eoa={eoa}
              days={days}
              backtest={results[row.strategyId]}
              backtestPending={pending.has(row.strategyId) || btLoading}
              busy={busy}
              onAllocate={(usd) =>
                mutate(`alloc:${row.address}`, () =>
                  upsertAllocation({ address: row.address, allocationUsd: usd }, eoa),
                )
              }
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
              onRemove={() =>
                mutate(`rm:${row.address}`, () => removeAllocation(row.address, eoa))
              }
            />
          ))}
        </div>
      )}
    </div>
  );
}

// ── Header: the desk's totals and its two global switches ──

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
  const over = (t?.unallocatedUsd ?? 0) < 0;

  return (
    <div className="pixel-panel p-4 space-y-3">
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
        <h1 className="font-mono text-[15px] tracking-[0.18em] text-pixel-green">COPY DESK</h1>
        <span className="font-mono text-[11px] text-pixel-gray">
          copy as many traders as you like — one allocation each, gated to the trades you want
        </span>
        <Link
          href="/copy/trades"
          className="font-mono text-[11px] tracking-[0.1em] text-pixel-gray hover:text-green-400"
          title="What the copying actually did: their trades against my fills, and how much of their flow I got"
        >
          MY COPY TRADES →
        </Link>
        {!eoa && (
          <span className="font-mono text-[11px] text-amber-400">
            no wallet — the book reads, but nothing can run
          </span>
        )}
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
        <Stat label="TRADERS" value={String(t?.traders ?? 0)} sub={`${t?.enabled ?? 0} enabled`} />
        <Stat label="ALLOCATED" value={fmtUsd(t?.allocatedUsd, 0)} />
        <Stat
          label={over ? "OVER BY" : "UNALLOCATED"}
          value={fmtUsd(Math.abs(t?.unallocatedUsd ?? 0), 0)}
          tone={over ? "warn" : undefined}
          sub={over ? "more allocated than bankroll" : undefined}
        />
        <Stat label="RUNNING" value={String(t?.running ?? 0)} />
        {/* One stat, both words. "EXECUTING: 0" next to "RUNNING: 5" was the
            desk's version of the question this whole screen exists to answer,
            phrased in a third vocabulary — say it in the two words the rows
            and the switches use. */}
        <Stat
          label="MODE"
          value={
            (t?.running ?? 0) === 0
              ? "—"
              : (t?.executing ?? 0) > 0
                ? `${t?.executing ?? 0} LIVE`
                : "TEST"
          }
          tone={
            (t?.running ?? 0) === 0
              ? undefined
              : (t?.executing ?? 0) > 0
                ? "live"
                : "warn"
          }
          sub={describeFleet(t?.running ?? 0, t?.executing ?? 0)}
        />
        <Stat
          label="BACKTESTS"
          value={`${days}D`}
          sub={workerAt ? `worker ${ago(workerAt)}` : "no worker pass yet"}
        />
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <label className="font-mono text-[11px] text-pixel-gray tracking-[0.1em]">BANKROLL $</label>
        <input
          className="pixel-input-sm w-28 font-mono text-[12px]"
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
          title="Give every enabled trader the same dollars"
        >
          SPLIT EQUAL
        </button>
        <button
          className="pixel-btn text-[11px]"
          disabled={busy !== null || !book?.bankroll}
          onClick={() => onRebalance("weighted")}
          title="Rescale the allocations you set to this bankroll — conviction preserved"
        >
          SPLIT WEIGHTED
        </button>

        <span className="flex-1" />

        {/* The desk sizes one leader at a time. Sizing SEVERAL against each
            other — and asking whether the split beats dividing evenly — is a
            different question and gets its own screen. */}
        <Link
          href="/copy/basket"
          className="pixel-btn text-[11px] border-pixel-green text-pixel-green"
          title="Copy a set of traders with a different amount against each, replayed as one basket"
        >
          BASKET →
        </Link>

        <div className="flex items-center gap-1">
          {HUB_WINDOWS.map((d) => (
            <button
              key={d}
              onClick={() => onDays(d)}
              className={`pixel-btn text-[10px] px-2 ${d === days ? "border-pixel-green text-pixel-green" : ""}`}
              title={`Backtest every trader over ${d} day(s)`}
            >
              {d}D
            </button>
          ))}
          <button className="pixel-btn text-[10px] px-2" onClick={onRefreshBacktests} title="Re-run every row's backtest now">
            ↻
          </button>
        </div>
      </div>

      {/* ── Mode, then run. Two controls, in that order, and the same two the
          strat workspace shows — pick whether the money is real, then press
          the button that starts it. The desk used to fuse them into START ALL
          (DRY RUN) / GO LIVE (REAL MONEY): two buttons that each did two
          things, with no way to change your mind afterwards. */}
      <div className="flex flex-wrap items-center gap-2 border-t border-pixel-gray/20 pt-3">
        <span className="font-mono text-[9px] tracking-[0.14em] text-pixel-gray">MODE</span>
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
          title={`Start every enabled trader in ${mode} — ${MODE[mode].meaning}`}
        >
          START ALL · {mode}
        </button>
        <button
          className="pixel-btn text-[11px] border-red-500 text-red-400"
          disabled={!eoa || busy !== null}
          onClick={onStopAll}
        >
          STOP ALL
        </button>
        <span className="font-mono text-[10px] text-pixel-gray">
          each row has the same two switches
        </span>
      </div>
      <ModeLegend className="block" />
    </div>
  );
}

function Stat({
  label, value, sub, tone,
}: {
  label: string;
  value: string;
  sub?: string;
  tone?: "warn" | "live";
}) {
  const color =
    tone === "warn" ? "text-amber-400" : tone === "live" ? "text-pixel-green" : "text-pixel-gray-light";
  return (
    <div className="border border-pixel-gray/25 rounded-[4px] px-2.5 py-1.5">
      <div className="font-mono text-[9px] tracking-[0.14em] text-pixel-gray">{label}</div>
      <div className={`font-mono text-[15px] ${color}`}>{value}</div>
      {sub && <div className="font-mono text-[9px] text-pixel-gray truncate">{sub}</div>}
    </div>
  );
}

// ── Adding a leader ──

function AddTrader({
  onAdd, busy, existing,
}: {
  onAdd: (address: string, allocationUsd: number, label?: string) => void;
  busy: boolean;
  existing: Set<string>;
}) {
  const [address, setAddress] = useState("");
  const [amount, setAmount] = useState("100");
  const [label, setLabel] = useState("");

  const addr = address.trim().toLowerCase();
  const valid = ADDR_RE.test(addr);
  const dupe = valid && existing.has(addr);
  const usd = Number(amount);
  const amountOk = Number.isFinite(usd) && usd > 0;

  const submit = () => {
    if (!valid || !amountOk) return;
    onAdd(addr, usd, label.trim() || undefined);
    setAddress("");
    setLabel("");
  };

  return (
    <div className="pixel-panel p-4 space-y-3">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <div className="font-mono text-[12px] tracking-[0.14em] text-pixel-gray-light">
          COPY A TRADER YOU ALREADY KNOW
        </div>
        <span className="font-mono text-[10px] text-pixel-gray">
          paste an address — or find one by market below
        </span>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <input
          className={`pixel-input-sm flex-1 min-w-[280px] font-mono text-[12px] ${
            address && !valid ? "border-red-500 text-red-400" : ""
          }`}
          placeholder="0x… trader address"
          value={address}
          onChange={(e) => setAddress(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && submit()}
        />
        <input
          className="pixel-input-sm w-24 font-mono text-[12px]"
          placeholder="$"
          inputMode="decimal"
          value={amount}
          onChange={(e) => setAmount(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && submit()}
        />
        <input
          className="pixel-input-sm w-40 font-mono text-[12px]"
          placeholder="label (optional)"
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && submit()}
        />
        <button
          className="pixel-btn text-[11px]"
          disabled={!valid || !amountOk || busy}
          onClick={submit}
          title={
            dupe
              ? "Already in the book — this updates their allocation"
              : "Add this trader to the copy book"
          }
        >
          {dupe ? "UPDATE" : "ADD"}
        </button>
      </div>

      {address && !valid && (
        <div className="font-mono text-[10px] text-red-400">
          that isn&apos;t a full 0x address — 42 characters, hex
        </div>
      )}
      {dupe && (
        <div className="font-mono text-[10px] text-amber-400">
          already copying this trader — adding again just changes their allocation
        </div>
      )}
    </div>
  );
}

function EmptyDesk() {
  return (
    <div className="pixel-panel p-8 text-center space-y-2">
      <div className="font-mono text-[13px] text-pixel-gray-light">THE COPY BOOK IS EMPTY</div>
      <div className="font-mono text-[11px] text-pixel-gray max-w-[520px] mx-auto">
        Paste a trader&apos;s address above with an amount, or open{" "}
        <Link href="/traders" className="text-pixel-green underline">
          TRADERS
        </Link>{" "}
        and copy someone from their profile. Each trader you add is backtested
        on their own, over the window you pick, before any money moves.
      </div>
    </div>
  );
}

// ── One leader ──

function TraderRow({
  row, eoa, days, backtest, backtestPending, busy, mode,
  onAllocate, onToggleEnabled, onGate, onMode, onStart, onStop, onRemove,
}: {
  row: CopyBookRow;
  eoa: string | null;
  days: number;
  backtest?: HubBacktest;
  backtestPending: boolean;
  busy: string | null;
  onAllocate: (usd: number) => void;
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
  const realized = live?.ledger?.realized ?? null;
  const rowBusy = busy?.endsWith(row.address) ?? false;

  const commit = () => {
    const v = Number(draft);
    if (Number.isFinite(v) && v >= 0 && v !== row.allocationUsd) onAllocate(v);
    else setDraft(String(row.allocationUsd));
  };

  return (
    <div
      className={`pixel-panel p-3 space-y-2 ${row.enabled ? "" : "opacity-60"} ${
        executing ? "border-pixel-green" : ""
      }`}
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        {/* Who. The NAME opens this row's workspace (/copy/<address>): the
            replay, the live session and the wallet behind this one leader. The
            address underneath goes the other way, to their own trading record
            on /traders — two different questions ("how is copying them going"
            vs "are they any good"), so two different links. */}
        <div className="min-w-[200px]">
          <div className="flex items-center gap-2">
            <Link
              href={`/copy/${row.address}`}
              className="font-mono text-[13px] text-pixel-gray-light hover:text-pixel-green"
              title={`Open ${row.name} — backtest, live session and wallet`}
            >
              {row.name}
            </Link>
            <StatusPill running={running} enabled={row.enabled} mode={mode} />
          </div>
          {/* Their record, read under THIS row's gate. `params.marketQuery` is
              what the engine copies them inside of, so it is also the slice of
              their flow worth looking at — a bare link opens their whole tape,
              which for a bitcoin gate is mostly markets this row will never
              touch. The profile shows the keyword as a clearable chip. */}
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
        </div>

        {/* How much — the position sizing model, in one field */}
        <div className="flex items-center gap-1.5">
          <span className="font-mono text-[9px] tracking-[0.14em] text-pixel-gray">COPY WITH $</span>
          <input
            className="pixel-input-sm w-24 font-mono text-[13px]"
            value={draft}
            inputMode="decimal"
            disabled={rowBusy}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={commit}
            onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
          />
        </div>

        {/* How it's actually going, live */}
        <div className="flex items-center gap-4 font-mono text-[10px]">
          <Field
            label="REALIZED"
            value={fmtSigned(realized)}
            tone={realized === null ? undefined : realized >= 0 ? "up" : "down"}
            title="Booked P&L from this leader's mirrors. Open positions are excluded — a mark is a guess, and it reads high."
          />
          <Field label="ORDERS" value={live ? String(live.ordersPlaced) : "—"} />
          <Field label="LAST FILL" value={ago(live?.ledger?.lastFillAt || null)} />
        </div>

        <span className="flex-1" />

        {/* What the replay says about copying them. `min-w-0` so the chip's
            long notes truncate instead of pushing the switches off the row. */}
        <div className="min-w-0">
          <BacktestChip bt={backtest} pending={backtestPending} days={days} />
        </div>

        {/* Switches. Same pair as the desk header and the strat workspace:
            MODE first, then RUN. `shrink-0` keeps them on the row's first
            line — a STOP button that has drifted onto its own line reads as
            belonging to the row below.

            The mode switch stays visible while the session runs, and flipping
            it re-arms in place. Before this the mode was frozen at start: the
            only way from TEST to LIVE was STOP, then start again, which threw
            away the session's cursor and its ledger position. */}
        <div className="flex items-center gap-1.5 shrink-0">
          <button
            className="pixel-btn text-[10px]"
            disabled={rowBusy}
            onClick={onToggleEnabled}
            title={row.enabled ? "Pause: keep the allocation and history, stop starting them" : "Un-pause"}
          >
            {row.enabled ? "PAUSE" : "RESUME"}
          </button>
          <ModeSwitch
            size="sm"
            mode={mode}
            onPick={onMode}
            running={running}
            // Dollars against the name, and a trading wallet that isn't
            // known-empty. `?? 1` because a never-started row has no balance
            // reading yet — unknown isn't empty, and locking LIVE on "we
            // haven't looked" would be its own dead end.
            canGoLive={row.allocationUsd > 0 && (live?.balance ?? 1) > 0}
            subject={row.name}
            amountUsd={row.allocationUsd}
            disabled={!eoa || rowBusy || !row.enabled}
          />
          {running ? (
            <button
              className="pixel-btn text-[10px] border-red-500 text-red-400"
              disabled={rowBusy}
              onClick={onStop}
              title="Stop this leader's session. Open positions are left alone."
            >
              STOP
            </button>
          ) : (
            <button
              className={`pixel-btn text-[10px] ${
                mode === "LIVE" ? "border-green-400 text-green-400" : ""
              }`}
              disabled={!eoa || rowBusy || !row.enabled}
              onClick={onStart}
              title={`Start copying ${row.name} in ${mode} — ${MODE[mode].meaning}`}
            >
              START
            </button>
          )}
          <button
            className="pixel-btn text-[10px]"
            disabled={rowBusy}
            onClick={() => {
              if (window.confirm(`Stop copying ${row.name} and remove them from the book?`)) {
                onRemove();
              }
            }}
            title="Stop the session and drop them from the book"
          >
            ×
          </button>
        </div>
      </div>

      <MarketGate
        query={row.params?.marketQuery ?? ""}
        disabled={rowBusy}
        onChange={onGate}
      />

      {stall && (
        <div className="font-mono text-[10px] text-amber-400 border-t border-pixel-gray/15 pt-1.5">
          {stall}
        </div>
      )}
    </div>
  );
}

/** WHICH of the leader's trades this row copies.
 *
 *  A copy allocation is two decisions, not one: whose trades, and which of
 *  them. The desk only ever showed the first, so a trader picked for their
 *  bitcoin record was quietly copied into their election book as well — and
 *  the row's backtest, which honours the gate, was measuring a different
 *  strategy than the one the search suggested.
 *
 *  The query is the same string the finder above searched with, matched the
 *  same way (`marketMatchesQuery`), applied in `Strat.shouldMirror` and in the
 *  engine's `market_ok` check. Entries only: an exit is never gated, or a
 *  filter change would strand an open position. */
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

  return (
    <div className="flex flex-wrap items-center gap-2 border-t border-pixel-gray/15 pt-1.5 font-mono text-[10px]">
      <span className="text-[9px] tracking-[0.14em] text-pixel-gray">COPIES</span>
      {editing ? (
        <>
          <input
            autoFocus
            className="pixel-input-sm w-64 text-[11px]"
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
          <span className="text-pixel-gray">enter to save · esc to cancel</span>
        </>
      ) : (
        <>
          <button
            className={`pixel-btn text-[10px] px-2 normal-case ${gated ? "border-pixel-green text-pixel-green" : ""}`}
            disabled={disabled}
            onClick={() => setEditing(true)}
            title={
              gated
                ? `Only this leader's trades in markets matching “${query}” are mirrored — click to change`
                : "Every market this leader trades — click to gate it to one topic"
            }
          >
            {gated ? describeMarketQuery(query) : "ALL MARKETS"}
          </button>
          {gated && (
            <>
              <span className="text-pixel-gray normal-case">“{query}”</span>
              <button
                className="pixel-btn text-[10px] px-2"
                disabled={disabled}
                onClick={() => onChange("")}
                title="Drop the gate — copy every market they trade"
              >
                ✕
              </button>
            </>
          )}
        </>
      )}
    </div>
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
  /** Live mode while running, armed mode while stopped — so a stopped row's
      tooltip names the mode START would actually use. */
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

/** The row's replay, in the space of a chip. A backtest is a claim about the
    past; the walk-forward verdict is the only part that says whether the claim
    survived being tested on a window it didn't get to see. */
function BacktestChip({
  bt, pending, days,
}: {
  bt?: HubBacktest;
  pending: boolean;
  days: number;
}) {
  if (pending && !bt) {
    return (
      <div className="font-mono text-[10px] text-pixel-gray min-w-[140px]">
        <div className="text-[9px] tracking-[0.14em]">{days}D BACKTEST</div>
        <div>replaying…</div>
      </div>
    );
  }
  if (!bt) {
    return (
      <div className="font-mono text-[10px] text-pixel-gray min-w-[140px]">
        <div className="text-[9px] tracking-[0.14em]">{days}D BACKTEST</div>
        <div title="No replay yet — the worker covers the desk on its own pass">queued</div>
      </div>
    );
  }
  const good = bt.pnl >= 0;
  const verdict = bt.forward?.verdict;
  // `held` is the only pass: profitable in the prior window AND in this one.
  const verdictTone =
    verdict === "held"
      ? "text-pixel-green"
      : verdict === "faded" || verdict === "no-edge"
        ? "text-red-400"
        : "text-pixel-gray";
  const marked = bt.settlement?.markedUsd ?? 0;
  return (
    <div className="font-mono text-[10px] min-w-[160px]">
      <div className="text-[9px] tracking-[0.14em] text-pixel-gray">
        {bt.days}D BACKTEST{bt.by === "worker" ? "" : " (local)"}
      </div>
      <div className="flex items-baseline gap-2">
        <span className={good ? "text-pixel-green" : "text-red-400"}>{fmtSigned(bt.pnl)}</span>
        <span className="text-pixel-gray">{bt.trades} trades</span>
      </div>
      <div className="flex items-baseline gap-2">
        {verdict ? (
          <span className={verdictTone} title="Walk-forward: replayed over the PREVIOUS window too. `held` is the only pass.">
            {verdict.toUpperCase()}
          </span>
        ) : (
          <span className="text-pixel-gray" title="No walk-forward check on this replay">
            UNCHECKED
          </span>
        )}
        {marked > 0.5 && (
          <span
            className="text-amber-400"
            title={`${fmtUsd(marked)} of this was still open at the end of the window, valued at the last observed price — unresolved marks read HIGH`}
          >
            {fmtUsd(marked, 0)} unsettled
          </span>
        )}
      </div>
      {bt.note && <div className="text-pixel-gray truncate max-w-[220px]" title={bt.note}>{bt.note}</div>}
    </div>
  );
}
