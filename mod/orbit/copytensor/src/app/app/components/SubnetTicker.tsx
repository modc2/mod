"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { fetchSubnets } from "../lib/api";
import type { SubnetInfo } from "../lib/types";
import { useCurrency, fmtAlphaPrice } from "../context/CurrencyContext";

/**
 * Live alpha tape above the top bar.
 *
 * The % on each chip is the indexed 24h change (same number the cards and
 * the market strip show), not a diff against the last poll — a tape that
 * re-derives its own deltas disagreed with every other surface. Tick-to-tick
 * movement still shows up as a brief green/red flash on the price.
 */
const POLL_MS = 15_000;
const MAX_CHIPS = 40;

export default function SubnetTicker() {
  const router = useRouter();
  const [subnets, setSubnets] = useState<SubnetInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const prevRef = useRef<Map<number, number>>(new Map());

  useEffect(() => {
    let alive = true;

    async function poll() {
      try {
        const next = await fetchSubnets();
        if (!alive) return;
        setSubnets((cur) => {
          prevRef.current = new Map(cur.map((s) => [s.netuid, s.alpha_price_tao]));
          return next;
        });
      } catch {
        // soft-fail — keep showing the last good snapshot
      } finally {
        if (alive) setLoading(false);
      }
    }

    poll();
    const timer = setInterval(() => {
      if (document.visibilityState === "visible") poll();
    }, POLL_MS);

    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  if (loading && subnets.length === 0) {
    return (
      <div className="h-8 border-b border-pixel-border bg-pixel-black/80 flex items-center px-4">
        <span className="text-[11px] tracking-[3px] text-pixel-gray uppercase">live · loading…</span>
      </div>
    );
  }
  if (subnets.length === 0) return null;

  // Biggest pools first, capped — 128 chips made one marquee lap take a
  // minute and a half.
  const shown = [...subnets]
    .sort((a, b) => (b.market_cap_tao ?? b.total_stake_tao) - (a.market_cap_tao ?? a.total_stake_tao))
    .slice(0, MAX_CHIPS);
  const loop = [...shown, ...shown];

  return (
    <div
      className="h-8 border-b border-pixel-border bg-pixel-black/80 overflow-hidden relative group"
      role="region"
      aria-label="live subnet alpha prices"
    >
      <div className="absolute inset-y-0 right-0 z-10 w-8 bg-gradient-to-l from-pixel-black to-transparent pointer-events-none" />
      <div className="absolute left-0 top-0 z-10 h-full px-3 flex items-center bg-pixel-black border-r border-pixel-border">
        <span className="text-[11px] tracking-[3px] text-pixel-white uppercase font-mono flex items-center">
          <span
            className="inline-block w-1.5 h-1.5 mr-2 rounded-full bg-green-400"
            style={{ animation: "ticker-pulse 1.4s infinite" }}
          />
          dTAO
        </span>
      </div>
      <div
        className="flex items-center gap-5 h-full whitespace-nowrap pl-24 will-change-transform group-hover:[animation-play-state:paused]"
        style={{ animation: "ticker-marquee 90s linear infinite" }}
      >
        {loop.map((s, idx) => (
          <TickerChip
            key={`${s.netuid}-${idx}`}
            subnet={s}
            prevPrice={prevRef.current.get(s.netuid)}
            onClick={() => router.push(`/subnets/${s.netuid}`)}
          />
        ))}
      </div>

      <style jsx>{`
        @keyframes ticker-marquee {
          from { transform: translateX(0); }
          to   { transform: translateX(-50%); }
        }
        @keyframes ticker-pulse {
          0%, 100% { opacity: 1; }
          50%      { opacity: 0.3; }
        }
      `}</style>
    </div>
  );
}

function TickerChip({
  subnet,
  prevPrice,
  onClick,
}: {
  subnet: SubnetInfo;
  prevPrice: number | undefined;
  onClick: () => void;
}) {
  const { currency, usdPerTao } = useCurrency();
  const price = subnet.alpha_price_tao;
  const change = subnet.change_24h;

  const tick =
    prevPrice == null || prevPrice === price ? 0 : price > prevPrice ? 1 : -1;
  const changeColor =
    change == null ? "#888" : change > 0.005 ? "#4ade80" : change < -0.005 ? "#f87171" : "#888";
  const priceColor = tick > 0 ? "#4ade80" : tick < 0 ? "#f87171" : "var(--fg)";

  return (
    <button
      onClick={onClick}
      className="flex items-center gap-2 cursor-pointer hover:bg-pixel-white/5 px-1.5 py-0.5 rounded-sm transition-colors"
      title={`${subnet.name} — ${fmtAlphaPrice(price, currency, usdPerTao)} per α`}
    >
      <span className="text-[11px] text-pixel-gray font-mono">SN{subnet.netuid}</span>
      <span className="text-[11px] text-pixel-gray-light max-w-[92px] truncate">{subnet.name}</span>
      <span
        className="text-[12px] font-mono font-bold tabular-nums transition-colors duration-500"
        style={{ color: priceColor }}
      >
        {fmtAlphaPrice(price, currency, usdPerTao)}
      </span>
      {change != null && (
        <span className="text-[11px] font-mono tabular-nums" style={{ color: changeColor }}>
          {change > 0 ? "▲" : change < 0 ? "▼" : ""}
          {Math.abs(change).toFixed(2)}%
        </span>
      )}
    </button>
  );
}
