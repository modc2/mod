"use client";
import { useState, useEffect } from "react";
import { useParams, useSearchParams } from "next/navigation";
import Link from "next/link";
import { fetchTrader } from "../../lib/api";
import { Trader, Chain } from "../../lib/types";
import { fmtUsd, fmtPct, shortAddr, pnlColor, scoreColor } from "../../lib/format";
import Sparkline from "../../components/Sparkline";
import TokenFlow from "../../components/TokenFlow";
import PoolBreakdown from "../../components/PoolBreakdown";
import MevBadge from "../../components/MevBadge";

export default function TraderProfilePage() {
  const params = useParams();
  const searchParams = useSearchParams();
  const address = params.address as string;
  const chain = (searchParams.get("chain") || "base") as Chain;

  const [trader, setTrader] = useState<Trader | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    setLoading(true);
    fetchTrader(address, chain, 30)
      .then((res) => {
        if ("trader" in res) {
          setTrader(res.trader);
        } else {
          setError(res.error || "Not found");
        }
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [address, chain]);

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="animate-pulse-glow text-uni-pink">Loading...</div>
      </div>
    );
  }

  if (error || !trader) {
    return (
      <div className="min-h-screen flex items-center justify-center flex-col gap-4">
        <p className="text-uni-red text-sm">{error || "Trader not found"}</p>
        <Link href="/traders" className="btn">Back to leaderboard</Link>
      </div>
    );
  }

  return (
    <div className="min-h-screen">
      {/* Header */}
      <header className="border-b border-uni-border px-6 py-4">
        <div className="flex items-center gap-4">
          <Link href="/traders" className="text-uni-muted hover:text-uni-pink text-xs">
            &larr; Back
          </Link>
          <h1 className="text-sm font-bold font-mono">{shortAddr(address)}</h1>
          <span className="badge badge-chain">{chain}</span>
          <MevBadge isMev={trader.is_mev_bot} indicators={trader.mev_indicators} />
        </div>
      </header>

      <main className="p-6 space-y-6">
        {/* Stats Grid */}
        <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3">
          <StatCard label="Score" value={trader.composite_score.toFixed(1)} className={scoreColor(trader.composite_score)} />
          <StatCard label="PnL" value={fmtUsd(trader.realized_pnl_usd)} className={pnlColor(trader.realized_pnl_usd)} />
          <StatCard label="Volume" value={fmtUsd(trader.total_volume_usd)} />
          <StatCard label="Win Rate" value={fmtPct(trader.win_rate)} />
          <StatCard label="Swaps" value={String(trader.swap_count)} />
          <StatCard label="Pools" value={String(trader.unique_pools)} />
        </div>

        {/* P&L Curve */}
        <div className="card p-4">
          <h3 className="text-xs font-bold text-uni-muted mb-3 uppercase">P&L Curve (30d)</h3>
          <Sparkline data={trader.pnl_curve} width={600} height={80} />
        </div>

        {/* Volume Curve */}
        <div className="card p-4">
          <h3 className="text-xs font-bold text-uni-muted mb-3 uppercase">Cumulative Volume</h3>
          <Sparkline data={trader.volume_curve} width={600} height={60} color="#7b3fe4" />
        </div>

        {/* Token Flow + Pool Breakdown */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <TokenFlow tokens={trader.top_tokens} />
          <PoolBreakdown pools={trader.pools_traded} diversityScore={trader.pool_diversity_score} />
        </div>

        {/* MEV Analysis */}
        {trader.is_mev_bot && (
          <div className="card p-4">
            <h3 className="text-xs font-bold text-uni-red mb-3 uppercase">MEV Indicators</h3>
            <div className="grid grid-cols-2 md:grid-cols-5 gap-3 text-[11px]">
              <div>
                <div className="text-uni-muted">Sandwiches</div>
                <div className="font-bold">{trader.mev_indicators.sandwich_count}</div>
              </div>
              <div>
                <div className="text-uni-muted">Arb Txns</div>
                <div className="font-bold">{trader.mev_indicators.arb_count}</div>
              </div>
              <div>
                <div className="text-uni-muted">Avg Swaps/Day</div>
                <div className="font-bold">{trader.mev_indicators.avg_swaps_per_day.toFixed(1)}</div>
              </div>
              <div>
                <div className="text-uni-muted">Min Interval</div>
                <div className="font-bold">{trader.mev_indicators.min_swap_interval_sec}s</div>
              </div>
              <div>
                <div className="text-uni-muted">Top Pool Ratio</div>
                <div className="font-bold">{fmtPct(trader.mev_indicators.high_volume_pool_ratio * 100)}</div>
              </div>
            </div>
          </div>
        )}

        {/* Raw Address */}
        <div className="text-[10px] text-uni-muted text-center font-mono pt-4">
          {address}
        </div>
      </main>
    </div>
  );
}

function StatCard({ label, value, className = "" }: { label: string; value: string; className?: string }) {
  return (
    <div className="card p-3">
      <div className="text-[9px] text-uni-muted uppercase">{label}</div>
      <div className={`text-sm font-bold mt-1 ${className}`}>{value}</div>
    </div>
  );
}
