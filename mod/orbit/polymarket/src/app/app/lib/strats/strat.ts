// THE Strat class — the one, standard strategy class.
//
// A strategy IS this class. There is no registry and no subclass zoo: the
// live engine (copyEngine.ts) and the backtest (CopyIndex.tsx) both do
// `new Strat(params)` and drive everything through five hooks. Every hook
// receives the full `StratHistory` — the observed trade history across all
// watched traders, per-trader stats, open positions, and balance — so the
// strat can reason over ANY history of the data, not just the single trade
// in front of it.
//
// Everything a strategy can do is a PARAM on this one class:
//
//   maxPerCycle      per-cycle BUY budget (fee control)
//   marketQuery      free-text market-topic gate
//   tradeFilters     semantic per-trade gates (side/price/size/category)
//   mirror           false = never mirror per-trade (origination only)
//   slippageBps      limit-price widening toward the fillable side
//   flow { … }       opt-in flow-momentum ORIGINATION: buy watchlist
//                    consensus from history, exit when flow flips
//   momentum { … }   opt-in PRICE-momentum ORIGINATION: buy the outcome
//                    whose own odds are rising (50¢ → 60¢), exit when the
//                    move reverses — needs no watchlist at all
//
// The five hooks (all have working defaults driven by those params):
//
//   maxPerCycle()                     per-cycle BUY budget
//   shouldMirror(trade, history)      pre-filter observed upstream trades
//   scoreCandidate(trade, s, history) rank candidates by expected $ edge
//   sizeAndPrice(trade, c, history)   final notional + limit price
//   propose(history, c)               ORIGINATE trades from history alone
//
// The engine handles plumbing — cycle loop, balance check, deposit wallet,
// CLOB submission, log persistence, ROI stats refresh. The strat only
// decides WHAT to trade, in WHAT size, at WHAT price.
//
// The class stays subclassable for power users (override any hook, hand
// the instance to CopyEngine's constructor), but the standard path is
// params on this one class. Mirrors src/strats/base/mod.py (sync → signal
// → execute) for users who author strats in Python.

import { PolymarketTrade, PolymarketPosition, TraderRoiStats, IndexTrader } from "../types";
import type { TradeFilters, MomentumParams } from "../types";
import { marketMatchesQuery } from "../marketQuery";
import { tradeMatchesFilters } from "../tradeFilters";

// ── Shared per-trade context the strat sees ──────────────────────

/** A single observed upstream trade from a watched trader. The strat decides
    whether/how to mirror it. */
export interface TraderTrade extends PolymarketTrade {
  /** Address of the watched trader who placed this trade. */
  trader: string;
  /** Trader's weight from the strat's watchlist. */
  weight: number;
  /** Trader's total weight share (`weight / sum(weights)`). */
  weightFraction: number;
  /** Proportional copy ratio — multiply by `trade.notional` for the raw
      mirror notional before clamping. See `copyRatioFor`: the leader risked
      some fraction of their bankroll, we risk the same fraction of our
      account value. */
  copyRatio: number;
  /** `price × size` — leader's dollar exposure on this trade. */
  notional: number;
}

/** Everything the engine knows, handed to every hook. This is the "any
    history of the data" contract: a strat can aggregate flow, detect
    momentum, compare against its own book — whatever its logic needs. */
export interface StratHistory {
  /** Observed upstream trades across ALL watched traders inside the
      strat's lookback window (`backtestDays`), newest-first. During the
      engine's collection phase this is complete for every trader already
      polled this cycle; by sizing (`sizeAndPrice`) and origination
      (`propose`) time it is complete for the whole watchlist. */
  trades: TraderTrade[];
  /** Per-trader ROI / Sharpe stats, keyed by lowercased address. */
  traderStats: Record<string, TraderRoiStats>;
  /** Open positions in the trading wallet. Fetched per-cycle only when the
      strat originates trades (`proposes()` true); empty otherwise. */
  positions: PolymarketPosition[];
  /** Usable USDC balance in the trading wallet. null = not yet read. */
  balance: number | null;
  /** Strat's allocated capital (USD). */
  capital: number;
  /** The strat's watchlist (enabled traders only). */
  watchlist: IndexTrader[];
  /** Engine cycle counter (0 in backtest). */
  cycle: number;
  /** Clock at history assembly (ms epoch). */
  now: number;
  /** CLOB price history for candidate markets — fetched by the engine only
      when the strat asks for it (`wantsMarketPrices()`, i.e. momentum
      strats). Absent everywhere else. */
  marketPrices?: MarketPriceSeries[];
}

