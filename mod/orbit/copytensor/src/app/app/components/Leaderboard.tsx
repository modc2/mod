"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import type { LeaderboardEntry, Universe } from "../lib/types";
import { fetchLeaderboard, fetchUniverse, fmtCompact,
         setPool, shortSs58, windowLabel, windowPhrase } from "../lib/api";
import PnlBadge from "./PnlBadge";
import Identicon from "./Identicon";
import StatTile from "./StatTile";
import { useFilters, type SortKey } from "../context/FiltersContext";
import { useCurrency, fmtValue } from "../context/CurrencyContext";
import { useSidebar } from "../context/SidebarContext";
import { useCoverage } from "../lib/useCoverage";
import WindowRail from "./WindowRail";
import { RankBy, StakeFloor } from "./BoardFilters";

// `days = 0` means "every day the index holds"; the server prices that as
// its ALL_DAYS horizon, so that is the number /universe reports it under.
const ALL_HORIZON = 365;
// Trader-pool sizes. Every step is real coldkeys ranked by on-chain stake;
// bigger pools take longer to price (one historical read per trader).
const POOL_SIZES = [100, 250, 500, 1000];

export default function Leaderboard() {
  const { days, search, sortKey, sortDir, toggleSort, minSubnets,
          setMinSubnets, minStake, reloadKey, reload } = useFilters();
  const { currency, usdPerTao } = useCurrency();
  const { openStrat } = useSidebar();
  const cov = useCoverage();
  const router = useRouter();
  // What /universe calls this horizon.
  const horizon = days === 0 ? ALL_HORIZON : days;
  const win = windowLabel(days);
  const [entries, setEntries] = useState<LeaderboardEntry[]>([]);
  // Ticked rows. A basket is the point of the board — "copy these twelve"
  // used to mean twelve trips through the drawer, one COPY click each.
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [universe, setUniverse] = useState<Universe | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  // Pool size + subnet floor, folded away on a handheld (always open on lg).
  const [knobs, setKnobs] = useState(false);

  useEffect(() => {
    setLoading(true);
    setError("");
    // Ask for the whole pool, not a slice of it — the board is the ranking
    // of every trader we watch, and the pool is user-resizable.
    fetchLeaderboard(days, 2000)
      .then(setEntries)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [days, reloadKey]);

  // Pool + board status. Discovery and the first pass over a horizon both
  // run server-side in the background, so poll while either is in flight
  // and pull the rows in the moment this horizon finishes pricing.
  useEffect(() => {
    let stop = false;
    let wasBuilding = false;
    const tick = () =>
      fetchUniverse()
        .then((u) => {
          if (stop) return;
          setUniverse(u);
          const building =
            u.status === "discovering" || (u.board?.building || []).includes(horizon);
          if (wasBuilding && !building) {
            setBusy(false);
            reload();               // rows are ready — refetch the board
            return;
          }
          wasBuilding = building;
          if (building) setTimeout(tick, 4000);
          else if (busy) { setBusy(false); reload(); }
        })
        .catch(() => {});
    tick();
    return () => { stop = true; };
  }, [reloadKey, busy, horizon, reload]);

  const grow = (size: number) => {
    setBusy(true);
    setError("");
    setPool(size)
      .then(setUniverse)
      .catch((e) => { setBusy(false); setError(e.message); });
  };

  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase();
    // A wallet that emptied itself over the window reads as −100% total on
    // +150% market and used to top the board. Dust books are hidden unless
    // you're searching for one by name.
    // Dust books top a percentage board and can't be copied at size. The
    // floor is a control now (`book` chips); searching by name always wins,
    // otherwise you can't find a wallet you already know about.
    const floor = Math.max(minStake, 1);
    let r = entries.filter(
      (e) => e.num_subnets >= minSubnets && (needle ? true : e.total_stake_tao >= floor));
    if (needle) {
      r = r.filter((e) =>
        (e.label || "").toLowerCase().includes(needle) ||
        e.ss58.toLowerCase().includes(needle)
      );
    }
    const dir = sortDir === "asc" ? 1 : -1;
    r = [...r].sort((a, b) => {
      const av = (a as Record<SortKey, number>)[sortKey] ?? 0;
      const bv = (b as Record<SortKey, number>)[sortKey] ?? 0;
      return (av - bv) * dir;
    });
    return r;
  }, [entries, search, minSubnets, minStake, sortKey, sortDir]);

  const totals = useMemo(() => {
    const stake = filtered.reduce((a, e) => a + e.total_stake_tao, 0);
    const priced = filtered.filter((e) => e.baseline !== false);
    const pnl = priced.reduce((a, e) => a + e.pnl_tao, 0);
    // "Best" ranks on market %: the raw leader is whoever deposited most
    // over the window, which tells you nothing about who to copy.
    const best = priced.reduce<LeaderboardEntry | null>(
      (b, e) => (!b || (e.market_pct ?? 0) > (b.market_pct ?? 0) ? e : b), null);
    return { stake, pnl, best, warming: filtered.length - priced.length };
  }, [filtered]);

  const maxStake = Math.max(1, ...filtered.map((e) => e.total_stake_tao));

  // Only what's on screen counts as "all" — ticking rows the filter hides
  // would send traders you never looked at into the basket.
  const pickedVisible = useMemo(
    () => filtered.filter((e) => picked.has(e.ss58)),
    [filtered, picked],
  );
  const allPicked = filtered.length > 0 && pickedVisible.length === filtered.length;

  const togglePick = (ss58: string) =>
    setPicked((cur) => {
      const next = new Set(cur);
      next.has(ss58) ? next.delete(ss58) : next.add(ss58);
      return next;
    });

  const pickMany = (rows: LeaderboardEntry[]) =>
    setPicked((cur) => {
      const next = new Set(cur);
      for (const e of rows) next.add(e.ss58);
      return next;
    });

  const open = (ss58: string) => router.push(`/traders/${ss58}`);

  /** Stop a row click when the thing clicked does its own job. */
  const own = (e: React.MouseEvent) => e.stopPropagation();

  const Th = ({ k, label, num, width }: { k: SortKey; label: string; num?: boolean; width?: number }) => (
    <th
      onClick={() => toggleSort(k)}
      style={width ? { width } : undefined}
      className={`sortable ${sortKey === k ? "sorted" : ""} ${num ? "num" : ""}`}
    >
      {label} {sortKey === k && (sortDir === "desc" ? "▼" : "▲")}
    </th>
  );

  return (
    <section className="space-y-4">
      {/* summary tiles */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <StatTile label="traders ranked" value={String(entries.length)}
              sub={totals.warming
                ? `${totals.warming} still warming`
                : universe?.board?.indexed
                  ? `indexed by bt · ${universe.watched} watched`
                  : universe?.known
                    ? `of ${fmtCompact(universe.known)} coldkeys on-chain`
                    : "all with history"} />
        <StatTile label="combined stake" value={fmtValue(totals.stake, currency, usdPerTao)} />
        <StatTile
          label={`combined pnl · ${win}`}
          value={fmtValue(totals.pnl, currency, usdPerTao)}
          tone={totals.pnl >= 0 ? "up" : "down"}
        />
        <StatTile
          label={`best return · ${win}`}
          value={totals.best
            ? `${(totals.best.market_pct ?? 0) >= 0 ? "+" : ""}${(totals.best.market_pct ?? 0).toFixed(1)}%`
            : "—"}
          sub={totals.best ? (totals.best.label || shortSs58(totals.best.ss58)) : undefined}
          tone={totals.best && (totals.best.market_pct ?? 0) >= 0 ? "up" : "down"}
        />
      </div>

      <div className="space-y-2">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <h2 className="font-display text-lg font-bold min-w-0">
            Top performers
            <span className="text-pixel-gray text-xs ml-2 font-mono">
              ({filtered.length}/{entries.length}
              {/* Where the pool came from is desktop detail — on a phone it
                  wrapped the heading onto a third line to say it. */}
              <span className="hidden lg:inline">
                {universe?.board?.indexed
                  ? " indexed"
                  : universe?.known
                    ? ` of ${fmtCompact(universe.known)} on-chain`
                    : ""}
              </span>)
            </span>
            {universe?.status === "discovering" && (
              <span className="text-[10px] ml-2 font-mono text-green-400 animate-pulse">
                adding traders… {universe.watched}/{universe.target}
              </span>
            )}
            {universe?.status === "error" && (
              <span className="text-[10px] ml-2 font-mono text-red-400">
                pool: {universe.error}
              </span>
            )}
          </h2>

          {/* A basket is the point of the board — take the slice you're looking
              at straight into the builder instead of clicking COPY ten times. */}
          {filtered.length > 1 && (
            <button
              onClick={() => openStrat(...filtered.slice(0, 10).map((e) => e.ss58))}
              title="Open the strat maker with the top 10 rows shown"
              className="pixel-btn text-[11px] px-2 py-1 text-green-400 border-green-400/40"
            >
              + INDEX TOP {Math.min(10, filtered.length)}
            </button>
          )}
        </div>

        {/* The tick bar. Only here once something is ticked — an always-on
            row of disabled buttons above every board is noise. */}
        {picked.size > 0 && (
          <div className="pixel-panel flex flex-wrap items-center gap-2 px-3 py-2 border-green-400/40">
            <span className="font-mono text-[12px] text-green-400 tabular-nums">
              {picked.size} SELECTED
            </span>
            {pickedVisible.length !== picked.size && (
              <span
                className="font-mono text-[10px] text-pixel-gray"
                title="Some ticked traders are hidden by the current filters — they're still in the selection"
              >
                ({pickedVisible.length} shown)
              </span>
            )}
            <button
              onClick={() => pickMany(filtered)}
              disabled={allPicked}
              title="Tick every row the filters are showing"
              className="pixel-btn text-[10px] px-2 py-0.5 text-pixel-gray-light disabled:opacity-40"
            >
              ALL {filtered.length}
            </button>
            <button
              onClick={() => setPicked(new Set())}
              className="pixel-btn text-[10px] px-2 py-0.5 text-pixel-gray-light"
            >
              CLEAR
            </button>
            <button
              onClick={() => openStrat(...Array.from(picked))}
              title="Open the strat maker with every ticked trader"
              className="pixel-btn text-[11px] px-2 py-1 ml-auto text-green-400 border-green-400"
            >
              + COPY {picked.size} SELECTED
            </button>
          </div>
        )}

        {/* Controls, in the order you actually reach for them: the horizon
            first (with the history behind each one printed on it), then what
            the ranking means and how big a book has to be to appear. Pool
            size and the subnet floor are set-once knobs and fold behind ⚙ on
            a phone rather than filling the screen with pills. */}
        <div className="filter-bar">
          <div className="flex items-start gap-2 min-w-0">
            <WindowRail caption={false} className="flex-1 min-w-0" />
            <button
              onClick={() => setKnobs((k) => !k)}
              aria-expanded={knobs}
              title="Pool size and subnet floor"
              className={`pixel-btn window-chip shrink-0 lg:hidden ${
                knobs ? "nav-active" : "text-pixel-gray-light"
              }`}
            >
              ⚙
            </button>
          </div>

          <div className="filter-bar-row">
            <RankBy />
            <StakeFloor />
          </div>

          {/* What the window rail is measured against. Without it the board
              happily offered 30 days over an index twelve days old. */}
          <p className="window-rail-note">
            {cov ? (
              <>
                showing {windowPhrase(days)} · index reaches back{" "}
                <span className="window-rail-em">{cov.depth_days} days</span>
                {" · "}
                {cov.priced} of {cov.traders} traders priced
                {universe?.board?.source?.[String(horizon)] === "rpc" && " · walked from chain"}
              </>
            ) : (
              <>measuring how far back the index goes…</>
            )}
          </p>

          <div
            className={`${knobs ? "flex" : "hidden"} lg:flex flex-wrap items-center gap-x-3 gap-y-2`}
          >
            {/* How many traders we rank. The board can only show accounts we
                watch, so this is the control that answers "why so few?". */}
            <div className="flex items-center gap-1">
              <span className="text-[11px] text-pixel-gray-light mr-1">pool</span>
              {POOL_SIZES.map((n) => (
                <button
                  key={n}
                  onClick={() => grow(n)}
                  disabled={busy || universe?.status === "discovering"}
                  title={`Watch the top ${n} coldkeys by stake`}
                  className={`pixel-btn text-[11px] px-2 py-1 disabled:opacity-40 ${
                    (universe?.pool_size ?? 0) === n
                      ? "border-green-400 text-green-400"
                      : "text-pixel-gray-light"
                  }`}
                >
                  {n}
                </button>
              ))}
            </div>

            <label className="text-[11px] text-pixel-gray-light flex items-center gap-2">
              min subnets
              <input
                type="number"
                min={0}
                max={64}
                value={minSubnets}
                onChange={(e) => setMinSubnets(Number(e.target.value) || 0)}
                className="pixel-input-sm w-16 text-right font-mono"
              />
            </label>
          </div>
        </div>
      </div>

      {error && (
        <div className="pixel-panel-red px-3 py-2 text-[12px] text-red-400 font-mono">
          {error}
        </div>
      )}

      {/* Under lg the nine-column board becomes one card per trader. A 900px
          table on a 390px screen is either a sideways scroll nobody finds or
          a column of ellipses — and the ranking is the whole product, so it's
          the one surface that gets its own handheld layout rather than a
          scrollbar. */}
      <div className="lg:hidden space-y-2">
        {loading && entries.length === 0 ? (
          <p className="text-pixel-gray text-sm py-4 text-center">loading leaderboard…</p>
        ) : filtered.length === 0 ? (
          <p className="text-pixel-gray text-sm py-4 text-center px-4">
            {universe && (universe.board?.building || []).includes(horizon)
              ? `pricing ${universe.watched} traders over ${windowPhrase(days)} — first pass takes a couple of minutes, rows appear here`
              : universe?.status === "discovering"
                ? "adding traders to the pool…"
                : "No matches. Widen the filters, or grow the pool above."}
          </p>
        ) : (
          filtered.map((e, i) => (
            <TraderCard
              key={e.ss58}
              e={e}
              i={i}
              days={days}
              picked={picked.has(e.ss58)}
              onPick={() => togglePick(e.ss58)}
              onCopy={() => openStrat(e.ss58)}
            />
          ))
        )}
      </div>

      <div className="pixel-panel overflow-x-auto hidden lg:block">
        <table className="pixel-table" style={{ minWidth: 920 }}>
          <thead className="sticky">
            <tr>
              <th className="tick" style={{ width: 34 }}>
                <input
                  type="checkbox"
                  checked={allPicked}
                  // Some-but-not-all reads as a dash, so one more click is
                  // obviously "take the rest" rather than "start over".
                  ref={(el) => {
                    if (el) el.indeterminate = !allPicked && pickedVisible.length > 0;
                  }}
                  onChange={() =>
                    allPicked
                      ? setPicked(new Set())
                      : pickMany(filtered)
                  }
                  disabled={!filtered.length}
                  aria-label="select every trader shown"
                  title="Select every trader shown"
                />
              </th>
              <th style={{ width: 56 }}>#</th>
              {/* No width: with `table-layout: fixed` the one unsized column
                  soaks up whatever the sized ones leave, so the name grows
                  with the screen instead of the numbers doing it. */}
              <th>Trader</th>
              <Th k="total_stake_tao" label={`Stake (${currency === "USD" ? "$" : "τ"})`} num width={160} />
              {/* The PnL pair is the widest thing on the row — "+229.5635 τ
                  (+63.52%)" needs the room, and used to get 150px and an
                  ellipsis right through the number you came to read. */}
              <Th k="pnl_tao" label={`${win} PnL`} num width={230} />
              <Th k="pnl_pct" label={`${win} %`} num width={120} />
              <Th k="market_pct" label="Market %" num width={130} />
              <Th k="num_subnets" label="SNs" num width={70} />
              <th style={{ width: 104 }}></th>
            </tr>
          </thead>
          <tbody>
            {loading && entries.length === 0 ? (
              <tr>
                <td colSpan={9} className="text-center text-pixel-gray py-6">
                  loading leaderboard…
                </td>
              </tr>
            ) : filtered.length === 0 ? (
              <tr>
                <td colSpan={9} className="text-center text-pixel-gray py-6">
                  {universe && (universe.board?.building || []).includes(horizon) ? (
                    <>pricing {universe.watched} traders over {windowPhrase(days)} — first
                       pass takes a couple of minutes, rows appear here</>
                  ) : universe?.status === "discovering" ? (
                    <>adding traders to the pool…</>
                  ) : (
                    <>No matches. Widen the filters, or grow the pool above.</>
                  )}
                </td>
              </tr>
            ) : (
              filtered.map((e, i) => {
                const share = (e.total_stake_tao / maxStake) * 100;
                const on = picked.has(e.ss58);
                return (
                  // The whole row opens the trader — the name was the only
                  // hit target before, which is a 200px link in a 900px row.
                  <tr
                    key={e.ss58}
                    onClick={() => open(e.ss58)}
                    className={`cursor-pointer ${on ? "row-picked" : ""}`}
                  >
                    <td className="tick" onClick={own}>
                      <input
                        type="checkbox"
                        checked={on}
                        onChange={() => togglePick(e.ss58)}
                        aria-label={`select ${e.label || shortSs58(e.ss58)}`}
                      />
                    </td>
                    <td className="num">
                      <Rank i={i} />
                    </td>
                    <td className="stack">
                      <Link
                        href={`/traders/${e.ss58}`}
                        onClick={own}
                        className="flex items-center gap-2 no-underline group"
                        title={e.ss58}
                      >
                        <Identicon ss58={e.ss58} />
                        <span className="min-w-0">
                          <span className="block text-pixel-white group-hover:text-green-400 truncate">
                            {e.label || shortSs58(e.ss58)}
                          </span>
                          {/* The address is only worth a second line when the
                              first one is a name — most coldkeys are unlabelled
                              and it was printing the same string twice. */}
                          {e.label && (
                            <span className="block text-[10px] text-pixel-gray font-mono truncate">
                              {shortSs58(e.ss58)}
                            </span>
                          )}
                        </span>
                      </Link>
                    </td>
                    <td className="num font-mono stack">
                      {/* `.stack` turns wrapping back on so the meter below
                          can sit on its own line — the number itself must
                          still be one line or "13.38K τ" breaks after the K. */}
                      <span className="block whitespace-nowrap">{fmtValue(e.total_stake_tao, currency, usdPerTao)}</span>
                      {/* 8px tall, not 4 — the meter needs room inside its
                          2px frame for the cell notches to read at all. */}
                      <span className="pixel-bar !h-2 mt-1.5 ml-auto block" style={{ maxWidth: 90 }}>
                        <span
                          className="pixel-bar-fill block bg-pixel-white/45"
                          style={{ width: `${share}%` }}
                        />
                      </span>
                    </td>
                    {e.baseline === false ? (
                      <td colSpan={3} className="num text-pixel-gray font-mono" title="No history yet — PnL appears once the first snapshot ages">
                        — warming
                      </td>
                    ) : (
                      <>
                        <td className="num">
                          <PnlBadge tao={e.pnl_tao} pct={e.pnl_pct} size="sm" />
                        </td>
                        <td className={`num font-mono ${e.pnl_pct >= 0 ? "text-green-400" : "text-red-400"}`}>
                          {e.pnl_pct >= 0 ? "+" : ""}{e.pnl_pct.toFixed(2)}%
                          {/* Rows whose history is shorter than the horizon
                              are not comparable to the rest — say so. */}
                          {/* Over the all-history window every row has its
                              own length, so print the span rather than
                              flagging every one of them as short. */}
                          {e.window_days > 0 && (days === 0 || e.window_days < days * 0.9) && (
                            <span
                              className={`block text-[9px] ${days === 0 ? "text-pixel-gray" : "text-amber-400"}`}
                              title={days === 0
                                ? `${e.window_days}d of history behind this number`
                                : `Only ${e.window_days}d of history for this trader — not a full ${days}d window`}
                            >
                              {e.window_days}d{days === 0 ? " history" : " only"}
                            </span>
                          )}
                        </td>
                        {/* What the book actually earned, with deposits and
                            withdrawals taken out — a coldkey that merely
                            funded itself shows +0% here and a huge flow. */}
                        <td className="num font-mono">
                          {e.market_pct == null ? (
                            <span className="text-pixel-gray">—</span>
                          ) : (
                            <span className={e.market_pct >= 0 ? "text-green-400" : "text-red-400"}>
                              {e.market_pct >= 0 ? "+" : ""}{e.market_pct.toFixed(2)}%
                            </span>
                          )}
                          {!!e.flow_tao && Math.abs(e.flow_tao) > Math.abs(e.market_pnl_tao ?? 0) && (
                            <span
                              className="block text-[9px] text-pixel-gray"
                              title={`${e.flow_tao > 0 ? "Deposited" : "Withdrew"} ${Math.abs(e.flow_tao).toFixed(2)} τ over this window — the headline % is mostly flow, not trading`}
                            >
                              {e.flow_tao > 0 ? "+" : "−"}{fmtCompact(Math.abs(e.flow_tao))} τ flow
                            </span>
                          )}
                        </td>
                      </>
                    )}
                    <td className="num text-pixel-gray-light font-mono">{e.num_subnets}</td>
                    <td onClick={own}>
                      <button
                        onClick={() => openStrat(e.ss58)}
                        title="Add to the strat maker's basket"
                        className="pixel-btn text-[10px] px-2 py-0.5 text-green-400 border-green-400/40"
                      >
                        COPY
                      </button>
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}

/**
 * One row of the board, on a phone. The same numbers as the table — stake,
 * PnL and the market-only PnL — stacked as labelled cells instead of
 * columns, with the two things you do with a trader (open it, copy it) on
 * the top line where a thumb lands.
 */
function TraderCard({
  e, i, days, picked, onPick, onCopy,
}: {
  e: LeaderboardEntry;
  i: number;
  days: number;
  picked: boolean;
  onPick: () => void;
  onCopy: () => void;
}) {
  const { currency, usdPerTao } = useCurrency();
  const win = windowLabel(days);
  const warming = e.baseline === false;

  return (
    <div className={`row-card ${picked ? "row-picked" : ""}`}>
      <div className="flex items-center gap-2.5">
        {/* The tick sits before the rank so a column of them lines up down
            the left edge — the same place the table puts it. */}
        <input
          type="checkbox"
          checked={picked}
          onChange={onPick}
          className="shrink-0"
          aria-label={`select ${e.label || shortSs58(e.ss58)}`}
        />
        <Rank i={i} />
        <Link
          href={`/traders/${e.ss58}`}
          className="flex items-center gap-2 min-w-0 flex-1 no-underline"
          title={e.ss58}
        >
          <Identicon ss58={e.ss58} />
          <span className="min-w-0">
            <span className="block text-pixel-white truncate">
              {e.label || shortSs58(e.ss58)}
            </span>
            {/* The address only earns the second line when the first one is
                a label — otherwise it's the same string printed twice. */}
            <span className="block text-[10px] text-pixel-gray font-mono truncate">
              {e.label ? `${shortSs58(e.ss58)} · ` : ""}
              {e.num_subnets} subnet{e.num_subnets === 1 ? "" : "s"}
            </span>
          </span>
        </Link>
        <button
          onClick={onCopy}
          title="Add to the strat maker's basket"
          className="pixel-btn text-[10px] px-3 py-1 shrink-0 text-green-400 border-green-400/40"
        >
          COPY
        </button>
      </div>

      <div className="row-card-grid mt-2.5 pt-2.5 border-t border-pixel-white/10">
        <div>
          <p className="row-card-k">stake</p>
          <p className="row-card-v">{fmtValue(e.total_stake_tao, currency, usdPerTao)}</p>
        </div>
        <div>
          <p className="row-card-k">{win} pnl</p>
          <p className="row-card-v">
            {warming ? (
              <span className="text-pixel-gray">— warming</span>
            ) : (
              <PnlBadge tao={e.pnl_tao} pct={e.pnl_pct} size="sm" />
            )}
          </p>
        </div>
        {!warming && (
          <>
            <div>
              <p className="row-card-k">{win} %</p>
              <p className={`row-card-v ${e.pnl_pct >= 0 ? "text-green-400" : "text-red-400"}`}>
                {e.pnl_pct >= 0 ? "+" : ""}{e.pnl_pct.toFixed(2)}%
                {e.window_days > 0 && (days === 0 || e.window_days < days * 0.9) && (
                  <span className="text-pixel-gray text-[11px] ml-1">
                    ({e.window_days}d{days === 0 ? "" : " only"})
                  </span>
                )}
              </p>
            </div>
            <div>
              {/* What the book earned on price, with deposits and withdrawals
                  taken out — the number that says whether copying is worth it. */}
              <p className="row-card-k">market %</p>
              <p className="row-card-v">
                {e.market_pct == null ? (
                  <span className="text-pixel-gray">—</span>
                ) : (
                  <span className={e.market_pct >= 0 ? "text-green-400" : "text-red-400"}>
                    {e.market_pct >= 0 ? "+" : ""}{e.market_pct.toFixed(2)}%
                  </span>
                )}
              </p>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function Rank({ i }: { i: number }) {
  // Solid medal plate, dark glyph. The old version tinted the badge to 13%
  // of the medal colour, which left silver invisible on a light background —
  // a filled block reads at any brightness, and looks like a scoreboard.
  const medal = ["#ffcf28", "#c9d4e8", "#ff8a1f"][i];
  if (!medal) return <span className="text-pixel-gray font-mono">{i + 1}</span>;
  return (
    <span
      className="medal-plate inline-flex items-center justify-center w-5 h-5 text-[10px] font-bold"
      style={{ background: medal, color: "#05030a", boxShadow: "2px 2px 0 var(--shadow-hard)" }}
    >
      {i + 1}
    </span>
  );
}
