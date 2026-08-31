"use client";

import { useEffect, useState } from "react";
import { useParams, useSearchParams } from "next/navigation";
import Link from "next/link";
import { analyzeTrader, fmtPnl, fmtUsd, fmtPct, shortAddr, ago } from "../../lib/api";
import { buildRoundTrips, fmtDuration } from "../../lib/trips";
import PnlChart from "../../components/PnlChart";

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
  const trips = buildRoundTrips(fills);
  const closedTripPnl = trips
    .filter((t) => t.closeTime != null)
    .reduce((a, t) => a + t.closedPnl, 0);
  const fillsDesc = [...fills].sort((a: any, b: any) => Number(b.time) - Number(a.time));
  const fillsClosedPnl = fills.reduce((a: number, f: any) => a + (Number(f.closedPnl) || 0), 0);

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <Link href="/" className="text-[11px] text-muted hover:text-ink">← traders</Link>
          <h1 className="text-gradient text-[24px] font-bold tracking-tight leading-tight mt-1">{shortAddr(addr)}</h1>
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

      {/* PnL over time */}
      <Panel title={`pnl over time (${days}d)`}>
        <PnlChart portfolio={data.pnl_history} days={days} />
      </Panel>

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

      {/* Round trips: entry → exit */}
      <Panel
        title={`trades · entry → exit (${days}d)`}
        right={trips.length > 0 ? (
          <span className={`num ${closedTripPnl >= 0 ? "text-win" : "text-loss"}`}>
            closed pnl {fmtPnl(closedTripPnl)}
          </span>
        ) : undefined}
      >
        {trips.length === 0 ? (
          <Empty>no trades in window</Empty>
        ) : (
          <div className="grid grid-cols-[1fr_0.6fr_0.9fr_1fr_1fr_1fr_0.9fr_1.1fr] gap-2 px-4 py-2 text-[10px] uppercase tracking-wider text-muted border-b border-border">
            <div>coin</div><div>side</div><div className="text-right">size</div>
            <div className="text-right">entry px</div><div className="text-right">exit px</div>
            <div className="text-right">closed pnl</div><div className="text-right">held</div>
            <div className="text-right">closed</div>
          </div>
        )}
        {trips.slice(0, 60).map((t, i) => {
          const open = t.closeTime == null;
          return (
            <div key={i} className="grid grid-cols-[1fr_0.6fr_0.9fr_1fr_1fr_1fr_0.9fr_1.1fr] gap-2 px-4 py-1.5 table-row">
              <div className="text-ink">{t.coin}</div>
              <div className={t.long ? "text-win" : "text-loss"}>{t.long ? "long" : "short"}</div>
              <div className="num text-right">{t.peakSize}</div>
              <div className="num text-right">
                {t.entrySz > 0 ? `${t.partialEntry ? "~" : ""}${t.entryPx.toFixed(4)}` : "—"}
              </div>
              <div className="num text-right">{t.exitSz > 0 ? t.exitPx.toFixed(4) : "—"}</div>
              <div className={`num text-right ${t.closedPnl >= 0 ? "text-win" : "text-loss"}`}>
                {open && t.closedPnl === 0 ? "—" : fmtPnl(t.closedPnl)}
              </div>
              <div className="num text-right text-[11px] text-muted">
                {fmtDuration((t.closeTime ?? Date.now()) - t.openTime)}
              </div>
              <div className="text-right text-[11px]">
                {open
                  ? <span className="text-accent2">open</span>
                  : <span className="text-muted">{ago(t.closeTime!)}</span>}
              </div>
            </div>
          );
        })}
        {trips.some((t) => t.partialEntry) && (
          <div className="px-4 py-2 text-[10px] text-muted border-t border-border">
            ~ entry opened before the {days}d window — avg price covers in-window fills only
          </div>
        )}
      </Panel>

      {/* Recent fills */}
      <Panel
        title={`recent fills (${days}d)`}
        right={fills.length > 0 ? (
          <span className={`num ${fillsClosedPnl >= 0 ? "text-win" : "text-loss"}`}>
            Σ closed pnl {fmtPnl(fillsClosedPnl)}
          </span>
        ) : undefined}
      >
        {fills.length === 0 ? (
          <Empty>no fills in window</Empty>
        ) : (
          <div className="grid grid-cols-[1.4fr_1fr_1fr_1fr_1fr_1.4fr] gap-2 px-4 py-2 text-[10px] uppercase tracking-wider text-muted border-b border-border">
            <div>time</div><div>coin</div><div className="text-right">side</div>
            <div className="text-right">px</div><div className="text-right">sz</div>
            <div className="text-right">closedPnl</div>
          </div>
        )}
        {fillsDesc.slice(0, 100).map((f: any, i: number) => (
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

function Panel({ title, right, children }: { title: string; right?: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="panel">
      <div className="px-4 py-2 border-b border-border text-[11px] uppercase tracking-wider text-muted flex items-center justify-between">
        <span>{title}</span>
        {right}
      </div>
      {children}
    </div>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return <div className="px-4 py-6 text-xs text-muted">{children}</div>;
}
