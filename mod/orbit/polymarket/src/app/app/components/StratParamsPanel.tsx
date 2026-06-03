"use client";

// Read-only view of the active strat's params + watchlist for the LIVE
// page. Same info that lives in the STRATS tab, surfaced here so the
// user can sanity-check what's actually about to fire without leaving
// the LIVE view. To EDIT params or weights, kick back to STRATS — keeps
// the LIVE tab focused on monitoring vs. configuring.

import { useEffect, useState } from "react";
import { loadIndexes, getActiveIndexId } from "../lib/indexStore";
import type { SavedIndex } from "../lib/types";

function shortAddr(a: string): string {
  return a.length > 10 ? `${a.slice(0, 6)}…${a.slice(-4)}` : a;
}

export default function StratParamsPanel({ tick }: { tick?: number }) {
  const [strat, setStrat] = useState<SavedIndex | null>(null);

  useEffect(() => {
    const id = getActiveIndexId();
    const all = loadIndexes();
    setStrat(all.find((s) => s.id === id) ?? null);
  }, [tick]);

  if (!strat) {
    return (
      <div className="pixel-panel border-2 border-pixel-border p-3 text-xs text-pixel-muted">
        No active strat. Open STRATS to pick or create one.
      </div>
    );
  }

  const enabledTraders = strat.traders.filter((t) => t.enabled !== false);
  const totalWeight = enabledTraders.reduce((s, t) => s + (t.weight ?? 0), 0);

  return (
    <div className="pixel-panel border-2 border-pixel-border p-3 space-y-3">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-xs uppercase tracking-wide text-pixel-muted">
          Strat Params
        </span>
        <span className="text-sm font-mono">{strat.name}</span>
        <span className="text-[10px] text-pixel-muted">
          · {strat.traders.length} traders ({enabledTraders.length} enabled)
        </span>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-3 gap-1.5 text-xs">
        <Kv label="capital" v={`$${(strat.capital ?? 1000).toLocaleString()}`} />
        <Kv label="min trade" v={`$${(strat.minTrade ?? 1).toFixed(2)}`} />
        <Kv label="max trade" v={`$${(strat.maxTrade ?? 100).toFixed(2)}`} />
        <Kv label="live poll" v={`${strat.livePollMinutes ?? 1}min`} />
        <Kv label="backtest days" v={`${strat.backtestDays ?? 3}d`} />
        <Kv label="max/hour" v={String(strat.maxTradesPerHour ?? 10)} />
      </div>

      <div className="space-y-1 text-xs">
        <div className="text-pixel-muted uppercase tracking-wide text-[10px]">
          Traders &amp; weights
        </div>
        <div className="space-y-1">
          {strat.traders.length === 0 ? (
            <div className="text-pixel-muted text-[11px]">No traders yet.</div>
          ) : (
            strat.traders.map((t) => {
              const enabled = t.enabled !== false;
              const pct = totalWeight > 0 ? ((t.weight ?? 0) / totalWeight) * 100 : 0;
              return (
                <div
                  key={t.address}
                  className={`flex items-center gap-2 border border-pixel-border/40 rounded px-2 py-1 ${
                    enabled ? "" : "opacity-40"
                  }`}
                >
                  <span
                    className={`inline-block w-1.5 h-1.5 rounded-full ${
                      enabled ? "bg-green-400" : "bg-pixel-muted"
                    }`}
                  />
                  <span className="font-mono truncate flex-1" title={t.address}>
                    {shortAddr(t.address)}
                  </span>
                  <span className="font-mono text-pixel-muted">
                    w={Number(t.weight ?? 0).toFixed(2)}
                  </span>
                  <span className="font-mono w-12 text-right">
                    {pct.toFixed(0)}%
                  </span>
                </div>
              );
            })
          )}
        </div>
      </div>

      <div className="text-[10px] text-pixel-muted">
        Edit in the STRATS tab — this panel is read-only.
      </div>
    </div>
  );
}

function Kv({ label, v }: { label: string; v: string }) {
  return (
    <div className="flex items-baseline justify-between gap-2 border border-pixel-border/40 rounded px-2 py-1">
      <span className="text-[10px] uppercase tracking-wide text-pixel-muted">
        {label}
      </span>
      <span className="font-mono">{v}</span>
    </div>
  );
}
