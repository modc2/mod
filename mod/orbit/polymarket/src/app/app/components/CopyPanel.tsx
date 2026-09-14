"use client";

// THE COPY BLOCK — the copy book, in the user sidebar.
//
// Copying is the console's whole job, and it is not one leader at a time: a
// desk is a SET of traders, each with a different amount behind them. So this
// column is built around a roster, not a row —
//
//   TRADERS   the book. One line per leader: the name, their $, run/stop.
//   MEASURE   what those amounts would have done — $N over the last M days,
//             per trader, through the exact pipeline the live engine runs.
//   TRADES    what they actually DID and what I actually got, joined
//             (components/CopyTradesPanel.tsx), filtered by a typed sentence.
//
// Nothing here is browser state. Every read is GET /copy/book and every write
// is a POST /copy/* — the same routes the `pm_copy_*` MCP tools call, so an
// allocation an agent moved shows up here on the next poll and vice versa.
//
// ONE LAYER OF CONTROLS. This block used to stack four copies of the same
// apparatus — a desk-wide PAPER|REAL + START ALL, a SPLIT bar, a checkbox
// bulk bar with its own $ / mode / ▶ / ■, and a PAPER|REAL + ▶ on every row.
// Four mode switches in a 340px column is how "which one is the real one"
// becomes unanswerable. Now:
//
//   • There is ONE mode switch, at the top. A row's ▶ starts that row in the
//     desk's mode; START ALL starts everyone in it. A RUNNING row shows the
//     mode it is actually in by dot color — green REAL, amber PAPER — read
//     off the server, never off a browser toggle.
//   • Rows are one line: name · $ · ▶/■ · ×. No checkboxes, no bulk bar —
//     mixing modes per trader, pausing several at once and sizing the whole
//     set live on the row's own page (/copy/0x…), the desk (/copy) and the
//     basket (/copy/basket). The sidebar is for glancing and the one obvious
//     act, not for the whole instrument panel.
//   • SPLIT $N EVENLY stays as the one bulk sizing gesture, only with ≥2
//     traders to split across.
//   • The roll-up line reconciles the desk against the WALLET. "$100 on 1
//     trader" over "MONEY $0.18" with nothing between them is a lie by
//     juxtaposition; short desks say so, as a link to MONEY.
//
// Three deliberate restraints, all of them about cost:
//
//   • The whole block, hooks and all, only mounts while it is EXPANDED. A
//     docked sidebar is open on every page; a book poll plus a replay sweep on
//     every mount would be a request storm for something nobody is looking at.
//   • MEASURE and TRADES are separately collapsible and separately mounted.
//     The trades feed walks my wallet and reads every leader's stored feed —
//     that is not a thing to run behind a closed section.
//   • The sim amount starts BLANK, meaning "each trader's own allocation".
//     That is the signature the background worker replays, so the default view
//     paints from its cache. Typing an N asks THIS browser to re-replay every
//     row — an explicit act, with a cost.
//
// Sizing note: every dense control here carries `btn-xs` / `input-xs`.
// globals.css loads after Tailwind's utilities, so `.pixel-btn` beats a
// `text-[9px]` on the same element — without the two-class variants a 340px
// column renders desk-sized buttons and wraps.

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";

import { useAuth } from "../context/AuthContext";
import { getOwnerAddress } from "../lib/access";
import { fundedUsd } from "../lib/funding";
import { useCopyBook } from "../lib/useCopyBook";
import type { CopyBookRow } from "../lib/copyBook";
import { identityStrat, shortAddress } from "../lib/identityStrat";
import { useHubBacktests, HUB_WINDOWS, type HubBacktest } from "../lib/hubBacktest";
import { MODE, MODES, confirmGoLive, type TradingMode } from "../lib/tradingMode";
import { type CompiledGate } from "../lib/semanticFilter";
import { confirmGate, gatePatch } from "../lib/armGate";
import CopyTradesPanel from "./CopyTradesPanel";
import { OPEN_MONEY_EVENT } from "./MoneyBlock";

const OPEN_KEY = "poly_copy_panel_open";
const MEASURE_KEY = "poly_copy_measure_open";
const TRADES_KEY = "poly_copy_trades_open";
/** Every 0x… in a pasted blob, however it was separated. A whole-string
    match would refuse a pasted list, a pasted table row, or a trailing space. */