/** One market's observed price series, as fed to momentum strats. Prices
    are the FIRST outcome's (index 0); the second outcome's price is its
    complement (binary markets), so one series covers both sides. */
export interface MarketPriceSeries {
  conditionId: string;
  /** Market title — for logs/reasons. */
  market: string;
  /** Outcome names, index-aligned with `tokenIds` (["Yes","No"] or
      ["Up","Down"]). */
  outcomes: [string, string];
  /** CLOB token ids, index-aligned with `outcomes`. */
  tokenIds: [string, string];
  /** Market end date (ms epoch); undefined when unknown. */
  endDateMs?: number;
  /** Price points for outcomes[0], ms timestamps, ascending. */
  points: { t: number; p: number }[];
}

/** An empty history for contexts that have nothing better (helper probes,
    unit tests). Real engine cycles always build the full thing. */
export function emptyHistory(capital = 0): StratHistory {
  return {
    trades: [],
    traderStats: {},
    positions: [],
    balance: null,
    capital,
    watchlist: [],
    cycle: 0,
    now: 0,
  };
}

/** Sizing constraints supplied by the strat config — the engine forwards
    these so the strat doesn't have to dig into config itself. */
export interface SizeConstraints {
  /** User's TRADE SIZE min (USD). Smaller mirror amounts → skip. */
  userFloor: number;
  /** User's TRADE SIZE max (USD). Larger mirror amounts → clamp down. */
  userCeiling: number;
  /** CLOB's hard floor for this trade's price (`max($1, 5 × price)`).
      Smaller mirror amounts → clamp up or skip. */
  clobFloor: number;
  /** Strat's allocated capital in USD. */
  capital: number;
}

/** Output of the strat's per-candidate decision. The engine consumes this
    rather than letting the strat call placeOrder itself — keeps order
    submission, retries, and logging in one place. */
export interface CandidateDecision {
  /** Mirror notional in USD. 0 = skip. */
  mirrorNotional: number;
  /** Limit price (0–1). Widened by `slippageBps` toward the fillable side. */
  limitPrice: number;
  /** Optional human-readable reason — surfaced in the BALANCE log row
      when a clamp/widening fired (e.g. "clamped up to CLOB floor"). */
  reason?: string;
}

/** A strat-ORIGINATED trade intent returned from `propose`. Unlike mirror
    candidates these are not tied to any upstream trade — the engine
    resolves the token id, clamps to the CLOB floor, and submits. */
export interface ProposedTrade {
  /** Market condition id (0x…). The engine resolves outcome → token id. */
  conditionId: string;
  /** "Yes" | "No". Defaults to "Yes". */
  outcome?: string;
  /** Exact CLOB token id when the strat already knows it (momentum strats
      carry it in the price series). Skips the engine's name-based lookup,
      which assumes Yes/No naming and would mis-resolve "Up"/"Down". */
  tokenId?: string;
  /** Market title — for the execution log only. */
  market: string;
  side: "BUY" | "SELL";
  /** Order size in USD. */
  notional: number;
  /** Limit price 0–1 (tick-rounded by the engine if off-grid). */
  limitPrice: number;
  /** Shown verbatim in the log next to the order. */
  reason?: string;
}

// ── Params ───────────────────────────────────────────────────────

/** Flow-momentum origination params. Setting `params.flow` (even `{}`)
    turns origination ON: the strat aggregates window flow across the
    watchlist and proposes entries where several traders pile into the
    same side, and exits when a held position's flow flips net-SELL. */
export interface FlowParams {
  /** How far back (minutes) to aggregate flow. Default 90. */
  lookbackMinutes?: number;
  /** Minimum distinct watched traders on the same side before the signal
      counts as consensus. Default 2. */
  minTraders?: number;
  /** Minimum aggregate BUY notional (USD) across those traders. Default 50. */
  minFlowUsd?: number;
  /** Max simultaneous open positions origination may hold. Default 5. */
  maxPositions?: number;
}

/** The one params surface. Every strategy is a value of this interface —
    saved strats, share bundles, and the engine config all reduce to it. */
