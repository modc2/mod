"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import type { LeaderboardEntry } from "../lib/types";
import { fetchLeaderboard, shortSs58 } from "../lib/api";
import PnlBadge from "./PnlBadge";
import { useFilters, type SortKey } from "../context/FiltersContext";
import { useCurrency, fmtValue } from "../context/CurrencyContext";

const WINDOWS = [1, 3, 7, 14, 30];

export default function Leaderboard() {
  const { days, setDays, search, sortKey, sortDir, toggleSort, minSubnets,
          setMinSubnets, reloadKey } = useFilters();
  const { currency, usdPerTao } = useCurrency();
  const [entries, setEntries] = useState<LeaderboardEntry[]>([]);
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

  const Th = ({ k, label, num }: { k: SortKey; label: string; num?: boolean }) => (
    <th
      onClick={() => toggleSort(k)}
      className={`sortable num:text-right ${sortKey === k ? "sorted" : ""} ${num ? "num" : ""}`}
    >
      {label} {sortKey === k && (sortDir === "desc" ? "▼" : "▲")}
    </th>
  );

  return (
    <section className="space-y-4">
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

      <div className="pixel-panel overflow-hidden">
        <table className="pixel-table">
          <thead className="sticky">
            <tr>
              <th style={{ width: 36 }}>#</th>
              <th>Validator</th>
              <Th k="total_stake_tao" label={`Stake (${currency === "USD" ? "$" : "τ"})`} num />
              <Th k="pnl_tao" label={`${days}d PnL (${currency === "USD" ? "$" : "τ"})`} num />
              <Th k="pnl_pct" label={`${days}d %`} num />
              <Th k="num_subnets" label="SNs" num />
              <th>Top SN</th>
              <th style={{ width: 80 }}></th>
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
              filtered.map((e, i) => (
                <tr key={e.ss58}>
                  <td className="text-pixel-gray num">{i + 1}</td>
                  <td>
                    <Link
                      href={`/traders/${e.ss58}`}
                      className="font-mono text-pixel-white hover:text-green-400 no-underline"
                    >
                      {e.label || shortSs58(e.ss58)}
                    </Link>
                  </td>
                  <td className="num font-mono">{fmtValue(e.total_stake_tao, currency, usdPerTao)}</td>
                  <td className="num">
                    <PnlBadge tao={e.pnl_tao} pct={e.pnl_pct} size="sm" />
                  </td>
                  <td className={`num font-mono ${e.pnl_pct >= 0 ? "text-green-400" : "text-red-400"}`}>
                    {e.pnl_pct >= 0 ? "+" : ""}{e.pnl_pct.toFixed(2)}%
                  </td>
                  <td className="num text-pixel-gray-light font-mono">{e.num_subnets}</td>
                  <td className="text-pixel-gray font-mono">
                    {e.top_subnet != null ? `SN${e.top_subnet}` : "—"}
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
              ))
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}
