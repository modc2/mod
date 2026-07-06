"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { listSignals, Signal, ago, shortAddr, fmtUsd } from "../lib/api";
import { useWallet } from "../lib/wallet";

export default function SignalsPage() {
  const { address } = useWallet();
  const [sigs, setSigs] = useState<Signal[]>([]);
  const [loading, setLoading] = useState(false);
  const [mine, setMine] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await listSignals(mine && address ? address : undefined, 200);
      setSigs(r.signals);
    } finally { setLoading(false); }
  }, [mine, address]);

  useEffect(() => {
    load();
    const t = setInterval(load, 6000);
    return () => clearInterval(t);
  }, [load]);

  return (
    <div className="space-y-4">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-xl text-ink">copy signals</h1>
          <p className="text-xs text-muted mt-1">
            Live mirror of leader fills, scaled per follow. Sign &amp; submit on Hyperliquid to execute.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <label className="text-[11px] text-muted flex items-center gap-1">
            <input type="checkbox" checked={mine} onChange={(e) => setMine(e.target.checked)} />
            mine only
          </label>
          <button className="btn" onClick={load} disabled={loading}>refresh</button>
        </div>
      </div>

      {sigs.length === 0 ? (
        <div className="panel p-6 text-xs text-muted">
          waiting for leader fills… make sure a follow is configured.
          <Link href="/follows" className="ml-2 text-accent2">view follows →</Link>
        </div>
      ) : (
        <div className="panel">
          <div className="grid grid-cols-[1fr_1.4fr_1fr_0.8fr_1fr_1fr_1fr_1fr] gap-2 px-4 py-2 border-b border-border text-[10px] uppercase tracking-wider text-muted">
            <div>time</div><div>leader</div><div>coin</div><div>side</div>
            <div className="text-right">leader px</div>
            <div className="text-right">leader sz</div>
            <div className="text-right">copy sz</div>
            <div className="text-right">status</div>
          </div>
          {sigs.map((s) => (
            <div key={s.id} className="grid grid-cols-[1fr_1.4fr_1fr_0.8fr_1fr_1fr_1fr_1fr] gap-2 px-4 py-2 table-row text-xs">
              <div className="text-muted">{ago(s.ts_ms)}</div>
              <Link href={`/trader/${s.leader}`} className="text-accent2 hover:underline">
                {shortAddr(s.leader)}
              </Link>
              <div>{s.coin}</div>
              <div className={s.side === "B" ? "text-win" : "text-loss"}>{s.side === "B" ? "buy" : "sell"}</div>
              <div className="num text-right">{s.leader_px.toFixed(4)}</div>
              <div className="num text-right">{s.leader_sz}</div>
              <div className="num text-right text-accent">{s.copy_sz.toFixed(6)}</div>
              <div className="text-right">
                <span className={`pill ${
                  s.status === "executed" ? "border-win/40 text-win"
                  : s.status === "skipped" ? "border-muted/40 text-muted"
                  : "border-warn/40 text-warn"}`}>
                  {s.status}
                </span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
