"use client";
import { useState } from "react";
import Link from "next/link";
import { Trader } from "../lib/types";
import { fmtUsd, fmtPct, shortAddr, fmtNum, pnlColor, scoreColor } from "../lib/format";
import Sparkline from "./Sparkline";
import MevBadge from "./MevBadge";

interface Props {
  traders: Trader[];
  chain: string;
}

type SortKey = "score" | "pnl" | "volume" | "winrate" | "swaps" | "pools";

export default function TraderTable({ traders, chain }: Props) {
  const [sortBy, setSortBy] = useState<SortKey>("score");
  const [page, setPage] = useState(0);
  const perPage = 25;

  const sorted = [...traders].sort((a, b) => {
    switch (sortBy) {
      case "pnl": return b.realized_pnl_usd - a.realized_pnl_usd;
      case "volume": return b.total_volume_usd - a.total_volume_usd;
      case "winrate": return b.win_rate - a.win_rate;
      case "swaps": return b.swap_count - a.swap_count;
      case "pools": return b.unique_pools - a.unique_pools;
      default: return b.composite_score - a.composite_score;
    }
  });

  const pageTraders = sorted.slice(page * perPage, (page + 1) * perPage);
  const totalPages = Math.ceil(sorted.length / perPage);

  const th = (label: string, key: SortKey) => (
    <th
      onClick={() => setSortBy(key)}
      className={sortBy === key ? "!text-uni-pink" : ""}
    >
      {label} {sortBy === key && "▼"}
    </th>
  );

  return (
    <div>
      <div className="overflow-x-auto">
        <table className="trader-table w-full">
          <thead>
            <tr>
              <th>#</th>
              <th>Address</th>
              <th>P&L</th>
              {th("SCORE", "score")}
              {th("PNL", "pnl")}
              {th("VOLUME", "volume")}
              {th("WIN%", "winrate")}
              {th("SWAPS", "swaps")}
              {th("POOLS", "pools")}
              <th>MEV</th>
            </tr>
          </thead>
          <tbody>
            {pageTraders.map((t, i) => (
              <tr key={t.address}>
                <td className="text-uni-muted">{page * perPage + i + 1}</td>
                <td>
                  <Link
                    href={`/traders/${t.address}?chain=${chain}`}
                    className="hover:text-uni-pink transition-colors"
                  >
                    {shortAddr(t.address)}
                  </Link>
                </td>
                <td>
                  <Sparkline data={t.pnl_curve} width={100} height={24} />
                </td>
                <td className={scoreColor(t.composite_score)}>
                  {t.composite_score.toFixed(1)}
                </td>
                <td className={pnlColor(t.realized_pnl_usd)}>
                  {fmtUsd(t.realized_pnl_usd)}
                </td>
                <td>{fmtUsd(t.total_volume_usd)}</td>
                <td>{fmtPct(t.win_rate)}</td>
                <td>{fmtNum(t.swap_count)}</td>
                <td>{t.unique_pools}</td>
                <td>
                  <MevBadge isMev={t.is_mev_bot} indicators={t.mev_indicators} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between mt-4 px-2">
          <span className="text-[10px] text-uni-muted">
            {sorted.length} traders
          </span>
          <div className="flex gap-1">
            <button
              className="btn text-[10px] px-2 py-1"
              disabled={page === 0}
              onClick={() => setPage(page - 1)}
            >
              Prev
            </button>
            <span className="text-[10px] text-uni-muted px-2 py-1">
              {page + 1}/{totalPages}
            </span>
            <button
              className="btn text-[10px] px-2 py-1"
              disabled={page >= totalPages - 1}
              onClick={() => setPage(page + 1)}
            >
              Next
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
