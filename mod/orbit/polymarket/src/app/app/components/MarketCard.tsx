"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { PolymarketMarket } from "../lib/types";
import { formatVolume } from "../lib/polymarket";

interface Props {
  market: PolymarketMarket;
  onSelect?: (market: PolymarketMarket) => void;
  selected?: boolean;
}

type Flash = "" | "up" | "down";

export default function MarketCard({ market, onSelect, selected }: Props) {
  const router = useRouter();
  const [hoveredOutcome, setHoveredOutcome] = useState<number | null>(null);

  const outcomes = market.outcomes || ["Yes", "No"];
  const prices = market.outcomePrices || [0.5, 0.5];
  const yesPrice = prices[0] || 0;
  const noPrice = prices[1] || 1 - yesPrice;
  const yesPct = Math.round(yesPrice * 100);
  const noPct = Math.round(noPrice * 100);

  // Detect price changes between renders and briefly flash the price chip.
  const prevYesRef = useRef<number>(yesPrice);
  const [flash, setFlash] = useState<Flash>("");
  useEffect(() => {
    const prev = prevYesRef.current;
    if (yesPrice !== prev) {
      const dir: Flash = yesPrice > prev ? "up" : "down";
      setFlash(dir);
      prevYesRef.current = yesPrice;
      const t = setTimeout(() => setFlash(""), 700);
      return () => clearTimeout(t);
    }
  }, [yesPrice]);

  const flashShadow =
    flash === "up"   ? "0 0 0 1px rgba(var(--accent) / 0.6), 0 0 14px rgba(var(--accent) / 0.45)" :
    flash === "down" ? "0 0 0 1px rgba(var(--danger) / 0.6), 0 0 14px rgba(var(--danger) / 0.45)" :
    "none";

  const endDate = market.endDate
    ? new Date(market.endDate).toLocaleDateString([], { month: "short", day: "numeric" })
    : "---";

  const handleClick = () => {
    if (onSelect) {
      onSelect(market);
    } else if (market.slug) {
      router.push(`/markets/${market.slug}`);
    }
  };

  const handleOutcomeClick = (e: React.MouseEvent, idx: number) => {
    e.stopPropagation();
    // Navigate to market detail with the selected outcome pre-highlighted
    if (market.slug) {
      router.push(`/markets/${market.slug}?side=${outcomes[idx]?.toLowerCase() || "yes"}`);
    }
  };

  const isHighConviction = yesPct >= 80 || yesPct <= 20;
  const isBinary = outcomes.length === 2;

  return (
    <div
      onClick={handleClick}
      className={`market-card group cursor-pointer flex flex-col ${
        selected ? "market-card-selected" : ""
      }`}
    >
      {/* Top: category + end date */}
      <div className="flex items-center justify-between gap-2 mb-2.5">
        {/* CSS truncation, not slice(0, 14) \u2014 the hard cut chopped mid-word
            with no ellipsis ("DRAW (LDU QUIT", "REPUBLICAN PAR") and read as
            corrupt data rather than as an abbreviated label. */}
        <span
          className="min-w-0 truncate text-[11px] tracking-[0.16em] text-pixel-gray uppercase font-semibold"
          title={market.category || undefined}
        >
          {market.category || "\u00A0"}
        </span>
        <span className="shrink-0 text-[12px] text-pixel-gray font-mono">{endDate}</span>
      </div>

      {/* Question — grouped tight against its price bar; the flex spacer under
          the outcome cluster pins the stats footer to the card's bottom edge.
          The 3-line reserve is what makes the grid read as a grid: cards in a
          row stretch to the tallest question, so without it a 1-line card's
          price bar floated ~45px above its neighbours' and every row landed
          on a different baseline. */}
      <div className="min-h-[68px] text-[15.5px] font-semibold tracking-[-0.01em] text-pixel-white leading-[1.45] mb-3.5 line-clamp-3">
        {market.question}
      </div>

      {/* Outcome buttons — clickable options */}
      {isBinary ? (
        <div className="space-y-1.5">
          {/* YES / NO probability bar */}
          <div
            className="relative h-[38px] w-full bg-[var(--input-bg)] border border-[var(--border-strong)] rounded-[var(--radius)] overflow-hidden"
            style={{ boxShadow: flashShadow, transition: "box-shadow 0.7s ease-out" }}
          >
            <div
              className="absolute inset-y-0 left-0 transition-all duration-500"
              style={{
                width: `${yesPct}%`,
                background: "linear-gradient(90deg, rgba(var(--accent) / 0.3) 0%, rgba(var(--accent) / 0.1) 100%)",
              }}
            />
            <div
              className="absolute inset-y-0 right-0 transition-all duration-500"
              style={{
                width: `${noPct}%`,
                background: "linear-gradient(270deg, rgba(var(--danger) / 0.15) 0%, transparent 100%)",
              }}
            />
            <div className="absolute inset-0 flex items-center justify-between px-3">
              <div className="flex items-baseline gap-1.5">
                <span className="text-[11px] font-mono font-semibold tracking-[0.1em] text-green-400">YES</span>
                <span className={`text-[16px] font-mono font-semibold ${yesPct >= 50 ? "text-green-400" : "text-pixel-gray"}`}>
                  {yesPct}¢
                </span>
              </div>
              <div className="flex items-baseline gap-1.5">
                <span className={`text-[16px] font-mono font-semibold ${noPct >= 50 ? "text-red-400" : "text-pixel-gray"}`}>
                  {noPct}¢
                </span>
                <span className="text-[11px] font-mono font-semibold tracking-[0.1em] text-red-400">NO</span>
              </div>
            </div>
          </div>

          {/* Quick-buy buttons — visible on hover */}
          <div className="flex gap-1.5 opacity-0 group-hover:opacity-100 transition-opacity duration-200">
            <button
              onClick={(e) => handleOutcomeClick(e, 0)}
              onMouseEnter={() => setHoveredOutcome(0)}
              onMouseLeave={() => setHoveredOutcome(null)}
              className={`flex-1 py-1.5 text-[13px] font-mono border transition-all ${
                hoveredOutcome === 0
                  ? "border-green-400 text-green-400 bg-green-400/10"
                  : "border-[var(--border-strong)] text-pixel-gray hover:border-green-400 hover:text-green-400"
              }`}
            >
              BUY YES {yesPct}¢
            </button>
            <button
              onClick={(e) => handleOutcomeClick(e, 1)}
              onMouseEnter={() => setHoveredOutcome(1)}
              onMouseLeave={() => setHoveredOutcome(null)}
              className={`flex-1 py-1.5 text-[13px] font-mono border transition-all ${
                hoveredOutcome === 1
                  ? "border-red-400 text-red-400 bg-red-400/10"
                  : "border-[var(--border-strong)] text-pixel-gray hover:border-red-400 hover:text-red-400"
              }`}
            >
              BUY NO {noPct}¢
            </button>
          </div>

          {/* Conviction indicator — the row is always laid out (invisible when
              the market is mid-book) so its presence doesn't shift the footer
              of one card relative to the card beside it. */}
          <div className={`flex items-center gap-1.5 pt-0.5 h-[17px] ${isHighConviction ? "" : "invisible"}`}>
            <div className={`w-1.5 h-1.5 rounded-full ${yesPct >= 80 ? "bg-green-400" : "bg-red-400"}`} />
            <span className={`text-[10.5px] font-semibold tracking-[0.14em] ${yesPct >= 80 ? "text-green-400" : "text-red-400"}`}>
              {yesPct >= 80 ? "HIGH YES" : "HIGH NO"}
            </span>
          </div>
        </div>
      ) : (
        /* Multi-outcome: list each option with price */
        <div className="space-y-1">
          {outcomes.map((outcome, i) => {
            const px = prices[i] ?? 0;
            const pct = Math.round(px * 100);
            return (
              <button
                key={outcome + i}
                onClick={(e) => handleOutcomeClick(e, i)}
                onMouseEnter={() => setHoveredOutcome(i)}
                onMouseLeave={() => setHoveredOutcome(null)}
                className={`w-full flex items-center justify-between px-3 py-1.5 text-[13px] font-mono border transition-all ${
                  hoveredOutcome === i
                    ? "border-pixel-white text-pixel-white bg-pixel-white/10"
                    : "border-[var(--border)] text-pixel-gray hover:border-pixel-white/40 hover:text-pixel-white"
                }`}
              >
                <span className="truncate mr-2">{outcome}</span>
                <span className="shrink-0">{pct}¢</span>
              </button>
            );
          })}
        </div>
      )}

      {/* Spacer — soaks up leftover height so short cards keep their footer
          pinned to the bottom edge like tall ones. */}
      <div className="flex-1" />

      {/* Bottom stats */}
      <div className="flex items-center justify-between pt-3 mt-3 border-t border-[var(--border)]">
        <div className="flex items-center gap-4">
          <div className="flex items-baseline gap-1.5">
            <span className="text-[10.5px] text-pixel-gray font-semibold tracking-[0.14em]">VOL</span>
            <span className="text-[13px] text-pixel-white font-mono">{formatVolume(market.volume)}</span>
          </div>
          <div className="flex items-baseline gap-1.5">
            <span className="text-[10.5px] text-pixel-gray font-semibold tracking-[0.14em]">LIQ</span>
            <span className="text-[13px] text-pixel-gray-light font-mono">{formatVolume(market.liquidity)}</span>
          </div>
        </div>
        {market.image && (
          <div className="w-6 h-6 rounded-[6px] border border-[var(--border-strong)] overflow-hidden opacity-50 group-hover:opacity-100 transition-opacity">
            <img src={market.image} alt="" className="w-full h-full object-cover" style={{ imageRendering: "auto" }} />
          </div>
        )}
      </div>
    </div>
  );
}
