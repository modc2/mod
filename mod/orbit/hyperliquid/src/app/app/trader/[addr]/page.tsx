"use client";

import { useEffect, useState } from "react";
import { useParams, useSearchParams } from "next/navigation";
import Link from "next/link";
import { analyzeTrader, fmtPnl, fmtUsd, fmtPct, shortAddr, ago } from "../../lib/api";

export default function TraderPage() {
  const { addr } = useParams<{ addr: string }>();
  const sp = useSearchParams();
  const days = Number(sp.get("days") || 7);
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    analyzeTrader(addr, days)
      .then((d) => { if (alive) setData(d); })
      .catch((e) => { if (alive) setErr(e.message ?? String(e)); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [addr, days]);

  if (loading) return <div className="text-xs text-muted">loading {shortAddr(addr)}…</div>;
  if (err) return <div className="text-xs text-loss">{err}</div>;
  if (!data) return null;

  const s = data.summary;
  const fills = Array.isArray(data.fills) ? data.fills : [];
  const positions = (data.state?.assetPositions ?? []) as any[];

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <Link href="/" className="text-[11px] text-muted hover:text-ink">← traders</Link>
          <h1 className="text-xl text-ink mt-1">{shortAddr(addr)}</h1>
          <a href={`https://app.hyperliquid.xyz/explorer/address/${addr}`} target="_blank" rel="noreferrer"
            className="text-[11px] text-muted hover:text-accent2">view on hyperliquid →</a>
        </div>
        <div className="flex gap-2">
          <Link href={`/follows/new?leader=${addr}`} className="btn-primary">copy this trader</Link>
        </div>
      </div>

      {/* Summary tiles */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <Tile label={`pnl (${days}d)`} value={fmtPnl(s.pnl)} tone={s.pnl >= 0 ? "win" : "loss"} />
        <Tile label="volume" value={fmtUsd(s.volume)} />
        <Tile label="win rate" value={s.win_rate < 0 ? "—" : fmtPct(s.win_rate, 0)} />
        <Tile label="trades" value={`${s.trades}`} />
        <Tile label="sharpe" value={s.sharpe.toFixed(2)} />
      </div>

      {/* Open positions */}
      <Panel title="open positions">
        {positions.length === 0 ? (
          <Empty>no open positions</Empty>
        ) : (
          <div className="grid grid-cols-[1fr_1fr_1fr_1fr_1fr] gap-2 px-4 py-2 text-[10px] uppercase tracking-wider text-muted border-b border-border">
            <div>coin</div><div className="text-right">size</div>
            <div className="text-right">entry</div><div className="text-right">unrealized</div>
            <div className="text-right">leverage</div>
          </div>
        )}
        {positions.map((p, i) => {
          const pos = p.position ?? p;
          const sz = Number(pos.szi || 0);
          if (sz === 0) return null;
          return (
            <div key={i} className="grid grid-cols-[1fr_1fr_1fr_1fr_1fr] gap-2 px-4 py-2 table-row">
              <div className="text-ink">{pos.coin}</div>
              <div className={`num text-right ${sz >= 0 ? "text-win" : "text-loss"}`}>{sz}</div>
              <div className="num text-right">{Number(pos.entryPx || 0).toFixed(4)}</div>
              <div className={`num text-right ${Number(pos.unrealizedPnl || 0) >= 0 ? "text-win" : "text-loss"}`}>
                {fmtPnl(Number(pos.unrealizedPnl || 0))}
              </div>
              <div className="num text-right">{pos.leverage?.value ?? "—"}x</div>
            </div>
          );
        })}
      </Panel>

      {/* Recent fills */}
      <Panel title={`recent fills (${days}d)`}>
        {fills.length === 0 ? (
          <Empty>no fills in window</Empty>
        ) : (
          <div className="grid grid-cols-[1.4fr_1fr_1fr_1fr_1fr_1.4fr] gap-2 px-4 py-2 text-[10px] uppercase tracking-wider text-muted border-b border-border">
            <div>time</div><div>coin</div><div className="text-right">side</div>
            <div className="text-right">px</div><div className="text-right">sz</div>
            <div className="text-right">closedPnl</div>
          </div>
        )}
        {fills.slice(0, 100).map((f: any, i: number) => (
          <div key={i} className="grid grid-cols-[1.4fr_1fr_1fr_1fr_1fr_1.4fr] gap-2 px-4 py-1.5 table-row">
            <div className="text-[11px] text-muted">{ago(Number(f.time))}</div>
            <div>{f.coin}</div>
            <div className={`text-right ${f.side === "B" ? "text-win" : "text-loss"}`}>{f.side === "B" ? "buy" : "sell"}</div>
            <div className="num text-right">{Number(f.px).toFixed(4)}</div>
            <div className="num text-right">{f.sz}</div>
            <div className={`num text-right ${Number(f.closedPnl) >= 0 ? "text-win" : "text-loss"}`}>
              {Number(f.closedPnl) === 0 ? "—" : fmtPnl(Number(f.closedPnl))}
            </div>
          </div>
        ))}
      </Panel>
    </div>
  );
}

function Tile({ label, value, tone }: { label: string; value: string; tone?: "win" | "loss" }) {
  return (
    <div className="panel p-3">
      <div className="stat">{label}</div>
      <div className={`text-lg num mt-1 ${tone === "win" ? "text-win" : tone === "loss" ? "text-loss" : ""}`}>
        {value}
      </div>
    </div>
  );
}

function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="panel">
      <div className="px-4 py-2 border-b border-border text-[11px] uppercase tracking-wider text-muted">{title}</div>
      {children}
    </div>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return <div className="px-4 py-6 text-xs text-muted">{children}</div>;
}