const ADDR_SCAN = /0x[0-9a-fA-F]{40}/g;

function fmtUsd(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  const sign = v < 0 ? "-" : "";
  return `${sign}$${Math.abs(v).toLocaleString("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}`;
}

function fmtSigned(v: number): string {
  return `${v >= 0 ? "+" : ""}${fmtUsd(v)}`;
}

export default function CopyPanel() {
  const [expanded, setExpanded] = useState(false);
  useEffect(() => {
    try {
      setExpanded(localStorage.getItem(OPEN_KEY) !== "0");
    } catch {
      setExpanded(true);
    }
  }, []);

  const toggle = () => {
    setExpanded((e) => {
      const next = !e;
      try {
        localStorage.setItem(OPEN_KEY, next ? "1" : "0");
      } catch {}
      return next;
    });
  };

  return (
    <div className="shrink-0" style={{ borderBottom: "1px solid var(--border)" }}>
      <button
        onClick={toggle}
        aria-expanded={expanded}
        className="w-full px-3 py-2 flex items-center gap-2 text-left hover:bg-pixel-white/[0.06] transition-colors"
        title="The copy book — which traders you copy, with how much, what that money would have done, and what it actually did"
      >
        <span className="min-w-0 flex-1">
          <span className="block text-[9.5px] font-mono tracking-[0.14em] text-pixel-gray">
            WHO I COPY
          </span>
          <span className="block truncate text-[11.5px] font-mono text-cyan-300">
            the traders, their dollars, start / stop
          </span>
        </span>
        <span className="text-[9px] text-pixel-gray shrink-0">{expanded ? "▲" : "▼"}</span>
      </button>
      {/* Mounted only while showing — see the file header. */}
      {expanded && <CopyBookBody />}
    </div>
  );
}

