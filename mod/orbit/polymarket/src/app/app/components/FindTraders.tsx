"use client";

// ADD TRADERS — the desk's one way in.
//
// One box. Paste a 0x address and ADD puts dollars behind that name. Type a
// market topic (or tap a preset) and FIND ranks the leaderboard on their
// record IN THAT MARKET ONLY — not lifetime P&L next to a filter. The query
// that found a trader is stored on the allocation as `params.marketQuery`, so
// the live engine and the backtest copy exactly the slice you ranked them on.
// Find on bitcoin, copy on bitcoin.
//
// Two things about the data, both surfaced rather than hidden:
//
//   • Only the windows the server's hourly warmup aggregates answer instantly
//     (1/7/14/30D). A window whose cache is cold says so and offers to warm it
//     here — the old panel sent you to another page to press SYNC.
//   • A filtered request answered from the DISK cache has no per-market
//     breakdown, so its numbers are lifetime. `statsAreScoped` detects that
//     from the payload and the panel says so.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";

import {
  API_BASE, fetchTradersPage, formatHistory, formatPnl, formatVolume, historyDays, timeAgo,
  WARMED_CANDIDATE_POOL, DEFAULT_ACTIVE_HOURS, type TopTrader,
} from "../lib/polymarket";
import { marketMatchesQuery } from "../lib/marketQuery";
import { MARKET_TYPES, matchPreset } from "../lib/marketTypes";
import { shortAddress } from "../lib/identityStrat";
import {
  DEFAULT_FORMULA, FORMULA_VARS, compileFormula, formatScore, scoreInputs,
  loadSavedFormula, matchScorePreset, saveFormula,
} from "../lib/scoreFormula";
import ScoreRatioChips from "./ScoreRatioChips";
import Sparkline from "./Sparkline";
import {
  addPicks, clearPicks, removePicks, setPickDays, togglePick as storeTogglePick, usePicks,
} from "../lib/pickStore";
import { OPEN_SIDEBAR_EVENT } from "./UserSidebar";

const ADDR_RE = /^0x[0-9a-fA-F]{40}$/;
/** Windows the hourly warmup aggregates (`warmup_cycle`, pipeline.rs). */
const WINDOWS = [1, 7, 14, 30] as const;
const SORTS = [
  { key: "pnl", label: "P&L" },
  { key: "sharpe", label: "SHARPE" },
  { key: "winRate", label: "WIN %" },
  { key: "trades", label: "TRADES" },
  { key: "volume", label: "VOLUME" },
  { key: "last", label: "RECENT" },
  { key: "history", label: "TRACK RECORD" },
  { key: "score", label: "CUSTOM SCORE" },
] as const;

/** Track-record presets for the HISTORY ≥ control.
 *
 *  0 is the default and it is deliberate: this is a user's call, not a house
 *  rule. Ranking over 30 days of a wallet that opened six days ago is a
 *  perfectly reasonable thing to want to look at — it just should not be
 *  something the console does to you without saying so. */
const HISTORY_FLOORS = [0, 7, 14, 30, 90] as const;

/** Is this trader's whole record shorter than the window being ranked?
 *  Then part of that window is a straight line through their non-existence —
 *  which is what a flat first-25-days backtest curve actually is. Unknown
 *  ages are not flagged; a missing start date isn't a short one. */
function tooNewForWindow(t: TopTrader, windowDays: number): boolean {
  const d = historyDays(t);
  return d !== null && d < windowDays;
}
const PAGE_SIZE = 12;
/** CUSTOM SCORE is a client-side rank (the formula is JS), so the server
    can't page it. Instead the panel pulls this many rows — ordered by the
    score preset's own metric (win rate by default), or by Sharpe for a
    hand-written formula — and ranks THEM with the formula. */
const SCORE_POOL = 200;
type SortDir = "asc" | "desc";

/** How long to keep re-asking after WARM before giving up (10s × 24). */
const WARM_POLLS = 24;

function statsAreScoped(traders: TopTrader[], query: string): boolean {
  if (!query.trim() || traders.length === 0) return true;
  return traders.every((t) => t.marketTitles.every((title) => marketMatchesQuery(title, query)));
}

interface Props {
  /** Adds the trader to the copy book, gated to `marketQuery` ("" = every market). */
  onAdd: (address: string, allocationUsd: number, marketQuery: string) => void;
  /** Shortlist into the BASKET draft instead (/copy/basket). */
  onBasket?: (address: string, allocationUsd: number, marketQuery: string) => void;
  inBasket?: Set<string>;
  busy: boolean;
  /** Addresses already in the book — ADD becomes UPDATE. */
  existing: Set<string>;
}

