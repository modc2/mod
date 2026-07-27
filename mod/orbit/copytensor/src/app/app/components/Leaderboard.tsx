"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import type { LeaderboardEntry, SubnetInfo } from "../lib/types";
import { fetchLeaderboard, fetchSubnets, fmtCompact, shortSs58 } from "../lib/api";
import PnlBadge from "./PnlBadge";
import SubnetLogo from "./SubnetLogo";
import { useFilters, type SortKey } from "../context/FiltersContext";
import { useCurrency, fmtValue } from "../context/CurrencyContext";

const WINDOWS = [1, 3, 7, 14, 30];

export default function Leaderboard() {
  const { days, setDays, search, sortKey, sortDir, toggleSort, minSubnets,
          setMinSubnets, reloadKey } = useFilters();
  const { currency, usdPerTao } = useCurrency();
  const [entries, setEntries] = useState<LeaderboardEntry[]>([]);
  const [subnets, setSubnets] = useState<Map<number, SubnetInfo>>(new Map());
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    setLoading(true);
    setError("");
    fetchLeaderboard(days, 100)
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
    const best = priced.reduce<LeaderboardEntry | null>(
      (b, e) => (!b || e.pnl_pct > b.pnl_pct ? e : b), null);
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
        <Tile label="tracked validators" value={String(entries.length)}
              sub={totals.warming ? `${totals.warming} still warming` : "all with history"} />
        <Tile label="combined stake" value={fmtValue(totals.stake, currency, usdPerTao)} />
        <Tile
          label={`${days}d combined pnl`}
          value={fmtValue(totals.pnl, currency, usdPerTao)}
          tone={totals.pnl >= 0 ? "up" : "down"}
        />
        <Tile
          label={`best ${days}d`}
          value={totals.best ? `${totals.best.pnl_pct >= 0 ? "+" : ""}${totals.best.pnl_pct.toFixed(1)}%` : "—"}
          sub={totals.best ? (totals.best.label || shortSs58(totals.best.ss58)) : undefined}
          tone={totals.best && totals.best.pnl_pct >= 0 ? "up" : "down"}
        />
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <h2 className="font-display text-lg font-bold">
          Top performers
          <span className="text-pixel-gray text-xs ml-2 font-mono">
            ({filtered.length}/{entries.length})
          </span>
        </h2>

        <div className="flex gap-1">
          {WINDOWS.map((w) => (
            <button
              key={w}
              onClick={() => setDays(w)}
              className={`pixel-btn text-[11px] px-2 py-1 ${
                days === w ? "border-green-400 text-green-400" : "text-pixel-gray-light"
              }`}
            >
              {w}d
            </button>
          ))}
        </div>

        <label className="text-[11px] text-pixel-gray-light flex items-center gap-2 ml-auto">
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

      {error && (
        <div className="pixel-panel-red px-3 py-2 text-[12px] text-red-400 font-mono">
          {error}
        </div>
      )}

      <div className="pixel-panel overflow-x-auto">
        <table className="pixel-table" style={{ minWidth: 900 }}>
          <thead className="sticky">
            <tr>
              <th style={{ width: 44 }}>#</th>
              <th style={{ width: "28%" }}>Validator</th>
              <Th k="total_stake_tao" label={`Stake (${currency === "USD" ? "$" : "τ"})`} num />
              <Th k="pnl_tao" label={`${days}d PnL`} num />
              <Th k="pnl_pct" label={`${days}d %`} num />
              <Th k="num_subnets" label="SNs" num />
              <th style={{ width: 150 }}>Top subnet</th>
              <th style={{ width: 84 }}></th>
            </tr>
          </thead>
          <tbody>
            {loading && entries.length === 0 ? (
              <tr>
                <td colSpan={8} className="text-center text-pixel-gray py-6">
                  loading leaderboard…
                </td>
              </tr>
            ) : filtered.length === 0 ? (
              <tr>
                <td colSpan={8} className="text-center text-pixel-gray py-6">
                  No matches. Seed validators are auto-added on first boot —
                  give the snapshot worker a minute, then refresh.
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
                      <span className="block">{fmtValue(e.total_stake_tao, currency, usdPerTao)}</span>
                      <span className="pixel-bar !h-1 mt-1 ml-auto block" style={{ maxWidth: 90 }}>
                        <span
                          className="pixel-bar-fill block bg-pixel-white/25"
                          style={{ width: `${share}%` }}
                        />
                      </span>
                    </td>
                    {e.baseline === false ? (
                      <td colSpan={2} className="num text-pixel-gray font-mono" title="No history yet — PnL appears once the first snapshot ages">
                        — warming
                      </td>
                    ) : (
                      <>
                        <td className="num">
                          <PnlBadge tao={e.pnl_tao} pct={e.pnl_pct} size="sm" />
                        </td>
                        <td className={`num font-mono ${e.pnl_pct >= 0 ? "text-green-400" : "text-red-400"}`}>
                          {e.pnl_pct >= 0 ? "+" : ""}{e.pnl_pct.toFixed(2)}%
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
                      <Link
                        href={`/strats?target=${e.ss58}`}
                        className="pixel-btn text-[10px] px-2 py-0.5 text-green-400 border-green-400/40 no-underline"
                      >
                        COPY
                      </Link>
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

function Rank({ i }: { i: number }) {
  const medal = ["#facc15", "#cbd5e1", "#d97706"][i];
  if (!medal) return <span className="text-pixel-gray">{i + 1}</span>;
  return (
    <span
      className="inline-flex items-center justify-center w-5 h-5 rounded-full text-[10px] font-bold"
      style={{ background: `${medal}22`, color: medal, border: `1px solid ${medal}55` }}
    >
      {i + 1}
    </span>
  );
}

/** Deterministic two-tone dot so each coldkey is visually distinct. */
function Identicon({ ss58 }: { ss58: string }) {
  const seed = ss58.split("").reduce((a, c) => (a * 31 + c.charCodeAt(0)) % 360, 7);
  return (
    <span
      className="shrink-0 w-6 h-6 rounded-full"
      style={{
        background: `linear-gradient(135deg, hsl(${seed} 65% 45%), hsl(${(seed + 60) % 360} 60% 28%))`,
        boxShadow: "inset 0 0 0 1px rgba(255,255,255,0.12)",
      }}
      aria-hidden
    />
  );
}

function Tile({
  label,
  value,
  sub,
  tone,
}: {
  label: string;
  value: string;
  sub?: string;
  tone?: "up" | "down";
}) {
  const color =
    tone === "up" ? "text-green-400" : tone === "down" ? "text-red-400" : "text-pixel-white";
  return (
    <div className="pixel-panel px-4 py-3">
      <p className="text-[10px] tracking-[2px] uppercase text-pixel-gray">{label}</p>
      <p className={`font-mono text-lg font-bold tabular-nums mt-0.5 truncate ${color}`}>{value}</p>
      {sub && <p className="text-[10px] text-pixel-gray font-mono truncate">{sub}</p>}
    </div>
  );
}
