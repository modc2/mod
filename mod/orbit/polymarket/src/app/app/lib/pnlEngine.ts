import { PolymarketTrade, PolymarketPosition } from "./types";
import { CurvePoint } from "../components/PnlChart";

export interface AnnotatedTrade extends PolymarketTrade {
  realized: number;
  hasBasis: boolean;
  buyPrice?: number;
  buyTimestamp?: number;
}

/**
 * Aggregate raw source-trader trades into rebalance-window deltas.
 * Each bucket is (conditionId, windowStart). Within a bucket, BUYs and SELLs
 * net out — only the residual position change is emitted, priced at the
 * volume-weighted average price on the dominant side. Buckets that net to
 * zero (entered and exited within the window) are dropped entirely, which is
 * the realistic copy-trader outcome: never observed, never traded.
 *
 * `rebalancePeriodHours` is the window length. `rebalanceHour` is the
 * intra-day offset (0–23) the schedule is anchored to in the user's local
 * timezone. `rebalancePeriodHours <= 0` is a passthrough.
 */
export function aggregateToRebalanceWindows(
  trades: PolymarketTrade[],
  rebalancePeriodHours: number,
  rebalanceHour: number,
): PolymarketTrade[] {
  if (rebalancePeriodHours <= 0) return trades;
  const periodMs = rebalancePeriodHours * 3600_000;

  // Anchor: most recent local-time `rebalanceHour` boundary at-or-before ts.
  const anchorMs = (ts: number) => {
    const d = new Date(ts);
    d.setHours(rebalanceHour, 0, 0, 0);
    if (d.getTime() > ts) d.setDate(d.getDate() - 1);
    return d.getTime();
  };

  type Bucket = {
    bucketStart: number;
    market: string;
    conditionId: string;
    buyShares: number;
    buyValue: number;
    sellShares: number;
    sellValue: number;
  };
  const buckets = new Map<string, Bucket>();

  for (const t of trades) {
    const anchor = anchorMs(t.timestamp);
    const idx = Math.floor((t.timestamp - anchor) / periodMs);
    const bucketStart = anchor + idx * periodMs;
    const key = `${t.conditionId || t.market}:${bucketStart}`;
    let b = buckets.get(key);
    if (!b) {
      b = {
        bucketStart,
        market: t.market,
        conditionId: t.conditionId,
        buyShares: 0, buyValue: 0,
        sellShares: 0, sellValue: 0,
      };
      buckets.set(key, b);
    }
    if (t.side === "BUY") {
      b.buyShares += t.size;
      b.buyValue += t.price * t.size;
    } else {
      b.sellShares += t.size;
      b.sellValue += t.price * t.size;
    }
  }

  const out: PolymarketTrade[] = [];
  for (const b of buckets.values()) {
    const net = b.buyShares - b.sellShares;
    if (Math.abs(net) < 1e-9) continue;
    const executeAt = b.bucketStart + periodMs;
    const isBuy = net > 0;
    out.push({
      id: `rebal:${b.conditionId || b.market}:${b.bucketStart}`,
      market: b.market,
      conditionId: b.conditionId,
      side: isBuy ? "BUY" : "SELL",
      size: Math.abs(net),
      price: isBuy
        ? (b.buyShares > 0 ? b.buyValue / b.buyShares : 0)
        : (b.sellShares > 0 ? b.sellValue / b.sellShares : 0),
      pnl: 0,
      timestamp: executeAt,
    });
  }
  return out.sort((a, b) => a.timestamp - b.timestamp);
}

/**
 * Replay full trade history with FIFO bookkeeping.
 * Returns trades annotated with realized PnL per SELL.
 * Pass cutoffMs to start fresh from that time (no seed basis from old positions).
 */