export default function FindTraders({ onAdd, onBasket, inBasket, busy, existing }: Props) {
  const [query, setQuery] = useState("");
  // 1D is the window the warmup always has ready — the panel opens on a
  // search that answers, not on a cold-cache apology.
  const [days, setDays] = useState<number>(1);
  const [sort, setSort] = useState<string>("pnl");
  const [order, setOrder] = useState<SortDir>("desc");
  const [activeOnly, setActiveOnly] = useState(true);
  // Track-record floor, in days. Off by default — see HISTORY_FLOORS.
  const [minHistoryDays, setMinHistoryDays] = useState<number>(0);
  const [amount, setAmount] = useState("100");

  // The custom SCORE formula — shared with the /traders board via one
  // sessionStorage key, so a formula written there ranks here too.
  const [formula, setFormula] = useState<string>(DEFAULT_FORMULA);
  useEffect(() => { setFormula(loadSavedFormula()); }, []);
  useEffect(() => { saveFormula(formula); }, [formula]);
  const compiled = useMemo(() => compileFormula(formula), [formula]);
  // The pool the formula re-ranks is server-ordered — by the preset's own
  // metric when the formula IS a preset (win rate out of the box), by Sharpe
  // for hand-written expressions.
  const scorePoolSort = matchScorePreset(formula)?.key ?? "sharpe";
  const scoreFor = useCallback(
    (t: TopTrader): number =>
      compiled.fn ? compiled.fn(scoreInputs(t)) : Number.NEGATIVE_INFINITY,
    [compiled],
  );

  const [rows, setRows] = useState<TopTrader[] | null>(null);
  const [total, setTotal] = useState(0);
  const [poolCount, setPoolCount] = useState(0);
  const [syncedAt, setSyncedAt] = useState(0);
  const [ran, setRan] = useState({ query: "", days: 1, activeOnly: true, sort: "pnl", order: "desc" as SortDir, minHistoryDays: 0 });
  const [dropped, setDropped] = useState(0);
  const [historyDropped, setHistoryDropped] = useState(0);
  const [historyKnown, setHistoryKnown] = useState(0);
  const [loading, setLoading] = useState(false);
  const [cold, setCold] = useState(false);
  const [warming, setWarming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const warmTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // ── The selection ──
  //
  // Checked rows live in lib/pickStore, not here: the tray that replays and
  // commits them is a block of the user sidebar (SelectionTray.tsx), where a
  // shortlist stays in view however far the page scrolls. Each pick remembers
  // the $ and the QUERY it was checked under, so a selection built across
  // several searches (two from bitcoin, one from nba) keeps each name's gate.
  const { picks } = usePicks();
  const picked = useMemo(() => new Set(picks.map((p) => p.address)), [picks]);
  // The sidebar replays every pick over this panel's window — keep it told.
  useEffect(() => { setPickDays(days); }, [days]);

  const usd = Number(amount);
  const amountOk = Number.isFinite(usd) && usd > 0;
  const preset = matchPreset(query);
  const typed = query.trim().toLowerCase();
  const isAddress = ADDR_RE.test(typed);
  const already = isAddress && existing.has(typed);

  const run = useCallback(
    async (opts: { query?: string; days?: number; sort?: string; order?: SortDir; activeOnly?: boolean; minHistoryDays?: number } = {}) => {
      const q = (opts.query ?? query).trim();
      const d = opts.days ?? days;
      const s = opts.sort ?? sort;
      const o = opts.order ?? order;
      const recent = opts.activeOnly ?? activeOnly;
      const minHist = opts.minHistoryDays ?? minHistoryDays;
      setLoading(true);
      setError(null);
      try {
        // CUSTOM SCORE ranks client-side: pull a pool wide enough to
        // re-rank (ordered by the score preset's metric, or Sharpe for a
        // hand-written formula), instead of one server-ordered page.
        const isScore = s === "score";
        const res = await fetchTradersPage({
          days: d,
          pool: WARMED_CANDIDATE_POOL,
          sort: isScore ? scorePoolSort : s,
          order: isScore ? "desc" : o,
          page: 0,
          pageSize: isScore ? SCORE_POOL : PAGE_SIZE,
          marketQuery: q || undefined,
          maxLastTradeHrs: recent ? DEFAULT_ACTIVE_HOURS : undefined,
          minHistoryDays: minHist || undefined,
        });
        if (res.cold) {
          setCold(true);
          setRows([]);
          setTotal(0);
        } else {
          setCold(false);
          setRows(res.traders);
          setTotal(res.total);
          setPoolCount(res.count);
          setSyncedAt(res.syncedAt ?? 0);
          setDropped(res.activityDropped ?? 0);
          setHistoryDropped(res.historyDropped ?? 0);
          setHistoryKnown(res.historyKnown ?? 0);
        }
        setRan({ query: q, days: d, activeOnly: recent, sort: s, order: o, minHistoryDays: minHist });
        return !res.cold;
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
        setRows([]);
        return false;
      } finally {
        setLoading(false);
      }
    },
    [query, days, sort, order, activeOnly, minHistoryDays, scorePoolSort],
  );

  useEffect(() => () => { if (warmTimer.current) clearTimeout(warmTimer.current); }, []);

  /** Ask the API to aggregate now, then keep re-asking until the window
      answers. The sync is the same hourly cycle — this just doesn't wait for
      the hour. */
  const warm = useCallback(async () => {
    setWarming(true);
    try {
      await fetch(`${API_BASE}/sync/run`, { method: "POST" });
    } catch {
      // The poll below reports the state either way.
    }
    let left = WARM_POLLS;
    const tick = async () => {
      const ok = await run();
      if (ok || --left <= 0) {
        setWarming(false);
        return;
      }
      warmTimer.current = setTimeout(() => void tick(), 10_000);
    };
    warmTimer.current = setTimeout(() => void tick(), 10_000);
  }, [run]);

  const submit = () => {
    if (isAddress) {
      if (!amountOk || busy) return;
      onAdd(typed, usd, "");
      setQuery("");
      return;
    }
    void run();
  };

  const pick = (presetQuery: string) => {
    setQuery(presetQuery);
    void run({ query: presetQuery });
  };

  const scoped = useMemo(() => statsAreScoped(rows ?? [], ran.query), [rows, ran.query]);

  // What the table shows: the server page as-is, or — on CUSTOM SCORE — the
  // pooled rows re-ranked by the formula, top of the page. Ranking is a memo,
  // so editing the formula re-orders instantly without a refetch.
  const view = useMemo(() => {
    if (!rows) return null;
    if (ran.sort !== "score") return rows;
    const dir = ran.order === "desc" ? -1 : 1;
    return [...rows].sort((a, b) => dir * (scoreFor(a) - scoreFor(b))).slice(0, PAGE_SIZE);
  }, [rows, ran.sort, ran.order, scoreFor]);

  const profileHref = useCallback(
    (address: string) => {
      const qs = new URLSearchParams();
      if (ran.query) qs.set("mq", ran.query);
      qs.set("days", String(ran.days));
      return `/traders/${address}?${qs.toString()}`;
    },
    [ran.query, ran.days],
  );

  const sortLabel =
    ran.sort === "score"
      ? `SCORE = ${formula}`
      : SORTS.find((s) => s.key === ran.sort)?.label ?? ran.sort;

  /** Check/uncheck one row. A fresh pick captures the current $ and the query
      that found the row; sizing it afterwards happens on the sidebar tray. */
  const togglePick = (address: string) =>
    storeTogglePick({ address, usd: amountOk ? usd : 100, marketQuery: ran.query });

  /** Header checkbox: all page rows picked → drop them; otherwise pick the missing. */
  const pageAddrs = useMemo(() => (view ?? []).map((t) => t.address.toLowerCase()), [view]);
  const allPagePicked = pageAddrs.length > 0 && pageAddrs.every((a) => picked.has(a));
  const togglePage = () => {
    if (allPagePicked) removePicks(pageAddrs);
    else addPicks(pageAddrs.map((a) => ({ address: a, usd: amountOk ? usd : 100, marketQuery: ran.query })));
  };

  /** Flip the rank direction — the cards re-rank on the server. */
  const flipOrder = () => {
    if (loading) return;
    const next: SortDir = order === "desc" ? "asc" : "desc";
    setOrder(next);
    void run({ order: next });
  };

  return (
    <div className="pixel-panel p-4 space-y-3">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <div className="font-mono text-[12px] tracking-[0.14em] text-pixel-gray-light">ADD A TRADER</div>
        <span className="font-mono text-[10px] text-pixel-gray">
          paste an address to copy them — or pick a market to find who does best there
        </span>
      </div>

      {/* Presets: each is a literal query, and the query is the copy gate. */}
      <div className="flex flex-wrap items-center gap-1.5">
        {MARKET_TYPES.map((m) => (
          <button
            key={m.label}
            className={`pixel-btn btn-xs ${preset?.label === m.label ? "border-pixel-green text-pixel-green" : ""}`}
            disabled={loading}
            onClick={() => pick(m.query)}
            title={`${m.hint} — matches: ${m.query}`}
          >
            {m.label}
          </button>
        ))}
        {typed !== "" && !isAddress && (
          <button className="pixel-btn btn-xs" disabled={loading} onClick={() => pick("")} title="Every market — no gate">
            ✕ ANY MARKET
          </button>
        )}
      </div>

      {/* Every control wears its label. A row of 1D/7D/BY P&L/ACTIVE 6H/$100
          with no words was the most-asked-about strip on the console. */}
      <div className="flex flex-wrap items-end gap-x-3 gap-y-2">
        <Field label={isAddress ? "TRADER" : "TRADER ADDRESS · OR A MARKET"} className="flex-1 min-w-[260px]">
        <input
          className={`pixel-input-sm w-full font-mono text-[12px] ${
            typed.startsWith("0x") && !isAddress ? "border-amber-400/60" : ""
          }`}
          placeholder="0x… address to copy, or a market — bitcoin, nba, elections"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && submit()}
          title="An address copies that trader. A topic finds traders: OR across commas, AND within a phrase."
        />
        </Field>
        {!isAddress && (
          <>
            <Field label="RANK OVER">
            <div className="flex items-center gap-1">
              {WINDOWS.map((d) => (
                <button
                  key={d}
                  className={`pixel-btn btn-xs ${d === days ? "border-pixel-green text-pixel-green" : ""}`}
                  disabled={loading}
                  onClick={() => { setDays(d); void run({ days: d }); }}
                  title={`Rank on the last ${d} day(s)`}
                >
                  {d}D
                </button>
              ))}
            </div>
            </Field>
            <Field label="RANK BY">
            <select
              value={sort}
              disabled={loading}
              onChange={(e) => { setSort(e.target.value); void run({ sort: e.target.value }); }}
              title="Rank by"
              className="bg-pixel-black/40 border border-pixel-border/60 rounded px-1.5 py-[3px] font-mono text-[10px] text-pixel-white outline-none cursor-pointer"
            >
              {SORTS.map((s) => (
                <option key={s.key} value={s.key}>{s.label}</option>
              ))}
            </select>
            </Field>
            {sort === "score" && (
              <Field label="SCORE =" className="flex-1 min-w-[200px]">
                <div className="flex items-center gap-1.5 flex-wrap">
                  <ScoreRatioChips
                    formula={formula}
                    setFormula={setFormula}
                    canSave={!compiled.error}
                    btnClass="pixel-btn btn-xs"
                  />
                  <input
                    className={`pixel-input-sm flex-1 min-w-[120px] font-mono text-[12px] ${compiled.error ? "border-red-400/70" : ""}`}
                    value={formula}
                    spellCheck={false}
                    placeholder={DEFAULT_FORMULA}
                    onChange={(e) => setFormula(e.target.value)}
                    title={`Any expression of ${FORMULA_VARS.join(", ")} (and Math) — e.g. sharpe * Math.log(1 + volume)`}
                  />
                  <button
                    className="pixel-btn btn-xs"
                    onClick={() => setFormula(DEFAULT_FORMULA)}
                    title="Back to the default — win rate"
                  >
                    RST
                  </button>
                </div>
              </Field>
            )}
            <Field label="ONLY ACTIVE">
            <button
              className={`pixel-btn btn-xs ${activeOnly ? "border-pixel-green text-pixel-green" : ""}`}
              disabled={loading}
              onClick={() => { const next = !activeOnly; setActiveOnly(next); void run({ activeOnly: next }); }}
              title={`Only traders who filled something in the last ${DEFAULT_ACTIVE_HOURS}h — a wallet that went quiet yesterday is a copy that never fills. Off = the whole board.`}
            >
              {activeOnly ? `LAST ${DEFAULT_ACTIVE_HOURS}H` : "OFF"}
            </button>
            </Field>
            {/* Track record. The sibling of ONLY ACTIVE: that one asks "are
                they trading NOW", this one asks "have they been trading LONG".
                A 30D window over a wallet that opened last week is 24 days of
                flat line and a return that rests on six.

                Deliberately not defaulted to the window — the user decides how
                much record they want behind a name, and OFF is a real answer. */}
            <Field label="HISTORY ≥">
            <div className="flex items-center gap-1">
              {HISTORY_FLOORS.map((h) => (
                <button
                  key={h}
                  className={`pixel-btn btn-xs ${h === minHistoryDays ? "border-pixel-green text-pixel-green" : ""}`}
                  disabled={loading}
                  onClick={() => { setMinHistoryDays(h); void run({ minHistoryDays: h }); }}
                  title={
                    h === 0
                      ? "No track-record floor — brand-new wallets included"
                      : `Only traders whose first-ever trade was at least ${h} days ago${
                          h < days ? "" : ` — enough record to make the ${days}D window mean something`
                        }`
                  }
                >
                  {h === 0 ? "OFF" : `${h}D`}
                </button>
              ))}
              <input
                className="pixel-input-sm w-14 font-mono text-[11px]"
                value={HISTORY_FLOORS.includes(minHistoryDays as (typeof HISTORY_FLOORS)[number]) ? "" : String(minHistoryDays)}
                inputMode="numeric"
                placeholder="days"
                disabled={loading}
                onChange={(e) => {
                  const n = Number(e.target.value);
                  setMinHistoryDays(Number.isFinite(n) && n > 0 ? n : 0);
                }}
                onKeyDown={(e) => e.key === "Enter" && void run()}
                title="Any number of days — press Enter to apply"
              />
            </div>
            </Field>
          </>
        )}
        <Field label="$ PER TRADER">
        <input
          className="pixel-input-sm w-24 font-mono text-[12px]"
          value={amount}
          inputMode="decimal"
          onChange={(e) => setAmount(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && submit()}
          title="Dollars behind each trader you add. Editable per row afterwards."
        />
        </Field>
        <button
          className={`pixel-btn btn-sm ${isAddress ? "border-pixel-green text-pixel-green" : ""}`}
          disabled={loading || (isAddress && (!amountOk || busy))}
          onClick={submit}
          title={
            isAddress
              ? already
                ? "Already in the book — this updates their allocation"
                : `Copy ${shortAddress(typed)} with $${amountOk ? usd : "…"} across every market they trade`
              : "Rank the leaderboard on this market"
          }
        >
          {loading ? "…" : isAddress ? (already ? "UPDATE $" : `COPY WITH $${amountOk ? usd : "…"}`) : "FIND TRADERS"}
        </button>
      </div>

      {typed.startsWith("0x") && !isAddress && (
        <div className="font-mono text-[10px] text-amber-400">
          that isn&apos;t a full address yet — 42 hex characters
        </div>
      )}
      {sort === "score" && (
        compiled.error ? (
          <div className="font-mono text-[10px] text-red-400">formula: {compiled.error}</div>
        ) : (
          <div className="font-mono text-[9px] text-pixel-gray">
            your formula ranks the top {SCORE_POOL} by {matchScorePreset(formula)?.label ?? "Sharpe"} — variables: {FORMULA_VARS.join(" · ")}, plus Math.
            Write your own ratio (pnl / volume) and + SAVE keeps it as a chip. Shared with the /traders board.
          </div>
        )
      )}
      {error && <div className="font-mono text-[10px] text-red-400">{error}</div>}

      {/* The selection itself lives in the SIDE PANEL (SelectionTray) — every
          checked row replayed there as it's checked, sized per name, committed
          with one COPY ALL. Here, just the count: the desk stays a finder. */}
      {picks.length > 0 && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-[10px]">
          <span className="text-[11px] tracking-[0.14em] text-pixel-green">
            {picks.length} SELECTED · ${picks.reduce((s, p) => s + p.usd, 0).toLocaleString()}
          </span>
          <span className="text-pixel-gray">
            replaying in the side panel — size and commit them there
          </span>
          <button
            className="pixel-btn btn-xs"
            onClick={() => window.dispatchEvent(new Event(OPEN_SIDEBAR_EVENT))}
            title="Open the side panel — the selection tray is its top block"
          >
            SHOW PANEL →
          </button>
          <button className="pixel-btn btn-xs" onClick={clearPicks} title="Uncheck everything">
            CLEAR
          </button>
        </div>
      )}

      {cold && (
        <div className="flex flex-wrap items-center gap-2 font-mono text-[10px] text-amber-400">
          <span>the {ran.days}D leaderboard hasn&apos;t been aggregated yet</span>
          <button className="pixel-btn btn-xs" disabled={warming} onClick={() => void warm()}>
            {warming ? "WARMING…" : "WARM IT NOW"}
          </button>
          <span className="text-pixel-gray">
            {warming ? "re-checking every 10s — a full sweep takes a few minutes" : "or use 1D, which is always ready"}
          </span>
        </div>
      )}

      {view !== null && !cold && (
        <>
          <div className="font-mono text-[10px] text-pixel-gray">
            {ran.query ? (
              <>
                <span className="text-pixel-gray-light">{total.toLocaleString()}</span> of{" "}
                {poolCount.toLocaleString()} traders on the {ran.days}D board trade{" "}
                <span className="text-pixel-gray-light">“{ran.query}”</span> — top{" "}
                {Math.min(PAGE_SIZE, view.length)} by {sortLabel}{ran.order === "asc" ? " (lowest first)" : ""}
              </>
            ) : (
              <>
                {total.toLocaleString()} traders on the {ran.days}D board, every market — top{" "}
                {Math.min(PAGE_SIZE, view.length)} by {sortLabel}{ran.order === "asc" ? " (lowest first)" : ""}
              </>
            )}
            {ran.activeOnly && (
              <>
                {" "}· <span className="text-pixel-green">active {DEFAULT_ACTIVE_HOURS}h</span>
                {dropped > 0 && <> ({dropped.toLocaleString()} dormant hidden)</>}
              </>
            )}
            {ran.minHistoryDays > 0 && (
              <>
                {" "}· <span className="text-pixel-green">{ran.minHistoryDays}d+ record</span>
                {historyDropped > 0 && <> ({historyDropped.toLocaleString()} too new hidden)</>}
              </>
            )}
            {syncedAt > 0 && <> · data {timeAgo(syncedAt * 1000)}</>}
          </div>

          {/* The floor keeps traders whose age hasn't been resolved yet — so
              say so rather than let a half-applied filter read as a clean cut.
              Ages fill in per wallet as the warmup sweeps the board. */}
          {ran.minHistoryDays > 0 && view.length > 0 && historyKnown < view.length && (
            <div className="font-mono text-[10px] text-amber-400">
              {(view.length - historyKnown).toLocaleString()} of {view.length} shown have no
              known start date yet — the {ran.minHistoryDays}d floor let them through rather
              than cut them on missing data. They fill in as the board re-syncs.
            </div>
          )}

          {ran.query && !scoped && (
            <div className="font-mono text-[10px] text-amber-400">
              these numbers are LIFETIME, not “{ran.query}”-only — answered from the disk cache.{" "}
              <button className="pixel-btn btn-xs" disabled={warming} onClick={() => void warm()}>
                {warming ? "WARMING…" : "RE-AGGREGATE"}
              </button>
            </div>
          )}

          {view.length === 0 ? (
            <div className="font-mono text-[10px] text-pixel-gray">
              nobody on the {ran.days}D board traded {ran.query ? `“${ran.query}”` : "anything"}
              {ran.minHistoryDays > 0 && historyDropped > 0
                ? ` — ${historyDropped.toLocaleString()} matched but have less than ${ran.minHistoryDays} days of history; lower HISTORY ≥ to see them`
                : ran.activeOnly && dropped > 0
                ? ` in the last ${DEFAULT_ACTIVE_HOURS}h — ${dropped.toLocaleString()} did earlier; set ONLY ACTIVE to OFF to see them`
                : ". Try a wider market, or a longer window."}
            </div>
          ) : (
            <>
              {/* One row of page-wide controls where the table header used to be. */}
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                <label
                  className="flex items-center gap-1.5 font-mono text-[9px] tracking-[0.1em] text-pixel-gray cursor-pointer"
                  title="Select everyone on this page — each gets replayed with your $ in the side panel, automatically"
                >
                  <input
                    type="checkbox"
                    className="cursor-pointer accent-emerald-400 w-3 h-3"
                    checked={allPagePicked}
                    onChange={togglePage}
                  />
                  SELECT ALL {view.length}
                </label>
                <button
                  className="pixel-btn btn-xs"
                  disabled={loading}
                  onClick={flipOrder}
                  title="Flip the ranking direction"
                >
                  {ran.order === "desc" ? "▼ BEST FIRST" : "▲ WORST FIRST"}
                </button>
              </div>

              {/* One card per trader: who they are, the shape of their P&L
                  over the window, the numbers behind it, and the two actions.
                  The curve is the same slice the stats are — filtered board,
                  filtered curve. */}
              <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(250px, 1fr))" }}>
                {view.map((t, i) => {
                  const addr = t.address.toLowerCase();
                  const inBook = existing.has(addr);
                  const basketed = inBasket?.has(addr) ?? false;
                  const curve = t.pnlCurve;
                  const score = ran.sort === "score" ? scoreFor(t) : null;
                  return (
                    <div
                      key={t.address}
                      className={`rounded border p-2.5 flex flex-col gap-2 ${
                        picked.has(addr)
                          ? "border-pixel-green/60 bg-pixel-green/5"
                          : "border-pixel-border/60 bg-pixel-black/20"
                      }`}
                    >
                      <div className="flex items-center gap-1.5 min-w-0">
                        <input
                          type="checkbox"
                          className="cursor-pointer accent-emerald-400 w-3 h-3 shrink-0"
                          checked={picked.has(addr)}
                          onChange={() => togglePick(t.address)}
                          title={
                            picked.has(addr)
                              ? "Unselect — drops them from the side panel"
                              : `Select — backtests $${amountOk ? usd : 100} on them automatically, in the side panel`
                          }
                        />
                        <span className="font-mono text-[9px] text-pixel-gray shrink-0">#{i + 1}</span>
                        <Link
                          href={profileHref(t.address)}
                          className="font-mono text-[11px] text-pixel-gray-light hover:text-pixel-green normal-case truncate"
                          title={`${t.address}\n${t.marketTitles.slice(0, 6).join("\n")}`}
                        >
                          {shortAddress(t.address)} ↗
                        </Link>
                        {inBook && (
                          <span className="font-mono text-[8px] tracking-[0.12em] border border-pixel-green/50 text-pixel-green rounded-[3px] px-1 py-[1px] shrink-0">
                            IN BOOK
                          </span>
                        )}
                        {/* Track record beside recency: how long they have
                            been doing this, next to when they last did it.
                            Amber when the record is shorter than the window
                            being ranked — that is exactly the case where the
                            headline number covers days the trader didn't
                            exist for. */}
                        <span
                          className={`ml-auto font-mono text-[9px] whitespace-nowrap shrink-0 ${
                            tooNewForWindow(t, ran.days) ? "text-amber-400" : "text-pixel-gray"
                          }`}
                          title={
                            t.firstTradeTs
                              ? `First trade ${timeAgo(t.firstTradeTs * 1000)}${
                                  tooNewForWindow(t, ran.days)
                                    ? ` — SHORTER than the ${ran.days}D window, so part of that window predates them`
                                    : ""
                                }`
                              : "Track record not resolved yet — fills in on the next board sync"
                          }
                        >
                          {formatHistory(t)}
                        </span>
                        <span
                          className="font-mono text-[9px] text-pixel-gray whitespace-nowrap shrink-0"
                          title="Time since their most recent trade"
                        >
                          {t.lastTradeTs ? timeAgo(t.lastTradeTs * 1000) : "—"}
                        </span>
                      </div>

                      <div className="flex items-baseline justify-between gap-2">
                        <span
                          className={`font-mono text-[16px] leading-none ${
                            t.pnl > 0 ? "text-pixel-green" : t.pnl < 0 ? "text-red-400" : "text-pixel-gray"
                          }`}
                          title="Realized + marked P&L over the window"
                        >
                          {formatPnl(t.pnl)}
                        </span>
                        <span
                          className={`font-mono text-[8.5px] tracking-[0.1em] ${
                            ran.sort === "pnl" ? "text-pixel-green" : "text-pixel-gray"
                          }`}
                        >
                          {ran.days}D P&L{ran.query ? " · THIS MARKET" : ""}
                        </span>
                      </div>

                      {curve && curve.length > 1 ? (
                        <div
                          className="w-full"
                          title="Cumulative realized P&L across the window — open positions' marks land in the total above, not this line"
                        >
                          <Sparkline
                            data={curve}
                            width={250}
                            height={48}
                            stretch
                            hoverLabel={(idx) => `bucket ${idx + 1}/${curve.length} of the ${ran.days}D window`}
                          />
                        </div>
                      ) : (
                        <div className="h-[48px] flex items-center justify-center font-mono text-[8.5px] text-pixel-gray border border-dashed border-pixel-border/40 rounded">
                          no P&L curve for this slice yet — the next sync draws it
                        </div>
                      )}

                      <div className={`grid ${score !== null ? "grid-cols-5" : "grid-cols-4"} gap-1`}>
                        <Stat
                          label="WIN"
                          active={ran.sort === "winRate"}
                          value={t.winRate < 0 ? "—" : `${Math.round(t.winRate)}%`}
                          // A rate is only as good as what it divides. Under
                          // ~10 settled positions the number is noise, and it
                          // used to render identically to one off hundreds —
                          // which is how "100%" ended up on screen.
                          sub={
                            t.winRate < 0
                              ? "not settled yet"
                              : `of ${t.decidedPositions}`
                          }
                          dim={t.winRate >= 0 && t.decidedPositions < THIN_SAMPLE}
                          title={
                            t.winRate < 0
                              ? "No positions settled in this window yet — unknown, not zero."
                              : `Share of the ${t.decidedPositions} position(s) that SETTLED in this window and returned more than they cost. Counts positions that expired worthless, which leave no sell and no redeem.${
                                  t.decidedPositions < THIN_SAMPLE
                                    ? " Thin sample — treat as noise."
                                    : ""
                                }`
                          }
                        />
                        <Stat
                          label="TRADES"
                          active={ran.sort === "trades"}
                          value={t.recentTrades.toLocaleString()}
                          title="Trades in the window"
                        />
                        <Stat
                          label="SHARPE"
                          active={ran.sort === "sharpe"}
                          value={t.sharpe ? t.sharpe.toFixed(2) : "—"}
                          title="Mean / stdev of per-trade returns"
                        />
                        <Stat
                          label="VOLUME"
                          active={ran.sort === "volume"}
                          value={formatVolume(t.volume)}
                          title="Dollars traded over the window"
                        />
                        {score !== null && (
                          <Stat
                            label="SCORE"
                            active
                            value={formatScore(score)}
                            valueClass={score > 0 ? "text-pixel-green" : score < 0 ? "text-red-400" : "text-pixel-gray"}
                            title={`score = ${formula}`}
                          />
                        )}
                      </div>

                      <div className="flex gap-1 mt-auto">
                        <button
                          className="pixel-btn btn-xs flex-1"
                          disabled={!amountOk || busy}
                          onClick={() => onAdd(t.address, usd, ran.query)}
                          title={
                            inBook
                              ? `Already in the book — sets $${usd} and gates them to ${ran.query ? `“${ran.query}”` : "all markets"}`
                              : ran.query
                                ? `Copy with $${usd}, only where they trade “${ran.query}”`
                                : `Copy with $${usd}, across every market they trade`
                          }
                        >
                          {inBook ? "UPDATE" : `COPY $${amountOk ? usd : "…"}`}
                        </button>
                        {onBasket && (
                          <button
                            className={`pixel-btn btn-xs flex-1 ${basketed ? "border-pixel-green text-pixel-green" : ""}`}
                            disabled={!amountOk || busy}
                            onClick={() => onBasket(t.address, usd, ran.query)}
                            title={
                              basketed
                                ? "In the basket — this re-sizes them there"
                                : "Shortlist: size several traders against each other on /copy/basket before committing"
                            }
                          >
                            {basketed ? "IN BASKET" : "BASKET"}
                          </button>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            </>
          )}

          {view.length > 0 && (
            <div className="font-mono text-[9px] text-pixel-gray">
              {ran.query
                ? `COPY follows them only in markets matching “${ran.query}” — the backtest and the live engine both use that filter, and each card's curve is that slice alone.`
                : "no market filter — COPY follows everything they trade."}
              {" "}Or tick several cards — each gets backtested automatically, in the side panel.
            </div>
          )}
        </>
      )}
    </div>
  );
}

/** A control with its name over it. */
function Field({ label, className = "", children }: { label: string; className?: string; children: React.ReactNode }) {
  return (
    <div className={`flex flex-col gap-1 ${className}`}>
      <span className="font-mono text-[8.5px] tracking-[0.14em] text-pixel-gray whitespace-nowrap">{label}</span>
      {children}
    </div>
  );
}

/** One number on a trader card — label over value, label lit when it's the
    metric the board is ranked by. */
/** Settled positions below which a win rate is noise rather than a record. */
const THIN_SAMPLE = 10;

function Stat({
  label,
  value,
  active = false,
  title,
  valueClass = "text-pixel-gray-light",
  sub,
  dim = false,
}: {
  label: string;
  value: string;
  active?: boolean;
  title?: string;
  valueClass?: string;
  /** Denominator or unit line under the value — what the number is OF. */
  sub?: string;
  /** Greys the value out: shown, but not to be trusted at face value. */
  dim?: boolean;
}) {
  return (
    <div className="flex flex-col gap-0.5 min-w-0" title={title}>
      <span className={`font-mono text-[8px] tracking-[0.12em] ${active ? "text-pixel-green" : "text-pixel-gray"}`}>
        {label}
      </span>
      <span className={`font-mono text-[11px] truncate ${dim ? "text-pixel-gray" : valueClass}`}>{value}</span>
      {sub ? (
        <span className="font-mono text-[7.5px] tracking-[0.08em] text-pixel-gray truncate">{sub}</span>
      ) : null}
    </div>
  );
}