export interface StratParams {
  /** Display name echoed in the UI / execution log. Default "strat". */
  name?: string;
  /** Max BUYs to act on per cycle. The single most important fee-control
      knob. Default 3. */
  maxPerCycle?: number;
  /** Free-text market-topic filter (e.g. "bitcoin"). When set, only trades
      in markets whose title matches pass `shouldMirror`. */
  marketQuery?: string;
  /** Semantic per-trade filters (side / price band / size band / category),
      AND-ed with `marketQuery` in `shouldMirror`. */
  tradeFilters?: TradeFilters;
  /** false = never mirror individual upstream trades (origination only).
      Default true. */
  mirror?: boolean;
  /** Limit-price widening in basis points toward the fillable side
      (BUY = up, SELL = down) so mirrors don't sit unfilled behind the
      market. 300 = 3¢ tolerance on a 100¢ market. Default 300. */
  slippageBps?: number;
  /** How far a mirror may be UPSIZED past its proportional notional when
      that notional lands below the order floor — the proportional-fidelity
      limit. 2 = "never place more than 2× what proportionality calls for";
      anything smaller is skipped rather than placed at the floor. Default 2.
      0 / null ⇒ legacy clamp-to-floor with no limit (how the strat used to
      turn every $0.20 intent into an identical $5 order). */
  maxUpscale?: number | null;
  /** Opt-in flow-momentum origination — see FlowParams. Absent = pure
      mirror strat, `propose` returns []. */
  flow?: FlowParams;
  /** Opt-in PRICE-momentum origination — see MomentumParams (types.ts).
      Buys the outcome whose own market price is rising, exits when the
      move reverses. Needs `history.marketPrices`, which the engine feeds
      only when this is set. */
  momentum?: MomentumParams;
}

// ── Flow aggregation internals ───────────────────────────────────

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

// ── The Strat class ──────────────────────────────────────────────

/** The standard strategy class. Construct it with `StratParams` and hand
    it to the engine — mirroring, scoring, sizing, and (opt-in) origination
    are all param-driven defaults. Subclassing still works for behavior no
    param expresses: override any hook and pass the instance to CopyEngine. */
export class Strat {
  /** Display name in the UI / log. */
  readonly name: string;

  /** The params the strat was constructed with — echoed for the UI and
      logs so a running engine can always show its exact configuration. */
  readonly params: StratParams;

  constructor(params: StratParams = {}) {
    this.params = params;
    this.name = params.name ?? "strat";
  }

  // ── Hook 1: per-cycle BUY cap ──
  // Top-N sampling keeps only this many BUYs by score (SELLs are always
  // honored). Lower = fewer fees, less churn.
  maxPerCycle(): number {
    return this.params.maxPerCycle ?? 3;
  }

  // ── Hook 2: pre-filter ──
  // Return false to skip an observed trade entirely (before scoring,
  // sizing, or any side-effects). Default applies three generic gates:
  //   1. `params.mirror === false` → never mirror (origination-only strat)
  //   2. market-topic query (`params.marketQuery`)
  //   3. semantic trade filters (`params.tradeFilters`)
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  shouldMirror(trade: TraderTrade, history: StratHistory): boolean {
    if (this.params.mirror === false) return false;
    return (
      marketMatchesQuery(trade.market, this.params.marketQuery ?? "") &&
      tradeMatchesFilters(trade, this.params.tradeFilters ?? {})
    );
  }

  // ── Hook 3: ranking ──
  // Returns probability-weighted dollars of expected edge. The engine sorts
  // BUY candidates by this and copies the top `maxPerCycle`; the same number
  // drives EP-based capital rotation. Default:
  //   P(success) × trader ROI × mirror$
  // where P(success) is the trader's Laplace-smoothed 30d win rate — so a
  // 70%-win trader's trades rank above a 52%-win trader's at equal dollar
  // edge. P is always > 0, so the sign (and the EP ≤ 0 skip) still comes
  // from ROI. No stats → 0 (can't estimate edge, never wins the budget).
  // Return 0 to drop a candidate without an explicit shouldMirror=false.
  // Mirrors live_engine.rs `candidate_score` — parity-fixture-tested.
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  scoreCandidate(trade: TraderTrade, stats: TraderRoiStats | null, history: StratHistory): number {
    if (!stats) return 0;
    return successProbability(stats) * stats.roi * trade.notional * trade.copyRatio;
  }

