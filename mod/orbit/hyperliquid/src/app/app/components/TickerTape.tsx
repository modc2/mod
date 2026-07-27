"use client";

import { useEffect, useRef, useState } from "react";
import { fetchMids } from "../lib/api";

// Majors pinned to the front so the tape always opens with the coins people
// recognize; everything else joins in mids order up to CAP entries.
const MAJORS = ["BTC", "ETH", "SOL", "HYPE", "XRP", "DOGE", "SUI", "AVAX", "LINK", "TAO", "PUMP", "FARTCOIN"];
const CAP = 24;

// Core perps only — spot ("@…"), builder-dex ("dex:…") and prediction ("#…")
// keys aren't tape material.
const isCore = (k: string) => /^[A-Z0-9]+$/.test(k);

type Tick = { coin: string; px: number; dir: -1 | 0 | 1 };

function fmtPx(px: number): string {
  if (px >= 1000) return px.toLocaleString("en-US", { maximumFractionDigits: 0 });
  if (px >= 10) return px.toFixed(2);
  if (px >= 0.1) return px.toFixed(4);
  return px.toPrecision(3);
}

export default function TickerTape() {
  const [ticks, setTicks] = useState<Tick[]>([]);
  const prev = useRef<Map<string, number>>(new Map());

  useEffect(() => {
    let alive = true;
    const poll = async () => {
      try {
        const mids = await fetchMids();
        if (!alive) return;
        const entries = Object.entries(mids)
          .filter(([k]) => isCore(k))
          .map(([coin, v]) => [coin, parseFloat(v as string)] as const)
          .filter(([, px]) => Number.isFinite(px) && px > 0);
        const byCoin = new Map(entries);
        const ordered: Tick[] = [];
        const seen = new Set<string>();
        for (const m of MAJORS) {
          const px = byCoin.get(m);
          if (px != null) { ordered.push({ coin: m, px, dir: 0 }); seen.add(m); }
        }
        for (const [coin, px] of entries) {
          if (ordered.length >= CAP) break;
          if (!seen.has(coin)) { ordered.push({ coin, px, dir: 0 }); seen.add(coin); }
        }
        for (const t of ordered) {
          const was = prev.current.get(t.coin);
          t.dir = was == null || was === t.px ? 0 : t.px > was ? 1 : -1;
          prev.current.set(t.coin, t.px);
        }
        setTicks(ordered);
      } catch { /* tape is decorative — never surface errors */ }
    };
    poll();
    const id = setInterval(poll, 5000);
    return () => { alive = false; clearInterval(id); };
  }, []);

  if (ticks.length === 0) return null;

  const cell = (t: Tick, key: string) => (
    <span key={key} className="inline-flex items-baseline gap-1.5 px-4 whitespace-nowrap">
      <span className="text-[10px] font-medium uppercase tracking-wider text-dim">{t.coin}</span>
      <span
        className={`num text-[11px] transition-colors duration-700 ${
          t.dir > 0 ? "text-win" : t.dir < 0 ? "text-loss" : "text-muted"
        }`}
      >
        {fmtPx(t.px)}
      </span>
      {t.dir !== 0 && (
        <span className={`text-[8px] ${t.dir > 0 ? "text-win" : "text-loss"}`}>
          {t.dir > 0 ? "▲" : "▼"}
        </span>
      )}
    </span>
  );

  return (
    <div
      className="relative overflow-hidden border-b border-white/[0.05] bg-black/20 backdrop-blur-xl select-none"
      aria-hidden
    >
      {/* Edge fades so the tape dissolves instead of clipping. */}
      <div className="pointer-events-none absolute inset-y-0 left-0 w-16 z-10 bg-gradient-to-r from-bg to-transparent" />
      <div className="pointer-events-none absolute inset-y-0 right-0 w-16 z-10 bg-gradient-to-l from-bg to-transparent" />
      {/* Two copies back-to-back; the track slides -50% then loops = seamless. */}
      <div className="flex w-max animate-ticker hover:[animation-play-state:paused] py-1">
        {ticks.map((t) => cell(t, t.coin))}
        {ticks.map((t) => cell(t, `${t.coin}-b`))}
      </div>
    </div>
  );
}