function CopyBookBody() {
  const { auth } = useAuth();
  // Single-owner deployment: the wallet that signed into the gate IS the
  // funded one, and auth.address lags a wallet switch. Same rule as the desk.
  const eoa = getOwnerAddress() ?? auth.address ?? null;
  const {
    book, rows, error, busy, deskMode, arm,
    allocate, remove, setEnabled, setBankrollUsd, rebalance, start, stop,
  } = useCopyBook(eoa);

  // ── Sections. Each is its own mount, because each has its own cost. ──
  const [measureOpen, setMeasureOpen] = useState(false);
  const [tradesOpen, setTradesOpen] = useState(false);
  useEffect(() => {
    try {
      setMeasureOpen(localStorage.getItem(MEASURE_KEY) !== "0");
      setTradesOpen(localStorage.getItem(TRADES_KEY) === "1");
    } catch {
      setMeasureOpen(true);
    }
  }, []);
  const remember = (key: string, v: boolean) => {
    try { localStorage.setItem(key, v ? "1" : "0"); } catch {}
  };

  const [bankrollDraft, setBankrollDraft] = useState("");
  useEffect(() => {
    if (book) setBankrollDraft(book.bankroll ? String(book.bankroll) : "");
  }, [book?.bankroll]); // eslint-disable-line react-hooks/exhaustive-deps

  const totals = book?.totals;
  const running = totals?.running ?? 0;

  // ── What is actually spendable, so the desk can't quote a size the wallet
  //    cannot cover. Same read MoneyBlock's collapsed line uses; slow cadence
  //    because this block is docked on every page. ──
  const [funded, setFunded] = useState<number | null>(null);
  useEffect(() => {
    if (!eoa) { setFunded(null); return; }
    let dead = false;
    const read = async () => {
      const v = await fundedUsd(eoa);
      if (!dead) setFunded(v);
    };
    void read();
    const t = setInterval(() => void read(), 60_000);
    return () => { dead = true; clearInterval(t); };
  }, [eoa]);

  const allocated = totals?.allocatedUsd ?? 0;
  /** Short only when we actually READ a balance — a failed read is not $0. */
  const short = funded !== null && allocated > funded + 0.01;

  /** SPLIT only exists with something to split across. */
  const many = rows.length > 1;

  /** Arm a typed sentence as a real gate, on the whole book. The confirm is
      shared with /copy/trades (lib/armGate.ts) so both say the same thing. */
  const armGate = async (gate: CompiledGate) => {
    const names = rows.map((r) => r.label?.trim() || shortAddress(r.address));
    if (!confirmGate(gate, names)) return;
    for (const row of rows) {
      await allocate(row.address, row.allocationUsd, undefined, gatePatch(gate));
    }
  };

  return (
    <div className="pb-2">
      {/* ── The desk in one line: money, names, run state ── */}
      <div className="px-3 pb-1.5 flex items-baseline gap-1.5">
        <span
          className="text-[15px] font-mono font-semibold tabular-nums text-pixel-white"
          title={`${fmtUsd(allocated, 2)} allocated across ${totals?.traders ?? 0} trader(s)`}
        >
          {fmtUsd(allocated, 0)}
        </span>
        <span className="text-[10px] font-mono text-pixel-gray">
          on {totals?.traders ?? 0} trader{(totals?.traders ?? 0) === 1 ? "" : "s"}
        </span>
        <span className="flex-1" />
        {running > 0 ? (
          <span
            className="text-[9.5px] font-mono text-green-400 flex items-center gap-1"
            title={`${running} session(s) running, ${totals?.executing ?? 0} of them placing real orders`}
          >
            <span className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse" />
            {running} RUNNING{(totals?.executing ?? 0) > 0 ? ` · ${totals?.executing} ${MODE.LIVE.label}` : ` · ${MODE.TEST.label}`}
          </span>
        ) : (
          <span className="text-[9.5px] font-mono text-pixel-gray">NOT RUNNING</span>
        )}
      </div>

      {/* ── The desk against the WALLET. A size the balance cannot cover is
             the single fact this column used to omit. ── */}
      {short && allocated > 0 && (
        <button
          onClick={() => window.dispatchEvent(new Event(OPEN_MONEY_EVENT))}
          className="mx-3 mb-1.5 w-[calc(100%-1.5rem)] text-left text-[9.5px] font-mono text-amber-400 hover:text-amber-300 leading-snug"
          title="Your Polymarket balance is smaller than what the desk is sized at. REAL orders will be cut down to what's there. Click to top up."
        >
          your wallet has {fmtUsd(funded)} — REAL orders size down to that
          <span className="block underline">TOP UP →</span>
        </button>
      )}

      {/* ── THE control row. One mode switch for the whole panel: rows start
             in it, START ALL starts everyone in it. ── */}
      {rows.length > 0 && (
        <div className="px-3 pb-1.5 flex items-center gap-1.5">
          <ModeSwitch
            mode={deskMode}
            canGoLive={allocated > 0}
            onPick={(m) => arm("", m)}
          />
          <button
            className="pixel-btn btn-xs flex-1"
            disabled={busy !== null || !eoa || rows.length === 0}
            onClick={() => {
              if (deskMode === "LIVE" && !confirmGoLive(`all ${rows.length} traders`, allocated)) return;
              void start(undefined, deskMode);
            }}
            title={eoa ? `Start every enabled trader in ${MODE[deskMode].label}` : "Sign in a wallet first"}
          >
            ▶ START{many ? " ALL" : ""}
          </button>
          <button
            className="pixel-btn btn-xs border-red-400/60 text-red-400"
            disabled={busy !== null || !eoa || running === 0}
            onClick={() => void stop()}
            title="Stop every running session"
          >
            ■ STOP{many ? " ALL" : ""}
          </button>
        </div>
      )}

      {error && (
        <div className="mx-3 mb-1.5 px-2 py-1 font-mono text-[10px] text-red-300 border border-red-400/40 rounded-[3px]">
          {error}
        </div>
      )}

      {!eoa && (
        <div className="px-3 pb-1.5 text-[9.5px] font-mono text-amber-400 leading-snug">
          no wallet connected — nothing can START until you sign in
        </div>
      )}

      {/* ── The book. Capped and scrollable: the sections below it are
             furniture too, and neither gets to push the other off screen. ── */}
      <div className="max-h-[34vh] overflow-y-auto px-1.5 space-y-0.5">
        {book === null ? (
          <div className="px-1.5 py-2 text-[10.5px] font-mono text-pixel-gray">
            reading the copy book…
          </div>
        ) : rows.length === 0 ? (
          <div className="px-1.5 py-2 text-[10.5px] font-mono text-pixel-gray leading-snug">
            No traders yet. Paste one address below — or a whole list of them, one
            amount each.
          </div>
        ) : (
          rows.map((row) => (
            <CopyRow
              key={row.address}
              row={row}
              busy={busy}
              deskMode={deskMode}
              onAllocate={(usd) => void allocate(row.address, usd)}
              onResume={() => void setEnabled(row, true)}
              onStart={() => {
                if (deskMode === "LIVE" && !confirmGoLive(row.name, row.allocationUsd)) return;
                void start(row.address, deskMode);
              }}
              onStop={() => void stop(row.address)}
              onRemove={() => void remove(row.address)}
              canRun={!!eoa}
            />
          ))
        )}
      </div>

      {/* ── The one bulk sizing gesture: divide a total across the set. ── */}
      {many && (
        <div className="px-3 pt-1.5 flex items-center gap-1.5">
          <span className="text-[9px] font-mono tracking-[0.12em] text-pixel-gray shrink-0">
            SPLIT $
          </span>
          <input
            className="pixel-input-sm input-xs w-[62px] font-mono"
            value={bankrollDraft}
            inputMode="decimal"
            onChange={(e) => setBankrollDraft(e.target.value)}
            placeholder="total"
            title="A total to divide across the book. It is not a budget — the engine budgets against each trader's own $."
          />
          <button
            className="pixel-btn btn-xs"
            disabled={busy !== null || !(Number(bankrollDraft) > 0)}
            onClick={async () => {
              const v = Number(bankrollDraft);
              if (!Number.isFinite(v) || v <= 0) return;
              if (v !== book?.bankroll) await setBankrollUsd(v);
              await rebalance("equal");
            }}
            title={`Give each of the ${rows.length} traders the same share of this total`}
          >
            EVENLY
          </button>
          <Link
            href="/copy/basket"
            className="ml-auto text-[9.5px] font-mono tracking-[0.1em] text-pixel-gray hover:text-cyan-300 shrink-0"
            title="Size the whole set at once: different amounts per trader, replayed together, with the equal-split counterfactual"
          >
            BASKET →
          </Link>
        </div>
      )}

      {/* ── Add one, or add ten ── */}
      <AddTraders
        busy={busy !== null}
        onAdd={async (addresses, usd) => {
          for (const a of addresses) await allocate(a, usd);
        }}
      />

      {/* ── MEASURE — what those amounts would have done ── */}
      <Section
        title="BACKTEST"
        hint="what $N would have done, per trader"
        open={measureOpen}
        onToggle={() => { const v = !measureOpen; setMeasureOpen(v); remember(MEASURE_KEY, v); }}
      >
        <MeasureBlock
          rows={rows}
          busy={busy !== null}
          onFund={(usd) => {
            for (const r of rows) void allocate(r.address, usd);
          }}
        />
      </Section>

      {/* ── TRADES — what they did, and what I got ── */}
      <Section
        title="RESULTS"
        hint="their trades vs mine"
        open={tradesOpen}
        onToggle={() => { const v = !tradesOpen; setTradesOpen(v); remember(TRADES_KEY, v); }}
      >
        <div className="px-2 pb-1">
          <CopyTradesPanel compact defaultDays={1} onArm={(g) => void armGate(g)} />
          <div className="mt-1 flex items-center justify-between">
            <span className="text-[9px] font-mono text-pixel-gray/70">
              ARM applies to every trader
            </span>
            <Link
              href="/copy/trades"
              className="text-[9.5px] font-mono tracking-[0.1em] text-pixel-gray hover:text-green-400"
              title="The full board: every trade, per-leader coverage, and the sentence box"
            >
              FULL BOARD →
            </Link>
          </div>
        </div>
      </Section>
    </div>
  );
}