  // ── Hook 4: sizing + limit price ──
  // Called AFTER score-ranking, so the candidate is already a "we want
  // this one". Order of clamping is significant:
  //   1. Ceiling can't accommodate CLOB floor          → skip (no legal order)
  //   2. Raw mirror below effective floor + leader dust → skip (no signal)
  //   3. Raw mirror below effective floor, but the floor is more than
  //      `maxUpscale`× the proportional size            → skip (SUB_SCALE)
  //   4. Raw mirror below effective floor, small gap    → clamp up
  //   5. Raw mirror above ceiling                       → clamp down
  // The effective floor is max(user TRADE SIZE floor, CLOB per-price min).
  // Clamping UP is a lie about size, so it's bounded: a small distortion is
  // the price of a discrete order floor, a large one means the account
  // simply can't copy this leader in proportion and the mirror is refused.
  // Return `mirrorNotional: 0` to SKIP with `reason` shown verbatim in the
  // log. Mirrors live_engine.rs `plan_mirror` — parity-fixture-tested.
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  sizeAndPrice(trade: TraderTrade, c: SizeConstraints, history: StratHistory): CandidateDecision {
    const rawMirrorNotional = trade.notional * trade.copyRatio;
    const limitPrice = tickRoundPrice(this.adjustPrice(trade));

    // (1) No legal size at the user's ceiling for this trade's price.
    if (c.userCeiling < c.clobFloor) {
      return {
        mirrorNotional: 0,
        limitPrice,
        reason: `CEILING_BELOW_CLOB_FLOOR · ceiling $${c.userCeiling.toFixed(2)} < CLOB min $${c.clobFloor.toFixed(2)} (5 shares × ${(trade.price * 100).toFixed(0)}¢)`,
      };
    }
    // Effective minimum order: the larger of the user's TRADE SIZE floor
    // and the CLOB per-price hard floor (max($1, 5 × price)).
    const minNotional = Math.max(c.userFloor, c.clobFloor);
    const maxUpscale = this.params.maxUpscale === undefined ? DEFAULT_MAX_UPSCALE : this.params.maxUpscale;
    let mirrorNotional = rawMirrorNotional;
    let reason: string | undefined;
    // (2)/(3)/(4) Below the effective floor.
    if (rawMirrorNotional < minNotional) {
      if (trade.notional < c.clobFloor) {
        return {
          mirrorNotional: 0,
          limitPrice,
          reason: `LEADER_DUST · leader $${trade.notional.toFixed(2)} < Polymarket $${c.clobFloor.toFixed(2)} hard floor (5 shares × ${(trade.price * 100).toFixed(0)}¢)`,
        };
      }
      const upscale = minNotional / Math.max(rawMirrorNotional, 1e-9);
      if (maxUpscale && maxUpscale > 0 && upscale > maxUpscale) {
        return {
          mirrorNotional: 0,
          limitPrice,
          reason: `SUB_SCALE · proportional $${rawMirrorNotional.toFixed(2)} vs $${minNotional.toFixed(2)} min order — needs ~${upscale.toFixed(0)}× the account value to copy this leader in proportion`,
        };
      }
      mirrorNotional = minNotional;
      reason = `clamped up: proportional $${rawMirrorNotional.toFixed(2)} → $${minNotional.toFixed(2)} (min order: max floor $${c.userFloor.toFixed(2)}, CLOB $${c.clobFloor.toFixed(2)} = 5 shares × ${(trade.price * 100).toFixed(0)}¢)`;
    }
    // (5) Above ceiling — clamp down.
    if (mirrorNotional > c.userCeiling) {
      mirrorNotional = c.userCeiling;
      reason = `clamped down: proportional $${rawMirrorNotional.toFixed(2)} → ceiling $${c.userCeiling.toFixed(2)}`;
    }
    return { mirrorNotional, limitPrice, reason };
  }

  // ── Hook 5: origination ──
  // Propose trades from the history alone — no upstream trade required.
  // Runs once per cycle AFTER the mirror pass, with the COMPLETE cycle
  // history. The engine tick-rounds prices, clamps to the CLOB floor,
  // caps to `maxPerCycle` proposals, and logs each with the `reason`.
  // Two param-driven signal sources, composable on one strat:
  //   `params.flow`     — watchlist consensus (see proposeFlow)
  //   `params.momentum` — the market's own price rising (see proposeMomentum)
  // Without either: originate nothing (pure mirror).
  propose(history: StratHistory, c: SizeConstraints): ProposedTrade[] {
    const proposals = [
      ...this.proposeFlow(history, c),
      ...this.proposeMomentum(history, c),
    ];
    // Both signal engines can fire on the same market in the same cycle —
    // keep the first (flow outranks momentum by list order).
    const seen = new Set<string>();
    return proposals.filter((p) => {
      const k = `${p.conditionId.toLowerCase()}:${(p.outcome || "Yes").toLowerCase()}:${p.side}`;
      if (seen.has(k)) return false;
      seen.add(k);
      return true;
    });
  }

