"use client";

import { useEffect, useState } from "react";
import type { Trade } from "../lib/types";
import { fetchTrades, ago } from "../lib/api";
import { useCurrency, fmtValue } from "../context/CurrencyContext";
import PageHeader from "../components/PageHeader";

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
    <div className="space-y-5">
      <PageHeader title="PORTFOLIO">
        Every stake / unstake the copy engine has fired on your behalf.
      </PageHeader>

      {loading ? (
        <p className="text-pixel-gray text-sm">loading…</p>
      ) : trades.length === 0 ? (
        <div className="pixel-panel p-6">
          <p className="arcade-prose">
            No trades executed yet. Activate a copy from the
            <a href="/strats" className="text-green-400 mx-1">strats</a>
            tab to start mirroring a validator.
          </p>
        </div>
      ) : (
        <div className="pixel-panel overflow-hidden">
          <table className="pixel-table">
            <thead className="sticky">
              <tr>
                <th>When</th>
                <th>Copy</th>
                <th>Action</th>
                <th>Subnet</th>
                <th className="num">Amount</th>
                <th>Status</th>
                <th className="num">Block</th>
                <th>Error</th>
              </tr>
            </thead>
            <tbody>
              {trades.map((t) => (
                <tr key={t.id}>
                  <td className="text-pixel-gray text-xs">{ago(t.timestamp)}</td>
                  <td className="font-mono text-[11px] text-pixel-gray">
                    {t.copy_id.slice(0, 8)}…
                  </td>
                  <td>
                    <span
                      className={`pixel-badge ${
                        t.action === "stake"
                          ? "border-green-400/40 text-green-400"
                          : "border-red-400/40 text-red-400"
                      }`}
                    >
                      {t.action}
                    </span>
                  </td>
                  <td className="font-mono">SN{t.netuid}</td>
                  <td className="num font-mono text-pixel-white">{fmtValue(t.amount_tao, currency, usdPerTao)}</td>
                  <td>
                    <span
                      className={`pixel-badge ${
                        t.status === "confirmed"
                          ? "border-green-400/40 text-green-400"
                          : t.status === "pending"
                            ? "border-amber-400/40 text-amber-400"
                            : "border-red-400/40 text-red-400"
                      }`}
                    >
                      {t.status}
                    </span>
                  </td>
                  <td className="num text-pixel-gray font-mono text-xs">{t.block || "—"}</td>
                  <td className="text-[11px] text-red-400 max-w-xs truncate">
                    {t.error || ""}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