/** A collapsible sub-section of the column. Its body is UNMOUNTED when closed,
    which is the point — each one of these polls something. */
function Section({
  title, hint, open, onToggle, children,
}: {
  title: string;
  hint: string;
  open: boolean;
  onToggle: () => void;
  children: React.ReactNode;
}) {
  return (
    // Indented and hairline-ruled: a full-width border at var(--border) is
    // what the DRAWER's own sections (ACCOUNT, MONEY) wear, and wearing it
    // here made these read as two more top-level panels instead of two parts
    // of the copy block.
    <div className="mt-1.5 ml-3 pl-2" style={{ borderLeft: "1px solid var(--border-strong)" }}>
      <button
        onClick={onToggle}
        aria-expanded={open}
        className="w-full pr-3 py-1 flex items-center gap-2 text-left hover:bg-pixel-white/[0.05] transition-colors"
      >
        <span className="text-[9px] font-mono tracking-[0.14em] text-pixel-gray">{title}</span>
        <span className="text-[9px] font-mono text-pixel-gray/60 truncate">{hint}</span>
        <span className="ml-auto text-[8px] text-pixel-gray shrink-0">{open ? "▲" : "▼"}</span>
      </button>
      {open && children}
    </div>
  );
}

/** PAPER | REAL, one implementation. The law lives in lib/tradingMode.ts. */
function ModeSwitch({
  mode, canGoLive, onPick, disabled,
}: {
  mode: TradingMode;
  canGoLive: boolean;
  onPick: (m: TradingMode) => void;
  disabled?: boolean;
}) {
  return (
    <div className="inline-flex items-center rounded-[3px] border border-pixel-border/70 p-[1px] gap-[1px] shrink-0">
      {MODES.map((m) => {
        const active = m === mode;
        const locked = disabled || (m === "LIVE" && !canGoLive);
        return (
          <button
            key={m}
            onClick={() => !locked && onPick(m)}
            disabled={locked}
            className={`px-1 text-[8.5px] font-mono tracking-[0.08em] ${
              active
                ? m === "LIVE"
                  ? "bg-green-400/20 text-green-400"
                  : "bg-amber-400/20 text-amber-400"
                : locked
                  ? "text-pixel-gray/40"
                  : "text-pixel-gray hover:text-pixel-white"
            }`}
            title={
              locked
                ? "Give them dollars before copying for real"
                : m === "LIVE"
                  ? "Real orders with real USDC"
                  : "Watch and log — no orders placed"
            }
          >
            {MODE[m].label}
          </button>
        );
      })}
    </div>
  );
}

