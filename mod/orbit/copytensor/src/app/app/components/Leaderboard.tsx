"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import type { LeaderboardEntry, SubnetInfo, Universe } from "../lib/types";
import { fetchLeaderboard, fetchSubnets, fetchUniverse, fmtCompact,
         setPool, shortSs58 } from "../lib/api";
import PnlBadge from "./PnlBadge";
import Identicon from "./Identicon";
import SubnetLogo from "./SubnetLogo";
import StatTile from "./StatTile";
import { useFilters, type SortKey } from "../context/FiltersContext";
import { useCurrency, fmtValue } from "../context/CurrencyContext";
import { useSidebar } from "../context/SidebarContext";

const WINDOWS = [1, 3, 7, 14, 30];
// Trader-pool sizes. Every step is real coldkeys ranked by on-chain stake;
// bigger pools take longer to price (one historical read per trader).
const POOL_SIZES = [100, 250, 500, 1000];

export default function Leaderboard() {
  const { days, setDays, search, sortKey, sortDir, toggleSort, minSubnets,
          setMinSubnets, reloadKey, reload } = useFilters();
  const { currency, usdPerTao } = useCurrency();
  const { openStrat } = useSidebar();
  const [entries, setEntries] = useState<LeaderboardEntry[]>([]);
  const [subnets, setSubnets] = useState<Map<number, SubnetInfo>>(new Map());
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

  // Names + logos for the "top SN" column — a bare "SN64" tells you nothing
  // about which subnet a validator is actually concentrated in.
  useEffect(() => {
    fetchSubnets()
      .then((all) => setSubnets(new Map(all.map((s) => [s.netuid, s]))))
      .catch(() => {});
  }, []);

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
            u.status === "discovering" || (u.board?.building || []).includes(days);
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
  }, [reloadKey, busy, days, reload]);

  const grow = (size: number) => {
    setBusy(true);
    setError("");
    setPool(size)
      .then(setUniverse)
      .catch((e) => { setBusy(false); setError(e.message); });
  };

  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase();
    let r = entries.filter((e) => e.num_subnets >= minSubnets);
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
  }, [entries, search, minSubnets, sortKey, sortDir]);

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

  const Th = ({ k, label, num }: { k: SortKey; label: string; num?: boolean }) => (
    <th
      onClick={() => toggleSort(k)}
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
          label={`${days}d combined pnl`}
          value={fmtValue(totals.pnl, currency, usdPerTao)}
          tone={totals.pnl >= 0 ? "up" : "down"}
        />
        <StatTile
          label={`best ${days}d market`}
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

        {/* Controls, in the order you actually reach for them: the horizon
            first, on a rail you can thumb; the pool and the subnet floor are
            set-once knobs and fold behind ⚙ on a phone rather than filling
            three rows of the screen with pills. */}
        <div className="flex flex-col gap-2 lg:flex-row lg:items-center lg:gap-3">
          <div className="flex items-center gap-2 min-w-0">
            {/* Bleeds off the left edge only — the ⚙ has to stay pinned where
                a thumb can find it, and inside a scrolling rail it scrolls
                away with everything else. */}
            <div className="rail no-scrollbar -ml-3 pl-3 lg:ml-0 lg:pl-0 min-w-0">
              {WINDOWS.map((w) => (
                <button
                  key={w}
                  onClick={() => setDays(w)}
                  className={`pixel-btn text-[11px] px-3 py-1 ${
                    days === w ? "border-green-400 text-green-400" : "text-pixel-gray-light"
                  }`}
                >
                  {w}d
                </button>
              ))}
            </div>
            <button
              onClick={() => setKnobs((k) => !k)}
              aria-expanded={knobs}
              title="Pool size and subnet floor"
              className={`pixel-btn text-[11px] px-3 py-1 shrink-0 lg:hidden ${
                knobs ? "nav-active" : "text-pixel-gray-light"
              }`}
            >
              ⚙
            </button>
          </div>

          <div
            className={`${knobs ? "flex" : "hidden"} lg:flex flex-wrap items-center gap-x-3 gap-y-2 lg:ml-auto`}
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
            {universe && (universe.board?.building || []).includes(days)
              ? `pricing ${universe.watched} traders over ${days}d — first pass takes a couple of minutes, rows appear here`
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
              sn={e.top_subnet != null ? subnets.get(e.top_subnet) : undefined}
              onCopy={() => openStrat(e.ss58)}
            />
          ))
        )}
      </div>

      <div className="pixel-panel overflow-x-auto hidden lg:block">
        <table className="pixel-table" style={{ minWidth: 900 }}>
          <thead className="sticky">
            <tr>
              <th style={{ width: 56 }}>#</th>
              <th style={{ width: "28%" }}>Trader</th>
              <Th k="total_stake_tao" label={`Stake (${currency === "USD" ? "$" : "τ"})`} num />
              <Th k="pnl_tao" label={`${days}d PnL`} num />
              <Th k="pnl_pct" label={`${days}d %`} num />
              <Th k="market_pct" label="Market %" num />
              <Th k="num_subnets" label="SNs" num />
              <th style={{ width: 190 }}>Top subnet</th>
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
                  {universe && (universe.board?.building || []).includes(days) ? (
                    <>pricing {universe.watched} traders over {days}d — first
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
                const sn = e.top_subnet != null ? subnets.get(e.top_subnet) : undefined;
                const share = (e.total_stake_tao / maxStake) * 100;
                return (
                  <tr key={e.ss58}>
                    <td className="num">
                      <Rank i={i} />
                    </td>
                    <td className="stack">
                      <Link
                        href={`/traders/${e.ss58}`}
                        className="flex items-center gap-2 no-underline group"
                        title={e.ss58}
                      >
                        <Identicon ss58={e.ss58} />
                        <span className="min-w-0">
                          <span className="block text-pixel-white group-hover:text-green-400 truncate">
                            {e.label || shortSs58(e.ss58)}
                          </span>
                          <span className="block text-[10px] text-pixel-gray font-mono truncate">
                            {shortSs58(e.ss58)}
                          </span>
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
                          {e.window_days > 0 && e.window_days < days * 0.9 && (
                            <span
                              className="block text-[9px] text-pixel-gray"
                              title={`Only ${e.window_days}d of history for this trader — not a full ${days}d window`}
                            >
                              {e.window_days}d only
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
                    <td>
                      {e.top_subnet == null ? (
                        <span className="text-pixel-gray">—</span>
                      ) : (
                        <Link
                          href={`/subnets/${e.top_subnet}`}
                          className="flex items-center gap-1.5 no-underline text-pixel-gray-light hover:text-green-400"
                        >
                          <SubnetLogo
                            netuid={e.top_subnet}
                            name={sn?.name}
                            symbol={sn?.symbol}
                            logo={sn?.logo}
                            size={18}
                          />
                          <span className="truncate">{sn?.name || `SN${e.top_subnet}`}</span>
                          {e.top_subnet_pnl !== 0 && (
                            <span
                              className={`text-[10px] font-mono ${e.top_subnet_pnl >= 0 ? "text-green-400" : "text-red-400"}`}
                            >
                              {e.top_subnet_pnl >= 0 ? "+" : ""}{fmtCompact(e.top_subnet_pnl)}
                            </span>
                          )}
                        </Link>
                      )}
                    </td>
                    <td>
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
 * PnL, the market-only PnL and the subnet the book is concentrated in —
 * stacked as labelled cells instead of columns, with the two things you do
 * with a trader (open it, copy it) on the top line where a thumb lands.
 */
function TraderCard({
  e, i, days, sn, onCopy,
}: {
  e: LeaderboardEntry;
  i: number;
  days: number;
  sn?: SubnetInfo;
  onCopy: () => void;
}) {
  const { currency, usdPerTao } = useCurrency();
  const warming = e.baseline === false;

  return (
    <div className="row-card">
      <div className="flex items-center gap-2.5">
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
          <p className="row-card-k">{days}d pnl</p>
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
              <p className="row-card-k">{days}d %</p>
              <p className={`row-card-v ${e.pnl_pct >= 0 ? "text-green-400" : "text-red-400"}`}>
                {e.pnl_pct >= 0 ? "+" : ""}{e.pnl_pct.toFixed(2)}%
                {e.window_days > 0 && e.window_days < days * 0.9 && (
                  <span className="text-pixel-gray text-[11px] ml-1">({e.window_days}d only)</span>
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

      {e.top_subnet != null && (
        <Link
          href={`/subnets/${e.top_subnet}`}
          className="flex items-center gap-1.5 no-underline mt-2.5 text-pixel-gray-light"
        >
          <SubnetLogo
            netuid={e.top_subnet}
            name={sn?.name}
            symbol={sn?.symbol}
            logo={sn?.logo}
            size={16}
          />
          <span className="text-[11px] truncate">{sn?.name || `SN${e.top_subnet}`}</span>
          {e.top_subnet_pnl !== 0 && (
            <span
              className={`text-[11px] font-mono ml-auto ${
                e.top_subnet_pnl >= 0 ? "text-green-400" : "text-red-400"
              }`}
            >
              {e.top_subnet_pnl >= 0 ? "+" : ""}{fmtCompact(e.top_subnet_pnl)}
            </span>
          )}
        </Link>
      )}
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
      className="inline-flex items-center justify-center w-5 h-5 text-[10px] font-bold"
      style={{ background: medal, color: "#05030a", boxShadow: "2px 2px 0 var(--shadow-hard)" }}
    >
      {i + 1}
    </span>
  );
}