export function computeFifoTrades(
  trades: PolymarketTrade[],
  positions: PolymarketPosition[],
  cutoffMs?: number,
): AnnotatedTrade[] {
  // Don't seed from positions if we're starting fresh
  const seedAvgPrice = new Map<string, number>();
  if (!cutoffMs) {
    for (const p of positions) {
      const key = p.conditionId || p.market;
      if (key && p.avgPrice > 0) seedAvgPrice.set(key, p.avgPrice);
    }
  }
  const sorted = [...trades].sort((a, b) => a.timestamp - b.timestamp);
  type BuyLot = { price: number; size: number; ts: number };
  const book = new Map<string, BuyLot[]>();

  return sorted.map((t) => {
    const key = t.conditionId || t.market;
    if (!book.has(key)) book.set(key, []);
    const lots = book.get(key)!;

    let realized = 0;
    let hasBasis = true;
    let buyPrice: number | undefined;
    let buyTimestamp: number | undefined;

    if (t.side === "BUY") {
      lots.push({ price: t.price, size: t.size, ts: t.timestamp });
    } else {
      let remaining = t.size;
      let totalCost = 0;
      let totalFilled = 0;
      let earliestBuyTs = Infinity;

      while (remaining > 0 && lots.length > 0) {
        const lot = lots[0];
        const take = Math.min(remaining, lot.size);
        totalCost += lot.price * take;
        totalFilled += take;
        if (lot.ts < earliestBuyTs) earliestBuyTs = lot.ts;
        lot.size -= take;
        remaining -= take;
        if (lot.size <= 1e-9) lots.shift();
      }

      if (totalFilled > 0) {
        const avgEntry = totalCost / totalFilled;
        realized = (t.price - avgEntry) * totalFilled;
        buyPrice = avgEntry;
        buyTimestamp = earliestBuyTs;
      } else if (seedAvgPrice.has(key)) {
        const seed = seedAvgPrice.get(key) || 0;
        realized = (t.price - seed) * t.size;
        buyPrice = seed;
      } else {
        hasBasis = false;
      }
    }

    return { ...t, realized, hasBasis, buyPrice, buyTimestamp };
  });
}

/**
 * Build MTM PnL curve for a single trader within a time window.
 */
export function buildPnlCurve(
  annotatedTrades: AnnotatedTrade[],
  positions: PolymarketPosition[],
  cutoffMs: number,
): CurvePoint[] {
  const windowTrades = annotatedTrades.filter((t) => t.timestamp >= cutoffMs);
  if (!windowTrades.length) return [];
  const sorted = [...windowTrades].sort((a, b) => a.timestamp - b.timestamp);

  const curByKey = new Map<string, number>();
  for (const p of positions) {
    const key = p.conditionId || p.market;
    if (key && p.currentPrice > 0) curByKey.set(key, p.currentPrice);
  }

  const inv = new Map<string, number>();
  const lastPx = new Map<string, number>();
  let cash = 0;
  const markFor = (key: string) => curByKey.get(key) ?? lastPx.get(key) ?? 0;

  const fmtDate = (ts: number) =>
    new Date(ts).toLocaleDateString([], { month: "short", day: "numeric" });
  const fmtTime = (ts: number) =>
    new Date(ts).toLocaleString([], {
      month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
    });

  const points: CurvePoint[] = sorted.map((t, i) => {
    const key = t.conditionId || t.market;
    if (t.side === "BUY") {
      cash -= t.price * t.size;
      inv.set(key, (inv.get(key) || 0) + t.size);
    } else {
      cash += t.price * t.size;
      inv.set(key, (inv.get(key) || 0) - t.size);
    }
    lastPx.set(key, t.price);

    let invValue = 0;
    for (const [k, s] of inv) {
      if (s !== 0) invValue += s * markFor(k);
    }
    const mtm = cash + invValue;

    return {
      i,
      ts: t.timestamp,
      date: fmtDate(t.timestamp),
      time: fmtTime(t.timestamp),
      pnl: Math.round(mtm * 100) / 100,
      side: t.side,
      realized: t.realized,
      market: t.market,
      size: t.size,
      price: t.price,
      buyPrice: t.buyPrice,
      buyTimestamp: t.buyTimestamp,
    };
  });

  // Append "NOW" mark for open inventory
  let nowInvValue = 0;
  let hasOpenInventory = false;
  for (const [k, s] of inv) {
    if (Math.abs(s) < 1e-9) continue;
    nowInvValue += s * markFor(k);
    hasOpenInventory = true;
  }
  if (hasOpenInventory) {
    const nowTs = Date.now();
    const nowMtm = cash + nowInvValue;
    points.push({
      i: points.length,
      ts: nowTs,
      date: fmtDate(nowTs),
      time: "NOW",
      pnl: Math.round(nowMtm * 100) / 100,
      side: "MARK",
      realized: 0,
      market: "",
      size: 0,
      price: 0,
    });
  }

  return points;
}

