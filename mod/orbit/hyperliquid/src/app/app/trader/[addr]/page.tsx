"use client";

import { useEffect, useState } from "react";
import { useParams, useSearchParams } from "next/navigation";
import Link from "next/link";
import {
  analyzeTrader, fmtPnl, fmtUsd, fmtPct, shortAddr, ago,
  sharpeMeasured, MIN_SHARPE_DAYS,
} from "../../lib/api";
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
  const ext = data.extended;
  const fills = Array.isArray(data.fills) ? data.fills : [];
  const positions = (data.state?.assetPositions ?? []) as any[];

  // The fills payload is ~31 days regardless of the window, because the client
  // caches that much per address. Everything below is labelled "(Nd)", so it
  // has to be scoped to N days or the tables quietly contradict the tiles.
  const cutoff = Number(data.cutoff_ms ?? 0);
  const inWindow = (t: number) => Number(t) >= cutoff;

  // Trips are built from ALL fills and filtered afterwards, never before: a
  // position opened before the window still needs its earlier legs to compute a
  // real entry price. Filtering first would silently reprice every open trade.
  const trips = buildRoundTrips(fills).filter(
    (t) => t.closeTime == null || inWindow(t.closeTime)
  );
  const closedTripPnl = trips
    .filter((t) => t.closeTime != null)
    .reduce((a, t) => a + t.closedPnl, 0);

  const windowFills = fills.filter((f: any) => inWindow(f.time));
  const fillsDesc = [...windowFills].sort((a: any, b: any) => Number(b.time) - Number(a.time));
  const fillsClosedPnl = windowFills.reduce((a: number, f: any) => a + (Number(f.closedPnl) || 0), 0);

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

      {/* Summary tiles.
          Every ratio here carries the count it was computed from. A win rate
          without its denominator is how "100% · 16 trades" ended up meaning
          "8 of 8 closes, and the other 8 fills were opens". */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <Tile
          label={`pnl (${days}d)`}
          value={fmtPnl(s.pnl)}
          tone={s.pnl >= 0 ? "win" : "loss"}
          sub={s.fees > 0 ? `after ${fmtUsd(s.fees)} fees` : undefined}
        />
        <Tile label="volume" value={fmtUsd(s.volume)} sub={`${s.trades} fills`} />
        <Tile
          label="win rate"
          value={s.win_rate < 0 ? "—" : fmtPct(s.win_rate, 0)}
          // Below MIN_CLOSES the percent is an anecdote. Say so on the tile
          // rather than trusting anyone to notice the denominator.
          tone={s.win_rate >= 0 && s.confidence !== "measured" ? "warn" : undefined}
          sub={
            s.win_rate < 0
              ? "nothing closed yet"
              : `${s.wins} of ${s.closes} closes` +
                (s.confidence === "measured" ? "" : " · thin sample")
          }
          title={
            s.win_rate < 0
              ? "No fill in this window realised PnL, so there is no ratio to take."
              : `${s.wins} win / ${s.losses} loss over ${s.closes} closes, net of fees. ` +
                `The other ${s.trades - s.closes} fills were opens, which can neither win nor lose. ` +
                `At this sample size the defensible rate is ${fmtPct(s.win_rate_lo, 0)} (Wilson 95% lower bound).`
          }
        />
        <Tile
          label="closes"
          value={`${s.closes}`}
          sub={`of ${s.trades} fills`}
          title="Fills that realised PnL — the denominator the win rate is actually over. The rest are opens."
        />
        <Tile
          label="sharpe"
          // A ratio built from two days of history is not a Sharpe ratio, and
          // printing it to two decimals only makes it more convincing.
          value={sharpeMeasured(s) ? s.sharpe.toFixed(2) : "—"}
          sub={`${s.sharpe_days} ${s.sharpe_days === 1 ? "day" : "days"} of history`}
          tone={sharpeMeasured(s) ? undefined : "warn"}
          title={
            sharpeMeasured(s)
              ? "Annualised Sharpe of daily net PnL. Idle days inside the window count as 0-return days."
              : `Needs at least ${MIN_SHARPE_DAYS} days of history; this wallet has ${s.sharpe_days}. ` +
                `Over so few days the standard deviation describes the sample, not the strategy.`
          }
        />
      </div>

      {/* The longer view, free: the fills call already caches ~31 days, so the
          wider window costs no extra request. It is also the only thing on this
          page that can contradict the tiles above — which is exactly why it is
          here and not behind a toggle. */}
      {ext && ext.trades > s.trades && <WiderView days={days} s={s} ext={ext} />}

      {/* PnL over time.
          `windowPnl` is handed down so the chart can reconcile its endpoint
          against the tile above rather than quietly disagreeing with it: the
          curve is equity marked to market, the tile is realised fills, and
          those are different numbers on any wallet holding risk overnight. */}
      <Panel title={`pnl over time (${days}d)`}>
        <PnlChart portfolio={data.pnl_history} days={days} windowPnl={s.pnl} />
      </Panel>

      {/* Open positions — perps only. `clearinghouseState` does not carry spot
          balances, so "none" here means no perp exposure, not a flat account. */}
      <Panel title="open positions · perps">
        {positions.length === 0 ? (
          <Empty>no open perp positions — spot balances are not in this table</Empty>
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
        right={windowFills.length > 0 ? (
          <span className={`num ${fillsClosedPnl >= 0 ? "text-win" : "text-loss"}`}>
            Σ closed pnl {fmtPnl(fillsClosedPnl)}
          </span>
        ) : undefined}
      >
        {windowFills.length === 0 ? (
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

/**
 * A stat and the evidence under it.
 *
 * `sub` is not decoration. A ratio shown alone invites the reader to supply a
 * denominator from whatever number is nearest, and on this page the nearest
 * number was the fill count — twice the real one. The sub-line removes the
 * guess.
 */
function Tile({ label, value, tone, sub, title }: {
  label: string;
  value: string;
  tone?: "win" | "loss" | "warn";
  sub?: string;
  title?: string;
}) {
  const toneClass =
    tone === "win" ? "text-win" : tone === "loss" ? "text-loss" : tone === "warn" ? "text-warn" : "";
  return (
    <div className="panel p-3" title={title}>
      <div className="stat">{label}</div>
      <div className={`text-lg num mt-1 ${toneClass}`}>{value}</div>
      {sub && (
        <div className={`text-[10px] mt-0.5 num ${tone === "warn" ? "text-warn/70" : "text-muted"}`}>
          {sub}
        </div>
      )}
    </div>
  );
}

/**
 * The same wallet over every day of history on hand, next to the window the
 * tiles describe.
 *
 * This exists because a short window is the easiest way to launder a losing
 * book into a perfect one: crop to the last few days and any trader who has
 * not lost recently reads as flawless. The fills request already returns ~31
 * days regardless of the window, so showing the longer view costs nothing and
 * is the single most useful number on the page for someone deciding whether to
 * mirror this wallet with real money.
 */
function WiderView({ days, s, ext }: { days: number; s: any; ext: any }) {
  const rows: { label: string; win: string; wide: string; flip: boolean }[] = [];

  const winOf = (x: any) => (x.win_rate < 0 ? "—" : fmtPct(x.win_rate, 0));
  rows.push({
    label: "win rate",
    win: `${winOf(s)}  (${s.wins}/${s.closes})`,
    wide: `${winOf(ext)}  (${ext.wins}/${ext.closes})`,
    // Flag it when the short window flatters the wallet by 10 points or more.
    flip: s.win_rate >= 0 && ext.win_rate >= 0 && s.win_rate - ext.win_rate >= 10,
  });
  rows.push({
    label: "net pnl",
    win: fmtPnl(s.pnl),
    wide: fmtPnl(ext.pnl),
    flip: s.pnl >= 0 && ext.pnl < 0,
  });
  rows.push({
    label: "sharpe",
    win: sharpeMeasured(s) ? s.sharpe.toFixed(2) : `— (${s.sharpe_days}d)`,
    wide: sharpeMeasured(ext) ? ext.sharpe.toFixed(2) : `— (${ext.sharpe_days}d)`,
    flip: sharpeMeasured(ext) && ext.sharpe < 0 && s.sharpe > 0,
  });
  rows.push({
    label: "worst close",
    win: s.worst_close < 0 ? fmtPnl(s.worst_close) : "—",
    wide: ext.worst_close < 0 ? fmtPnl(ext.worst_close) : "—",
    flip: ext.worst_close < s.worst_close * 2 && ext.worst_close < 0,
  });

  const misleading = rows.some((r) => r.flip);

  return (
    <Panel
      title={`the same wallet over ${ext.span_days}d`}
      right={
        misleading ? (
          <span className="text-warn text-[11px]">the {days}d window flatters this book</span>
        ) : undefined
      }
    >
      <div className="grid grid-cols-[1.2fr_1fr_1fr] gap-2 px-4 py-2 text-[10px] uppercase tracking-wider text-muted border-b border-border">
        <div />
        <div className="text-right">{days}d window</div>
        <div className="text-right">{ext.span_days}d, all history</div>
      </div>
      {rows.map((r) => (
        <div key={r.label} className="grid grid-cols-[1.2fr_1fr_1fr] gap-2 px-4 py-1.5 table-row">
          <div className="text-[11px] text-muted uppercase tracking-wider">{r.label}</div>
          <div className={`num text-right ${r.flip ? "text-warn" : "text-ink/90"}`}>{r.win}</div>
          <div className="num text-right text-ink/90">{r.wide}</div>
        </div>
      ))}
      <div className="px-4 py-2 text-[10px] text-muted border-t border-border">
        Both columns come from the same cached fills — the wider view costs no extra request.
      </div>
    </Panel>
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