/** Paste one address or a whole list. Anything that looks like an address in
    the blob is taken — a copied table, a comma list and a column of lines all
    behave the same, which is how "copy these five" stops being five gestures. */
function AddTraders({
  busy, onAdd,
}: {
  busy: boolean;
  onAdd: (addresses: string[], usd: number) => void | Promise<void>;
}) {
  const [raw, setRaw] = useState("");
  const [usd, setUsd] = useState("100");
  const found = useMemo(() => {
    const hits = raw.match(ADDR_SCAN) ?? [];
    return Array.from(new Set(hits.map((a) => a.toLowerCase())));
  }, [raw]);
  const amount = Number(usd);
  const valid = found.length > 0 && Number.isFinite(amount) && amount >= 0;
  const partial = raw.trim().length > 0 && found.length === 0;

  return (
    <div className="px-3 pt-1.5 space-y-1">
      <textarea
        className="pixel-input-sm input-xs w-full font-mono resize-y"
        rows={raw.includes("\n") || found.length > 1 ? 3 : 1}
        value={raw}
        placeholder="0x… paste one trader, or a list"
        onChange={(e) => setRaw(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey && valid) {
            e.preventDefault();
            void onAdd(found, amount);
            setRaw("");
          }
        }}
        title="Every 0x… in what you paste is added, each with the amount beside it. Shift+Enter for a new line."
      />
      <div className="flex items-center gap-1.5">
        <span className="text-[9px] font-mono tracking-[0.12em] text-pixel-gray shrink-0">
          EACH $
        </span>
        <input
          className="pixel-input-sm input-xs w-[56px] font-mono"
          value={usd}
          inputMode="decimal"
          onChange={(e) => setUsd(e.target.value)}
          title="Dollars to give EACH trader you paste above. Traders already in the book keep their own $ — edit it on their row."
        />
        <button
          className="pixel-btn btn-xs"
          disabled={!valid || busy}
          onClick={() => { void onAdd(found, amount); setRaw(""); }}
          title={found.length > 1
            ? `Add all ${found.length} traders with ${usd} each`
            : "Add this leader to the copy book"}
        >
          + COPY{found.length > 1 ? ` ${found.length}` : ""}
        </button>
        {partial && (
          <span className="text-[9px] font-mono text-amber-400" title="An address is 0x followed by 40 hex characters">
            no address found
          </span>
        )}
        <Link
          href="/copy"
          className="ml-auto text-[9.5px] font-mono tracking-[0.1em] text-pixel-gray hover:text-green-400 shrink-0"
          title="The desk — find the best traders in a market"
        >
          FIND TRADERS →
        </Link>
      </div>
    </div>
  );
}