/**
 * Combine multiple traders' PnL curves into a single weighted index curve.
 *
 * Algorithm:
 *  1. Collect all timestamps across all traders
 *  2. Sort chronologically
 *  3. At each timestamp, step-interpolate each trader's PnL
 *  4. Combined PnL = sum(trader_pnl * weight)
 *  5. Downsample to maxPoints for SVG performance
 */
export function buildCombinedPnlCurve(
  traderCurves: { address: string; points: CurvePoint[]; weight: number }[],
  maxPoints = 300,
): CurvePoint[] {
  // Collect all unique timestamps
  const allTs = new Set<number>();
  for (const tc of traderCurves) {
    for (const p of tc.points) allTs.add(p.ts);
  }
  if (allTs.size === 0) return [];

  const sortedTs = Array.from(allTs).sort((a, b) => a - b);

  // For each timestamp, compute weighted sum of each trader's PnL
  const fmtDate = (ts: number) =>
    new Date(ts).toLocaleDateString([], { month: "short", day: "numeric" });
  const fmtTime = (ts: number) =>
    new Date(ts).toLocaleString([], {
      month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
    });

  const rawPoints: CurvePoint[] = sortedTs.map((ts, i) => {
    let combinedPnl = 0;
    for (const tc of traderCurves) {
      if (tc.points.length === 0) continue;
      // Step-interpolate: find last point at or before ts
      let traderPnl = 0;
      for (let j = tc.points.length - 1; j >= 0; j--) {
        if (tc.points[j].ts <= ts) {
          traderPnl = tc.points[j].pnl;
          break;
        }
      }
      combinedPnl += traderPnl * tc.weight;
    }

    return {
      i,
      ts,
      date: fmtDate(ts),
      time: fmtTime(ts),
      pnl: Math.round(combinedPnl * 100) / 100,
      side: "MARK" as const,
      realized: 0,
      market: "",
      size: 0,
      price: 0,
    };
  });

  // Downsample if too many points
  if (rawPoints.length <= maxPoints) return rawPoints;

  const step = rawPoints.length / maxPoints;
  const downsampled: CurvePoint[] = [];
  for (let i = 0; i < maxPoints; i++) {
    const idx = Math.min(Math.floor(i * step), rawPoints.length - 1);
    downsampled.push({ ...rawPoints[idx], i });
  }
  // Always include the last point
  if (downsampled[downsampled.length - 1].ts !== rawPoints[rawPoints.length - 1].ts) {
    downsampled.push({ ...rawPoints[rawPoints.length - 1], i: downsampled.length });
  }

  return downsampled;
}

/**
 * Build a combined PnL curve that shows individual trades from all traders.
 * Each point represents an actual trade (BUY/SELL) or mark-to-market point.
 * This allows you to see exactly which trades are happening across the strategy.
 */