  // Flow-momentum origination when `params.flow` is set — ENTRIES on
  // watchlist consensus (≥ minTraders distinct buyers, aggregate ≥
  // minFlowUsd, net flow positive) in markets not already held, ranked by
  // net flow, sized by conviction and clamped into the user's trade band;
  // EXITS on held positions whose window flow flipped net-SELL.
  protected proposeFlow(history: StratHistory, c: SizeConstraints): ProposedTrade[] {
    const flow = this.params.flow;
    if (!flow) return [];

    const minTraders = flow.minTraders ?? 2;
    const minFlowUsd = flow.minFlowUsd ?? 50;
    const maxPositions = flow.maxPositions ?? 5;
    const flows = this.aggregateFlow(history);
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
        reason: `CONSENSUS · ${f.buyers.size} traders +$${netUsd.toFixed(0)} net BUY in ${flow.lookbackMinutes ?? 90}m`,
      });
    }

    return proposals;
  }

  // Price-momentum origination when `params.momentum` is set — THE
  // "odds are rising, ride them" strat: for every candidate market series
  // (engine-fed via `history.marketPrices`), measure each outcome's price
  // move over the lookback. ENTRIES buy the outcome that rose ≥
  // minRiseCents (e.g. BTC-up going 50¢ → 60¢) inside the price band, in
  // markets not already held and not about to resolve — sub-hour markets
  // are HFT-bot turf a polling strat structurally loses to. EXITS sell a
  // held outcome once its own price fell ≥ exitDropCents over the window.
  // Binary markets: outcomes[1]'s price is the complement of the series,
  // so a falling series IS a rising second outcome — both sides are
  // candidates and only one can qualify at a time.
  protected proposeMomentum(history: StratHistory, c: SizeConstraints): ProposedTrade[] {
    const mo = this.params.momentum;
    const series = history.marketPrices ?? [];
    if (!mo || series.length === 0) return [];

    const lookbackMin = mo.lookbackMinutes ?? 60;
    const minRise = (mo.minRiseCents ?? 5) / 100;
    const exitDrop = (mo.exitDropCents ?? mo.minRiseCents ?? 5) / 100;
    // Entry floor defaults to the favorite side (≥50¢): momentum rides a
    // leader crossing toward resolution, not sub-50¢ longshots — same
    // likely-to-win bias as the copy path's DEFAULT_MIN_ENTRY_PRICE. An
    // explicit `mo.minPrice` (e.g. 0.15) opts back into cheaper entries.
    const minPrice = mo.minPrice ?? 0.5;
    const maxPrice = mo.maxPrice ?? 0.85;
    const maxPositions = mo.maxPositions ?? 5;
    const minCloseMs = (mo.minMinutesToClose ?? 90) * 60_000;
    const cents = (p: number) => (p * 100).toFixed(0);

    const held = new Map(
      history.positions
        .filter((p) => p.size > 0)
        .map((p) => [`${p.conditionId.toLowerCase()}:${(p.outcome || "Yes").toLowerCase()}`, p]),
    );
    const proposals: ProposedTrade[] = [];
    type Entry = { s: MarketPriceSeries; idx: 0 | 1; rise: number; pNow: number; pThen: number };
    const entries: Entry[] = [];

    for (const s of series) {
      const m = seriesMomentum(s.points, history.now, lookbackMin * 60_000);
      if (!m) continue;
      // Holding EITHER side blocks new entries in this market — when a held
      // Yes decays, the No side reads as "rising" the same cycle, and buying
      // it before the exit fills would just lock in a hedged loss.
      const heldInMarket =
        held.has(`${s.conditionId.toLowerCase()}:${s.outcomes[0].toLowerCase()}`) ||
        held.has(`${s.conditionId.toLowerCase()}:${s.outcomes[1].toLowerCase()}`);
      for (const idx of [0, 1] as const) {
        // outcomes[1] moves by the complement of the tracked series.
        const rise = idx === 0 ? m.rise : -m.rise;
        const pNow = idx === 0 ? m.pNow : 1 - m.pNow;
        const pThen = idx === 0 ? m.pThen : 1 - m.pThen;
        const pos = held.get(`${s.conditionId.toLowerCase()}:${s.outcomes[idx].toLowerCase()}`);

        // EXIT — we hold this outcome and its price is now falling.
        if (pos && rise <= -exitDrop) {
          proposals.push({
            conditionId: s.conditionId,
            outcome: pos.outcome,
            tokenId: s.tokenIds[idx],
            market: s.market,
            side: "SELL",
            notional: pos.value,
            limitPrice: tickRoundPrice(pNow * 0.97),
            reason: `MOMENTUM FLIPPED · ${s.outcomes[idx]} ${cents(pThen)}¢→${cents(pNow)}¢ (−${cents(-rise)}¢) in ${lookbackMin}m`,
          });
          continue;
        }

        // ENTRY candidate — rising, in band, market not held, not near
        // resolution.
        if (heldInMarket || rise < minRise) continue;
        if (pNow < minPrice || pNow > maxPrice) continue;
        if (s.endDateMs !== undefined && s.endDateMs - history.now < minCloseMs) continue;
        entries.push({ s, idx, rise, pNow, pThen });
      }
    }

    // Strongest rise first, capped to the free position slots.
    const openSlots = Math.max(0, maxPositions - held.size);
    entries.sort((a, b) => b.rise - a.rise);
    for (const e of entries.slice(0, openSlots)) {
      // Equal-slice sizing (capital / maxPositions) clamped into the
      // user's trade band — momentum has no leader notional to scale by.
      const raw = c.capital / Math.max(maxPositions, 1);
      const notional = Math.min(Math.max(raw, c.userFloor, c.clobFloor), c.userCeiling);
      proposals.push({
        conditionId: e.s.conditionId,
        outcome: e.s.outcomes[e.idx],
        tokenId: e.s.tokenIds[e.idx],
        market: e.s.market,
        side: "BUY",
        notional,
        // Chase up to 2¢ past the last observed price so the entry fills.
        limitPrice: tickRoundPrice(e.pNow + 0.02),
        reason: `MOMENTUM · ${e.s.outcomes[e.idx]} ${cents(e.pThen)}¢→${cents(e.pNow)}¢ (+${cents(e.rise)}¢) in ${lookbackMin}m`,
      });
    }
    return proposals;
  }

  // ── Capability probes ──
  // True when this strat originates trades — gates the engine's per-cycle
  // positions fetch and Phase 4. Param-driven (`params.flow` /
  // `params.momentum`), with a prototype check so a subclass that
  // overrides `propose` still counts.
  proposes(): boolean {
    return !!this.params.flow || !!this.params.momentum
      || this.propose !== Strat.prototype.propose;
  }

  /** True when the strat needs `history.marketPrices` — gates the engine's
      per-cycle market search + price-history fetch (extra API calls). */
  wantsMarketPrices(): boolean {
    return !!this.params.momentum;
  }

  // ── Overridable price shaping used by the default sizeAndPrice ──
  // Widens the leader's price by `slippageBps` toward whichever side is
  // fillable (BUY = up, SELL = down) so mirrors don't sit unfilled behind
  // the market. slippageBps: 0 quotes the leader's exact price. Caller
  // tick-rounds.
  protected adjustPrice(trade: TraderTrade): number {
    const bps = (this.params.slippageBps ?? 300) / 10_000;
    if (trade.side === "BUY") return Math.min(trade.price * (1 + bps), 0.99);
    return Math.max(trade.price * (1 - bps), 0.01);
  }

  // Aggregate observed flow per market+outcome over the flow lookback
  // window. Shared by propose()'s entry and exit passes.
  private aggregateFlow(history: StratHistory): Map<string, MarketFlow> {
    const lookbackMs = (this.params.flow?.lookbackMinutes ?? 90) * 60_000;
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
}

