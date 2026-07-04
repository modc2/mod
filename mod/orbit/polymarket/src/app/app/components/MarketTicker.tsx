"use client";

import { useRouter } from "next/navigation";
import { useLiveMarkets } from "../lib/useLiveMarkets";
import { PolymarketMarket } from "../lib/types";
import { useEmbedded } from "../lib/embedded";

/**
 * Slim live-price tape that sits above the top bar.
 *
 * - Streams the top markets' prices over the CLOB websocket (paused while the
 *   tab is hidden); the market list itself refreshes slowly over REST.
 * - Computes Δ vs the previous streamed price and colors the chip green/red.
 * - Marquee-scrolls horizontally; pauses on hover so users can click an entry.
 */
export default function MarketTicker() {
  const router = useRouter();
  const embedded = useEmbedded();
  const { markets, prevPrices, loading, error } = useLiveMarkets({
    limit: 24,
    order: "volume",
  });

  // Embedded panes skip the ticker — it eats vertical space and the user
  // already has it in the main pane.
  if (embedded) return null;

  if (loading && markets.length === 0) {
    return (
      <div className="h-7 border-b border-pixel-border bg-pixel-black/80 flex items-center px-4">
        <span className="text-[13px] tracking-[3px] text-pixel-gray uppercase">live · loading…</span>
      </div>
    );
  }
  if (error && markets.length === 0) {
    return null;
  }

  // Duplicate the list so the marquee loops seamlessly.
  const loop = [...markets, ...markets];

  return (
    <div
      className="h-7 border-b border-pixel-border bg-pixel-black/80 overflow-hidden relative group"
      role="region"
      aria-label="live market prices"
    >
      <div className="absolute inset-y-0 left-0 z-10 w-3 bg-gradient-to-r from-pixel-black to-transparent pointer-events-none" />
      <div className="absolute inset-y-0 right-0 z-10 w-3 bg-gradient-to-l from-pixel-black to-transparent pointer-events-none" />
      <div className="absolute left-0 top-0 z-10 h-full px-2 flex items-center bg-pixel-black border-r border-pixel-border">
        <span className="text-[13px] tracking-[3px] text-pixel-white uppercase font-mono">
          <span
            className="inline-block w-1.5 h-1.5 mr-1.5 rounded-full bg-green-400 align-middle"
            style={{ animation: "ticker-pulse 1.4s infinite" }}
          />
          live
        </span>
      </div>
      <div
        className="flex items-center gap-6 h-full whitespace-nowrap pl-16 will-change-transform group-hover:[animation-play-state:paused]"
        style={{ animation: "ticker-marquee 60s linear infinite" }}
      >
        {loop.map((m, idx) => (
          <TickerChip
            key={`${m.conditionId}-${idx}`}
            market={m}
            prevYes={prevPrices.get(m.conditionId)}
            onClick={() => m.slug && router.push(`/markets/${m.slug}`)}
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
  market,
  prevYes,
  onClick,
}: {
  market: PolymarketMarket;
  prevYes: number | undefined;
  onClick: () => void;
}) {
  const yes = market.outcomePrices?.[0] ?? 0;
  const yesPct = Math.round(yes * 100);
  const delta = prevYes != null ? Math.round((yes - prevYes) * 100) : 0;

  const dirColor =
    delta > 0 ? "#4ade80" :
    delta < 0 ? "#f87171" :
    "#888";

  const title = market.question.length > 48
    ? market.question.slice(0, 47) + "…"
    : market.question;

  return (
    <button
      onClick={onClick}
      className="flex items-center gap-2 cursor-pointer hover:bg-pixel-white/5 px-1 py-0.5 transition-colors"
      title={market.question}
    >
      <span className="text-[14px] text-pixel-gray-light truncate max-w-[280px] font-mono">{title}</span>
      <span className="text-[15px] font-mono font-bold" style={{ color: dirColor }}>{yesPct}¢</span>
      {delta !== 0 && (
        <span className="text-[13px] font-mono" style={{ color: dirColor }}>
          {delta > 0 ? "▲" : "▼"}{Math.abs(delta)}
        </span>
      )}
    </button>
  );
}
