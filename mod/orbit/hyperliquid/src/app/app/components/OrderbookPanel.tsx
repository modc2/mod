"use client";

import { useEffect, useRef, useState } from "react";
import { fetchOrderbook } from "../lib/api";

// l2Book level: [priceStr, sizeStr, numOrders]
type Level = [string, string, number];

type Tooltip = { x: number; y: number; price: number; size: number; orders: number; side: "bid" | "ask" };

const ROWS = 12;
const BAR_H = 18;
const MID_GAP = 24;
const H = ROWS * 2 * BAR_H + MID_GAP + 8;
const L = 80;   // left label width
const R = 80;   // right label width
const PILL_H = MID_GAP - 4;

const fmtNum = (n: number): string => {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return n.toFixed(2);
};

const fmtPrice = (n: number): string => {
  if (n >= 10_000) return `$${(n / 1_000).toFixed(1)}k`;
  if (n >= 1_000) return `$${n.toFixed(0)}`;
  if (n >= 10) return `$${n.toFixed(2)}`;
  return `$${n.toPrecision(4)}`;
};

export default function OrderbookPanel({ coin }: { coin: string }) {
  const [book, setBook] = useState<{ bids: Level[]; asks: Level[]; mid: number; spread: number } | null>(null);
  const [tooltip, setTooltip] = useState<Tooltip | null>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(0);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setW(el.clientWidth));
    ro.observe(el);
    setW(el.clientWidth);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    let alive = true;
    const load = () => {
      fetchOrderbook(coin)
        .then((data: any) => {
          if (!alive) return;
          const rawBids: Level[] = (data?.levels?.[0] ?? []).slice(0, ROWS);
          const rawAsks: Level[] = (data?.levels?.[1] ?? []).slice(0, ROWS);

          // cumulative notional for bar sizing
          let cumBid = 0;
          const bids = rawBids.map((l) => {
            cumBid += Number(l[0]) * Number(l[1]);
            return l;
          });
          let cumAsk = 0;
          const asks = rawAsks.map((l) => {
            cumAsk += Number(l[0]) * Number(l[1]);
            return l;
          });

          const bestBid = rawBids.length > 0 ? Number(rawBids[0][0]) : 0;
          const bestAsk = rawAsks.length > 0 ? Number(rawAsks[0][0]) : 0;
          const mid = bestBid > 0 && bestAsk > 0 ? (bestBid + bestAsk) / 2 : 0;
          const spread = bestAsk > 0 && bestBid > 0 ? bestAsk - bestBid : 0;

          setBook({ bids, asks, mid, spread });
        })
        .catch(() => {});
    };
    load();
    const id = setInterval(load, 2000);
    return () => { alive = false; clearInterval(id); };
  }, [coin]);

  if (!book) {
    return <div className="px-4 py-6 text-xs text-muted">loading orderbook…</div>;
  }

  const { bids, asks, mid, spread } = book;

  // max cumulative notional across both sides for bar scaling
  let maxNotional = 0;
  let cumB = 0;
  for (const l of bids) { cumB += Number(l[0]) * Number(l[1]); maxNotional = Math.max(maxNotional, cumB); }
  let cumA = 0;
  for (const l of asks) { cumA += Number(l[0]) * Number(l[1]); maxNotional = Math.max(maxNotional, cumA); }
  if (maxNotional === 0) maxNotional = 1;

  const iw = Math.max(0, w - L - R);
  const halfW = iw / 2;
  const cx = L + halfW; // SVG x of centre divider

  const midY = ROWS * BAR_H + MID_GAP / 2;

  const barX = (cum: number, side: "bid" | "ask") => {
    const barW = (cum / maxNotional) * halfW;
    return side === "bid" ? cx - barW : cx;
  };

  const spreadBps = mid > 0 ? ((spread / mid) * 10_000).toFixed(1) : "—";

  return (
    <div ref={wrapRef} className="relative select-none" onPointerLeave={() => setTooltip(null)}>
      {w > 0 && (
        <svg width={w} height={H} className="block overflow-visible">
          {/* bid bars — top half, rows from centre upwards */}
          {(() => {
            let cum = 0;
            return bids.map((l, i) => {
              const price = Number(l[0]);
              const size = Number(l[1]);
              const orders = l[2];
              cum += price * size;
              const barW = (cum / maxNotional) * halfW;
              const y = midY - MID_GAP / 2 - (i + 1) * BAR_H;
              const bx = cx - barW;
              return (
                <g key={i}>
                  <rect
                    x={bx} y={y} width={barW} height={BAR_H - 2}
                    fill="rgb(var(--c-win))" fillOpacity={0.18}
                    style={{ transition: "width 0.3s ease, x 0.3s ease" }}
                  />
                  <rect
                    x={bx} y={y} width={barW} height={BAR_H - 2}
                    fill="transparent"
                    className="cursor-crosshair"
                    onPointerEnter={(e) => setTooltip({ x: e.clientX, y: e.clientY, price, size, orders, side: "bid" })}
                  />
                  <text x={L - 4} y={y + BAR_H - 6} textAnchor="end" fontSize={10} className="fill-win num">{fmtPrice(price)}</text>
                  <text x={w - R + 4} y={y + BAR_H - 6} textAnchor="start" fontSize={10} className="fill-muted num">{fmtNum(size)}</text>
                </g>
              );
            });
          })()}

          {/* ask bars — bottom half, rows from centre downwards */}
          {(() => {
            let cum = 0;
            return asks.map((l, i) => {
              const price = Number(l[0]);
              const size = Number(l[1]);
              const orders = l[2];
              cum += price * size;
              const barW = (cum / maxNotional) * halfW;
              const y = midY + MID_GAP / 2 + i * BAR_H;
              return (
                <g key={i}>
                  <rect
                    x={cx} y={y} width={barW} height={BAR_H - 2}
                    fill="rgb(var(--c-loss))" fillOpacity={0.18}
                    style={{ transition: "width 0.3s ease" }}
                  />
                  <rect
                    x={cx} y={y} width={barW} height={BAR_H - 2}
                    fill="transparent"
                    className="cursor-crosshair"
                    onPointerEnter={(e) => setTooltip({ x: e.clientX, y: e.clientY, price, size, orders, side: "ask" })}
                  />
                  <text x={L - 4} y={y + BAR_H - 6} textAnchor="end" fontSize={10} className="fill-loss num">{fmtPrice(price)}</text>
                  <text x={w - R + 4} y={y + BAR_H - 6} textAnchor="start" fontSize={10} className="fill-muted num">{fmtNum(size)}</text>
                </g>
              );
            });
          })()}

          {/* centre divider line */}
          <line x1={cx} x2={cx} y1={0} y2={H} stroke="var(--border)" strokeWidth={1} />

          {/* mid price + spread pill */}
          <rect
            x={cx - 60} y={midY - PILL_H / 2}
            width={120} height={PILL_H} rx={PILL_H / 2}
            fill="var(--surface)" stroke="var(--border)" strokeWidth={1}
          />
          {mid > 0 && (
            <text x={cx} y={midY - 1} textAnchor="middle" fontSize={11} fontWeight={600} className="fill-ink num">
              {fmtPrice(mid)}
            </text>
          )}
          {spread > 0 && (
            <text x={cx} y={midY + 9} textAnchor="middle" fontSize={9} className="fill-muted num">
              {`spread ${fmtPrice(spread)} · ${spreadBps}bps`}
            </text>
          )}
        </svg>
      )}

      {/* hover tooltip */}
      {tooltip && (
        <div
          className="fixed z-50 pointer-events-none rounded-md border border-white/[0.08] bg-black/80 px-2.5 py-1.5 text-xs"
          style={{ left: tooltip.x + 12, top: tooltip.y - 40 }}
        >
          <div className={`num font-semibold ${tooltip.side === "bid" ? "text-win" : "text-loss"}`}>
            {tooltip.side === "bid" ? "bid" : "ask"} {fmtPrice(tooltip.price)}
          </div>
          <div className="text-muted">{fmtNum(tooltip.size)} contracts · {tooltip.orders} order{tooltip.orders !== 1 ? "s" : ""}</div>
        </div>
      )}
    </div>
  );
}