// ── Helpers strats commonly need ─────────────────────────────────

/** Polymarket binary markets use a 0.01 (1¢) tick size. Round limit
    prices so the resulting maker/taker amount ratio lands on the grid —
    CLOB rejects off-tick orders with "Invalid order payload". */
export function tickRoundPrice(p: number): number {
  if (!Number.isFinite(p)) return 0;
  const clamped = Math.max(0.01, Math.min(0.99, p));
  return Math.round(clamped * 100) / 100;
}

/** Price move over a lookback window from an ascending series: latest
    point vs the latest point AT/BEFORE the window start. Requires real
    coverage — a brand-new market whose series starts inside the window
    returns null rather than faking a full-window rise from partial data.
    Terminal prices (0/1 — resolved) also return null: nothing to ride. */
export function seriesMomentum(
  points: { t: number; p: number }[],
  now: number,
  lookbackMs: number,
): { pThen: number; pNow: number; rise: number } | null {
  if (points.length < 2) return null;
  const cutoff = now - lookbackMs;
  let then: { t: number; p: number } | null = null;
  for (const pt of points) {
    if (pt.t <= cutoff) then = pt;
    else break;
  }
  if (!then) return null;
  const last = points[points.length - 1];
  if (!(last.p > 0 && last.p < 1) || !(then.p > 0 && then.p < 1)) return null;
  return { pThen: then.p, pNow: last.p, rise: last.p - then.p };
}