export function buildTradeByTradeCombinedCurve(
  traderData: {
    address: string;
    trades: AnnotatedTrade[];
    positions: PolymarketPosition[];
    weight: number;
  }[],
  cutoffMs: number,
): CurvePoint[] {
  // Collect all trades from all traders and merge chronologically
  type TradeEvent = {
    ts: number;
    trade: AnnotatedTrade;
    traderAddress: string;
    weight: number;
  };

  const allEvents: TradeEvent[] = [];
  for (const td of traderData) {
    for (const trade of td.trades) {
      if (trade.timestamp >= cutoffMs) {
        allEvents.push({
          ts: trade.timestamp,
          trade,
          traderAddress: td.address,
          weight: td.weight,
        });
      }
    }
  }

  // Sort chronologically
  allEvents.sort((a, b) => a.ts - b.ts);

  if (allEvents.length === 0) return [];

  // Filter out SELLs without a prior window BUY (copy-trader wouldn't hold those)
  const windowBook = new Map<string, number>(); // "trader:conditionId" → shares
  const filteredEvents: TradeEvent[] = [];
  for (const event of allEvents) {
    const t = event.trade;
    const bk = `${event.traderAddress}:${t.conditionId || t.market}`;
    if (t.side === "BUY") {
      windowBook.set(bk, (windowBook.get(bk) || 0) + t.size);
      filteredEvents.push(event);
    } else {
      const inv = windowBook.get(bk) || 0;
      if (inv <= 1e-9) continue; // no window basis, skip
      const sold = Math.min(t.size, inv);
      windowBook.set(bk, inv - sold);
      filteredEvents.push({ ...event, trade: { ...t, size: sold } });
    }
  }

  // Build combined portfolio state - track inventory and cash for each trader
  const portfolios = new Map<string, {
    cash: number;
    inv: Map<string, number>;
    lastPx: Map<string, number>;
  }>();

  for (const td of traderData) {
    portfolios.set(td.address, {
      cash: 0,
      inv: new Map(),
      lastPx: new Map(),
    });
  }

  // Get current prices for mark-to-market
  const curByKey = new Map<string, number>();
  for (const td of traderData) {
    for (const p of td.positions) {
      const key = p.conditionId || p.market;
      if (key && p.currentPrice > 0) curByKey.set(key, p.currentPrice);
    }
  }

  const markFor = (key: string, traderAddr: string) => {
    const portfolio = portfolios.get(traderAddr);
    if (!portfolio) return 0;
    return curByKey.get(key) ?? portfolio.lastPx.get(key) ?? 0;
  };

  const fmtDate = (ts: number) =>
    new Date(ts).toLocaleDateString([], { month: "short", day: "numeric" });
  const fmtTime = (ts: number) =>
    new Date(ts).toLocaleString([], {
      month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
    });

  const points: CurvePoint[] = [];

  for (let i = 0; i < filteredEvents.length; i++) {
    const event = filteredEvents[i];
    const portfolio = portfolios.get(event.traderAddress)!;
    const t = event.trade;
    const key = t.conditionId || t.market;

    // Update this trader's portfolio
    if (t.side === "BUY") {
      portfolio.cash -= t.price * t.size;
      portfolio.inv.set(key, (portfolio.inv.get(key) || 0) + t.size);
    } else {
      portfolio.cash += t.price * t.size;
      portfolio.inv.set(key, (portfolio.inv.get(key) || 0) - t.size);
    }
    portfolio.lastPx.set(key, t.price);

    // Compute combined weighted PnL across all traders
    let combinedPnl = 0;
    for (const td of traderData) {
      const p = portfolios.get(td.address)!;
      let invValue = 0;
      for (const [k, s] of p.inv) {
        if (s !== 0) invValue += s * markFor(k, td.address);
      }
      const traderMtm = p.cash + invValue;
      combinedPnl += traderMtm * td.weight;
    }

    points.push({
      i,
      ts: t.timestamp,
      date: fmtDate(t.timestamp),
      time: fmtTime(t.timestamp),
      pnl: Math.round(combinedPnl * 100) / 100,
      side: t.side,
      realized: t.realized,
      market: t.market,
      size: t.size,
      price: t.price,
      buyPrice: t.buyPrice,
      buyTimestamp: t.buyTimestamp,
    });
  }

  // Add final NOW mark if there's open inventory
  let hasOpenInv = false;
  let nowPnl = 0;
  for (const td of traderData) {
    const p = portfolios.get(td.address)!;
    let invValue = 0;
    for (const [k, s] of p.inv) {
      if (Math.abs(s) >= 1e-9) {
        invValue += s * markFor(k, td.address);
        hasOpenInv = true;
      }
    }
    nowPnl += (p.cash + invValue) * td.weight;
  }

  if (hasOpenInv) {
    const nowTs = Date.now();
    points.push({
      i: points.length,
      ts: nowTs,
      date: fmtDate(nowTs),
      time: "NOW",
      pnl: Math.round(nowPnl * 100) / 100,
      side: "MARK",
      realized: 0,
      market: "",
      size: 0,
      price: 0,
    });
  }

  return points;
}
