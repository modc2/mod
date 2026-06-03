"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { fetchSubnets } from "../lib/api";
import type { SubnetInfo } from "../lib/types";
import { useCurrency, fmtAlphaPrice } from "../context/CurrencyContext";

/**
 * Slim live alpha-price tape that sits above the top bar.
 *
 * - Polls all subnets every 12s (paused while tab is hidden).
 * - Computes Δ vs the previous poll and colors the chip green/red.
 * - Marquee-scrolls horizontally; pauses on hover.
 */
export default function SubnetTicker() {
  const router = useRouter();
  const [subnets, setSubnets] = useState<SubnetInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const prevPricesRef = useRef<Map<number, number>>(new Map());
  const [prevPrices, setPrevPrices] = useState<Map<number, number>>(new Map());

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setInterval>;

    async function poll() {
      try {
        const next = await fetchSubnets();
        if (!alive) return;
        // Capture the previous-poll prices before overwriting.
        const snap = new Map<number, number>();
        for (const s of subnets) snap.set(s.netuid, s.alpha_price_tao);
        setPrevPrices(snap);
        setSubnets(next);
      } catch {
        // soft-fail — keep showing the last good snapshot
      } finally {
        if (alive) setLoading(false);
      }
    }

    poll();
    timer = setInterval(() => {
      if (document.visibilityState === "visible") poll();
    }, 12_000);

    return () => {
      alive = false;
      clearInterval(timer);
    };
    // We intentionally depend on subnets so each poll captures the latest
    // snapshot as "previous" — the closure would otherwise stale.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [subnets.length]);

  if (loading && subnets.length === 0) {
    return (
      <div className="h-7 border-b border-pixel-border bg-pixel-black/80 flex items-center px-4">
        <span className="text-[13px] tracking-[3px] text-pixel-gray uppercase">
          live · loading…
        </span>
      </div>
    );
  }
  if (subnets.length === 0) return null;

  const loop = [...subnets, ...subnets];

  return (
    <div
      className="h-7 border-b border-pixel-border bg-pixel-black/80 overflow-hidden relative group"
      role="region"
      aria-label="live subnet alpha prices"
    >
      <div className="absolute inset-y-0 left-0 z-10 w-3 bg-gradient-to-r from-pixel-black to-transparent pointer-events-none" />
      <div className="absolute inset-y-0 right-0 z-10 w-3 bg-gradient-to-l from-pixel-black to-transparent pointer-events-none" />
      <div className="absolute left-0 top-0 z-10 h-full px-2 flex items-center bg-pixel-black border-r border-pixel-border">
        <span className="text-[13px] tracking-[3px] text-pixel-white uppercase font-mono">
          <span
            className="inline-block w-1.5 h-1.5 mr-1.5 bg-green-400 align-middle"
            style={{ animation: "ticker-pulse 1.4s infinite" }}
          />
          dTAO
        </span>
      </div>
      <div
        className="flex items-center gap-6 h-full whitespace-nowrap pl-16 will-change-transform group-hover:[animation-play-state:paused]"
        style={{ animation: "ticker-marquee 80s linear infinite" }}
      >
        {loop.map((s, idx) => (
          <TickerChip
            key={`${s.netuid}-${idx}`}
            subnet={s}
            prevPrice={prevPrices.get(s.netuid)}
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
          50%      { opacity: 0.35; }
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
  const delta = prevPrice != null && prevPrice > 0
    ? ((price - prevPrice) / prevPrice) * 100
    : 0;

  const dirColor =
    delta > 0.0001 ? "#4ade80" :
    delta < -0.0001 ? "#f87171" :
    "#888";

  const showPrice = fmtAlphaPrice(price, currency, usdPerTao);

  return (
    <button
      onClick={onClick}
      className="flex items-center gap-2 cursor-pointer hover:bg-pixel-white/5 px-1 py-0.5 transition-colors"
      title={`${subnet.name} — ${showPrice} per α`}
    >
      <span className="text-[12px] text-pixel-gray-light font-mono">
        SN{subnet.netuid}
      </span>
      <span className="text-[14px] font-mono font-bold" style={{ color: dirColor }}>
        {showPrice}
      </span>
      {Math.abs(delta) >= 0.01 && (
        <span className="text-[11px] font-mono" style={{ color: dirColor }}>
          {delta > 0 ? "▲" : "▼"}{Math.abs(delta).toFixed(2)}%
        </span>
      )}
    </button>
  );
}