/** Deterministic slug of the candle LIVE at `nowMs` for a recurring
    short-candle series. Polymarket's sub-hour Up/Down markets use
    `<prefix>-<candle start unix seconds>` (e.g. "btc-updown-5m-1784659200"
    for the 5-minute candle starting at that epoch), so the current market
    is addressable without any search. */
export function candleSlug(prefix: string, periodMinutes: number, nowMs: number): string {
  const period = Math.max(1, Math.round(periodMinutes)) * 60;
  return `${prefix}-${Math.floor(nowMs / 1000 / period) * period}`;
}

/** CLOB hard floors. The matcher rejects any order below either:
    - $1 notional ("Invalid order payload" generic), or
    - 5 shares ("Size (N) lower than the minimum: 5").
    So the effective floor is per-price: max($1, 5 × price). */
export const POLYMARKET_MIN_USD = 1.0;
export const POLYMARKET_MIN_SHARES = 5;
export function clobMinNotional(price: number): number {
  return Math.max(POLYMARKET_MIN_USD, POLYMARKET_MIN_SHARES * Math.max(price, 1e-9));
}

// ── THE playbook math — one copy per language ────────────────────
// These three functions are the decision math shared by the backtest sim
// (CopyIndex.tsx), the JS engine (copyEngine.ts), and — ported line-for-line —
// the Rust live engine (live_engine.rs `stats_from_returns` /
// `candidate_score` / `stop_loss_hit`). parity.fixture.json is asserted by
// BOTH `npx tsx app/lib/strats/__test__.ts` and `cargo test` so the two
// languages cannot drift without a red test.

/** Core ROI/Sharpe/win-rate stats from a trader's per-trade fractional
    returns (closed SELLs in the window). Single source of truth for the
    formulas — every TraderRoiStats in the app is built through this. */
export function statsFromReturns(returns: number[]): {
  roi: number; stdev: number; sharpe: number; sampleSize: number;
  wins: number; successProb: number;
} {
  const n = returns.length;
  let roi = 0, stdev = 0, wins = 0;
  if (n > 0) {
    roi = returns.reduce((s, r) => s + r, 0) / n;
    if (n >= 2) {
      const variance = returns.reduce((s, r) => s + (r - roi) ** 2, 0) / (n - 1);
      stdev = Math.sqrt(variance);
    }
    for (const r of returns) if (r > 0) wins++;
  }
  // Epsilon guard, not `> 0`: near-identical returns leave a quantization-
  // noise stdev (float noise ~1e-17, tick-scalper noise ~1e-7) that explodes
  // Sharpe to 1e5–1e15. Genuine dispersion is ≥1e-3; under 1e-6 → 0. Mirrors
  // live_engine.rs stats_from_returns; parity fixture pins the case.
  const sharpe = n >= 3 && stdev > 1e-6 ? roi / stdev : 0;
  // Laplace-smoothed win rate: shrinks toward 50% on thin samples so a
  // 2-for-2 trader doesn't read as "100% success"; no samples ⇒ exactly 0.5.
  const successProb = (wins + 2) / (n + 4);
  return { roi, stdev, sharpe, sampleSize: n, wins, successProb };
}

/** THE copy ratio — multiply a leader's trade notional by this to get the
    notional we should trade. Mirror of live_engine.rs `copy_ratio_for`;
    pinned cross-language by parity.fixture.json `copyRatioCases`.

    The proportional model: **the leader risked `notional / bankroll` of
    their net worth, so we risk the same fraction of ours**, scaled by that
    trader's share of the watchlist weight. A $10,000 conviction entry then
    copies 100× larger than a $100 punt — which is the entire point of
    copying, and exactly what the old volume model destroyed: `capital /
    leaderVolume` produced a ratio so small that nearly every mirror landed
    under the order floor and was clamped to the same flat minimum.

    `leaderBankroll` = their positions' mark value + free USDC. Unknown (or
    under $1, which is noise rather than a denominator) falls back to the
    volume model — a rough ratio beats not copying, and `sizeAndPrice`'s
    SUB_SCALE gate still stops it from placing wildly out-of-scale orders. */
