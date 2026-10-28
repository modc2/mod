"use client";

import { useEffect, useState } from "react";
import type { Trade } from "../lib/types";
import { fetchTrades, ago } from "../lib/api";
import { useCurrency, fmtValue } from "../context/CurrencyContext";
import PageHeader from "../components/PageHeader";
import MyCopies from "../components/MyCopies";

export default function PortfolioPage() {
  const { currency, usdPerTao } = useCurrency();
  const [trades, setTrades] = useState<Trade[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchTrades(200)
      .then(setTrades)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="space-y-8">
      <PageHeader title="MY COPIES">
        The traders you follow, and every move we made on your behalf.
      </PageHeader>

      <MyCopies />

      <section className="space-y-3">
        <h2 className="font-display text-pixel-white">Activity</h2>
        {loading ? (
          <p className="arcade-prose-sm">loading…</p>
        ) : trades.length === 0 ? (
          <p className="arcade-prose-sm">No moves yet — the first sync happens within a few minutes of starting a copy.</p>
        ) : (
          <>
            {/* Cards on a phone, a table from lg up. */}
            <div className="lg:hidden space-y-2">
              {trades.map((t) => (
                <div key={t.id} className="row-card">
                  <div className="flex items-center gap-2">
                    <Badge kind={t.action} />
                    <span className="font-mono text-pixel-white">SN{t.netuid}</span>
                    <span className="font-mono text-pixel-white ml-auto tabular-nums">
                      {fmtValue(t.amount_tao, currency, usdPerTao)}
                    </span>
                  </div>
                  <div className="flex items-center gap-2 mt-2.5 pt-2.5 border-t border-pixel-white/10">
                    <Badge kind={t.status} />
                    <span className="text-[11px] text-pixel-gray">{ago(t.timestamp)}</span>
                  </div>
                  {t.error && <p className="text-[11px] text-red-400 mt-2 break-words">{t.error}</p>}
                </div>
              ))}
            </div>

            <div className="pixel-panel overflow-hidden hidden lg:block">
              <table className="pixel-table">
                <thead className="sticky">
                  <tr>
                    <th>When</th>
                    <th>What</th>
                    <th>Subnet</th>
                    <th className="num">Amount</th>
                    <th>Status</th>
                    <th>Note</th>
                  </tr>
                </thead>
                <tbody>
                  {trades.map((t) => (
                    <tr key={t.id}>
                      <td className="text-pixel-gray text-xs">{ago(t.timestamp)}</td>
                      <td><Badge kind={t.action} /></td>
                      <td className="font-mono">SN{t.netuid}</td>
                      <td className="num font-mono text-pixel-white">{fmtValue(t.amount_tao, currency, usdPerTao)}</td>
                      <td><Badge kind={t.status} /></td>
                      <td className="text-[11px] text-red-400 max-w-xs truncate">{t.error || ""}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </section>
    </div>
  );
}

function Badge({ kind }: { kind: string }) {
  const cls =
    kind === "stake" || kind === "confirmed" ? "border-green-400/40 text-green-400"
    : kind === "pending" ? "border-amber-400/40 text-amber-400"
    : "border-red-400/40 text-red-400";
  const label = kind === "stake" ? "buy" : kind === "unstake" ? "sell" : kind;
  return <span className={`pixel-badge ${cls}`}>{label}</span>;
}
