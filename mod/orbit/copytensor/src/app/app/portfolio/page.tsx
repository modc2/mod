"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
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
            {/* <Link>, not <a>: a raw href skips the /copytensor basePath
                and lands on the gateway root. */}
            <Link href="/strats" className="text-green-400 mx-1">strats</Link>
            tab to start mirroring a validator.
          </p>
        </div>
      ) : (
        <>
        {/* Eight columns of tape. On a phone each fill becomes a card —
            what happened, to which subnet, for how much — with the copy id
            and block number demoted to the footnote they are. */}
        <div className="lg:hidden space-y-2">
          {trades.map((t) => (
            <div key={t.id} className="row-card">
              <div className="flex items-center gap-2">
                <span
                  className={`pixel-badge ${
                    t.action === "stake"
                      ? "border-green-400/40 text-green-400"
                      : "border-red-400/40 text-red-400"
                  }`}
                >
                  {t.action}
                </span>
                <span className="font-mono text-pixel-white">SN{t.netuid}</span>
                <span className="font-mono text-pixel-white ml-auto tabular-nums">
                  {fmtValue(t.amount_tao, currency, usdPerTao)}
                </span>
              </div>
              <div className="flex items-center gap-2 mt-2.5 pt-2.5 border-t border-pixel-white/10">
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
                <span className="text-[11px] text-pixel-gray">{ago(t.timestamp)}</span>
                <span className="text-[11px] text-pixel-gray font-mono ml-auto truncate">
                  {t.copy_id.slice(0, 8)}… {t.block ? `· #${t.block}` : ""}
                </span>
              </div>
              {t.error && (
                <p className="text-[11px] text-red-400 mt-2 break-words">{t.error}</p>
              )}
            </div>
          ))}
        </div>

        <div className="pixel-panel overflow-hidden hidden lg:block">
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
        </>
      )}
    </div>
  );
}
