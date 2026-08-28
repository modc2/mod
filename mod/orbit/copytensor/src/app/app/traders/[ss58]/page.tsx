"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import type { AccountData, CurveData, PnlData } from "../../lib/types";
import {
  fetchAccount,
  fetchCurve,
  fetchPnl,
  fmtPct,
  shortSs58,
  watchAccount,
} from "../../lib/api";
import PnlBadge from "../../components/PnlBadge";
import PnlCurve from "../../components/PnlCurve";
import StatTile from "../../components/StatTile";
import SubnetPositions from "../../components/SubnetPositions";
import { useCurrency, fmtValue, fmtPnlValue } from "../../context/CurrencyContext";
import { useFilters } from "../../context/FiltersContext";
import { useSidebar } from "../../context/SidebarContext";

const WINDOWS = [1, 3, 7, 14, 30];

export default function TraderPage() {
  const { ss58 } = useParams<{ ss58: string }>();
  const { currency, usdPerTao } = useCurrency();
  const { openStrat } = useSidebar();
  // The board's window, not a private one — clicking a row off a 7d board
  // and landing on a profile measured over something else is a silent lie.
  const { days, setDays } = useFilters();
  const [account, setAccount] = useState<AccountData | null>(null);
  const [pnl, setPnl] = useState<PnlData | null>(null);
  const [curve, setCurve] = useState<CurveData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [watched, setWatched] = useState(false);

  useEffect(() => {
    if (!ss58) return;
    setLoading(true);
    setError("");
    Promise.all([fetchAccount(ss58, days), fetchPnl(ss58, days)])
      .then(([a, p]) => {
        setAccount(a);
        setPnl(p);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [ss58, days]);

  // The curve reads only the local snapshot record — it must never block the
  // page on a slow chain walk, so it loads on its own.
  useEffect(() => {
    if (!ss58) return;
    let cancelled = false;
    setCurve(null);
    fetchCurve(ss58, days)
      .then((c) => !cancelled && setCurve(c))
      .catch(() => !cancelled && setCurve(null));
    return () => { cancelled = true; };
  }, [ss58, days]);

  if (loading && !account) {
    return <p className="text-pixel-gray">loading {shortSs58(ss58 || "")}…</p>;
  }
  if (error) {
    return (
      <div className="pixel-panel-red p-4 text-red-400 font-mono text-sm">{error}</div>
    );
  }
  if (!account) {
    return <p className="text-pixel-gray">account not found</p>;
  }

  return (
    <div className="space-y-6">
      <Link href="/traders" className="text-pixel-gray hover:text-pixel-white text-sm no-underline">
        ← traders
      </Link>

      <header className="pixel-panel p-4 sm:p-6 flex items-start justify-between flex-wrap gap-4">
        <div className="min-w-0">
          <h1 className="font-display text-2xl font-bold text-pixel-white">
            {shortSs58(ss58)}
          </h1>
          <p className="font-mono text-[11px] text-pixel-gray break-all max-w-[600px]">
            {ss58}
          </p>
        </div>
        <div className="flex gap-2 w-full sm:w-auto">
          <button
            className="pixel-btn text-[11px] flex-1 sm:flex-none"
            onClick={async () => {
              await watchAccount(ss58);
              setWatched(true);
            }}
            disabled={watched}
          >
            {watched ? "★ WATCHED" : "+ WATCH"}
          </button>
          <button
            onClick={() => openStrat(ss58)}
            title="Add to the strat maker's basket"
            className="pixel-btn text-[11px] flex-1 sm:flex-none border-green-400 text-green-400"
          >
            COPY
          </button>
        </div>
      </header>

      {/* The same scoreboard cluster the board uses — three hand-rolled
          panels here meant the PnL ran off the plate on a narrow screen and
          sat on a different baseline from every other readout in the app. */}
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
        <StatTile label="total stake" value={fmtValue(account.total_stake_tao, currency, usdPerTao)} />
        <StatTile
          label={`${days}d pnl`}
          value={fmtPnlValue(account.pnl_tao, currency, usdPerTao)}
          sub={fmtPct(account.pnl_pct)}
          tone={account.pnl_tao >= 0 ? "up" : "down"}
        />
        <StatTile label="subnets" value={String(account.allocations.length)} />
      </div>

      <div className="rail no-scrollbar">
        <span className="text-[11px] text-pixel-gray-light mr-1">history</span>
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

      {curve ? (
        <PnlCurve curve={curve} days={days} />
      ) : (
        <div className="pixel-panel p-6 text-sm text-pixel-gray">
          building PnL curve from snapshots…
        </div>
      )}

      <section>
        <h2 className="font-display text-lg font-bold mb-3">Allocations</h2>
        <SubnetPositions allocations={account.allocations} />
      </section>

      {pnl && pnl.by_subnet.length > 0 && (
        <section>
          <h2 className="font-display text-lg font-bold mb-3">
            PnL by subnet ({days}d)
          </h2>
          {/* Six numeric columns don't fit a phone at any type size worth
              reading — the plate scrolls sideways rather than crushing every
              price into an ellipsis. */}
          <div className="pixel-panel overflow-x-auto">
            <table className="pixel-table" style={{ minWidth: 660 }}>
              <thead className="sticky">
                <tr>
                  <th>Subnet</th>
                  <th className="num">Start α</th>
                  <th className="num">End α</th>
                  <th className="num">Start price</th>
                  <th className="num">End price</th>
                  <th className="num">PnL</th>
                </tr>
              </thead>
              <tbody>
                {pnl.by_subnet
                  .slice()
                  .sort((a, b) => b.pnl_tao - a.pnl_tao)
                  .map((s) => (
                    <tr key={s.netuid}>
                      <td>
                        <span className="font-mono text-pixel-white">SN{s.netuid}</span>
                        <span className="text-pixel-gray text-xs ml-2">
                          {s.subnet_name}
                        </span>
                      </td>
                      <td className="num font-mono">{s.alpha_start.toFixed(4)}</td>
                      <td className="num font-mono">{s.alpha_end.toFixed(4)}</td>
                      <td className="num font-mono">{s.price_start_tao.toFixed(6)}</td>
                      <td className="num font-mono">{s.price_end_tao.toFixed(6)}</td>
                      <td className="num">
                        <PnlBadge tao={s.pnl_tao} pct={s.pnl_pct} size="sm" />
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  );
}