/** "$N over the last M days", for every row at once. */
function MeasureBlock({
  rows, busy, onFund,
}: {
  rows: CopyBookRow[];
  busy: boolean;
  onFund: (usd: number) => void;
}) {
  const [days, setDays] = useState(1);
  const [simStr, setSimStr] = useState("");
  const [sim, setSim] = useState(0);
  useEffect(() => {
    const n = Number(simStr);
    const t = setTimeout(() => setSim(simStr.trim() && Number.isFinite(n) && n > 0 ? n : 0), 400);
    return () => clearTimeout(t);
  }, [simStr]);

  const strats = useMemo(
    () => rows.map((r) => identityStrat(sim > 0 ? { ...r, allocationUsd: sim } : r)),
    [rows, sim],
  );
  // `publish: false` — the worker reads the copy book itself; publishing this
  // roster would overwrite the strat hub's manifest with it.
  const { results, pending, loading, worker, refresh } =
    useHubBacktests(strats, days, { publish: false, templates: false });

  return (
    <div className="px-3 pb-1 space-y-1.5">
      <div className="flex items-center gap-1.5">
        <span className="text-[9px] font-mono tracking-[0.12em] text-pixel-gray shrink-0">TRY $</span>
        <input
          className="pixel-input-sm input-xs w-[52px] font-mono"
          value={simStr}
          inputMode="decimal"
          placeholder="each"
          onChange={(e) => setSimStr(e.target.value)}
          title="Replay every row with THIS much behind the trader. Blank = each row's own allocation, which is the number the background worker already replayed."
        />
        <div className="flex items-center gap-0.5 flex-1">
          {HUB_WINDOWS.map((d) => (
            <button
              key={d}
              onClick={() => setDays(d)}
              className={`pixel-btn btn-xs flex-1 ${d === days ? "border-pixel-green text-pixel-green" : ""}`}
              title={`Replay the last ${d} day(s)`}
            >
              {d}D
            </button>
          ))}
        </div>
        <button onClick={refresh} className="pixel-btn btn-xs shrink-0" title="Re-run every row's replay in this browser now">
          ⟳
        </button>
      </div>
      <div className="flex items-baseline gap-1.5">
        <span className="text-[9px] font-mono text-pixel-gray leading-snug min-w-0 flex-1">
          {sim > 0
            ? `what ${fmtUsd(sim, 0)} behind each trader would have done over ${days}D`
            : `replayed at the $ you already gave each of them, over ${days}D`}
          {worker?.at ? "" : " · no worker pass yet"}
        </span>
        {/* Simulated a size and liked it? Fund exactly what was measured. */}
        {sim > 0 && rows.length > 0 && (
          <button
            className="pixel-btn btn-xs shrink-0"
            disabled={busy}
            onClick={() => onFund(sim)}
            title={`Set all ${rows.length} traders to the ${fmtUsd(sim, 0)} this replay used`}
          >
            USE {fmtUsd(sim, 0)}
          </button>
        )}
      </div>
      <div className="space-y-0.5 max-h-[26vh] overflow-y-auto">
        {rows.length === 0 ? (
          <div className="text-[9.5px] font-mono text-pixel-gray">nothing to measure yet</div>
        ) : (
          rows.map((r) => (
            <div key={r.address} className="flex items-baseline gap-1.5">
              <span className="min-w-0 flex-1 truncate text-[10px] font-mono text-pixel-white" title={r.address}>
                {r.label?.trim() || shortAddress(r.address)}
              </span>
              <BacktestLine
                bt={results[r.strategyId]}
                pending={pending.has(r.strategyId) || loading}
                days={days}
                simAmount={sim}
              />
            </div>
          ))
        )}
      </div>
    </div>
  );
}

