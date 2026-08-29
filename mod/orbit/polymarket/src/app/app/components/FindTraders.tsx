"use client";

// FIND TRADERS BY MARKET — the desk's front door to the leaderboard.
//
// The question this answers is "who is good at BITCOIN", not "who is good".
// Those are different lists, and the difference is the whole point: a trader
// with a $400k lifetime P&L made none of it in the market you want to copy
// them in, and copying them would have you following their election book.
//
// So the chain runs one way, all the way down:
//
//   market type  →  their trades in it  →  their numbers from those trades
//                →  the traders that survive  →  the gate the copy runs under
//
// Every number in this table is recomputed server-side from ONLY the matching
// markets (`apply_pagination` in api/src/routes.rs) — this is not a lifetime
// P&L next to a market filter. And the query that produced the list is stored
// on the allocation as `params.marketQuery`, so the live engine and the
// backtest both refuse the leader's trades outside it. Find on bitcoin, copy
// on bitcoin.
//
// The one thing that can quietly break that promise is the server answering a
// filtered request from its DISK cache, which drops the per-market breakdown
// and leaves the stats lifetime. `statsAreScoped` below detects it from the
// payload itself and the panel says so rather than showing numbers that mean
// something else.

import { useCallback, useMemo, useState } from "react";
import Link from "next/link";

import {
  fetchTradersPage, formatPnl, formatVolume, timeAgo,
  WARMED_CANDIDATE_POOL, DEFAULT_ACTIVE_HOURS, type TopTrader,
} from "../lib/polymarket";
import { marketMatchesQuery } from "../lib/marketQuery";
import { MARKET_TYPES, matchPreset } from "../lib/marketTypes";
import { shortAddress } from "../lib/identityStrat";

/** Windows the server's hourly warmup actually aggregates (`warmup_cycle` in
    pipeline.rs). Asking for any other window is a different cache key, and a
    cold key answers `{cold:true}` instead of computing — so these four are the
    only ones that come back instantly. */
const WINDOWS = [1, 7, 14, 30] as const;

const SORTS = [
  { key: "pnl", label: "P&L" },
  { key: "sharpe", label: "SHARPE" },
  { key: "winRate", label: "WIN %" },
  { key: "volume", label: "VOLUME" },
  { key: "last", label: "RECENT" },
] as const;

const PAGE_SIZE = 12;

/** True when the server recomputed each row's stats from the matching markets
    only. Derived, not reported: the scoped path REPLACES `marketTitles` with
    the matching titles, so a row carrying a title that doesn't match the query
    is a row whose numbers are still lifetime — the disk-cache fallback. */
function statsAreScoped(traders: TopTrader[], query: string): boolean {
  if (!query.trim() || traders.length === 0) return true;
  return traders.every((t) => t.marketTitles.every((title) => marketMatchesQuery(title, query)));
}

interface Props {
  /** Adds the trader to the copy book with `marketQuery` as their gate. */
  onAdd: (address: string, allocationUsd: number, marketQuery: string) => void;
  /** Drops the trader into the BASKET draft instead — a shortlist you size and
      replay as a set before any of it is committed (/copy/basket). Same gate
      rides along, so a name found on bitcoin is basketed on bitcoin. */
  onBasket?: (address: string, allocationUsd: number, marketQuery: string) => void;
  /** Addresses already in the basket draft — shown as IN BASKET. */
  inBasket?: Set<string>;
  busy: boolean;
  /** Addresses already in the book — shown as ADDED, and adding again just
      re-points their gate at the current query. */
  existing: Set<string>;
}