export function copyRatioFor(
  accountValue: number,
  weightFraction: number,
  leaderBankroll: number | null | undefined,
  capitalAlloc: number,
  traderVol: number,
): number {
  if (typeof leaderBankroll === "number" && leaderBankroll >= 1 && accountValue > 0) {
    return (accountValue * weightFraction) / leaderBankroll;
  }
  return capitalAlloc / Math.max(traderVol, 1);
}

/** Default proportional-fidelity limit (live_engine.rs
    `default_max_upscale`): a floor-clamped mirror may be at most 2× the
    size proportionality asked for before the strat would rather place
    nothing. See `StratParams.maxUpscale`. */
export const DEFAULT_MAX_UPSCALE = 2;

/** Probability-of-success for a candidate copied from this trader. Missing
    or pre-upgrade cached stats (no `successProb` field) read as the neutral
    0.5 prior rather than 0 — a trade must never be zeroed out by a stale
    localStorage cache. */
export function successProbability(stats: TraderRoiStats | null | undefined): number {
  const p = stats?.successProb;
  return typeof p === "number" && Number.isFinite(p) && p > 0 ? p : 0.5;
}

/** Stop-loss trigger — mirror of live_engine.rs `stop_loss_hit`. `stopLoss`
    is the fraction of avg entry price to defend (0.5 = sell once the exit
    mark decays to half the entry). Fractions outside (0, 1) are OFF: ≥1
    would trigger on the spread alone, 0 can never fire. */
export function stopLossTriggered(entryPrice: number, mark: number, stopLoss: number | undefined): boolean {
  if (!stopLoss || stopLoss <= 0 || stopLoss >= 1) return false;
  if (!(entryPrice > 0) || !(mark > 0)) return false;
  return mark <= entryPrice * stopLoss;
}

/** Take-profit trigger — mirror of live_engine.rs `take_profit_hit`.
    `takeProfit` is the ABSOLUTE exit-price level (0–1) at which a held
    position is fully liquidated: the market ran to ~100%, it's decided,
    free the capital instead of holding until resolution. Levels above 0.99
    clamp to 0.99 (the book's top tick — a 1.00 bid never prints, so an
    unclamped 1.0 could never fire). 0/undefined ⇒ off. */
export function takeProfitTriggered(mark: number, takeProfit: number | undefined): boolean {
  if (!takeProfit || takeProfit <= 0) return false;
  if (!(mark > 0)) return false;
  return mark >= Math.min(takeProfit, 0.99);
}

/** Default per-position stop-loss (live_engine.rs `default_stop_loss`):
    defend 0.75 of entry — sell once the exit mark decays to 75% of the
    entry price. Console, backtest sim, and a bare API start all share it. */
export const DEFAULT_STOP_LOSS = 0.75;

/** Default per-position take-profit (live_engine.rs `default_take_profit`):
    liquidate once the exit mark reaches 99¢ — the top tick, i.e. the market
    ran to 100%. */
export const DEFAULT_TAKE_PROFIT = 0.99;

/** Live sync cadence floor in MINUTES (live_engine.rs
    `default_min_interval_ms` = 30_000). Polymarket's data-api sits behind
    Cloudflare, which 429s a sustained few req/s; anything faster gets
    rate-limited into zero observations. Both the console and the engine clamp
    here, so the console must never offer a faster option than it can deliver.
    Also the default for a strat that has never set one. */
export const MIN_POLL_MINUTES = 0.5;

/** Anti-churn rebalance margin (live_engine.rs `default_rebalance_margin_pct`):
    a candidate must out-score a held position by this fraction before the
    hold is sold to fund it. Backtest sim reads the same constant. */
export const REBALANCE_MARGIN_PCT = 0.2;

// Re-exports so a strat consumer only has to `import from "./strat"`.
export type { PolymarketTrade, PolymarketPosition, TraderRoiStats, IndexTrader };
export type { TradeFilters, MomentumParams } from "../types";
export { marketMatchesQuery } from "../marketQuery";
export { tradeMatchesFilters, tradeFiltersActive, describeTradeFilters } from "../tradeFilters";