/** One leader, one line: the name, the money, run/stop, drop. A RUNNING row's
    dot says which mode the SERVER has it in — green REAL, amber PAPER — so
    the roster never renders a mode the engine isn't actually in. */
function CopyRow({
  row, busy, deskMode, canRun,
  onAllocate, onResume, onStart, onStop, onRemove,
}: {
  row: CopyBookRow;
  busy: string | null;
  deskMode: TradingMode;
  canRun: boolean;
  onAllocate: (usd: number) => void;
  onResume: () => void;
  onStart: () => void;
  onStop: () => void;
  onRemove: () => void;
}) {
  const [draft, setDraft] = useState(String(row.allocationUsd));
  // The book is re-read every 15s and an agent may have moved this number —
  // follow the server unless the field is being edited.
  const [editing, setEditing] = useState(false);
  useEffect(() => {
    if (!editing) setDraft(String(row.allocationUsd));
  }, [row.allocationUsd, editing]);

  const running = !!row.live?.running;
  const real = !!row.live?.autoExecute;
  const rowBusy = busy?.endsWith(row.address) ?? false;
  const gated = !!row.params?.marketQuery?.trim() || !!row.params?.tradeFilters;

  const commit = () => {
    setEditing(false);
    const v = Number(draft);
    if (Number.isFinite(v) && v >= 0 && v !== row.allocationUsd) onAllocate(v);
    else setDraft(String(row.allocationUsd));
  };

  return (
    <div
      className={`rounded-[var(--radius-sm)] px-1.5 py-1 ${
        running ? "bg-green-400/[0.07]" : "hover:bg-pixel-white/[0.05]"
      }`}
    >
      <div className="flex items-center gap-1.5">
        {running && (
          <span
            className={`w-1.5 h-1.5 rounded-full animate-pulse shrink-0 ${real ? "bg-green-400" : "bg-amber-400"}`}
            title={`Copying in ${real ? MODE.LIVE.label : MODE.TEST.label} — ${row.live?.ordersPlaced ?? 0} orders placed`}
          />
        )}
        <Link
          href={`/copy/${row.address}`}
          className="min-w-0 flex-1 truncate text-[11px] font-mono font-semibold text-pixel-white hover:text-green-400"
          title={`${row.address} — open this copy's own screen: its trades, its wallet, its own mode and knobs`}
        >
          {row.label?.trim() || shortAddress(row.address)}
        </Link>
        {/* A paused row has to be un-pausable from where it says it's
            paused, or the badge is a dead end. */}
        {!row.enabled && (
          <button
            onClick={onResume}
            disabled={rowBusy}
            className="text-[8.5px] font-mono tracking-[0.1em] text-pixel-gray hover:text-green-400 border border-pixel-border/60 hover:border-green-400/60 px-1 shrink-0"
            title="Paused — kept out of START ALL. Click to resume."
          >
            PAUSED
          </button>
        )}
        <span className="text-[9px] font-mono text-pixel-gray shrink-0">$</span>
        <input
          className="pixel-input-sm input-xs w-[52px] font-mono shrink-0"
          value={draft}
          inputMode="decimal"
          onFocus={() => setEditing(true)}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === "Enter") (e.target as HTMLInputElement).blur();
            if (e.key === "Escape") { setEditing(false); setDraft(String(row.allocationUsd)); }
          }}
          title="Dollars behind this leader — the live engine budgets against it"
        />
        {running ? (
          <button
            onClick={onStop}
            disabled={rowBusy}
            className="pixel-btn btn-xs border-red-400/60 text-red-400 shrink-0"
            title="Stop copying this trader — others keep running"
          >
            ■
          </button>
        ) : (
          <button
            onClick={onStart}
            disabled={rowBusy || !canRun}
            className="pixel-btn btn-xs shrink-0"
            title={canRun ? `Start copying in ${MODE[deskMode].label} — the switch above picks the mode` : "Sign in a wallet first"}
          >
            ▶
          </button>
        )}
        <button
          onClick={onRemove}
          disabled={rowBusy}
          className="text-[12px] leading-none text-pixel-gray hover:text-red-400 shrink-0"
          title="Stop copying this trader and drop them from the book"
        >
          ×
        </button>
      </div>

      {/* The gate this copy runs under — the markets AND the trades inside
          them. A row that only copies one slice has to say which. */}
      {gated && <GateLine row={row} />}
    </div>
  );
}

