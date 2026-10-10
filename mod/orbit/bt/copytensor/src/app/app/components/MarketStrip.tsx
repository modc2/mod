"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { fetchMarket, fmtCompact } from "../lib/api";
import type { MarketStats, SubnetInfo } from "../lib/types";
import { useCurrency } from "../context/CurrencyContext";
import ChangeChip from "./ChangeChip";
import SubnetLogo from "./SubnetLogo";
import StatTile from "./StatTile";

const POLL_MS = 30_000;

/**
 * Network-wide header: TAO price, total alpha market cap, 24h volume,
 * chain head — plus the day's biggest movers. One `/market` call, which
 * the API answers off the same cached screener the grid uses.
 */
export default function MarketStrip() {
  const { currency, usdPerTao } = useCurrency();
  const [m, setM] = useState<MarketStats | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () =>
      fetchMarket()
        .then((d) => alive && setM(d))
        .catch(() => {});
    load();
    const id = setInterval(() => {
      if (document.visibilityState === "visible") load();
    }, POLL_MS);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  const usd = (tao: number) =>
    usdPerTao ? `$${fmtCompact(tao * usdPerTao)}` : null;
  const show = (tao: number) =>
    currency === "USD" && usdPerTao ? `$${fmtCompact(tao * usdPerTao)}` : `${fmtCompact(tao)} τ`;

  return (
    <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_auto]">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <StatTile
          label="TAO price"
          value={m?.tao_usd ? `$${m.tao_usd.toFixed(2)}` : "—"}
          sub="coingecko · 5m cache"
          tone="accent"
        />
        <StatTile
          label="alpha market cap"
          value={m ? show(m.total_market_cap_tao) : "—"}
          sub={m && currency !== "USD" ? usd(m.total_market_cap_tao) ?? undefined : undefined}
        />
        <StatTile
          label="24h volume"
          value={m ? show(m.volume_24h_tao) : "—"}
          sub={m ? `${fmtCompact(m.total_tao_in_pools)} τ in pools` : undefined}
        />
        <StatTile
          label="subnets"
          value={m ? String(m.subnets) : "—"}
          sub={m ? `block ${m.block.toLocaleString()}` : undefined}
        />
      </div>

      <div className="grid grid-cols-2 gap-3 lg:w-[420px]">
        <Movers title="Top gainers 24h" rows={m?.gainers} />
        <Movers title="Top losers 24h" rows={m?.losers} />
      </div>
    </div>
  );
}

function Movers({ title, rows }: { title: string; rows?: SubnetInfo[] }) {
  return (
    <div className="pixel-panel px-3 py-2.5">
      <p className="text-[10px] tracking-[2px] uppercase text-pixel-gray mb-1.5">{title}</p>
      <div className="space-y-1">
        {(rows || []).slice(0, 3).map((s) => (
          <Link
            key={s.netuid}
            href={`/subnets/${s.netuid}`}
            className="flex items-center gap-2 no-underline hover:bg-pixel-white/5 rounded-sm px-1 -mx-1 py-0.5"
          >
            <SubnetLogo netuid={s.netuid} name={s.name} symbol={s.symbol} logo={s.logo} size={18} />
            <span className="text-[11px] text-pixel-white truncate flex-1">{s.name}</span>
            <ChangeChip pct={s.change_24h} size="xs" bare />
          </Link>
        ))}
        {!rows?.length && <p className="text-[11px] text-pixel-gray font-mono">loading…</p>}
      </div>
    </div>
  );
}
