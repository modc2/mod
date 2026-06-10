// Strat registry — the ONE place where the active strategy is bound.
//
// To swap strategies:
//   1. Drop a new class in this directory (e.g. `my_strat.ts`)
//   2. Import + register it below
//   3. Switch DEFAULT_STRAT to its name
//
// Both the live engine (copyEngine.ts) and the backtest (CopyIndex.tsx)
// read from this registry, so a single change re-points everything —
// live trading, backtest preview, log score-breakdowns — all at once.

import { Strat } from "./base";
import { CopyTrader, CopyTraderOpts } from "./copytrader";

export type StratName = "copytrader";

/** Options that flow from SavedIndex / CopyEngineConfig into the strat
    constructor. Each strat picks the fields it cares about. */
export interface StratFactoryOpts {
  maxPerCycle?: number;
  // Add per-strat tunables here as new strats land (e.g. `kellyFraction`,
  // `momentumLookbackMinutes`, etc.). Keep the type unioned with the
  // strat-specific opts so the engine doesn't need to know which fields
  // belong to which strat.
}

const FACTORIES: Record<StratName, (opts: StratFactoryOpts) => Strat> = {
  copytrader: (opts) => new CopyTrader({
    maxPerCycle: opts.maxPerCycle,
  } as CopyTraderOpts),
};

/** Default strat used when nothing is specified. */
export const DEFAULT_STRAT: StratName = "copytrader";

/** Construct a Strat by name. Falls back to DEFAULT_STRAT on unknown
    names so a stale strategy reference in localStorage doesn't crash
    the engine. */
export function getStrat(name: StratName | string | undefined, opts: StratFactoryOpts = {}): Strat {
  const key = (name && name in FACTORIES ? name : DEFAULT_STRAT) as StratName;
  return FACTORIES[key](opts);
}

/** List of registered strat names — surfaces in the UI strat picker. */
export function listStratNames(): StratName[] {
  return Object.keys(FACTORIES) as StratName[];
}