export default function FindTraders({ onAdd, onBasket, inBasket, busy, existing }: Props) {
  const [query, setQuery] = useState("");
  const [days, setDays] = useState<number>(7);
  const [sort, setSort] = useState<string>("pnl");
  // The recency lens, on by default. This panel's output goes straight into
  // the copy book, and a wallet that stopped trading two days ago is an
  // allocation that fills nothing — it just looks good on a 7D board. Off
  // shows the whole board, dormants included.
  const [activeOnly, setActiveOnly] = useState(true);
  const [amount, setAmount] = useState("100");
  const [rows, setRows] = useState<TopTrader[] | null>(null);
  const [total, setTotal] = useState(0);
  const [poolCount, setPoolCount] = useState(0);
  const [ranQuery, setRanQuery] = useState("");
  // The window the shown rows were actually scored over — `days` is the
  // selector and can be moved without re-running, so the profile links use
  // this instead. Same reason `ranQuery` exists.
  const [ranDays, setRanDays] = useState(7);
  // The lens the CURRENT rows were fetched under, and how many it removed —
  // the toggle can be flipped without re-running, so the copy below has to
  // describe the result, not the control.
  const [ranActiveOnly, setRanActiveOnly] = useState(true);
  const [dropped, setDropped] = useState(0);
  const [loading, setLoading] = useState(false);
  const [cold, setCold] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const usd = Number(amount);
  const amountOk = Number.isFinite(usd) && usd > 0;
  const active = matchPreset(query);

  const run = useCallback(
    async (opts: { query?: string; days?: number; sort?: string; activeOnly?: boolean } = {}) => {
      const q = (opts.query ?? query).trim();
      const d = opts.days ?? days;
      const s = opts.sort ?? sort;
      const recent = opts.activeOnly ?? activeOnly;
      setLoading(true);
      setError(null);
      setCold(false);
      try {
        const res = await fetchTradersPage({
          days: d,
          // The warmed pool, always — see WARMED_CANDIDATE_POOL. A different
          // pool is a different cache key and answers cold.
          pool: WARMED_CANDIDATE_POOL,
          sort: s,
          order: "desc",
          page: 0,
          pageSize: PAGE_SIZE,
          marketQuery: q || undefined,
          // Server-side, against the same cached aggregate — so `total` is the
          // count of traders you could actually copy right now.
          maxLastTradeHrs: recent ? DEFAULT_ACTIVE_HOURS : undefined,
        });
        if (res.cold) {
          setCold(true);
          setRows([]);
          setTotal(0);
        } else {
          setRows(res.traders);
          setTotal(res.total);
          setPoolCount(res.count);
          setDropped(res.activityDropped ?? 0);
        }
        setRanQuery(q);
        setRanDays(d);
        setRanActiveOnly(recent);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
        setRows([]);
      } finally {
        setLoading(false);
      }
    },
    [query, days, sort, activeOnly],
  );

  const pick = (presetQuery: string) => {
    setQuery(presetQuery);
    void run({ query: presetQuery });
  };

  const scoped = useMemo(() => statsAreScoped(rows ?? [], ranQuery), [rows, ranQuery]);

  /** The profile link for a row, carrying the search that produced the row.
   *
   *  This panel's whole claim is that a row is a trader's record IN THIS
   *  MARKET TYPE — so the screen the row opens has to be that record too. The
   *  link used to be a bare `/traders/<addr>`, which handed the profile no
   *  filter at all: you searched BITCOIN, clicked the winner, and read a tape
   *  of tennis. `?mq=` is the profile's topic filter (FiltersContext, seeded
   *  from the URL by `useUrlSync`) and `?days=` matches the window the row
   *  was scored over, so the two screens describe the same slice of flow.
   *  The profile shows it as a clearable chip — one ✕ for the whole record. */
  const profileHref = useCallback(
    (address: string) => {
      const qs = new URLSearchParams();
      if (ranQuery) qs.set("mq", ranQuery);
      qs.set("days", String(ranDays));
      return `/traders/${address}?${qs.toString()}`;
    },
    [ranQuery, ranDays],
  );

  return (
    <div className="pixel-panel p-4 space-y-3">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <div className="font-mono text-[12px] tracking-[0.14em] text-pixel-gray-light">
          FIND TRADERS BY MARKET
        </div>
        <span className="font-mono text-[10px] text-pixel-gray">
          their numbers in this market only — and the gate the copy runs under
        </span>
      </div>

      {/* The market type. One string: the search, and the copy gate. */}
      <div className="flex flex-wrap items-center gap-1.5">
        {MARKET_TYPES.map((m) => (
          <button
            key={m.label}
            className={`pixel-btn text-[10px] px-2 ${
              active?.label === m.label ? "border-pixel-green text-pixel-green" : ""
            }`}
            disabled={loading}
            onClick={() => pick(m.query)}
            title={`${m.hint} — matches: ${m.query}`}
          >
            {m.label}
          </button>
        ))}
        {query.trim() !== "" && (
          <button
            className="pixel-btn text-[10px] px-2"
            disabled={loading}
            onClick={() => pick("")}
            title="Every market — no gate, the leaderboard whole"
          >
            ✕ ANY MARKET
          </button>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <input
          className="pixel-input-sm flex-1 min-w-[220px] font-mono text-[12px]"
          placeholder="market topic — bitcoin, btc"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && void run()}
          title={
            "OR across commas, AND within a phrase. \"price of bitcoin\" is one " +
            "phrase; \"bitcoin, btc\" is either spelling."
          }
        />
        <div className="flex items-center gap-1">
          {WINDOWS.map((d) => (
            <button
              key={d}
              className={`pixel-btn text-[10px] px-2 ${d === days ? "border-pixel-green text-pixel-green" : ""}`}
              disabled={loading}
              onClick={() => { setDays(d); void run({ days: d }); }}
              title={`Rank on the last ${d} day(s) of their flow`}
            >
              {d}D
            </button>
          ))}
        </div>
        <div className="flex items-center gap-1">
          {SORTS.map((s) => (
            <button
              key={s.key}
              className={`pixel-btn text-[10px] px-2 ${s.key === sort ? "border-pixel-green text-pixel-green" : ""}`}
              disabled={loading}
              onClick={() => { setSort(s.key); void run({ sort: s.key }); }}
              title={`Best ${s.label} in these markets first`}
            >
              {s.label}
            </button>
          ))}
        </div>
        <button
          className={`pixel-btn text-[10px] px-2 ${activeOnly ? "border-pixel-green text-pixel-green" : ""}`}
          disabled={loading}
          onClick={() => { const next = !activeOnly; setActiveOnly(next); void run({ activeOnly: next }); }}
          title={`Only traders who filled something in the last ${DEFAULT_ACTIVE_HOURS}h. A great ${days}D record on a wallet that went quiet yesterday is a copy that never fills. Off = the whole board.`}
        >
          ACTIVE {DEFAULT_ACTIVE_HOURS}H
        </button>
        <button className="pixel-btn text-[11px]" disabled={loading} onClick={() => void run()}>
          {loading ? "…" : "FIND"}
        </button>

        <span className="flex-1" />

        <span className="font-mono text-[9px] tracking-[0.14em] text-pixel-gray">COPY WITH $</span>
        <input
          className="pixel-input-sm w-20 font-mono text-[12px]"
          value={amount}
          inputMode="decimal"
          onChange={(e) => setAmount(e.target.value)}
          title="What each ADD puts behind the trader. Change it per row afterwards."
        />
      </div>

      {error && <div className="font-mono text-[10px] text-red-400">{error}</div>}

      {cold && (
        <div className="font-mono text-[10px] text-amber-400">
          the leaderboard cache is cold for {days}D — open{" "}
          <Link href="/traders" className="text-pixel-green underline">TRADERS</Link>{" "}
          and press SYNC to aggregate it, then come back
        </div>
      )}

      {rows !== null && !cold && (
        <>
          <div className="font-mono text-[10px] text-pixel-gray">
            {ranQuery ? (
              <>
                <span className="text-pixel-gray-light">{total.toLocaleString()}</span> of{" "}
                {poolCount.toLocaleString()} traders in the {days}D leaderboard trade{" "}
                <span className="text-pixel-gray-light">“{ranQuery}”</span> — showing the top{" "}
                {Math.min(PAGE_SIZE, rows.length)} by {SORTS.find((s) => s.key === sort)?.label}
              </>
            ) : (
              <>
                every market, {total.toLocaleString()} traders — pick a market type above to rank
                traders on that flow alone
              </>
            )}
            {ranActiveOnly && (
              <>
                {" "}·{" "}
                <span className="text-pixel-green">active {DEFAULT_ACTIVE_HOURS}h</span>
                {dropped > 0 && <> ({dropped.toLocaleString()} dormant hidden)</>}
              </>
            )}
          </div>

          {ranQuery && !scoped && (
            <div className="font-mono text-[10px] text-amber-400">
              these numbers are LIFETIME, not “{ranQuery}”-only — the server answered from its disk
              cache, which has no per-market breakdown. Press SYNC on{" "}
              <Link href="/traders" className="text-pixel-green underline">TRADERS</Link> to get
              market-scoped stats.
            </div>
          )}

          {rows.length === 0 ? (
            <div className="font-mono text-[10px] text-pixel-gray">
              no trader in the {days}D leaderboard traded {ranQuery ? `“${ranQuery}”` : "anything"}
              {ranActiveOnly && dropped > 0 ? (
                <> in the last {DEFAULT_ACTIVE_HOURS}h — {dropped.toLocaleString()} traded it earlier,
                  so turn ACTIVE {DEFAULT_ACTIVE_HOURS}H off to see them</>
              ) : (
                <>. Try a wider market type, or a longer window.</>
              )}
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="pixel-table w-full" style={{ minWidth: "760px" }}>
                <thead>
                  <tr>
                    <th>TRADER</th>
                    <th className="text-right">P&amp;L</th>
                    <th className="text-right">TRADES</th>
                    <th className="text-right">WIN</th>
                    <th className="text-right">SHARPE</th>
                    <th className="text-right">VOLUME</th>
                    <th className="text-right">MARKETS</th>
                    <th className="text-right">LAST</th>
                    {/* Two actions live here (copy now, or shortlist into the
                        basket). `table-layout: fixed` + `overflow: hidden` on
                        a cell means an unsized action column clips the second
                        button — and a clipped button is not just ugly, it
                        can't be clicked. */}
                    <th className={onBasket ? "w-[232px]" : "w-[110px]"} />
                  </tr>
                </thead>
                <tbody>
                  {rows.map((t) => {
                    const already = existing.has(t.address.toLowerCase());
                    return (
                      <tr key={t.address}>
                        <td className="font-mono text-[11px]">
                          <Link
                            href={profileHref(t.address)}
                            className="text-pixel-gray-light hover:text-pixel-green normal-case"
                            title={
                              ranQuery
                                ? `${t.address} — their record in “${ranQuery}” over ${ranDays}D`
                                : t.address
                            }
                          >
                            {shortAddress(t.address)} ↗
                          </Link>
                        </td>
                        <td
                          className={`num text-right font-mono text-[11px] ${
                            t.pnl > 0 ? "text-pixel-green" : t.pnl < 0 ? "text-red-400" : "text-pixel-gray"
                          }`}
                        >
                          {formatPnl(t.pnl)}
                        </td>
                        <td className="num text-right font-mono text-[11px] text-pixel-gray-light">
                          {t.recentTrades.toLocaleString()}
                        </td>
                        <td className="num text-right font-mono text-[11px] text-pixel-gray-light">
                          {t.winRate < 0 ? "—" : `${Math.round(t.winRate)}%`}
                        </td>
                        <td className="num text-right font-mono text-[11px] text-pixel-gray-light">
                          {t.sharpe ? t.sharpe.toFixed(2) : "—"}
                        </td>
                        <td className="num text-right font-mono text-[11px] text-pixel-gray-light">
                          {formatVolume(t.volume)}
                        </td>
                        <td
                          className="num text-right font-mono text-[11px] text-pixel-gray"
                          title={t.marketTitles.slice(0, 6).join("\n")}
                        >
                          {t.marketTitles.length}
                        </td>
                        <td className="num text-right font-mono text-[10px] text-pixel-gray">
                          {t.lastTradeTs ? timeAgo(t.lastTradeTs * 1000) : "—"}
                        </td>
                        <td className="text-right" style={{ overflow: "visible" }}>
                          <button
                            className="pixel-btn text-[10px] px-2"
                            disabled={!amountOk || busy}
                            onClick={() => onAdd(t.address, usd, ranQuery)}
                            title={
                              already
                                ? `Already in the book — this re-points their gate at ${
                                    ranQuery ? `“${ranQuery}”` : "all markets"
                                  } and sets $${usd}`
                                : ranQuery
                                  ? `Copy with $${usd}, only where they trade “${ranQuery}”`
                                  : `Copy with $${usd}, across every market they trade`
                            }
                          >
                            {already ? "RE-GATE" : `ADD $${amountOk ? usd : "…"}`}
                          </button>
                          {onBasket && (
                            <button
                              className={`pixel-btn text-[10px] px-2 ml-1 ${
                                inBasket?.has(t.address.toLowerCase()) ? "border-pixel-green text-pixel-green" : ""
                              }`}
                              disabled={!amountOk || busy}
                              onClick={() => onBasket(t.address, usd, ranQuery)}
                              title={
                                inBasket?.has(t.address.toLowerCase())
                                  ? "Already in the basket — this re-sizes them there"
                                  : "Shortlist them: the basket sizes several traders at once and replays the whole split before anything is committed"
                              }
                            >
                              {inBasket?.has(t.address.toLowerCase()) ? "IN BASKET" : "+ BASKET"}
                            </button>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}

          {rows.length > 0 && (
            <div className="font-mono text-[9px] text-pixel-gray">
              {ranQuery
                ? `ADD copies them only in markets matching “${ranQuery}” — the same gate the backtest replays under. Change or drop it on the row below.`
                : "no market gate — ADD copies everything they trade."}
            </div>
          )}
        </>
      )}
    </div>
  );
}