/** The one-line rendering of a per-leader gate. */
function GateLine({ row }: { row: CopyBookRow }) {
  const q = row.params?.marketQuery?.trim();
  const f = row.params?.tradeFilters;
  const bits: string[] = [];
  if (f?.sides && f.sides !== "both") bits.push(f.sides.toUpperCase());
  if (f?.minPrice !== undefined || f?.maxPrice !== undefined) {
    bits.push(`${Math.round((f.minPrice ?? 0) * 100)}–${Math.round((f.maxPrice ?? 1) * 100)}¢`);
  }
  if (f?.minNotional !== undefined) bits.push(`≥$${f.minNotional}`);
  if (f?.maxNotional !== undefined) bits.push(`≤$${f.maxNotional}`);
  const groups = q ? q.split(",").length : 0;
  return (
    <div
      className="mt-0.5 truncate text-[9.5px] font-mono text-amber-300/80"
      title={`Only copies:${q ? `\n  markets matching — ${q}` : ""}${bits.length ? `\n  trades — ${bits.join(", ")}` : ""}`}
    >
      ⌕ {q ? `${groups} pattern${groups === 1 ? "" : "s"}` : "any market"}
      {bits.length ? ` · ${bits.join(" · ")}` : ""}
    </div>
  );
}

/** The replay, in one line of a 340px column. It never prints an empty result
    as breaking even: a strat whose every candidate was gated has a `note`, and
    that is the answer, not $0. */
function BacktestLine({
  bt, pending, days, simAmount,
}: {
  bt?: HubBacktest;
  pending: boolean;
  days: number;
  simAmount: number;
}) {
  if (!bt) {
    return (
      <span className="text-[9.5px] font-mono text-pixel-gray shrink-0">
        {days}D {pending ? "replaying…" : "queued"}
      </span>
    );
  }
  const verdict = bt.forward?.verdict;
  const marked = bt.settlement?.markedUsd ?? 0;
  // Nothing traded. "+$0.00 0t IDLE ⚠" is four tokens for one fact, and IDLE
  // sitting under the desk's NOT RUNNING reads as a run state, which it is
  // not. Say the fact, and let the note say why.
  if (bt.trades === 0) {
    return (
      <span
        className="text-[9.5px] font-mono text-pixel-gray shrink-0"
        title={bt.note ?? `No trade of theirs landed inside the last ${bt.days}D window.`}
      >
        no trades in {bt.days}D{bt.note && <span className="text-amber-400"> ⚠</span>}
      </span>
    );
  }
  return (
    <span
      className="text-[9.5px] font-mono text-pixel-gray shrink-0 tabular-nums"
      title={`${fmtSigned(bt.pnl)} on ${fmtUsd(bt.capital, 0)} over ${bt.days}D · ${bt.trades} trades · ${
        bt.by === "worker" ? "background worker" : "replayed in this browser"
      }${marked > 0.5 ? ` · ${fmtUsd(marked)} still open at the end of the window, valued at the last observed price` : ""}${
        bt.note ? `\n${bt.note}` : ""
      }`}
    >
      <span className={bt.pnl > 0 ? "text-green-400" : bt.pnl < 0 ? "text-red-400" : ""}>
        {fmtSigned(bt.pnl)}
      </span>{" "}
      {simAmount > 0 ? "(sim) " : ""}
      {bt.trades}t
      {verdict && (
        <span
          className={
            verdict === "held"
              ? " text-pixel-green"
              : verdict === "faded" || verdict === "no-edge"
                ? " text-red-400"
                : ""
          }
          title="Walk-forward: replayed over the PREVIOUS window too. `held` is the only pass."
        >
          {" "}{verdict.toUpperCase()}
        </span>
      )}
      {bt.note && <span className="text-amber-400" title={bt.note}> ⚠</span>}
    </span>
  );
}
