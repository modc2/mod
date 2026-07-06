// FlowMomentum — reference HISTORY-DRIVEN strat.
//
// Where CopyTrader mirrors individual fills 1:1, this class reads the
// AGGREGATE observed flow in `StratHistory` and originates its own trades
// via `propose`: when several watched traders pile into the same side of
// the same market inside the lookback window, it buys that consensus once,
// sized by conviction — and exits when the flow flips against a held
// position. It never mirrors per-trade (shouldMirror → false), so it is a
// pure demonstration of the propose path: any strat that can be written as
// "look at the history, decide what to hold" fits this shape.
//
// Tunables are ordinary class params — the registry constructs the class
// with them, and `this.params` carries them at runtime.

import {
  Strat,
  StratParams,
  StratHistory,
  ProposedTrade,
  SizeConstraints,
  tickRoundPrice,
  marketMatchesQuery,
} from "./base";

export interface FlowMomentumOpts extends StratParams {
  /** How far back (minutes) to aggregate flow. Default 90. */
  lookbackMinutes?: number;
  /** Minimum distinct watched traders on the same side before the signal
      counts as consensus. Default 2. */
  minTraders?: number;
  /** Minimum aggregate BUY notional (USD) across those traders. Default 50. */
  minFlowUsd?: number;
  /** Max simultaneous open positions this strat originates. Default 5. */
  maxPositions?: number;
}

interface MarketFlow {
  conditionId: string;
  market: string;
  outcome: string;
  buyUsd: number;
  sellUsd: number;
  buyers: Set<string>;
  sellers: Set<string>;
  lastPrice: number;
  lastTs: number;
}

export class FlowMomentum extends Strat<FlowMomentumOpts> {
  readonly name: string = "flowmomentum";

  // Never mirror individual trades — this strat trades the aggregate only.
  shouldMirror(): boolean {
    return false;
  }

  // ── Flow aggregation over the history window ───────────────────
  private aggregate(history: StratHistory): Map<string, MarketFlow> {
    const lookbackMs = (this.params.lookbackMinutes ?? 90) * 60_000;
    const cutoff = history.now - lookbackMs;
    const flows = new Map<string, MarketFlow>();
    for (const t of history.trades) {
      if (t.timestamp < cutoff) continue;
      if (!marketMatchesQuery(t.market, this.params.marketQuery ?? "")) continue;
      const outcome = t.outcome || "Yes";
      const key = `${t.conditionId.toLowerCase()}:${outcome.toLowerCase()}`;
      let f = flows.get(key);
      if (!f) {
        f = {
          conditionId: t.conditionId,
          market: t.market,
          outcome,
          buyUsd: 0,
          sellUsd: 0,
          buyers: new Set(),
          sellers: new Set(),
          lastPrice: t.price,
          lastTs: t.timestamp,
        };
        flows.set(key, f);
      }
      if (t.side === "BUY") {
        f.buyUsd += t.notional;
        f.buyers.add(t.trader.toLowerCase());
      } else {
        f.sellUsd += t.notional;
        f.sellers.add(t.trader.toLowerCase());
      }
      // trades are newest-first; keep the newest price we've seen.
      if (t.timestamp > f.lastTs) {
        f.lastTs = t.timestamp;
        f.lastPrice = t.price;
      }
    }
    return flows;
  }

  // ── Origination ────────────────────────────────────────────────
  // ENTRIES: consensus BUY flow (≥ minTraders distinct buyers, aggregate
  // ≥ minFlowUsd, net flow positive) in markets we don't already hold,
  // ranked by net flow, sized proportionally to conviction and clamped
  // into the user's trade band. EXITS: held positions whose window flow
  // has flipped net-SELL.
  propose(history: StratHistory, c: SizeConstraints): ProposedTrade[] {
    const minTraders = this.params.minTraders ?? 2;
    const minFlowUsd = this.params.minFlowUsd ?? 50;
    const maxPositions = this.params.maxPositions ?? 5;
    const flows = this.aggregate(history);
    const proposals: ProposedTrade[] = [];

    const held = new Map(
      history.positions
        .filter((p) => p.size > 0)
        .map((p) => [`${p.conditionId.toLowerCase()}:${(p.outcome || "Yes").toLowerCase()}`, p]),
    );

    // EXITS first — freed capital funds the entries below.
    for (const [key, pos] of held) {
      const f = flows.get(key);
      if (!f) continue;
      const netUsd = f.buyUsd - f.sellUsd;
      if (netUsd < 0 && f.sellers.size >= minTraders) {
        proposals.push({
          conditionId: pos.conditionId,
          outcome: pos.outcome,
          market: pos.market,
          side: "SELL",
          notional: pos.value,
          limitPrice: tickRoundPrice(pos.currentPrice * 0.97),
          reason: `FLOW FLIPPED · ${f.sellers.size} traders net -$${Math.abs(netUsd).toFixed(0)} in window`,
        });
      }
    }

    // ENTRIES — strongest net-BUY consensus first.
    const openSlots = Math.max(0, maxPositions - held.size);
    const entries = [...flows.entries()]
      .filter(([key, f]) =>
        !held.has(key) &&
        f.buyers.size >= minTraders &&
        f.buyUsd >= minFlowUsd &&
        f.buyUsd > f.sellUsd &&
        f.lastPrice > 0.02 && f.lastPrice < 0.95,
      )
      .sort((a, b) => (b[1].buyUsd - b[1].sellUsd) - (a[1].buyUsd - a[1].sellUsd))
      .slice(0, openSlots);

    for (const [, f] of entries) {
      // Conviction sizing: share of capital proportional to this market's
      // slice of total net flow, clamped into the user's trade band.
      const netUsd = f.buyUsd - f.sellUsd;
      const raw = Math.min(c.capital / Math.max(maxPositions, 1), netUsd);
      const notional = Math.min(Math.max(raw, c.userFloor, c.clobFloor), c.userCeiling);
      proposals.push({
        conditionId: f.conditionId,
        outcome: f.outcome,
        market: f.market,
        side: "BUY",
        notional,
        // Chase up to 2¢ past the last observed print so the entry fills.
        limitPrice: tickRoundPrice(f.lastPrice + 0.02),
        reason: `CONSENSUS · ${f.buyers.size} traders +$${netUsd.toFixed(0)} net BUY in ${this.params.lookbackMinutes ?? 90}m`,
      });
    }

    return proposals;
  }
}
