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
      <div className="h-11 border-b-2 border-pixel-border bg-pixel-black flex items-center px-4">
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
      className="h-11 border-b-2 border-pixel-border bg-pixel-black overflow-hidden relative group"
      role="region"
      aria-label="live subnet alpha prices"
    >
      <div className="absolute inset-y-0 right-0 z-10 w-10 bg-gradient-to-l from-pixel-black to-transparent pointer-events-none" />
      <div className="absolute left-0 top-0 z-10 h-full w-[64px] sm:w-[100px] px-2 sm:px-3 flex items-center bg-pixel-black border-r-2 border-pixel-border">
        <span className="text-[13px] tracking-[2px] sm:tracking-[3px] text-pixel-white uppercase font-mono flex items-center">
          <span
            className="inline-block w-1.5 h-1.5 mr-1.5 sm:mr-2 bg-green-400"
            style={{ animation: "ticker-pulse 1.4s infinite" }}
          />
          dTAO
        </span>
      </div>
      <div
        className="flex items-center h-full whitespace-nowrap pl-[76px] sm:pl-[112px] will-change-transform group-hover:[animation-play-state:paused]"
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
  // Palette vars, not literal hexes — the tape was the last surface still
  // painting itself in stock Tailwind green/red, so it drifted off the
  // five-colour palette in light mode.
  const up = "var(--neon-lime)";
  const down = "var(--neon-red)";
  const flat = "var(--fg-dim)";
  const changeColor =
    change == null ? flat : change > 0.005 ? up : change < -0.005 ? down : flat;
  const priceColor = tick > 0 ? up : tick < 0 ? down : "var(--fg)";

  return (
    // Each entry is a cell on the tape, divided by a hard rule — with only
    // whitespace between them the chips ran together into one long word.
    <button
      onClick={onClick}
      className="flex items-center gap-1.5 sm:gap-2 h-full shrink-0 cursor-pointer px-2.5 sm:px-4 border-r border-pixel-border hover:bg-pixel-white/10 transition-colors"
      title={`${subnet.name} — ${fmtAlphaPrice(price, currency, usdPerTao)} per α`}
    >
      <span className="text-[13px] text-pixel-gray font-mono">SN{subnet.netuid}</span>
      {/* The subnet's name is the first thing to go on a phone: a tape you
          can read three chips of beats one you can read a chip and a half
          of, and the netuid already names the row. */}
      <span className="hidden sm:inline text-[10px] text-pixel-gray-light max-w-[120px] truncate">
        {subnet.name}
      </span>
      <span
        className="text-[15px] font-mono tabular-nums transition-colors duration-500"
        style={{ color: priceColor }}
      >
        {fmtAlphaPrice(price, currency, usdPerTao)}
      </span>
      {change != null && (
        <span className="text-[13px] font-mono tabular-nums" style={{ color: changeColor }}>
          {change > 0 ? "▲" : change < 0 ? "▼" : ""}
          {Math.abs(change).toFixed(2)}%
        </span>
      )}
    </button>
  );
}
