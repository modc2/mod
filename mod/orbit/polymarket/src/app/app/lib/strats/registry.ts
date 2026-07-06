// Strat registry — the ONE place where strategy CLASSES are bound.
//
// The engine is parameterized by the class: `getStrat(name, params)` looks
// the class up here and constructs it with its params. To add a strategy:
//   1. Drop a new class in this directory (e.g. `my_strat.ts`) extending
//      `Strat<MyOpts>` from base.ts — override any subset of the hooks
//   2. Import + add the class to REGISTRY below
//   3. (optional) switch DEFAULT_STRAT to its name
//
// Both the live engine (copyEngine.ts) and the backtest (CopyIndex.tsx)
// read from this registry, so a single change re-points everything —
// live trading, backtest preview, log score-breakdowns — all at once.

import { Strat, StratParams, TradeFilters } from "./base";
import { CopyTrader, CopyTraderOpts } from "./copytrader";
import { FlowMomentum, FlowMomentumOpts } from "./flowmomentum";

/** A registrable strategy class: anything constructible with its params.
    The generic keeps `new REGISTRY[name](opts)` type-checked while letting
    each class declare its own params interface. */
export type StratClass = new (params?: StratParams) => Strat<StratParams>;

const REGISTRY = {
  copytrader: CopyTrader as StratClass,
  flowmomentum: FlowMomentum as StratClass,
} as const;

export type StratName = keyof typeof REGISTRY;

/** Options that flow from SavedIndex / CopyEngineConfig into the strat
    constructor. The union of every registered class's params — each class
    picks the fields it cares about and ignores the rest, so the engine
    doesn't need to know which fields belong to which strat. */
export type StratFactoryOpts = StratParams & CopyTraderOpts & FlowMomentumOpts;

// Re-export the common params type for engine call sites.
export type { StratParams, TradeFilters };

/** Default strat used when nothing is specified. */
export const DEFAULT_STRAT: StratName = "copytrader";

/** Construct a Strat by class name. Falls back to DEFAULT_STRAT on unknown
    names so a stale strategy reference in localStorage doesn't crash
    the engine. */
export function getStrat(name: StratName | string | undefined, opts: StratFactoryOpts = {}): Strat {
  const key = (name && name in REGISTRY ? name : DEFAULT_STRAT) as StratName;
  return new REGISTRY[key](opts);
}

/** List of registered strat names — surfaces in the UI strat picker. */
export function listStratNames(): StratName[] {
  return Object.keys(REGISTRY) as StratName[];
}
